#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "RISK_REWARD_GATE_v1.json"
DEFAULT_REPORTS = [
    "docs/STRATEGY_POLYGON_100_PARALLEL_2026-06-04.json",
    "docs/EVENT_FEATURE_FACTORY_2026-06-04.json",
    "docs/EVENT_FEATURE_FACTORY_EXTENDED_2026-06-04.json",
    "docs/EVENT_FEATURE_HOLDOUT_VALIDATION_2026-06-04.json",
    "docs/COMBINED_REGIME_WALKFORWARD_2026-06-03.json",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def r6(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def load_thresholds(config_path: Path) -> dict[str, Any]:
    payload = read_json(config_path)
    return payload.get("thresholds", {})


def payoff_ratio(summary: dict[str, Any]) -> float | None:
    avg_win = as_float(summary.get("avg_win_r"))
    avg_loss = as_float(summary.get("avg_loss_r"))
    if avg_win is None or avg_loss is None or avg_loss == 0:
        return None
    return avg_win / abs(avg_loss)


def breakeven_winrate_pct(payoff: float | None) -> float | None:
    if payoff is None or payoff <= 0:
        return None
    return 100.0 / (1.0 + payoff)


def metrics_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    payoff = payoff_ratio(summary)
    breakeven = breakeven_winrate_pct(payoff)
    winrate = as_float(summary.get("winrate_pct"))
    edge_margin = None if winrate is None or breakeven is None else winrate - breakeven
    return {
        "trades": as_int(summary.get("trades")),
        "winrate_pct": r6(winrate),
        "expectancy_r": r6(as_float(summary.get("expectancy_r"))),
        "net_r_total": r6(as_float(summary.get("net_r_total"))),
        "avg_win_r": r6(as_float(summary.get("avg_win_r"))),
        "avg_loss_r": r6(as_float(summary.get("avg_loss_r"))),
        "payoff_ratio": r6(payoff),
        "breakeven_winrate_pct": r6(breakeven),
        "edge_margin_pct": r6(edge_margin),
        "max_drawdown_r": r6(as_float(summary.get("max_drawdown_r"))),
        "max_losing_streak": as_int(summary.get("max_losing_streak")),
    }


def stable_folds(item: dict[str, Any]) -> int:
    if "stable_folds" in item:
        return as_int(item.get("stable_folds"))
    folds = item.get("folds") or []
    if isinstance(folds, list):
        return sum(1 for fold in folds if isinstance(fold, dict) and fold.get("stable"))
    return 0


def check_item(*, metrics: dict[str, Any], stable: int, source_verdict: str, holdout_verdict: str | None, thresholds: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "min_trades": metrics["trades"] >= as_int(thresholds.get("min_trades")),
        "min_expectancy_r": (metrics["expectancy_r"] if metrics["expectancy_r"] is not None else -999.0) >= float(thresholds.get("min_expectancy_r", 0.05)),
        "min_edge_margin_pct": (metrics["edge_margin_pct"] if metrics["edge_margin_pct"] is not None else -999.0) >= float(thresholds.get("min_edge_margin_pct", 2.0)),
        "min_payoff_ratio": (metrics["payoff_ratio"] if metrics["payoff_ratio"] is not None else -999.0) >= float(thresholds.get("min_payoff_ratio", 1.05)),
        "min_stable_folds": stable >= as_int(thresholds.get("min_stable_folds")),
        "max_drawdown_r": (metrics["max_drawdown_r"] if metrics["max_drawdown_r"] is not None else -999.0) >= float(thresholds.get("max_drawdown_r", -25.0)),
        "max_losing_streak": metrics["max_losing_streak"] <= as_int(thresholds.get("max_losing_streak")),
        "has_win_loss_stats": metrics["avg_win_r"] is not None and metrics["avg_loss_r"] is not None,
        "not_known_holdout_fail": holdout_verdict not in {"holdout_fail_do_not_trade"},
    }
    hard_block_reasons: list[str] = []
    if source_verdict in {"do_not_trade", "holdout_fail_do_not_trade"}:
        hard_block_reasons.append("source_verdict_blocks")
    if holdout_verdict == "holdout_fail_do_not_trade":
        hard_block_reasons.append("holdout_fail_blocks")
    if not checks["has_win_loss_stats"]:
        hard_block_reasons.append("missing_avg_win_or_avg_loss")

    passed = all(checks.values()) and not hard_block_reasons
    weak = (
        not passed
        and checks["min_expectancy_r"]
        and metrics["trades"] >= max(30, as_int(thresholds.get("min_trades")) // 2)
        and stable >= 2
        and not hard_block_reasons
    )
    if passed:
        decision = "rr_gate_pass_needs_fresh_oos"
    elif weak:
        decision = "rr_watchlist_only"
    else:
        decision = "rr_block_do_not_trade"
    return {"checks": checks, "hard_block_reasons": hard_block_reasons, "decision": decision}


def items_from_report(path: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    report_name = path.as_posix()
    out: list[dict[str, Any]] = []
    for item in payload.get("top_results", []) if isinstance(payload.get("top_results"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("summary"), dict):
            continue
        out.append(
            {
                "source_report": report_name,
                "strategy_id": str(item.get("strategy_id") or item.get("id") or "unknown"),
                "source_verdict": str(item.get("verdict") or item.get("gate", {}).get("verdict") or "unknown"),
                "holdout_verdict": None,
                "summary": item["summary"],
                "stable_folds": stable_folds(item),
            }
        )
    for item in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
        if not isinstance(item, dict):
            continue
        summary = item.get("all_summary") or item.get("summary")
        if not isinstance(summary, dict):
            continue
        out.append(
            {
                "source_report": report_name,
                "strategy_id": str(item.get("strategy_id") or "unknown"),
                "source_verdict": str(item.get("source_verdict") or item.get("verdict") or "unknown"),
                "holdout_verdict": str(item.get("holdout_verdict")) if item.get("holdout_verdict") else None,
                "summary": summary,
                "stable_folds": as_int((item.get("train_summary") or {}).get("stable_folds")),
                "holdout_summary": item.get("holdout_summary"),
            }
        )
    for item in payload.get("candidates", []) if isinstance(payload.get("candidates"), list) else []:
        summary = item.get("summary")
        if isinstance(summary, dict):
            out.append(
                {
                    "source_report": report_name,
                    "strategy_id": str(item.get("strategy_id") or item.get("id") or "unknown"),
                    "source_verdict": str(item.get("verdict") or item.get("gate", {}).get("verdict") or "unknown"),
                    "holdout_verdict": None,
                    "summary": summary,
                    "stable_folds": stable_folds(item),
                }
            )
    return out


def evaluate_reports(report_paths: list[Path], thresholds: dict[str, Any]) -> dict[str, Any]:
    source_status: list[dict[str, Any]] = []
    raw_items: list[dict[str, Any]] = []
    for path in report_paths:
        if not path.exists():
            source_status.append({"path": str(path), "exists": False, "items": 0})
            continue
        payload = read_json(path)
        items = items_from_report(path, payload)
        source_status.append({"path": str(path), "exists": True, "items": len(items)})
        raw_items.extend(items)

    holdout_by_strategy: dict[str, str] = {}
    for item in raw_items:
        holdout = item.get("holdout_verdict")
        if holdout:
            holdout_by_strategy[item["strategy_id"]] = str(holdout)

    evaluated: list[dict[str, Any]] = []
    for item in raw_items:
        inherited_holdout = holdout_by_strategy.get(item["strategy_id"])
        effective_holdout = inherited_holdout or item.get("holdout_verdict")
        metrics = metrics_from_summary(item["summary"])
        gate = check_item(
            metrics=metrics,
            stable=item["stable_folds"],
            source_verdict=item["source_verdict"],
            holdout_verdict=effective_holdout,
            thresholds=thresholds,
        )
        evaluated.append(
            {
                "source_report": item["source_report"],
                "strategy_id": item["strategy_id"],
                "source_verdict": item["source_verdict"],
                "holdout_verdict": item.get("holdout_verdict"),
                "effective_holdout_verdict": effective_holdout,
                "stable_folds": item["stable_folds"],
                "metrics": metrics,
                "gate": gate,
            }
        )
    evaluated.sort(
        key=lambda item: (
            item["gate"]["decision"] == "rr_gate_pass_needs_fresh_oos",
            item["gate"]["decision"] == "rr_watchlist_only",
            item["metrics"]["expectancy_r"] if item["metrics"]["expectancy_r"] is not None else -999.0,
            item["metrics"]["trades"],
        ),
        reverse=True,
    )
    return {
        "sources": source_status,
        "evaluated": evaluated,
        "pass_count": sum(1 for item in evaluated if item["gate"]["decision"] == "rr_gate_pass_needs_fresh_oos"),
        "watchlist_count": sum(1 for item in evaluated if item["gate"]["decision"] == "rr_watchlist_only"),
        "blocked_count": sum(1 for item in evaluated if item["gate"]["decision"] == "rr_block_do_not_trade"),
    }


def choose_next_action(report: dict[str, Any]) -> dict[str, str]:
    if report["pass_count"] > 0:
        return {
            "id": "run_fresh_oos_before_paper",
            "reason": "At least one item passed risk/reward math, but policy still requires fresh OOS before paper trading.",
        }
    if report["watchlist_count"] > 0:
        return {
            "id": "oos_watchlist_or_tighten_features",
            "reason": "Some items have positive math but fail at least one hard gate. They are research watchlist only.",
        }
    return {
        "id": "continue_document_extraction_and_feature_design",
        "reason": "No current hypothesis passes the risk/reward gate. More parameter tuning is lower value than extracting better deterministic rules.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    t = report["thresholds"]
    lines = [
        "# Risk/Reward Gate",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only quality gate.",
        "- No orders, no private credentials, no paper/live permission.",
        "- Winrate alone is not accepted as an edge.",
        "- A pass still means `needs_fresh_oos`, not deploy.",
        "",
        "## Thresholds",
        "",
        f"- Min trades: `{t.get('min_trades')}`.",
        f"- Min expectancy R: `{t.get('min_expectancy_r')}`.",
        f"- Min edge margin over breakeven: `{t.get('min_edge_margin_pct')}` percentage points.",
        f"- Min payoff ratio: `{t.get('min_payoff_ratio')}`.",
        f"- Min stable folds: `{t.get('min_stable_folds')}`.",
        f"- Max drawdown R: `{t.get('max_drawdown_r')}`.",
        f"- Max losing streak: `{t.get('max_losing_streak')}`.",
        "",
        "## Result",
        "",
        f"- Evaluated items: `{report['evaluated_count']}`.",
        f"- RR pass: `{report['pass_count']}`.",
        f"- Watchlist only: `{report['watchlist_count']}`.",
        f"- Blocked/do not trade: `{report['blocked_count']}`.",
        "",
        "## Top Evaluated Items",
        "",
        "| Strategy | Trades | Winrate | Payoff | Breakeven WR | Edge Margin | Exp R | DD R | Stable Folds | Decision |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["top_items"]:
        m = item["metrics"]
        lines.append(
            f"| `{item['strategy_id']}` | `{m['trades']}` | `{m['winrate_pct']}` | `{m['payoff_ratio']}` | "
            f"`{m['breakeven_winrate_pct']}` | `{m['edge_margin_pct']}` | `{m['expectancy_r']}` | "
            f"`{m['max_drawdown_r']}` | `{item['stable_folds']}` | `{item['gate']['decision']}` |"
        )
    lines.extend(["", "## Source Coverage", "", "| Source | Exists | Items |", "|---|---:|---:|"])
    for source in report["sources"]:
        lines.append(f"| `{source['path']}` | `{source['exists']}` | `{source['items']}` |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Next action: `{report['next_action']['id']}`.",
            f"- Reason: {report['next_action']['reason']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only risk/reward gate for strategy reports")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--reports", default=",".join(DEFAULT_REPORTS))
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--out-prefix", default="docs/RISK_REWARD_GATE_2026-06-04")
    args = parser.parse_args()

    config_path = Path(args.config)
    thresholds = load_thresholds(config_path)
    report_paths = [Path(item.strip()) for item in args.reports.split(",") if item.strip()]
    evaluated = evaluate_reports(report_paths, thresholds)
    report: dict[str, Any] = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_gate_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "config": str(config_path),
        "thresholds": thresholds,
        "sources": evaluated["sources"],
        "evaluated_count": len(evaluated["evaluated"]),
        "pass_count": evaluated["pass_count"],
        "watchlist_count": evaluated["watchlist_count"],
        "blocked_count": evaluated["blocked_count"],
        "top_items": evaluated["evaluated"][: args.top],
    }
    report["next_action"] = choose_next_action(report)

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "evaluated_count": report["evaluated_count"],
                "pass_count": report["pass_count"],
                "watchlist_count": report["watchlist_count"],
                "blocked_count": report["blocked_count"],
                "next_action": report["next_action"],
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
