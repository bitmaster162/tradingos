#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_squeeze_disagreement_research import (
    atr_values,
    pct_change,
    read_csv,
    safe_float,
    simulate_trade,
    summarize,
)


@dataclass(frozen=True)
class Bar:
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Config:
    mode: str
    lookback: int
    alt_move_pct: float
    btc_mute_pct: float
    gap_pct: float
    min_alt_count: int
    atr_filter: str
    stop_atr: float
    take_atr: float
    max_hold_bars: int

    @property
    def side(self) -> str:
        return "long" if self.mode in {"alt_up_btc_lag_long", "alt_down_btc_overreact_long"} else "short"

    @property
    def strategy_id(self) -> str:
        return (
            f"alt_breadth_dislocation_{self.mode}_lb{self.lookback}"
            f"_alt{self.alt_move_pct:g}_mute{self.btc_mute_pct:g}"
            f"_gap{self.gap_pct:g}_cnt{self.min_alt_count}_{self.atr_filter}"
            f"_s{self.stop_atr:g}_t{self.take_atr:g}_h{self.max_hold_bars}"
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_symbol(cache_dir: Path, symbol: str) -> list[Bar]:
    path = cache_dir / "futures" / symbol / "1h_klines.csv"
    bars: list[Bar] = []
    for row in read_csv(path):
        ts = str(row.get("time", "")).strip()
        open_ = safe_float(row.get("open"))
        high = safe_float(row.get("high"))
        low = safe_float(row.get("low"))
        close = safe_float(row.get("close"))
        volume = safe_float(row.get("volume"))
        if not ts or open_ is None or high is None or low is None or close is None or volume is None:
            continue
        bars.append(Bar(ts=ts, open=open_, high=high, low=low, close=close, volume=volume))
    return bars


def align_closes(btc: list[Bar], alt_bars: dict[str, list[Bar]]) -> dict[str, list[float | None]]:
    by_time = {
        symbol: {bar.ts: bar.close for bar in bars}
        for symbol, bars in alt_bars.items()
    }
    return {
        symbol: [values.get(bar.ts) for bar in btc]
        for symbol, values in by_time.items()
    }


def rolling_atr_ratio(atrs: list[float | None], window: int = 100) -> list[float | None]:
    out: list[float | None] = []
    history: list[float] = []
    for value in atrs:
        if value is not None and value > 0:
            history.append(value)
        if len(history) > window:
            history.pop(0)
        if value is None or len(history) < max(20, window // 4):
            out.append(None)
        else:
            mean = statistics.mean(history)
            out.append(value / mean if mean > 0 else None)
    return out


def atr_filter_ok(value: float | None, mode: str) -> bool:
    if mode == "none":
        return True
    if value is None:
        return False
    if mode == "low":
        return value <= 0.85
    if mode == "mid":
        return 0.85 < value < 1.15
    if mode == "high":
        return value >= 1.15
    raise ValueError(f"unsupported atr_filter={mode}")


def split_name(ts: str, train_end: str, validation_end: str) -> str:
    if ts < train_end:
        return "train"
    if ts < validation_end:
        return "validation"
    return "oos"


def pass_gate(summary: dict[str, Any], min_trades: int) -> bool:
    return (
        int(summary.get("trades") or 0) >= min_trades
        and float(summary.get("expectancy_r") or 0.0) >= 0.05
        and float(summary.get("max_drawdown_r") or 0.0) >= -18.0
        and ((summary.get("profit_factor") is not None and float(summary["profit_factor"]) >= 1.08) or float(summary.get("expectancy_r") or 0.0) >= 0.12)
    )


def mode_matches(cfg: Config, btc_ret: float, alt_returns: list[float]) -> tuple[bool, dict[str, Any]]:
    alt_up = [value for value in alt_returns if value >= cfg.alt_move_pct]
    alt_down = [value for value in alt_returns if value <= -cfg.alt_move_pct]
    alt_mean = statistics.mean(alt_returns) if alt_returns else 0.0
    gap_alt_minus_btc = alt_mean - btc_ret
    gap_btc_minus_alt = btc_ret - alt_mean
    snapshot = {
        "btc_ret_pct": round(btc_ret, 6),
        "alt_mean_ret_pct": round(alt_mean, 6),
        "alt_up_count": len(alt_up),
        "alt_down_count": len(alt_down),
        "gap_alt_minus_btc": round(gap_alt_minus_btc, 6),
        "gap_btc_minus_alt": round(gap_btc_minus_alt, 6),
    }
    if cfg.mode == "alt_up_btc_lag_long":
        return len(alt_up) >= cfg.min_alt_count and abs(btc_ret) <= cfg.btc_mute_pct and gap_alt_minus_btc >= cfg.gap_pct, snapshot
    if cfg.mode == "alt_up_btc_lag_short_fade":
        return len(alt_up) >= cfg.min_alt_count and abs(btc_ret) <= cfg.btc_mute_pct and gap_alt_minus_btc >= cfg.gap_pct, snapshot
    if cfg.mode == "alt_down_btc_lag_short":
        return len(alt_down) >= cfg.min_alt_count and abs(btc_ret) <= cfg.btc_mute_pct and gap_btc_minus_alt >= cfg.gap_pct, snapshot
    if cfg.mode == "alt_down_btc_overreact_long":
        return len(alt_down) >= cfg.min_alt_count and btc_ret <= -cfg.gap_pct and abs(alt_mean) <= abs(btc_ret) - cfg.gap_pct / 2, snapshot
    raise ValueError(f"unsupported mode={cfg.mode}")


def evaluate_config(
    cfg: Config,
    btc: list[Bar],
    alt_closes: dict[str, list[float | None]],
    atrs: list[float | None],
    atr_ratios: list[float | None],
    train_end: str,
    validation_end: str,
) -> dict[str, Any]:
    by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "oos": []}
    btc_closes = [bar.close for bar in btc]
    last_exit = -1
    start_index = max(120, cfg.lookback, 14)
    for index in range(start_index, len(btc) - 1):
        if index <= last_exit:
            continue
        atr = atrs[index]
        if atr is None or atr <= 0 or not atr_filter_ok(atr_ratios[index], cfg.atr_filter):
            continue
        btc_ret = pct_change(btc_closes[index], btc_closes[index - cfg.lookback])
        if btc_ret is None:
            continue
        alt_returns: list[float] = []
        for closes in alt_closes.values():
            value = pct_change(closes[index], closes[index - cfg.lookback])
            if value is not None:
                alt_returns.append(value)
        if len(alt_returns) < cfg.min_alt_count:
            continue
        ok, feature_snapshot = mode_matches(cfg, btc_ret, alt_returns)
        if not ok:
            continue
        trade = simulate_trade(btc, index + 1, cfg.side, atr, cfg.stop_atr, cfg.take_atr, cfg.max_hold_bars)
        trade["signal_ts"] = btc[index].ts
        trade["feature_snapshot"] = feature_snapshot
        by_split[split_name(trade["entry_ts"], train_end, validation_end)].append(trade)
        last_exit = int(trade["exit_index"])

    train = summarize(by_split["train"])
    validation = summarize(by_split["validation"])
    oos = summarize(by_split["oos"])
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
            "train": by_split["train"][:3],
            "validation": by_split["validation"][:3],
            "oos": by_split["oos"][:3],
        },
    }


