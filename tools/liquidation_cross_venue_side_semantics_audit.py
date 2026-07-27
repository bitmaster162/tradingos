#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import liquidation_cross_venue_canonical_paired_leadership_forward_observer as canonical
from tools import liquidation_cross_venue_lead_lag_forward_observer as base
from tools import liquidation_cross_venue_paired_leadership_forward_observer as paired


def compare_pairing(
    binance_raw: list[dict[str, Any]],
    bybit_raw: list[dict[str, Any]],
    *,
    cutoff_ns: int,
    windows_seconds: list[int],
) -> dict[str, Any]:
    maximum_window_ns = max(windows_seconds) * 1_000_000_000
    raw_pairs = paired.build_pairs(
        binance_raw,
        bybit_raw,
        cutoff_ns=cutoff_ns,
        maximum_window_ns=maximum_window_ns,
    )
    canonical_pairs = paired.build_pairs(
        canonical.canonicalize_events("binance", binance_raw),
        canonical.canonicalize_events("bybit", bybit_raw),
        cutoff_ns=cutoff_ns,
        maximum_window_ns=maximum_window_ns,
    )
    return {
        "raw_same_side": {
            "windows_seconds": paired.summarize_pairs(raw_pairs, windows_seconds),
            "primary_sample": paired.primary_sample(raw_pairs, 5),
        },
        "canonical_liquidated_position_side": {
            "windows_seconds": paired.summarize_pairs(canonical_pairs, windows_seconds),
            "primary_sample": paired.primary_sample(canonical_pairs, 5),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    comparison = report["diagnostic_comparison"]
    raw = comparison["raw_same_side"]["windows_seconds"]
    canonical_windows = comparison["canonical_liquidated_position_side"]["windows_seconds"]
    lines = [
        "# Cross-Venue Liquidation V2 Side-Semantics Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Edge evaluated: `false`",
        "- Can trade: `false`",
        "",
        "## Finding",
        "",
        "V2 matched the raw `side` field across venues, but the fields do not represent the same entity. "
        "Binance reports forced-order side; Bybit reports liquidated-position side. V2 is therefore "
        "terminally invalid as a cross-venue semantic contract, independent of its observed pair count.",
        "",
        "| Window | V2 raw-side pairs | Canonical diagnostic pairs |",
        "|---:|---:|---:|",
    ]
    for seconds in ("1", "5", "15"):
        lines.append(
            f"| `{seconds}s` | `{raw.get(seconds, {}).get('matched_pairs', 0)}` | "
            f"`{canonical_windows.get(seconds, {}).get('matched_pairs', 0)}` |"
        )
    lines.extend(
        [
            "",
            "## Resolution",
            "",
            "- V2 files and lock remain unchanged for reproducibility.",
            "- No V2 event is admitted into V3.",
            "- The diagnostic canonical pair count is plumbing evidence only, not evidence of leadership or edge.",
            "- V3 starts after a new future floor and matches canonical `liquidated_position_side`.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(lock_path: Path) -> dict[str, Any]:
    lock = base.read_json(lock_path)
    failures = paired.validate_lock(lock)
    if failures:
        raise ValueError("invalid V2 forward lock: " + ",".join(failures))
    floor_ns = base.parse_iso_ns(lock["forward_start_at"])
    assert floor_ns is not None
    rules = lock["fixed_rules"]
    windows_seconds = [int(value) for value in rules["pair_windows_seconds"]]
    maximum_window_ns = max(windows_seconds) * 1_000_000_000
    symbols = {str(value).upper() for value in lock["shared_symbols"]}
    load_kwargs = {
        "floor_ns": floor_ns,
        "symbols": symbols,
        "required_host": str(rules["required_collector_host"]),
        "required_schema_version": int(rules["required_ingest_schema_version"]),
    }
    binance, binance_counters = base.load_events(
        "binance", base.resolve_path(lock["sources"]["binance"]), **load_kwargs
    )
    bybit, bybit_counters = base.load_events(
        "bybit", base.resolve_path(lock["sources"]["bybit"]), **load_kwargs
    )
    current_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    if binance and bybit:
        latest_common = min(binance[-1]["received_at_ns"], bybit[-1]["received_at_ns"], current_ns)
        cutoff_ns = latest_common - maximum_window_ns
    else:
        cutoff_ns = floor_ns - 1
    return {
        "generated_at": base.now_iso(),
        "tool": "tools/liquidation_cross_venue_side_semantics_audit.py",
        "decision": "liquidation_cross_venue_paired_v2_terminal_semantic_contract_failure",
        "edge_evaluated": False,
        "can_trade": False,
        "v2_lock": {
            "path": base.portable(lock_path),
            "sha256": base.sha256_file(lock_path),
            "lock_id": lock["lock_id"],
            "integrity_valid": True,
        },
        "source_counters": {"binance": binance_counters, "bybit": bybit_counters},
        "evaluation_cutoff": base.iso_from_ns(cutoff_ns),
        "semantic_contract": {
            "v2_match_dimension": "raw side",
            "failure": "venue raw side fields have different meanings",
            "binance_raw_side": "forced liquidation order side",
            "bybit_raw_side": "liquidated position side",
            "canonical_mapping": canonical.CANONICAL_SIDE_MAP,
            "binance_mapping_is_inference_from_closing_order_side": True,
            "references": {
                "binance": "https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market#all-market-liquidation-order-streams",
                "bybit": "https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation",
            },
        },
        "diagnostic_comparison": compare_pairing(
            binance,
            bybit,
            cutoff_ns=cutoff_ns,
            windows_seconds=windows_seconds,
        ),
        "resolution": {
            "v2_modified": False,
            "v2_observations_admitted_to_v3": False,
            "replacement": "liquidation_cross_venue_canonical_paired_receipt_leadership_2026_07_13_v3",
            "diagnostic_counts_are_edge_evidence": False,
        },
        "runtime_boundary": {
            "observer_only": True,
            "price_outcomes_read": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit V2 Binance/Bybit liquidation side semantics without evaluating edge")
    parser.add_argument(
        "--lock", default="configs/LIQUIDATION_CROSS_VENUE_PAIRED_LEADERSHIP_LOCK_2026-07-13.json"
    )
    parser.add_argument(
        "--out-prefix", default="docs/LIQUIDATION_CROSS_VENUE_PAIRED_LEADERSHIP_V2_SIDE_SEMANTICS_AUDIT_2026-07-13"
    )
    args = parser.parse_args()
    try:
        report = build_report(base.resolve_path(args.lock))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"decision": "side_semantics_audit_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    out_prefix = base.resolve_path(args.out_prefix)
    base.write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    comparison = report["diagnostic_comparison"]
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "raw_primary_pairs": comparison["raw_same_side"]["primary_sample"]["matched_pairs"],
                "canonical_primary_pairs": comparison["canonical_liquidated_position_side"]["primary_sample"]["matched_pairs"],
                "edge_evaluated": False,
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
