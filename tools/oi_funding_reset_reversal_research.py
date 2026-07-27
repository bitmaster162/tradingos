#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Bar:
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float | None
    funding: float | None


@dataclass(frozen=True)
class Config:
    interval: str
    side: str
    lookback: int
    impulse_atr: float
    oi_build_pct: float
    oi_reset_pct: float
    funding_abs_max: float
    stop_atr: float
    take_atr: float
    max_hold_bars: int

    @property
    def strategy_id(self) -> str:
        return (
            f"oi_funding_reset_{self.side}_{self.interval}"
            f"_lb{self.lookback}_imp{self.impulse_atr:g}"
            f"_build{self.oi_build_pct:g}_reset{self.oi_reset_pct:g}"
            f"_fund{self.funding_abs_max:g}_s{self.stop_atr:g}_t{self.take_atr:g}_h{self.max_hold_bars}"
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_float(value: Any) -> float | None:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) else result


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_bars(cache_dir: Path, interval: str) -> list[Bar]:
    kline_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
    oi_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_oi_aligned.csv"
    oi_by_time = {str(row.get("time", "")).strip(): row for row in read_csv(oi_path)}
    bars: list[Bar] = []
    for row in read_csv(kline_path):
        ts = str(row.get("time", "")).strip()
        oi_row = oi_by_time.get(ts, {})
        open_ = safe_float(row.get("open"))
        high = safe_float(row.get("high"))
        low = safe_float(row.get("low"))
        close = safe_float(row.get("close"))
        volume = safe_float(row.get("volume"))
        if not ts or open_ is None or high is None or low is None or close is None or volume is None:
            continue
        bars.append(
            Bar(
                ts=ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                oi=safe_float(oi_row.get("open_interest")),
                funding=safe_float(oi_row.get("funding")),
            )
        )
    return bars


def atr_values(bars: list[Bar], length: int = 14) -> list[float | None]:
    true_ranges: list[float] = []
    out: list[float | None] = [None] * len(bars)
    for index, bar in enumerate(bars):
        if index == 0:
            tr = bar.high - bar.low
        else:
            prev_close = bars[index - 1].close
            tr = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))
        true_ranges.append(tr)
        if index + 1 >= length:
            out[index] = sum(true_ranges[index + 1 - length : index + 1]) / length
    return out


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def simulate_trade(bars: list[Bar], entry_index: int, side: str, atr: float, stop_atr: float, take_atr: float, max_hold_bars: int) -> dict[str, Any]:
    entry = bars[entry_index].open
    if side == "long":
        stop = entry - stop_atr * atr
        take = entry + take_atr * atr
    else:
        stop = entry + stop_atr * atr
        take = entry - take_atr * atr
    last_index = min(len(bars) - 1, entry_index + max_hold_bars)
    exit_price = bars[last_index].close
    exit_reason = "time"
    exit_index = last_index
    for index in range(entry_index, last_index + 1):
        bar = bars[index]
        if side == "long":
            stop_hit = bar.low <= stop
            take_hit = bar.high >= take
            if stop_hit and take_hit:
                exit_price = stop
                exit_reason = "stop_first_same_bar"
                exit_index = index
                break
            if stop_hit:
                exit_price = stop
                exit_reason = "stop"
                exit_index = index
                break
            if take_hit:
                exit_price = take
                exit_reason = "take"
                exit_index = index
                break
        else:
            stop_hit = bar.high >= stop
            take_hit = bar.low <= take
            if stop_hit and take_hit:
                exit_price = stop
                exit_reason = "stop_first_same_bar"
                exit_index = index
                break
            if stop_hit:
                exit_price = stop
                exit_reason = "stop"
                exit_index = index
                break
            if take_hit:
                exit_price = take
                exit_reason = "take"
                exit_index = index
                break
    pnl = (exit_price - entry) if side == "long" else (entry - exit_price)
    risk = stop_atr * atr
    return {
        "entry_ts": bars[entry_index].ts,
        "exit_ts": bars[exit_index].ts,
        "side": side,
        "entry": entry,
        "exit": exit_price,
        "r": pnl / risk if risk > 0 else 0.0,
        "exit_reason": exit_reason,
        "hold_bars": exit_index - entry_index,
    }


