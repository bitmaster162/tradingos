#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.liquidity_sweep_hardening import max_drawdown, max_losing_streak, summarize_trades  # noqa: E402
from tools.strategy_mix_combo_tester import generate_signals, load_interval_data  # noqa: E402
from tools.strategy_mix_deep_validator import safe_float, safe_int, signal_config  # noqa: E402
from tools.strategy_mix_holdout_validator import ReplayConfig, result_to_config  # noqa: E402


@dataclass
class PaperPosition:
    strategy_id: str
    signal_bar_index: int
    entry_bar_index: int
    entry_ts: str
    side: str
    entry: float
    stop: float
    take: float
    atr: float
    risk: float
    max_exit_bar_index: int
    conditions: tuple[str, ...]


@dataclass(frozen=True)
class PaperTrade:
    strategy_id: str
    entry_ts: str
    exit_ts: str
    side: str
    entry: float
    exit: float
    stop: float
    take: float
    atr: float
    r_net: float
    exit_reason: str
    bars_held: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def expected_interval_seconds(interval: str) -> int:
    raw = interval.strip().lower()
    if raw.endswith("m"):
        return int(raw[:-1]) * 60
    if raw.endswith("h"):
        return int(raw[:-1]) * 3600
    if raw.endswith("d"):
        return int(raw[:-1]) * 86400
    return 0


def select_candidates(source: dict[str, Any], verdicts: set[str], top: int) -> list[dict[str, Any]]:
    rows = []
    for item in source.get("results") or source.get("top_results") or source.get("all_results") or []:
        verdict = item.get("deep_gate", {}).get("verdict") or item.get("verdict")
        if verdict in verdicts:
            rows.append(item)
    rows.sort(
        key=lambda item: (
            safe_float(item.get("holdout", {}).get("summary", {}).get("expectancy_r")),
            safe_float(item.get("full", {}).get("summary", {}).get("expectancy_r")),
        ),
        reverse=True,
    )
    return rows[: max(1, top)]


def build_position(config: ReplayConfig, bars: list[Any], signal: dict[str, Any]) -> PaperPosition | None:
    signal_index = int(signal["bar_index"])
    entry_index = signal_index + 1
    if entry_index >= len(bars):
        return None
    entry_bar = bars[entry_index]
    atr = float(signal["atr"])
    if atr <= 0:
        return None
    side = config.side.upper()
    entry = float(entry_bar.open)
    risk = atr * config.stop_atr
    if risk <= 0:
        return None
    if side == "SHORT":
        stop = entry + risk
        take = entry - atr * config.take_atr
    else:
        stop = entry - risk
        take = entry + atr * config.take_atr
    return PaperPosition(
        strategy_id=config.strategy_id,
        signal_bar_index=signal_index,
        entry_bar_index=entry_index,
        entry_ts=str(entry_bar.ts),
        side=side,
        entry=entry,
        stop=stop,
        take=take,
        atr=atr,
        risk=risk,
        max_exit_bar_index=min(len(bars) - 1, entry_index + config.max_hold_bars),
        conditions=config.conditions,
    )


def maybe_exit(position: PaperPosition, bar: Any, bar_index: int, cost_bps_per_side: float) -> PaperTrade | None:
    if bar_index < position.entry_bar_index:
        return None
    exit_price = None
    exit_reason = None
    if position.side == "SHORT":
        stop_hit = float(bar.high) >= position.stop
        take_hit = float(bar.low) <= position.take
    else:
        stop_hit = float(bar.low) <= position.stop
        take_hit = float(bar.high) >= position.take
    if stop_hit and take_hit:
        exit_price = position.stop
        exit_reason = "stop_first_same_bar"
    elif take_hit:
        exit_price = position.take
        exit_reason = "take_profit"
    elif stop_hit:
        exit_price = position.stop
        exit_reason = "stop_loss"
    elif bar_index >= position.max_exit_bar_index:
        exit_price = float(bar.close)
        exit_reason = "time_exit"
    if exit_price is None or exit_reason is None:
        return None
    if position.side == "SHORT":
        gross_r = (position.entry - exit_price) / position.risk
    else:
        gross_r = (exit_price - position.entry) / position.risk
    round_turn_cost_quote = (position.entry + exit_price) * cost_bps_per_side / 10_000.0
    cost_r = round_turn_cost_quote / position.risk
    return PaperTrade(
        strategy_id=position.strategy_id,
        entry_ts=position.entry_ts,
        exit_ts=str(bar.ts),
        side=position.side,
        entry=round(position.entry, 8),
        exit=round(float(exit_price), 8),
        stop=round(position.stop, 8),
        take=round(position.take, 8),
        atr=round(position.atr, 8),
        r_net=round(gross_r - cost_r, 6),
        exit_reason=exit_reason,
        bars_held=bar_index - position.entry_bar_index + 1,
    )


