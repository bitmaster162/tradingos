from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.liquidity_sweep_hardening import Trade, summarize_trades


FUNDING_STATUS = "UNMODELED_NO_HISTORICAL_RATE_SERIES"
FUNDING_BOUNDARY_METHOD = "STANDARD_8H_UTC_BOUNDARIES_DIAGNOSTIC_ONLY"


def parse_ts(value: str) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def standard_8h_boundaries_crossed(entry_ts: str, exit_ts: str) -> int:
    """Count strict-interior 00/08/16 UTC boundaries as exposure diagnostics only."""
    start = parse_ts(entry_ts)
    end = parse_ts(exit_ts)
    if end < start:
        raise ValueError("exit precedes entry")
    if end == start:
        return 0
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    boundary = day
    count = 0
    while boundary < end:
        if boundary > start and boundary < end and boundary.hour in (0, 8, 16):
            count += 1
        boundary += timedelta(hours=8)
    return count


def simulate_trade_gap_aware(
    *,
    dataset_id: str,
    strategy_id: str,
    bars: list[Any],
    signal: dict[str, Any],
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
) -> tuple[Trade | None, dict[str, Any]]:
    """Replay with explicit bar-open gap handling.

    Stop gaps are filled at the worse bar open. Favorable target gaps are
    conservatively capped at the target price. Intrabar ambiguity remains
    stop-first. This is still OHLC replay, not an observed execution.
    """
    signal_index = int(signal["bar_index"])
    entry_index = signal_index + 1
    if entry_index >= len(bars):
        return None, {"status": "NO_ENTRY_BAR"}

    entry_bar = bars[entry_index]
    entry = float(entry_bar.open)
    atr = float(signal["atr"])
    if atr <= 0:
        return None, {"status": "INVALID_ATR"}
    side = str(signal["side_hint"]).upper()
    risk = atr * float(stop_atr)
    if risk <= 0:
        return None, {"status": "INVALID_RISK"}

    if side == "SHORT":
        stop = entry + risk
        take = entry - atr * float(take_atr)
    elif side == "LONG":
        stop = entry - risk
        take = entry + atr * float(take_atr)
    else:
        return None, {"status": "INVALID_SIDE"}

    last_index = min(len(bars) - 1, entry_index + int(max_hold_bars))
    exit_price = float(bars[last_index].close)
    exit_reason = "time_exit"
    exit_index = last_index
    stop_gap = False
    favorable_take_gap = False
    same_bar_ambiguous = False

    for index in range(entry_index, last_index + 1):
        bar = bars[index]
        bar_open = float(bar.open)

        if index > entry_index:
            if side == "LONG" and bar_open <= stop:
                exit_price = bar_open
                exit_reason = "stop_gap_open"
                exit_index = index
                stop_gap = True
                break
            if side == "SHORT" and bar_open >= stop:
                exit_price = bar_open
                exit_reason = "stop_gap_open"
                exit_index = index
                stop_gap = True
                break
            if side == "LONG" and bar_open >= take:
                exit_price = take
                exit_reason = "take_gap_capped_at_target"
                exit_index = index
                favorable_take_gap = True
                break
            if side == "SHORT" and bar_open <= take:
                exit_price = take
                exit_reason = "take_gap_capped_at_target"
                exit_index = index
                favorable_take_gap = True
                break

        if side == "SHORT":
            stop_hit = float(bar.high) >= stop
            take_hit = float(bar.low) <= take
        else:
            stop_hit = float(bar.low) <= stop
            take_hit = float(bar.high) >= take

        if stop_hit and take_hit:
            exit_price = stop
            exit_reason = "stop_first_same_bar"
            exit_index = index
            same_bar_ambiguous = True
            break
        if take_hit:
            exit_price = take
            exit_reason = "take_profit"
            exit_index = index
            break
        if stop_hit:
            exit_price = stop
            exit_reason = "stop_loss"
            exit_index = index
            break

    gross_r = (
        (entry - exit_price) / risk
        if side == "SHORT"
        else (exit_price - entry) / risk
    )
    round_turn_cost_quote = (
        (entry + exit_price) * float(cost_bps_per_side) / 10_000.0
    )
    cost_r = round_turn_cost_quote / risk
    r_net = gross_r - cost_r
    trade = Trade(
        dataset_id=dataset_id,
        strategy_id=strategy_id,
        entry_ts=str(entry_bar.ts),
        exit_ts=str(bars[exit_index].ts),
        side=side,
        entry=round(entry, 8),
        exit=round(exit_price, 8),
        stop=round(stop, 8),
        take=round(take, 8),
        atr=round(atr, 8),
        r_net=round(r_net, 6),
        exit_reason=exit_reason,
        bars_held=exit_index - entry_index + 1,
    )
    funding_boundaries = standard_8h_boundaries_crossed(
        trade.entry_ts,
        trade.exit_ts,
    )
    return trade, {
        "status": "MODELED",
        "stop_gap_open": stop_gap,
        "favorable_take_gap": favorable_take_gap,
        "same_bar_ambiguous": same_bar_ambiguous,
        "standard_8h_funding_boundaries_crossed": funding_boundaries,
        "funding_status": FUNDING_STATUS,
        "funding_boundary_method": FUNDING_BOUNDARY_METHOD,
    }


