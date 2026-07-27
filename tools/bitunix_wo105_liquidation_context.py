#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidation_side_semantics import liquidated_position_side  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_liquidation_context.py"
SOURCE = "binance_usdm_forceOrder_websocket"
SCHEMA_VERSION = 2
DEFAULT_WINDOW_MS = 60 * 60 * 1000
DEFAULT_MIN_EVENTS = 3
DEFAULT_MIN_NOTIONAL_USD = 1000.0


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def parse_iso_ms(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def finite_positive(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed) and parsed > 0


def validate_row(row: Any, *, floor_ms: int, cutoff_ms: int) -> tuple[dict[str, Any] | None, str | None]:
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
    if event_ms > received_ms:
        return None, "event_after_receipt"
    if event_ms < floor_ms or received_ms < floor_ms:
        return None, "pre_floor"
    if event_ms > cutoff_ms or received_ms > cutoff_ms:
        return None, "after_cutoff"
    if row.get("side") not in ("BUY", "SELL"):
        return None, "raw_side_invalid"
    if not finite_positive(row.get("price")) or not finite_positive(row.get("quantity")):
        return None, "price_or_quantity_invalid"
    if not finite_positive(row.get("notional_usd")):
        return None, "notional_invalid"
    recomputed = float(row["price"]) * float(row["quantity"])
    tolerance = max(1e-6, recomputed * 1e-8)
    if abs(float(row["notional_usd"]) - recomputed) > tolerance:
        return None, "notional_not_recomputable"
    normalized = {
        "event_time_ms": event_ms,
        "received_at_ms": received_ms,
        "side": row["side"],
        "liquidated_position_side": liquidated_position_side("binance_force_order", row["side"]),
        "price": float(row["price"]),
        "quantity": float(row["quantity"]),
        "notional_usd": float(row["notional_usd"]),
        "source_row_sha256": canonical_sha256(row),
    }
    return normalized, None


def load_rows(data_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for path in sorted((data_dir / "BTCUSDT").glob("*.jsonl")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                failures.append(f"json_decode:{path.name}:{line_number}")
                continue
            rows.append(row)
    return rows, failures


def build_context(
    rows: list[dict[str, Any]],
    *,
    floor_ms: int,
    cutoff_ms: int,
    window_ms: int = DEFAULT_WINDOW_MS,
    minimum_events: int = DEFAULT_MIN_EVENTS,
    minimum_notional_usd: float = DEFAULT_MIN_NOTIONAL_USD,
) -> dict[str, Any]:
    window_start = cutoff_ms - window_ms
    accepted: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    for row in rows:
        normalized, reason = validate_row(row, floor_ms=floor_ms, cutoff_ms=cutoff_ms)
        if reason:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue
        assert normalized is not None
        if normalized["event_time_ms"] < window_start or normalized["received_at_ms"] < window_start:
            rejection_counts["outside_window"] = rejection_counts.get("outside_window", 0) + 1
            continue
        accepted.append(normalized)
    identities: set[tuple[Any, ...]] = set()
    deduplicated: list[dict[str, Any]] = []
    for row in sorted(accepted, key=lambda item: (item["received_at_ms"], item["event_time_ms"], item["source_row_sha256"])):
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
            "first_source_row_sha256": deduplicated[0]["source_row_sha256"],
            "last_source_row_sha256": deduplicated[-1]["source_row_sha256"],
        }
        observed_at = deduplicated[-1]["event_time_ms"]
        received_at = deduplicated[-1]["received_at_ms"]
        record = {
            "source_id": f"binance:wo105:liquidation_skew:{cutoff_ms}:{canonical_sha256(payload)}",
            "observed_at": observed_at,
            "received_at": received_at,
            "source_hash": canonical_sha256(payload),
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
        "rejection_counts": dict(sorted(rejection_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Real Binance forceOrder context adapter for Bitunix WO105")
    parser.add_argument("--data-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--forward-floor", default="2026-07-14T12:00:00Z")
    parser.add_argument("--cutoff", default="")
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--minimum-events", type=int, default=3)
    parser.add_argument("--minimum-notional-usd", type=float, default=1000.0)
    parser.add_argument("--out", default="_dl/bitunix_wo105_liquidation_context/LAST_CONTEXT.json")
    args = parser.parse_args()
    floor = parse_iso_ms(args.forward_floor)
    cutoff = parse_iso_ms(args.cutoff) if args.cutoff else now_ms()
    if floor is None or cutoff is None:
        raise SystemExit("invalid timezone-aware --forward-floor or --cutoff")
    rows, load_failures = load_rows(resolve(args.data_dir))
    result = build_context(
        rows,
        floor_ms=floor,
        cutoff_ms=cutoff,
        window_ms=args.window_minutes * 60 * 1000,
        minimum_events=args.minimum_events,
        minimum_notional_usd=args.minimum_notional_usd,
    )
    decision = "bitunix_wo105_liquidation_context_ready" if result["record"] else "bitunix_wo105_liquidation_context_hold"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
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
    output = resolve(args.out)
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
