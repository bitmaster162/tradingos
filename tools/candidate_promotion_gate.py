#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path("configs/CANDIDATE_PROMOTION_GATE_v1.json")
DEFAULT_OUT_PREFIX = Path("docs/CANDIDATE_PROMOTION_GATE_2026-06-04")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
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


def candidate_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or item.get("strategy_id") or item.get("candidate_id") or "unknown_candidate")


def stable_folds(item: dict[str, Any]) -> int:
    if "stable_folds" in item:
        return as_int(item.get("stable_folds"))
    train = item.get("train_summary")
    if isinstance(train, dict) and "stable_folds" in train:
        return as_int(train.get("stable_folds"))
    folds = item.get("folds") or []
    if isinstance(folds, list):
        return sum(1 for fold in folds if isinstance(fold, dict) and fold.get("stable"))
    return 0


def summary_for(item: dict[str, Any]) -> dict[str, Any]:
    for key in ("summary", "all_summary", "metrics"):
        value = item.get(key)
        if isinstance(value, dict):
            return value
    return {}


def metrics_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "trades": as_int(summary.get("trades")),
        "winrate_pct": r6(as_float(summary.get("winrate_pct"))),
        "expectancy_r": r6(as_float(summary.get("expectancy_r"))),
        "net_r_total": r6(as_float(summary.get("net_r_total"))),
        "avg_win_r": r6(as_float(summary.get("avg_win_r"))),
        "avg_loss_r": r6(as_float(summary.get("avg_loss_r"))),
        "max_drawdown_r": r6(as_float(summary.get("max_drawdown_r"))),
        "max_losing_streak": as_int(summary.get("max_losing_streak")),
    }


def bootstrap_prob_gt_0(item: dict[str, Any]) -> float | None:
    bootstrap = item.get("bootstrap")
    if isinstance(bootstrap, dict):
        expectancy = bootstrap.get("expectancy_r")
        if isinstance(expectancy, dict):
            return r6(as_float(expectancy.get("prob_gt_0")))
    return None


def research_gate_pass(item: dict[str, Any]) -> bool | None:
    gate = item.get("research_gate")
    if isinstance(gate, dict) and "pass" in gate:
        return bool(gate.get("pass"))
    gate = item.get("gate")
    if isinstance(gate, dict):
        decision = str(gate.get("decision") or "").lower()
        if decision:
            return decision in {"rr_gate_pass_needs_fresh_oos", "research_pass", "pass", "rr_pass"}
    source_verdict = str(item.get("source_verdict") or item.get("verdict") or "").lower()
    if source_verdict:
        if source_verdict in {"do_not_trade", "holdout_fail_do_not_trade", "research_only", "watchlist_only"}:
            return False
        if source_verdict in {"pass", "research_pass"}:
            return True
    return None


def holdout_verdict(item: dict[str, Any]) -> str | None:
    value = item.get("holdout_verdict") or item.get("effective_holdout_verdict")
    return str(value) if value else None


def has_trade_level_data(item: dict[str, Any]) -> bool:
    trades = item.get("trades")
    if not isinstance(trades, list) or not trades:
        return False
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        if trade.get("entry") is not None and trade.get("stop") is not None and (
            trade.get("take_profit") is not None or trade.get("tp") is not None
        ):
            return True
    return False


