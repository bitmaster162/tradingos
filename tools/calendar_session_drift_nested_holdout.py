#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import random
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
class CalendarConfig:
    strategy_id: str
    interval: str
    side: str
    entry_hour_utc: int
    weekday_mode: str
    prior_return_filter: str
    prior_lookback_bars: int
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


def load_interval_bars(cache_dir: Path, interval: str) -> list[Any]:
    path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
    return load_ohlcv(path)


def split_bars(bars: list[Any], start: datetime | None, end: datetime | None) -> list[Any]:
    out: list[Any] = []
    for bar in bars:
        ts = parse_ts(str(bar.ts))
        if start is not None and ts < start:
            continue
        if end is not None and ts >= end:
            continue
        out.append(bar)
    return out


def weekday_ok(mode: str, ts: datetime) -> bool:
    weekday = ts.weekday()
    if mode == "all":
        return True
    if mode == "mon_fri":
        return weekday < 5
    if mode == "weekend":
        return weekday >= 5
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    if mode in names:
        return weekday == names.index(mode)
    raise ValueError(f"unsupported weekday_mode={mode}")


def build_features(bars: list[Any], atr_window: int, atr_ratio_window: int) -> dict[str, list[Any]]:
    closes = [float(bar.close) for bar in bars]
    atr = compute_atr(bars, atr_window)
    atr_mean = rolling_mean(atr, atr_ratio_window)
    atr_ratio = [
        (float(atr_value) / float(mean_value)) if atr_value is not None and mean_value not in {None, 0} else None
        for atr_value, mean_value in zip(atr, atr_mean)
    ]
    return {"closes": closes, "atr": atr, "atr_ratio": atr_ratio}


def prior_return_ok(config: CalendarConfig, features: dict[str, list[Any]], index: int) -> bool:
    if config.prior_return_filter == "none":
        return True
    lookback = config.prior_lookback_bars
    if index < lookback:
        return False
    closes = features["closes"]
    prior = closes[index - lookback]
    current = closes[index]
    if prior <= 0:
        return False
    ret = (current - prior) / prior
    if config.prior_return_filter == "up":
        return ret > 0
    if config.prior_return_filter == "down":
        return ret < 0
    if config.prior_return_filter == "flat":
        return abs(ret) <= 0.01
    raise ValueError(f"unsupported prior_return_filter={config.prior_return_filter}")


def atr_regime_ok(config: CalendarConfig, features: dict[str, list[Any]], index: int) -> bool:
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