def replay_gap_aware(
    *,
    dataset_id: str,
    strategy_id: str,
    bars: list[Any],
    signals: list[dict[str, Any]],
    stop_atr: float,
    take_atr: float,
    max_hold_bars: int,
    cost_bps_per_side: float,
    no_overlap: bool,
) -> dict[str, Any]:
    trades: list[Trade] = []
    diagnostics: list[dict[str, Any]] = []
    last_exit_bar = -1
    index_by_ts = {str(bar.ts): index for index, bar in enumerate(bars)}

    for signal in sorted(signals, key=lambda row: int(row["bar_index"])):
        signal_index = int(signal["bar_index"])
        if no_overlap and signal_index <= last_exit_bar:
            continue
        trade, diagnostic = simulate_trade_gap_aware(
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
        diagnostics.append(
            {
                "entry_ts": trade.entry_ts,
                "exit_ts": trade.exit_ts,
                "exit_reason": trade.exit_reason,
                **diagnostic,
            }
        )
        if no_overlap:
            last_exit_bar = max(
                last_exit_bar,
                index_by_ts.get(trade.exit_ts, signal_index),
            )

    funding_crossings = sum(
        int(row["standard_8h_funding_boundaries_crossed"])
        for row in diagnostics
    )
    trades_crossing_funding = sum(
        int(row["standard_8h_funding_boundaries_crossed"] > 0)
        for row in diagnostics
    )
    return {
        "schema": "tradingos.execution_realism_shadow.v1",
        "summary": summarize_trades(trades),
        "trade_count": len(trades),
        "stop_gap_open_count": sum(bool(row["stop_gap_open"]) for row in diagnostics),
        "favorable_take_gap_count": sum(
            bool(row["favorable_take_gap"]) for row in diagnostics
        ),
        "same_bar_ambiguous_count": sum(
            bool(row["same_bar_ambiguous"]) for row in diagnostics
        ),
        "standard_8h_funding_boundaries_crossed": funding_crossings,
        "trades_crossing_standard_8h_funding_boundary": trades_crossing_funding,
        "funding_status": FUNDING_STATUS,
        "funding_boundary_method": FUNDING_BOUNDARY_METHOD,
        "model_scope": "OHLC_REPLAY_DIAGNOSTIC_NOT_OBSERVED_EXECUTION",
        "posthoc_use": "DOWNGRADE_ONLY_NEVER_PROMOTION",
        "trades": [asdict(trade) for trade in trades],
        "diagnostics": diagnostics,
    }
