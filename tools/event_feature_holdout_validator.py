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

from tools.event_feature_factory import FeatureConfig, evaluate_config  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def combine_folds(folds: list[dict[str, Any]]) -> dict[str, Any]:
    trades = sum(int(item.get("trades") or 0) for item in folds)
    wins = sum(int(item.get("wins") or 0) for item in folds)
    losses = sum(int(item.get("losses") or 0) for item in folds)
    net = sum(float(item.get("net_r_total") or 0.0) for item in folds)
    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "winrate_pct": round(wins / trades * 100.0, 3) if trades else None,
        "expectancy_r": round(net / trades, 6) if trades else None,
        "net_r_total": round(net, 6),
        "stable_folds": sum(1 for item in folds if item.get("stable")),
    }


def select_items(source: dict[str, Any], top_n: int) -> list[dict[str, Any]]:
    items = [
        item
        for item in source.get("top_results", [])
        if item.get("verdict") in {"feature_candidate_needs_oos", "watchlist_only"}
    ]
    if not items:
        items = source.get("top_results", [])[:top_n]
    return items[:top_n]


def holdout_verdict(train: dict[str, Any], holdout: dict[str, Any], args: argparse.Namespace) -> str:
    train_ok = (
        (train.get("trades") or 0) >= args.min_train_trades
        and (train.get("expectancy_r") or -999.0) > 0
        and (train.get("stable_folds") or 0) >= args.min_train_stable_folds
    )
    holdout_ok = (
        (holdout.get("trades") or 0) >= args.min_holdout_trades
        and (holdout.get("expectancy_r") or -999.0) > 0
        and (holdout.get("winrate_pct") or 0.0) >= args.min_holdout_winrate
    )
    if train_ok and holdout_ok:
        return "holdout_pass_needs_fresh_oos"
    if train_ok and (holdout.get("expectancy_r") or -999.0) > 0:
        return "weak_holdout_positive"
    return "holdout_fail_do_not_trade"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Event Feature Holdout Validator",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Runtime Boundary",
        "",
        "- Research-only holdout sanity check.",
        "- Re-evaluates selected feature hypotheses and treats the last chronological fold as holdout.",
        "- This is stricter than the feature factory ranking, but still not a final OOS/paper proof.",
        "- No orders, no private credentials, no paper/live permission.",
        "",
        "## Result",
        "",
        f"- Source report: `{report['source_report']}`.",
        f"- Tested: `{report['tested']}`.",
        f"- Holdout pass: `{report['holdout_pass_count']}`.",
        f"- Weak holdout positive: `{report['weak_holdout_count']}`.",
        f"- Failed: `{report['holdout_fail_count']}`.",
        "",
        "| Strategy | Source Verdict | All Trades | All Exp R | Train Trades | Train Exp R | Holdout Trades | Holdout Winrate | Holdout Exp R | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["results"]:
        all_summary = item["all_summary"]
        train = item["train_summary"]
        holdout = item["holdout_summary"]
        lines.append(
            f"| `{item['strategy_id']}` | `{item['source_verdict']}` | `{all_summary['trades']}` | "
            f"`{all_summary['expectancy_r']}` | `{train['trades']}` | `{train['expectancy_r']}` | "
            f"`{holdout['trades']}` | `{holdout['winrate_pct']}` | `{holdout['expectancy_r']}` | `{item['holdout_verdict']}` |"
        )
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


def choose_next_action(report: dict[str, Any]) -> dict[str, str]:
    if report["holdout_pass_count"] > 0:
        return {
            "id": "build_fresh_oos_replay_for_holdout_passes",
            "reason": "At least one feature survived the last-fold sanity check; next proof must use a fresh data window or later market data.",
        }
    if report["weak_holdout_count"] > 0:
        return {
            "id": "tighten_feature_definitions_and_wait_for_fresh_data",
            "reason": "Some features stayed positive but did not meet sample or winrate requirements. Do not trade; refine and test later.",
        }
    return {
        "id": "reject_current_watchlist_and_process_next_docs",
        "reason": "The watchlist did not survive holdout sanity. Extract more concrete rules from documents before more tuning.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Holdout validator for event feature factory watchlist")
    parser.add_argument("--source-report", default="docs/EVENT_FEATURE_FACTORY_EXTENDED_2026-06-04.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--min-train-trades", type=int, default=40)
    parser.add_argument("--min-train-stable-folds", type=int, default=2)
    parser.add_argument("--min-holdout-trades", type=int, default=10)
    parser.add_argument("--min-holdout-winrate", type=float, default=50.0)
    parser.add_argument("--out-prefix", default="docs/EVENT_FEATURE_HOLDOUT_VALIDATION_2026-06-04")
    args = parser.parse_args()

    source_path = Path(args.source_report)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    settings = source.get("settings", {})
    selected = select_items(source, args.top_n)
    results: list[dict[str, Any]] = []
    for item in selected:
        config = FeatureConfig(
            strategy_id=str(item["strategy_id"]),
            family=str(item["family"]),
            interval=str(item["interval"]),
            params=dict(item.get("params") or {}),
        )
        evaluated = evaluate_config(
            config,
            cache_dir=args.cache_dir,
            stop_atr=float(settings.get("stop_atr", 1.5)),
            take_atr=float(settings.get("take_atr", 2.0)),
            max_hold_bars=int(settings.get("max_hold_bars", 12)),
            cost_bps_per_side=float(settings.get("fee_bps", 5.0)) + float(settings.get("slippage_bps", 2.0)),
            folds_count=int(settings.get("folds", 4)),
            min_trades=int(settings.get("min_trades", 100)),
            no_overlap=bool(settings.get("no_overlap", True)),
            oi_lag=int(settings.get("oi_lag", 4)),
            spot_perp_lookback=int(settings.get("spot_perp_lookback", 12)),
        )
        folds = evaluated.get("folds") or []
        train_folds = folds[:-1]
        holdout = folds[-1] if folds else {}
        train = combine_folds(train_folds)
        hv = holdout_verdict(train, holdout, args)
        results.append(
            {
                "strategy_id": config.strategy_id,
                "source_verdict": item.get("verdict"),
                "all_summary": evaluated["summary"],
                "train_summary": train,
                "holdout_summary": holdout,
                "holdout_verdict": hv,
                "params": config.params,
            }
        )

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_holdout_validator_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "source_report": str(source_path),
        "tested": len(results),
        "holdout_pass_count": sum(1 for item in results if item["holdout_verdict"] == "holdout_pass_needs_fresh_oos"),
        "weak_holdout_count": sum(1 for item in results if item["holdout_verdict"] == "weak_holdout_positive"),
        "holdout_fail_count": sum(1 for item in results if item["holdout_verdict"] == "holdout_fail_do_not_trade"),
        "settings": vars(args),
        "results": results,
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
                "tested": report["tested"],
                "holdout_pass_count": report["holdout_pass_count"],
                "weak_holdout_count": report["weak_holdout_count"],
                "holdout_fail_count": report["holdout_fail_count"],
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