def generate_signals(config: CalendarConfig, bars: list[Any], features: dict[str, list[Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    last_day_key: str | None = None
    for index, bar in enumerate(bars):
        if index + config.max_hold_bars + 1 >= len(bars):
            continue
        ts = parse_ts(str(bar.ts))
        if ts.hour != config.entry_hour_utc:
            continue
        if not weekday_ok(config.weekday_mode, ts):
            continue
        day_key = f"{ts.date()}|{config.entry_hour_utc}"
        if day_key == last_day_key:
            continue
        last_day_key = day_key
        atr = features["atr"][index]
        if atr is None or float(atr) <= 0:
            continue
        if not prior_return_ok(config, features, index):
            continue
        if not atr_regime_ok(config, features, index):
            continue
        signals.append(
            {
                "bar_index": index,
                "side_hint": config.side,
                "atr": float(atr),
                "reason": "calendar_session_drift",
                "entry_hour_utc": config.entry_hour_utc,
                "weekday_mode": config.weekday_mode,
                "prior_return_filter": config.prior_return_filter,
                "atr_regime_filter": config.atr_regime_filter,
            }
        )
    return signals


def replay(config: CalendarConfig, bars: list[Any], features: dict[str, list[Any]], cost_bps_per_side: float, no_overlap: bool) -> list[Trade]:
    trades: list[Trade] = []
    last_exit_bar = -1
    bar_index_by_ts = {str(bar.ts): index for index, bar in enumerate(bars)}
    for signal in generate_signals(config, bars, features):
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=f"calendar_session_drift_BTCUSDT_{config.interval}",
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
    out: list[dict[str, Any]] = []
    for fold in range(folds):
        start = round(len(ordered) * fold / folds)
        end = round(len(ordered) * (fold + 1) / folds)
        chunk = ordered[start:end]
        summary = summarize_trades(chunk)
        summary["fold"] = fold + 1
        summary["stable"] = bool(summary["trades"] >= 5 and (summary["expectancy_r"] or 0.0) > 0)
        out.append(summary)
    return out


def evaluate_window(config: CalendarConfig, bars: list[Any], features: dict[str, list[Any]], args: argparse.Namespace, folds: int) -> dict[str, Any]:
    cost = args.fee_bps + args.slippage_bps
    trades = replay(config, bars, features, cost, args.no_overlap)
    stress_trades = replay(config, bars, features, cost + args.cost_stress_extra_bps, args.no_overlap)
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


def evaluate_config(config: CalendarConfig, windows: dict[str, dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
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
        "config": asdict(config),
        "strategy_id": config.strategy_id,
        "family": "calendar_session_drift",
        "train": {key: value for key, value in train.items() if key != "trades"},
        "validation": {key: value for key, value in validation.items() if key != "trades"},
        "oos": {key: value for key, value in oos.items() if key != "trades"},
        "gates": {"train": train_gate, "validation": validation_gate, "oos": oos_gate},
        "decision": decision,
        "can_trade": False,
    }


def build_configs(intervals: list[str], max_per_interval: int, seed: int) -> list[CalendarConfig]:
    configs: list[CalendarConfig] = []
    entry_hours = [0, 4, 8, 12, 16, 20]
    weekday_modes = ["all", "mon_fri", "weekend", "mon", "tue", "wed", "thu", "fri"]
    prior_filters = ["none", "up", "down", "flat"]
    atr_filters = ["none", "low", "mid", "high"]
    hold_by_interval = {
        "15m": [16, 32, 64],
        "1h": [8, 16, 24],
        "4h": [4, 8, 12],
    }
    lookback_by_interval = {
        "15m": [16, 64, 96],
        "1h": [12, 24, 48],
        "4h": [6, 12, 24],
    }
    for interval in intervals:
        rows: list[CalendarConfig] = []
        for side, hour, weekday, prior_filter, atr_filter, hold, lookback in itertools.product(
            ["LONG", "SHORT"],
            entry_hours,
            weekday_modes,
            prior_filters,
            atr_filters,
            hold_by_interval.get(interval, [8, 16, 24]),
            lookback_by_interval.get(interval, [12, 24, 48]),
        ):
            sid = (
                f"calendar_drift_{interval}_{side.lower()}_h{hour:02d}_{weekday}"
                f"_pr{prior_filter}_atr{atr_filter}_lb{lookback}_sl1_tp3_hold{hold}"
            )
            rows.append(
                CalendarConfig(
                    strategy_id=sid,
                    interval=interval,
                    side=side,
                    entry_hour_utc=hour,
                    weekday_mode=weekday,
                    prior_return_filter=prior_filter,
                    prior_lookback_bars=lookback,
                    atr_regime_filter=atr_filter,
                    stop_atr=1.0,
                    take_atr=3.0,
                    max_hold_bars=hold,
                )
            )
        rng = random.Random(f"{seed}|{interval}")
        rng.shuffle(rows)
        configs.extend(rows[:max_per_interval])
    return configs


def result_sort_key(row: dict[str, Any]) -> tuple[int, float, int, float]:
    decision_rank = {
        "candidate_needs_forward_proof": 3,
        "reject_oos_gate_failed": 2,
        "reject_validation_gate_failed_oos_unopened": 1,
        "reject_train_gate_failed": 0,
    }.get(row.get("decision"), 0)
    val_exp = float(row.get("validation", {}).get("summary", {}).get("expectancy_r") or -999.0)
    val_trades = int(row.get("validation", {}).get("summary", {}).get("trades") or 0)
    oos_exp = float(row.get("oos", {}).get("summary", {}).get("expectancy_r") or -999.0)
    return (decision_rank, val_exp, val_trades, oos_exp)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Calendar Session Drift Nested Holdout",
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
        "- Research-only calendar/session drift test.",
        "- Completed-bar signal, next-bar open entry.",
        "- No private credentials, no network, no paper/live orders.",
        "- This is a materially different class from breakout/OI/basis/liquidation mechanisms.",
        "",
        "## Top Results",
        "",
        "| Strategy | Decision | TF | Side | Hour UTC | Weekday | Prior | ATR Regime | Train Exp | Val Trades | Val Exp | OOS Trades | OOS Exp |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report.get("top_results", [])[:30]:
        cfg = row["config"]
        lines.append(
            "| `{sid}` | `{decision}` | `{tf}` | `{side}` | `{hour}` | `{weekday}` | `{prior}` | `{atr}` | `{train_exp}` | `{val_trades}` | `{val_exp}` | `{oos_trades}` | `{oos_exp}` |".format(
                sid=row["strategy_id"],
                decision=row["decision"],
                tf=cfg["interval"],
                side=cfg["side"],
                hour=cfg["entry_hour_utc"],
                weekday=cfg["weekday_mode"],
                prior=cfg["prior_return_filter"],
                atr=cfg["atr_regime_filter"],
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
        "interval",
        "side",
        "entry_hour_utc",
        "weekday_mode",
        "prior_return_filter",
        "atr_regime_filter",
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
                    "interval": cfg["interval"],
                    "side": cfg["side"],
                    "entry_hour_utc": cfg["entry_hour_utc"],
                    "weekday_mode": cfg["weekday_mode"],
                    "prior_return_filter": cfg["prior_return_filter"],
                    "atr_regime_filter": cfg["atr_regime_filter"],
                    "train_trades": row["train"]["summary"].get("trades"),
                    "train_expectancy_r": row["train"]["summary"].get("expectancy_r"),
                    "validation_trades": row["validation"]["summary"].get("trades"),
                    "validation_expectancy_r": row["validation"]["summary"].get("expectancy_r"),
                    "oos_trades": row["oos"]["summary"].get("trades"),
                    "oos_expectancy_r": row["oos"]["summary"].get("expectancy_r"),
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research-only calendar/session drift nested holdout")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="1h,4h")
    parser.add_argument("--max-configs-per-interval", type=int, default=800)
    parser.add_argument("--seed", type=int, default=1701)
    parser.add_argument("--train-end", default="2022-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--atr-window", type=int, default=20)
    parser.add_argument("--atr-ratio-window", type=int, default=100)
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--cost-stress-extra-bps", type=float, default=2.0)
    parser.add_argument("--no-overlap", action="store_true", default=True)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--train-min-trades", type=int, default=40)
    parser.add_argument("--validation-min-trades", type=int, default=25)
    parser.add_argument("--oos-min-trades", type=int, default=25)
    parser.add_argument("--train-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--validation-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--oos-min-expectancy-r", type=float, default=0.08)
    parser.add_argument("--train-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--validation-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--oos-min-winrate-pct", type=float, default=32.0)
    parser.add_argument("--train-min-stable-folds", type=int, default=2)
    parser.add_argument("--validation-min-stable-folds", type=int, default=2)
    parser.add_argument("--oos-min-stable-folds", type=int, default=2)
    parser.add_argument("--train-max-drawdown-r", type=float, default=35.0)
    parser.add_argument("--validation-max-drawdown-r", type=float, default=25.0)
    parser.add_argument("--oos-max-drawdown-r", type=float, default=25.0)
    parser.add_argument("--out-prefix", default="docs/CALENDAR_SESSION_DRIFT_NESTED_HOLDOUT_2026-07-01")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir = resolve_path(args.cache_dir)
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    train_end = parse_ts(args.train_end)
    validation_end = parse_ts(args.validation_end)
    windows: dict[str, dict[str, dict[str, Any]]] = {}
    for interval in intervals:
        bars = load_interval_bars(cache_dir, interval)
        windows[interval] = {
            "train": {"bars": split_bars(bars, None, train_end)},
            "validation": {"bars": split_bars(bars, train_end, validation_end)},
            "oos": {"bars": split_bars(bars, validation_end, None)},
        }
        for payload in windows[interval].values():
            payload["features"] = build_features(payload["bars"], args.atr_window, args.atr_ratio_window)

    configs = build_configs(intervals, args.max_configs_per_interval, args.seed)
    results = [evaluate_config(config, windows[config.interval], args) for config in configs]
    results.sort(key=result_sort_key, reverse=True)
    train_qualified = [row for row in results if row["gates"]["train"]["pass"]]
    validation_qualified = [row for row in results if row["gates"]["train"]["pass"] and row["gates"]["validation"]["pass"]]
    oos_qualified = [row for row in validation_qualified if row["gates"]["oos"]["pass"]]
    decision = "reject_no_train_qualified_calendar_session_drift"
    next_action = "reject this independent class for now; do not retune without a different calendar hypothesis"
    if train_qualified and not validation_qualified:
        decision = "reject_validation_gate_failed_oos_unopened"
        next_action = "calendar drift overfit on train; do not open OOS for promotion"
    elif validation_qualified and not oos_qualified:
        decision = "reject_oos_gate_failed"
        next_action = "reject calendar drift for promotion; keep as tombstone evidence"
    elif oos_qualified:
        decision = "candidate_needs_forward_proof"
        next_action = "route top calendar drift candidate into observer-only forward proof, not live trading"
    report = {
        "generated_at": now_iso(),
        "tool": "tools/calendar_session_drift_nested_holdout.py",
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
            "intervals": intervals,
        },
        "settings": vars(args),
        "windows": {
            interval: {name: {"bars": len(payload["bars"])} for name, payload in by_window.items()}
            for interval, by_window in windows.items()
        },
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
