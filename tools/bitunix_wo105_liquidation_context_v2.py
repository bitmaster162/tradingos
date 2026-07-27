#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_liquidation_context as base
from tools.liquidation_side_semantics import liquidated_position_side


TOOL_PATH = "tools/bitunix_wo105_liquidation_context_v2.py"
SOURCE = base.SOURCE
SCHEMA_VERSION = base.SCHEMA_VERSION

# This matches the already-reviewed public websocket parser clock-skew bound.
# Causal availability still uses max(event, receive), so the tolerance cannot
# make an event available before either timestamp.
DEFAULT_MAX_CLOCK_SKEW_MS = 5_000
DEFAULT_WINDOW_MS = base.DEFAULT_WINDOW_MS
DEFAULT_MIN_EVENTS = base.DEFAULT_MIN_EVENTS
DEFAULT_MIN_NOTIONAL_USD = base.DEFAULT_MIN_NOTIONAL_USD


def validate_row(
    row: Any,
    *,
    floor_ms: int,
    cutoff_ms: int,
    max_clock_skew_ms: int = DEFAULT_MAX_CLOCK_SKEW_MS,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(max_clock_skew_ms, int) or isinstance(max_clock_skew_ms, bool) or max_clock_skew_ms < 0:
        raise ValueError("max_clock_skew_ms must be a non-negative integer")
    if not isinstance(row, dict):
        return None, "row_not_object"
    if row.get("source") != SOURCE or row.get("is_real_liquidation_feed") is not True:
        return None, "source_or_real_feed_flag_invalid"
    if row.get("symbol") != "BTCUSDT":
        return None, "symbol_invalid"
    if row.get("ingest_schema_version") != SCHEMA_VERSION:
        return None, "ingest_schema_invalid"

    event_ms = row.get("event_time_ms")
    received_ns = row.get("received_at_ns")
    if not isinstance(event_ms, int) or isinstance(event_ms, bool) or event_ms <= 0:
        return None, "event_time_invalid"
    if not isinstance(received_ns, int) or isinstance(received_ns, bool) or received_ns <= 0:
        return None, "received_at_ns_invalid"
    received_ms = received_ns // 1_000_000
    clock_lead_ms = event_ms - received_ms
    if clock_lead_ms > max_clock_skew_ms:
        return None, "event_after_receipt_beyond_clock_skew"

    causal_available_ms = max(event_ms, received_ms)
    if event_ms < floor_ms or received_ms < floor_ms:
        return None, "pre_floor"
    if causal_available_ms > cutoff_ms:
        return None, "after_cutoff"
    if row.get("side") not in ("BUY", "SELL"):
        return None, "raw_side_invalid"
    if not base.finite_positive(row.get("price")) or not base.finite_positive(row.get("quantity")):
        return None, "price_or_quantity_invalid"
    if not base.finite_positive(row.get("notional_usd")):
        return None, "notional_invalid"

    recomputed = float(row["price"]) * float(row["quantity"])
    tolerance = max(1e-6, recomputed * 1e-8)
    if abs(float(row["notional_usd"]) - recomputed) > tolerance:
        return None, "notional_not_recomputable"
    return (
        {
            "event_time_ms": event_ms,
            "raw_received_at_ms": received_ms,
            "received_at_ms": causal_available_ms,
            "causal_available_ms": causal_available_ms,
            "clock_lead_ms": clock_lead_ms,
            "side": row["side"],
            "liquidated_position_side": liquidated_position_side("binance_force_order", row["side"]),
            "price": float(row["price"]),
            "quantity": float(row["quantity"]),
            "notional_usd": float(row["notional_usd"]),
            "source_row_sha256": base.canonical_sha256(row),
        },
        None,
    )


def build_context(
    rows: list[dict[str, Any]],
    *,
    floor_ms: int,
    cutoff_ms: int,
    window_ms: int = DEFAULT_WINDOW_MS,
    minimum_events: int = DEFAULT_MIN_EVENTS,
    minimum_notional_usd: float = DEFAULT_MIN_NOTIONAL_USD,
    max_clock_skew_ms: int = DEFAULT_MAX_CLOCK_SKEW_MS,
) -> dict[str, Any]:
    window_start = cutoff_ms - window_ms
    accepted: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    for row in rows:
        normalized, reason = validate_row(
            row,
            floor_ms=floor_ms,
            cutoff_ms=cutoff_ms,
            max_clock_skew_ms=max_clock_skew_ms,
        )
        if reason:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue
        assert normalized is not None
        if normalized["event_time_ms"] < window_start or normalized["causal_available_ms"] < window_start:
            rejection_counts["outside_window"] = rejection_counts.get("outside_window", 0) + 1
            continue
        accepted.append(normalized)

    identities: set[tuple[Any, ...]] = set()
    deduplicated: list[dict[str, Any]] = []
    for row in sorted(
        accepted,
        key=lambda item: (item["causal_available_ms"], item["event_time_ms"], item["source_row_sha256"]),
    ):
        identity = (row["event_time_ms"], row["side"], row["price"], row["quantity"])
        if identity in identities:
            rejection_counts["duplicate"] = rejection_counts.get("duplicate", 0) + 1
            continue
        identities.add(identity)
        deduplicated.append(row)

    long_notional = sum(row["notional_usd"] for row in deduplicated if row["liquidated_position_side"] == "LONG")
    short_notional = sum(row["notional_usd"] for row in deduplicated if row["liquidated_position_side"] == "SHORT")
    total = long_notional + short_notional
    blockers: list[str] = []
    if len(deduplicated) < minimum_events:
        blockers.append(f"minimum_events_not_met:{len(deduplicated)}<{minimum_events}")
    if total < minimum_notional_usd:
        blockers.append(f"minimum_notional_not_met:{total:.8f}<{minimum_notional_usd:.8f}")

    record: dict[str, Any] | None = None
    if not blockers:
        value = (short_notional - long_notional) / total
        payload = {
            "kind": "liquidation_skew",
            "value": value,
            "unit": "signed_notional_share",
            "method": "(short_liquidated_notional-long_liquidated_notional)/total_liquidated_notional",
            "source": SOURCE,
            "side_semantics": {"BUY": "liquidated_SHORT", "SELL": "liquidated_LONG"},
            "window_ms": window_ms,
            "window_start_ms": window_start,
            "window_end_ms": cutoff_ms,
            "events": len(deduplicated),
            "long_liquidated_notional_usd": long_notional,
            "short_liquidated_notional_usd": short_notional,
            "total_liquidated_notional_usd": total,
            "max_clock_skew_ms": max_clock_skew_ms,
            "maximum_observed_clock_lead_ms": max(row["clock_lead_ms"] for row in deduplicated),
            "causal_availability_rule": "max(event_time_ms,raw_received_at_ms)",
            "first_source_row_sha256": deduplicated[0]["source_row_sha256"],
            "last_source_row_sha256": deduplicated[-1]["source_row_sha256"],
        }
        observed_at = deduplicated[-1]["event_time_ms"]
        received_at = deduplicated[-1]["causal_available_ms"]
        record = {
            "source_id": f"binance:wo105:liquidation_skew:v2:{cutoff_ms}:{base.canonical_sha256(payload)}",
            "observed_at": observed_at,
            "received_at": received_at,
            "source_hash": base.canonical_sha256(payload),
            "schema_version": "crowd-point-v1",
            "payload": payload,
        }
    return {
        "record": record,
        "blockers": blockers,
        "accepted_events": len(deduplicated),
        "long_liquidated_notional_usd": long_notional,
        "short_liquidated_notional_usd": short_notional,
        "total_liquidated_notional_usd": total,
        "maximum_observed_clock_lead_ms": max((row["clock_lead_ms"] for row in deduplicated), default=None),
        "max_clock_skew_ms": max_clock_skew_ms,
        "causal_availability_rule": "max(event_time_ms,raw_received_at_ms)",
        "rejection_counts": dict(sorted(rejection_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Clock-skew-bounded real Binance forceOrder context for Bitunix")
    parser.add_argument("--data-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--forward-floor", required=True)
    parser.add_argument("--cutoff", default="")
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--minimum-events", type=int, default=3)
    parser.add_argument("--minimum-notional-usd", type=float, default=1000.0)
    parser.add_argument("--max-clock-skew-ms", type=int, default=DEFAULT_MAX_CLOCK_SKEW_MS)
    parser.add_argument("--out", default="_dl/bitunix_wo105_liquidation_context_v2/LAST_CONTEXT.json")
    args = parser.parse_args()
    floor = base.parse_iso_ms(args.forward_floor)
    cutoff = base.parse_iso_ms(args.cutoff) if args.cutoff else base.now_ms()
    if floor is None or cutoff is None:
        raise SystemExit("invalid timezone-aware --forward-floor or --cutoff")
    rows, load_failures = base.load_rows(base.resolve(args.data_dir))
    result = build_context(
        rows,
        floor_ms=floor,
        cutoff_ms=cutoff,
        window_ms=args.window_minutes * 60 * 1000,
        minimum_events=args.minimum_events,
        minimum_notional_usd=args.minimum_notional_usd,
        max_clock_skew_ms=args.max_clock_skew_ms,
    )
    decision = "bitunix_wo105_liquidation_context_v2_ready" if result["record"] else "bitunix_wo105_liquidation_context_v2_hold"
    report = {
        "schema_version": 2,
        "generated_at": base.now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "forward_floor_ms": floor,
        "cutoff_ms": cutoff,
        "source_rows": len(rows),
        "load_failures": load_failures,
        **result,
        "signals_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }
    output = base.resolve(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "decision": decision,
                "accepted_events": result["accepted_events"],
                "blockers": result["blockers"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not load_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