def split_name(ts: str, train_end: str, validation_end: str) -> str:
    if ts < train_end:
        return "train"
    if ts < validation_end:
        return "validation"
    return "oos"


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "winrate_pct": 0.0,
            "expectancy_r": 0.0,
            "net_r": 0.0,
            "max_drawdown_r": 0.0,
            "profit_factor": None,
        }
    rs = [float(row["r"]) for row in trades]
    wins = [value for value in rs if value > 0]
    losses = [value for value in rs if value <= 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in rs:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(trades) * 100.0, 3),
        "expectancy_r": round(statistics.mean(rs), 6),
        "median_r": round(statistics.median(rs), 6),
        "net_r": round(sum(rs), 6),
        "avg_win_r": round(statistics.mean(wins), 6) if wins else 0.0,
        "avg_loss_r": round(statistics.mean(losses), 6) if losses else 0.0,
        "max_drawdown_r": round(max_dd, 6),
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else None,
    }


def fold_stability(trades: list[dict[str, Any]], folds: int = 4) -> dict[str, Any]:
    if len(trades) < folds:
        return {"folds": 0, "positive_folds": 0, "fold_expectancies": []}
    ordered = sorted(trades, key=lambda row: row["entry_ts"])
    chunks: list[list[dict[str, Any]]] = []
    for fold in range(folds):
        start = int(len(ordered) * fold / folds)
        end = int(len(ordered) * (fold + 1) / folds)
        chunks.append(ordered[start:end])
    expectancies = [summarize(chunk)["expectancy_r"] for chunk in chunks if chunk]
    return {
        "folds": len(expectancies),
        "positive_folds": sum(1 for value in expectancies if value > 0),
        "fold_expectancies": expectancies,
    }


def detect_and_score(bars: list[Bar], atrs: list[float | None], config: Config, train_end: str, validation_end: str) -> dict[str, Any]:
    trades_by_split = {"train": [], "validation": [], "oos": []}
    events = 0
    for index in range(config.lookback + 1, len(bars) - 1):
        atr = atrs[index - 1]
        if atr is None or atr <= 0:
            continue
        previous = bars[index - 1 - config.lookback]
        event_prev = bars[index - 1]
        event_bar = bars[index]
        if previous.oi is None or event_prev.oi is None or event_bar.oi is None:
            continue
        funding = event_bar.funding
        if funding is None or abs(funding) > config.funding_abs_max:
            continue
        price_impulse_atr = (event_prev.close - previous.close) / atr
        oi_build = pct_change(event_prev.oi, previous.oi)
        oi_reset = pct_change(event_bar.oi, event_prev.oi)
        if oi_build is None or oi_reset is None:
            continue
        if oi_build < config.oi_build_pct or oi_reset > -config.oi_reset_pct:
            continue
        if config.side == "long":
            if price_impulse_atr > -config.impulse_atr:
                continue
        else:
            if price_impulse_atr < config.impulse_atr:
                continue
        entry_index = index + 1
        if entry_index >= len(bars):
            continue
        trade = simulate_trade(
            bars,
            entry_index,
            config.side,
            atr,
            config.stop_atr,
            config.take_atr,
            config.max_hold_bars,
        )
        trade.update(
            {
                "strategy_id": config.strategy_id,
                "interval": config.interval,
                "price_impulse_atr": round(price_impulse_atr, 6),
                "oi_build_pct": round(oi_build, 6),
                "oi_reset_pct": round(oi_reset, 6),
                "funding": funding,
            }
        )
        trades_by_split[split_name(trade["entry_ts"], train_end, validation_end)].append(trade)
        events += 1
    all_trades = [trade for rows in trades_by_split.values() for trade in rows]
    return {
        "strategy_id": config.strategy_id,
        "config": config.__dict__,
        "events": events,
        "summary": {
            "all": summarize(all_trades),
            "train": summarize(trades_by_split["train"]),
            "validation": summarize(trades_by_split["validation"]),
            "oos": summarize(trades_by_split["oos"]),
        },
        "folds": {
            "train": fold_stability(trades_by_split["train"]),
            "validation": fold_stability(trades_by_split["validation"]),
            "oos": fold_stability(trades_by_split["oos"]),
        },
    }


