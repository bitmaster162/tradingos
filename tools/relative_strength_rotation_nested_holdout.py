#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402
from tools.liquidity_sweep_hardening import Trade, simulate_trade, summarize_trades  # noqa: E402


@dataclass(frozen=True)
class RotationConfig:
    strategy_id: str
    interval: str
    side: str
    mode: str
    lookback: int
    min_rel_strength_pct: float
    alt_symbols: tuple[str, ...]
    alt_confirm: str
    atr_regime_filter: str
    stop_atr: float
    take_atr: float
    max_hold_bars: int


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


def parse_ts(value: str) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def split_bars(bars: list[Any], start: datetime | None, end: datetime | None) -> list[Any]:
    out = []
    for bar in bars:
        ts = parse_ts(str(bar.ts))
        if start is not None and ts < start:
            continue
        if end is not None and ts >= end:
            continue
        out.append(bar)
    return out


def rolling_mean(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    valid: list[float] = []
    for value in values:
        if value is not None:
            valid.append(float(value))
        if len(valid) > window:
            valid.pop(0)
        out.append(sum(valid) / len(valid) if len(valid) >= max(5, window // 4) else None)
    return out


def load_symbol_bars(cache_dir: Path, symbol: str, interval: str) -> list[Any]:
    return load_ohlcv(cache_dir / "futures" / symbol / f"{interval}_klines.csv")


def align_alt_closes(btc_bars: list[Any], alt_bars_by_symbol: dict[str, list[Any]]) -> dict[str, list[float | None]]:
    by_symbol_time = {
        symbol: {str(bar.ts): float(bar.close) for bar in bars}
        for symbol, bars in alt_bars_by_symbol.items()
    }
    return {
        symbol: [by_time.get(str(bar.ts)) for bar in btc_bars]
        for symbol, by_time in by_symbol_time.items()
    }


def pct_return(values: list[float | None], index: int, lookback: int) -> float | None:
    if index < lookback:
        return None
    current = values[index]
    prior = values[index - lookback]
    if current is None or prior is None or prior <= 0:
        return None
    return (current - prior) / prior * 100.0


def build_features(btc_bars: list[Any], alt_bars_by_symbol: dict[str, list[Any]], atr_window: int, atr_ratio_window: int) -> dict[str, Any]:
    closes = [float(bar.close) for bar in btc_bars]
    atr = compute_atr(btc_bars, atr_window)
    atr_mean = rolling_mean(atr, atr_ratio_window)
    atr_ratio = [
        (float(atr_value) / float(mean_value)) if atr_value is not None and mean_value not in {None, 0} else None
        for atr_value, mean_value in zip(atr, atr_mean)
    ]
    return {
        "btc_closes": closes,
        "atr": atr,
        "atr_ratio": atr_ratio,
        "alt_closes": align_alt_closes(btc_bars, alt_bars_by_symbol),
    }


def add_return_cache(features: dict[str, Any], lookbacks: list[int]) -> None:
    btc_closes = features["btc_closes"]
    alt_closes = features["alt_closes"]
    features["btc_returns"] = {lookback: [pct_return(btc_closes, index, lookback) for index in range(len(btc_closes))] for lookback in lookbacks}
    features["alt_returns"] = {
        symbol: {lookback: [pct_return(values, index, lookback) for index in range(len(values))] for lookback in lookbacks}
        for symbol, values in alt_closes.items()
    }


def atr_regime_ok(config: RotationConfig, features: dict[str, Any], index: int) -> bool:
    if config.atr_regime_filter == "none":
        return True
    ratio = features["atr_ratio"][index]
    if ratio is None:
        return False
    if config.atr_regime_filter == "low":
        return ratio <= 0.85
    if config.atr_regime_filter == "mid":
        return 0.85 < ratio < 1.15
    if config.atr_regime_filter == "high":
        return ratio >= 1.15
    raise ValueError(f"unsupported atr_regime_filter={config.atr_regime_filter}")


def alt_confirm_ok(config: RotationConfig, alt_returns: list[float]) -> bool:
    if config.alt_confirm == "none":
        return True
    if not alt_returns:
        return False
    if config.alt_confirm == "alts_up":
        return sum(1 for value in alt_returns if value > 0) >= max(1, len(alt_returns) // 2 + 1)
    if config.alt_confirm == "alts_down":
        return sum(1 for value in alt_returns if value < 0) >= max(1, len(alt_returns) // 2 + 1)
    if config.alt_confirm == "mixed":
        return any(value > 0 for value in alt_returns) and any(value < 0 for value in alt_returns)
    raise ValueError(f"unsupported alt_confirm={config.alt_confirm}")


def generate_signals(config: RotationConfig, bars: list[Any], features: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    btc_returns = features.get("btc_returns", {})
    alt_returns_by_symbol = features.get("alt_returns", {})
    for index, _bar in enumerate(bars):
        if index + config.max_hold_bars + 1 >= len(bars):
            continue
        atr = features["atr"][index]
        if atr is None or float(atr) <= 0:
            continue
        if not atr_regime_ok(config, features, index):
            continue
        if config.lookback in btc_returns:
            btc_ret = btc_returns[config.lookback][index]
        else:
            btc_ret = pct_return(features["btc_closes"], index, config.lookback)
        if btc_ret is None:
            continue
        alt_returns = []
        for symbol in config.alt_symbols:
            if symbol in alt_returns_by_symbol and config.lookback in alt_returns_by_symbol[symbol]:
                alt_ret = alt_returns_by_symbol[symbol][config.lookback][index]
            else:
                alt_ret = pct_return(features["alt_closes"][symbol], index, config.lookback)
            if alt_ret is not None:
                alt_returns.append(alt_ret)
        if not alt_returns:
            continue
        basket_ret = statistics.mean(alt_returns)
        rel_strength = btc_ret - basket_ret
        if config.mode == "btc_leads" and rel_strength < config.min_rel_strength_pct:
            continue
        if config.mode == "btc_lags" and rel_strength > -config.min_rel_strength_pct:
            continue
        if config.mode == "dispersion_abs" and abs(rel_strength) < config.min_rel_strength_pct:
            continue
        if not alt_confirm_ok(config, alt_returns):
            continue
        signals.append(
            {
                "bar_index": index,
                "side_hint": config.side,
                "atr": float(atr),
                "reason": "relative_strength_rotation",
                "btc_return_pct": round(btc_ret, 6),
                "alt_basket_return_pct": round(basket_ret, 6),
                "rel_strength_pct": round(rel_strength, 6),
                "mode": config.mode,
            }
        )
    return signals


def replay_signals(config: RotationConfig, bars: list[Any], signals: list[dict[str, Any]], cost_bps_per_side: float, no_overlap: bool) -> list[Trade]:
    trades: list[Trade] = []
    last_exit_bar = -1
    bar_index_by_ts = {str(bar.ts): index for index, bar in enumerate(bars)}
    for signal in signals:
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"relative_strength_rotation_BTCUSDT_{config.interval}",
            strategy_id=config.strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=config.stop_atr,
            take_atr=config.take_atr,
            max_hold_bars=config.max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        if no_overlap:
            last_exit_bar = max(last_exit_bar, bar_index_by_ts.get(trade.exit_ts, signal_index))
    return trades


def fold_summaries(trades: list[Trade], folds: int) -> list[dict[str, Any]]:
    ordered = sorted(trades, key=lambda item: item.entry_ts)
    out = []
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        summary = summarize_trades(chunk)
        summary["fold"] = fold + 1
        summary["stable"] = bool(summary["trades"] >= 5 and (summary["expectancy_r"] or 0.0) > 0)
        out.append(summary)
    return out


def evaluate_window(config: RotationConfig, bars: list[Any], features: dict[str, Any], args: argparse.Namespace, folds: int) -> dict[str, Any]:
    cost = args.fee_bps + args.slippage_bps
    signals = generate_signals(config, bars, features)
    trades = replay_signals(config, bars, signals, cost, args.no_overlap)
    stress_trades = replay_signals(config, bars, signals, cost + args.cost_stress_extra_bps, args.no_overlap)
    folds_payload = fold_summaries(trades, folds)
    return {
        "summary": summarize_trades(trades),
        "folds": folds_payload,
        "stable_folds": sum(1 for item in folds_payload if item.get("stable")),
        "cost_stress": {"summary": summarize_trades(stress_trades)},
        "trades": trades,
    }


def gate(window: dict[str, Any], *, min_trades: int, min_expectancy: float, min_stable_folds: int, min_winrate: float, max_drawdown: float) -> dict[str, Any]:
    summary = window["summary"]
    stress = window["cost_stress"]["summary"]
    checks = {
        "min_trades": int(summary.get("trades") or 0) >= min_trades,
        "min_expectancy_r": float(summary.get("expectancy_r") or -999.0) >= min_expectancy,
        "min_winrate_pct": float(summary.get("winrate_pct") or 0.0) >= min_winrate,
        "min_stable_folds": int(window.get("stable_folds") or 0) >= min_stable_folds,
        "max_drawdown_r": float(summary.get("max_drawdown_r") or -999.0) >= -abs(max_drawdown),
        "cost_stress_positive": float(stress.get("expectancy_r") or -999.0) > 0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def evaluate_config(config: RotationConfig, windows: dict[str, dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    train = evaluate_window(config, windows["train"]["bars"], windows["train"]["features"], args, args.folds)
    train_gate = gate(
        train,
        min_trades=args.train_min_trades,
        min_expectancy=args.train_min_expectancy_r,
        min_stable_folds=args.train_min_stable_folds,
        min_winrate=args.train_min_winrate_pct,
        max_drawdown=args.train_max_drawdown_r,
    )
    validation = evaluate_window(config, windows["validation"]["bars"], windows["validation"]["features"], args, args.folds)
    validation_gate = gate(
        validation,
        min_trades=args.validation_min_trades,
        min_expectancy=args.validation_min_expectancy_r,
        min_stable_folds=args.validation_min_stable_folds,
        min_winrate=args.validation_min_winrate_pct,
        max_drawdown=args.validation_max_drawdown_r,
    )
    oos = evaluate_window(config, windows["oos"]["bars"], windows["oos"]["features"], args, args.folds)
    oos_gate = gate(
        oos,
        min_trades=args.oos_min_trades,
        min_expectancy=args.oos_min_expectancy_r,
        min_stable_folds=args.oos_min_stable_folds,
        min_winrate=args.oos_min_winrate_pct,
        max_drawdown=args.oos_max_drawdown_r,
    )
    decision = "reject_train_gate_failed"
    if train_gate["pass"] and not validation_gate["pass"]:
        decision = "reject_validation_gate_failed_oos_unopened"
    elif train_gate["pass"] and validation_gate["pass"] and not oos_gate["pass"]:
        decision = "reject_oos_gate_failed"
    elif train_gate["pass"] and validation_gate["pass"] and oos_gate["pass"]:
        decision = "candidate_needs_forward_proof"
    return {
        "config": {**asdict(config), "alt_symbols": list(config.alt_symbols)},
        "strategy_id": config.strategy_id,
        "family": "relative_strength_rotation",
        "train": {key: value for key, value in train.items() if key != "trades"},
        "validation": {key: value for key, value in validation.items() if key != "trades"},
        "oos": {key: value for key, value in oos.items() if key != "trades"},
        "gates": {"train": train_gate, "validation": validation_gate, "oos": oos_gate},
        "decision": decision,
        "can_trade": False,
    }


def build_configs(interval: str, alt_symbols: tuple[str, ...], max_configs: int, seed: int) -> list[RotationConfig]:
    rows: list[RotationConfig] = []
    alt_sets = [alt_symbols]
    if len(alt_symbols) >= 2:
        alt_sets.extend(tuple([symbol]) for symbol in alt_symbols)
        alt_sets.extend(tuple(combo) for combo in itertools.combinations(alt_symbols, 2))
    for side, mode, lookback, threshold, alt_set, alt_confirm, atr_regime, hold in itertools.product(
        ["LONG", "SHORT"],
        ["btc_leads", "btc_lags", "dispersion_abs"],
        [6, 12, 24, 48],
        [0.5, 1.0, 2.0, 3.0],
        alt_sets,
        ["none", "alts_up", "alts_down", "mixed"],
        ["none", "low", "mid", "high"],
        [8, 16, 24],
    ):
        sid = (
            f"rel_strength_{interval}_{side.lower()}_{mode}_lb{lookback}_thr{threshold:g}"
            f"_{'-'.join(alt_set).lower()}_{alt_confirm}_atr{atr_regime}_hold{hold}"
        )
        rows.append(
            RotationConfig(
                strategy_id=sid,
                interval=interval,
                side=side,
                mode=mode,
                lookback=lookback,
                min_rel_strength_pct=threshold,
                alt_symbols=alt_set,
                alt_confirm=alt_confirm,
                atr_regime_filter=atr_regime,
                stop_atr=1.0,
                take_atr=3.0,
                max_hold_bars=hold,
            )
        )
    rng = random.Random(seed)
    rng.shuffle(rows)
    return rows[:max_configs]


def result_sort_key(row: dict[str, Any]) -> tuple[int, float, int, float]:
    rank = {
        "candidate_needs_forward_proof": 3,
        "reject_oos_gate_failed": 2,
        "reject_validation_gate_failed_oos_unopened": 1,
        "reject_train_gate_failed": 0,
    }.get(row.get("decision"), 0)
    val_exp = float(row.get("validation", {}).get("summary", {}).get("expectancy_r") or -999.0)
    val_trades = int(row.get("validation", {}).get("summary", {}).get("trades") or 0)
    oos_exp = float(row.get("oos", {}).get("summary", {}).get("expectancy_r") or -999.0)
    return (rank, val_exp, val_trades, oos_exp)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Relative Strength Rotation Nested Holdout",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Tested configs: `{report['summary']['tested_configs']}`",
        f"- Train qualified: `{report['summary']['train_qualified']}`",
        f"- Validation qualified: `{report['summary']['validation_qualified']}`",
        f"- OOS qualified: `{report['summary']['oos_qualified']}`",
        "",
        "## Boundary",
        "",
        "- Research-only cross-asset relative-strength test.",
        "- BTCUSDT futures traded in simulation only; ETH/SOL/BCH are context inputs.",
        "- Completed-bar signal, next-bar open entry.",
        "- No private credentials, no network, no paper/live orders.",
        "",
        "## Top Results",
        "",
        "| Strategy | Decision | Side | Mode | Lookback | Threshold | Alts | Train Exp | Val Trades | Val Exp | OOS Trades | OOS Exp |",
        "| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report.get("top_results", [])[:30]:
        cfg = row["config"]
        lines.append(
            "| `{sid}` | `{decision}` | `{side}` | `{mode}` | `{lb}` | `{thr}` | `{alts}` | `{train_exp}` | `{val_trades}` | `{val_exp}` | `{oos_trades}` | `{oos_exp}` |".format(
                sid=row["strategy_id"],
                decision=row["decision"],
                side=cfg["side"],
                mode=cfg["mode"],
                lb=cfg["lookback"],
                thr=cfg["min_rel_strength_pct"],
                alts=",".join(cfg["alt_symbols"]),
                train_exp=row["train"]["summary"].get("expectancy_r"),
                val_trades=row["validation"]["summary"].get("trades"),
                val_exp=row["validation"]["summary"].get("expectancy_r"),
                oos_trades=row["oos"]["summary"].get("trades"),
                oos_exp=row["oos"]["summary"].get("expectancy_r"),
            )
        )
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "strategy_id",
        "decision",
        "side",
        "mode",
        "lookback",
        "threshold",
        "alt_symbols",
        "train_trades",
        "train_expectancy_r",
        "validation_trades",
        "validation_expectancy_r",
        "oos_trades",
        "oos_expectancy_r",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            cfg = row["config"]
            writer.writerow(
                {
                    "strategy_id": row["strategy_id"],
                    "decision": row["decision"],
                    "side": cfg["side"],
                    "mode": cfg["mode"],
                    "lookback": cfg["lookback"],
                    "threshold": cfg["min_rel_strength_pct"],
                    "alt_symbols": ",".join(cfg["alt_symbols"]),
                    "train_trades": row["train"]["summary"].get("trades"),
                    "train_expectancy_r": row["train"]["summary"].get("expectancy_r"),
                    "validation_trades": row["validation"]["summary"].get("trades"),
                    "validation_expectancy_r": row["validation"]["summary"].get("expectancy_r"),
                    "oos_trades": row["oos"]["summary"].get("trades"),
                    "oos_expectancy_r": row["oos"]["summary"].get("expectancy_r"),
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research-only BTC relative-strength rotation nested holdout")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--alt-symbols", default="ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--max-configs", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=2701)
    parser.add_argument("--train-end", default="2022-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--atr-window", type=int, default=20)
    parser.add_argument("--atr-ratio-window", type=int, default=100)
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--cost-stress-extra-bps", type=float, default=2.0)
    parser.add_argument("--no-overlap", action="store_true", default=True)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--train-min-trades", type=int, default=60)
    parser.add_argument("--validation-min-trades", type=int, default=40)
    parser.add_argument("--oos-min-trades", type=int, default=40)
    parser.add_argument("--train-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--validation-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--oos-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--train-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--validation-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--oos-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--train-min-stable-folds", type=int, default=2)
    parser.add_argument("--validation-min-stable-folds", type=int, default=2)
    parser.add_argument("--oos-min-stable-folds", type=int, default=2)
    parser.add_argument("--train-max-drawdown-r", type=float, default=40.0)
    parser.add_argument("--validation-max-drawdown-r", type=float, default=30.0)
    parser.add_argument("--oos-max-drawdown-r", type=float, default=30.0)
    parser.add_argument("--out-prefix", default="docs/RELATIVE_STRENGTH_ROTATION_NESTED_HOLDOUT_2026-07-01")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir = resolve_path(args.cache_dir)
    alt_symbols = tuple(item.strip().upper() for item in args.alt_symbols.split(",") if item.strip())
    btc_bars = load_symbol_bars(cache_dir, "BTCUSDT", args.interval)
    alt_bars = {symbol: load_symbol_bars(cache_dir, symbol, args.interval) for symbol in alt_symbols}
    train_end = parse_ts(args.train_end)
    validation_end = parse_ts(args.validation_end)
    windows: dict[str, dict[str, Any]] = {}
    for name, start, end in (
        ("train", None, train_end),
        ("validation", train_end, validation_end),
        ("oos", validation_end, None),
    ):
        bars = split_bars(btc_bars, start, end)
        alt_window = {symbol: split_bars(rows, start, end) for symbol, rows in alt_bars.items()}
        features = build_features(bars, alt_window, args.atr_window, args.atr_ratio_window)
        add_return_cache(features, [6, 12, 24, 48])
        windows[name] = {
            "bars": bars,
            "features": features,
        }
    configs = build_configs(args.interval, alt_symbols, args.max_configs, args.seed)
    results = [evaluate_config(config, windows, args) for config in configs]
    results.sort(key=result_sort_key, reverse=True)
    train_qualified = [row for row in results if row["gates"]["train"]["pass"]]
    validation_qualified = [row for row in results if row["gates"]["train"]["pass"] and row["gates"]["validation"]["pass"]]
    oos_qualified = [row for row in validation_qualified if row["gates"]["oos"]["pass"]]
    decision = "reject_no_train_qualified_relative_strength_rotation"
    next_action = "reject this mechanism; do not retune without a materially different relative-strength hypothesis"
    if train_qualified and not validation_qualified:
        decision = "reject_validation_gate_failed_oos_unopened"
        next_action = "relative-strength signal overfit on train; do not promote"
    elif validation_qualified and not oos_qualified:
        decision = "reject_oos_gate_failed"
        next_action = "reject relative-strength rotation for promotion; keep as tombstone evidence"
    elif oos_qualified:
        decision = "candidate_needs_forward_proof"
        next_action = "route top relative-strength candidate into observer-only forward proof, not live trading"
    report = {
        "generated_at": now_iso(),
        "tool": "tools/relative_strength_rotation_nested_holdout.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "research_only": True,
            "completed_bar_next_open": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "network_required": False,
        },
        "summary": {
            "tested_configs": len(results),
            "train_qualified": len(train_qualified),
            "validation_qualified": len(validation_qualified),
            "oos_qualified": len(oos_qualified),
            "interval": args.interval,
            "alt_symbols": list(alt_symbols),
        },
        "settings": vars(args),
        "windows": {name: {"bars": len(payload["bars"])} for name, payload in windows.items()},
        "top_results": results[:100],
        "next_action": next_action,
    }
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_csv(out_prefix.with_name(out_prefix.name + "_top_results.csv"), results[:200])
    print(
        json.dumps(
            {
                "decision": decision,
                "summary": report["summary"],
                "json": portable(out_prefix.with_suffix(".json")),
                "md": portable(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
