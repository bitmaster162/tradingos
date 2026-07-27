#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import INTERVAL_MS, candle_value  # noqa: E402
from tools.max_v11_candidate_validator import (  # noqa: E402
    atr14_at,
    bootstrap_report,
    fold_report,
    load_or_fetch,
    summarize_trades,
)
from tools.max_v15_state_filters import load_or_fetch_derivatives, precompute_htf_bias  # noqa: E402
from tools.max_v17_short_continuation_hardening import short_exit  # noqa: E402


@dataclass(frozen=True)
class ShortConfig:
    strategy_id: str
    mode: str
    trend_down_atr: float
    oi_min_delta_pct: float
    stop_atr: float
    take_atr: float
    max_hold_bars: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_list(value: str, cast: Any) -> list[Any]:
    return [cast(item.strip()) for item in value.split(",") if item.strip()]


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def split_index(rows: list[dict[str, str]], split_ts: str) -> int:
    boundary = parse_ts(split_ts)
    for index, row in enumerate(rows):
        if parse_ts(str(row.get("time"))) >= boundary:
            return index
    raise ValueError(f"split timestamp after data: {split_ts}")


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def build_features(
    rows: list[dict[str, str]],
    derivatives_rows: list[dict[str, str]],
    htf_biases: list[dict[str, Any]],
    warmup_bars: int,
) -> dict[int, dict[str, Any]]:
    derivatives_by_time = {str(row.get("time")): row for row in derivatives_rows}
    aligned = [derivatives_by_time.get(str(row.get("time"))) for row in rows]
    features: dict[int, dict[str, Any]] = {}
    for index in range(max(warmup_bars, 220), len(rows) - 1):
        derivative = aligned[index]
        previous_derivative = aligned[index - 12]
        if not isinstance(derivative, dict) or not isinstance(previous_derivative, dict):
            continue
        oi = finite_float(derivative.get("open_interest"))
        previous_oi = finite_float(previous_derivative.get("open_interest"))
        funding = finite_float(derivative.get("funding"))
        if oi is None or previous_oi in {None, 0.0} or funding is None:
            continue
        atr = atr14_at(rows, index)
        if not math.isfinite(atr) or atr <= 0:
            continue
        close = candle_value(rows[index], "close")
        previous_close = candle_value(rows[index - 20], "close")
        if not math.isfinite(close) or not math.isfinite(previous_close):
            continue
        previous = rows[index - 20 : index]
        previous_low = min(candle_value(row, "low") for row in previous)
        previous_high = max(candle_value(row, "high") for row in previous)
        low = candle_value(rows[index], "low")
        high = candle_value(rows[index], "high")
        bullish_sweep = low < previous_low and close > previous_low
        bearish_sweep = high > previous_high and close < previous_high
        sweep_side = "both" if bullish_sweep and bearish_sweep else "bullish" if bullish_sweep else "bearish" if bearish_sweep else "none"
        width = previous_high - previous_low
        near_low = close <= previous_low + max(width * 0.18, atr * 0.9)
        htf = htf_biases[index] if index < len(htf_biases) else {}
        features[index] = {
            "signal_time": rows[index].get("time"),
            "signal_row": index,
            "atr14": atr,
            "trend_strength_20_atr": (close - previous_close) / atr,
            "oi_delta_12_pct": (oi - previous_oi) / previous_oi * 100.0,
            "funding": funding,
            "htf_bias": htf.get("bias", "NEUTRAL"),
            "htf_regime": htf.get("regime", "unknown"),
            "sweep_side": sweep_side,
            "near_low": near_low,
        }
    return features


def signal_matches(config: ShortConfig, feature: dict[str, Any]) -> bool:
    if str(feature.get("htf_bias")) != "SHORT":
        return False
    if float(feature.get("trend_strength_20_atr", 999.0)) > config.trend_down_atr:
        return False
    if float(feature.get("oi_delta_12_pct", -999.0)) < config.oi_min_delta_pct:
        return False
    if config.mode == "base":
        return True
    if config.mode == "no_sweep":
        return feature.get("sweep_side") == "none"
    if config.mode == "funding_positive":
        return float(feature.get("funding", -999.0)) >= 0.0
    if config.mode == "near_low":
        return bool(feature.get("near_low"))
    raise ValueError(f"unsupported mode: {config.mode}")