def train_gate(row: dict[str, Any]) -> bool:
    train = row["summary"]["train"]
    folds = row["folds"]["train"]
    return (
        train["trades"] >= 40
        and train["expectancy_r"] >= 0.05
        and train["profit_factor"] is not None
        and train["profit_factor"] >= 1.10
        and folds["positive_folds"] >= 2
        and train["max_drawdown_r"] >= -20.0
    )


def validation_gate(row: dict[str, Any]) -> bool:
    val = row["summary"]["validation"]
    folds = row["folds"]["validation"]
    return (
        val["trades"] >= 20
        and val["expectancy_r"] >= 0.03
        and val["profit_factor"] is not None
        and val["profit_factor"] >= 1.05
        and folds["positive_folds"] >= 2
    )


def oos_gate(row: dict[str, Any]) -> bool:
    oos = row["summary"]["oos"]
    folds = row["folds"]["oos"]
    return (
        oos["trades"] >= 20
        and oos["expectancy_r"] >= 0.03
        and oos["profit_factor"] is not None
        and oos["profit_factor"] >= 1.05
        and folds["positive_folds"] >= 2
    )


def render_md(report: dict[str, Any]) -> str:
    lines = [
        "# OI/Funding Reset Reversal Research",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- decision: `{report['decision']}`",
        f"- tested: `{report['tested']}`",
        f"- train_qualified: `{report['train_qualified']}`",
        f"- validation_qualified: `{report['validation_qualified']}`",
        f"- oos_qualified: `{report['oos_qualified']}`",
        f"- can_trade: `{report['can_trade']}`",
        "",
        "## Thesis",
        "",
        "After a directional price impulse with OI build, a sharp OI reset plus compressed funding can mark leverage flush/reload conditions. This pass tests only fixed historical research variants and grants no signal permission.",
        "",
        "## Top Results",
        "",
    ]
    rows = report.get("top_results", [])
    if not rows:
        lines.append("- none")
    else:
        lines.append("| strategy | train exp/trades | validation exp/trades | oos exp/trades | status |")
        lines.append("|---|---:|---:|---:|---|")
        for row in rows[:12]:
            summary = row["summary"]
            lines.append(
                "| `{}` | `{}`/`{}` | `{}`/`{}` | `{}`/`{}` | `{}` |".format(
                    row["strategy_id"],
                    summary["train"]["expectancy_r"],
                    summary["train"]["trades"],
                    summary["validation"]["expectancy_r"],
                    summary["validation"]["trades"],
                    summary["oos"]["expectancy_r"],
                    summary["oos"]["trades"],
                    row.get("status"),
                )
            )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Research only.",
            "- Does not emit alerts, paper entries, or orders.",
            "- Any qualified historical candidate still requires a forward observer lock before promotion discussion.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only OI reset + funding compression reversal event study")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--max-configs", type=int, default=1200, help="Hard cap for bounded research passes")
    parser.add_argument("--out-prefix", default="docs/OI_FUNDING_RESET_REVERSAL_RESEARCH_2026-07-03")
    args = parser.parse_args()

    cache_dir = ROOT / args.cache_dir
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    configs: list[Config] = []
    for interval, side, lookback, impulse_atr, oi_build, oi_reset, funding_abs, rr, max_hold in itertools.product(
        intervals,
        ("long", "short"),
        (6, 12),
        (1.5, 2.5),
        (0.5, 1.0),
        (0.25, 0.5),
        (0.0002, 0.0004),
        ((1.0, 1.5), (1.0, 2.0), (1.5, 3.0)),
        (8, 16),
    ):
        configs.append(
            Config(
                interval=interval,
                side=side,
                lookback=lookback,
                impulse_atr=impulse_atr,
                oi_build_pct=oi_build,
                oi_reset_pct=oi_reset,
                funding_abs_max=funding_abs,
                stop_atr=rr[0],
                take_atr=rr[1],
                max_hold_bars=max_hold,
            )
        )
    total_configs_before_cap = len(configs)
    if args.max_configs > 0:
        configs = configs[: args.max_configs]

    by_interval: dict[str, tuple[list[Bar], list[float | None]]] = {}
    for interval in intervals:
        bars = load_bars(cache_dir, interval)
        by_interval[interval] = (bars, atr_values(bars))

    results: list[dict[str, Any]] = []
    for config in configs:
        bars, atrs = by_interval[config.interval]
        if not bars:
            continue
        row = detect_and_score(bars, atrs, config, args.train_end, args.validation_end)
        if row["summary"]["all"]["trades"] == 0:
            continue
        train_ok = train_gate(row)
        validation_ok = validation_gate(row)
        oos_ok = oos_gate(row)
        if train_ok and validation_ok and oos_ok:
            status = "historical_oos_qualified_needs_forward_observer"
        elif train_ok and validation_ok:
            status = "validation_passed_oos_failed_or_insufficient"
        elif train_ok:
            status = "train_passed_validation_failed"
        else:
            status = "train_failed"
        row["status"] = status
        row["gates"] = {"train": train_ok, "validation": validation_ok, "oos": oos_ok}
        results.append(row)

    train_qualified = [row for row in results if row["gates"]["train"]]
    validation_qualified = [row for row in results if row["gates"]["train"] and row["gates"]["validation"]]
    oos_qualified = [row for row in validation_qualified if row["gates"]["oos"]]
    top_results = sorted(
        results,
        key=lambda row: (
            row["gates"]["train"] and row["gates"]["validation"] and row["gates"]["oos"],
            row["gates"]["train"] and row["gates"]["validation"],
            row["gates"]["train"],
            row["summary"]["validation"]["expectancy_r"],
            row["summary"]["oos"]["expectancy_r"],
            row["summary"]["all"]["trades"],
        ),
        reverse=True,
    )[:30]
    if oos_qualified:
        decision = "oi_funding_reset_reversal_historical_oos_candidate_needs_forward_lock"
    elif validation_qualified:
        decision = "oi_funding_reset_reversal_validation_only_no_promotion"
    elif train_qualified:
        decision = "oi_funding_reset_reversal_train_only_no_promotion"
    else:
        decision = "reject_no_train_qualified_oi_funding_reset_candidate"

    report = {
        "generated_at": now_iso(),
        "tool": "tools/oi_funding_reset_reversal_research.py",
        "decision": decision,
        "cache_dir": str(cache_dir.relative_to(ROOT)),
        "split": {"train_end": args.train_end, "validation_end": args.validation_end},
        "grid": {
            "total_configs_before_cap": total_configs_before_cap,
            "max_configs": args.max_configs,
            "truncated": len(configs) < total_configs_before_cap,
        },
        "tested": len(configs),
        "nonzero_results": len(results),
        "train_qualified": len(train_qualified),
        "validation_qualified": len(validation_qualified),
        "oos_qualified": len(oos_qualified),
        "top_results": top_results,
        "runtime_boundary": {
            "research_only": True,
            "alerts_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    out_prefix = ROOT / args.out_prefix
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_md(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "tested": report["tested"],
                "train_qualified": report["train_qualified"],
                "validation_qualified": report["validation_qualified"],
                "oos_qualified": report["oos_qualified"],
                "out": str(out_prefix.with_suffix(".json").relative_to(ROOT)),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
