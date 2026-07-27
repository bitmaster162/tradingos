#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_context_intake as base


CONTEXTS = ("long_liquidation_flush", "short_liquidation_squeeze")
DIRECTIONS = ("continuation", "reversal")
HORIZONS = (1, 2, 4, 8)


def load_records(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                normalized = dict(row)
                normalized["horizon_bars"] = int(row["horizon_bars"])
                normalized["continuation_return_bps"] = float(row["continuation_return_bps"])
                normalized["reversal_return_bps"] = float(row["reversal_return_bps"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(normalized)
    return rows


def summarize_candidate(
    rows: list[dict[str, Any]],
    context: str,
    direction: str,
    horizon: int,
    cost_bps: float,
) -> dict[str, Any]:
    field = f"{direction}_return_bps"
    selected = sorted(
        [row for row in rows if row["dominant_context"] == context and row["horizon_bars"] == horizon],
        key=lambda row: (str(row["bar_ts"]), str(row["symbol"])),
    )
    values = [float(row[field]) - cost_bps for row in selected]
    symbols: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(selected, values):
        symbols[str(row["symbol"])].append(value)
    blocks = Counter(str(row["independent_4h_block"]) for row in selected)
    cuts = (len(selected) // 3, 2 * len(selected) // 3)
    thirds = [selected[: cuts[0]], selected[cuts[0] : cuts[1]], selected[cuts[1] :]]
    third_summaries = []
    for index, chunk in enumerate(thirds, start=1):
        chunk_values = [float(row[field]) - cost_bps for row in chunk]
        third_summaries.append(
            {
                "third": index,
                "n": len(chunk_values),
                "first_bar_ts": chunk[0]["bar_ts"] if chunk else None,
                "last_bar_ts": chunk[-1]["bar_ts"] if chunk else None,
                "mean_net_bps": round(statistics.fmean(chunk_values), 6) if chunk_values else None,
                "winrate_net_positive_pct": (
                    round(100.0 * sum(value > 0 for value in chunk_values) / len(chunk_values), 3)
                    if chunk_values
                    else None
                ),
            }
        )
    positive_symbols = sum(statistics.fmean(items) > 0 for items in symbols.values())
    return {
        "candidate_id": f"{context}__{direction}__h{horizon}",
        "context": context,
        "direction": direction,
        "horizon_bars": horizon,
        "side": "LONG" if (context, direction) in {
            ("long_liquidation_flush", "reversal"),
            ("short_liquidation_squeeze", "continuation"),
        } else "SHORT",
        "n": len(values),
        "mean_net_bps": round(statistics.fmean(values), 6) if values else None,
        "median_net_bps": round(statistics.median(values), 6) if values else None,
        "winrate_net_positive_pct": (
            round(100.0 * sum(value > 0 for value in values) / len(values), 3) if values else None
        ),
        "independent_4h_blocks": len(blocks),
        "max_block_share": round(max(blocks.values()) / len(values), 8) if values and blocks else 0.0,
        "symbol_count": len(symbols),
        "positive_symbol_count": positive_symbols,
        "max_symbol_share": (
            round(max(len(items) for items in symbols.values()) / len(values), 8) if values and symbols else 0.0
        ),
        "symbols": {
            symbol: {
                "n": len(items),
                "mean_net_bps": round(statistics.fmean(items), 6),
                "winrate_net_positive_pct": round(100.0 * sum(value > 0 for value in items) / len(items), 3),
            }
            for symbol, items in sorted(symbols.items())
        },
        "chronological_thirds": third_summaries,
    }


def screen(candidate: dict[str, Any], gate: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    checks = {
        "minimum_events": candidate["n"] >= gate["minimum_events"],
        "minimum_independent_4h_blocks": candidate["independent_4h_blocks"] >= gate["minimum_independent_4h_blocks"],
        "minimum_symbols": candidate["symbol_count"] >= gate["minimum_symbols"],
        "minimum_positive_symbols": candidate["positive_symbol_count"] >= gate["minimum_positive_symbols"],
        "maximum_symbol_share": candidate["max_symbol_share"] <= gate["maximum_symbol_share"],
        "minimum_mean_net_bps": (candidate["mean_net_bps"] or -10**9) >= gate["minimum_mean_net_bps"],
        "minimum_winrate_pct": (candidate["winrate_net_positive_pct"] or 0.0) >= gate["minimum_winrate_pct"],
        "all_chronological_thirds_positive": all(
            (item["mean_net_bps"] or -10**9) > 0 for item in candidate["chronological_thirds"]
        ),
    }
    failures.extend(name for name, passed in checks.items() if not passed)
    candidate["screen_checks"] = checks
    candidate["screen_pass"] = not failures
    return not failures, failures


def build_report(rows: list[dict[str, Any]], cost_bps: float) -> dict[str, Any]:
    gate = {
        "minimum_events": 100,
        "minimum_independent_4h_blocks": 20,
        "minimum_symbols": 5,
        "minimum_positive_symbols": 7,
        "maximum_symbol_share": 0.5,
        "minimum_mean_net_bps": 15.0,
        "minimum_winrate_pct": 55.0,
        "all_chronological_thirds_positive": True,
    }
    candidates = [
        summarize_candidate(rows, context, direction, horizon, cost_bps)
        for context in CONTEXTS
        for direction in DIRECTIONS
        for horizon in HORIZONS
    ]
    for candidate in candidates:
        screen(candidate, gate)
    qualified = [candidate for candidate in candidates if candidate["screen_pass"]]
    qualified.sort(key=lambda item: (-float(item["mean_net_bps"]), -int(item["n"]), item["candidate_id"]))
    selected = qualified[0] if qualified else None
    return {
        "generated_at": base.now_iso(),
        "tool": "tools/bybit_liquidation_canonical_discovery_audit.py",
        "decision": (
            "bybit_canonical_discovery_candidate_requires_new_forward_lock"
            if selected
            else "bybit_canonical_discovery_no_stable_forward_candidate"
        ),
        "can_trade": False,
        "selection_boundary": {
            "discovery_only": True,
            "selection_was_made_after_outcome_review": True,
            "untouched_validation": False,
            "independent_forward_proof": False,
            "automatic_promotion_allowed": False,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "fixed_discovery_grid": {
            "contexts": list(CONTEXTS),
            "directions": list(DIRECTIONS),
            "horizons_bars": list(HORIZONS),
            "cost_bps": cost_bps,
            "configs_tested": len(candidates),
        },
        "screen_gate": gate,
        "qualified_count": len(qualified),
        "selected_candidate": selected,
        "leaderboard": sorted(
            candidates,
            key=lambda item: (-float(item["mean_net_bps"] or -10**9), -int(item["n"]), item["candidate_id"]),
        ),
        "next_action": (
            "seal a new observer-only future-floor lock before reading any new event outcome"
            if selected
            else "do not create an observer for this corrected formulation"
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_candidate") or {}
    lines = [
        "# Bybit Canonical Liquidation Discovery Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Qualified discovery candidates: `{report['qualified_count']}`",
        "- Untouched validation: `false`",
        "- Can trade: `false`",
        "",
        "## Selected Discovery Candidate",
        "",
        f"- Candidate: `{selected.get('candidate_id')}`",
        f"- Events: `{selected.get('n')}`",
        f"- Independent 4h blocks: `{selected.get('independent_4h_blocks')}`",
        f"- Symbols: `{selected.get('symbol_count')}`",
        f"- Mean after cost: `{selected.get('mean_net_bps')}` bps",
        f"- Winrate after cost: `{selected.get('winrate_net_positive_pct')}`%",
        "",
        "## Boundary",
        "",
        "This is outcome-reviewed discovery, not OOS proof. It may define one new future-only observer but cannot "
        "support a paper or live trade.",
        "",
        "## Next Action",
        "",
        f"- {report['next_action']}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discovery-only robustness screen for corrected Bybit liquidation labels")
    parser.add_argument(
        "--records", default="docs/BYBIT_ALL_LIQUIDATION_CANONICAL_EVENT_STUDY_V2_2026-07-13_records.csv"
    )
    parser.add_argument("--cost-bps", type=float, default=7.0)
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_DISCOVERY_AUDIT_2026-07-13")
    args = parser.parse_args()
    report = build_report(load_records(base.resolve_path(args.records)), args.cost_bps)
    out = base.resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    selected = report.get("selected_candidate") or {}
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "qualified_count": report["qualified_count"],
                "selected_candidate": selected.get("candidate_id"),
                "mean_net_bps": selected.get("mean_net_bps"),
                "winrate_net_positive_pct": selected.get("winrate_net_positive_pct"),
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