def build_configs(args: argparse.Namespace) -> list[ShortConfig]:
    configs: list[ShortConfig] = []
    for mode in parse_list(args.modes, str):
        for trend in parse_list(args.trend_thresholds, float):
            for oi_min in parse_list(args.oi_thresholds, float):
                for take in parse_list(args.take_atrs, float):
                    for hold in parse_list(args.max_hold_bars, int):
                        strategy_id = (
                            f"short_cont_1h_{mode}_trend{trend:g}_oi{oi_min:g}"
                            f"_sl{args.stop_atr:g}_tp{take:g}_h{hold}"
                        )
                        configs.append(
                            ShortConfig(
                                strategy_id=strategy_id,
                                mode=mode,
                                trend_down_atr=trend,
                                oi_min_delta_pct=oi_min,
                                stop_atr=args.stop_atr,
                                take_atr=take,
                                max_hold_bars=hold,
                            )
                        )
    return configs


def simulate_window(
    config: ShortConfig,
    rows: list[dict[str, str]],
    features: dict[int, dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    fee_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    safe_end = min(end_index, len(rows) - config.max_hold_bars - 1)
    index = max(start_index, 220)
    while index < safe_end:
        feature = features.get(index)
        if feature is None or not signal_matches(config, feature):
            index += 1
            continue
        outcome = short_exit(
            rows=rows,
            signal_row=index,
            atr=float(feature["atr14"]),
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_bars,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        if outcome is None or int(outcome["exit_row"]) >= end_index:
            index += 1
            continue
        trades.append(
            {
                "index": len(trades) + 1,
                "candidate_id": config.strategy_id,
                "side": "SHORT",
                "setup": config.strategy_id,
                "signal_row": index,
                "entry_row": outcome["entry_row"],
                "exit_row": outcome["exit_row"],
                "signal_time": feature["signal_time"],
                "entry_time": rows[outcome["entry_row"]].get("time"),
                "exit_time": rows[outcome["exit_row"]].get("time"),
                "entry": round(float(outcome["entry"]), 8),
                "exit": round(float(outcome["exit"]), 8),
                "net_r": round(float(outcome["net_r"]), 6),
                "exit_reason": outcome["exit_reason"],
                "bars_held": outcome["bars_held"],
                "trend_strength_20_atr": round(float(feature["trend_strength_20_atr"]), 6),
                "oi_delta_12_pct": round(float(feature["oi_delta_12_pct"]), 6),
                "funding": feature["funding"],
                "htf_bias": feature["htf_bias"],
                "sweep_side": feature["sweep_side"],
            }
        )
        index = int(outcome["exit_row"]) + 1
    return trades


def stable_fold_count(folds: list[dict[str, Any]]) -> int:
    return sum(
        1
        for fold in folds
        if int(fold.get("trades") or 0) >= 5
        and finite_float(fold.get("expectancy_r")) is not None
        and float(fold["expectancy_r"]) > 0.0
    )


def evaluate_window(
    config: ShortConfig,
    rows: list[dict[str, str]],
    features: dict[int, dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    folds: int,
    fee_bps: float,
    slippage_bps: float,
    stress_extra_bps: float,
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    trades = simulate_window(
        config,
        rows,
        features,
        start_index=start_index,
        end_index=end_index,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    stress = simulate_window(
        config,
        rows,
        features,
        start_index=start_index,
        end_index=end_index,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps + stress_extra_bps,
    )
    fold_rows = fold_report(trades, rows_count=end_index, warmup_bars=start_index, folds=folds)
    bootstrap = bootstrap_report(trades, iterations=bootstrap_iterations, seed=bootstrap_seed) if bootstrap_iterations > 0 else {}
    return {
        "summary": summarize_trades(trades),
        "stable_folds": stable_fold_count(fold_rows),
        "folds": fold_rows,
        "bootstrap": bootstrap,
        "cost_stress": {
            "extra_bps_per_side": stress_extra_bps,
            "summary": summarize_trades(stress),
        },
        "sample_trades": trades[:3],
    }


def train_gate(window: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    bootstrap_prob = finite_float((window.get("bootstrap", {}).get("expectancy_r") or {}).get("prob_gt_0"))
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= args.min_train_trades,
        "min_expectancy_r": finite_float(summary.get("expectancy_r")) is not None and float(summary["expectancy_r"]) >= args.min_train_expectancy_r,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= args.min_train_stable_folds,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or 0.0) >= -abs(args.max_train_drawdown_r),
        "bootstrap_prob": bootstrap_prob is not None and bootstrap_prob >= args.min_train_bootstrap_prob,
        "cost_stress_positive": finite_float(stress.get("expectancy_r")) is not None and float(stress["expectancy_r"]) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def oos_gate(window: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= args.min_oos_trades,
        "min_expectancy_r": finite_float(summary.get("expectancy_r")) is not None and float(summary["expectancy_r"]) >= args.min_oos_expectancy_r,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= args.min_oos_stable_folds,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or 0.0) >= -abs(args.max_oos_drawdown_r),
        "cost_stress_positive": finite_float(stress.get("expectancy_r")) is not None and float(stress["expectancy_r"]) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    summary = row["train"]["summary"]
    stress = row["train"]["cost_stress"]["summary"]
    trades = max(1, int(summary.get("trades") or 0))
    return (
        float(stress.get("expectancy_r") or -999.0) * math.sqrt(trades),
        int(row["train"].get("stable_folds") or 0),
        float(summary.get("expectancy_r") or -999.0),
        trades,
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = resolve_path(args.cache_dir)
    rows, futures_source = load_or_fetch(
        use_cache=True,
        cache_dir=cache_dir,
        market="futures",
        symbol=args.symbol,
        interval="1h",
        limit=1000,
        pages=100,
    )
    htf_rows, htf_source = load_or_fetch(
        use_cache=True,
        cache_dir=cache_dir,
        market="futures",
        symbol=args.symbol,
        interval="4h",
        limit=1000,
        pages=100,
    )
    derivatives_rows, derivatives_source = load_or_fetch_derivatives(
        use_cache=True,
        cache_dir=cache_dir,
        symbol=args.symbol,
        interval="1h",
        rows=rows,
        limit=500,
        pages=100,
    )
    htf_biases = precompute_htf_bias(
        rows=rows,
        htf_rows=htf_rows,
        interval_ms=INTERVAL_MS["1h"],
        htf_interval="4h",
    )
    features = build_features(rows, derivatives_rows, htf_biases, args.warmup_bars)
    split = split_index(rows, args.split_ts)
    rng = random.Random(args.bootstrap_seed)
    results: list[dict[str, Any]] = []
    for config in build_configs(args):
        train = evaluate_window(
            config,
            rows,
            features,
            start_index=args.warmup_bars,
            end_index=split,
            folds=args.train_folds,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            bootstrap_iterations=args.bootstrap_iterations,
            bootstrap_seed=rng.randrange(1, 10_000_000),
        )
        gate = train_gate(train, args)
        results.append({"strategy_id": config.strategy_id, "config": asdict(config), "train": train, "train_gate": gate})
    qualified = [row for row in results if row["train_gate"]["pass"]]
    qualified.sort(key=rank_key, reverse=True)
    selected = qualified[0] if qualified else None
    oos = None
    gate = None
    decision = "reject_no_train_candidate"
    if selected:
        config = ShortConfig(**selected["config"])
        oos = evaluate_window(
            config,
            rows,
            features,
            start_index=split,
            end_index=len(rows),
            folds=args.oos_folds,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            stress_extra_bps=args.stress_extra_bps,
            bootstrap_iterations=0,
            bootstrap_seed=args.bootstrap_seed,
        )
        gate = oos_gate(oos, args)
        decision = "pass_oos_observer_candidate_not_trade_permission" if gate["pass"] else "reject_oos_gate_failed"
    results.sort(key=rank_key, reverse=True)
    derivative_times = {str(row.get("time")) for row in derivatives_rows}
    matched_derivatives = sum(str(row.get("time")) in derivative_times for row in rows)
    return {
        "generated_at": now_iso(),
        "method": "train_only_selection_then_untouched_calendar_oos",
        "selection_frozen_before_oos": True,
        "runtime_boundary": {"research_only": True, "sends_orders": False, "can_trade": False},
        "data": {
            "cache_dir": rel_path(cache_dir),
            "futures_source": futures_source,
            "htf_source": htf_source,
            "derivatives_source": derivatives_source,
            "rows": len(rows),
            "first_time": rows[0].get("time") if rows else None,
            "last_time": rows[-1].get("time") if rows else None,
            "split_ts": args.split_ts,
            "split_index": split,
            "feature_ready_rows": len(features),
            "derivatives_matched_rows": matched_derivatives,
            "derivatives_coverage_pct": round(matched_derivatives / len(rows) * 100.0, 3) if rows else 0.0,
            "spot_data_used": False,
        },
        "search": {"tested": len(results), "train_qualified": len(qualified)},
        "selected_on_train": selected,
        "oos": oos,
        "oos_gate": gate,
        "top_train_results": results[:20],
        "decision": decision,
        "next_action": "observer_only_forward_proof" if decision.startswith("pass_oos") else "reject_family_without_oos_reuse",
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    data = report["data"]
    selected = report.get("selected_on_train")
    lines = [
        "# SHORT Continuation Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- New independent SHORT family; no RANGE or EDGE parameter reuse.",
        "- Candidate selection uses only pre-2025 data.",
        "- OOS is opened only after one train winner is frozen.",
        "- Spot data is not used because pre-2025 spot coverage is unavailable in the current cache.",
        "- No paper/live permission and no orders.",
        "",
        "## Data",
        "",
        f"- Rows: `{data['rows']}` from `{data['first_time']}` to `{data['last_time']}`.",
        f"- Split: `{data['split_ts']}` at index `{data['split_index']}`.",
        f"- Feature-ready rows: `{data['feature_ready_rows']}`.",
        f"- Derivatives coverage: `{data['derivatives_coverage_pct']}%`.",
        "",
        "## Result",
        "",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    if selected:
        train = selected["train"]["summary"]
        oos = report["oos"]["summary"]
        stress = report["oos"]["cost_stress"]["summary"]
        lines.extend(
            [
                f"- Selected: `{selected['strategy_id']}`.",
                f"- Train: `{train.get('trades')}` trades, `{train.get('winrate_pct')}%`, `{train.get('expectancy_r')}`R, DD `{train.get('max_drawdown_r')}`R.",
                f"- OOS: `{oos.get('trades')}` trades, `{oos.get('winrate_pct')}%`, `{oos.get('expectancy_r')}`R, DD `{oos.get('max_drawdown_r')}`R.",
                f"- OOS +10bps/side: `{stress.get('expectancy_r')}`R.",
            ]
        )
    else:
        lines.append("- No train-qualified candidate; OOS was not used for rescue.")
    lines.extend(["", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Honest nested validation for an independent BTCUSDT 1H SHORT-continuation family")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--split-ts", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--modes", default="base,no_sweep,funding_positive,near_low")
    parser.add_argument("--trend-thresholds", default="-0.75,-1.0")
    parser.add_argument("--oi-thresholds", default="0,0.1")
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atrs", default="1.5,2.0")
    parser.add_argument("--max-hold-bars", default="8,12,16")
    parser.add_argument("--warmup-bars", type=int, default=220)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--train-folds", type=int, default=6)
    parser.add_argument("--oos-folds", type=int, default=4)
    parser.add_argument("--bootstrap-iterations", type=int, default=500)
    parser.add_argument("--bootstrap-seed", type=int, default=20260623)
    parser.add_argument("--min-train-trades", type=int, default=60)
    parser.add_argument("--min-train-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-train-stable-folds", type=int, default=4)
    parser.add_argument("--min-train-bootstrap-prob", type=float, default=0.80)
    parser.add_argument("--max-train-drawdown-r", type=float, default=12.0)
    parser.add_argument("--min-oos-trades", type=int, default=30)
    parser.add_argument("--min-oos-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-oos-stable-folds", type=int, default=2)
    parser.add_argument("--max-oos-drawdown-r", type=float, default=8.0)
    parser.add_argument("--out-prefix", default="docs/SHORT_CONTINUATION_NESTED_HOLDOUT_2026-06-23")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "tested": report["search"]["tested"],
                "train_qualified": report["search"]["train_qualified"],
                "selected": report.get("selected_on_train", {}).get("strategy_id") if report.get("selected_on_train") else None,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
