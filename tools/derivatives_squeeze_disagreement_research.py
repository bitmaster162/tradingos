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
    spot_close: float | None
    oi: float | None
    funding: float | None
    crowd_ratio: float | None


@dataclass(frozen=True)
class Config:
    interval: str
    side: str
    squeeze_window: int
    squeeze_pctile_max: float
    lookback: int
    oi_build_pct: float
    funding_min_abs: float
    spot_lead_min_pct: float
    crowd_extreme: float
    min_confirmations: int
    stop_atr: float
    take_atr: float
    max_hold_bars: int

    @property
    def strategy_id(self) -> str:
        return (
            f"deriv_squeeze_disagree_{self.side}_{self.interval}"
            f"_sw{self.squeeze_window}_p{self.squeeze_pctile_max:g}"
            f"_lb{self.lookback}_oi{self.oi_build_pct:g}"
            f"_fund{self.funding_min_abs:g}_spot{self.spot_lead_min_pct:g}"
            f"_crowd{self.crowd_extreme:g}_c{self.min_confirmations}"
            f"_s{self.stop_atr:g}_t{self.take_atr:g}_h{self.max_hold_bars}"
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


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def load_bars(cache_dir: Path, interval: str) -> list[Bar]:
    fut_dir = cache_dir / "futures" / "BTCUSDT"
    spot_dir = cache_dir / "spot" / "BTCUSDT"
    futures_rows = read_csv(fut_dir / f"{interval}_klines.csv")
    spot_by_time = {str(row.get("time", "")).strip(): row for row in read_csv(spot_dir / f"{interval}_klines.csv")}
    oi_by_time = {str(row.get("time", "")).strip(): row for row in read_csv(fut_dir / f"{interval}_oi_aligned.csv")}
    crowd_by_time = {str(row.get("time", "")).strip(): row for row in read_csv(fut_dir / f"{interval}_crowd_positioning.csv")}

    bars: list[Bar] = []
    for row in futures_rows:
        ts = str(row.get("time", "")).strip()
        open_ = safe_float(row.get("open"))
        high = safe_float(row.get("high"))
        low = safe_float(row.get("low"))
        close = safe_float(row.get("close"))
        volume = safe_float(row.get("volume"))
        if not ts or open_ is None or high is None or low is None or close is None or volume is None:
            continue
        spot_row = spot_by_time.get(ts, {})
        oi_row = oi_by_time.get(ts, {})
        crowd_row = crowd_by_time.get(ts, {})
        bars.append(
            Bar(
                ts=ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                spot_close=safe_float(spot_row.get("close")),
                oi=safe_float(oi_row.get("open_interest")),
                funding=safe_float(oi_row.get("funding")),
                crowd_ratio=safe_float(crowd_row.get("global_long_short_ratio")),
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


def rolling_range_pctiles(bars: list[Bar], window: int, rank_lookback: int = 200) -> list[float | None]:
    ranges: list[float | None] = [None] * len(bars)
    ranks: list[float | None] = [None] * len(bars)
    for index in range(window - 1, len(bars)):
        span = bars[index + 1 - window : index + 1]
        current = (max(bar.high for bar in span) - min(bar.low for bar in span)) / bars[index].close * 100.0
        ranges[index] = current
        start = max(0, index - rank_lookback)
        history = [value for value in ranges[start:index] if value is not None]
        if len(history) >= 50:
            ranks[index] = sum(1 for value in history if value <= current) / len(history)
    return ranks


def simulate_trade(
    bars: list[Bar],
    entry_index: int,
    side: str,
    atr: float,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
) -> dict[str, Any]:
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
        "exit_index": exit_index,
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
        "net_r": round(sum(rs), 6),
        "max_drawdown_r": round(max_dd, 6),
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else None,
        "avg_win_r": round(statistics.mean(wins), 6) if wins else 0.0,
        "avg_loss_r": round(statistics.mean(losses), 6) if losses else 0.0,
    }


def pass_gate(summary: dict[str, Any], min_trades: int) -> bool:
    return (
        int(summary.get("trades") or 0) >= min_trades
        and float(summary.get("expectancy_r") or 0.0) >= 0.05
        and float(summary.get("max_drawdown_r") or 0.0) >= -18.0
        and ((summary.get("profit_factor") is not None and float(summary["profit_factor"]) >= 1.08) or float(summary.get("expectancy_r") or 0.0) >= 0.12)
    )


def confirmations(bar: Bar, prev: Bar, cfg: Config, fut_ret: float | None, spot_ret: float | None, oi_chg: float | None) -> tuple[int, dict[str, Any]]:
    spot_lead = None if fut_ret is None or spot_ret is None else spot_ret - fut_ret
    checks: dict[str, Any] = {
        "oi_build_pct": oi_chg,
        "funding": bar.funding,
        "crowd_ratio": bar.crowd_ratio,
        "futures_ret_pct": fut_ret,
        "spot_ret_pct": spot_ret,
        "spot_lead_pct": spot_lead,
    }
    count = 0
    if oi_chg is not None and oi_chg >= cfg.oi_build_pct:
        count += 1
    if cfg.side == "long":
        crowd_limit = max(0.75, 2.8 - cfg.crowd_extreme)
        if bar.crowd_ratio is not None and bar.crowd_ratio <= crowd_limit:
            count += 1
        if bar.funding is not None and bar.funding <= -cfg.funding_min_abs:
            count += 1
        if spot_lead is not None and spot_lead >= cfg.spot_lead_min_pct:
            count += 1
        if fut_ret is not None and fut_ret > 0:
            count -= 1
    else:
        if bar.crowd_ratio is not None and bar.crowd_ratio >= cfg.crowd_extreme:
            count += 1
        if bar.funding is not None and bar.funding >= cfg.funding_min_abs:
            count += 1
        if spot_lead is not None and spot_lead <= -cfg.spot_lead_min_pct:
            count += 1
        if fut_ret is not None and fut_ret < 0:
            count -= 1
    checks["confirmation_count"] = count
    return count, checks


def evaluate_config(
    bars: list[Bar],
    atrs: list[float | None],
    squeeze_pctiles: list[float | None],
    cfg: Config,
    train_end: str,
    validation_end: str,
) -> dict[str, Any]:
    trades_by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "oos": []}
    last_exit = -1
    start_index = max(220, cfg.squeeze_window, cfg.lookback, 14)
    for index in range(start_index, len(bars) - 1):
        if index <= last_exit:
            continue
        atr = atrs[index]
        squeeze_rank = squeeze_pctiles[index]
        if atr is None or atr <= 0 or squeeze_rank is None or squeeze_rank > cfg.squeeze_pctile_max:
            continue
        bar = bars[index]
        lookback_bar = bars[index - cfg.lookback]
        fut_ret = pct_change(bar.close, lookback_bar.close)
        spot_ret = pct_change(bar.spot_close, lookback_bar.spot_close)
        oi_chg = pct_change(bar.oi, lookback_bar.oi)
        count, feature_snapshot = confirmations(bar, lookback_bar, cfg, fut_ret, spot_ret, oi_chg)
        if count < cfg.min_confirmations:
            continue
        trade = simulate_trade(
            bars,
            index + 1,
            cfg.side,
            atr,
            cfg.stop_atr,
            cfg.take_atr,
            cfg.max_hold_bars,
        )
        trade["signal_ts"] = bar.ts
        trade["squeeze_rank"] = round(squeeze_rank, 6)
        trade["feature_snapshot"] = feature_snapshot
        trades_by_split[split_name(trade["entry_ts"], train_end, validation_end)].append(trade)
        last_exit = int(trade["exit_index"])

    train = summarize(trades_by_split["train"])
    validation = summarize(trades_by_split["validation"])
    oos = summarize(trades_by_split["oos"])
    train_pass = pass_gate(train, min_trades=40)
    validation_pass = train_pass and pass_gate(validation, min_trades=15)
    oos_pass = validation_pass and pass_gate(oos, min_trades=15)
    status = "rejected_train_gate_failed"
    if train_pass and not validation_pass:
        status = "train_passed_validation_failed"
    elif validation_pass and not oos_pass:
        status = "validation_passed_oos_failed"
    elif oos_pass:
        status = "oos_pass_needs_forward_proof"
    return {
        "strategy_id": cfg.strategy_id,
        "config": cfg.__dict__,
        "status": status,
        "train_pass": train_pass,
        "validation_pass": validation_pass,
        "oos_pass": oos_pass,
        "train": train,
        "validation": validation,
        "oos": oos,
        "sample_trades": {
            "train": trades_by_split["train"][:3],
            "validation": trades_by_split["validation"][:3],
            "oos": trades_by_split["oos"][:3],
        },
    }


def bounded_grid(intervals: list[str], max_configs: int) -> list[Config]:
    values = list(itertools.product(
        intervals,
        ["long", "short"],
        [12, 24, 48],
        [0.10, 0.20, 0.30],
        [6, 12, 24],
        [0.25, 0.75, 1.5],
        [0.0001, 0.0003],
        [0.05, 0.15],
        [1.6, 2.0],
        [1, 2],
        [(1.0, 1.5), (1.0, 2.0), (1.5, 3.0)],
        [8, 16],
    ))
    configs = [
        Config(
            interval=interval,
            side=side,
            squeeze_window=squeeze_window,
            squeeze_pctile_max=squeeze_pctile_max,
            lookback=lookback,
            oi_build_pct=oi_build_pct,
            funding_min_abs=funding_min_abs,
            spot_lead_min_pct=spot_lead_min_pct,
            crowd_extreme=crowd_extreme,
            min_confirmations=min_confirmations,
            stop_atr=rr[0],
            take_atr=rr[1],
            max_hold_bars=max_hold_bars,
        )
        for (
            interval,
            side,
            squeeze_window,
            squeeze_pctile_max,
            lookback,
            oi_build_pct,
            funding_min_abs,
            spot_lead_min_pct,
            crowd_extreme,
            min_confirmations,
            rr,
            max_hold_bars,
        ) in values
    ]
    if max_configs and len(configs) > max_configs:
        step = len(configs) / max_configs
        return [configs[int(index * step)] for index in range(max_configs)]
    return configs


def sort_key(result: dict[str, Any]) -> tuple[int, int, float, float, int]:
    return (
        int(bool(result.get("oos_pass"))),
        int(bool(result.get("validation_pass"))),
        float(result.get("oos", {}).get("expectancy_r") or -999.0),
        float(result.get("validation", {}).get("expectancy_r") or -999.0),
        int(result.get("oos", {}).get("trades") or 0),
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = (ROOT / args.cache_dir).resolve() if not Path(args.cache_dir).is_absolute() else Path(args.cache_dir)
    intervals = [item.strip() for item in args.intervals.split(",") if item.strip()]
    configs = bounded_grid(intervals, args.max_configs)

    bars_by_interval: dict[str, list[Bar]] = {interval: load_bars(cache_dir, interval) for interval in intervals}
    atr_by_interval: dict[str, list[float | None]] = {interval: atr_values(bars) for interval, bars in bars_by_interval.items()}
    squeeze_cache: dict[tuple[str, int], list[float | None]] = {}
    results: list[dict[str, Any]] = []
    for cfg in configs:
        bars = bars_by_interval.get(cfg.interval, [])
        if len(bars) < 500:
            continue
        key = (cfg.interval, cfg.squeeze_window)
        if key not in squeeze_cache:
            squeeze_cache[key] = rolling_range_pctiles(bars, cfg.squeeze_window)
        results.append(
            evaluate_config(
                bars,
                atr_by_interval[cfg.interval],
                squeeze_cache[key],
                cfg,
                args.train_end,
                args.validation_end,
            )
        )

    train_qualified = [row for row in results if row.get("train_pass")]
    validation_qualified = [row for row in results if row.get("validation_pass")]
    oos_qualified = [row for row in results if row.get("oos_pass")]
    results.sort(key=sort_key, reverse=True)
    decision = "reject_no_train_qualified_derivatives_squeeze_candidate"
    if train_qualified and not validation_qualified:
        decision = "derivatives_squeeze_train_only_no_promotion"
    elif validation_qualified and not oos_qualified:
        decision = "reject_validation_passed_oos_failed"
    elif oos_qualified:
        decision = "oos_pass_derivatives_squeeze_needs_forward_proof"

    return {
        "generated_at": now_iso(),
        "tool": "derivatives_squeeze_disagreement_research",
        "decision": decision,
        "cache_dir": str(cache_dir),
        "split": {
            "train_end": args.train_end,
            "validation_end": args.validation_end,
            "oos_start": args.validation_end,
        },
        "hypothesis": (
            "BTC volatility compression becomes actionable only when derivatives positioning disagrees with spot/price: "
            "OI builds during squeeze while funding/crowd/spot-vs-perp skew marks a crowded side. "
            "This is a research-only contrarian breakout/fade test, not a live signal."
        ),
        "tested": len(results),
        "nonzero_results": sum(1 for row in results if (row.get("train", {}).get("trades") or 0) + (row.get("validation", {}).get("trades") or 0) + (row.get("oos", {}).get("trades") or 0) > 0),
        "train_qualified": len(train_qualified),
        "validation_qualified": len(validation_qualified),
        "oos_qualified": len(oos_qualified),
        "top_results": results[:20],
        "runtime_boundary": {
            "research_only": True,
            "alerts_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Derivatives Squeeze Disagreement Research",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        "",
        "## Boundary",
        "",
        "- Research-only.",
        "- No alerts, no paper-entry intents, no orders.",
        "- Conservative next-bar entry, non-overlapping trades, same-bar stop-first.",
        "",
        "## Summary",
        "",
        f"- Tested configs: `{report.get('tested')}`.",
        f"- Non-zero configs: `{report.get('nonzero_results')}`.",
        f"- Train qualified: `{report.get('train_qualified')}`.",
        f"- Validation qualified: `{report.get('validation_qualified')}`.",
        f"- OOS qualified: `{report.get('oos_qualified')}`.",
        "",
        "## Hypothesis",
        "",
        str(report.get("hypothesis") or ""),
        "",
        "## Top Results",
        "",
        "| status | strategy | train exp/trades | validation exp/trades | oos exp/trades |",
        "|---|---|---:|---:|---:|",
    ]
    for row in report.get("top_results", [])[:20]:
        train = row.get("train", {})
        validation = row.get("validation", {})
        oos = row.get("oos", {})
        lines.append(
            f"| {row.get('status')} | `{row.get('strategy_id')}` | "
            f"{train.get('expectancy_r')}/{train.get('trades')} | "
            f"{validation.get('expectancy_r')}/{validation.get('trades')} | "
            f"{oos.get('expectancy_r')}/{oos.get('trades')} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], out_prefix: str) -> None:
    prefix = ROOT / out_prefix if not Path(out_prefix).is_absolute() else Path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Research volatility squeeze + derivatives disagreement BTCUSDT mechanism")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--intervals", default="15m,1h,4h")
    parser.add_argument("--max-configs", type=int, default=1200)
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_SQUEEZE_DISAGREEMENT_RESEARCH_2026-07-03")
    args = parser.parse_args()
    report = build_report(args)
    write_outputs(report, args.out_prefix)
    print(json.dumps({
        "decision": report["decision"],
        "tested": report["tested"],
        "train_qualified": report["train_qualified"],
        "validation_qualified": report["validation_qualified"],
        "oos_qualified": report["oos_qualified"],
        "can_trade": report["can_trade"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
