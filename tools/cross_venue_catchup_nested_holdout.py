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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MINUTE_MS = 60_000
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class CatchupConfig:
    strategy_id: str
    return_lookback_minutes: int
    divergence_z_window_minutes: int
    entry_z: float
    min_coinbase_return_bps: float
    hold_minutes: int
    min_window_coverage: float = 0.95


@dataclass(frozen=True)
class CatchupTrade:
    strategy_id: str
    signal_ts: str
    entry_ts: str
    exit_ts: str
    entry: float
    exit: float
    gross_bps: float
    net_bps: float
    hold_minutes: int
    coinbase_return_bps: float
    divergence_bps: float
    divergence_z: float


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


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


def read_binance(path: Path) -> dict[int, dict[str, float]]:
    rows: dict[int, dict[str, float]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = int(row["time_ms"])
                rows[timestamp] = {
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def read_aligned(path: Path) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append(
                    {
                        "time_ms": int(row["time_ms"]),
                        "binance_close": float(row["binance_close"]),
                        "coinbase_close": float(row["coinbase_close"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(rows, key=lambda item: int(item["time_ms"]))


def build_configs() -> list[CatchupConfig]:
    configs: list[CatchupConfig] = []
    for lookback in (1, 2, 5):
        for window in (360, 1440):
            for entry_z in (2.0, 2.5, 3.0):
                for minimum_return in (5.0, 10.0):
                    for hold in (1, 2, 5):
                        strategy_id = (
                            f"coinbase_binance_catchup_lb{lookback}_z{window}_e{entry_z:g}_"
                            f"r{minimum_return:g}_h{hold}"
                        )
                        configs.append(
                            CatchupConfig(
                                strategy_id=strategy_id,
                                return_lookback_minutes=lookback,
                                divergence_z_window_minutes=window,
                                entry_z=entry_z,
                                min_coinbase_return_bps=minimum_return,
                                hold_minutes=hold,
                            )
                        )
    return configs


def return_features(
    aligned: list[dict[str, float | int]], lookback_minutes: int
) -> list[dict[str, float | int]]:
    by_time = {int(row["time_ms"]): row for row in aligned}
    output: list[dict[str, float | int]] = []
    lag_ms = lookback_minutes * MINUTE_MS
    for row in aligned:
        timestamp = int(row["time_ms"])
        prior = by_time.get(timestamp - lag_ms)
        if prior is None:
            continue
        binance_prior = float(prior["binance_close"])
        coinbase_prior = float(prior["coinbase_close"])
        if binance_prior <= 0 or coinbase_prior <= 0:
            continue
        binance_return = (float(row["binance_close"]) / binance_prior - 1.0) * 10_000
        coinbase_return = (float(row["coinbase_close"]) / coinbase_prior - 1.0) * 10_000
        output.append(
            {
                "time_ms": timestamp,
                "binance_return_bps": binance_return,
                "coinbase_return_bps": coinbase_return,
                "divergence_bps": coinbase_return - binance_return,
            }
        )
    return output


def causal_time_z(
    features: list[dict[str, float | int]], window_minutes: int, min_coverage: float = 0.95
) -> dict[int, float]:
    prior: deque[tuple[int, float]] = deque()
    rolling_sum = 0.0
    rolling_sq = 0.0
    output: dict[int, float] = {}
    window_ms = window_minutes * MINUTE_MS
    required = math.ceil(window_minutes * min_coverage)
    for row in features:
        timestamp = int(row["time_ms"])
        value = float(row["divergence_bps"])
        while prior and prior[0][0] < timestamp - window_ms:
            _, old = prior.popleft()
            rolling_sum -= old
            rolling_sq -= old * old
        if len(prior) >= required:
            mean = rolling_sum / len(prior)
            variance = max(0.0, rolling_sq / len(prior) - mean * mean)
            std = math.sqrt(variance)
            if std > 1e-15:
                output[timestamp] = (value - mean) / std
        prior.append((timestamp, value))
        rolling_sum += value
        rolling_sq += value * value
    return output


def generate_signals(
    config: CatchupConfig,
    features: list[dict[str, float | int]],
    z_values: dict[int, float],
) -> list[dict[str, float | int]]:
    signals: list[dict[str, float | int]] = []
    for row in features:
        timestamp = int(row["time_ms"])
        z_value = z_values.get(timestamp)
        coinbase_return = float(row["coinbase_return_bps"])
        if z_value is None:
            continue
        if coinbase_return < config.min_coinbase_return_bps or z_value < config.entry_z:
            continue
        signals.append(
            {
                "time_ms": timestamp,
                "coinbase_return_bps": coinbase_return,
                "divergence_bps": float(row["divergence_bps"]),
                "divergence_z": z_value,
            }
        )
    return signals


def replay(
    config: CatchupConfig,
    signals: list[dict[str, float | int]],
    binance: dict[int, dict[str, float]],
    *,
    start_ms: int,
    end_ms: int,
    cost_bps_per_side: float,
) -> list[CatchupTrade]:
    trades: list[CatchupTrade] = []
    last_exit_ms = start_ms - 1
    for signal in signals:
        signal_ms = int(signal["time_ms"])
        if signal_ms < start_ms or signal_ms >= end_ms:
            continue
        entry_ms = signal_ms + MINUTE_MS
        exit_ms = signal_ms + config.hold_minutes * MINUTE_MS
        if entry_ms <= last_exit_ms or exit_ms >= end_ms:
            continue
        entry_row = binance.get(entry_ms)
        exit_row = binance.get(exit_ms)
        if entry_row is None or exit_row is None or entry_row["open"] <= 0:
            continue
        entry = float(entry_row["open"])
        exit_price = float(exit_row["close"])
        gross_bps = (exit_price / entry - 1.0) * 10_000
        net_bps = gross_bps - 2.0 * cost_bps_per_side
        trades.append(
            CatchupTrade(
                strategy_id=config.strategy_id,
                signal_ts=iso_from_ms(signal_ms),
                entry_ts=iso_from_ms(entry_ms),
                exit_ts=iso_from_ms(exit_ms),
                entry=entry,
                exit=exit_price,
                gross_bps=round(gross_bps, 6),
                net_bps=round(net_bps, 6),
                hold_minutes=config.hold_minutes,
                coinbase_return_bps=round(float(signal["coinbase_return_bps"]), 6),
                divergence_bps=round(float(signal["divergence_bps"]), 6),
                divergence_z=round(float(signal["divergence_z"]), 6),
            )
        )
        last_exit_ms = exit_ms
    return trades


def summarize(trades: list[CatchupTrade]) -> dict[str, Any]:
    values = [trade.net_bps for trade in trades]
    if not values:
        return {
            "trades": 0,
            "wins": 0,
            "winrate_pct": 0.0,
            "mean_net_bps": None,
            "median_net_bps": None,
            "total_net_bps": 0.0,
            "max_drawdown_bps": None,
        }
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    wins = sum(value > 0 for value in values)
    return {
        "trades": len(values),
        "wins": wins,
        "winrate_pct": round(wins / len(values) * 100.0, 6),
        "mean_net_bps": round(statistics.mean(values), 6),
        "median_net_bps": round(statistics.median(values), 6),
        "total_net_bps": round(sum(values), 6),
        "max_drawdown_bps": round(max_drawdown, 6),
    }


def fold_summaries(trades: list[CatchupTrade], folds: int) -> list[dict[str, Any]]:
    if not trades:
        return []
    output: list[dict[str, Any]] = []
    for fold in range(folds):
        start = len(trades) * fold // folds
        end = len(trades) * (fold + 1) // folds
        summary = summarize(trades[start:end])
        output.append({**summary, "fold": fold + 1, "stable": float(summary.get("mean_net_bps") or 0.0) > 0.0})
    return output


def bootstrap_probability(values: list[float], iterations: int = 2_000, seed: int = 20260624) -> float | None:
    if not values:
        return None
    rng = random.Random(seed)
    positive = 0
    for _ in range(iterations):
        positive += int(statistics.mean(rng.choice(values) for _ in values) > 0.0)
    return round(positive / iterations, 6)


def evaluate(
    config: CatchupConfig,
    signals: list[dict[str, float | int]],
    binance: dict[int, dict[str, float]],
    *,
    start_ms: int,
    end_ms: int,
    cost_bps_per_side: float,
    stress_extra_bps_per_side: float,
    folds: int,
) -> dict[str, Any]:
    trades = replay(
        config,
        signals,
        binance,
        start_ms=start_ms,
        end_ms=end_ms,
        cost_bps_per_side=cost_bps_per_side,
    )
    stressed = replay(
        config,
        signals,
        binance,
        start_ms=start_ms,
        end_ms=end_ms,
        cost_bps_per_side=cost_bps_per_side + stress_extra_bps_per_side,
    )
    summary = summarize(trades)
    stress_summary = summarize(stressed)
    fold_rows = fold_summaries(trades, folds)
    stable_folds = sum(1 for row in fold_rows if row.get("stable"))
    bootstrap_eligible = bool(
        int(summary.get("trades") or 0) >= 50
        and float(summary.get("mean_net_bps") or -999.0) >= 2.0
        and stable_folds >= 3
        and float(summary.get("max_drawdown_bps") or -999_999.0) >= -500.0
        and float(stress_summary.get("mean_net_bps") or -999.0) > 0.0
    )
    return {
        "signals": len(signals),
        "summary": summary,
        "stable_folds": stable_folds,
        "folds": fold_rows,
        "bootstrap_probability_mean_gt_0": (
            bootstrap_probability([trade.net_bps for trade in trades]) if bootstrap_eligible else None
        ),
        "cost_stress": {
            "extra_bps_per_side": stress_extra_bps_per_side,
            "summary": stress_summary,
        },
        "sample_trades": [asdict(trade) for trade in trades[:3]],
    }


def gate(result: dict[str, Any], stage: str) -> dict[str, Any]:
    summary = result["summary"]
    stress = result["cost_stress"]["summary"]
    if stage == "train":
        checks = {
            "min_trades": int(summary.get("trades") or 0) >= 50,
            "min_mean_net_bps": float(summary.get("mean_net_bps") or -999.0) >= 2.0,
            "min_positive_folds": int(result.get("stable_folds") or 0) >= 3,
            "max_drawdown_bps": float(summary.get("max_drawdown_bps") or -999_999.0) >= -500.0,
            "screening_bootstrap_probability": float(result.get("bootstrap_probability_mean_gt_0") or 0.0) >= 0.95,
            "cost_stress_positive": float(stress.get("mean_net_bps") or -999.0) > 0.0,
        }
    else:
        checks = {
            "min_trades": int(summary.get("trades") or 0) >= 20,
            "min_mean_net_bps": float(summary.get("mean_net_bps") or -999.0) > 0.0,
            "min_positive_folds": int(result.get("stable_folds") or 0) >= 2,
            "max_drawdown_bps": float(summary.get("max_drawdown_bps") or -999_999.0) >= -300.0,
            "cost_stress_positive": float(stress.get("mean_net_bps") or -999.0) > 0.0,
        }
    return {"pass": all(checks.values()), "checks": checks}


def rank_key(item: dict[str, Any]) -> tuple[float, int, float, int]:
    summary = item["train"]["summary"]
    stress = item["train"]["cost_stress"]["summary"]
    trades = int(summary.get("trades") or 0)
    return (
        float(stress.get("mean_net_bps") or -999.0) * math.sqrt(max(1, trades)),
        int(item["train"].get("stable_folds") or 0),
        float(summary.get("mean_net_bps") or -999.0),
        trades,
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-Venue Catch-up Nested Holdout",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "- Prospective hypothesis `HYP-CROSS-VENUE-CATCHUP-001` was registered before code.",
        "- Signal compares synchronized returns only; USD/USDT price-level spread is not used.",
        "- Coinbase impulse is known at minute close; Binance long entry uses the next minute open.",
        f"- Tested / train-qualified: `{report['search']['tested']}` / `{report['search']['train_qualified']}`.",
        f"- Decision: `{report['decision']}`.",
    ]
    selected = report.get("selected_on_train")
    if selected:
        train = selected["train"]["summary"]
        lines.append(f"- Frozen train candidate: `{selected['strategy_id']}`, `{train['trades']}` trades, `{train['mean_net_bps']}` net bps/trade.")
    else:
        best = report["top_train_results_regardless_of_gate"][0]
        train = best["train"]["summary"]
        lines.append(f"- Best rejected: `{best['strategy_id']}`, `{train['trades']}` trades, `{train['mean_net_bps']}` net bps/trade.")
        lines.append("- Validation and OOS remained unopened.")
    lines.extend(["- Registry-level Bonferroni assessment is applied by the verified runner.", "- `can_trade=false`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nested holdout for Coinbase-to-Binance minute catch-up")
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--train-end", default="2026-06-08T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2026-06-15T00:00:00+00:00")
    parser.add_argument("--cost-bps-per-side", type=float, default=10.0)
    parser.add_argument("--stress-extra-bps", type=float, default=5.0)
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_CATCHUP_NESTED_HOLDOUT_2026-06-24")
    parser.add_argument("--lock-path", default="configs/CROSS_VENUE_CATCHUP_RESEARCH_LOCK.json")
    args = parser.parse_args()

    cache = resolve_path(args.cache_dir)
    binance = read_binance(cache / "binance" / "BTCUSDT" / "1m_candles.csv")
    aligned = read_aligned(cache / "aligned" / "BTCUSDT__BTC-USD" / "1m_candles.csv")
    if not aligned or not binance:
        raise SystemExit("cross_venue_snapshot_empty")
    train_end_ms = int(parse_ts(args.train_end).timestamp() * 1000)
    validation_end_ms = int(parse_ts(args.validation_end).timestamp() * 1000)
    data_start_ms = int(aligned[0]["time_ms"])
    data_end_ms = int(aligned[-1]["time_ms"]) + MINUTE_MS

    feature_cache: dict[int, list[dict[str, float | int]]] = {}
    z_cache: dict[tuple[int, int], dict[int, float]] = {}
    signal_cache: dict[tuple[int, int, float, float], list[dict[str, float | int]]] = {}
    candidates: list[dict[str, Any]] = []
    for config in build_configs():
        lookback = config.return_lookback_minutes
        if lookback not in feature_cache:
            feature_cache[lookback] = return_features(aligned, lookback)
        z_key = (lookback, config.divergence_z_window_minutes)
        if z_key not in z_cache:
            z_cache[z_key] = causal_time_z(
                feature_cache[lookback],
                config.divergence_z_window_minutes,
                config.min_window_coverage,
            )
        signal_key = z_key + (config.entry_z, config.min_coinbase_return_bps)
        if signal_key not in signal_cache:
            signal_cache[signal_key] = generate_signals(config, feature_cache[lookback], z_cache[z_key])
        train = evaluate(
            config,
            signal_cache[signal_key],
            binance,
            start_ms=data_start_ms,
            end_ms=train_end_ms,
            cost_bps_per_side=args.cost_bps_per_side,
            stress_extra_bps_per_side=args.stress_extra_bps,
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
    decision = "reject_no_train_qualified_cross_venue_candidate"
    if selected:
        config = CatchupConfig(**selected["config"])
        validation = evaluate(
            config,
            selected["_signals"],
            binance,
            start_ms=train_end_ms,
            end_ms=validation_end_ms,
            cost_bps_per_side=args.cost_bps_per_side,
            stress_extra_bps_per_side=args.stress_extra_bps,
            folds=3,
        )
        validation_gate = gate(validation, "validation")
        decision = "reject_validation_gate_failed_oos_unopened"
        if validation_gate["pass"]:
            oos_opened = True
            oos = evaluate(
                config,
                selected["_signals"],
                binance,
                start_ms=validation_end_ms,
                end_ms=data_end_ms,
                cost_bps_per_side=args.cost_bps_per_side,
                stress_extra_bps_per_side=args.stress_extra_bps,
                folds=3,
            )
            oos_gate = gate(oos, "validation")
            decision = "cross_venue_candidate_requires_registry_review" if oos_gate["pass"] else "reject_oos_gate_failed"

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
        "hypothesis_id": "HYP-CROSS-VENUE-CATCHUP-001",
        "family": "COINBASE_TO_BINANCE_CATCHUP_1M",
        "method": "prospective_train_search_then_validation_then_conditionally_open_oos",
        "data": {
            "cache_dir": portable_path(cache),
            "snapshot": snapshot_provenance(cache),
            "aligned_rows": len(aligned),
            "binance_rows": len(binance),
            "first": iso_from_ms(data_start_ms),
            "last": iso_from_ms(data_end_ms - MINUTE_MS),
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
            "long_only": True,
            "returns_only": True,
            "exact_timestamp_lookback": True,
            "min_rolling_window_coverage": 0.95,
            "entry_next_minute": True,
            "cost_bps_per_side": args.cost_bps_per_side,
            "stress_extra_bps_per_side": args.stress_extra_bps,
            "bonferroni_required_probability": 0.999537037037037,
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
