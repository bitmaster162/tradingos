#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.event_feature_factory import (  # noqa: E402
    FeatureConfig,
    build_features,
    generate_signals as generate_event_signals,
    load_csv_by_time,
)
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402
from tools.liquidity_sweep_hardening import fold_summaries, simulate_trade, summarize_trades  # noqa: E402
from tools.strategy_polygon_parallel import (  # noqa: E402
    StrategyConfig,
    generate_signals as generate_polygon_signals,
    rsi,
)


_CACHE_LOCK = Lock()
_BARS_CACHE: dict[str, list[Any]] = {}
_CSV_BY_TIME_CACHE: dict[str, dict[str, dict[str, str]]] = {}
_EVENT_FEATURES_CACHE: dict[tuple[str, str, int, int], tuple[list[Any], dict[str, list[Any]]]] = {}
_POLYGON_INPUTS_CACHE: dict[tuple[str, str], tuple[list[Any], list[float | None], list[float | None]]] = {}


@dataclass(frozen=True)
class RuleTest:
    rule_id: str
    source_rel: str
    source_score: int
    rule_score: int
    card_family: str
    trade_side: str
    engine: str
    config: FeatureConfig | StrategyConfig


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


def safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return cleaned.strip("_")[:90] or "rule"


def side_filter(value: str) -> str | None:
    side = str(value or "").strip().lower()
    if side == "long":
        return "LONG"
    if side == "short":
        return "SHORT"
    return None