def bounded_grid(max_configs: int) -> list[Config]:
    values = list(itertools.product(
        ["alt_up_btc_lag_long", "alt_up_btc_lag_short_fade", "alt_down_btc_lag_short", "alt_down_btc_overreact_long"],
        [3, 6, 12, 24],
        [1.0, 2.0, 3.0],
        [0.25, 0.75, 1.25],
        [0.75, 1.5, 2.5],
        [2, 3],
        ["none", "low", "mid", "high"],
        [(1.0, 1.5), (1.0, 2.0), (1.5, 3.0)],
        [6, 12, 24],
    ))
    configs = [
        Config(
            mode=mode,
            lookback=lookback,
            alt_move_pct=alt_move_pct,
            btc_mute_pct=btc_mute_pct,
            gap_pct=gap_pct,
            min_alt_count=min_alt_count,
            atr_filter=atr_filter,
            stop_atr=rr[0],
            take_atr=rr[1],
            max_hold_bars=max_hold_bars,
        )
        for mode, lookback, alt_move_pct, btc_mute_pct, gap_pct, min_alt_count, atr_filter, rr, max_hold_bars in values
    ]
    if max_configs and len(configs) > max_configs:
        step = len(configs) / max_configs
        return [configs[int(index * step)] for index in range(max_configs)]
    return configs


