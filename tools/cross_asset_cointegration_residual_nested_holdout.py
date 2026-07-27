#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Panel:
    times: list[str]
    opens: dict[str, list[float]]
    closes: dict[str, list[float]]


@dataclass(frozen=True)
class ResidualConfig:
    lookback_hours: int
    entry_z: float
    exit_z: float
    max_hold_hours: int
    stop_z: float
    min_beta: float
    max_beta: float

    @property
    def config_id(self) -> str:
        return (
            f"resid_w{self.lookback_hours}_e{self.entry_z:g}_x{self.exit_z:g}_"
            f"stop{self.stop_z:g}_h{self.max_hold_hours}"
        )


@dataclass(frozen=True)
class RegressionPoint:
    alpha: float
    beta: float
    residual_std: float
    signal_z: float
    half_life_hours: float | None = None


@dataclass(frozen=True)
class PrefixStats:
    x: list[float]
    y: list[float]
    xx: list[float]
    xy: list[float]
    yy: list[float]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_lock(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("can_trade") is not False:
        raise ValueError("research lock must explicitly set can_trade=false")
    if payload.get("status") != "immutable_research_lock":
        raise ValueError("research lock status must be immutable_research_lock")
    fractions = payload["split"]
    total = sum(float(fractions[key]) for key in ("train_fraction", "validation_fraction", "oos_fraction"))
    if not math.isclose(total, 1.0, abs_tol=1e-9):
        raise ValueError("split fractions must total 1.0")
    return payload


def load_symbol(path: Path) -> dict[str, tuple[float, float]]:
    required = {"time", "open", "close"}
    rows: dict[str, tuple[float, float]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} missing columns: {sorted(missing)}")
        for raw in reader:
            open_price = float(raw["open"])
            close_price = float(raw["close"])
            if open_price > 0.0 and close_price > 0.0:
                rows[raw["time"]] = (open_price, close_price)
    if not rows:
        raise ValueError(f"no valid bars in {path}")
    return rows


def load_panel(
    cache_dir: Path,
    symbols: list[str],
    interval: str,
    max_time_inclusive: str | None = None,
    minimum_rows: int = 1000,
) -> Panel:
    by_symbol = {
        symbol: load_symbol(cache_dir / "futures" / symbol / f"{interval}_klines.csv")
        for symbol in symbols
    }
    common_times = set.intersection(*(set(rows) for rows in by_symbol.values()))
    times = [
        timestamp
        for timestamp in sorted(common_times)
        if max_time_inclusive is None or timestamp <= max_time_inclusive
    ]
    if len(times) < minimum_rows:
        raise ValueError(f"insufficient aligned history: {len(times)} rows")
    return Panel(
        times=times,
        opens={symbol: [by_symbol[symbol][ts][0] for ts in times] for symbol in symbols},
        closes={symbol: [by_symbol[symbol][ts][1] for ts in times] for symbol in symbols},
    )


def prefix_sum(values: list[float]) -> list[float]:
    out = [0.0]
    total = 0.0
    for value in values:
        total += value
        out.append(total)
    return out


def build_prefix(log_x: list[float], log_y: list[float]) -> PrefixStats:
    return PrefixStats(
        x=prefix_sum(log_x),
        y=prefix_sum(log_y),
        xx=prefix_sum([value * value for value in log_x]),
        xy=prefix_sum([x * y for x, y in zip(log_x, log_y)]),
        yy=prefix_sum([value * value for value in log_y]),
    )


def segment(prefix: list[float], start: int, end: int) -> float:
    return prefix[end] - prefix[start]


def residual_half_life(log_x: list[float], log_y: list[float], start: int, end: int, alpha: float, beta: float) -> float | None:
    residuals = [log_y[index] - alpha - beta * log_x[index] for index in range(start, end)]
    if len(residuals) < 4:
        return None
    previous = residuals[:-1]
    current = residuals[1:]
    mean_previous = statistics.mean(previous)
    mean_current = statistics.mean(current)
    denominator = sum((value - mean_previous) ** 2 for value in previous)
    if denominator <= 1e-18:
        return None
    phi = sum(
        (prior - mean_previous) * (later - mean_current)
        for prior, later in zip(previous, current)
    ) / denominator
    if not 0.0 < phi < 1.0:
        return None
    return -math.log(2.0) / math.log(phi)


def rolling_ols_point(
    log_x: list[float],
    log_y: list[float],
    prefix: PrefixStats,
    *,
    index: int,
    window: int,
    include_half_life: bool = False,
) -> RegressionPoint | None:
    if index < window or window < 4:
        return None
    start = index - window
    end = index
    count = float(window)
    sx = segment(prefix.x, start, end)
    sy = segment(prefix.y, start, end)
    sxx = segment(prefix.xx, start, end)
    sxy = segment(prefix.xy, start, end)
    syy = segment(prefix.yy, start, end)
    denominator = count * sxx - sx * sx
    if abs(denominator) <= 1e-14:
        return None
    beta = (count * sxy - sx * sy) / denominator
    alpha = (sy - beta * sx) / count
    sse = (
        syy
        + count * alpha * alpha
        + beta * beta * sxx
        - 2.0 * alpha * sy
        - 2.0 * beta * sxy
        + 2.0 * alpha * beta * sx
    )
    residual_variance = max(0.0, sse) / (count - 2.0)
    if residual_variance <= 1e-16:
        return None
    residual_std = math.sqrt(residual_variance)
    signal_residual = log_y[index] - alpha - beta * log_x[index]
    half_life = (
        residual_half_life(log_x, log_y, start, end, alpha, beta)
        if include_half_life
        else None
    )
    return RegressionPoint(
        alpha=alpha,
        beta=beta,
        residual_std=residual_std,
        signal_z=signal_residual / residual_std,
        half_life_hours=half_life,
    )


def build_regression_cache(
    x_closes: list[float],
    y_closes: list[float],
    window: int,
    minimum_entry_z: float,
) -> list[RegressionPoint | None]:
    log_x = [math.log(value) for value in x_closes]
    log_y = [math.log(value) for value in y_closes]
    prefix = build_prefix(log_x, log_y)
    out: list[RegressionPoint | None] = [None] * len(log_x)
    for index in range(window, len(log_x)):
        point = rolling_ols_point(log_x, log_y, prefix, index=index, window=window)
        if point is None:
            continue
        if abs(point.signal_z) >= minimum_entry_z:
            point = rolling_ols_point(
                log_x,
                log_y,
                prefix,
                index=index,
                window=window,
                include_half_life=True,
            )
        out[index] = point
    return out


def generate_configs(lock: dict[str, Any]) -> list[ResidualConfig]:
    grid = lock["grid"]
    return [
        ResidualConfig(
            lookback_hours=int(window),
            entry_z=float(entry_z),
            exit_z=float(exit_z),
            max_hold_hours=int(max_hold),
            stop_z=float(grid["stop_z"]),
            min_beta=float(grid["min_beta"]),
            max_beta=float(grid["max_beta"]),
        )
        for window, entry_z, exit_z, max_hold in product(
            grid["lookback_hours"],
            grid["entry_z"],
            grid["exit_z"],
            grid["max_hold_hours"],
        )
    ]


def simulate_stage(
    config: ResidualConfig,
    panel: Panel,
    x_symbol: str,
    y_symbol: str,
    regression_cache: list[RegressionPoint | None],
    *,
    start_index: int,
    end_index: int,
) -> list[dict[str, Any]]:
    log_x_close = [math.log(value) for value in panel.closes[x_symbol]]
    log_y_close = [math.log(value) for value in panel.closes[y_symbol]]
    trades: list[dict[str, Any]] = []
    signal_index = max(start_index, config.lookback_hours)
    while signal_index + 2 < end_index:
        point = regression_cache[signal_index]
        if (
            point is None
            or not config.min_beta <= point.beta <= config.max_beta
            or abs(point.signal_z) < config.entry_z
            or abs(point.signal_z) >= config.stop_z
        ):
            signal_index += 1
            continue
        entry_index = signal_index + 1
        side = -1.0 if point.signal_z > 0.0 else 1.0
        exit_index: int | None = None
        exit_reason = ""
        exit_z = 0.0
        last_check = min(entry_index + config.max_hold_hours - 1, end_index - 2)
        for check_index in range(entry_index, last_check + 1):
            live_residual = log_y_close[check_index] - point.alpha - point.beta * log_x_close[check_index]
            live_z = live_residual / point.residual_std
            held = check_index - entry_index + 1
            if abs(live_z) >= config.stop_z:
                exit_reason = "residual_stop"
            elif abs(live_z) <= config.exit_z:
                exit_reason = "residual_reversion"
            elif held >= config.max_hold_hours:
                exit_reason = "time_stop"
            else:
                continue
            exit_index = check_index + 1
            exit_z = live_z
            break
        if exit_index is None or exit_index >= end_index:
            signal_index += 1
            continue
        entry_y = panel.opens[y_symbol][entry_index]
        entry_x = panel.opens[x_symbol][entry_index]
        exit_y = panel.opens[y_symbol][exit_index]
        exit_x = panel.opens[x_symbol][exit_index]
        gross_bps = side * (
            math.log(exit_y / entry_y) - point.beta * math.log(exit_x / entry_x)
        ) * 10000.0 / (1.0 + abs(point.beta))
        trades.append(
            {
                "pair": f"{y_symbol}~{x_symbol}",
                "config_id": config.config_id,
                "signal_time": panel.times[signal_index],
                "entry_time": panel.times[entry_index],
                "exit_time": panel.times[exit_index],
                "side": "short_y_long_x" if side < 0 else "long_y_short_x",
                "signal_z": round(point.signal_z, 6),
                "exit_z": round(exit_z, 6),
                "alpha": round(point.alpha, 9),
                "beta": round(point.beta, 9),
                "half_life_hours": round(point.half_life_hours, 6) if point.half_life_hours else None,
                "holding_hours": exit_index - entry_index,
                "exit_reason": exit_reason,
                "gross_return_bps": round(gross_bps, 6),
            }
        )
        signal_index = exit_index
    return trades


def summarize(trades: list[dict[str, Any]], round_trip_cost_bps: float) -> dict[str, Any]:
    values = [float(item["gross_return_bps"]) - round_trip_cost_bps for item in trades]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    if not values:
        return {
            "trades": 0,
            "mean_net_bps": None,
            "median_net_bps": None,
            "positive_pct": None,
            "total_net_bps": 0.0,
            "max_drawdown_bps": 0.0,
            "mean_holding_hours": None,
            "median_beta": None,
            "median_half_life_hours": None,
            "exit_reasons": {},
        }
    half_lives = [float(item["half_life_hours"]) for item in trades if item.get("half_life_hours") is not None]
    exit_reasons: dict[str, int] = {}
    for item in trades:
        reason = str(item["exit_reason"])
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    return {
        "trades": len(values),
        "mean_net_bps": round(statistics.mean(values), 6),
        "median_net_bps": round(statistics.median(values), 6),
        "positive_pct": round(sum(value > 0.0 for value in values) / len(values) * 100.0, 6),
        "total_net_bps": round(sum(values), 6),
        "max_drawdown_bps": round(max_drawdown, 6),
        "mean_holding_hours": round(statistics.mean(float(item["holding_hours"]) for item in trades), 6),
        "median_beta": round(statistics.median(float(item["beta"]) for item in trades), 6),
        "median_half_life_hours": round(statistics.median(half_lives), 6) if half_lives else None,
        "exit_reasons": exit_reasons,
    }


def positive_folds(trades: list[dict[str, Any]], cost_bps: float, folds: int) -> int:
    ordered = sorted(trades, key=lambda item: item["entry_time"])
    positive = 0
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        values = [float(item["gross_return_bps"]) - cost_bps for item in ordered[start:end]]
        if len(values) >= 3 and statistics.mean(values) > 0.0:
            positive += 1
    return positive


def bootstrap_positive_probability(values: list[float], iterations: int, seed: int) -> float | None:
    if not values or iterations <= 0:
        return None
    rng = random.Random(seed)
    sample_size = len(values)
    positive = 0
    for _ in range(iterations):
        positive += int(sum(rng.choice(values) for _ in range(sample_size)) / sample_size > 0.0)
    return round(positive / iterations, 6)


def evaluate_stage(
    config: ResidualConfig,
    panel: Panel,
    x_symbol: str,
    y_symbol: str,
    regression_cache: list[RegressionPoint | None],
    *,
    start_index: int,
    end_index: int,
    base_cost_bps: float,
    stress_cost_bps: float,
    folds: int,
) -> dict[str, Any]:
    trades = simulate_stage(
        config,
        panel,
        x_symbol,
        y_symbol,
        regression_cache,
        start_index=start_index,
        end_index=end_index,
    )
    return {
        "summary": summarize(trades, base_cost_bps),
        "positive_folds": positive_folds(trades, base_cost_bps, folds),
        "stress": {"round_trip_cost_bps": stress_cost_bps, "summary": summarize(trades, stress_cost_bps)},
        "bootstrap_probability_mean_gt_0": None,
        "bootstrap_status": "not_requested",
        "sample_trades": trades[:5],
        "_trades": trades,
    }


def gate_failures(stage: str, evaluation: dict[str, Any], lock: dict[str, Any], *, include_bootstrap: bool) -> list[str]:
    gate = lock["gates"][stage]
    summary = evaluation["summary"]
    stress = evaluation["stress"]["summary"]
    failures: list[str] = []
    if int(summary["trades"]) < int(gate["min_trades"]):
        failures.append("min_trades")
    if summary["mean_net_bps"] is None or float(summary["mean_net_bps"]) <= float(gate["min_mean_net_bps"]):
        failures.append("min_mean_net_bps")
    if summary["positive_pct"] is None or float(summary["positive_pct"]) < float(gate["min_positive_pct"]):
        failures.append("min_positive_pct")
    if int(evaluation["positive_folds"]) < int(gate["min_positive_folds"]):
        failures.append("min_positive_folds")
    if gate.get("require_stress_mean_positive") and (
        stress["mean_net_bps"] is None or float(stress["mean_net_bps"]) <= 0.0
    ):
        failures.append("stress_mean_not_positive")
    if include_bootstrap:
        probability = evaluation.get("bootstrap_probability_mean_gt_0")
        if probability is None or float(probability) < float(gate["min_bootstrap_probability_mean_gt_0"]):
            failures.append("bootstrap_probability_mean_gt_0")
    return failures


def public_evaluation(evaluation: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in evaluation.items() if key != "_trades"}


def train_rank(evaluation: dict[str, Any]) -> tuple[float, float, float, int]:
    summary = evaluation["summary"]
    stress = evaluation["stress"]["summary"]
    return (
        float(stress["mean_net_bps"] or -999999.0),
        float(summary["mean_net_bps"] or -999999.0),
        float(summary["positive_pct"] or 0.0),
        int(summary["trades"]),
    )


def evaluate_pair_train(
    configs: list[ResidualConfig],
    panel: Panel,
    x_symbol: str,
    y_symbol: str,
    caches: dict[int, list[RegressionPoint | None]],
    train_end: int,
    lock: dict[str, Any],
) -> dict[str, Any]:
    execution = lock["execution"]
    folds = int(lock["folds"])
    bootstrap = lock["bootstrap"]
    rows: list[dict[str, Any]] = []
    qualified: list[tuple[ResidualConfig, dict[str, Any]]] = []
    for offset, config in enumerate(configs):
        evaluation = evaluate_stage(
            config,
            panel,
            x_symbol,
            y_symbol,
            caches[config.lookback_hours],
            start_index=0,
            end_index=train_end,
            base_cost_bps=float(execution["round_trip_cost_bps"]),
            stress_cost_bps=float(execution["stress_round_trip_cost_bps"]),
            folds=folds,
        )
        failures = gate_failures("train", evaluation, lock, include_bootstrap=False)
        if not failures:
            values = [
                float(item["gross_return_bps"]) - float(execution["round_trip_cost_bps"])
                for item in evaluation["_trades"]
            ]
            evaluation["bootstrap_probability_mean_gt_0"] = bootstrap_positive_probability(
                values,
                int(bootstrap["iterations"]),
                int(bootstrap["seed"]) + offset,
            )
            evaluation["bootstrap_status"] = "computed_after_basic_train_gate"
            failures = gate_failures("train", evaluation, lock, include_bootstrap=True)
        else:
            evaluation["bootstrap_status"] = "skipped_basic_train_gate_failed"
        row = {
            "config": asdict(config),
            "config_id": config.config_id,
            "evaluation": public_evaluation(evaluation),
            "status": "pass" if not failures else "fail",
            "failures": failures,
        }
        rows.append(row)
        if not failures:
            qualified.append((config, evaluation))
    rows.sort(key=lambda item: train_rank(item["evaluation"]), reverse=True)
    selected = max(qualified, key=lambda item: train_rank(item[1])) if qualified else None
    return {
        "searched_configs": len(configs),
        "qualified_configs": len(qualified),
        "leaderboard": rows[:10],
        "selected_config": asdict(selected[0]) if selected else None,
        "selected_config_id": selected[0].config_id if selected else None,
        "selected_train": public_evaluation(selected[1]) if selected else None,
    }


def config_from_payload(payload: dict[str, Any]) -> ResidualConfig:
    return ResidualConfig(**payload)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Cross-Asset Residual Reversion Nested Holdout",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{summary['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Aligned 1h rows: `{report['dataset']['aligned_rows']}`",
        f"- Train-qualified pairs: `{summary['train_qualified_pairs']}`",
        f"- Validation-qualified pairs: `{summary['validation_qualified_pairs']}`",
        f"- OOS opened: `{str(summary['oos_opened']).lower()}`",
        f"- OOS-qualified pairs: `{summary['oos_qualified_pairs']}`",
        "",
        "## Pair Results",
        "",
        "| Pair | Train config | Train mean bps | Validation | Validation mean bps | OOS | OOS mean bps |",
        "|---|---|---:|---|---:|---|---:|",
    ]
    for pair, payload in report["pairs"].items():
        train = payload["train"]
        validation = payload["validation"]
        oos = payload["oos"]
        best_attempt = (train.get("leaderboard") or [{}])[0]
        selected_or_best = train.get("selected_train") or best_attempt.get("evaluation") or {}
        selected_id = train.get("selected_config_id") or best_attempt.get("config_id")
        train_mean = selected_or_best.get("summary", {}).get("mean_net_bps")
        validation_mean = (validation.get("evaluation") or {}).get("summary", {}).get("mean_net_bps")
        oos_mean = (oos.get("evaluation") or {}).get("summary", {}).get("mean_net_bps")
        lines.append(
            f"| `{pair}` | `{selected_id}` | `{train_mean}` | "
            f"`{validation.get('status')}` | `{validation_mean}` | `{oos.get('status')}` | `{oos_mean}` |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Parameters are selected on train only; validation receives one selected config per pair.",
            "- OOS is not evaluated unless at least two independent pairs pass validation.",
            "- Signals use bar close; entries and exits use the next bar open.",
            "- Base and stress costs cover both perpetual legs on entry and exit.",
            "- Funding is not modeled. This is a research limitation and blocks production use.",
            "- Rolling OLS plus residual half-life is a stationarity proxy, not formal proof of cointegration.",
            "- No result auto-promotes to live trading; `can_trade` remains false.",
            "",
            "## Provenance",
            "",
            f"- Lock: `{report['lock']['path']}`",
            f"- Lock SHA-256: `{report['lock']['sha256']}`",
            f"- Source DOCX SHA-256: `{report['source']['sha256']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def run(lock_path: Path, output_json: Path, output_md: Path) -> dict[str, Any]:
    lock = load_lock(lock_path)
    dataset = lock["dataset"]
    x_symbol = str(dataset["x_symbol"])
    y_symbols = [str(value) for value in dataset["y_symbols"]]
    symbols = [x_symbol, *y_symbols]
    panel = load_panel(
        resolve_path(dataset["cache_dir"]),
        symbols,
        str(dataset["interval"]),
        str(dataset.get("max_time_inclusive")) if dataset.get("max_time_inclusive") else None,
    )
    configs = generate_configs(lock)
    minimum_entry_z = min(config.entry_z for config in configs)
    train_end = int(len(panel.times) * float(lock["split"]["train_fraction"]))
    validation_end = int(
        len(panel.times)
        * (float(lock["split"]["train_fraction"]) + float(lock["split"]["validation_fraction"]))
    )
    pair_payloads: dict[str, dict[str, Any]] = {}
    selected: dict[str, tuple[ResidualConfig, dict[int, list[RegressionPoint | None]]]] = {}
    for y_symbol in y_symbols:
        caches = {
            window: build_regression_cache(
                panel.closes[x_symbol],
                panel.closes[y_symbol],
                window,
                minimum_entry_z,
            )
            for window in sorted({config.lookback_hours for config in configs})
        }
        train = evaluate_pair_train(configs, panel, x_symbol, y_symbol, caches, train_end, lock)
        pair = f"{y_symbol}~{x_symbol}"
        validation: dict[str, Any]
        if train["selected_config"] is None:
            validation = {"status": "not_run_train_failed", "failures": ["no_train_qualified_config"]}
        else:
            config = config_from_payload(train["selected_config"])
            evaluation = evaluate_stage(
                config,
                panel,
                x_symbol,
                y_symbol,
                caches[config.lookback_hours],
                start_index=train_end,
                end_index=validation_end,
                base_cost_bps=float(lock["execution"]["round_trip_cost_bps"]),
                stress_cost_bps=float(lock["execution"]["stress_round_trip_cost_bps"]),
                folds=int(lock["folds"]),
            )
            failures = gate_failures("validation", evaluation, lock, include_bootstrap=False)
            validation = {
                "status": "pass" if not failures else "fail",
                "failures": failures,
                "evaluation": public_evaluation(evaluation),
            }
            if not failures:
                selected[pair] = (config, caches)
        pair_payloads[pair] = {
            "train": train,
            "validation": validation,
            "oos": {"status": "pending_cross_pair_validation_gate"},
        }
    minimum_pairs = int(lock["split"]["minimum_independent_pairs_to_open_oos"])
    oos_opened = len(selected) >= minimum_pairs
    oos_qualified = 0
    if oos_opened:
        for pair, (config, caches) in selected.items():
            y_symbol = pair.split("~", 1)[0]
            evaluation = evaluate_stage(
                config,
                panel,
                x_symbol,
                y_symbol,
                caches[config.lookback_hours],
                start_index=validation_end,
                end_index=len(panel.times),
                base_cost_bps=float(lock["execution"]["round_trip_cost_bps"]),
                stress_cost_bps=float(lock["execution"]["stress_round_trip_cost_bps"]),
                folds=int(lock["folds"]),
            )
            failures = gate_failures("oos", evaluation, lock, include_bootstrap=False)
            oos_qualified += int(not failures)
            pair_payloads[pair]["oos"] = {
                "status": "pass" if not failures else "fail",
                "failures": failures,
                "evaluation": public_evaluation(evaluation),
            }
        for pair in pair_payloads:
            if pair not in selected:
                pair_payloads[pair]["oos"] = {
                    "status": "not_run_pair_validation_failed",
                    "failures": ["pair_validation_gate_failed"],
                }
    else:
        for pair in pair_payloads:
            pair_payloads[pair]["oos"] = {
                "status": "not_run_cross_pair_validation_failed",
                "failures": ["minimum_independent_pairs_validation_gate_failed"],
            }
    train_qualified_pairs = sum(payload["train"]["selected_config"] is not None for payload in pair_payloads.values())
    validation_qualified_pairs = len(selected)
    if train_qualified_pairs == 0:
        decision = "reject_train_gate_failed_validation_and_oos_unopened"
    elif not oos_opened:
        decision = "reject_validation_gate_failed_oos_unopened"
    elif oos_qualified < minimum_pairs:
        decision = "reject_oos_gate_failed"
    else:
        decision = "research_candidate_oos_passed_forward_shadow_required"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "can_trade": False,
        "summary": {
            "decision": decision,
            "train_qualified_pairs": train_qualified_pairs,
            "validation_qualified_pairs": validation_qualified_pairs,
            "minimum_pairs_to_open_oos": minimum_pairs,
            "oos_opened": oos_opened,
            "oos_qualified_pairs": oos_qualified,
        },
        "dataset": {
            "cache_dir": portable_path(resolve_path(dataset["cache_dir"])),
            "interval": dataset["interval"],
            "symbols": symbols,
            "max_time_inclusive": dataset.get("max_time_inclusive"),
            "aligned_rows": len(panel.times),
            "first_time": panel.times[0],
            "last_time": panel.times[-1],
            "split": {
                "train": {"rows": train_end, "end_exclusive": panel.times[train_end]},
                "validation": {
                    "rows": validation_end - train_end,
                    "start": panel.times[train_end],
                    "end_exclusive": panel.times[validation_end],
                },
                "oos": {"rows": len(panel.times) - validation_end, "start": panel.times[validation_end]},
            },
        },
        "lock": {"path": portable_path(lock_path), "sha256": sha256_file(lock_path), "lock_id": lock["lock_id"]},
        "source": lock["source"],
        "pairs": pair_payloads,
        "limitations": [
            "funding_not_modeled",
            "rolling_ols_residual_proxy_not_formal_cointegration_test",
            "bar_data_cannot_model_intrabar_execution_or_queue_position",
            "multiple_testing_risk_remains_despite_nested_holdout",
            "research_only_no_live_consumer",
        ],
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_md.write_text(render_markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validation-gated cross-asset residual-reversion study")
    parser.add_argument(
        "--lock",
        default="configs/CROSS_ASSET_COINTEGRATION_RESIDUAL_RESEARCH_LOCK_2026-07-12.json",
    )
    parser.add_argument(
        "--output-json",
        default="docs/CROSS_ASSET_COINTEGRATION_RESIDUAL_NESTED_HOLDOUT_2026-07-12.json",
    )
    parser.add_argument(
        "--output-md",
        default="docs/CROSS_ASSET_COINTEGRATION_RESIDUAL_NESTED_HOLDOUT_2026-07-12.md",
    )
    args = parser.parse_args()
    report = run(resolve_path(args.lock), resolve_path(args.output_json), resolve_path(args.output_md))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
