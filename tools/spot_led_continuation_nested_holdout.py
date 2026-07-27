#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import Counter, deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HOUR_MS = 3_600_000
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.funding_settlement_reversion_nested_holdout import (  # noqa: E402
    funding_adjustment_r,
    read_funding_events,
)
from tools.liquidity_sweep_hardening import (  # noqa: E402
    Trade,
    fold_summaries,
    simulate_trade,
    summarize_trades,
)
from tools.range_family_validator import load_interval_payload  # noqa: E402


@dataclass(frozen=True)
class SpotLeadConfig:
    strategy_id: str
    return_lookback_hours: int
    divergence_z_window_hours: int
    entry_abs_z: float
    side: str
    volume_filter: str
    stop_atr: float
    take_atr: float = 2.0
    max_hold_hours: int = 8
    volume_z_window_hours: int = 168


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def snapshot_provenance(cache: Path) -> dict[str, Any] | None:
    path = cache / "SNAPSHOT_MANIFEST.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return {
        "snapshot_id": payload.get("snapshot_id"),
        "profile": payload.get("profile"),
        "dataset_sha256": payload.get("dataset_sha256"),
        "manifest_path": portable_path(path),
    }


def read_ohlcv(path: Path) -> dict[int, dict[str, float]]:
    rows: dict[int, dict[str, float]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = int(row.get("time_ms") or int(parse_ts(row["time"]).timestamp() * 1000))
                close = float(row["close"])
                volume = float(row["volume"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(close) and math.isfinite(volume):
                rows[timestamp - timestamp % HOUR_MS] = {"close": close, "volume": volume}
    return rows


def causal_rolling_z(values: list[float | None], window: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    prior: deque[float] = deque()
    rolling_sum = 0.0
    rolling_sq = 0.0
    for index, value in enumerate(values):
        if value is None or not math.isfinite(value):
            continue
        if len(prior) == window:
            mean = rolling_sum / window
            variance = max(0.0, rolling_sq / window - mean * mean)
            std = math.sqrt(variance)
            if std > 1e-15:
                output[index] = (value - mean) / std
            old = prior.popleft()
            rolling_sum -= old
            rolling_sq -= old * old
        prior.append(value)
        rolling_sum += value
        rolling_sq += value * value
    return output


def build_configs() -> list[SpotLeadConfig]:
    configs: list[SpotLeadConfig] = []
    for lookback in (1, 3):
        for window in (168, 336):
            for entry_z in (1.5, 2.0, 2.5):
                for side in ("LONG_SPOT_LEADS", "SHORT_SPOT_LEADS"):
                    for volume_filter in ("none", "spot_relative_volume_leads"):
                        for stop_atr in (1.0, 1.5):
                            strategy_id = (
                                f"spot_lead_r{lookback}_z{window}_e{entry_z:g}_{side.lower()}_"
                                f"{volume_filter}_sl{stop_atr:g}_tp2_h8"
                            )
                            configs.append(
                                SpotLeadConfig(
                                    strategy_id=strategy_id,
                                    return_lookback_hours=lookback,
                                    divergence_z_window_hours=window,
                                    entry_abs_z=entry_z,
                                    side=side,
                                    volume_filter=volume_filter,
                                    stop_atr=stop_atr,
                                )
                            )
    return configs


def signal_matches(
    config: SpotLeadConfig,
    *,
    spot_return_pct: float,
    divergence_z: float,
    spot_volume_z: float | None,
    futures_volume_z: float | None,
) -> bool:
    if config.side == "LONG_SPOT_LEADS":
        direction_ok = spot_return_pct > 0 and divergence_z >= config.entry_abs_z
    else:
        direction_ok = spot_return_pct < 0 and divergence_z <= -config.entry_abs_z
    if config.volume_filter == "none":
        volume_ok = True
    else:
        volume_ok = (
            isinstance(spot_volume_z, (int, float))
            and isinstance(futures_volume_z, (int, float))
            and math.isfinite(float(spot_volume_z))
            and math.isfinite(float(futures_volume_z))
            and float(spot_volume_z) > float(futures_volume_z)
        )
    return bool(direction_ok and volume_ok)


def aligned_series(bars: list[Any], spot: dict[int, dict[str, float]]) -> dict[str, list[float | None]]:
    spot_close: list[float | None] = []
    spot_volume: list[float | None] = []
    futures_close: list[float | None] = []
    futures_volume: list[float | None] = []
    for bar in bars:
        hour = int(parse_ts(str(bar.ts)).timestamp() * 1000)
        spot_row = spot.get(hour)
        spot_close.append(float(spot_row["close"]) if spot_row else None)
        spot_volume.append(float(spot_row["volume"]) if spot_row else None)
        futures_close.append(float(bar.close))
        futures_volume.append(float(bar.volume))
    return {
        "spot_close": spot_close,
        "spot_volume": spot_volume,
        "futures_close": futures_close,
        "futures_volume": futures_volume,
    }


def return_series(values: list[float | None], lookback: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    for index in range(lookback, len(values)):
        current = values[index]
        previous = values[index - lookback]
        if current is None or previous is None or previous <= 0:
            continue
        output[index] = (current / previous - 1.0) * 100.0
    return output


def build_signal_features(
    series: dict[str, list[float | None]], lookback: int, divergence_window: int
) -> dict[str, list[float | None]]:
    spot_returns = return_series(series["spot_close"], lookback)
    futures_returns = return_series(series["futures_close"], lookback)
    divergence = [
        (spot - futures) if spot is not None and futures is not None else None
        for spot, futures in zip(spot_returns, futures_returns)
    ]
    return {
        "spot_return_pct": spot_returns,
        "divergence_pct": divergence,
        "divergence_z": causal_rolling_z(divergence, divergence_window),
        "spot_volume_z": causal_rolling_z(series["spot_volume"], 168),
        "futures_volume_z": causal_rolling_z(series["futures_volume"], 168),
    }


def generate_signals(
    config: SpotLeadConfig,
    bars: list[Any],
    features: list[dict[str, Any]],
    signal_features: dict[str, list[float | None]],
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        if index + config.max_hold_hours + 2 >= len(bars):
            continue
        spot_return = signal_features["spot_return_pct"][index]
        divergence = signal_features["divergence_pct"][index]
        divergence_z = signal_features["divergence_z"][index]
        if spot_return is None or divergence is None or divergence_z is None:
            continue
        feature = features[index]
        atr = feature.get("atr")
        if not isinstance(atr, (int, float)) or float(atr) <= 0:
            continue
        spot_volume_z = signal_features["spot_volume_z"][index]
        futures_volume_z = signal_features["futures_volume_z"][index]
        if not signal_matches(
            config,
            spot_return_pct=float(spot_return),
            divergence_z=float(divergence_z),
            spot_volume_z=spot_volume_z,
            futures_volume_z=futures_volume_z,
        ):
            continue
        signals.append(
            {
                "bar_index": index,
                "side_hint": "LONG" if config.side == "LONG_SPOT_LEADS" else "SHORT",
                "atr": float(atr),
                "spot_return_pct": float(spot_return),
                "spot_perp_divergence_pct": float(divergence),
                "spot_perp_divergence_z": float(divergence_z),
                "spot_volume_z": spot_volume_z,
                "futures_volume_z": futures_volume_z,
                "reason": "spot_led_return_divergence",
            }
        )
    return signals


def replay(
    config: SpotLeadConfig,
    bars: list[Any],
    signals: list[dict[str, Any]],
    funding_events: list[dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    cost_bps: float,
) -> list[Trade]:
    prices = {int(parse_ts(str(bar.ts)).timestamp() * 1000): float(bar.close) for bar in bars}
    bar_index = {str(bar.ts): index for index, bar in enumerate(bars)}
    trades: list[Trade] = []
    last_exit = start_index - 1
    for signal in sorted(signals, key=lambda item: int(item["bar_index"])):
        index = int(signal["bar_index"])
        if index < start_index or index <= last_exit:
            continue
        if index + config.max_hold_hours + 1 >= end_index:
            continue
        trade = simulate_trade(
            dataset_id="spot_led_continuation_BTCUSDT_1h",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_hours,
            cost_bps_per_side=cost_bps,
        )
        if trade is None:
            continue
        adjustment = funding_adjustment_r(trade, trade.side, config.stop_atr, funding_events, prices)
        trade = replace(trade, r_net=round(trade.r_net + adjustment, 6))
        trades.append(trade)
        last_exit = bar_index.get(trade.exit_ts, index)
    return trades


def bootstrap_probability(values: list[float], iterations: int = 2_000, seed: int = 20260624) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        positive += int(statistics.mean(rng.choice(values) for _ in values) > 0)
    return round(positive / iterations, 6)


def evaluate(
    config: SpotLeadConfig,
    bars: list[Any],
    signals: list[dict[str, Any]],
    funding_events: list[dict[str, Any]],
    *,
    start_index: int,
    end_index: int,
    cost_bps: float,
    stress_extra_bps: float,
    folds: int,
) -> dict[str, Any]:
    trades = replay(config, bars, signals, funding_events, start_index=start_index, end_index=end_index, cost_bps=cost_bps)
    stressed = replay(
        config,
        bars,
        signals,
        funding_events,
        start_index=start_index,
        end_index=end_index,
        cost_bps=cost_bps + stress_extra_bps,
    )
    summary = summarize_trades(trades)
    fold_rows = fold_summaries(trades, folds)
    stable_folds = sum(1 for row in fold_rows if row.get("stable"))
    stress_summary = summarize_trades(stressed)
    bootstrap_eligible = bool(
        int(summary.get("trades") or 0) >= 80
        and float(summary.get("expectancy_r") or -999.0) >= 0.08
        and stable_folds >= 3
        and float(summary.get("max_drawdown_r") or -999.0) >= -15.0
        and float(stress_summary.get("expectancy_r") or -999.0) > 0.0
    )
    return {
        "signals": len(signals),
        "summary": summary,
        "stable_folds": stable_folds,
        "folds": fold_rows,
        "bootstrap_probability_expectancy_gt_0": (
            bootstrap_probability([trade.r_net for trade in trades]) if bootstrap_eligible else None
        ),
        "cost_stress": {"extra_bps_per_side": stress_extra_bps, "summary": stress_summary},
        "sample_trades": [asdict(trade) for trade in trades[:3]],
    }


def gate(result: dict[str, Any], stage: str) -> dict[str, Any]:
    summary = result["summary"]
    stress = result["cost_stress"]["summary"]
    if stage == "train":
        checks = {
            "min_trades": int(summary.get("trades") or 0) >= 80,
            "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= 0.08,
            "min_positive_folds": int(result.get("stable_folds") or 0) >= 3,
            "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -15.0,
            "screening_bootstrap_probability": float(result.get("bootstrap_probability_expectancy_gt_0") or 0.0) >= 0.95,
            "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
        }
    else:
        checks = {
            "min_trades": int(summary.get("trades") or 0) >= 20,
            "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= 0.05,
            "min_positive_folds": int(result.get("stable_folds") or 0) >= 2,
            "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -8.0,
            "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0.0,
        }
    return {"pass": all(checks.values()), "checks": checks}


def split_index(bars: list[Any], timestamp: str) -> int:
    boundary = parse_ts(timestamp)
    for index, bar in enumerate(bars):
        if parse_ts(str(bar.ts)) >= boundary:
            return index
    raise ValueError(f"split after data: {timestamp}")


def rank_key(item: dict[str, Any]) -> tuple[float, int, float, int]:
    summary = item["train"]["summary"]
    stress = item["train"]["cost_stress"]["summary"]
    trades = int(summary.get("trades") or 0)
    return (
        float(stress.get("expectancy_r") or -999.0) * math.sqrt(max(1, trades)),
        int(item["train"].get("stable_folds") or 0),
        float(summary.get("expectancy_r") or -999.0),
        trades,
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Spot-Led Continuation Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "- Prospective hypothesis `HYP-SPOT-LEAD-001` was registered before code.",
        "- Signal uses completed 1H spot/perpetual returns, causal rolling z-scores and next-hour perpetual entry.",
        "- Train is 2021-2023; validation is 2024; OOS from 2025 opens only after prior gates pass.",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    selected = report.get("selected_on_train")
    if selected:
        train = selected["train"]["summary"]
        lines.append(f"- Frozen train candidate: `{selected['strategy_id']}`, `{train['trades']}` trades, `{train['expectancy_r']}`R.")
    else:
        best = report["top_train_results_regardless_of_gate"][0]
        train = best["train"]["summary"]
        lines.append(f"- Best rejected: `{best['strategy_id']}`, `{train['trades']}` trades, `{train['expectancy_r']}`R.")
        lines.append("- Validation and OOS remained unopened.")
    lines.extend(["- Registry-level Bonferroni assessment is applied by the verified runner.", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nested holdout for BTC spot-led perpetual continuation")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--cost-bps-per-side", type=float, default=7.0)
    parser.add_argument("--stress-extra-bps", type=float, default=10.0)
    parser.add_argument("--out-prefix", default="docs/SPOT_LED_CONTINUATION_NESTED_HOLDOUT_2026-06-24")
    parser.add_argument("--lock-path", default="configs/SPOT_LED_CONTINUATION_RESEARCH_LOCK.json")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    bars, features, _ = load_interval_payload(cache, "1h", 12, 12)
    spot = read_ohlcv(cache / "spot" / "BTCUSDT" / "1h_klines.csv")
    funding_events = read_funding_events(cache / "futures" / "BTCUSDT" / "funding_raw.csv")
    series = aligned_series(bars, spot)
    train_end = split_index(bars, args.train_end)
    validation_end = split_index(bars, args.validation_end)
    feature_cache: dict[tuple[int, int], dict[str, list[float | None]]] = {}
    signal_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    candidates: list[dict[str, Any]] = []

    for config in build_configs():
        feature_key = (config.return_lookback_hours, config.divergence_z_window_hours)
        if feature_key not in feature_cache:
            feature_cache[feature_key] = build_signal_features(series, *feature_key)
        signal_key = feature_key + (config.entry_abs_z, config.side, config.volume_filter)
        if signal_key not in signal_cache:
            signal_cache[signal_key] = generate_signals(config, bars, features, feature_cache[feature_key])
        train = evaluate(
            config,
            bars,
            signal_cache[signal_key],
            funding_events,
            start_index=0,
            end_index=train_end,
            cost_bps=args.cost_bps_per_side,
            stress_extra_bps=args.stress_extra_bps,
            folds=4,
        )
        candidates.append(
            {
                "strategy_id": config.strategy_id,
                "config": asdict(config),
                "train": train,
                "train_gate": gate(train, "train"),
                "_signals": signal_cache[signal_key],
            }
        )

    candidates.sort(key=rank_key, reverse=True)
    qualified = [item for item in candidates if item["train_gate"]["pass"]]
    selected = qualified[0] if qualified else None
    validation = validation_gate = oos = oos_gate = None
    oos_opened = False
    decision = "reject_no_train_qualified_spot_lead_candidate"
    if selected:
        config = SpotLeadConfig(**selected["config"])
        validation = evaluate(
            config,
            bars,
            selected["_signals"],
            funding_events,
            start_index=train_end,
            end_index=validation_end,
            cost_bps=args.cost_bps_per_side,
            stress_extra_bps=args.stress_extra_bps,
            folds=3,
        )
        validation_gate = gate(validation, "validation")
        decision = "reject_validation_gate_failed_oos_unopened"
        if validation_gate["pass"]:
            oos_opened = True
            oos = evaluate(
                config,
                bars,
                selected["_signals"],
                funding_events,
                start_index=validation_end,
                end_index=len(bars),
                cost_bps=args.cost_bps_per_side,
                stress_extra_bps=args.stress_extra_bps,
                folds=3,
            )
            oos_gate = gate(oos, "validation")
            decision = "spot_lead_candidate_requires_registry_review" if oos_gate["pass"] else "reject_oos_gate_failed"

    failed_checks = Counter(
        name
        for item in candidates
        for name, passed in item["train_gate"]["checks"].items()
        if not passed
    )

    def public(item: dict[str, Any] | None) -> dict[str, Any] | None:
        if item is None:
            return None
        return {key: value for key, value in item.items() if not key.startswith("_")}

    report = {
        "generated_at": now_iso(),
        "hypothesis_id": "HYP-SPOT-LEAD-001",
        "family": "SPOT_LED_CONTINUATION_1H",
        "method": "prospective_train_search_then_validation_then_conditionally_open_oos",
        "data": {
            "cache_dir": portable_path(cache),
            "snapshot": snapshot_provenance(cache),
            "bars": len(bars),
            "spot_aligned_bars": sum(value is not None for value in series["spot_close"]),
            "funding_events": len(funding_events),
            "first": bars[0].ts,
            "last": bars[-1].ts,
            "train_end": args.train_end,
            "validation_end": args.validation_end,
        },
        "search": {
            "tested": len(candidates),
            "unique_signal_sets": len(signal_cache),
            "train_qualified": len(qualified),
            "failed_check_counts": dict(sorted(failed_checks.items())),
            "oos_used_for_selection": False,
        },
        "protocol": {
            "completed_bars_only": True,
            "causal_z_excludes_signal_bar": True,
            "entry_next_hour": True,
            "max_hold_hours": 8,
            "cost_bps_per_side": args.cost_bps_per_side,
            "stress_extra_bps_per_side": args.stress_extra_bps,
            "bonferroni_required_probability": 0.9994791666666667,
        },
        "top_train_candidates": [public(item) for item in qualified[:10]],
        "top_train_results_regardless_of_gate": [public(item) for item in candidates[:10]],
        "selected_on_train": public(selected),
        "validation": validation,
        "validation_gate": validation_gate,
        "oos_opened": oos_opened,
        "oos": oos,
        "oos_gate": oos_gate,
        "runtime_boundary": {
            "research_only": True,
            "observer_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "decision": decision,
        "can_trade": False,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    lock = {
        "schema_version": 1,
        "hypothesis_id": report["hypothesis_id"],
        "family": report["family"],
        "enabled": False,
        "status": decision,
        "selected_on_train": selected["config"] if selected else None,
        "validation_gate": validation_gate,
        "oos_opened": oos_opened,
        "source_report": portable_path(out.with_suffix(".json")),
        "boundaries": {
            "observer_allowed": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    lock_path = resolve_path(args.lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "tested": len(candidates),
                "train_qualified": len(qualified),
                "selected": selected["strategy_id"] if selected else None,
                "validation_pass": validation_gate["pass"] if validation_gate else False,
                "oos_opened": oos_opened,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