def journal_event(event_type: str, **payload: Any) -> dict[str, Any]:
    event = {"event_type": event_type, "ts_emitted": now_iso(), **payload}
    return event


def daily_key(ts: str) -> str:
    return str(ts)[:10]


def summarize_paper_trades(trades: list[PaperTrade]) -> dict[str, Any]:
    shim = [
        type(
            "TradeShim",
            (),
            {
                "r_net": trade.r_net,
            },
        )()
        for trade in trades
    ]
    values = [trade.r_net for trade in trades]
    base = summarize_trades(shim)
    base["max_drawdown_r"] = max_drawdown(values)
    base["max_losing_streak"] = max_losing_streak(values)
    return base


def replay_candidate(config: ReplayConfig, args: argparse.Namespace) -> dict[str, Any]:
    bars, features, matrix = load_interval_data(Path(args.cache_dir), config.interval, oi_lag=args.oi_lag, spot_perp_lookback=args.spot_perp_lookback)
    all_signals = generate_signals(signal_config(config), bars, features, matrix)
    signal_by_index: dict[int, list[dict[str, Any]]] = {}
    for signal in all_signals:
        signal_by_index.setdefault(int(signal["bar_index"]), []).append(signal)

    start_index = max(0, int(len(bars) * args.start_fraction))
    end_index = min(len(bars) - 1, int(len(bars) * args.end_fraction))
    base_cost = args.fee_bps + args.slippage_bps
    expected_gap = expected_interval_seconds(config.interval)
    max_gap = expected_gap * args.max_gap_multiplier if expected_gap else None

    active: PaperPosition | None = None
    trades: list[PaperTrade] = []
    journal: list[dict[str, Any]] = []
    equity_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    consecutive_losses = 0
    cooldown_until = -1
    hard_locked = False
    daily_trades: dict[str, int] = {}
    daily_r: dict[str, float] = {}
    skip_counts: dict[str, int] = {}

    for index in range(start_index, end_index + 1):
        bar = bars[index]
        if index > 0 and max_gap:
            prev_dt = parse_ts(str(bars[index - 1].ts))
            cur_dt = parse_ts(str(bar.ts))
            if prev_dt and cur_dt and (cur_dt - prev_dt).total_seconds() > max_gap:
                skip_counts["stale_gap"] = skip_counts.get("stale_gap", 0) + 1
                journal.append(
                    journal_event(
                        "data_gap_warning",
                        bar_ts=bar.ts,
                        bar_index=index,
                        gap_seconds=(cur_dt - prev_dt).total_seconds(),
                        max_gap_seconds=max_gap,
                    )
                )

        if active is not None:
            trade = maybe_exit(active, bar, index, base_cost)
            if trade is not None:
                trades.append(trade)
                equity_r = round(equity_r + trade.r_net, 6)
                peak_r = max(peak_r, equity_r)
                max_dd_r = min(max_dd_r, equity_r - peak_r)
                key = daily_key(trade.exit_ts)
                daily_r[key] = round(daily_r.get(key, 0.0) + trade.r_net, 6)
                consecutive_losses = consecutive_losses + 1 if trade.r_net <= 0 else 0
                if trade.r_net <= 0:
                    cooldown_until = max(cooldown_until, index + args.cooldown_bars_after_loss)
                journal.append(
                    journal_event(
                        "paper_exit",
                        bar_ts=bar.ts,
                        bar_index=index,
                        strategy_id=trade.strategy_id,
                        side=trade.side,
                        entry_ts=trade.entry_ts,
                        exit_ts=trade.exit_ts,
                        exit_reason=trade.exit_reason,
                        r_net=trade.r_net,
                        equity_r=equity_r,
                        drawdown_r=round(equity_r - peak_r, 6),
                        consecutive_losses=consecutive_losses,
                    )
                )
                active = None
                kill_reason = None
                if max_dd_r <= -abs(args.max_drawdown_r):
                    kill_reason = "max_drawdown_r"
                elif consecutive_losses >= args.max_consecutive_losses:
                    kill_reason = "max_consecutive_losses"
                elif daily_r.get(key, 0.0) <= -abs(args.max_daily_loss_r):
                    kill_reason = "max_daily_loss_r"
                if kill_reason:
                    hard_locked = True
                    journal.append(
                        journal_event(
                            "kill_switch",
                            bar_ts=bar.ts,
                            bar_index=index,
                            reason=kill_reason,
                            equity_r=equity_r,
                            max_drawdown_r=round(max_dd_r, 6),
                            consecutive_losses=consecutive_losses,
                            daily_r=daily_r.get(key, 0.0),
                        )
                    )

        signals = signal_by_index.get(index, [])
        for signal in signals:
            if index < start_index or index > end_index:
                continue
            journal.append(
                journal_event(
                    "signal",
                    bar_ts=bar.ts,
                    bar_index=index,
                    strategy_id=config.strategy_id,
                    side=config.side,
                    conditions=list(config.conditions),
                    atr=round(float(signal["atr"]), 8),
                    close=round(float(bar.close), 8),
                    feature_snapshot=signal.get("feature_snapshot", {}),
                )
            )
            if hard_locked:
                skip_counts["kill_switch_locked"] = skip_counts.get("kill_switch_locked", 0) + 1
                journal.append(journal_event("signal_skipped", bar_ts=bar.ts, bar_index=index, reason="kill_switch_locked", strategy_id=config.strategy_id))
                continue
            if active is not None:
                skip_counts["position_active"] = skip_counts.get("position_active", 0) + 1
                journal.append(journal_event("signal_skipped", bar_ts=bar.ts, bar_index=index, reason="position_active", strategy_id=config.strategy_id))
                continue
            if index <= cooldown_until:
                skip_counts["cooldown_after_loss"] = skip_counts.get("cooldown_after_loss", 0) + 1
                journal.append(
                    journal_event(
                        "signal_skipped",
                        bar_ts=bar.ts,
                        bar_index=index,
                        reason="cooldown_after_loss",
                        cooldown_until_bar=cooldown_until,
                        strategy_id=config.strategy_id,
                    )
                )
                continue
            key = daily_key(str(bar.ts))
            if daily_trades.get(key, 0) >= args.max_daily_trades:
                skip_counts["max_daily_trades"] = skip_counts.get("max_daily_trades", 0) + 1
                journal.append(journal_event("signal_skipped", bar_ts=bar.ts, bar_index=index, reason="max_daily_trades", strategy_id=config.strategy_id))
                continue
            position = build_position(config, bars, signal)
            if position is None or position.entry_bar_index > end_index:
                skip_counts["no_next_bar"] = skip_counts.get("no_next_bar", 0) + 1
                journal.append(journal_event("signal_skipped", bar_ts=bar.ts, bar_index=index, reason="no_next_bar", strategy_id=config.strategy_id))
                continue
            active = position
            daily_trades[key] = daily_trades.get(key, 0) + 1
            journal.append(
                journal_event(
                    "paper_entry_intent",
                    signal_bar_ts=bar.ts,
                    signal_bar_index=index,
                    entry_bar_ts=position.entry_ts,
                    entry_bar_index=position.entry_bar_index,
                    strategy_id=config.strategy_id,
                    side=position.side,
                    entry=round(position.entry, 8),
                    stop=round(position.stop, 8),
                    take=round(position.take, 8),
                    atr=round(position.atr, 8),
                    risk_model="1R=ATR*stop_atr",
                    stop_atr=config.stop_atr,
                    take_atr=config.take_atr,
                    max_hold_bars=config.max_hold_bars,
                    cost_bps_per_side=base_cost,
                )
            )

    if active is not None:
        final_bar = bars[end_index]
        trade = maybe_exit(active, final_bar, active.max_exit_bar_index, base_cost)
        if trade is not None:
            trades.append(trade)
    summary = summarize_paper_trades(trades)
    return {
        "strategy_id": config.strategy_id,
        "interval": config.interval,
        "side": config.side,
        "conditions": list(config.conditions),
        "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
        "max_hold_bars": config.max_hold_bars,
        "bars": {
            "total": len(bars),
            "start_index": start_index,
            "end_index": end_index,
            "start_ts": bars[start_index].ts,
            "end_ts": bars[end_index].ts,
        },
        "settings": {
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "cost_bps_per_side": base_cost,
            "max_daily_trades": args.max_daily_trades,
            "max_daily_loss_r": args.max_daily_loss_r,
            "max_drawdown_r": args.max_drawdown_r,
            "max_consecutive_losses": args.max_consecutive_losses,
            "cooldown_bars_after_loss": args.cooldown_bars_after_loss,
        },
        "signals_seen": sum(1 for event in journal if event["event_type"] == "signal"),
        "entry_intents": sum(1 for event in journal if event["event_type"] == "paper_entry_intent"),
        "skip_counts": skip_counts,
        "kill_switch_triggered": any(event["event_type"] == "kill_switch" for event in journal),
        "kill_switch_events": [event for event in journal if event["event_type"] == "kill_switch"],
        "summary": summary,
        "trades": [trade.__dict__ for trade in trades],
        "journal": journal,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Mix Paper Replay",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Paper-only replay over cached public BTCUSDT data.",
        "- No orders, no private credentials, no exchange connection.",
        "- This is a paper replay harness, not live/paper exchange execution.",
        "",
        "## Summary",
        "",
        f"- Source: `{report['source_report']}`.",
        f"- Tested candidates: `{report['tested']}`.",
        f"- Decision: `{report['decision']}`.",
        f"- Journal: `{report['journal_path']}`.",
        f"- Trades CSV: `{report['trades_csv']}`.",
        "",
        "## Results",
        "",
        "| Strategy | TF | Side | RR | Signals | Entries | Trades | Winrate | Exp R | Net R | Max DD | Kill Switch |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["results"]:
        summary = item["summary"]
        lines.append(
            f"| `{item['strategy_id']}` | `{item['interval']}` | `{item['side']}` | `{item['rr']}` | "
            f"`{item['signals_seen']}` | `{item['entry_intents']}` | `{summary['trades']}` | `{summary['winrate_pct']}` | "
            f"`{summary['expectancy_r']}` | `{summary['net_r_total']}` | `{summary['max_drawdown_r']}` | `{item['kill_switch_triggered']}` |"
        )
    lines.extend(
        [
            "",
        "## Operational Meaning",
        "",
        "- `signal` means the closed-bar rules matched.",
        "- `paper_entry_intent` means the harness would enter on the next bar open if this were a paper executor.",
        "- `paper_exit` records stop, take-profit or time-exit outcome.",
        "- `signal_skipped` records blocked entries from cooldown, daily limits or active position.",
        "- `kill_switch` means the replay halted new entries after a risk rule was hit.",
        "",
        "## Replay Detail",
        "",
        "- This harness processes exits before new closed-bar signals on the same bar.",
        "- That can create more entries than the stricter research no-overlap simulator.",
        "- Treat this as paper-executor behavior, not as an apples-to-apples replacement for deep validation.",
        "",
        "## Next Action",
        "",
            f"- `{report['next_action']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def write_trades_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "strategy_id",
        "entry_ts",
        "exit_ts",
        "side",
        "entry",
        "exit",
        "stop",
        "take",
        "atr",
        "r_net",
        "exit_reason",
        "bars_held",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Paper-only replay harness for locked strategy mix candidates")
    parser.add_argument("--source-report", default="docs/STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-06-29_4H_GUARDED_SHORT.json")
    parser.add_argument("--candidate-verdicts", default="paper_replay_candidate_locked")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--top", type=int, default=1)
    parser.add_argument("--start-fraction", type=float, default=0.0)
    parser.add_argument("--end-fraction", type=float, default=1.0)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--max-daily-trades", type=int, default=2)
    parser.add_argument("--max-daily-loss-r", type=float, default=3.0)
    parser.add_argument("--max-drawdown-r", type=float, default=8.0)
    parser.add_argument("--max-consecutive-losses", type=int, default=6)
    parser.add_argument("--cooldown-bars-after-loss", type=int, default=1)
    parser.add_argument("--max-gap-multiplier", type=float, default=3.0)
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_PAPER_REPLAY_2026-06-08")
    args = parser.parse_args()

    source_path = Path(args.source_report)
    source = json.loads(source_path.read_text(encoding="utf-8-sig"))
    verdicts = {item.strip() for item in args.candidate_verdicts.split(",") if item.strip()}
    candidates = select_candidates(source, verdicts, args.top)
    results = []
    all_events: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    for item in candidates:
        config = result_to_config(item)
        result = replay_candidate(config, args)
        results.append({key: value for key, value in result.items() if key not in {"journal", "trades"}})
        for event in result["journal"]:
            event["strategy_id"] = event.get("strategy_id") or result["strategy_id"]
            all_events.append(event)
        all_trades.extend(result["trades"])

    out_prefix = Path(args.out_prefix)
    journal_path = out_prefix.with_name(out_prefix.name + "_journal.jsonl")
    trades_csv = out_prefix.with_name(out_prefix.name + "_trades.csv")
    write_jsonl(journal_path, all_events)
    write_trades_csv(trades_csv, all_trades)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "paper_replay_cached_data_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "exchange_connection": False,
        },
        "source_report": str(source_path),
        "tested": len(results),
        "decision": "paper_replay_only_no_orders",
        "next_action": "build_forward_paper_feed_after_reviewing_replay_journal",
        "journal_path": str(journal_path),
        "trades_csv": str(trades_csv),
        "results": results,
        "can_trade": False,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "journal": str(journal_path),
                "trades_csv": str(trades_csv),
                "tested": len(results),
                "decision": report["decision"],
                "best": results[0] if results else None,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