def collect_candidates_from_payload(path: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    best = payload.get("best_candidate")
    if isinstance(best, dict):
        collected.append(best)
    for key in ("candidates", "top_results", "results", "top_items"):
        values = payload.get(key)
        if isinstance(values, list):
            collected.extend(item for item in values if isinstance(item, dict))

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in collected:
        cid = candidate_id(item)
        if cid in seen:
            continue
        summary = summary_for(item)
        if not summary:
            continue
        merged = dict(item)
        merged["_source_report"] = path.as_posix()
        merged["_candidate_id"] = cid
        seen.add(cid)
        out.append(merged)
    return out


def evaluate_candidate(item: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    metrics = metrics_from_summary(summary_for(item))
    stable = stable_folds(item)
    bootstrap_prob = bootstrap_prob_gt_0(item)
    research_pass = research_gate_pass(item)
    holdout = holdout_verdict(item)
    trade_data = has_trade_level_data(item)

    checks = {
        "min_trades": metrics["trades"] >= as_int(thresholds.get("min_trades")),
        "min_expectancy_r": (metrics["expectancy_r"] if metrics["expectancy_r"] is not None else -999.0)
        >= float(thresholds.get("min_expectancy_r", 0.05)),
        "min_winrate_pct": (metrics["winrate_pct"] if metrics["winrate_pct"] is not None else -999.0)
        >= float(thresholds.get("min_winrate_pct", 50.0)),
        "min_bootstrap_prob_gt_0": (bootstrap_prob if bootstrap_prob is not None else -999.0)
        >= float(thresholds.get("min_bootstrap_prob_gt_0", 0.8)),
        "min_stable_folds": stable >= as_int(thresholds.get("min_stable_folds")),
        "max_drawdown_r": (metrics["max_drawdown_r"] if metrics["max_drawdown_r"] is not None else -999.0)
        >= float(thresholds.get("max_drawdown_r", -10.0)),
        "max_losing_streak": metrics["max_losing_streak"] <= as_int(thresholds.get("max_losing_streak")),
        "research_gate_pass": (research_pass is True) if thresholds.get("require_research_gate_pass", True) else True,
        "has_trade_level_data": trade_data if thresholds.get("require_trade_level_data", True) else True,
        "no_holdout_fail": holdout != "holdout_fail_do_not_trade" if thresholds.get("require_no_holdout_fail", True) else True,
    }
    hard_blocks = [name for name, ok in checks.items() if not ok]

    if not hard_blocks:
        decision = "promoted_to_live_review_candidate"
    elif checks["min_expectancy_r"] and checks["max_drawdown_r"] and checks["max_losing_streak"] and metrics["trades"] >= 30:
        decision = "watchlist_no_promotion"
    else:
        decision = "blocked_no_promotion"

    return {
        "candidate_id": item["_candidate_id"],
        "source_report": item["_source_report"],
        "metrics": metrics,
        "stable_folds": stable,
        "bootstrap_prob_gt_0": bootstrap_prob,
        "research_gate_pass": research_pass,
        "holdout_verdict": holdout,
        "has_trade_level_data": trade_data,
        "checks": checks,
        "hard_blocks": hard_blocks,
        "promotion_decision": decision,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Candidate Promotion Gate",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-to-live-review promotion gate only.",
        "- No orders, no private credentials, no trade permission.",
        "- Promotion only allows card review; it does not allow execution.",
        "",
        "## Summary",
        "",
        f"- Evaluated: `{report['evaluated_count']}`",
        f"- Promoted: `{report['promoted_count']}`",
        f"- Watchlist: `{report['watchlist_count']}`",
        f"- Blocked: `{report['blocked_count']}`",
        "",
        "| Candidate | Decision | Trades | WR | ExpR | Boot P>0 | Stable | Blocks |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["candidates"][:30]:
        metrics = item["metrics"]
        lines.append(
            f"| `{item['candidate_id']}` | `{item['promotion_decision']}` | `{metrics['trades']}` | "
            f"`{metrics['winrate_pct']}` | `{metrics['expectancy_r']}` | `{item['bootstrap_prob_gt_0']}` | "
            f"`{item['stable_folds']}` | `{', '.join(item['hard_blocks']) or '-'}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Evaluate research candidates for promotion to live-review card pipeline")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    config = read_json(Path(args.config))
    thresholds = config.get("thresholds", {})
    sources = args.source or config.get("default_sources", [])
    source_status: list[dict[str, Any]] = []
    raw_candidates: list[dict[str, Any]] = []
    for raw_path in sources:
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            source_status.append({"path": str(path), "exists": False, "candidates": 0})
            continue
        payload = read_json(path)
        candidates = collect_candidates_from_payload(path, payload if isinstance(payload, dict) else {})
        source_status.append({"path": str(path), "exists": True, "candidates": len(candidates)})
        raw_candidates.extend(candidates)

    evaluated = [evaluate_candidate(item, thresholds) for item in raw_candidates]
    evaluated.sort(
        key=lambda item: (
            item["promotion_decision"] == "promoted_to_live_review_candidate",
            item["promotion_decision"] == "watchlist_no_promotion",
            item["metrics"]["expectancy_r"] if item["metrics"]["expectancy_r"] is not None else -999.0,
            item["metrics"]["trades"],
        ),
        reverse=True,
    )
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": config.get("runtime_boundary", {}),
        "config": str(Path(args.config)),
        "sources": source_status,
        "thresholds": thresholds,
        "evaluated_count": len(evaluated),
        "promoted_count": sum(1 for item in evaluated if item["promotion_decision"] == "promoted_to_live_review_candidate"),
        "watchlist_count": sum(1 for item in evaluated if item["promotion_decision"] == "watchlist_no_promotion"),
        "blocked_count": sum(1 for item in evaluated if item["promotion_decision"] == "blocked_no_promotion"),
        "candidates": evaluated,
        "can_trade": False,
    }
    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "evaluated_count": report["evaluated_count"],
                "promoted_count": report["promoted_count"],
                "watchlist_count": report["watchlist_count"],
                "blocked_count": report["blocked_count"],
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