def load_rule_cards(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cards = payload.get("rule_cards")
    if not isinstance(cards, list):
        raise ValueError(f"no rule_cards list in {path}")
    return [card for card in cards if card.get("codable_status") == "codable_now_existing_data"]


def cached_ohlcv(path: Path) -> list[Any]:
    key = str(path.resolve())
    with _CACHE_LOCK:
        cached = _BARS_CACHE.get(key)
    if cached is not None:
        return cached
    loaded = load_ohlcv(path)
    with _CACHE_LOCK:
        _BARS_CACHE[key] = loaded
    return loaded


def cached_csv_by_time(path: Path) -> dict[str, dict[str, str]]:
    key = str(path.resolve())
    with _CACHE_LOCK:
        cached = _CSV_BY_TIME_CACHE.get(key)
    if cached is not None:
        return cached
    loaded = load_csv_by_time(path)
    with _CACHE_LOCK:
        _CSV_BY_TIME_CACHE[key] = loaded
    return loaded


def cached_event_inputs(cache_dir: Path, interval: str, oi_lag: int, spot_perp_lookback: int) -> tuple[list[Any], dict[str, list[Any]]]:
    key = (str(cache_dir.resolve()), interval, oi_lag, spot_perp_lookback)
    with _CACHE_LOCK:
        cached = _EVENT_FEATURES_CACHE.get(key)
    if cached is not None:
        return cached
    futures_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
    spot_path = cache_dir / "spot" / "BTCUSDT" / f"{interval}_klines.csv"
    derivatives_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_oi_aligned.csv"
    bars = cached_ohlcv(futures_path)
    spot_bars = cached_ohlcv(spot_path) if spot_path.exists() else []
    features = build_features(
        bars=bars,
        spot_by_time={bar.ts: bar for bar in spot_bars},
        derivatives_by_time=cached_csv_by_time(derivatives_path),
        oi_lag=oi_lag,
        spot_perp_lookback=spot_perp_lookback,
        volume_window=20,
        atr_window=20,
    )
    loaded = (bars, features)
    with _CACHE_LOCK:
        _EVENT_FEATURES_CACHE[key] = loaded
    return loaded


def cached_polygon_inputs(cache_dir: Path, interval: str) -> tuple[list[Any], list[float | None], list[float | None]]:
    key = (str(cache_dir.resolve()), interval)
    with _CACHE_LOCK:
        cached = _POLYGON_INPUTS_CACHE.get(key)
    if cached is not None:
        return cached
    futures_path = cache_dir / "futures" / "BTCUSDT" / f"{interval}_klines.csv"
    bars = cached_ohlcv(futures_path)
    atr = compute_atr(bars, 14)
    rsi14 = rsi([bar.close for bar in bars], 14)
    loaded = (bars, atr, rsi14)
    with _CACHE_LOCK:
        _POLYGON_INPUTS_CACHE[key] = loaded
    return loaded


def event_config(strategy_id: str, family: str, interval: str, params: dict[str, Any]) -> FeatureConfig:
    return FeatureConfig(strategy_id=strategy_id, family=family, interval=interval, params=params)


def polygon_config(strategy_id: str, family: str, interval: str, params: dict[str, Any]) -> StrategyConfig:
    return StrategyConfig(strategy_id=strategy_id, family=family, interval=interval, params=params)


def build_rule_tests(cards: list[dict[str, Any]]) -> list[RuleTest]:
    tests: list[RuleTest] = []
    for card in cards:
        rule_id = str(card.get("rule_id") or "unknown")
        base = f"doc_{safe_id(rule_id)}"
        family = str(card.get("family") or "unknown")
        source_rel = str(card.get("source_rel") or "")
        source_score = int(card.get("source_score") or 0)
        rule_score = int(card.get("rule_score") or 0)
        trade_side = str(card.get("trade_side") or "unknown")

        def add(engine: str, config: FeatureConfig | StrategyConfig) -> None:
            tests.append(
                RuleTest(
                    rule_id=rule_id,
                    source_rel=source_rel,
                    source_score=source_score,
                    rule_score=rule_score,
                    card_family=family,
                    trade_side=trade_side,
                    engine=engine,
                    config=config,
                )
            )

        if family == "breakout_continuation":
            add(
                "event_feature",
                event_config(
                    f"{base}_compression_accept_1h",
                    "compression_acceptance_breakout",
                    "1h",
                    {"lookback": 20, "max_atr_ratio": 0.85, "min_volume_z": 0.25, "min_body_pct": 0.35},
                ),
            )
            add(
                "event_feature",
                event_config(
                    f"{base}_spot_confirm_1h",
                    "spot_confirmed_breakout",
                    "1h",
                    {"lookback": 20, "min_body_pct": 0.30, "min_spot_div_abs": 0.03},
                ),
            )
        elif family == "derivatives_event":
            add(
                "event_feature",
                event_config(
                    f"{base}_oi_compression_1h",
                    "oi_compression_breakout",
                    "1h",
                    {"lookback": 20, "max_atr_ratio": 0.9, "min_oi_delta_pct": 0.05, "min_body_pct": 0.30},
                ),
            )
            add(
                "event_feature",
                event_config(
                    f"{base}_false_reclaim_1h",
                    "false_breakout_reclaim",
                    "1h",
                    {"lookback": 20, "min_volume_z": -0.25, "min_wick_atr": 0.10},
                ),
            )
            add(
                "event_feature",
                event_config(
                    f"{base}_climax_reclaim_1h",
                    "climax_reclaim",
                    "1h",
                    {"lookback": 20, "min_volume_z": 0.50, "min_range_atr": 1.20, "min_wick_atr": 0.10},
                ),
            )
        elif family == "liquidity_sweep_reclaim":
            add(
                "event_feature",
                event_config(
                    f"{base}_sweep_reclaim_15m",
                    "false_breakout_reclaim",
                    "15m",
                    {"lookback": 20, "min_volume_z": -0.25, "min_wick_atr": 0.10},
                ),
            )
            add(
                "polygon",
                polygon_config(
                    f"{base}_sweep_reversal_1h",
                    "sweep_reversal",
                    "1h",
                    {"lookback": 20, "min_wick_atr": 0.15},
                ),
            )
        elif family == "range_mean_reversion":
            add(
                "polygon",
                polygon_config(
                    f"{base}_range_fade_1h",
                    "range_fade",
                    "1h",
                    {"lookback": 80, "low_rsi": 30, "high_rsi": 70, "entry_atr": 0.35, "min_width_atr": 2.0},
                ),
            )
            add(
                "polygon",
                polygon_config(
                    f"{base}_bb_fade_1h",
                    "bb_fade",
                    "1h",
                    {"window": 40, "z": 2.0, "low_rsi": 32, "high_rsi": 68},
                ),
            )
    return tests


def simulate_signals(
    *,
    dataset_id: str,
    strategy_id: str,
    bars: list[Any],
    signals: list[dict[str, Any]],
    side: str | None,
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
    no_overlap: bool,
) -> tuple[list[Any], int]:
    trades = []
    skipped_side = 0
    last_exit_bar = -1
    for signal in sorted(signals, key=lambda item: item["bar_index"]):
        if side is not None and str(signal.get("side_hint") or "").upper() != side:
            skipped_side += 1
            continue
        if no_overlap and int(signal["bar_index"]) <= last_exit_bar:
            continue
        trade = simulate_trade(
            dataset_id=dataset_id,
            strategy_id=strategy_id,
            bars=bars,
            signal=signal,
            stop_atr=stop_atr,
            take_atr=take_atr,
            max_hold_bars=max_hold_bars,
            cost_bps_per_side=cost_bps_per_side,
        )
        if trade is None:
            continue
        trades.append(trade)
        if no_overlap:
            for index in range(int(signal["bar_index"]) + 1, min(len(bars), int(signal["bar_index"]) + max_hold_bars + 2)):
                if bars[index].ts == trade.exit_ts:
                    last_exit_bar = index
                    break
    return trades, skipped_side


def evaluate_event_test(test: RuleTest, args: argparse.Namespace) -> dict[str, Any]:
    config = test.config
    assert isinstance(config, FeatureConfig)
    cache_dir = resolve_path(args.cache_dir)
    bars, features = cached_event_inputs(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
    signals = generate_event_signals(config, bars, features)
    trades, skipped_side = simulate_signals(
        dataset_id=f"doc_rule_event_BTCUSDT_{config.interval}",
        strategy_id=config.strategy_id,
        bars=bars,
        signals=signals,
        side=side_filter(test.trade_side),
        stop_atr=args.stop_atr,
        take_atr=args.take_atr,
        max_hold_bars=args.max_hold_bars,
        cost_bps_per_side=args.fee_bps + args.slippage_bps,
        no_overlap=not args.allow_overlap,
    )
    return finalize_result(test, config, signals, trades, skipped_side, args)


def evaluate_polygon_test(test: RuleTest, args: argparse.Namespace) -> dict[str, Any]:
    config = test.config
    assert isinstance(config, StrategyConfig)
    bars, atr, rsi14 = cached_polygon_inputs(resolve_path(args.cache_dir), config.interval)
    signals = generate_polygon_signals(config, bars, atr, rsi14)
    trades, skipped_side = simulate_signals(
        dataset_id=f"doc_rule_polygon_BTCUSDT_{config.interval}",
        strategy_id=config.strategy_id,
        bars=bars,
        signals=signals,
        side=side_filter(test.trade_side),
        stop_atr=args.stop_atr,
        take_atr=args.take_atr,
        max_hold_bars=args.max_hold_bars,
        cost_bps_per_side=args.fee_bps + args.slippage_bps,
        no_overlap=not args.allow_overlap,
    )
    return finalize_result(test, config, signals, trades, skipped_side, args)


def gate(summary: dict[str, Any], folds: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    trades = int(summary.get("trades") or 0)
    winrate = float(summary.get("winrate_pct") or 0.0)
    expectancy = float(summary.get("expectancy_r") or -999.0)
    drawdown = float(summary.get("max_drawdown_r") or 0.0)
    stable_folds = sum(1 for item in folds if item.get("stable"))
    pass_gate = (
        trades >= args.min_trades
        and winrate >= args.min_winrate_pct
        and expectancy >= args.min_expectancy_r
        and stable_folds >= args.min_stable_folds
        and drawdown >= -abs(args.max_drawdown_r)
    )
    watchlist = (
        not pass_gate
        and trades >= max(30, args.min_trades // 2)
        and expectancy > 0.0
        and stable_folds >= max(2, args.min_stable_folds - 1)
        and drawdown >= -abs(args.max_drawdown_r)
    )
    if pass_gate:
        verdict = "candidate_needs_oos"
    elif watchlist:
        verdict = "watchlist_only"
    elif trades < args.min_trades:
        verdict = "blocked_insufficient_sample"
    elif expectancy <= 0:
        verdict = "blocked_negative_expectancy"
    else:
        verdict = "research_only"
    return {
        "pass": pass_gate,
        "watchlist": watchlist,
        "verdict": verdict,
        "stable_folds": stable_folds,
        "requirements": {
            "min_trades": args.min_trades,
            "min_winrate_pct": args.min_winrate_pct,
            "min_expectancy_r": args.min_expectancy_r,
            "min_stable_folds": args.min_stable_folds,
            "max_drawdown_r": -abs(args.max_drawdown_r),
        },
    }


def finalize_result(
    test: RuleTest,
    config: FeatureConfig | StrategyConfig,
    signals: list[dict[str, Any]],
    trades: list[Any],
    skipped_side: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    summary = summarize_trades(trades)
    folds = fold_summaries(trades, args.folds)
    research_gate = gate(summary, folds, args)
    return {
        "rule_id": test.rule_id,
        "source_rel": test.source_rel,
        "source_score": test.source_score,
        "rule_score": test.rule_score,
        "card_family": test.card_family,
        "trade_side": test.trade_side,
        "engine": test.engine,
        "strategy_id": config.strategy_id,
        "strategy_family": config.family,
        "interval": config.interval,
        "params": config.params,
        "signals": len(signals),
        "skipped_by_side_filter": skipped_side,
        "summary": summary,
        "folds": folds,
        "research_gate": research_gate,
        "sample_trades": [trade.__dict__ for trade in trades[:8]],
    }


def evaluate_test(test: RuleTest, args: argparse.Namespace) -> dict[str, Any]:
    if test.engine == "event_feature":
        return evaluate_event_test(test, args)
    if test.engine == "polygon":
        return evaluate_polygon_test(test, args)
    raise ValueError(f"unsupported engine: {test.engine}")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Document Rule Card Batch Tester",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research-only deterministic batch from extracted rule cards.",
        "- No private keys, no orders, no live/paper permission.",
        "- A passing result only means `candidate_needs_oos`; it still needs separate OOS/forward validation.",
        "- This batch avoids broad parameter grids; each rule card maps to a small fixed test template.",
        "",
        "## Summary",
        "",
        f"- Codable cards: `{report['codable_cards']}`",
        f"- Planned tests: `{report['planned_tests']}`",
        f"- Completed tests: `{report['completed_tests']}`",
        f"- Pass count: `{report['pass_count']}`",
        f"- Watchlist count: `{report['watchlist_count']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Decision: `{report['decision']}`",
        "",
        "## By Family",
        "",
    ]
    for family, count in sorted(report["by_card_family"].items()):
        lines.append(f"- `{family}`: `{count}`")
    lines.extend(["", "## Top Results", "", "| Rule | Engine | Strategy | Trades | Winrate | Exp R | Stable | Gate |", "|---|---|---|---:|---:|---:|---:|---|"])
    for item in report["top_results"][:20]:
        summary = item["summary"]
        gate_info = item["research_gate"]
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                item["rule_id"],
                item["engine"],
                item["strategy_id"],
                summary.get("trades"),
                summary.get("winrate_pct"),
                summary.get("expectancy_r"),
                gate_info.get("stable_folds"),
                gate_info.get("verdict"),
            )
        )
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic tester for extracted document rule cards")
    parser.add_argument("--rule-cards", default="docs/TARGETED_STRATEGY_RULE_EXTRACTOR_PARALLEL_SEARCH_2026-06-30.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=3.0)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--min-trades", type=int, default=100)
    parser.add_argument("--min-winrate-pct", type=float, default=42.0)
    parser.add_argument("--min-expectancy-r", type=float, default=0.10)
    parser.add_argument("--min-stable-folds", type=int, default=3)
    parser.add_argument("--max-drawdown-r", type=float, default=25.0)
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--out-prefix", default="docs/DOCUMENT_RULE_CARD_BATCH_TEST_2026-06-30")
    args = parser.parse_args()

    cards = load_rule_cards(resolve_path(args.rule_cards))
    tests = build_rule_tests(cards)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(tests) or 1))) as executor:
        futures = {executor.submit(evaluate_test, test, args): test for test in tests}
        for future in as_completed(futures):
            test = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "rule_id": test.rule_id,
                        "source_rel": test.source_rel,
                        "source_score": test.source_score,
                        "rule_score": test.rule_score,
                        "card_family": test.card_family,
                        "trade_side": test.trade_side,
                        "engine": test.engine,
                        "strategy_id": test.config.strategy_id,
                        "strategy_family": test.config.family,
                        "interval": test.config.interval,
                        "params": test.config.params,
                        "signals": 0,
                        "skipped_by_side_filter": 0,
                        "summary": summarize_trades([]),
                        "folds": [],
                        "research_gate": {
                            "pass": False,
                            "watchlist": False,
                            "verdict": "error",
                            "stable_folds": 0,
                            "requirements": {},
                        },
                        "sample_trades": [],
                    }
                )
                errors.append({"rule_id": test.rule_id, "strategy_id": test.config.strategy_id, "error": str(exc)})

    ranked = sorted(
        results,
        key=lambda item: (
            1 if item["research_gate"].get("pass") else 0,
            1 if item["research_gate"].get("watchlist") else 0,
            float(item["summary"].get("expectancy_r") or -999.0),
            int(item["research_gate"].get("stable_folds") or 0),
            int(item["summary"].get("trades") or 0),
        ),
        reverse=True,
    )
    pass_count = sum(1 for item in ranked if item["research_gate"].get("pass"))
    watchlist_count = sum(1 for item in ranked if item["research_gate"].get("watchlist"))
    by_family: dict[str, int] = {}
    for card in cards:
        family = str(card.get("family") or "unknown")
        by_family[family] = by_family.get(family, 0) + 1
    decision = (
        "rule_card_batch_has_oos_candidates"
        if pass_count
        else "rule_card_batch_no_promotable_candidate"
    )
    next_action = (
        "run a sealed OOS/forward validation only for pass candidates; no trade permission"
        if pass_count
        else "reject this deterministic batch for promotion; add new event definitions or better data before more tuning"
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_card_batch_tester.py",
        "runtime_boundary": {
            "classification": "research_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "risk_reward_mode": f"1:{args.take_atr / args.stop_atr:g}",
        },
        "rule_cards_source": portable(resolve_path(args.rule_cards)),
        "cache_dir": portable(resolve_path(args.cache_dir)),
        "settings": {
            "stop_atr": args.stop_atr,
            "take_atr": args.take_atr,
            "max_hold_bars": args.max_hold_bars,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "folds": args.folds,
            "min_trades": args.min_trades,
            "min_winrate_pct": args.min_winrate_pct,
            "min_expectancy_r": args.min_expectancy_r,
            "min_stable_folds": args.min_stable_folds,
            "max_drawdown_r": args.max_drawdown_r,
        },
        "codable_cards": len(cards),
        "planned_tests": len(tests),
        "completed_tests": len(results),
        "pass_count": pass_count,
        "watchlist_count": watchlist_count,
        "error_count": len(errors),
        "by_card_family": by_family,
        "errors": errors,
        "top_results": ranked[:30],
        "all_results": ranked,
        "decision": decision,
        "next_action": next_action,
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
                "codable_cards": len(cards),
                "planned_tests": len(tests),
                "completed_tests": len(results),
                "pass_count": pass_count,
                "watchlist_count": watchlist_count,
                "decision": decision,
                "json": portable(json_path),
                "md": portable(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