def sort_key(row: dict[str, Any]) -> tuple[int, int, float, float, int]:
    return (
        int(bool(row.get("oos_pass"))),
        int(bool(row.get("validation_pass"))),
        float(row.get("oos", {}).get("expectancy_r") or -999.0),
        float(row.get("validation", {}).get("expectancy_r") or -999.0),
        int(row.get("oos", {}).get("trades") or 0),
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = (ROOT / args.cache_dir).resolve() if not Path(args.cache_dir).is_absolute() else Path(args.cache_dir)
    alt_symbols = [item.strip() for item in args.alt_symbols.split(",") if item.strip()]
    btc = load_symbol(cache_dir, "BTCUSDT")
    alt_bars = {symbol: load_symbol(cache_dir, symbol) for symbol in alt_symbols}
    alt_closes = align_closes(btc, alt_bars)
    atrs = atr_values(btc)
    atr_ratios = rolling_atr_ratio(atrs)
    results = [
        evaluate_config(cfg, btc, alt_closes, atrs, atr_ratios, args.train_end, args.validation_end)
        for cfg in bounded_grid(args.max_configs)
    ]
    train_qualified = [row for row in results if row.get("train_pass")]
    validation_qualified = [row for row in results if row.get("validation_pass")]
    oos_qualified = [row for row in results if row.get("oos_pass")]
    results.sort(key=sort_key, reverse=True)
    decision = "reject_no_train_qualified_alt_breadth_candidate"
    if train_qualified and not validation_qualified:
        decision = "alt_breadth_train_only_no_promotion"
    elif validation_qualified and not oos_qualified:
        decision = "reject_validation_passed_oos_failed"
    elif oos_qualified:
        decision = "oos_pass_alt_breadth_dislocation_needs_forward_proof"
    return {
        "generated_at": now_iso(),
        "tool": "alt_breadth_dislocation_research",
        "decision": decision,
        "cache_dir": str(cache_dir),
        "symbols": {"target": "BTCUSDT", "alts": alt_symbols},
        "split": {
            "train_end": args.train_end,
            "validation_end": args.validation_end,
            "oos_start": args.validation_end,
        },
        "hypothesis": "BTC may lag or fade broad 1H alt moves when ETH/SOL/BCH move together and BTC is muted or overreacts.",
        "tested": len(results),
        "nonzero_results": sum(
            1 for row in results
            if (row.get("train", {}).get("trades") or 0) + (row.get("validation", {}).get("trades") or 0) + (row.get("oos", {}).get("trades") or 0) > 0
        ),
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
        "# Alt Breadth Dislocation Research",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        "",
        "## Boundary",
        "",
        "- Research-only.",
        "- No alerts, no paper-entry intents, no orders.",
        "- BTCUSDT 1H target; ETH/SOL/BCH 1H breadth context.",
        "",
        "## Summary",
        "",
        f"- Tested configs: `{report.get('tested')}`.",
        f"- Non-zero configs: `{report.get('nonzero_results')}`.",
        f"- Train qualified: `{report.get('train_qualified')}`.",
        f"- Validation qualified: `{report.get('validation_qualified')}`.",
        f"- OOS qualified: `{report.get('oos_qualified')}`.",
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
    parser = argparse.ArgumentParser(description="BTC 1H alt-breadth dislocation research-only holdout")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--alt-symbols", default="ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--max-configs", type=int, default=1200)
    parser.add_argument("--train-end", default="2024-01-01T00:00:00+00:00")
    parser.add_argument("--validation-end", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--out-prefix", default="docs/ALT_BREADTH_DISLOCATION_RESEARCH_2026-07-03")
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
