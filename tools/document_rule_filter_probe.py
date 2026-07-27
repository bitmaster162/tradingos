#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if not math.isnan(out) else default


def summarize(rows: list[dict[str, str]]) -> dict[str, Any]:
    values = [safe_float(row.get("r_net")) for row in rows]
    wins = [value for value in values if value > 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    losing_streak = 0
    max_losing = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        if value <= 0:
            losing_streak += 1
            max_losing = max(max_losing, losing_streak)
        else:
            losing_streak = 0
    folds = []
    for fold in sorted({row.get("chronological_fold", "missing") for row in rows}):
        subset = [row for row in rows if row.get("chronological_fold") == fold]
        vals = [safe_float(row.get("r_net")) for row in subset]
        folds.append(
            {
                "fold": fold,
                "trades": len(vals),
                "expectancy_r": round(sum(vals) / len(vals), 6) if vals else None,
                "net_r_total": round(sum(vals), 6) if vals else 0.0,
            }
        )
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(values) - len(wins),
        "winrate_pct": round(len(wins) / len(values) * 100.0, 3) if values else None,
        "expectancy_r": round(sum(values) / len(values), 6) if values else None,
        "net_r_total": round(sum(values), 6) if values else 0.0,
        "max_drawdown_r": round(max_dd, 6),
        "max_losing_streak": max_losing,
        "stable_folds": sum(1 for fold in folds if fold["trades"] >= 5 and (fold["expectancy_r"] or 0) > 0),
        "folds": folds,
    }


def filter_rows(rows: list[dict[str, str]], criteria: dict[str, str]) -> list[dict[str, str]]:
    return [row for row in rows if all(row.get(field) == value for field, value in criteria.items())]


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Document Rule Filter Probe",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Post-hoc diagnostic probe only.",
        "- No trade permission and no paper promotion.",
        "- Any useful filter must be frozen and retested in a separate preregistered validation.",
        "",
        "## Summary",
        "",
        f"- Source trades: `{report['source_trades']}`",
        f"- Tested filters: `{report['tested_filters']}`",
        f"- Probe candidates: `{report['probe_candidate_count']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        "",
        "## Top Filters",
        "",
        "| Rank | Filter | Trades | Winrate | Exp R | Net R | Stable Folds | Max DD | Verdict |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for index, item in enumerate(report["top_filters"][:30], start=1):
        summary = item["summary"]
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                index,
                item["filter"],
                summary.get("trades"),
                summary.get("winrate_pct"),
                summary.get("expectancy_r"),
                summary.get("net_r_total"),
                summary.get("stable_folds"),
                summary.get("max_drawdown_r"),
                item.get("verdict"),
            )
        )
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-hoc filter probe for document rule candidate diagnostics")
    parser.add_argument("--trades-csv", default="docs/DOCUMENT_RULE_CANDIDATE_DIAGNOSTICS_RR1X3_2026-06-30_trades.csv")
    parser.add_argument("--fields", default="oi_regime,volume_regime,spot_perp_regime,atr_regime,funding_regime")
    parser.add_argument("--max-filter-size", type=int, default=3)
    parser.add_argument("--min-trades", type=int, default=25)
    parser.add_argument("--min-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-stable-folds", type=int, default=3)
    parser.add_argument("--out-prefix", default="docs/DOCUMENT_RULE_FILTER_PROBE_RR1X3_2026-06-30")
    args = parser.parse_args()

    trades_csv = resolve_path(args.trades_csv)
    with trades_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fields = [field.strip() for field in args.fields.split(",") if field.strip()]
    candidates = []
    for size in range(1, min(args.max_filter_size, len(fields)) + 1):
        for combo in itertools.combinations(fields, size):
            values = sorted({tuple(row.get(field, "") for field in combo) for row in rows})
            for value_tuple in values:
                criteria = dict(zip(combo, value_tuple))
                subset = filter_rows(rows, criteria)
                summary = summarize(subset)
                if summary["trades"] < args.min_trades:
                    continue
                pass_probe = (
                    (summary["expectancy_r"] or 0.0) >= args.min_expectancy_r
                    and int(summary["stable_folds"]) >= args.min_stable_folds
                )
                candidates.append(
                    {
                        "filter": criteria,
                        "summary": summary,
                        "verdict": "posthoc_probe_candidate_needs_preregistered_validation" if pass_probe else "diagnostic_only",
                    }
                )
    ranked = sorted(
        candidates,
        key=lambda item: (
            1 if item["verdict"].startswith("posthoc_probe_candidate") else 0,
            int(item["summary"].get("stable_folds") or 0),
            float(item["summary"].get("expectancy_r") or -999.0),
            int(item["summary"].get("trades") or 0),
        ),
        reverse=True,
    )
    probe_candidates = [item for item in ranked if item["verdict"].startswith("posthoc_probe_candidate")]
    report = {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_filter_probe.py",
        "runtime_boundary": {
            "classification": "posthoc_diagnostic_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "trades_csv": portable(trades_csv),
        "source_trades": len(rows),
        "fields": fields,
        "settings": {
            "max_filter_size": args.max_filter_size,
            "min_trades": args.min_trades,
            "min_expectancy_r": args.min_expectancy_r,
            "min_stable_folds": args.min_stable_folds,
        },
        "tested_filters": len(candidates),
        "probe_candidate_count": len(probe_candidates),
        "top_filters": ranked[:100],
        "decision": "posthoc_filter_probe_has_candidates" if probe_candidates else "posthoc_filter_probe_no_candidates",
        "next_action": (
            "freeze the top filter as a preregistered hypothesis and run a separate validation; no trade permission"
            if probe_candidates
            else "do not continue this candidate; return to feature design"
        ),
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "source_trades": len(rows),
                "tested_filters": len(candidates),
                "probe_candidate_count": len(probe_candidates),
                "top_filter": ranked[0] if ranked else None,
                "decision": report["decision"],
                "json": portable(json_path),
                "md": portable(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
