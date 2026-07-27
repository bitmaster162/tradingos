#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.event_feature_factory import (  # noqa: E402
    FeatureConfig,
    build_configs,
    build_features,
    generate_signals,
    load_csv_by_time,
    parse_list,
    stable_fold_count,
    verdict,
)
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402


DEFAULT_PROMOTION_REPORT = Path("docs/CANDIDATE_PROMOTION_GATE_2026-06-04.json")
DEFAULT_OUT_PREFIX = Path("docs/EVENT_FEATURE_TRADE_EXPORT_2026-06-07")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def select_strategy_ids(promotion_report: Path, explicit_ids: list[str], top_n: int) -> list[str]:
    if explicit_ids:
        return explicit_ids
    payload = read_json(promotion_report)
    selected: list[str] = []
    for item in payload.get("candidates", []):
        if item.get("promotion_decision") != "watchlist_no_promotion":
            continue
        cid = str(item.get("candidate_id") or "").strip()
        if cid and cid not in selected:
            selected.append(cid)
        if len(selected) >= top_n:
            break
    return selected


def config_index(intervals: list[str], max_strategies: int) -> dict[str, FeatureConfig]:
    return {config.strategy_id: config for config in build_configs(intervals, max_strategies)}


def export_config(
    config: FeatureConfig,
    *,
    cache_dir: str,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
    folds_count: int,
    min_trades: int,
    no_overlap: bool,
    oi_lag: int,
    spot_perp_lookback: int,
) -> dict[str, Any]:
    futures_path = Path(cache_dir) / "futures" / "BTCUSDT" / f"{config.interval}_klines.csv"
    spot_path = Path(cache_dir) / "spot" / "BTCUSDT" / f"{config.interval}_klines.csv"
    derivatives_path = Path(cache_dir) / "futures" / "BTCUSDT" / f"{config.interval}_oi_aligned.csv"
    bars = load_ohlcv(futures_path)
    spot_bars = load_ohlcv(spot_path) if spot_path.exists() else []
    spot_by_time = {bar.ts: bar for bar in spot_bars}
    derivatives_by_time = load_csv_by_time(derivatives_path)
    features = build_features(
        bars=bars,
        spot_by_time=spot_by_time,
        derivatives_by_time=derivatives_by_time,
        oi_lag=oi_lag,
        spot_perp_lookback=spot_perp_lookback,
        volume_window=20,
        atr_window=20,
    )
    signals = generate_signals(config, bars, features)
    trades = []
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: item["bar_index"]):
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"feature_factory_BTCUSDT_{config.interval}",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        record = asdict(trade)
        record["tp"] = record["take"]
        record["take_profit"] = record["take"]
        record["signal_bar_index"] = signal_index
        record["signal_ts"] = bars[signal_index].ts if 0 <= signal_index < len(bars) else None
        record["signal_reason"] = signal.get("reason")
        record["feature_snapshot"] = signal.get("feature_snapshot") or {}
        trades.append(record)
        if no_overlap:
            for offset in range(signal_index + 1, min(len(bars), signal_index + max_hold_bars + 2)):
                if bars[offset].ts == trade.exit_ts:
                    last_exit_bar = offset
                    break

    # Reconstruct dataclass-compatible trade objects for shared summary helpers.
    trade_objects = []
    for item in trades:
        trade_objects.append(
            SimpleNamespace(
                dataset_id=item["dataset_id"],
                strategy_id=item["strategy_id"],
                entry_ts=item["entry_ts"],
                exit_ts=item["exit_ts"],
                side=item["side"],
                entry=item["entry"],
                exit=item["exit"],
                stop=item["stop"],
                take=item["take"],
                atr=item["atr"],
                r_net=item["r_net"],
                exit_reason=item["exit_reason"],
                bars_held=item["bars_held"],
            )
        )
    summary = summarize_trades(trade_objects)
    folds = fold_summaries(trade_objects, folds_count)
    return {
        "id": config.strategy_id,
        "strategy_id": config.strategy_id,
        "family": config.family,
        "interval": config.interval,
        "params": config.params,
        "signals": len(signals),
        "summary": summary,
        "folds": folds,
        "stable_folds": stable_fold_count(folds),
        "verdict": verdict(summary, folds, min_trades),
        "research_gate": {
            "pass": False,
            "decision": "trade_level_export_only",
            "reason": "Trade-level export satisfies data availability checks only; it does not prove OOS robustness or grant promotion.",
        },
        "trades": trades,
        "sample_trades": trades[:8],
        "data_contract": {
            "futures_ohlcv": str(futures_path),
            "futures_rows": len(bars),
            "spot_ohlcv_exists": spot_path.exists(),
            "spot_rows": len(spot_bars),
            "derivatives_exists": derivatives_path.exists(),
            "derivatives_rows": len(derivatives_by_time),
            "entry_model": "next_bar_open_after_closed_signal_bar",
            "cost_bps_per_side": cost_bps_per_side,
            "no_overlap": no_overlap,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Event Feature Trade Export",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only trade-level export.",
        "- Exports every simulated trade for selected event-feature candidates.",
        "- Does not create OOS proof, paper permission or live-trading permission.",
        "- Promotion Gate may use this to verify trade-level data availability, but other blockers remain.",
        "",
        "## Result",
        "",
        f"- Requested strategy ids: `{', '.join(report['requested_strategy_ids']) or 'none'}`.",
        f"- Exported candidates: `{len(report['candidates'])}`.",
        f"- Missing strategy ids: `{', '.join(report['missing_strategy_ids']) or '-'}`.",
        "",
        "| Strategy | Family | TF | Signals | Trades | Winrate | Exp R | Net R | Stable Folds | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["candidates"]:
        summary = item["summary"]
        lines.append(
            f"| `{item['strategy_id']}` | `{item['family']}` | `{item['interval']}` | `{item['signals']}` | "
            f"`{summary['trades']}` | `{summary['winrate_pct']}` | `{summary['expectancy_r']}` | "
            f"`{summary['net_r_total']}` | `{item['stable_folds']}` | `{item['verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- This export is useful only as evidence plumbing.",
            "- A candidate still fails live review if sample size, holdout, bootstrap, stable folds or research gate fail.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Export full trade-level data for event-feature candidates")
    parser.add_argument("--promotion-report", default=str(DEFAULT_PROMOTION_REPORT))
    parser.add_argument("--strategy-id", action="append", default=[])
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--max-strategies", type=int, default=72)
    parser.add_argument("--stop-atr", type=float, default=1.5)
    parser.add_argument("--take-atr", type=float, default=2.0)
    parser.add_argument("--max-hold-bars", type=int, default=12)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    promotion_report = resolve_path(args.promotion_report)
    requested_ids = select_strategy_ids(promotion_report, args.strategy_id, args.top_n)
    configs = config_index(parse_list(args.intervals, str), args.max_strategies)
    candidates = []
    missing = []
    for strategy_id in requested_ids:
        config = configs.get(strategy_id)
        if config is None:
            missing.append(strategy_id)
            continue
        candidates.append(
            export_config(
                config,
                cache_dir=args.cache_dir,
                stop_atr=args.stop_atr,
                take_atr=args.take_atr,
                max_hold_bars=args.max_hold_bars,
                cost_bps_per_side=args.fee_bps + args.slippage_bps,
                folds_count=args.folds,
                min_trades=args.min_trades,
                no_overlap=not args.allow_overlap,
                oi_lag=args.oi_lag,
                spot_perp_lookback=args.spot_perp_lookback,
            )
        )

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_trade_level_export_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "source_promotion_report": str(promotion_report),
        "settings": vars(args),
        "requested_strategy_ids": requested_ids,
        "missing_strategy_ids": missing,
        "candidates": candidates,
        "top_results": candidates,
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
                "exported": len(candidates),
                "missing": missing,
                "top": candidates[0]["strategy_id"] if candidates else None,
                "top_summary": candidates[0]["summary"] if candidates else None,
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
