#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.document_rule_candidate_diagnostics import build_trade_rows, summarize_r, group_summary  # noqa: E402


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


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_candidate(batch_report: Path, strategy_id: str) -> dict[str, Any]:
    payload = json.loads(batch_report.read_text(encoding="utf-8-sig"))
    for item in payload.get("all_results") or payload.get("top_results") or []:
        if item.get("strategy_id") == strategy_id:
            return item
    raise ValueError(f"strategy_id not found in batch report: {strategy_id}")


def filter_rows(rows: list[dict[str, Any]], criteria: dict[str, str]) -> list[dict[str, Any]]:
    return [row for row in rows if all(str(row.get(field)) == str(value) for field, value in criteria.items())]


def chronological_split(rows: list[dict[str, Any]], oos_fraction: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: str(row.get("entry_ts") or ""))
    if not ordered:
        return [], []
    oos_count = max(1, round(len(ordered) * oos_fraction))
    if oos_count >= len(ordered):
        oos_count = max(1, len(ordered) // 3)
    split_at = len(ordered) - oos_count
    return ordered[:split_at], ordered[split_at:]


def gate_summary(summary: dict[str, Any], *, min_trades: int, min_expectancy: float, min_winrate: float, max_drawdown: float) -> dict[str, Any]:
    trades = int(summary.get("trades") or 0)
    expectancy = float(summary.get("expectancy_r") or -999.0)
    winrate = float(summary.get("winrate_pct") or 0.0)
    drawdown = float(summary.get("max_drawdown_r") or 0.0)
    checks = [
        {"name": "min_trades", "passed": trades >= min_trades, "actual": trades, "required": f">= {min_trades}"},
        {"name": "min_expectancy_r", "passed": expectancy >= min_expectancy, "actual": expectancy, "required": f">= {min_expectancy}"},
        {"name": "min_winrate_pct", "passed": winrate >= min_winrate, "actual": winrate, "required": f">= {min_winrate}"},
        {"name": "max_drawdown_r", "passed": drawdown >= -abs(max_drawdown), "actual": drawdown, "required": f">= {-abs(max_drawdown)}"},
    ]
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Document Rule Preregistered Validation",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research validation only.",
        "- The filter is fixed before this validation: no post-hoc selection inside this runner.",
        "- Passing this validation would still mean design-review only, not live trading.",
        "- No private keys, no orders, no paper/live permission.",
        "",
        "## Preregistered Hypothesis",
        "",
        f"- ID: `{report['hypothesis']['id']}`",
        f"- Strategy: `{report['hypothesis']['strategy_id']}`",
        f"- Filter: `{report['hypothesis']['filter']}`",
        f"- RR mode: `{report['hypothesis']['rr_mode']}`",
        f"- OOS fraction: `{report['hypothesis']['oos_fraction']}`",
        "",
        "## Results",
        "",
        "| Split | Trades | Winrate | Exp R | Net R | Max DD | Pass |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for split in ("full", "train", "oos"):
        item = report["splits"][split]
        summary = item["summary"]
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                split,
                summary.get("trades"),
                summary.get("winrate_pct"),
                summary.get("expectancy_r"),
                summary.get("net_r_total"),
                summary.get("max_drawdown_r"),
                str(item["gate"]["passed"]).lower(),
            )
        )
    lines.extend(["", "## OOS Gates", ""])
    for check in report["splits"]["oos"]["gate"]["checks"]:
        lines.append(f"- `{check['name']}`: `{check['passed']}` actual=`{check['actual']}` required=`{check['required']}`")
    lines.extend(["", "## OOS Monthly Buckets", "", "| Bucket | Trades | Winrate | Exp R | Net R |", "|---|---:|---:|---:|---:|"])
    for item in report["oos_by_month"]:
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                item.get("bucket"),
                item.get("trades"),
                item.get("winrate_pct"),
                item.get("expectancy_r"),
                item.get("net_r_total"),
            )
        )
    lines.extend(["", "## Decision", "", f"- `{report['decision']}`", "", f"- Next action: {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preregistered validation for document-derived spot-confirm volume-active candidate")
    parser.add_argument("--batch-report", default="docs/DOCUMENT_RULE_CARD_BATCH_TEST_RR1X3_2026-06-30.json")
    parser.add_argument("--strategy-id", default="doc_rule_ad70abbc50_spot_confirm_1h")
    parser.add_argument("--filter-field", default="volume_regime")
    parser.add_argument("--filter-value", default="volume_active")
    parser.add_argument("--oos-fraction", type=float, default=0.30)
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=3.0)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--min-oos-trades", type=int, default=8)
    parser.add_argument("--min-oos-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-oos-winrate-pct", type=float, default=35.0)
    parser.add_argument("--max-oos-drawdown-r", type=float, default=8.0)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/DOCUMENT_RULE_PREREG_VALIDATION_VOLUME_ACTIVE_RR1X3_2026-06-30")
    args = parser.parse_args()

    candidate = load_candidate(resolve_path(args.batch_report), args.strategy_id)
    # Reuse the existing deterministic reconstruction path; these args match the frozen hypothesis.
    rows, metadata = build_trade_rows(candidate, args)
    criteria = {args.filter_field: args.filter_value}
    filtered = filter_rows(rows, criteria)
    train, oos = chronological_split(filtered, args.oos_fraction)
    full_summary = summarize_r(filtered)
    train_summary = summarize_r(train)
    oos_summary = summarize_r(oos)
    train_gate = gate_summary(
        train_summary,
        min_trades=max(args.min_oos_trades, 15),
        min_expectancy=0.0,
        min_winrate=30.0,
        max_drawdown=12.0,
    )
    oos_gate = gate_summary(
        oos_summary,
        min_trades=args.min_oos_trades,
        min_expectancy=args.min_oos_expectancy_r,
        min_winrate=args.min_oos_winrate_pct,
        max_drawdown=args.max_oos_drawdown_r,
    )
    passed = bool(train_gate["passed"] and oos_gate["passed"])
    decision = "preregistered_validation_passed_design_review_only" if passed else "preregistered_validation_failed_or_insufficient"
    next_action = (
        "move to independent forward observer design review; still no live trading"
        if passed
        else "do not promote; either collect fresh OOS/forward data or reject this filtered candidate"
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_preregistered_validation.py",
        "runtime_boundary": {
            "classification": "preregistered_research_validation_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "hypothesis": {
            "id": "DOC_RULE_SPOT_CONFIRM_1H_VOLUME_ACTIVE_RR1X3_V1",
            "strategy_id": args.strategy_id,
            "filter": criteria,
            "rr_mode": f"1:{args.take_atr / args.stop_atr:g}",
            "oos_fraction": args.oos_fraction,
            "frozen_from": "docs/DOCUMENT_RULE_FILTER_PROBE_RR1X3_2026-06-30.json",
            "note": "Filter was selected in a prior post-hoc diagnostic and is frozen before this validation runner.",
        },
        "metadata": metadata,
        "splits": {
            "full": {"rows": len(filtered), "summary": full_summary, "gate": gate_summary(full_summary, min_trades=25, min_expectancy=0.10, min_winrate=35.0, max_drawdown=12.0)},
            "train": {"rows": len(train), "summary": train_summary, "gate": train_gate},
            "oos": {"rows": len(oos), "summary": oos_summary, "gate": oos_gate},
        },
        "oos_by_month": group_summary(oos, "month"),
        "oos_by_oi_regime": group_summary(oos, "oi_regime"),
        "oos_by_spot_perp_regime": group_summary(oos, "spot_perp_regime"),
        "decision": decision,
        "next_action": next_action,
        "can_trade": False,
    }

    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    oos_csv = out_prefix.with_name(out_prefix.name + "_oos_trades").with_suffix(".csv")
    full_csv = out_prefix.with_name(out_prefix.name + "_all_filtered_trades").with_suffix(".csv")
    write_csv(filtered, full_csv)
    write_csv(oos, oos_csv)
    report["all_filtered_trades_csv"] = portable(full_csv)
    report["oos_trades_csv"] = portable(oos_csv)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "hypothesis": report["hypothesis"]["id"],
                "full": full_summary,
                "train": train_summary,
                "oos": oos_summary,
                "decision": decision,
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
