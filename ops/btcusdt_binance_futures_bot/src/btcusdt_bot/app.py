from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from btcusdt_bot.authoritative.backfill import (
    AuthoritativeHistoryBackfillConfig,
    AuthoritativeHistoryBackfiller,
)
from btcusdt_bot.backtest.engine import BreakoutBacktestConfig, BreakoutBacktester, ParityBreakoutBacktester
from btcusdt_bot.backtest.execution_quality import build_execution_quality_report
from btcusdt_bot.backtest.reader import iter_mark_price_ticks, iter_market_events
from btcusdt_bot.backtest.readiness import build_backtest_readiness_report
from btcusdt_bot.backtest.walkforward import (
    BreakoutParameterCandidate,
    WalkForwardScoreConfig,
    build_breakout_parameter_grid,
    build_walkforward_folds,
    discover_available_market_dates,
    run_walkforward,
)
from btcusdt_bot.bootstrap.reconcile import BootstrapSynchronizer
from btcusdt_bot.collector.book_ticker import BookTickerCollector
from btcusdt_bot.collector.depth_book import DepthBookCollector, RPIDepthBookCollector
from btcusdt_bot.collector.crowding import CrowdingCollector
from btcusdt_bot.collector.market import MarketCollector
from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceAPIError, BinanceRESTClient
from btcusdt_bot.execution_drift_daemon import ExecutionDriftDaemon, ExecutionDriftDaemonConfig
from btcusdt_bot.intraday_protection_daemon import IntradayProtectionDaemon, IntradayProtectionDaemonConfig
from btcusdt_bot.pnl_protection_daemon import PnLProtectionDaemon, PnLProtectionDaemonConfig
from btcusdt_bot.combined_protection_daemon import CombinedProtectionDaemon, CombinedProtectionDaemonConfig
from btcusdt_bot.trade_reconciliation_daemon import TradeReconciliationDaemon, TradeReconciliationDaemonConfig
from btcusdt_bot.session_truth_daemon import SessionTruthDaemon, SessionTruthDaemonConfig
from btcusdt_bot.session_truth_trend_daemon import SessionTruthTrendDaemon, SessionTruthTrendDaemonConfig
from btcusdt_bot.economics_regime_daemon import EconomicsRegimeDaemon, EconomicsRegimeDaemonConfig
from btcusdt_bot.connectors.ws_urls import build_combined_stream_url, build_private_url
from btcusdt_bot.domain.enums import OrderType, PositionSide, Side, TimeInForce
from btcusdt_bot.domain.models import AlgoOrderProposal, OrderProposal
from btcusdt_bot.execution.gateway import ExecutionGateway
from btcusdt_bot.heartbeat_daemon import CountdownHeartbeatDaemon
from btcusdt_bot.live_breakout import LiveBreakoutConfig, LiveBreakoutRunner
from btcusdt_bot.live_execution_quality import build_live_execution_quality_report
from btcusdt_bot.monitoring.execution_drift import ExecutionDriftThresholds
from btcusdt_bot.monitoring.intraday_protection import IntradayProtectionThresholds
from btcusdt_bot.monitoring.pnl_protection import PnLProtectionThresholds
from btcusdt_bot.monitoring.post_fill_markout import (
    BOOK_MID,
    MARK_PRICE,
    PostFillMarkoutConfig,
    analyze_post_fill_markout,
)
from btcusdt_bot.monitoring.post_fill_forward import build_post_fill_forward_report
from btcusdt_bot.monitoring.combined_protection import CombinedProtectionThresholds
from btcusdt_bot.monitoring.trade_reconciliation import TradeReconciliationThresholds
from btcusdt_bot.monitoring.session_truth import SessionTruthThresholds
from btcusdt_bot.monitoring.session_truth_trend import SessionTruthTrendThresholds
from btcusdt_bot.monitoring.economics_regime import EconomicsRegimeThresholds
from btcusdt_bot.reconcile_daemon import ReconcileDaemon
from btcusdt_bot.execution.planner import ExecutionPlanner
from btcusdt_bot.execution.validator import ExecutionValidator, extract_symbol_filters
from btcusdt_bot.private.consumer import PrivateStreamConsumer
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.reporting.aggregate import aggregate_daily_reports
from btcusdt_bot.storage.jsonl import JSONLWriter
from btcusdt_bot.utils.envfile import load_env_file
from btcusdt_bot.utils.serde import to_jsonable


def _json_dump(value: object) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, indent=2)


def _parse_int_grid(raw: str) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        value = int(token)
        if value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _parse_decimal_grid(raw: str) -> list[Decimal]:
    values: list[Decimal] = []
    seen: set[str] = set()
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        value = Decimal(token)
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def _parse_optional_decimal_grid(raw: str) -> list[Decimal | None]:
    values: list[Decimal | None] = []
    seen: set[str] = set()
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        lowered = token.lower()
        if lowered in {"none", "null", "na"}:
            key = "none"
            value: Decimal | None = None
        else:
            value = Decimal(token)
            key = str(value)
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def _parse_choice_grid(raw: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for token in (item.strip().lower() for item in raw.split(",")):
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        values.append(token)
    return values


def _setup_logging(config: BotConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _build_client(config: BotConfig) -> BinanceRESTClient:
    return BinanceRESTClient(
        base_url=config.rest_base_url,
        api_key=config.api_key,
        api_secret=config.api_secret,
        recv_window_ms=config.recv_window_ms,
        timeout_s=config.timeout_s,
    )


def _build_validator(client: BinanceRESTClient, symbol: str) -> ExecutionValidator:
    exchange_info = client.exchange_info().data
    filters = extract_symbol_filters(exchange_info, symbol)
    return ExecutionValidator(filters)


def _utc_day_start_ms(date_value: str) -> int:
    parsed = datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _resolve_backfill_window_ms(
    *,
    start_date: str | None,
    end_date: str | None,
    start_ms: int | None,
    end_ms: int | None,
    days: int | None,
) -> tuple[int, int]:
    if start_ms is not None or end_ms is not None:
        if start_ms is None or end_ms is None:
            raise ValueError("start_ms_and_end_ms_must_be_provided_together")
        if int(end_ms) < int(start_ms):
            raise ValueError("end_ms_before_start_ms")
        return int(start_ms), int(end_ms)

    if start_date is not None or end_date is not None:
        if start_date is None or end_date is None:
            raise ValueError("start_date_and_end_date_must_be_provided_together")
        start_time_ms = _utc_day_start_ms(start_date)
        end_time_ms = _utc_day_start_ms(end_date) + (24 * 60 * 60 * 1000) - 1
        if end_time_ms < start_time_ms:
            raise ValueError("end_date_before_start_date")
        return start_time_ms, end_time_ms

    days = max(1, int(days or 1))
    now = datetime.now(tz=UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_time_ms = int((today_start.timestamp() * 1000) + (24 * 60 * 60 * 1000) - 1)
    start_time_ms = int((today_start.timestamp() * 1000) - ((days - 1) * 24 * 60 * 60 * 1000))
    return start_time_ms, end_time_ms


def cmd_snapshot(config: BotConfig) -> int:
    client = _build_client(config)

    try:
        exchange_info = client.exchange_info().data
        filters = extract_symbol_filters(exchange_info, config.symbol)
    except Exception as exc:  # noqa: BLE001
        print(f"snapshot failed: {exc}")
        return 1

    snapshot: dict[str, object] = {
        "env": config.env,
        "symbol": config.symbol,
        "rest_base_url": config.rest_base_url,
        "ws_public_example": build_combined_stream_url(
            config.ws_public_base_url,
            [f"{config.symbol.lower()}@bookTicker"],
        ),
        "ws_market_example": build_combined_stream_url(
            config.ws_market_base_url,
            [f"{config.symbol.lower()}@aggTrade", f"{config.symbol.lower()}@markPrice@1s"],
        ),
        "kline_intervals": list(config.kline_intervals),
        "private_events": list(config.private_events),
        "symbol_filters": {
            "tick_size": filters.tick_size,
            "step_size": filters.step_size,
            "market_step_size": filters.market_step_size,
            "min_qty": filters.min_qty,
            "market_min_qty": filters.market_min_qty,
            "min_notional": filters.min_notional,
            "percent_price_up": filters.percent_price_up,
            "percent_price_down": filters.percent_price_down,
            "trigger_protect": filters.trigger_protect,
            "market_take_bound": filters.market_take_bound,
            "max_num_orders": filters.max_num_orders,
        },
    }

    if config.has_api_credentials:
        try:
            listen_key = client.start_user_stream().data["listenKey"]
            snapshot["ws_private_example"] = build_private_url(
                config.ws_private_base_url,
                listen_key,
                list(config.private_events),
            )
            snapshot["symbol_config"] = client.symbol_config(config.symbol).data
            snapshot["account_v3"] = client.account_v3().data
            snapshot["position_risk_v3"] = client.position_risk_v3(config.symbol).data
            snapshot["leverage_brackets"] = client.leverage_brackets(config.symbol).data
            snapshot["api_trading_status"] = client.api_trading_status(config.symbol).data
            snapshot["commission_rate"] = client.commission_rate(config.symbol).data
        except BinanceAPIError as exc:
            snapshot["signed_snapshot_error"] = {
                "status": exc.status,
                "code": exc.code,
                "message": exc.message,
            }
        except Exception as exc:  # noqa: BLE001
            snapshot["signed_snapshot_error"] = {"message": str(exc)}
    else:
        snapshot["note"] = "Set API credentials in .env to fetch USER_DATA endpoints."

    print(_json_dump(snapshot))
    return 0


def cmd_plan_example(config: BotConfig, side: str, mark_price: Decimal, qty: Decimal, atr: Decimal) -> int:
    planner = ExecutionPlanner()
    entry = planner.entry_order(
        symbol=config.symbol,
        side=Side(side.upper()),
        qty=qty,
        mark_price=mark_price,
    )
    stop_algo, tp_algo = planner.bracket_exits(
        symbol=config.symbol,
        entry_side=entry.side,
        qty=qty,
        entry_price=entry.price or mark_price,
        atr=atr,
    )
    print(_json_dump({"entry": entry, "stop_algo": stop_algo, "take_profit_algo": tp_algo}))
    return 0


def cmd_validate_example(config: BotConfig, price: Decimal, qty: Decimal, mark_price: Decimal) -> int:
    client = _build_client(config)
    exchange_info = client.exchange_info().data
    filters = extract_symbol_filters(exchange_info, config.symbol)
    validator = ExecutionValidator(filters)
    result = validator.validate_limit(price=price, qty=qty, reference_price=mark_price)
    print(_json_dump(result))
    return 0


def cmd_market_manifest(config: BotConfig) -> int:
    with JSONLWriter(config.data_dir) as writer:
        market_collector = MarketCollector(config, writer=writer)
        book_ticker_collector = BookTickerCollector(config, writer=writer)
        depth_collector = DepthBookCollector(config, client=_build_client(config), writer=writer)
        rpi_depth_collector = RPIDepthBookCollector(config, client=_build_client(config), writer=writer)
        print(_json_dump({
            "market": market_collector.manifest(),
            "book_ticker": book_ticker_collector.manifest(),
            "depth_book": depth_collector.manifest(),
            "rpi_depth_book": rpi_depth_collector.manifest(),
        }))
    return 0


def cmd_bootstrap_sync(config: BotConfig) -> int:
    if not config.has_api_credentials:
        print("bootstrap-sync requires BINANCE_API_KEY and BINANCE_API_SECRET")
        return 1

    client = _build_client(config)
    store = StateStore()
    synchronizer = BootstrapSynchronizer(config, client=client, store=store)
    result, raw_snapshot = synchronizer.sync()

    with JSONLWriter(config.data_dir) as writer:
        raw_path = writer.write_json("bootstrap/latest_raw.json", raw_snapshot)
        state_path = writer.write_json("bootstrap/state_after_sync.json", store.snapshot())

    print(_json_dump({"bootstrap": result, "raw_snapshot_path": raw_path, "state_snapshot_path": state_path}))
    return 0


async def cmd_collect_market(config: BotConfig, max_messages: int | None) -> int:
    with JSONLWriter(config.data_dir) as writer:
        collector = MarketCollector(config, writer=writer)
        status = await collector.run(stop_after_messages=max_messages)
        print(_json_dump({"collector": "market", "status": status, "data_dir": config.data_dir}))
    return 0


async def cmd_collect_book_ticker(config: BotConfig, max_messages: int | None) -> int:
    with JSONLWriter(config.data_dir) as writer:
        collector = BookTickerCollector(config, writer=writer)
        status = await collector.run(stop_after_messages=max_messages)
        print(_json_dump({"collector": "book_ticker", "status": status, "data_dir": config.data_dir}))
    return 0


async def cmd_collect_depth_book(
    config: BotConfig,
    *,
    max_messages: int | None,
    depth_levels: int,
    snapshot_limit: int,
) -> int:
    client = _build_client(config)
    with JSONLWriter(config.data_dir) as writer:
        collector = DepthBookCollector(
            config,
            client=client,
            writer=writer,
            depth_levels=depth_levels,
            snapshot_limit=snapshot_limit,
        )
        status = await collector.run(stop_after_messages=max_messages)
        print(_json_dump({"collector": "depth_book", "status": status, "data_dir": config.data_dir}))
    return 0


async def cmd_collect_rpi_depth_book(
    config: BotConfig,
    *,
    max_messages: int | None,
    depth_levels: int,
    snapshot_limit: int,
) -> int:
    client = _build_client(config)
    with JSONLWriter(config.data_dir) as writer:
        collector = RPIDepthBookCollector(
            config,
            client=client,
            writer=writer,
            depth_levels=depth_levels,
            snapshot_limit=snapshot_limit,
        )
        status = await collector.run(stop_after_messages=max_messages)
        print(_json_dump({"collector": "rpi_depth_book", "status": status, "data_dir": config.data_dir}))
    return 0


async def cmd_collect_crowding(
    config: BotConfig,
    *,
    period: str,
    interval_seconds: float,
    max_iterations: int | None,
) -> int:
    client = _build_client(config)
    with JSONLWriter(config.data_dir) as writer:
        collector = CrowdingCollector(config, client=client, writer=writer)
        status = await collector.run(period=period, interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"collector": "crowding", "status": status, "data_dir": config.data_dir, "period": period}))
    return 0


async def cmd_consume_private(config: BotConfig, max_messages: int | None) -> int:
    if not config.has_api_credentials:
        print("private consumer requires BINANCE_API_KEY and BINANCE_API_SECRET")
        return 1

    client = _build_client(config)
    store = StateStore()
    with JSONLWriter(config.data_dir) as writer:
        consumer = PrivateStreamConsumer(config, client=client, store=store, writer=writer)
        status = await consumer.run(stop_after_messages=max_messages)
        print(_json_dump({"collector": "private", "status": status, "runtime_state": store.snapshot(), "data_dir": config.data_dir}))
    return 0


def cmd_backtest_breakout(
    config: BotConfig,
    *,
    start_date: str | None,
    end_date: str | None,
    strategy_kind: str,
    lookback: int,
    atr_window: int,
    reversion_lookback: int | None,
    reversion_entry_atr_multiple: Decimal,
    reversion_max_atr_fraction: Decimal | None,
    reversion_min_flow_flip: Decimal,
    router_range_max_atr_fraction: Decimal,
    router_trend_min_atr_fraction: Decimal,
    router_trend_min_abs_flow_imbalance: Decimal,
    router_range_max_abs_flow_imbalance: Decimal,
    router_neutral_preference: str,
    router_opportunistic_fallback: bool,
    entry_timeout: int,
    hold_seconds: int,
    position_notional: Decimal,
    spread_bps: Decimal,
    taker_slippage_bps: Decimal,
    maker_fee_bps: Decimal,
    taker_fee_bps: Decimal,
    trade_flow_window_seconds: int,
    min_recent_agg_trades: int,
    min_flow_imbalance: Decimal,
    max_mark_trade_divergence_bps: Decimal | None,
    max_positive_funding_rate: Decimal | None,
    min_negative_funding_rate: Decimal | None,
    crowding_period: str,
    max_crowding_snapshot_age_seconds: int | None,
    min_crowding_score: Decimal | None,
    crowding_oi_expansion_weight: Decimal,
    use_book_ticker_fills: bool,
    use_local_depth_fills: bool,
    use_rpi_depth_fills: bool,
    max_book_spread_bps: Decimal | None,
    max_book_ticker_staleness_ms: int | None,
    max_depth_snapshot_staleness_ms: int | None,
    min_depth_imbalance: Decimal | None,
    depth_levels: int,
    min_notional_multiplier: Decimal,
    max_notional_multiplier: Decimal,
    abstain_below_multiplier: Decimal,
    min_effective_notional_usdt: Decimal,
    sizing_flow_weight: Decimal,
    sizing_crowding_weight: Decimal,
    sizing_divergence_penalty_weight: Decimal,
    sizing_funding_penalty_weight: Decimal,
    sizing_divergence_penalty_cap_bps: Decimal,
    sizing_funding_penalty_cap_rate: Decimal,
    volatility_target_atr_fraction: Decimal | None,
    volatility_abstain_above_atr_fraction: Decimal | None,
    volatility_min_notional_multiplier: Decimal,
    volatility_max_notional_multiplier: Decimal,
    min_expected_fill_ratio: Decimal | None,
    max_expected_queue_clear_seconds: Decimal | None,
    max_queue_ahead_to_order_ratio: Decimal | None,
    min_directional_queue_flow_qty_per_second: Decimal,
    min_exit_depth_coverage_ratio: Decimal | None,
    max_exit_depth_sweep_bps: Decimal | None,
    exit_depth_tail_penalty_bps: Decimal,
    synthetic_tail_levels: int,
    synthetic_tail_replenishment_ratio: Decimal,
    synthetic_tail_step_bps: Decimal,
    economics_lookback_days: int,
    economics_feedback_enabled: bool,
    economics_feedback_min_active_day_count: int,
    economics_feedback_min_multiplier: Decimal,
    economics_regime_enabled: bool,
    economics_regime_min_active_day_count: int,
    mark_only: bool,
    ignore_contract_status: bool,
) -> int:
    client = _build_client(config)
    try:
        filters = extract_symbol_filters(client.exchange_info().data, config.symbol)
    except Exception:  # noqa: BLE001
        filters = None

    backtest_config = BreakoutBacktestConfig(
        strategy_kind=strategy_kind,
        breakout_lookback_ticks=lookback,
        atr_window_ticks=atr_window,
        reversion_lookback_ticks=reversion_lookback,
        reversion_entry_atr_multiple=reversion_entry_atr_multiple,
        reversion_max_atr_fraction=reversion_max_atr_fraction,
        reversion_min_flow_flip=reversion_min_flow_flip,
        router_range_max_atr_fraction=router_range_max_atr_fraction,
        router_trend_min_atr_fraction=router_trend_min_atr_fraction,
        router_trend_min_abs_flow_imbalance=router_trend_min_abs_flow_imbalance,
        router_range_max_abs_flow_imbalance=router_range_max_abs_flow_imbalance,
        router_neutral_preference=router_neutral_preference,
        router_opportunistic_fallback=router_opportunistic_fallback,
        entry_timeout_seconds=entry_timeout,
        max_hold_seconds=hold_seconds,
        position_notional_usdt=position_notional,
        synthetic_spread_bps=spread_bps,
        taker_slippage_bps=taker_slippage_bps,
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        trade_flow_window_seconds=trade_flow_window_seconds,
        min_recent_agg_trades=min_recent_agg_trades,
        min_flow_imbalance=min_flow_imbalance,
        max_mark_trade_divergence_bps=max_mark_trade_divergence_bps,
        max_positive_funding_rate=max_positive_funding_rate,
        min_negative_funding_rate=min_negative_funding_rate,
        require_contract_trading_status=not ignore_contract_status,
        crowding_period=crowding_period,
        max_crowding_snapshot_age_seconds=max_crowding_snapshot_age_seconds,
        min_crowding_score=min_crowding_score,
        crowding_oi_expansion_weight=crowding_oi_expansion_weight,
        use_book_ticker_fills=use_book_ticker_fills,
        use_local_depth_fills=use_local_depth_fills,
        use_rpi_depth_fills=use_rpi_depth_fills,
        max_book_spread_bps=max_book_spread_bps,
        max_book_ticker_staleness_ms=max_book_ticker_staleness_ms,
        max_depth_snapshot_staleness_ms=max_depth_snapshot_staleness_ms,
        min_depth_imbalance=min_depth_imbalance,
        depth_levels=depth_levels,
        min_notional_multiplier=min_notional_multiplier,
        max_notional_multiplier=max_notional_multiplier,
        abstain_below_multiplier=abstain_below_multiplier,
        min_effective_notional_usdt=min_effective_notional_usdt,
        sizing_flow_weight=sizing_flow_weight,
        sizing_crowding_weight=sizing_crowding_weight,
        sizing_divergence_penalty_weight=sizing_divergence_penalty_weight,
        sizing_funding_penalty_weight=sizing_funding_penalty_weight,
        sizing_divergence_penalty_cap_bps=sizing_divergence_penalty_cap_bps,
        sizing_funding_penalty_cap_rate=sizing_funding_penalty_cap_rate,
        volatility_target_atr_fraction=volatility_target_atr_fraction,
        volatility_abstain_above_atr_fraction=volatility_abstain_above_atr_fraction,
        volatility_min_notional_multiplier=volatility_min_notional_multiplier,
        volatility_max_notional_multiplier=volatility_max_notional_multiplier,
        min_expected_fill_ratio=min_expected_fill_ratio,
        max_expected_queue_clear_seconds=max_expected_queue_clear_seconds,
        max_queue_ahead_to_order_ratio=max_queue_ahead_to_order_ratio,
        min_directional_queue_flow_qty_per_second=min_directional_queue_flow_qty_per_second,
        min_exit_depth_coverage_ratio=min_exit_depth_coverage_ratio,
        max_exit_depth_sweep_bps=max_exit_depth_sweep_bps,
        exit_depth_tail_penalty_bps=exit_depth_tail_penalty_bps,
        synthetic_tail_levels=synthetic_tail_levels,
        synthetic_tail_replenishment_ratio=synthetic_tail_replenishment_ratio,
        synthetic_tail_step_bps=synthetic_tail_step_bps,
        economics_lookback_days=economics_lookback_days,
        economics_feedback_enabled=economics_feedback_enabled,
        economics_feedback_min_active_day_count=economics_feedback_min_active_day_count,
        economics_feedback_min_multiplier=economics_feedback_min_multiplier,
        economics_regime_enabled=economics_regime_enabled,
        economics_regime_min_active_day_count=economics_regime_min_active_day_count,
    )

    if mark_only:
        backtester = BreakoutBacktester(symbol=config.symbol, config=backtest_config, filters=filters, economics_data_dir=config.data_dir)
        report = backtester.run(
            iter_mark_price_ticks(config.data_dir, symbol=config.symbol, start_date=start_date, end_date=end_date)
        )
        mode = "mark_only"
    else:
        backtester = ParityBreakoutBacktester(symbol=config.symbol, config=backtest_config, filters=filters, economics_data_dir=config.data_dir)
        report = backtester.run(
            iter_market_events(
                config.data_dir,
                symbol=config.symbol,
                start_date=start_date,
                end_date=end_date,
                include_agg_trades=True,
                include_book_ticker=True,
                include_contract_info=config.enable_contract_info_stream,
                include_crowding=True,
                crowding_period=crowding_period,
                include_local_depth=True,
                local_depth_levels=depth_levels,
                include_local_rpi_depth=use_rpi_depth_fills,
                local_rpi_depth_levels=depth_levels,
            )
        )
        mode = "multistream_parity"

    if report.ticks == 0:
        print(_json_dump({
            "error": "no_backtest_ticks_found",
            "mode": mode,
            "data_dir": config.data_dir,
            "symbol": config.symbol,
            "start_date": start_date,
            "end_date": end_date,
        }))
        return 1

    execution_quality = build_execution_quality_report(report)

    payload = {
        "baseline_source": "backtest_report",
        "symbol": config.symbol,
        "strategy_kind": strategy_kind,
        "mode": mode,
        "data_dir": config.data_dir,
        "filters_loaded": filters is not None,
        "config": backtest_config,
        "summary": {
            "ticks": report.ticks,
            "market_events": report.market_events,
            "trade_count": report.trade_count,
            "wins": report.wins,
            "losses": report.losses,
            "win_rate": report.win_rate,
            "missed_entries": report.missed_entries,
            "rejected_entries": report.rejected_entries,
            "signal_gate_rejections": report.signal_gate_rejections,
            "contract_gate_rejections": report.contract_gate_rejections,
            "crowding_gate_rejections": report.crowding_gate_rejections,
            "book_gate_rejections": report.book_gate_rejections,
            "depth_gate_rejections": report.depth_gate_rejections,
            "queue_gate_rejections": report.queue_gate_rejections,
            "depth_liquidity_gate_rejections": report.depth_liquidity_gate_rejections,
            "adaptive_abstentions": report.adaptive_abstentions,
            "crowding_events": report.crowding_events,
            "depth_events": report.depth_events,
            "gross_pnl": report.gross_pnl,
            "fee_pnl": report.fee_pnl,
            "funding_pnl": report.funding_pnl,
            "net_pnl": report.net_pnl,
            "max_drawdown": report.max_drawdown,
            "last_contract_status": report.last_contract_status,
            "last_contract_bracket_count": report.last_contract_bracket_count,
            "last_crowding_side_score": report.last_crowding_side_score,
            "last_crowding_snapshot_age_ms": report.last_crowding_snapshot_age_ms,
            "last_crowding_period": report.last_crowding_period,
            "last_book_spread_bps": report.last_book_spread_bps,
            "last_book_age_ms": report.last_book_age_ms,
            "last_depth_imbalance": report.last_depth_imbalance,
            "last_depth_age_ms": report.last_depth_age_ms,
            "last_depth_levels": report.last_depth_levels,
            "last_volatility_multiplier": report.last_volatility_multiplier,
            "last_atr_fraction_bps": report.last_atr_fraction_bps,
            "last_economics_dashboard_end_date": report.last_economics_dashboard_end_date,
            "last_economics_dashboard_active_day_count": report.last_economics_dashboard_active_day_count,
            "last_economics_regime_action": report.last_economics_regime_action,
            "last_economics_regime_size_multiplier": report.last_economics_regime_size_multiplier,
            "last_economics_regime_negative_day_ratio": report.last_economics_regime_negative_day_ratio,
            "last_economics_regime_recent_day_net_realized_bps": report.last_economics_regime_recent_day_net_realized_bps,
            "last_economics_regime_average_maker_ratio": report.last_economics_regime_average_maker_ratio,
            "last_economics_feedback_multiplier": report.last_economics_feedback_multiplier,
            "last_economics_feedback_total_penalty": report.last_economics_feedback_total_penalty,
            "last_economics_feedback_reason": report.last_economics_feedback_reason,
            "router_breakout_signal_count": report.router_breakout_signal_count,
            "router_reversion_signal_count": report.router_reversion_signal_count,
            "router_fallback_signal_count": report.router_fallback_signal_count,
            "last_router_regime": report.last_router_regime,
            "last_router_selected_strategy_kind": report.last_router_selected_strategy_kind,
            "last_router_preferred_strategy_kind": report.last_router_preferred_strategy_kind,
            "ensemble_breakout_signal_count": report.ensemble_breakout_signal_count,
            "ensemble_reversion_signal_count": report.ensemble_reversion_signal_count,
            "ensemble_override_signal_count": report.ensemble_override_signal_count,
            "last_ensemble_regime": report.last_ensemble_regime,
            "last_ensemble_selected_strategy_kind": report.last_ensemble_selected_strategy_kind,
            "last_ensemble_preferred_strategy_kind": report.last_ensemble_preferred_strategy_kind,
            "last_ensemble_breakout_score": report.last_ensemble_breakout_score,
            "last_ensemble_reversion_score": report.last_ensemble_reversion_score,
            "economics_feedback_decision_count": report.economics_feedback_decision_count,
            "economics_regime_reduce_size_applications": report.economics_regime_reduce_size_applications,
            "economics_regime_observe_rejections": report.economics_regime_observe_rejections,
            "last_expected_fill_ratio": report.last_expected_fill_ratio,
            "last_expected_queue_clear_seconds": report.last_expected_queue_clear_seconds,
            "last_queue_ahead_ratio": report.last_queue_ahead_ratio,
            "last_directional_queue_flow_qty_per_second": report.last_directional_queue_flow_qty_per_second,
            "last_actual_entry_fill_ratio": report.last_actual_entry_fill_ratio,
            "last_entry_fill_latency_seconds": report.last_entry_fill_latency_seconds,
            "last_entry_fill_ratio_shortfall": report.last_entry_fill_ratio_shortfall,
            "last_entry_fill_latency_overshoot_seconds": report.last_entry_fill_latency_overshoot_seconds,
            "last_exit_depth_coverage_ratio": report.last_exit_depth_coverage_ratio,
            "last_exit_depth_sweep_bps": report.last_exit_depth_sweep_bps,
            "last_exit_depth_levels_consumed": report.last_exit_depth_levels_consumed,
            "average_entry_notional": report.average_entry_notional,
            "average_notional_multiplier": report.average_notional_multiplier,
            "average_economics_feedback_multiplier": report.average_economics_feedback_multiplier,
            "average_expected_fill_ratio": report.average_expected_fill_ratio,
            "average_queue_clear_seconds": report.average_queue_clear_seconds,
            "average_queue_ahead_ratio": report.average_queue_ahead_ratio,
            "average_directional_queue_flow_qty_per_second": report.average_directional_queue_flow_qty_per_second,
            "average_realized_entry_fill_ratio": report.average_realized_entry_fill_ratio,
            "average_entry_fill_ratio_shortfall": report.average_entry_fill_ratio_shortfall,
            "average_entry_fill_latency_seconds": report.average_entry_fill_latency_seconds,
            "average_entry_fill_latency_overshoot_seconds": report.average_entry_fill_latency_overshoot_seconds,
            "entry_timeout_rate": report.entry_timeout_rate,
            "modeled_partial_entry_count": report.modeled_partial_entry_count,
            "modeled_partial_entry_qty": report.modeled_partial_entry_qty,
            "entry_remainder_cancel_count": report.entry_remainder_cancel_count,
            "unmodeled_partial_entry_count": report.unmodeled_partial_entry_count,
            "unmodeled_partial_entry_qty": report.unmodeled_partial_entry_qty,
            "last_entry_completion_reason": report.last_entry_completion_reason,
            "promotion_blocked_by_partial_fills": report.promotion_blocked_by_partial_fills,
            "execution_fidelity_status": report.execution_fidelity_status,
            "average_exit_depth_sweep_bps": report.average_exit_depth_sweep_bps,
            "average_exit_depth_coverage_ratio": report.average_exit_depth_coverage_ratio,
            "average_exit_depth_levels_consumed": report.average_exit_depth_levels_consumed,
            "average_exit_terminal_tail_ratio": report.average_exit_terminal_tail_ratio,
            "last_exit_pricing_source": report.last_exit_pricing_source,
            "last_exit_pricing_fallback_reason": report.last_exit_pricing_fallback_reason,
            "last_exit_depth_age_ms": report.last_exit_depth_age_ms,
            "last_exit_book_age_ms": report.last_exit_book_age_ms,
            "exit_depth_pricing_count": report.exit_depth_pricing_count,
            "exit_book_pricing_count": report.exit_book_pricing_count,
            "exit_mark_pricing_count": report.exit_mark_pricing_count,
            "exit_depth_fallback_count": report.exit_depth_fallback_count,
            "exit_book_fallback_count": report.exit_book_fallback_count,
        },
        "execution_quality": execution_quality,
        "trades": report.trades,
    }

    with JSONLWriter(config.data_dir) as writer:
        report_path = writer.append_record(
            "reports",
            f"{config.symbol.lower()}_backtest_reports",
            payload,
        )
        latest_report_path = writer.write_json("backtest/latest_report.json", payload)
        latest_quality_path = writer.write_json("backtest/latest_execution_quality.json", execution_quality)
        payload["report_path"] = report_path
        payload["latest_report_path"] = latest_report_path
        payload["latest_execution_quality_path"] = latest_quality_path

    print(_json_dump(payload))
    return 0



def cmd_backtest_readiness(
    config: BotConfig,
    *,
    start_date: str | None,
    end_date: str | None,
    mark_only: bool,
    crowding_period: str,
    depth_levels: int,
    use_rpi_depth_fills: bool,
    ignore_contract_status: bool,
) -> int:
    report = build_backtest_readiness_report(
        config.data_dir,
        symbol=config.symbol,
        start_date=start_date,
        end_date=end_date,
        mark_only=mark_only,
        crowding_period=crowding_period,
        depth_levels=depth_levels,
        use_rpi_depth_fills=use_rpi_depth_fills,
        ignore_contract_status=ignore_contract_status,
    )
    payload = {
        "symbol": report.symbol,
        "data_dir": report.data_dir,
        "start_date": report.start_date,
        "end_date": report.end_date,
        "days": list(report.days),
        "requested_mode": report.requested_mode,
        "recommendation": report.recommendation,
        "missing_required_streams": list(report.missing_required_streams),
        "low_density_warnings": list(report.low_density_warnings),
        "recommended_command": report.recommended_command,
        "requested_streams": [
            {
                "label": item.label,
                "namespace": item.namespace,
                "filename": item.filename,
                "required": item.required,
                "present_days": list(item.present_days),
                "missing_days": list(item.missing_days),
                "coverage_ratio": item.coverage_ratio,
                "min_line_count": item.min_line_count,
                "line_counts": item.line_counts,
            }
            for item in report.requested_streams
        ],
    }
    print(_json_dump(payload))
    ready_recommendations = {"mark_only_ready", "mark_only_only", "multistream_ready", "multistream_ready_but_sample_sparse"}
    return 0 if report.recommendation in ready_recommendations else 1



def cmd_walkforward_breakout(
    config: BotConfig,
    *,
    start_date: str | None,
    end_date: str | None,
    strategy_kind: str,
    strategy_grid: str,
    train_days: int,
    test_days: int,
    step_days: int | None,
    anchored_train: bool,
    max_folds: int | None,
    max_candidates: int,
    lookback: int,
    lookback_grid: str,
    atr_window: int,
    reversion_lookback: int | None,
    reversion_entry_atr_multiple: Decimal,
    reversion_entry_atr_multiple_grid: str,
    reversion_max_atr_fraction: Decimal | None,
    reversion_max_atr_fraction_grid: str,
    reversion_min_flow_flip: Decimal,
    reversion_min_flow_flip_grid: str,
    router_range_max_atr_fraction: Decimal,
    router_trend_min_atr_fraction: Decimal,
    router_trend_min_abs_flow_imbalance: Decimal,
    router_range_max_abs_flow_imbalance: Decimal,
    router_neutral_preference: str,
    router_opportunistic_fallback: bool,
    entry_timeout: int,
    hold_seconds: int,
    hold_seconds_grid: str,
    position_notional: Decimal,
    spread_bps: Decimal,
    taker_slippage_bps: Decimal,
    maker_fee_bps: Decimal,
    taker_fee_bps: Decimal,
    trade_flow_window_seconds: int,
    min_recent_agg_trades: int,
    min_flow_imbalance: Decimal,
    min_flow_imbalance_grid: str,
    max_mark_trade_divergence_bps: Decimal | None,
    max_positive_funding_rate: Decimal | None,
    min_negative_funding_rate: Decimal | None,
    crowding_period: str,
    max_crowding_snapshot_age_seconds: int | None,
    min_crowding_score: Decimal | None,
    min_crowding_score_grid: str,
    crowding_oi_expansion_weight: Decimal,
    use_book_ticker_fills: bool,
    use_local_depth_fills: bool,
    use_rpi_depth_fills: bool,
    max_book_spread_bps: Decimal | None,
    max_book_spread_bps_grid: str,
    max_book_ticker_staleness_ms: int | None,
    max_depth_snapshot_staleness_ms: int | None,
    min_depth_imbalance: Decimal | None,
    min_depth_imbalance_grid: str,
    depth_levels: int,
    min_notional_multiplier: Decimal,
    max_notional_multiplier: Decimal,
    abstain_below_multiplier: Decimal,
    min_effective_notional_usdt: Decimal,
    sizing_flow_weight: Decimal,
    sizing_crowding_weight: Decimal,
    sizing_divergence_penalty_weight: Decimal,
    sizing_funding_penalty_weight: Decimal,
    sizing_divergence_penalty_cap_bps: Decimal,
    sizing_funding_penalty_cap_rate: Decimal,
    volatility_target_atr_fraction: Decimal | None,
    volatility_abstain_above_atr_fraction: Decimal | None,
    volatility_min_notional_multiplier: Decimal,
    volatility_max_notional_multiplier: Decimal,
    min_expected_fill_ratio: Decimal | None,
    min_expected_fill_ratio_grid: str,
    max_expected_queue_clear_seconds: Decimal | None,
    max_queue_ahead_to_order_ratio: Decimal | None,
    min_directional_queue_flow_qty_per_second: Decimal,
    min_exit_depth_coverage_ratio: Decimal | None,
    max_exit_depth_sweep_bps: Decimal | None,
    exit_depth_tail_penalty_bps: Decimal,
    synthetic_tail_levels: int,
    synthetic_tail_replenishment_ratio: Decimal,
    synthetic_tail_step_bps: Decimal,
    economics_lookback_days: int,
    economics_feedback_enabled: bool,
    economics_feedback_min_active_day_count: int,
    economics_feedback_min_multiplier: Decimal,
    economics_regime_enabled: bool,
    economics_regime_min_active_day_count: int,
    max_drawdown_penalty: Decimal,
    entry_timeout_rate_penalty: Decimal,
    exit_depth_sweep_bps_penalty: Decimal,
    min_trade_count: int,
    mark_only: bool,
    ignore_contract_status: bool,
) -> int:
    client = _build_client(config)
    try:
        filters = extract_symbol_filters(client.exchange_info().data, config.symbol)
    except Exception:  # noqa: BLE001
        filters = None

    base_backtest_config = BreakoutBacktestConfig(
        strategy_kind=strategy_kind,
        breakout_lookback_ticks=lookback,
        atr_window_ticks=atr_window,
        reversion_lookback_ticks=reversion_lookback,
        reversion_entry_atr_multiple=reversion_entry_atr_multiple,
        reversion_max_atr_fraction=reversion_max_atr_fraction,
        reversion_min_flow_flip=reversion_min_flow_flip,
        router_range_max_atr_fraction=router_range_max_atr_fraction,
        router_trend_min_atr_fraction=router_trend_min_atr_fraction,
        router_trend_min_abs_flow_imbalance=router_trend_min_abs_flow_imbalance,
        router_range_max_abs_flow_imbalance=router_range_max_abs_flow_imbalance,
        router_neutral_preference=router_neutral_preference,
        router_opportunistic_fallback=router_opportunistic_fallback,
        entry_timeout_seconds=entry_timeout,
        max_hold_seconds=hold_seconds,
        position_notional_usdt=position_notional,
        synthetic_spread_bps=spread_bps,
        taker_slippage_bps=taker_slippage_bps,
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        trade_flow_window_seconds=trade_flow_window_seconds,
        min_recent_agg_trades=min_recent_agg_trades,
        min_flow_imbalance=min_flow_imbalance,
        max_mark_trade_divergence_bps=max_mark_trade_divergence_bps,
        max_positive_funding_rate=max_positive_funding_rate,
        min_negative_funding_rate=min_negative_funding_rate,
        require_contract_trading_status=not ignore_contract_status,
        crowding_period=crowding_period,
        max_crowding_snapshot_age_seconds=max_crowding_snapshot_age_seconds,
        min_crowding_score=min_crowding_score,
        crowding_oi_expansion_weight=crowding_oi_expansion_weight,
        use_book_ticker_fills=use_book_ticker_fills,
        use_local_depth_fills=use_local_depth_fills,
        use_rpi_depth_fills=use_rpi_depth_fills,
        max_book_spread_bps=max_book_spread_bps,
        max_book_ticker_staleness_ms=max_book_ticker_staleness_ms,
        max_depth_snapshot_staleness_ms=max_depth_snapshot_staleness_ms,
        min_depth_imbalance=min_depth_imbalance,
        depth_levels=depth_levels,
        min_notional_multiplier=min_notional_multiplier,
        max_notional_multiplier=max_notional_multiplier,
        abstain_below_multiplier=abstain_below_multiplier,
        min_effective_notional_usdt=min_effective_notional_usdt,
        sizing_flow_weight=sizing_flow_weight,
        sizing_crowding_weight=sizing_crowding_weight,
        sizing_divergence_penalty_weight=sizing_divergence_penalty_weight,
        sizing_funding_penalty_weight=sizing_funding_penalty_weight,
        sizing_divergence_penalty_cap_bps=sizing_divergence_penalty_cap_bps,
        sizing_funding_penalty_cap_rate=sizing_funding_penalty_cap_rate,
        volatility_target_atr_fraction=volatility_target_atr_fraction,
        volatility_abstain_above_atr_fraction=volatility_abstain_above_atr_fraction,
        volatility_min_notional_multiplier=volatility_min_notional_multiplier,
        volatility_max_notional_multiplier=volatility_max_notional_multiplier,
        min_expected_fill_ratio=min_expected_fill_ratio,
        max_expected_queue_clear_seconds=max_expected_queue_clear_seconds,
        max_queue_ahead_to_order_ratio=max_queue_ahead_to_order_ratio,
        min_directional_queue_flow_qty_per_second=min_directional_queue_flow_qty_per_second,
        min_exit_depth_coverage_ratio=min_exit_depth_coverage_ratio,
        max_exit_depth_sweep_bps=max_exit_depth_sweep_bps,
        exit_depth_tail_penalty_bps=exit_depth_tail_penalty_bps,
        synthetic_tail_levels=synthetic_tail_levels,
        synthetic_tail_replenishment_ratio=synthetic_tail_replenishment_ratio,
        synthetic_tail_step_bps=synthetic_tail_step_bps,
        economics_lookback_days=economics_lookback_days,
        economics_feedback_enabled=economics_feedback_enabled,
        economics_feedback_min_active_day_count=economics_feedback_min_active_day_count,
        economics_feedback_min_multiplier=economics_feedback_min_multiplier,
        economics_regime_enabled=economics_regime_enabled,
        economics_regime_min_active_day_count=economics_regime_min_active_day_count,
    )

    available_dates = discover_available_market_dates(
        data_dir=config.data_dir,
        symbol=config.symbol,
        start_date=start_date,
        end_date=end_date,
    )
    if not available_dates:
        print(_json_dump({
            "error": "no_market_dates_found",
            "symbol": config.symbol,
            "data_dir": config.data_dir,
            "start_date": start_date,
            "end_date": end_date,
        }))
        return 1

    try:
        folds = build_walkforward_folds(
            available_dates=available_dates,
            train_days=train_days,
            test_days=test_days,
            step_days=step_days,
            anchored_train=anchored_train,
            max_folds=max_folds,
        )
    except ValueError as exc:
        print(_json_dump({"error": "invalid_walkforward_window", "detail": str(exc)}))
        return 1

    if not folds:
        print(_json_dump({
            "error": "no_walkforward_folds",
            "available_day_count": len(available_dates),
            "train_days": train_days,
            "test_days": test_days,
            "step_days": step_days or test_days,
            "anchored_train": anchored_train,
        }))
        return 1

    strategy_kinds = _parse_choice_grid(strategy_grid) or [strategy_kind]
    lookbacks = _parse_int_grid(lookback_grid) or [lookback]
    hold_values = _parse_int_grid(hold_seconds_grid) or [hold_seconds]
    min_flow_values = _parse_decimal_grid(min_flow_imbalance_grid) or [min_flow_imbalance]
    min_crowding_values = _parse_optional_decimal_grid(min_crowding_score_grid) or [min_crowding_score]
    min_depth_values = _parse_optional_decimal_grid(min_depth_imbalance_grid) or [min_depth_imbalance]
    max_spread_values = _parse_optional_decimal_grid(max_book_spread_bps_grid) or [max_book_spread_bps]
    min_fill_values = _parse_optional_decimal_grid(min_expected_fill_ratio_grid) or [min_expected_fill_ratio]
    reversion_entry_atr_values = _parse_optional_decimal_grid(reversion_entry_atr_multiple_grid) or [reversion_entry_atr_multiple]
    reversion_max_atr_fraction_values = _parse_optional_decimal_grid(reversion_max_atr_fraction_grid) or [reversion_max_atr_fraction]
    reversion_min_flow_flip_values = _parse_optional_decimal_grid(reversion_min_flow_flip_grid) or [reversion_min_flow_flip]

    candidates = build_breakout_parameter_grid(
        lookbacks=lookbacks,
        hold_seconds=hold_values,
        min_flow_imbalances=min_flow_values,
        min_crowding_scores=min_crowding_values,
        min_depth_imbalances=min_depth_values,
        max_book_spread_bps_values=max_spread_values,
        min_expected_fill_ratios=min_fill_values,
        strategy_kinds=strategy_kinds,
        reversion_entry_atr_multiples=reversion_entry_atr_values,
        reversion_max_atr_fractions=reversion_max_atr_fraction_values,
        reversion_min_flow_flips=reversion_min_flow_flip_values,
    )
    if not candidates:
        print(_json_dump({"error": "empty_candidate_grid"}))
        return 1
    if len(candidates) > max_candidates:
        print(_json_dump({
            "error": "candidate_grid_too_large",
            "candidate_count": len(candidates),
            "max_candidates": max_candidates,
        }))
        return 1

    score_config = WalkForwardScoreConfig(
        max_drawdown_penalty=max_drawdown_penalty,
        entry_timeout_rate_penalty=entry_timeout_rate_penalty,
        exit_depth_sweep_bps_penalty=exit_depth_sweep_bps_penalty,
        min_trade_count=min_trade_count,
    )

    def evaluator(candidate: BreakoutParameterCandidate, eval_start_date: str, eval_end_date: str):
        candidate_config = candidate.apply(base_backtest_config)
        if mark_only:
            backtester = BreakoutBacktester(
                symbol=config.symbol,
                config=candidate_config,
                filters=filters,
                economics_data_dir=config.data_dir,
            )
            return backtester.run(
                iter_mark_price_ticks(
                    config.data_dir,
                    symbol=config.symbol,
                    start_date=eval_start_date,
                    end_date=eval_end_date,
                )
            )
        backtester = ParityBreakoutBacktester(
            symbol=config.symbol,
            config=candidate_config,
            filters=filters,
            economics_data_dir=config.data_dir,
        )
        return backtester.run(
            iter_market_events(
                config.data_dir,
                symbol=config.symbol,
                start_date=eval_start_date,
                end_date=eval_end_date,
                include_agg_trades=True,
                include_book_ticker=True,
                include_contract_info=config.enable_contract_info_stream,
                include_crowding=True,
                crowding_period=crowding_period,
                include_local_depth=True,
                local_depth_levels=depth_levels,
                include_local_rpi_depth=use_rpi_depth_fills,
                local_rpi_depth_levels=depth_levels,
            )
        )

    mode = "mark_only" if mark_only else "multistream_parity"
    walkforward_report = run_walkforward(
        symbol=config.symbol,
        mode=mode,
        available_dates=available_dates,
        folds=folds,
        candidates=candidates,
        evaluator=evaluator,
        score_config=score_config,
    )

    payload = {
        "source": "walkforward_report",
        "symbol": config.symbol,
        "strategy_kind": strategy_kind,
        "mode": mode,
        "data_dir": config.data_dir,
        "filters_loaded": filters is not None,
        "train_days": train_days,
        "test_days": test_days,
        "step_days": step_days or test_days,
        "anchored_train": anchored_train,
        "candidate_grid": {
            "strategy_kinds": strategy_kinds,
            "lookbacks": lookbacks,
            "hold_seconds": hold_values,
            "min_flow_imbalances": min_flow_values,
            "min_crowding_scores": min_crowding_values,
            "min_depth_imbalances": min_depth_values,
            "max_book_spread_bps_values": max_spread_values,
            "min_expected_fill_ratios": min_fill_values,
            "reversion_entry_atr_multiples": reversion_entry_atr_values,
            "reversion_max_atr_fractions": reversion_max_atr_fraction_values,
            "reversion_min_flow_flips": reversion_min_flow_flip_values,
        },
        "summary": {
            "available_day_count": len(available_dates),
            "fold_count": walkforward_report.fold_count,
            "skipped_fold_count": walkforward_report.skipped_fold_count,
            "candidate_count": walkforward_report.candidate_count,
            "total_test_net_pnl": walkforward_report.total_test_net_pnl,
            "total_test_gross_pnl": walkforward_report.total_test_gross_pnl,
            "total_test_fee_pnl": walkforward_report.total_test_fee_pnl,
            "total_test_funding_pnl": walkforward_report.total_test_funding_pnl,
            "total_test_trade_count": walkforward_report.total_test_trade_count,
            "average_test_win_rate": walkforward_report.average_test_win_rate,
            "average_test_entry_timeout_rate": walkforward_report.average_test_entry_timeout_rate,
            "average_test_exit_depth_sweep_bps": walkforward_report.average_test_exit_depth_sweep_bps,
            "average_test_entry_fill_latency_seconds": walkforward_report.average_test_entry_fill_latency_seconds,
            "selection_turnover_ratio": walkforward_report.selection_turnover_ratio,
            "selected_candidate_counts": walkforward_report.selected_candidate_counts,
            "selected_parameter_value_counts": walkforward_report.selected_parameter_value_counts,
        },
        "score_config": score_config,
        "walkforward": walkforward_report,
    }

    with JSONLWriter(config.data_dir) as writer:
        report_path = writer.append_record(
            "reports",
            f"{config.symbol.lower()}_walkforward_reports",
            payload,
        )
        latest_report_path = writer.write_json("backtest/latest_walkforward_report.json", payload)
        payload["report_path"] = report_path
        payload["latest_report_path"] = latest_report_path

    print(_json_dump(payload))
    return 0


def cmd_backfill_authoritative_history(
    config: BotConfig,
    *,
    start_date: str | None,
    end_date: str | None,
    start_ms: int | None,
    end_ms: int | None,
    days: int | None,
    archive_root: str | None,
    user_trade_limit: int,
    income_limit: int,
    income_window_days: int,
    user_trades_only: bool,
) -> int:
    if not config.has_api_credentials:
        print("backfill-authoritative-history requires BINANCE_API_KEY and BINANCE_API_SECRET")
        return 1

    try:
        resolved_start_ms, resolved_end_ms = _resolve_backfill_window_ms(
            start_date=start_date,
            end_date=end_date,
            start_ms=start_ms,
            end_ms=end_ms,
            days=days,
        )
    except ValueError as exc:
        print(f"backfill-authoritative-history invalid window: {exc}")
        return 1

    client = _build_client(config)
    backfill_config = AuthoritativeHistoryBackfillConfig(
        archive_root=Path(archive_root) if archive_root is not None else config.data_dir,
        start_ms=resolved_start_ms,
        end_ms=resolved_end_ms,
        user_trade_limit=user_trade_limit,
        income_limit=income_limit,
        income_window_ms=max(1, int(income_window_days)) * 24 * 60 * 60 * 1000,
        include_income_history=not user_trades_only,
    )
    with JSONLWriter(config.data_dir) as writer:
        backfiller = AuthoritativeHistoryBackfiller(
            config,
            client=client,
            writer=writer,
            backfill_config=backfill_config,
        )
        result = backfiller.run_once()
        print(_json_dump({"command": "backfill_authoritative_history", "result": result}))
    return 0


def cmd_post_fill_markout(
    config: BotConfig,
    *,
    start_date: str | None,
    end_date: str | None,
    start_ms: int | None,
    end_ms: int | None,
    days: int | None,
    archive_root: str | None,
    market_root: str,
    reference_source: str,
    horizon_seconds: int,
    max_pre_fill_age_ms: int,
    max_post_horizon_delay_ms: int,
) -> int:
    try:
        resolved_start_ms, resolved_end_ms = _resolve_backfill_window_ms(
            start_date=start_date,
            end_date=end_date,
            start_ms=start_ms,
            end_ms=end_ms,
            days=days,
        )
        markout_config = PostFillMarkoutConfig(
            archive_root=Path(archive_root) if archive_root is not None else config.data_dir,
            market_root=Path(market_root),
            symbol=config.symbol,
            start_ms=resolved_start_ms,
            end_ms=resolved_end_ms,
            horizon_ms=int(horizon_seconds) * 1000,
            max_pre_fill_age_ms=max_pre_fill_age_ms,
            max_post_horizon_delay_ms=max_post_horizon_delay_ms,
            reference_source=reference_source,
        )
        report = analyze_post_fill_markout(markout_config)
    except (OSError, ValueError) as exc:
        print(f"post-fill-markout failed: {exc}")
        return 1

    with JSONLWriter(config.data_dir) as writer:
        latest_path = writer.write_json("live/reports/latest_post_fill_markout.json", report)
        report_path = writer.append_record(
            "reports",
            f"{config.symbol.lower()}_post_fill_markout",
            {"report": report},
            event_time_ms=report.generated_at_ms,
        )
    print(
        _json_dump(
            {
                "command": "post_fill_markout",
                "report": report,
                "latest_path": latest_path,
                "report_path": report_path,
            }
        )
    )
    return 0


def cmd_post_fill_forward_observer(
    config: BotConfig,
    *,
    prereg_path: str,
    project_root: str,
) -> int:
    try:
        report = build_post_fill_forward_report(
            Path(prereg_path),
            project_root=Path(project_root).resolve(),
            generated_at_ms=int(datetime.now(tz=UTC).timestamp() * 1000),
            credentials_present=config.has_api_credentials,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"post-fill-forward-observer failed: {exc}")
        return 1

    with JSONLWriter(config.data_dir) as writer:
        latest_path = writer.write_json("live/reports/latest_post_fill_forward_observer.json", report)
        report_path = writer.append_record(
            "reports",
            f"{config.symbol.lower()}_post_fill_forward_observer",
            {"report": report},
            event_time_ms=int(report["generated_at_ms"]),
        )
    print(
        _json_dump(
            {
                "command": "post_fill_forward_observer",
                "report": report,
                "latest_path": latest_path,
                "report_path": report_path,
            }
        )
    )
    return 0


def cmd_aggregate_reports(config: BotConfig, *, date: str | None) -> int:
    report_date = date or datetime.now(tz=UTC).strftime("%Y-%m-%d")
    aggregate = aggregate_daily_reports(data_dir=config.data_dir, symbol=config.symbol, date=report_date)
    print(_json_dump({"aggregate": aggregate, "data_dir": config.data_dir}))
    return 0


async def cmd_execution_drift_watch(
    config: BotConfig,
    *,
    baseline_path: str | None,
    live_report_path: str | None,
    interval_seconds: float,
    max_iterations: int | None,
    min_expected_fill_ratio_factor_reduce: Decimal,
    min_expected_fill_ratio_factor_observe: Decimal,
    max_queue_clear_seconds_factor_reduce: Decimal,
    max_queue_clear_seconds_factor_observe: Decimal,
    max_exit_depth_sweep_bps_add_reduce: Decimal,
    max_exit_depth_sweep_bps_add_observe: Decimal,
    max_terminal_tail_ratio_reduce: Decimal,
    max_terminal_tail_ratio_observe: Decimal,
) -> int:
    thresholds = ExecutionDriftThresholds(
        min_expected_fill_ratio_factor_reduce=min_expected_fill_ratio_factor_reduce,
        min_expected_fill_ratio_factor_observe=min_expected_fill_ratio_factor_observe,
        max_queue_clear_seconds_factor_reduce=max_queue_clear_seconds_factor_reduce,
        max_queue_clear_seconds_factor_observe=max_queue_clear_seconds_factor_observe,
        max_exit_depth_sweep_bps_add_reduce=max_exit_depth_sweep_bps_add_reduce,
        max_exit_depth_sweep_bps_add_observe=max_exit_depth_sweep_bps_add_observe,
        max_terminal_tail_ratio_reduce=max_terminal_tail_ratio_reduce,
        max_terminal_tail_ratio_observe=max_terminal_tail_ratio_observe,
    )
    daemon_config = ExecutionDriftDaemonConfig(
        baseline_path=Path(baseline_path or (config.data_dir / "backtest" / "latest_report.json")),
        live_report_path=Path(live_report_path or (config.data_dir / "live" / "reports" / "latest_execution_quality.json")),
        thresholds=thresholds,
    )
    with JSONLWriter(config.data_dir) as writer:
        daemon = ExecutionDriftDaemon(config, writer=writer, daemon_config=daemon_config)
        status = await daemon.run(interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"daemon": "execution_drift", "status": status, "config": daemon_config}))
    return 0


async def cmd_intraday_protection_watch(
    config: BotConfig,
    *,
    interval_seconds: float,
    max_iterations: int | None,
    max_quant_utilization_reduce: Decimal,
    max_quant_utilization_observe: Decimal,
    max_adl_quantile_reduce: int,
    max_adl_quantile_observe: int,
    with_adl: bool,
) -> int:
    if not config.has_api_credentials:
        print("intraday-protection-watch requires BINANCE_API_KEY and BINANCE_API_SECRET")
        return 1

    client = _build_client(config)
    thresholds = IntradayProtectionThresholds(
        max_quant_utilization_reduce=max_quant_utilization_reduce,
        max_quant_utilization_observe=max_quant_utilization_observe,
        max_adl_quantile_reduce=max_adl_quantile_reduce,
        max_adl_quantile_observe=max_adl_quantile_observe,
    )
    daemon_config = IntradayProtectionDaemonConfig(
        thresholds=thresholds,
        include_adl=with_adl,
        position_mode=config.position_mode,
    )
    with JSONLWriter(config.data_dir) as writer:
        daemon = IntradayProtectionDaemon(
            config,
            client=client,
            writer=writer,
            daemon_config=daemon_config,
        )
        status = await daemon.run(interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"daemon": "intraday_protection", "status": status, "config": daemon_config}))
    return 0


async def cmd_pnl_protection_watch(
    config: BotConfig,
    *,
    runtime_state_path: str | None,
    bootstrap_state_path: str | None,
    anchor_path: str | None,
    asset: str,
    interval_seconds: float,
    max_iterations: int | None,
    max_session_loss_fraction_reduce: Decimal | None,
    max_session_loss_fraction_observe: Decimal | None,
    max_drawdown_fraction_reduce: Decimal | None,
    max_drawdown_fraction_observe: Decimal | None,
    max_unrealized_loss_fraction_reduce: Decimal | None,
    max_unrealized_loss_fraction_observe: Decimal | None,
    max_session_loss_usdt_reduce: Decimal | None,
    max_session_loss_usdt_observe: Decimal | None,
    max_drawdown_usdt_reduce: Decimal | None,
    max_drawdown_usdt_observe: Decimal | None,
    max_unrealized_loss_usdt_reduce: Decimal | None,
    max_unrealized_loss_usdt_observe: Decimal | None,
) -> int:
    thresholds = PnLProtectionThresholds(
        max_session_loss_fraction_reduce=max_session_loss_fraction_reduce,
        max_session_loss_fraction_observe=max_session_loss_fraction_observe,
        max_drawdown_fraction_reduce=max_drawdown_fraction_reduce,
        max_drawdown_fraction_observe=max_drawdown_fraction_observe,
        max_unrealized_loss_fraction_reduce=max_unrealized_loss_fraction_reduce,
        max_unrealized_loss_fraction_observe=max_unrealized_loss_fraction_observe,
        max_session_loss_usdt_reduce=max_session_loss_usdt_reduce,
        max_session_loss_usdt_observe=max_session_loss_usdt_observe,
        max_drawdown_usdt_reduce=max_drawdown_usdt_reduce,
        max_drawdown_usdt_observe=max_drawdown_usdt_observe,
        max_unrealized_loss_usdt_reduce=max_unrealized_loss_usdt_reduce,
        max_unrealized_loss_usdt_observe=max_unrealized_loss_usdt_observe,
    )
    daemon_config = PnLProtectionDaemonConfig(
        runtime_state_path=Path(runtime_state_path or (config.data_dir / "private" / "state" / "latest.json")),
        bootstrap_state_path=Path(bootstrap_state_path or (config.data_dir / "live" / "bootstrap_state.json")),
        anchor_path=Path(anchor_path) if anchor_path is not None else Path("live/guards/latest_pnl_anchor.json"),
        thresholds=thresholds,
        asset=asset,
    )
    with JSONLWriter(config.data_dir) as writer:
        daemon = PnLProtectionDaemon(config, writer=writer, daemon_config=daemon_config)
        status = await daemon.run(interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"daemon": "pnl_protection", "status": status, "config": daemon_config}))
    return 0


async def cmd_trade_reconciliation_watch(
    config: BotConfig,
    *,
    runtime_state_path: str | None,
    session_state_path: str | None,
    lookback_minutes: int,
    interval_seconds: float,
    max_iterations: int | None,
    authoritative_archive_root: str | None,
    prefer_authoritative_archive: bool,
    hydrate_archive_gaps: bool,
    income_window_days: int,
    min_exchange_trade_count: int,
    max_missing_local_trade_ratio_reduce: Decimal,
    max_missing_local_trade_ratio_observe: Decimal,
    max_unmatched_local_trade_ratio_reduce: Decimal,
    max_unmatched_local_trade_ratio_observe: Decimal,
    max_missing_local_order_ratio_reduce: Decimal,
    max_missing_local_order_ratio_observe: Decimal,
    max_unmatched_local_order_ratio_reduce: Decimal,
    max_unmatched_local_order_ratio_observe: Decimal,
    max_realized_pnl_diff_usdt_reduce: Decimal,
    max_realized_pnl_diff_usdt_observe: Decimal,
    max_commission_abs_diff_usdt_reduce: Decimal,
    max_commission_abs_diff_usdt_observe: Decimal,
    max_quote_qty_abs_diff_usdt_reduce: Decimal,
    max_quote_qty_abs_diff_usdt_observe: Decimal,
    max_income_trade_realized_pnl_diff_usdt_reduce: Decimal,
    max_income_trade_realized_pnl_diff_usdt_observe: Decimal,
    max_income_trade_link_gap_ratio_reduce: Decimal,
    max_income_trade_link_gap_ratio_observe: Decimal,
    reduce_size_multiplier: Decimal,
) -> int:
    if not config.has_api_credentials:
        print("trade-reconciliation-watch requires BINANCE_API_KEY and BINANCE_API_SECRET")
        return 1

    client = _build_client(config)
    thresholds = TradeReconciliationThresholds(
        min_exchange_trade_count=min_exchange_trade_count,
        max_missing_local_trade_ratio_reduce=max_missing_local_trade_ratio_reduce,
        max_missing_local_trade_ratio_observe=max_missing_local_trade_ratio_observe,
        max_unmatched_local_trade_ratio_reduce=max_unmatched_local_trade_ratio_reduce,
        max_unmatched_local_trade_ratio_observe=max_unmatched_local_trade_ratio_observe,
        max_missing_local_order_ratio_reduce=max_missing_local_order_ratio_reduce,
        max_missing_local_order_ratio_observe=max_missing_local_order_ratio_observe,
        max_unmatched_local_order_ratio_reduce=max_unmatched_local_order_ratio_reduce,
        max_unmatched_local_order_ratio_observe=max_unmatched_local_order_ratio_observe,
        max_realized_pnl_diff_usdt_reduce=max_realized_pnl_diff_usdt_reduce,
        max_realized_pnl_diff_usdt_observe=max_realized_pnl_diff_usdt_observe,
        max_commission_abs_diff_usdt_reduce=max_commission_abs_diff_usdt_reduce,
        max_commission_abs_diff_usdt_observe=max_commission_abs_diff_usdt_observe,
        max_quote_qty_abs_diff_usdt_reduce=max_quote_qty_abs_diff_usdt_reduce,
        max_quote_qty_abs_diff_usdt_observe=max_quote_qty_abs_diff_usdt_observe,
        max_income_trade_realized_pnl_diff_usdt_reduce=max_income_trade_realized_pnl_diff_usdt_reduce,
        max_income_trade_realized_pnl_diff_usdt_observe=max_income_trade_realized_pnl_diff_usdt_observe,
        max_income_trade_link_gap_ratio_reduce=max_income_trade_link_gap_ratio_reduce,
        max_income_trade_link_gap_ratio_observe=max_income_trade_link_gap_ratio_observe,
        reduce_size_multiplier=reduce_size_multiplier,
    )
    daemon_config = TradeReconciliationDaemonConfig(
        runtime_state_path=Path(runtime_state_path or (config.data_dir / "private" / "state" / "latest.json")),
        lookback_ms=max(1, int(lookback_minutes)) * 60 * 1000,
        thresholds=thresholds,
        session_state_path=Path(session_state_path) if session_state_path is not None else (config.data_dir / "live" / "status" / "latest.json"),
        authoritative_archive_root=(Path(authoritative_archive_root) if authoritative_archive_root is not None else config.data_dir),
        prefer_authoritative_archive=prefer_authoritative_archive,
        hydrate_archive_gaps=hydrate_archive_gaps,
        income_window_ms=max(1, int(income_window_days)) * 24 * 60 * 60 * 1000,
    )
    with JSONLWriter(config.data_dir) as writer:
        daemon = TradeReconciliationDaemon(
            config,
            client=client,
            writer=writer,
            daemon_config=daemon_config,
        )
        status = await daemon.run(interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"daemon": "trade_reconciliation", "status": status, "config": daemon_config}))
    return 0


async def cmd_session_truth_watch(
    config: BotConfig,
    *,
    session_state_path: str | None,
    lookback_minutes: int,
    interval_seconds: float,
    max_iterations: int | None,
    authoritative_archive_root: str | None,
    prefer_authoritative_archive: bool,
    hydrate_archive_gaps: bool,
    income_window_days: int,
    min_exchange_trade_count: int,
    min_quote_qty_usdt: Decimal,
    max_negative_net_realized_pnl_usdt_reduce: Decimal,
    max_negative_net_realized_pnl_usdt_observe: Decimal,
    max_negative_net_realized_bps_reduce: Decimal,
    max_negative_net_realized_bps_observe: Decimal,
    max_negative_net_per_trade_usdt_reduce: Decimal,
    max_negative_net_per_trade_usdt_observe: Decimal,
    min_maker_ratio_reduce: Decimal,
    min_maker_ratio_observe: Decimal,
    max_commission_bps_reduce: Decimal,
    max_commission_bps_observe: Decimal,
    max_negative_funding_bps_reduce: Decimal,
    max_negative_funding_bps_observe: Decimal,
    reduce_size_multiplier: Decimal,
) -> int:
    if not config.has_api_credentials:
        print("session-truth-watch requires BINANCE_API_KEY and BINANCE_API_SECRET")
        return 1

    client = _build_client(config)
    thresholds = SessionTruthThresholds(
        min_exchange_trade_count=min_exchange_trade_count,
        min_quote_qty_usdt=min_quote_qty_usdt,
        max_negative_net_realized_pnl_usdt_reduce=max_negative_net_realized_pnl_usdt_reduce,
        max_negative_net_realized_pnl_usdt_observe=max_negative_net_realized_pnl_usdt_observe,
        max_negative_net_realized_bps_reduce=max_negative_net_realized_bps_reduce,
        max_negative_net_realized_bps_observe=max_negative_net_realized_bps_observe,
        max_negative_net_per_trade_usdt_reduce=max_negative_net_per_trade_usdt_reduce,
        max_negative_net_per_trade_usdt_observe=max_negative_net_per_trade_usdt_observe,
        min_maker_ratio_reduce=min_maker_ratio_reduce,
        min_maker_ratio_observe=min_maker_ratio_observe,
        max_commission_bps_reduce=max_commission_bps_reduce,
        max_commission_bps_observe=max_commission_bps_observe,
        max_negative_funding_bps_reduce=max_negative_funding_bps_reduce,
        max_negative_funding_bps_observe=max_negative_funding_bps_observe,
        reduce_size_multiplier=reduce_size_multiplier,
    )
    daemon_config = SessionTruthDaemonConfig(
        lookback_ms=max(1, int(lookback_minutes)) * 60 * 1000,
        thresholds=thresholds,
        session_state_path=Path(session_state_path) if session_state_path is not None else (config.data_dir / "live" / "status" / "latest.json"),
        authoritative_archive_root=(Path(authoritative_archive_root) if authoritative_archive_root is not None else config.data_dir),
        prefer_authoritative_archive=prefer_authoritative_archive,
        hydrate_archive_gaps=hydrate_archive_gaps,
        income_window_ms=max(1, int(income_window_days)) * 24 * 60 * 60 * 1000,
    )
    with JSONLWriter(config.data_dir) as writer:
        daemon = SessionTruthDaemon(
            config,
            client=client,
            writer=writer,
            daemon_config=daemon_config,
        )
        status = await daemon.run(interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"daemon": "session_truth", "status": status, "config": daemon_config}))
    return 0


async def cmd_session_truth_trend_watch(
    config: BotConfig,
    *,
    report_path: str | None,
    interval_seconds: float,
    max_iterations: int | None,
    min_active_bucket_count: int,
    max_negative_bucket_ratio_reduce: Decimal,
    max_negative_bucket_ratio_observe: Decimal,
    consecutive_negative_buckets_reduce: int,
    consecutive_negative_buckets_observe: int,
    max_negative_recent_bucket_net_realized_bps_reduce: Decimal,
    max_negative_recent_bucket_net_realized_bps_observe: Decimal,
    max_negative_recent_two_bucket_net_realized_bps_reduce: Decimal,
    max_negative_recent_two_bucket_net_realized_bps_observe: Decimal,
    min_recent_bucket_maker_ratio_reduce: Decimal,
    min_recent_bucket_maker_ratio_observe: Decimal,
    max_negative_worst_bucket_net_realized_bps_reduce: Decimal,
    max_negative_worst_bucket_net_realized_bps_observe: Decimal,
    max_cumulative_drawdown_usdt_reduce: Decimal,
    max_cumulative_drawdown_usdt_observe: Decimal,
    reduce_size_multiplier: Decimal,
) -> int:
    thresholds = SessionTruthTrendThresholds(
        min_active_bucket_count=min_active_bucket_count,
        max_negative_bucket_ratio_reduce=max_negative_bucket_ratio_reduce,
        max_negative_bucket_ratio_observe=max_negative_bucket_ratio_observe,
        consecutive_negative_buckets_reduce=consecutive_negative_buckets_reduce,
        consecutive_negative_buckets_observe=consecutive_negative_buckets_observe,
        max_negative_recent_bucket_net_realized_bps_reduce=max_negative_recent_bucket_net_realized_bps_reduce,
        max_negative_recent_bucket_net_realized_bps_observe=max_negative_recent_bucket_net_realized_bps_observe,
        max_negative_recent_two_bucket_net_realized_bps_reduce=max_negative_recent_two_bucket_net_realized_bps_reduce,
        max_negative_recent_two_bucket_net_realized_bps_observe=max_negative_recent_two_bucket_net_realized_bps_observe,
        min_recent_bucket_maker_ratio_reduce=min_recent_bucket_maker_ratio_reduce,
        min_recent_bucket_maker_ratio_observe=min_recent_bucket_maker_ratio_observe,
        max_negative_worst_bucket_net_realized_bps_reduce=max_negative_worst_bucket_net_realized_bps_reduce,
        max_negative_worst_bucket_net_realized_bps_observe=max_negative_worst_bucket_net_realized_bps_observe,
        max_cumulative_drawdown_usdt_reduce=max_cumulative_drawdown_usdt_reduce,
        max_cumulative_drawdown_usdt_observe=max_cumulative_drawdown_usdt_observe,
        reduce_size_multiplier=reduce_size_multiplier,
    )
    daemon_config = SessionTruthTrendDaemonConfig(
        report_path=Path(report_path) if report_path is not None else (config.data_dir / "live" / "reports" / "latest_session_truth_report.json"),
        thresholds=thresholds,
    )
    with JSONLWriter(config.data_dir) as writer:
        daemon = SessionTruthTrendDaemon(config, writer=writer, daemon_config=daemon_config)
        status = await daemon.run(interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"daemon": "session_truth_trend", "status": status, "config": daemon_config}))
    return 0


async def cmd_economics_regime_watch(
    config: BotConfig,
    *,
    lookback_days: int,
    end_date: str | None,
    interval_seconds: float,
    max_iterations: int | None,
    min_active_day_count: int,
    max_negative_day_ratio_reduce: Decimal,
    max_negative_day_ratio_observe: Decimal,
    consecutive_negative_days_reduce: int,
    consecutive_negative_days_observe: int,
    max_negative_recent_day_net_realized_bps_reduce: Decimal,
    max_negative_recent_day_net_realized_bps_observe: Decimal,
    max_negative_recent_two_day_net_realized_bps_reduce: Decimal,
    max_negative_recent_two_day_net_realized_bps_observe: Decimal,
    min_average_maker_ratio_reduce: Decimal,
    min_average_maker_ratio_observe: Decimal,
    max_average_commission_bps_reduce: Decimal,
    max_average_commission_bps_observe: Decimal,
    max_negative_average_funding_bps_reduce: Decimal,
    max_negative_average_funding_bps_observe: Decimal,
    max_average_negative_bucket_ratio_reduce: Decimal,
    max_average_negative_bucket_ratio_observe: Decimal,
    max_cumulative_drawdown_usdt_reduce: Decimal,
    max_cumulative_drawdown_usdt_observe: Decimal,
    reduce_size_multiplier: Decimal,
) -> int:
    thresholds = EconomicsRegimeThresholds(
        min_active_day_count=min_active_day_count,
        max_negative_day_ratio_reduce=max_negative_day_ratio_reduce,
        max_negative_day_ratio_observe=max_negative_day_ratio_observe,
        consecutive_negative_days_reduce=consecutive_negative_days_reduce,
        consecutive_negative_days_observe=consecutive_negative_days_observe,
        max_negative_recent_day_net_realized_bps_reduce=max_negative_recent_day_net_realized_bps_reduce,
        max_negative_recent_day_net_realized_bps_observe=max_negative_recent_day_net_realized_bps_observe,
        max_negative_recent_two_day_net_realized_bps_reduce=max_negative_recent_two_day_net_realized_bps_reduce,
        max_negative_recent_two_day_net_realized_bps_observe=max_negative_recent_two_day_net_realized_bps_observe,
        min_average_maker_ratio_reduce=min_average_maker_ratio_reduce,
        min_average_maker_ratio_observe=min_average_maker_ratio_observe,
        max_average_commission_bps_reduce=max_average_commission_bps_reduce,
        max_average_commission_bps_observe=max_average_commission_bps_observe,
        max_negative_average_funding_bps_reduce=max_negative_average_funding_bps_reduce,
        max_negative_average_funding_bps_observe=max_negative_average_funding_bps_observe,
        max_average_negative_bucket_ratio_reduce=max_average_negative_bucket_ratio_reduce,
        max_average_negative_bucket_ratio_observe=max_average_negative_bucket_ratio_observe,
        max_cumulative_drawdown_usdt_reduce=max_cumulative_drawdown_usdt_reduce,
        max_cumulative_drawdown_usdt_observe=max_cumulative_drawdown_usdt_observe,
        reduce_size_multiplier=reduce_size_multiplier,
    )
    daemon_config = EconomicsRegimeDaemonConfig(
        lookback_days=lookback_days,
        thresholds=thresholds,
        end_date=end_date,
    )
    with JSONLWriter(config.data_dir) as writer:
        daemon = EconomicsRegimeDaemon(config, writer=writer, daemon_config=daemon_config)
        status = await daemon.run(interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"daemon": "economics_regime", "status": status, "config": daemon_config}))
    return 0


async def cmd_combined_protection_watch(
    config: BotConfig,
    *,
    execution_drift_guard_path: str | None,
    intraday_protection_guard_path: str | None,
    pnl_protection_guard_path: str | None,
    trade_reconciliation_guard_path: str | None,
    session_truth_guard_path: str | None,
    session_truth_trend_guard_path: str | None,
    economics_regime_guard_path: str | None,
    state_path: str | None,
    interval_seconds: float,
    max_iterations: int | None,
    observe_cooldown_seconds: int,
    min_trade_confirmations_to_relax_reduce: int,
    min_trade_confirmations_to_relax_observe: int,
    min_reduce_confirmations_to_relax_observe: int,
    multisource_reduce_size_multiplier: Decimal,
) -> int:
    thresholds = CombinedProtectionThresholds(
        observe_cooldown_seconds=observe_cooldown_seconds,
        min_trade_confirmations_to_relax_reduce=min_trade_confirmations_to_relax_reduce,
        min_trade_confirmations_to_relax_observe=min_trade_confirmations_to_relax_observe,
        min_reduce_confirmations_to_relax_observe=min_reduce_confirmations_to_relax_observe,
        multisource_reduce_size_multiplier=multisource_reduce_size_multiplier,
    )
    daemon_config = CombinedProtectionDaemonConfig(
        execution_drift_guard_path=Path(
            execution_drift_guard_path or (config.data_dir / "live" / "guards" / "latest_execution_drift.json")
        ),
        intraday_protection_guard_path=Path(
            intraday_protection_guard_path or (config.data_dir / "live" / "guards" / "latest_intraday_protection.json")
        ),
        pnl_protection_guard_path=Path(
            pnl_protection_guard_path or (config.data_dir / "live" / "guards" / "latest_pnl_protection.json")
        ),
        trade_reconciliation_guard_path=Path(
            trade_reconciliation_guard_path or (config.data_dir / "live" / "guards" / "latest_trade_reconciliation.json")
        ),
        session_truth_guard_path=Path(
            session_truth_guard_path or (config.data_dir / "live" / "guards" / "latest_session_truth.json")
        ),
        session_truth_trend_guard_path=Path(
            session_truth_trend_guard_path or (config.data_dir / "live" / "guards" / "latest_session_truth_trend.json")
        ),
        economics_regime_guard_path=Path(
            economics_regime_guard_path or (config.data_dir / "live" / "guards" / "latest_economics_regime.json")
        ),
        state_path=Path(state_path) if state_path is not None else Path("live/guards/latest_combined_protection_state.json"),
        thresholds=thresholds,
    )
    with JSONLWriter(config.data_dir) as writer:
        daemon = CombinedProtectionDaemon(config, writer=writer, daemon_config=daemon_config)
        status = await daemon.run(interval_seconds=interval_seconds, max_iterations=max_iterations)
        print(_json_dump({"daemon": "combined_protection", "status": status, "config": daemon_config}))
    return 0


async def cmd_run_breakout_loop(
    config: BotConfig,
    *,
    max_messages: int | None,
    strategy_kind: str,
    lookback: int,
    atr_window: int,
    reversion_lookback: int | None,
    reversion_entry_atr_multiple: Decimal,
    reversion_max_atr_fraction: Decimal | None,
    reversion_min_flow_flip: Decimal,
    router_range_max_atr_fraction: Decimal,
    router_trend_min_atr_fraction: Decimal,
    router_trend_min_abs_flow_imbalance: Decimal,
    router_range_max_abs_flow_imbalance: Decimal,
    router_neutral_preference: str,
    router_opportunistic_fallback: bool,
    entry_timeout: int,
    hold_seconds: int,
    position_notional: Decimal,
    reconcile_interval_seconds: float,
    max_reconcile_staleness_ms: int | None,
    trade_flow_window_seconds: int,
    min_recent_agg_trades: int,
    min_flow_imbalance: Decimal,
    max_mark_trade_divergence_bps: Decimal | None,
    max_positive_funding_rate: Decimal | None,
    min_negative_funding_rate: Decimal | None,
    crowding_period: str,
    crowding_interval_seconds: float,
    max_crowding_snapshot_age_seconds: int | None,
    min_crowding_score: Decimal | None,
    crowding_oi_expansion_weight: Decimal,
    with_book_ticker: bool,
    with_depth_book: bool,
    with_rpi_depth_book: bool,
    use_rpi_depth_if_available: bool,
    max_book_spread_bps: Decimal | None,
    max_book_ticker_staleness_ms: int | None,
    max_depth_snapshot_staleness_ms: int | None,
    min_depth_imbalance: Decimal | None,
    min_notional_multiplier: Decimal,
    max_notional_multiplier: Decimal,
    abstain_below_multiplier: Decimal,
    min_effective_notional_usdt: Decimal,
    sizing_flow_weight: Decimal,
    sizing_crowding_weight: Decimal,
    sizing_divergence_penalty_weight: Decimal,
    sizing_funding_penalty_weight: Decimal,
    sizing_divergence_penalty_cap_bps: Decimal,
    sizing_funding_penalty_cap_rate: Decimal,
    volatility_target_atr_fraction: Decimal | None,
    volatility_abstain_above_atr_fraction: Decimal | None,
    volatility_min_notional_multiplier: Decimal,
    volatility_max_notional_multiplier: Decimal,
    min_expected_fill_ratio: Decimal | None,
    max_expected_queue_clear_seconds: Decimal | None,
    max_queue_ahead_to_order_ratio: Decimal | None,
    min_directional_queue_flow_qty_per_second: Decimal,
    min_exit_depth_coverage_ratio: Decimal | None,
    max_exit_depth_sweep_bps: Decimal | None,
    exit_depth_tail_penalty_bps: Decimal,
    synthetic_tail_levels: int,
    synthetic_tail_replenishment_ratio: Decimal,
    synthetic_tail_step_bps: Decimal,
    require_contract_trading_status: bool,
    with_private: bool,
    with_reconcile: bool,
    with_crowding: bool,
    heal_on_reconcile: bool,
    targeted_heal_on_reconcile: bool,
    execution_drift_guard_path: str | None,
    intraday_protection_guard_path: str | None,
    pnl_protection_guard_path: str | None,
    trade_reconciliation_guard_path: str | None,
    session_truth_guard_path: str | None,
    session_truth_trend_guard_path: str | None,
    economics_regime_guard_path: str | None,
    economics_dashboard_path: str | None,
    economics_feedback_enabled: bool,
    economics_feedback_min_active_day_count: int,
    economics_feedback_min_multiplier: Decimal,
    combined_protection_guard_path: str | None,
    send: bool,
    test_orders: bool,
) -> int:
    if send and not config.has_api_credentials:
        print("run-breakout-loop with --send requires BINANCE_API_KEY and BINANCE_API_SECRET")
        return 1
    if send and not with_reconcile:
        print("run-breakout-loop with --send requires --with-reconcile")
        return 1
    if send and max_reconcile_staleness_ms is None:
        print("run-breakout-loop with --send requires an explicit --max-reconcile-staleness-ms")
        return 1

    client = _build_client(config)
    try:
        validator = _build_validator(client, config.symbol)
    except Exception:  # noqa: BLE001
        validator = None
    store = StateStore()
    gateway = ExecutionGateway(config, client=client, store=store, validator=validator)
    with JSONLWriter(config.data_dir) as writer:
        runner = LiveBreakoutRunner(
            config,
            client=client,
            store=store,
            writer=writer,
            gateway=gateway,
            live_config=LiveBreakoutConfig(
                strategy_kind=strategy_kind,
                lookback_ticks=lookback,
                atr_window_ticks=atr_window,
                reversion_lookback_ticks=reversion_lookback,
                reversion_entry_atr_multiple=reversion_entry_atr_multiple,
                reversion_max_atr_fraction=reversion_max_atr_fraction,
                reversion_min_flow_flip=reversion_min_flow_flip,
                router_range_max_atr_fraction=router_range_max_atr_fraction,
                router_trend_min_atr_fraction=router_trend_min_atr_fraction,
                router_trend_min_abs_flow_imbalance=router_trend_min_abs_flow_imbalance,
                router_range_max_abs_flow_imbalance=router_range_max_abs_flow_imbalance,
                router_neutral_preference=router_neutral_preference,
                router_opportunistic_fallback=router_opportunistic_fallback,
                entry_timeout_seconds=entry_timeout,
                hold_seconds=hold_seconds,
                position_notional_usdt=position_notional,
                reconcile_interval_seconds=reconcile_interval_seconds,
                max_reconcile_staleness_ms=max_reconcile_staleness_ms,
                trade_flow_window_seconds=trade_flow_window_seconds,
                min_recent_agg_trades=min_recent_agg_trades,
                min_flow_imbalance=min_flow_imbalance,
                max_mark_trade_divergence_bps=max_mark_trade_divergence_bps,
                max_positive_funding_rate=max_positive_funding_rate,
                min_negative_funding_rate=min_negative_funding_rate,
                require_contract_trading_status=require_contract_trading_status,
                with_private_consumer=with_private,
                with_reconcile_daemon=with_reconcile,
                with_crowding_collector=with_crowding,
                with_book_ticker_collector=with_book_ticker,
                with_depth_book_collector=with_depth_book,
                with_rpi_depth_book_collector=with_rpi_depth_book,
                use_rpi_depth_if_available=use_rpi_depth_if_available,
                heal_on_reconcile_divergence=heal_on_reconcile,
                targeted_heal_on_reconcile_divergence=targeted_heal_on_reconcile,
                crowding_period=crowding_period,
                crowding_interval_seconds=crowding_interval_seconds,
                max_crowding_snapshot_age_seconds=max_crowding_snapshot_age_seconds,
                min_crowding_score=min_crowding_score,
                crowding_oi_expansion_weight=crowding_oi_expansion_weight,
                max_book_spread_bps=max_book_spread_bps,
                max_book_ticker_staleness_ms=max_book_ticker_staleness_ms,
                max_depth_snapshot_staleness_ms=max_depth_snapshot_staleness_ms,
                min_depth_imbalance=min_depth_imbalance,
                min_notional_multiplier=min_notional_multiplier,
                max_notional_multiplier=max_notional_multiplier,
                abstain_below_multiplier=abstain_below_multiplier,
                min_effective_notional_usdt=min_effective_notional_usdt,
                sizing_flow_weight=sizing_flow_weight,
                sizing_crowding_weight=sizing_crowding_weight,
                sizing_divergence_penalty_weight=sizing_divergence_penalty_weight,
                sizing_funding_penalty_weight=sizing_funding_penalty_weight,
                sizing_divergence_penalty_cap_bps=sizing_divergence_penalty_cap_bps,
                sizing_funding_penalty_cap_rate=sizing_funding_penalty_cap_rate,
                volatility_target_atr_fraction=volatility_target_atr_fraction,
                volatility_abstain_above_atr_fraction=volatility_abstain_above_atr_fraction,
                volatility_min_notional_multiplier=volatility_min_notional_multiplier,
                volatility_max_notional_multiplier=volatility_max_notional_multiplier,
                min_expected_fill_ratio=min_expected_fill_ratio,
                max_expected_queue_clear_seconds=max_expected_queue_clear_seconds,
                max_queue_ahead_to_order_ratio=max_queue_ahead_to_order_ratio,
                min_directional_queue_flow_qty_per_second=min_directional_queue_flow_qty_per_second,
                min_exit_depth_coverage_ratio=min_exit_depth_coverage_ratio,
                max_exit_depth_sweep_bps=max_exit_depth_sweep_bps,
                exit_depth_tail_penalty_bps=exit_depth_tail_penalty_bps,
                synthetic_tail_levels=synthetic_tail_levels,
                synthetic_tail_replenishment_ratio=synthetic_tail_replenishment_ratio,
                synthetic_tail_step_bps=synthetic_tail_step_bps,
                execution_drift_guard_path=execution_drift_guard_path,
                intraday_protection_guard_path=intraday_protection_guard_path,
                pnl_protection_guard_path=pnl_protection_guard_path,
                trade_reconciliation_guard_path=trade_reconciliation_guard_path,
                session_truth_guard_path=session_truth_guard_path,
                session_truth_trend_guard_path=session_truth_trend_guard_path,
                economics_regime_guard_path=economics_regime_guard_path,
                economics_dashboard_path=economics_dashboard_path,
                economics_feedback_enabled=economics_feedback_enabled,
                economics_feedback_min_active_day_count=economics_feedback_min_active_day_count,
                economics_feedback_min_multiplier=economics_feedback_min_multiplier,
                combined_protection_guard_path=combined_protection_guard_path,
                send_orders=send,
                test_orders=test_orders,
            ),
        )
        status = await runner.run(stop_after_messages=max_messages)
        print(_json_dump({"strategy_kind": strategy_kind, "runner": status, "execution_quality": build_live_execution_quality_report(status), "runtime_state": store.snapshot(), "data_dir": config.data_dir}))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="BTCUSDT Binance USDⓈ-M bot skeleton")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("snapshot", help="Fetch exchange and account snapshot")
    subparsers.add_parser("market-manifest", help="Show planned market streams and routed URL")
    subparsers.add_parser("bootstrap-sync", help="Fetch signed bootstrap snapshot and rebuild runtime state")

    plan_parser = subparsers.add_parser("plan-example", help="Build an example entry + exit plan")
    plan_parser.add_argument("--side", required=True, choices=["BUY", "SELL"])
    plan_parser.add_argument("--mark-price", required=True, type=Decimal)
    plan_parser.add_argument("--qty", required=True, type=Decimal)
    plan_parser.add_argument("--atr", required=True, type=Decimal)

    validate_parser = subparsers.add_parser("validate-example", help="Validate a limit order example")
    validate_parser.add_argument("--price", required=True, type=Decimal)
    validate_parser.add_argument("--qty", required=True, type=Decimal)
    validate_parser.add_argument("--mark-price", required=True, type=Decimal)

    collect_market_parser = subparsers.add_parser("collect-market", help="Run market data collector")
    collect_market_parser.add_argument("--max-messages", type=int, default=None)

    collect_book_ticker_parser = subparsers.add_parser("collect-book-ticker", help="Run top-of-book collector")
    collect_book_ticker_parser.add_argument("--max-messages", type=int, default=None)

    collect_depth_parser = subparsers.add_parser("collect-depth-book", help="Run diff-depth collector with REST snapshot sync")
    collect_depth_parser.add_argument("--max-messages", type=int, default=None)
    collect_depth_parser.add_argument("--depth-levels", type=int, default=20)
    collect_depth_parser.add_argument("--snapshot-limit", type=int, default=1000)

    collect_rpi_depth_parser = subparsers.add_parser("collect-rpi-depth-book", help="Run RPI diff-depth collector with REST RPI snapshot sync")
    collect_rpi_depth_parser.add_argument("--max-messages", type=int, default=None)
    collect_rpi_depth_parser.add_argument("--depth-levels", type=int, default=20)
    collect_rpi_depth_parser.add_argument("--snapshot-limit", type=int, default=1000)

    collect_crowding_parser = subparsers.add_parser("collect-crowding", help="Fetch periodic futures crowding snapshots via REST")
    collect_crowding_parser.add_argument("--period", default="5m", choices=["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"])
    collect_crowding_parser.add_argument("--interval-seconds", type=float, default=30.0)
    collect_crowding_parser.add_argument("--max-iterations", type=int, default=None)

    consume_private_parser = subparsers.add_parser("consume-private", help="Run private user-data consumer")
    consume_private_parser.add_argument("--max-messages", type=int, default=None)

    query_normal_parser = subparsers.add_parser("query-normal", help="Query a normal order by orderId or client id and hydrate local state")
    query_normal_parser.add_argument("--order-id", type=int, default=None)
    query_normal_parser.add_argument("--client-order-id", default=None)

    query_algo_parser = subparsers.add_parser("query-algo", help="Query an algo order by algoId or client id and hydrate local state")
    query_algo_parser.add_argument("--algo-id", type=int, default=None)
    query_algo_parser.add_argument("--client-algo-id", default=None)

    heartbeat_parser = subparsers.add_parser("heartbeat-watch", help="Run countdownCancelAll heartbeat loop")
    heartbeat_parser.add_argument("--interval-seconds", type=float, default=30.0)
    heartbeat_parser.add_argument("--max-iterations", type=int, default=None)
    heartbeat_parser.add_argument("--send", action="store_true")

    reconcile_parser = subparsers.add_parser("reconcile-watch", help="Periodically diff runtime state against signed REST bootstrap snapshots")
    reconcile_parser.add_argument("--interval-seconds", type=float, default=30.0)
    reconcile_parser.add_argument("--max-iterations", type=int, default=None)
    reconcile_parser.add_argument("--heal", action="store_true")
    reconcile_parser.add_argument("--targeted-heal", action="store_true")

    backfill_parser = subparsers.add_parser("backfill-authoritative-history", help="Download userTrades/income into a local authoritative archive with coverage manifest")
    backfill_parser.add_argument("--start-date", default=None, help="UTC start date YYYY-MM-DD")
    backfill_parser.add_argument("--end-date", default=None, help="UTC end date YYYY-MM-DD")
    backfill_parser.add_argument("--start-ms", type=int, default=None)
    backfill_parser.add_argument("--end-ms", type=int, default=None)
    backfill_parser.add_argument("--days", type=int, default=1, help="Fallback UTC day span when explicit window is not provided")
    backfill_parser.add_argument("--archive-root", default=None)
    backfill_parser.add_argument("--user-trade-limit", type=int, default=1000)
    backfill_parser.add_argument("--income-limit", type=int, default=1000)
    backfill_parser.add_argument("--income-window-days", type=int, default=7)
    backfill_parser.add_argument(
        "--user-trades-only",
        action="store_true",
        help="Fetch only /fapi/v1/userTrades; do not request or mark income-history coverage",
    )

    markout_parser = subparsers.add_parser(
        "post-fill-markout",
        help="Measure causal post-fill markout from authoritative fills and one explicit market-data root",
    )
    markout_parser.add_argument("--start-date", default=None, help="UTC start date YYYY-MM-DD")
    markout_parser.add_argument("--end-date", default=None, help="UTC end date YYYY-MM-DD")
    markout_parser.add_argument("--start-ms", type=int, default=None)
    markout_parser.add_argument("--end-ms", type=int, default=None)
    markout_parser.add_argument("--days", type=int, default=1)
    markout_parser.add_argument("--archive-root", default=None)
    markout_parser.add_argument("--market-root", required=True)
    markout_parser.add_argument(
        "--reference-source",
        choices=[BOOK_MID, MARK_PRICE],
        default=BOOK_MID,
    )
    markout_parser.add_argument("--horizon-seconds", type=int, required=True)
    markout_parser.add_argument("--max-pre-fill-age-ms", type=int, required=True)
    markout_parser.add_argument("--max-post-horizon-delay-ms", type=int, required=True)

    forward_markout_parser = subparsers.add_parser(
        "post-fill-forward-observer",
        help="Evaluate the immutable forward post-fill markout contract without creating signals or orders",
    )
    forward_markout_parser.add_argument(
        "--prereg-path",
        default="configs/POST_FILL_MARKOUT_FORWARD_PREREG_2026-07-14.json",
    )
    forward_markout_parser.add_argument("--project-root", default=".")

    aggregate_parser = subparsers.add_parser("aggregate-reports", help="Aggregate daily backtest/live/drift/intraday/pnl/combined/trade-reconciliation reports")
    aggregate_parser.add_argument("--date", default=None, help="UTC date bucket YYYY-MM-DD; defaults to today")

    drift_parser = subparsers.add_parser("execution-drift-watch", help="Evaluate live execution drift against backtest baseline")
    drift_parser.add_argument("--baseline-path", default=None)
    drift_parser.add_argument("--live-report-path", default=None)
    drift_parser.add_argument("--interval-seconds", type=float, default=30.0)
    drift_parser.add_argument("--max-iterations", type=int, default=None)
    drift_parser.add_argument("--min-expected-fill-ratio-factor-reduce", type=Decimal, default=Decimal("0.85"))
    drift_parser.add_argument("--min-expected-fill-ratio-factor-observe", type=Decimal, default=Decimal("0.65"))
    drift_parser.add_argument("--max-queue-clear-seconds-factor-reduce", type=Decimal, default=Decimal("1.50"))
    drift_parser.add_argument("--max-queue-clear-seconds-factor-observe", type=Decimal, default=Decimal("2.25"))
    drift_parser.add_argument("--max-exit-depth-sweep-bps-add-reduce", type=Decimal, default=Decimal("1.0"))
    drift_parser.add_argument("--max-exit-depth-sweep-bps-add-observe", type=Decimal, default=Decimal("2.0"))
    drift_parser.add_argument("--max-terminal-tail-ratio-reduce", type=Decimal, default=Decimal("0.05"))
    drift_parser.add_argument("--max-terminal-tail-ratio-observe", type=Decimal, default=Decimal("0.12"))

    intraday_guard_parser = subparsers.add_parser("intraday-protection-watch", help="Poll quantitative-rules and ADL pressure, then write a live guard")
    intraday_guard_parser.add_argument("--interval-seconds", type=float, default=30.0)
    intraday_guard_parser.add_argument("--max-iterations", type=int, default=None)
    intraday_guard_parser.add_argument("--max-quant-utilization-reduce", type=Decimal, default=Decimal("0.90"))
    intraday_guard_parser.add_argument("--max-quant-utilization-observe", type=Decimal, default=Decimal("0.97"))
    intraday_guard_parser.add_argument("--max-adl-quantile-reduce", type=int, default=3)
    intraday_guard_parser.add_argument("--max-adl-quantile-observe", type=int, default=4)
    intraday_guard_parser.add_argument("--with-adl", action=argparse.BooleanOptionalAction, default=True)

    pnl_guard_parser = subparsers.add_parser("pnl-protection-watch", help="Evaluate session equity, drawdown and unrealized-loss guards from runtime state")
    pnl_guard_parser.add_argument("--runtime-state-path", default=None)
    pnl_guard_parser.add_argument("--bootstrap-state-path", default=None)
    pnl_guard_parser.add_argument("--anchor-path", default=None)
    pnl_guard_parser.add_argument("--asset", default="USDT")
    pnl_guard_parser.add_argument("--interval-seconds", type=float, default=30.0)
    pnl_guard_parser.add_argument("--max-iterations", type=int, default=None)
    pnl_guard_parser.add_argument("--max-session-loss-fraction-reduce", type=Decimal, default=Decimal("0.010"))
    pnl_guard_parser.add_argument("--max-session-loss-fraction-observe", type=Decimal, default=Decimal("0.020"))
    pnl_guard_parser.add_argument("--max-drawdown-fraction-reduce", type=Decimal, default=Decimal("0.008"))
    pnl_guard_parser.add_argument("--max-drawdown-fraction-observe", type=Decimal, default=Decimal("0.015"))
    pnl_guard_parser.add_argument("--max-unrealized-loss-fraction-reduce", type=Decimal, default=Decimal("0.006"))
    pnl_guard_parser.add_argument("--max-unrealized-loss-fraction-observe", type=Decimal, default=Decimal("0.012"))
    pnl_guard_parser.add_argument("--max-session-loss-usdt-reduce", type=Decimal, default=None)
    pnl_guard_parser.add_argument("--max-session-loss-usdt-observe", type=Decimal, default=None)
    pnl_guard_parser.add_argument("--max-drawdown-usdt-reduce", type=Decimal, default=None)
    pnl_guard_parser.add_argument("--max-drawdown-usdt-observe", type=Decimal, default=None)
    trade_recon_parser = subparsers.add_parser("trade-reconciliation-watch", help="Reconcile local trade fills against exchange userTrades/income and write a live guard")
    trade_recon_parser.add_argument("--runtime-state-path", default=None)
    trade_recon_parser.add_argument("--session-state-path", default=None)
    trade_recon_parser.add_argument("--lookback-minutes", type=int, default=60)
    trade_recon_parser.add_argument("--interval-seconds", type=float, default=30.0)
    trade_recon_parser.add_argument("--max-iterations", type=int, default=None)
    trade_recon_parser.add_argument("--authoritative-archive-root", default=None)
    trade_recon_parser.add_argument("--prefer-authoritative-archive", action=argparse.BooleanOptionalAction, default=True)
    trade_recon_parser.add_argument("--hydrate-archive-gaps", action=argparse.BooleanOptionalAction, default=True)
    trade_recon_parser.add_argument("--income-window-days", type=int, default=7)
    trade_recon_parser.add_argument("--min-exchange-trade-count", type=int, default=1)
    trade_recon_parser.add_argument("--max-missing-local-trade-ratio-reduce", type=Decimal, default=Decimal("0.10"))
    trade_recon_parser.add_argument("--max-missing-local-trade-ratio-observe", type=Decimal, default=Decimal("0.25"))
    trade_recon_parser.add_argument("--max-unmatched-local-trade-ratio-reduce", type=Decimal, default=Decimal("0.10"))
    trade_recon_parser.add_argument("--max-unmatched-local-trade-ratio-observe", type=Decimal, default=Decimal("0.25"))
    trade_recon_parser.add_argument("--max-missing-local-order-ratio-reduce", type=Decimal, default=Decimal("0.10"))
    trade_recon_parser.add_argument("--max-missing-local-order-ratio-observe", type=Decimal, default=Decimal("0.25"))
    trade_recon_parser.add_argument("--max-unmatched-local-order-ratio-reduce", type=Decimal, default=Decimal("0.10"))
    trade_recon_parser.add_argument("--max-unmatched-local-order-ratio-observe", type=Decimal, default=Decimal("0.25"))
    trade_recon_parser.add_argument("--max-realized-pnl-diff-usdt-reduce", type=Decimal, default=Decimal("1.00"))
    trade_recon_parser.add_argument("--max-realized-pnl-diff-usdt-observe", type=Decimal, default=Decimal("3.00"))
    trade_recon_parser.add_argument("--max-commission-abs-diff-usdt-reduce", type=Decimal, default=Decimal("0.25"))
    trade_recon_parser.add_argument("--max-commission-abs-diff-usdt-observe", type=Decimal, default=Decimal("1.00"))
    trade_recon_parser.add_argument("--max-quote-qty-abs-diff-usdt-reduce", type=Decimal, default=Decimal("25.00"))
    trade_recon_parser.add_argument("--max-quote-qty-abs-diff-usdt-observe", type=Decimal, default=Decimal("100.00"))
    trade_recon_parser.add_argument("--max-income-trade-realized-pnl-diff-usdt-reduce", type=Decimal, default=Decimal("1.00"))
    trade_recon_parser.add_argument("--max-income-trade-realized-pnl-diff-usdt-observe", type=Decimal, default=Decimal("3.00"))
    trade_recon_parser.add_argument("--max-income-trade-link-gap-ratio-reduce", type=Decimal, default=Decimal("0.10"))
    trade_recon_parser.add_argument("--max-income-trade-link-gap-ratio-observe", type=Decimal, default=Decimal("0.25"))
    trade_recon_parser.add_argument("--reduce-size-multiplier", type=Decimal, default=Decimal("0.60"))

    session_truth_parser = subparsers.add_parser("session-truth-watch", help="Evaluate authoritative session economics from userTrades/income and write a live guard")
    session_truth_parser.add_argument("--session-state-path", default=None)
    session_truth_parser.add_argument("--lookback-minutes", type=int, default=60)
    session_truth_parser.add_argument("--interval-seconds", type=float, default=30.0)
    session_truth_parser.add_argument("--max-iterations", type=int, default=None)
    session_truth_parser.add_argument("--authoritative-archive-root", default=None)
    session_truth_parser.add_argument("--prefer-authoritative-archive", action=argparse.BooleanOptionalAction, default=True)
    session_truth_parser.add_argument("--hydrate-archive-gaps", action=argparse.BooleanOptionalAction, default=True)
    session_truth_parser.add_argument("--income-window-days", type=int, default=7)
    session_truth_parser.add_argument("--min-exchange-trade-count", type=int, default=3)
    session_truth_parser.add_argument("--min-quote-qty-usdt", type=Decimal, default=Decimal("1000"))
    session_truth_parser.add_argument("--max-negative-net-realized-pnl-usdt-reduce", type=Decimal, default=Decimal("2.50"))
    session_truth_parser.add_argument("--max-negative-net-realized-pnl-usdt-observe", type=Decimal, default=Decimal("10.00"))
    session_truth_parser.add_argument("--max-negative-net-realized-bps-reduce", type=Decimal, default=Decimal("1.00"))
    session_truth_parser.add_argument("--max-negative-net-realized-bps-observe", type=Decimal, default=Decimal("4.00"))
    session_truth_parser.add_argument("--max-negative-net-per-trade-usdt-reduce", type=Decimal, default=Decimal("0.25"))
    session_truth_parser.add_argument("--max-negative-net-per-trade-usdt-observe", type=Decimal, default=Decimal("1.00"))
    session_truth_parser.add_argument("--min-maker-ratio-reduce", type=Decimal, default=Decimal("0.40"))
    session_truth_parser.add_argument("--min-maker-ratio-observe", type=Decimal, default=Decimal("0.20"))
    session_truth_parser.add_argument("--max-commission-bps-reduce", type=Decimal, default=Decimal("6.00"))
    session_truth_parser.add_argument("--max-commission-bps-observe", type=Decimal, default=Decimal("10.00"))
    session_truth_parser.add_argument("--max-negative-funding-bps-reduce", type=Decimal, default=Decimal("0.50"))
    session_truth_parser.add_argument("--max-negative-funding-bps-observe", type=Decimal, default=Decimal("2.00"))
    session_truth_parser.add_argument("--reduce-size-multiplier", type=Decimal, default=Decimal("0.60"))

    session_truth_trend_parser = subparsers.add_parser("session-truth-trend-watch", help="Evaluate bucketed session-truth trend from the latest authoritative session report")
    session_truth_trend_parser.add_argument("--report-path", default=None)
    session_truth_trend_parser.add_argument("--interval-seconds", type=float, default=30.0)
    session_truth_trend_parser.add_argument("--max-iterations", type=int, default=None)
    session_truth_trend_parser.add_argument("--min-active-bucket-count", type=int, default=3)
    session_truth_trend_parser.add_argument("--max-negative-bucket-ratio-reduce", type=Decimal, default=Decimal("0.50"))
    session_truth_trend_parser.add_argument("--max-negative-bucket-ratio-observe", type=Decimal, default=Decimal("0.75"))
    session_truth_trend_parser.add_argument("--consecutive-negative-buckets-reduce", type=int, default=2)
    session_truth_trend_parser.add_argument("--consecutive-negative-buckets-observe", type=int, default=3)
    session_truth_trend_parser.add_argument("--max-negative-recent-bucket-net-realized-bps-reduce", type=Decimal, default=Decimal("1.00"))
    session_truth_trend_parser.add_argument("--max-negative-recent-bucket-net-realized-bps-observe", type=Decimal, default=Decimal("3.00"))
    session_truth_trend_parser.add_argument("--max-negative-recent-two-bucket-net-realized-bps-reduce", type=Decimal, default=Decimal("0.75"))
    session_truth_trend_parser.add_argument("--max-negative-recent-two-bucket-net-realized-bps-observe", type=Decimal, default=Decimal("2.50"))
    session_truth_trend_parser.add_argument("--min-recent-bucket-maker-ratio-reduce", type=Decimal, default=Decimal("0.35"))
    session_truth_trend_parser.add_argument("--min-recent-bucket-maker-ratio-observe", type=Decimal, default=Decimal("0.15"))
    session_truth_trend_parser.add_argument("--max-negative-worst-bucket-net-realized-bps-reduce", type=Decimal, default=Decimal("3.00"))
    session_truth_trend_parser.add_argument("--max-negative-worst-bucket-net-realized-bps-observe", type=Decimal, default=Decimal("8.00"))
    session_truth_trend_parser.add_argument("--max-cumulative-drawdown-usdt-reduce", type=Decimal, default=Decimal("5.00"))
    session_truth_trend_parser.add_argument("--max-cumulative-drawdown-usdt-observe", type=Decimal, default=Decimal("15.00"))
    session_truth_trend_parser.add_argument("--reduce-size-multiplier", type=Decimal, default=Decimal("0.60"))

    economics_regime_parser = subparsers.add_parser("economics-regime-watch", help="Evaluate multi-day economics regime from authoritative session-truth reports")
    economics_regime_parser.add_argument("--lookback-days", type=int, default=7)
    economics_regime_parser.add_argument("--end-date", default=None, help="UTC end date YYYY-MM-DD; defaults to today")
    economics_regime_parser.add_argument("--interval-seconds", type=float, default=300.0)
    economics_regime_parser.add_argument("--max-iterations", type=int, default=None)
    economics_regime_parser.add_argument("--min-active-day-count", type=int, default=3)
    economics_regime_parser.add_argument("--max-negative-day-ratio-reduce", type=Decimal, default=Decimal("0.50"))
    economics_regime_parser.add_argument("--max-negative-day-ratio-observe", type=Decimal, default=Decimal("0.75"))
    economics_regime_parser.add_argument("--consecutive-negative-days-reduce", type=int, default=2)
    economics_regime_parser.add_argument("--consecutive-negative-days-observe", type=int, default=3)
    economics_regime_parser.add_argument("--max-negative-recent-day-net-realized-bps-reduce", type=Decimal, default=Decimal("1.00"))
    economics_regime_parser.add_argument("--max-negative-recent-day-net-realized-bps-observe", type=Decimal, default=Decimal("3.00"))
    economics_regime_parser.add_argument("--max-negative-recent-two-day-net-realized-bps-reduce", type=Decimal, default=Decimal("0.75"))
    economics_regime_parser.add_argument("--max-negative-recent-two-day-net-realized-bps-observe", type=Decimal, default=Decimal("2.50"))
    economics_regime_parser.add_argument("--min-average-maker-ratio-reduce", type=Decimal, default=Decimal("0.35"))
    economics_regime_parser.add_argument("--min-average-maker-ratio-observe", type=Decimal, default=Decimal("0.15"))
    economics_regime_parser.add_argument("--max-average-commission-bps-reduce", type=Decimal, default=Decimal("6.00"))
    economics_regime_parser.add_argument("--max-average-commission-bps-observe", type=Decimal, default=Decimal("10.00"))
    economics_regime_parser.add_argument("--max-negative-average-funding-bps-reduce", type=Decimal, default=Decimal("0.50"))
    economics_regime_parser.add_argument("--max-negative-average-funding-bps-observe", type=Decimal, default=Decimal("2.00"))
    economics_regime_parser.add_argument("--max-average-negative-bucket-ratio-reduce", type=Decimal, default=Decimal("0.50"))
    economics_regime_parser.add_argument("--max-average-negative-bucket-ratio-observe", type=Decimal, default=Decimal("0.75"))
    economics_regime_parser.add_argument("--max-cumulative-drawdown-usdt-reduce", type=Decimal, default=Decimal("10.00"))
    economics_regime_parser.add_argument("--max-cumulative-drawdown-usdt-observe", type=Decimal, default=Decimal("25.00"))
    economics_regime_parser.add_argument("--reduce-size-multiplier", type=Decimal, default=Decimal("0.60"))

    combined_guard_parser = subparsers.add_parser("combined-protection-watch", help="Fuse execution, intraday, pnl and trade-reconciliation guards into one stateful live guard")
    combined_guard_parser.add_argument("--execution-drift-guard-path", default=None)
    combined_guard_parser.add_argument("--intraday-protection-guard-path", default=None)
    combined_guard_parser.add_argument("--pnl-protection-guard-path", default=None)
    combined_guard_parser.add_argument("--trade-reconciliation-guard-path", default=None)
    combined_guard_parser.add_argument("--session-truth-guard-path", default=None)
    combined_guard_parser.add_argument("--session-truth-trend-guard-path", default=None)
    combined_guard_parser.add_argument("--economics-regime-guard-path", default=None)
    combined_guard_parser.add_argument("--state-path", default=None)
    combined_guard_parser.add_argument("--interval-seconds", type=float, default=30.0)
    combined_guard_parser.add_argument("--max-iterations", type=int, default=None)
    combined_guard_parser.add_argument("--observe-cooldown-seconds", type=int, default=180)
    combined_guard_parser.add_argument("--min-trade-confirmations-to-relax-reduce", type=int, default=2)
    combined_guard_parser.add_argument("--min-trade-confirmations-to-relax-observe", type=int, default=3)
    combined_guard_parser.add_argument("--min-reduce-confirmations-to-relax-observe", type=int, default=2)
    combined_guard_parser.add_argument("--multisource-reduce-size-multiplier", type=Decimal, default=Decimal("0.50"))

    live_parser = subparsers.add_parser("run-breakout-loop", help="Run a futures strategy orchestration loop on markPrice@1s + aggTrade")
    live_parser.add_argument("--max-messages", type=int, default=None)
    live_parser.add_argument("--strategy", choices=["breakout", "reversion", "router", "ensemble"], default="breakout")
    live_parser.add_argument("--lookback", type=int, default=120)
    live_parser.add_argument("--atr-window", type=int, default=30)
    live_parser.add_argument("--reversion-lookback", type=int, default=None)
    live_parser.add_argument("--reversion-entry-atr-multiple", type=Decimal, default=Decimal("1.25"))
    live_parser.add_argument("--reversion-max-atr-fraction", type=Decimal, default=Decimal("0.0040"))
    live_parser.add_argument("--reversion-min-flow-flip", type=Decimal, default=Decimal("0"))
    live_parser.add_argument("--router-range-max-atr-fraction", type=Decimal, default=Decimal("0.0040"))
    live_parser.add_argument("--router-trend-min-atr-fraction", type=Decimal, default=Decimal("0.0060"))
    live_parser.add_argument("--router-trend-min-abs-flow-imbalance", type=Decimal, default=Decimal("0.20"))
    live_parser.add_argument("--router-range-max-abs-flow-imbalance", type=Decimal, default=Decimal("0.12"))
    live_parser.add_argument("--router-neutral-preference", choices=["breakout", "reversion"], default="breakout")
    live_parser.add_argument("--router-opportunistic-fallback", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--entry-timeout", type=int, default=5)
    live_parser.add_argument("--hold-seconds", type=int, default=300)
    live_parser.add_argument("--position-notional", type=Decimal, default=Decimal("100"))
    live_parser.add_argument("--reconcile-interval-seconds", type=float, default=30.0)
    live_parser.add_argument(
        "--max-reconcile-staleness-ms",
        type=int,
        default=None,
        help="Explicit trust budget for the last exchange reconciliation; required with --send.",
    )
    live_parser.add_argument("--trade-flow-window-seconds", type=int, default=10)
    live_parser.add_argument("--min-recent-agg-trades", type=int, default=0)
    live_parser.add_argument("--min-flow-imbalance", type=Decimal, default=Decimal("0"))
    live_parser.add_argument("--max-mark-trade-divergence-bps", type=Decimal, default=None)
    live_parser.add_argument("--max-positive-funding-rate", type=Decimal, default=None)
    live_parser.add_argument("--min-negative-funding-rate", type=Decimal, default=None)
    live_parser.add_argument("--crowding-period", default="5m", choices=["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"])
    live_parser.add_argument("--crowding-interval-seconds", type=float, default=30.0)
    live_parser.add_argument("--max-crowding-snapshot-age-seconds", type=int, default=None)
    live_parser.add_argument("--min-crowding-score", type=Decimal, default=None)
    live_parser.add_argument("--crowding-oi-expansion-weight", type=Decimal, default=Decimal("0.5"))
    live_parser.add_argument("--with-book-ticker", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--with-depth-book", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--with-rpi-depth-book", action=argparse.BooleanOptionalAction, default=False)
    live_parser.add_argument("--use-rpi-depth-if-available", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--max-book-spread-bps", type=Decimal, default=None)
    live_parser.add_argument("--max-book-ticker-staleness-ms", type=int, default=None)
    live_parser.add_argument("--max-depth-snapshot-staleness-ms", type=int, default=None)
    live_parser.add_argument("--min-depth-imbalance", type=Decimal, default=None)
    live_parser.add_argument("--min-notional-multiplier", type=Decimal, default=Decimal("0.35"))
    live_parser.add_argument("--max-notional-multiplier", type=Decimal, default=Decimal("1.75"))
    live_parser.add_argument("--abstain-below-multiplier", type=Decimal, default=Decimal("0.50"))
    live_parser.add_argument("--min-effective-notional-usdt", type=Decimal, default=Decimal("25"))
    live_parser.add_argument("--sizing-flow-weight", type=Decimal, default=Decimal("0.60"))
    live_parser.add_argument("--sizing-crowding-weight", type=Decimal, default=Decimal("0.40"))
    live_parser.add_argument("--sizing-divergence-penalty-weight", type=Decimal, default=Decimal("0.25"))
    live_parser.add_argument("--sizing-funding-penalty-weight", type=Decimal, default=Decimal("0.15"))
    live_parser.add_argument("--sizing-divergence-penalty-cap-bps", type=Decimal, default=Decimal("3.0"))
    live_parser.add_argument("--sizing-funding-penalty-cap-rate", type=Decimal, default=Decimal("0.0005"))
    live_parser.add_argument("--volatility-target-atr-fraction", type=Decimal, default=Decimal("0.0020"))
    live_parser.add_argument("--volatility-abstain-above-atr-fraction", type=Decimal, default=Decimal("0.0080"))
    live_parser.add_argument("--volatility-min-notional-multiplier", type=Decimal, default=Decimal("0.50"))
    live_parser.add_argument("--volatility-max-notional-multiplier", type=Decimal, default=Decimal("1.60"))
    live_parser.add_argument("--min-expected-fill-ratio", type=Decimal, default=Decimal("0.35"))
    live_parser.add_argument("--max-expected-queue-clear-seconds", type=Decimal, default=Decimal("4.0"))
    live_parser.add_argument("--max-queue-ahead-to-order-ratio", type=Decimal, default=Decimal("8.0"))
    live_parser.add_argument("--min-directional-queue-flow-qty-per-second", type=Decimal, default=Decimal("0.01"))
    live_parser.add_argument("--min-exit-depth-coverage-ratio", type=Decimal, default=Decimal("0.75"))
    live_parser.add_argument("--max-exit-depth-sweep-bps", type=Decimal, default=Decimal("3.0"))
    live_parser.add_argument("--exit-depth-tail-penalty-bps", type=Decimal, default=Decimal("5.0"))
    live_parser.add_argument("--synthetic-tail-levels", type=int, default=3)
    live_parser.add_argument("--synthetic-tail-replenishment-ratio", type=Decimal, default=Decimal("0.50"))
    live_parser.add_argument("--synthetic-tail-step-bps", type=Decimal, default=Decimal("1.0"))
    live_parser.add_argument("--require-contract-trading-status", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--combined-protection-guard-path", default=None)
    live_parser.add_argument("--with-private", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--with-reconcile", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--with-crowding", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--heal-on-reconcile", action="store_true")
    live_parser.add_argument("--targeted-heal-on-reconcile", action="store_true")
    live_parser.add_argument("--execution-drift-guard-path", default=None)
    live_parser.add_argument("--intraday-protection-guard-path", default=None)
    live_parser.add_argument("--pnl-protection-guard-path", default=None)
    live_parser.add_argument("--trade-reconciliation-guard-path", default=None)
    live_parser.add_argument("--session-truth-guard-path", default=None)
    live_parser.add_argument("--session-truth-trend-guard-path", default=None)
    live_parser.add_argument("--economics-regime-guard-path", default=None)
    live_parser.add_argument("--economics-dashboard-path", default=None)
    live_parser.add_argument("--economics-feedback-enabled", action=argparse.BooleanOptionalAction, default=True)
    live_parser.add_argument("--economics-feedback-min-active-day-count", type=int, default=3)
    live_parser.add_argument("--economics-feedback-min-multiplier", type=Decimal, default=Decimal("0.70"))
    live_parser.add_argument("--send", action="store_true")
    live_parser.add_argument("--test-orders", action="store_true")


    readiness_parser = subparsers.add_parser("backtest-readiness", help="Audit local JSONL coverage for mark-only vs multistream parity backtest")
    readiness_parser.add_argument("--start-date", default=None)
    readiness_parser.add_argument("--end-date", default=None)
    readiness_parser.add_argument("--mark-only", action="store_true")
    readiness_parser.add_argument("--crowding-period", default="5m", choices=["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"])
    readiness_parser.add_argument("--depth-levels", type=int, default=20)
    readiness_parser.add_argument("--use-rpi-depth-fills", action=argparse.BooleanOptionalAction, default=True)
    readiness_parser.add_argument("--ignore-contract-status", action="store_true")

    backtest_parser = subparsers.add_parser("backtest-breakout", help="Replay futures JSONL through a deterministic strategy backtester")
    backtest_parser.add_argument("--start-date", default=None)
    backtest_parser.add_argument("--end-date", default=None)
    backtest_parser.add_argument("--strategy", choices=["breakout", "reversion", "router", "ensemble"], default="breakout")
    backtest_parser.add_argument("--lookback", type=int, default=120)
    backtest_parser.add_argument("--atr-window", type=int, default=30)
    backtest_parser.add_argument("--reversion-lookback", type=int, default=None)
    backtest_parser.add_argument("--reversion-entry-atr-multiple", type=Decimal, default=Decimal("1.25"))
    backtest_parser.add_argument("--reversion-max-atr-fraction", type=Decimal, default=Decimal("0.0040"))
    backtest_parser.add_argument("--reversion-min-flow-flip", type=Decimal, default=Decimal("0"))
    backtest_parser.add_argument("--router-range-max-atr-fraction", type=Decimal, default=Decimal("0.0040"))
    backtest_parser.add_argument("--router-trend-min-atr-fraction", type=Decimal, default=Decimal("0.0060"))
    backtest_parser.add_argument("--router-trend-min-abs-flow-imbalance", type=Decimal, default=Decimal("0.20"))
    backtest_parser.add_argument("--router-range-max-abs-flow-imbalance", type=Decimal, default=Decimal("0.12"))
    backtest_parser.add_argument("--router-neutral-preference", choices=["breakout", "reversion"], default="breakout")
    backtest_parser.add_argument("--router-opportunistic-fallback", action=argparse.BooleanOptionalAction, default=True)
    backtest_parser.add_argument("--entry-timeout", type=int, default=5)
    backtest_parser.add_argument("--hold-seconds", type=int, default=300)
    backtest_parser.add_argument("--position-notional", type=Decimal, default=Decimal("100"))
    backtest_parser.add_argument("--spread-bps", type=Decimal, default=Decimal("0.8"))
    backtest_parser.add_argument("--taker-slippage-bps", type=Decimal, default=Decimal("0.8"))
    backtest_parser.add_argument("--maker-fee-bps", type=Decimal, default=Decimal("2.0"))
    backtest_parser.add_argument("--taker-fee-bps", type=Decimal, default=Decimal("5.0"))
    backtest_parser.add_argument("--trade-flow-window-seconds", type=int, default=10)
    backtest_parser.add_argument("--min-recent-agg-trades", type=int, default=0)
    backtest_parser.add_argument("--min-flow-imbalance", type=Decimal, default=Decimal("0"))
    backtest_parser.add_argument("--max-mark-trade-divergence-bps", type=Decimal, default=None)
    backtest_parser.add_argument("--max-positive-funding-rate", type=Decimal, default=None)
    backtest_parser.add_argument("--min-negative-funding-rate", type=Decimal, default=None)
    backtest_parser.add_argument("--crowding-period", default="5m", choices=["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"])
    backtest_parser.add_argument("--max-crowding-snapshot-age-seconds", type=int, default=None)
    backtest_parser.add_argument("--min-crowding-score", type=Decimal, default=None)
    backtest_parser.add_argument("--crowding-oi-expansion-weight", type=Decimal, default=Decimal("0.5"))
    backtest_parser.add_argument("--use-book-ticker-fills", action=argparse.BooleanOptionalAction, default=True)
    backtest_parser.add_argument("--use-local-depth-fills", action=argparse.BooleanOptionalAction, default=True)
    backtest_parser.add_argument("--use-rpi-depth-fills", action=argparse.BooleanOptionalAction, default=True)
    backtest_parser.add_argument("--max-book-spread-bps", type=Decimal, default=None)
    backtest_parser.add_argument(
        "--max-book-ticker-staleness-ms",
        type=int,
        default=None,
        help="Explicit book trust budget for gates and exit pricing; omit to disable book-based exits.",
    )
    backtest_parser.add_argument(
        "--max-depth-snapshot-staleness-ms",
        type=int,
        default=None,
        help="Explicit depth trust budget for gates and exit pricing; omit to disable depth-based exits.",
    )
    backtest_parser.add_argument("--min-depth-imbalance", type=Decimal, default=None)
    backtest_parser.add_argument("--depth-levels", type=int, default=20)
    backtest_parser.add_argument("--min-notional-multiplier", type=Decimal, default=Decimal("0.35"))
    backtest_parser.add_argument("--max-notional-multiplier", type=Decimal, default=Decimal("1.75"))
    backtest_parser.add_argument("--abstain-below-multiplier", type=Decimal, default=Decimal("0.50"))
    backtest_parser.add_argument("--min-effective-notional-usdt", type=Decimal, default=Decimal("25"))
    backtest_parser.add_argument("--sizing-flow-weight", type=Decimal, default=Decimal("0.60"))
    backtest_parser.add_argument("--sizing-crowding-weight", type=Decimal, default=Decimal("0.40"))
    backtest_parser.add_argument("--sizing-divergence-penalty-weight", type=Decimal, default=Decimal("0.25"))
    backtest_parser.add_argument("--sizing-funding-penalty-weight", type=Decimal, default=Decimal("0.15"))
    backtest_parser.add_argument("--sizing-divergence-penalty-cap-bps", type=Decimal, default=Decimal("3.0"))
    backtest_parser.add_argument("--sizing-funding-penalty-cap-rate", type=Decimal, default=Decimal("0.0005"))
    backtest_parser.add_argument("--volatility-target-atr-fraction", type=Decimal, default=Decimal("0.0020"))
    backtest_parser.add_argument("--volatility-abstain-above-atr-fraction", type=Decimal, default=Decimal("0.0080"))
    backtest_parser.add_argument("--volatility-min-notional-multiplier", type=Decimal, default=Decimal("0.50"))
    backtest_parser.add_argument("--volatility-max-notional-multiplier", type=Decimal, default=Decimal("1.60"))
    backtest_parser.add_argument("--min-expected-fill-ratio", type=Decimal, default=Decimal("0.35"))
    backtest_parser.add_argument("--max-expected-queue-clear-seconds", type=Decimal, default=Decimal("4.0"))
    backtest_parser.add_argument("--max-queue-ahead-to-order-ratio", type=Decimal, default=Decimal("8.0"))
    backtest_parser.add_argument("--min-directional-queue-flow-qty-per-second", type=Decimal, default=Decimal("0.01"))
    backtest_parser.add_argument("--min-exit-depth-coverage-ratio", type=Decimal, default=Decimal("0.75"))
    backtest_parser.add_argument("--max-exit-depth-sweep-bps", type=Decimal, default=Decimal("3.0"))
    backtest_parser.add_argument("--exit-depth-tail-penalty-bps", type=Decimal, default=Decimal("5.0"))
    backtest_parser.add_argument("--synthetic-tail-levels", type=int, default=3)
    backtest_parser.add_argument("--synthetic-tail-replenishment-ratio", type=Decimal, default=Decimal("0.50"))
    backtest_parser.add_argument("--synthetic-tail-step-bps", type=Decimal, default=Decimal("1.0"))
    backtest_parser.add_argument("--economics-lookback-days", type=int, default=7)
    backtest_parser.add_argument("--economics-feedback-enabled", action=argparse.BooleanOptionalAction, default=True)
    backtest_parser.add_argument("--economics-feedback-min-active-day-count", type=int, default=3)
    backtest_parser.add_argument("--economics-feedback-min-multiplier", type=Decimal, default=Decimal("0.70"))
    backtest_parser.add_argument("--economics-regime-enabled", action=argparse.BooleanOptionalAction, default=True)
    backtest_parser.add_argument("--economics-regime-min-active-day-count", type=int, default=3)
    backtest_parser.add_argument("--mark-only", action="store_true")
    backtest_parser.add_argument("--ignore-contract-status", action="store_true")

    walkforward_parser = subparsers.add_parser("walkforward-breakout", help="Run walk-forward optimization over strategy parameter candidates")
    walkforward_parser.add_argument("--start-date", default=None)
    walkforward_parser.add_argument("--end-date", default=None)
    walkforward_parser.add_argument("--strategy", choices=["breakout", "reversion", "router", "ensemble"], default="breakout")
    walkforward_parser.add_argument("--strategy-grid", default="breakout")
    walkforward_parser.add_argument("--train-days", type=int, default=5)
    walkforward_parser.add_argument("--test-days", type=int, default=2)
    walkforward_parser.add_argument("--step-days", type=int, default=None)
    walkforward_parser.add_argument("--anchored-train", action="store_true")
    walkforward_parser.add_argument("--max-folds", type=int, default=None)
    walkforward_parser.add_argument("--max-candidates", type=int, default=128)
    walkforward_parser.add_argument("--lookback", type=int, default=120)
    walkforward_parser.add_argument("--lookback-grid", default="120")
    walkforward_parser.add_argument("--atr-window", type=int, default=30)
    walkforward_parser.add_argument("--reversion-lookback", type=int, default=None)
    walkforward_parser.add_argument("--reversion-entry-atr-multiple", type=Decimal, default=Decimal("1.25"))
    walkforward_parser.add_argument("--reversion-entry-atr-multiple-grid", default="1.25")
    walkforward_parser.add_argument("--reversion-max-atr-fraction", type=Decimal, default=Decimal("0.0040"))
    walkforward_parser.add_argument("--reversion-max-atr-fraction-grid", default="0.0040")
    walkforward_parser.add_argument("--reversion-min-flow-flip", type=Decimal, default=Decimal("0"))
    walkforward_parser.add_argument("--reversion-min-flow-flip-grid", default="0")
    walkforward_parser.add_argument("--router-range-max-atr-fraction", type=Decimal, default=Decimal("0.0040"))
    walkforward_parser.add_argument("--router-trend-min-atr-fraction", type=Decimal, default=Decimal("0.0060"))
    walkforward_parser.add_argument("--router-trend-min-abs-flow-imbalance", type=Decimal, default=Decimal("0.20"))
    walkforward_parser.add_argument("--router-range-max-abs-flow-imbalance", type=Decimal, default=Decimal("0.12"))
    walkforward_parser.add_argument("--router-neutral-preference", choices=["breakout", "reversion"], default="breakout")
    walkforward_parser.add_argument("--router-opportunistic-fallback", action=argparse.BooleanOptionalAction, default=True)
    walkforward_parser.add_argument("--entry-timeout", type=int, default=5)
    walkforward_parser.add_argument("--hold-seconds", type=int, default=300)
    walkforward_parser.add_argument("--hold-seconds-grid", default="300")
    walkforward_parser.add_argument("--position-notional", type=Decimal, default=Decimal("100"))
    walkforward_parser.add_argument("--spread-bps", type=Decimal, default=Decimal("0.8"))
    walkforward_parser.add_argument("--taker-slippage-bps", type=Decimal, default=Decimal("0.8"))
    walkforward_parser.add_argument("--maker-fee-bps", type=Decimal, default=Decimal("2.0"))
    walkforward_parser.add_argument("--taker-fee-bps", type=Decimal, default=Decimal("5.0"))
    walkforward_parser.add_argument("--trade-flow-window-seconds", type=int, default=10)
    walkforward_parser.add_argument("--min-recent-agg-trades", type=int, default=0)
    walkforward_parser.add_argument("--min-flow-imbalance", type=Decimal, default=Decimal("0"))
    walkforward_parser.add_argument("--min-flow-imbalance-grid", default="0")
    walkforward_parser.add_argument("--max-mark-trade-divergence-bps", type=Decimal, default=None)
    walkforward_parser.add_argument("--max-positive-funding-rate", type=Decimal, default=None)
    walkforward_parser.add_argument("--min-negative-funding-rate", type=Decimal, default=None)
    walkforward_parser.add_argument("--crowding-period", default="5m", choices=["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"])
    walkforward_parser.add_argument("--max-crowding-snapshot-age-seconds", type=int, default=None)
    walkforward_parser.add_argument("--min-crowding-score", type=Decimal, default=None)
    walkforward_parser.add_argument("--min-crowding-score-grid", default="none")
    walkforward_parser.add_argument("--crowding-oi-expansion-weight", type=Decimal, default=Decimal("0.5"))
    walkforward_parser.add_argument("--use-book-ticker-fills", action=argparse.BooleanOptionalAction, default=True)
    walkforward_parser.add_argument("--use-local-depth-fills", action=argparse.BooleanOptionalAction, default=True)
    walkforward_parser.add_argument("--use-rpi-depth-fills", action=argparse.BooleanOptionalAction, default=True)
    walkforward_parser.add_argument("--max-book-spread-bps", type=Decimal, default=None)
    walkforward_parser.add_argument("--max-book-spread-bps-grid", default="none")
    walkforward_parser.add_argument(
        "--max-book-ticker-staleness-ms",
        type=int,
        default=None,
        help="Explicit book trust budget for gates and exit pricing; omit to disable book-based exits.",
    )
    walkforward_parser.add_argument(
        "--max-depth-snapshot-staleness-ms",
        type=int,
        default=None,
        help="Explicit depth trust budget for gates and exit pricing; omit to disable depth-based exits.",
    )
    walkforward_parser.add_argument("--min-depth-imbalance", type=Decimal, default=None)
    walkforward_parser.add_argument("--min-depth-imbalance-grid", default="none")
    walkforward_parser.add_argument("--depth-levels", type=int, default=20)
    walkforward_parser.add_argument("--min-notional-multiplier", type=Decimal, default=Decimal("0.35"))
    walkforward_parser.add_argument("--max-notional-multiplier", type=Decimal, default=Decimal("1.75"))
    walkforward_parser.add_argument("--abstain-below-multiplier", type=Decimal, default=Decimal("0.50"))
    walkforward_parser.add_argument("--min-effective-notional-usdt", type=Decimal, default=Decimal("25"))
    walkforward_parser.add_argument("--sizing-flow-weight", type=Decimal, default=Decimal("0.60"))
    walkforward_parser.add_argument("--sizing-crowding-weight", type=Decimal, default=Decimal("0.40"))
    walkforward_parser.add_argument("--sizing-divergence-penalty-weight", type=Decimal, default=Decimal("0.25"))
    walkforward_parser.add_argument("--sizing-funding-penalty-weight", type=Decimal, default=Decimal("0.15"))
    walkforward_parser.add_argument("--sizing-divergence-penalty-cap-bps", type=Decimal, default=Decimal("3.0"))
    walkforward_parser.add_argument("--sizing-funding-penalty-cap-rate", type=Decimal, default=Decimal("0.0005"))
    walkforward_parser.add_argument("--volatility-target-atr-fraction", type=Decimal, default=Decimal("0.0020"))
    walkforward_parser.add_argument("--volatility-abstain-above-atr-fraction", type=Decimal, default=Decimal("0.0080"))
    walkforward_parser.add_argument("--volatility-min-notional-multiplier", type=Decimal, default=Decimal("0.50"))
    walkforward_parser.add_argument("--volatility-max-notional-multiplier", type=Decimal, default=Decimal("1.60"))
    walkforward_parser.add_argument("--min-expected-fill-ratio", type=Decimal, default=Decimal("0.35"))
    walkforward_parser.add_argument("--min-expected-fill-ratio-grid", default="0.35")
    walkforward_parser.add_argument("--max-expected-queue-clear-seconds", type=Decimal, default=Decimal("4.0"))
    walkforward_parser.add_argument("--max-queue-ahead-to-order-ratio", type=Decimal, default=Decimal("8.0"))
    walkforward_parser.add_argument("--min-directional-queue-flow-qty-per-second", type=Decimal, default=Decimal("0.01"))
    walkforward_parser.add_argument("--min-exit-depth-coverage-ratio", type=Decimal, default=Decimal("0.75"))
    walkforward_parser.add_argument("--max-exit-depth-sweep-bps", type=Decimal, default=Decimal("3.0"))
    walkforward_parser.add_argument("--exit-depth-tail-penalty-bps", type=Decimal, default=Decimal("5.0"))
    walkforward_parser.add_argument("--synthetic-tail-levels", type=int, default=3)
    walkforward_parser.add_argument("--synthetic-tail-replenishment-ratio", type=Decimal, default=Decimal("0.50"))
    walkforward_parser.add_argument("--synthetic-tail-step-bps", type=Decimal, default=Decimal("1.0"))
    walkforward_parser.add_argument("--economics-lookback-days", type=int, default=7)
    walkforward_parser.add_argument("--economics-feedback-enabled", action=argparse.BooleanOptionalAction, default=True)
    walkforward_parser.add_argument("--economics-feedback-min-active-day-count", type=int, default=3)
    walkforward_parser.add_argument("--economics-feedback-min-multiplier", type=Decimal, default=Decimal("0.70"))
    walkforward_parser.add_argument("--economics-regime-enabled", action=argparse.BooleanOptionalAction, default=True)
    walkforward_parser.add_argument("--economics-regime-min-active-day-count", type=int, default=3)
    walkforward_parser.add_argument("--max-drawdown-penalty", type=Decimal, default=Decimal("0.50"))
    walkforward_parser.add_argument("--entry-timeout-rate-penalty", type=Decimal, default=Decimal("25"))
    walkforward_parser.add_argument("--exit-depth-sweep-bps-penalty", type=Decimal, default=Decimal("2"))
    walkforward_parser.add_argument("--min-trade-count", type=int, default=1)
    walkforward_parser.add_argument("--mark-only", action="store_true")
    walkforward_parser.add_argument("--ignore-contract-status", action="store_true")

    submit_normal_parser = subparsers.add_parser("submit-normal", help="Render or send a normal futures order")
    submit_normal_parser.add_argument("--side", required=True, choices=["BUY", "SELL"])
    submit_normal_parser.add_argument("--order-type", required=True, choices=["LIMIT", "MARKET"])
    submit_normal_parser.add_argument("--qty", required=True, type=Decimal)
    submit_normal_parser.add_argument("--price", type=Decimal, default=None)
    submit_normal_parser.add_argument("--mark-price", type=Decimal, default=None)
    submit_normal_parser.add_argument("--tif", default="GTX", choices=["GTC", "IOC", "FOK", "GTX", "GTD", "RPI"])
    submit_normal_parser.add_argument("--client-id", default="")
    submit_normal_parser.add_argument("--reduce-only", action="store_true")
    submit_normal_parser.add_argument("--send", action="store_true")
    submit_normal_parser.add_argument("--test", action="store_true")

    submit_algo_parser = subparsers.add_parser("submit-algo", help="Render or send an algo futures order")
    submit_algo_parser.add_argument("--side", required=True, choices=["BUY", "SELL"])
    submit_algo_parser.add_argument("--order-type", required=True, choices=["STOP_MARKET", "TAKE_PROFIT_MARKET", "STOP", "TAKE_PROFIT", "TRAILING_STOP_MARKET"])
    submit_algo_parser.add_argument("--qty", type=Decimal, default=None)
    submit_algo_parser.add_argument("--trigger-price", type=Decimal, default=Decimal("0"))
    submit_algo_parser.add_argument("--price", type=Decimal, default=None)
    submit_algo_parser.add_argument("--activate-price", type=Decimal, default=None)
    submit_algo_parser.add_argument("--callback-rate", type=Decimal, default=None)
    submit_algo_parser.add_argument("--tif", default="GTC", choices=["GTC", "IOC", "FOK", "GTX", "GTD"])
    submit_algo_parser.add_argument("--client-algo-id", default="")
    submit_algo_parser.add_argument("--reduce-only", action="store_true")
    submit_algo_parser.add_argument("--close-position", action="store_true")
    submit_algo_parser.add_argument("--price-protect", action="store_true")
    submit_algo_parser.add_argument("--send", action="store_true")

    cancel_normal_parser = subparsers.add_parser("cancel-normal", help="Render or send cancel for a normal order")
    cancel_normal_parser.add_argument("--order-id", type=int, default=None)
    cancel_normal_parser.add_argument("--client-order-id", default=None)
    cancel_normal_parser.add_argument("--send", action="store_true")

    cancel_algo_parser = subparsers.add_parser("cancel-algo", help="Render or send cancel for an algo order")
    cancel_algo_parser.add_argument("--algo-id", type=int, default=None)
    cancel_algo_parser.add_argument("--client-algo-id", default=None)
    cancel_algo_parser.add_argument("--send", action="store_true")

    args = parser.parse_args()
    load_env_file()
    config = BotConfig.from_env()
    _setup_logging(config)

    if args.command == "snapshot":
        return cmd_snapshot(config)
    if args.command == "market-manifest":
        return cmd_market_manifest(config)
    if args.command == "bootstrap-sync":
        return cmd_bootstrap_sync(config)
    if args.command == "plan-example":
        return cmd_plan_example(config, args.side, args.mark_price, args.qty, args.atr)
    if args.command == "validate-example":
        return cmd_validate_example(config, args.price, args.qty, args.mark_price)
    if args.command == "collect-market":
        return asyncio.run(cmd_collect_market(config, args.max_messages))
    if args.command == "collect-book-ticker":
        return asyncio.run(cmd_collect_book_ticker(config, args.max_messages))
    if args.command == "collect-depth-book":
        return asyncio.run(
            cmd_collect_depth_book(
                config,
                max_messages=args.max_messages,
                depth_levels=args.depth_levels,
                snapshot_limit=args.snapshot_limit,
            )
        )
    if args.command == "collect-rpi-depth-book":
        return asyncio.run(
            cmd_collect_rpi_depth_book(
                config,
                max_messages=args.max_messages,
                depth_levels=args.depth_levels,
                snapshot_limit=args.snapshot_limit,
            )
        )
    if args.command == "collect-crowding":
        return asyncio.run(
            cmd_collect_crowding(
                config,
                period=args.period,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
            )
        )
    if args.command == "consume-private":
        return asyncio.run(cmd_consume_private(config, args.max_messages))
    if args.command == "query-normal":
        return cmd_query_normal(config, order_id=args.order_id, client_order_id=args.client_order_id)
    if args.command == "query-algo":
        return cmd_query_algo(config, algo_id=args.algo_id, client_algo_id=args.client_algo_id)
    if args.command == "heartbeat-watch":
        return asyncio.run(
            cmd_heartbeat_watch(
                config,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                send=args.send,
            )
        )
    if args.command == "reconcile-watch":
        return asyncio.run(
            cmd_reconcile_watch(
                config,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                heal=args.heal,
                targeted_heal=args.targeted_heal,
            )
        )
    if args.command == "backfill-authoritative-history":
        return cmd_backfill_authoritative_history(
            config,
            start_date=args.start_date,
            end_date=args.end_date,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
            days=args.days,
            archive_root=args.archive_root,
            user_trade_limit=args.user_trade_limit,
            income_limit=args.income_limit,
            income_window_days=args.income_window_days,
            user_trades_only=args.user_trades_only,
        )
    if args.command == "post-fill-markout":
        return cmd_post_fill_markout(
            config,
            start_date=args.start_date,
            end_date=args.end_date,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
            days=args.days,
            archive_root=args.archive_root,
            market_root=args.market_root,
            reference_source=args.reference_source,
            horizon_seconds=args.horizon_seconds,
            max_pre_fill_age_ms=args.max_pre_fill_age_ms,
            max_post_horizon_delay_ms=args.max_post_horizon_delay_ms,
        )
    if args.command == "post-fill-forward-observer":
        return cmd_post_fill_forward_observer(
            config,
            prereg_path=args.prereg_path,
            project_root=args.project_root,
        )
    if args.command == "aggregate-reports":
        return cmd_aggregate_reports(config, date=args.date)
    if args.command == "execution-drift-watch":
        return asyncio.run(
            cmd_execution_drift_watch(
                config,
                baseline_path=args.baseline_path,
                live_report_path=args.live_report_path,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                min_expected_fill_ratio_factor_reduce=args.min_expected_fill_ratio_factor_reduce,
                min_expected_fill_ratio_factor_observe=args.min_expected_fill_ratio_factor_observe,
                max_queue_clear_seconds_factor_reduce=args.max_queue_clear_seconds_factor_reduce,
                max_queue_clear_seconds_factor_observe=args.max_queue_clear_seconds_factor_observe,
                max_exit_depth_sweep_bps_add_reduce=args.max_exit_depth_sweep_bps_add_reduce,
                max_exit_depth_sweep_bps_add_observe=args.max_exit_depth_sweep_bps_add_observe,
                max_terminal_tail_ratio_reduce=args.max_terminal_tail_ratio_reduce,
                max_terminal_tail_ratio_observe=args.max_terminal_tail_ratio_observe,
            )
        )
    if args.command == "intraday-protection-watch":
        return asyncio.run(
            cmd_intraday_protection_watch(
                config,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                max_quant_utilization_reduce=args.max_quant_utilization_reduce,
                max_quant_utilization_observe=args.max_quant_utilization_observe,
                max_adl_quantile_reduce=args.max_adl_quantile_reduce,
                max_adl_quantile_observe=args.max_adl_quantile_observe,
                with_adl=args.with_adl,
            )
        )
    if args.command == "pnl-protection-watch":
        return asyncio.run(
            cmd_pnl_protection_watch(
                config,
                runtime_state_path=args.runtime_state_path,
                bootstrap_state_path=args.bootstrap_state_path,
                anchor_path=args.anchor_path,
                asset=args.asset,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                max_session_loss_fraction_reduce=args.max_session_loss_fraction_reduce,
                max_session_loss_fraction_observe=args.max_session_loss_fraction_observe,
                max_drawdown_fraction_reduce=args.max_drawdown_fraction_reduce,
                max_drawdown_fraction_observe=args.max_drawdown_fraction_observe,
                max_unrealized_loss_fraction_reduce=args.max_unrealized_loss_fraction_reduce,
                max_unrealized_loss_fraction_observe=args.max_unrealized_loss_fraction_observe,
                max_session_loss_usdt_reduce=args.max_session_loss_usdt_reduce,
                max_session_loss_usdt_observe=args.max_session_loss_usdt_observe,
                max_drawdown_usdt_reduce=args.max_drawdown_usdt_reduce,
                max_drawdown_usdt_observe=args.max_drawdown_usdt_observe,
                max_unrealized_loss_usdt_reduce=args.max_unrealized_loss_usdt_reduce,
                max_unrealized_loss_usdt_observe=args.max_unrealized_loss_usdt_observe,
            )
        )
    if args.command == "trade-reconciliation-watch":
        return asyncio.run(
            cmd_trade_reconciliation_watch(
                config,
                runtime_state_path=args.runtime_state_path,
                session_state_path=args.session_state_path,
                lookback_minutes=args.lookback_minutes,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                authoritative_archive_root=args.authoritative_archive_root,
                prefer_authoritative_archive=args.prefer_authoritative_archive,
                hydrate_archive_gaps=args.hydrate_archive_gaps,
                income_window_days=args.income_window_days,
                min_exchange_trade_count=args.min_exchange_trade_count,
                max_missing_local_trade_ratio_reduce=args.max_missing_local_trade_ratio_reduce,
                max_missing_local_trade_ratio_observe=args.max_missing_local_trade_ratio_observe,
                max_unmatched_local_trade_ratio_reduce=args.max_unmatched_local_trade_ratio_reduce,
                max_unmatched_local_trade_ratio_observe=args.max_unmatched_local_trade_ratio_observe,
                max_missing_local_order_ratio_reduce=args.max_missing_local_order_ratio_reduce,
                max_missing_local_order_ratio_observe=args.max_missing_local_order_ratio_observe,
                max_unmatched_local_order_ratio_reduce=args.max_unmatched_local_order_ratio_reduce,
                max_unmatched_local_order_ratio_observe=args.max_unmatched_local_order_ratio_observe,
                max_realized_pnl_diff_usdt_reduce=args.max_realized_pnl_diff_usdt_reduce,
                max_realized_pnl_diff_usdt_observe=args.max_realized_pnl_diff_usdt_observe,
                max_commission_abs_diff_usdt_reduce=args.max_commission_abs_diff_usdt_reduce,
                max_commission_abs_diff_usdt_observe=args.max_commission_abs_diff_usdt_observe,
                max_quote_qty_abs_diff_usdt_reduce=args.max_quote_qty_abs_diff_usdt_reduce,
                max_quote_qty_abs_diff_usdt_observe=args.max_quote_qty_abs_diff_usdt_observe,
                max_income_trade_realized_pnl_diff_usdt_reduce=args.max_income_trade_realized_pnl_diff_usdt_reduce,
                max_income_trade_realized_pnl_diff_usdt_observe=args.max_income_trade_realized_pnl_diff_usdt_observe,
                max_income_trade_link_gap_ratio_reduce=args.max_income_trade_link_gap_ratio_reduce,
                max_income_trade_link_gap_ratio_observe=args.max_income_trade_link_gap_ratio_observe,
                reduce_size_multiplier=args.reduce_size_multiplier,
            )
        )
    if args.command == "session-truth-watch":
        return asyncio.run(
            cmd_session_truth_watch(
                config,
                session_state_path=args.session_state_path,
                lookback_minutes=args.lookback_minutes,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                authoritative_archive_root=args.authoritative_archive_root,
                prefer_authoritative_archive=args.prefer_authoritative_archive,
                hydrate_archive_gaps=args.hydrate_archive_gaps,
                income_window_days=args.income_window_days,
                min_exchange_trade_count=args.min_exchange_trade_count,
                min_quote_qty_usdt=args.min_quote_qty_usdt,
                max_negative_net_realized_pnl_usdt_reduce=args.max_negative_net_realized_pnl_usdt_reduce,
                max_negative_net_realized_pnl_usdt_observe=args.max_negative_net_realized_pnl_usdt_observe,
                max_negative_net_realized_bps_reduce=args.max_negative_net_realized_bps_reduce,
                max_negative_net_realized_bps_observe=args.max_negative_net_realized_bps_observe,
                max_negative_net_per_trade_usdt_reduce=args.max_negative_net_per_trade_usdt_reduce,
                max_negative_net_per_trade_usdt_observe=args.max_negative_net_per_trade_usdt_observe,
                min_maker_ratio_reduce=args.min_maker_ratio_reduce,
                min_maker_ratio_observe=args.min_maker_ratio_observe,
                max_commission_bps_reduce=args.max_commission_bps_reduce,
                max_commission_bps_observe=args.max_commission_bps_observe,
                max_negative_funding_bps_reduce=args.max_negative_funding_bps_reduce,
                max_negative_funding_bps_observe=args.max_negative_funding_bps_observe,
                reduce_size_multiplier=args.reduce_size_multiplier,
            )
        )
    if args.command == "session-truth-trend-watch":
        return asyncio.run(
            cmd_session_truth_trend_watch(
                config,
                report_path=args.report_path,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                min_active_bucket_count=args.min_active_bucket_count,
                max_negative_bucket_ratio_reduce=args.max_negative_bucket_ratio_reduce,
                max_negative_bucket_ratio_observe=args.max_negative_bucket_ratio_observe,
                consecutive_negative_buckets_reduce=args.consecutive_negative_buckets_reduce,
                consecutive_negative_buckets_observe=args.consecutive_negative_buckets_observe,
                max_negative_recent_bucket_net_realized_bps_reduce=args.max_negative_recent_bucket_net_realized_bps_reduce,
                max_negative_recent_bucket_net_realized_bps_observe=args.max_negative_recent_bucket_net_realized_bps_observe,
                max_negative_recent_two_bucket_net_realized_bps_reduce=args.max_negative_recent_two_bucket_net_realized_bps_reduce,
                max_negative_recent_two_bucket_net_realized_bps_observe=args.max_negative_recent_two_bucket_net_realized_bps_observe,
                min_recent_bucket_maker_ratio_reduce=args.min_recent_bucket_maker_ratio_reduce,
                min_recent_bucket_maker_ratio_observe=args.min_recent_bucket_maker_ratio_observe,
                max_negative_worst_bucket_net_realized_bps_reduce=args.max_negative_worst_bucket_net_realized_bps_reduce,
                max_negative_worst_bucket_net_realized_bps_observe=args.max_negative_worst_bucket_net_realized_bps_observe,
                max_cumulative_drawdown_usdt_reduce=args.max_cumulative_drawdown_usdt_reduce,
                max_cumulative_drawdown_usdt_observe=args.max_cumulative_drawdown_usdt_observe,
                reduce_size_multiplier=args.reduce_size_multiplier,
            )
        )

    if args.command == "economics-regime-watch":
        return asyncio.run(
            cmd_economics_regime_watch(
                config,
                lookback_days=args.lookback_days,
                end_date=args.end_date,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                min_active_day_count=args.min_active_day_count,
                max_negative_day_ratio_reduce=args.max_negative_day_ratio_reduce,
                max_negative_day_ratio_observe=args.max_negative_day_ratio_observe,
                consecutive_negative_days_reduce=args.consecutive_negative_days_reduce,
                consecutive_negative_days_observe=args.consecutive_negative_days_observe,
                max_negative_recent_day_net_realized_bps_reduce=args.max_negative_recent_day_net_realized_bps_reduce,
                max_negative_recent_day_net_realized_bps_observe=args.max_negative_recent_day_net_realized_bps_observe,
                max_negative_recent_two_day_net_realized_bps_reduce=args.max_negative_recent_two_day_net_realized_bps_reduce,
                max_negative_recent_two_day_net_realized_bps_observe=args.max_negative_recent_two_day_net_realized_bps_observe,
                min_average_maker_ratio_reduce=args.min_average_maker_ratio_reduce,
                min_average_maker_ratio_observe=args.min_average_maker_ratio_observe,
                max_average_commission_bps_reduce=args.max_average_commission_bps_reduce,
                max_average_commission_bps_observe=args.max_average_commission_bps_observe,
                max_negative_average_funding_bps_reduce=args.max_negative_average_funding_bps_reduce,
                max_negative_average_funding_bps_observe=args.max_negative_average_funding_bps_observe,
                max_average_negative_bucket_ratio_reduce=args.max_average_negative_bucket_ratio_reduce,
                max_average_negative_bucket_ratio_observe=args.max_average_negative_bucket_ratio_observe,
                max_cumulative_drawdown_usdt_reduce=args.max_cumulative_drawdown_usdt_reduce,
                max_cumulative_drawdown_usdt_observe=args.max_cumulative_drawdown_usdt_observe,
                reduce_size_multiplier=args.reduce_size_multiplier,
            )
        )

    if args.command == "combined-protection-watch":
        return asyncio.run(
            cmd_combined_protection_watch(
                config,
                execution_drift_guard_path=args.execution_drift_guard_path,
                intraday_protection_guard_path=args.intraday_protection_guard_path,
                pnl_protection_guard_path=args.pnl_protection_guard_path,
                trade_reconciliation_guard_path=args.trade_reconciliation_guard_path,
                session_truth_guard_path=args.session_truth_guard_path,
                session_truth_trend_guard_path=args.session_truth_trend_guard_path,
                economics_regime_guard_path=args.economics_regime_guard_path,
                state_path=args.state_path,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                observe_cooldown_seconds=args.observe_cooldown_seconds,
                min_trade_confirmations_to_relax_reduce=args.min_trade_confirmations_to_relax_reduce,
                min_trade_confirmations_to_relax_observe=args.min_trade_confirmations_to_relax_observe,
                min_reduce_confirmations_to_relax_observe=args.min_reduce_confirmations_to_relax_observe,
                multisource_reduce_size_multiplier=args.multisource_reduce_size_multiplier,
            )
        )
    if args.command == "run-breakout-loop":
        return asyncio.run(
            cmd_run_breakout_loop(
                config,
                max_messages=args.max_messages,
                strategy_kind=args.strategy,
                lookback=args.lookback,
                atr_window=args.atr_window,
                reversion_lookback=args.reversion_lookback,
                reversion_entry_atr_multiple=args.reversion_entry_atr_multiple,
                reversion_max_atr_fraction=args.reversion_max_atr_fraction,
                reversion_min_flow_flip=args.reversion_min_flow_flip,
                router_range_max_atr_fraction=args.router_range_max_atr_fraction,
                router_trend_min_atr_fraction=args.router_trend_min_atr_fraction,
                router_trend_min_abs_flow_imbalance=args.router_trend_min_abs_flow_imbalance,
                router_range_max_abs_flow_imbalance=args.router_range_max_abs_flow_imbalance,
                router_neutral_preference=args.router_neutral_preference,
                router_opportunistic_fallback=args.router_opportunistic_fallback,
                entry_timeout=args.entry_timeout,
                hold_seconds=args.hold_seconds,
                position_notional=args.position_notional,
                reconcile_interval_seconds=args.reconcile_interval_seconds,
                max_reconcile_staleness_ms=args.max_reconcile_staleness_ms,
                trade_flow_window_seconds=args.trade_flow_window_seconds,
                min_recent_agg_trades=args.min_recent_agg_trades,
                min_flow_imbalance=args.min_flow_imbalance,
                max_mark_trade_divergence_bps=args.max_mark_trade_divergence_bps,
                max_positive_funding_rate=args.max_positive_funding_rate,
                min_negative_funding_rate=args.min_negative_funding_rate,
                crowding_period=args.crowding_period,
                crowding_interval_seconds=args.crowding_interval_seconds,
                max_crowding_snapshot_age_seconds=args.max_crowding_snapshot_age_seconds,
                min_crowding_score=args.min_crowding_score,
                crowding_oi_expansion_weight=args.crowding_oi_expansion_weight,
                with_book_ticker=args.with_book_ticker,
                with_depth_book=args.with_depth_book,
                with_rpi_depth_book=args.with_rpi_depth_book,
                use_rpi_depth_if_available=args.use_rpi_depth_if_available,
                max_book_spread_bps=args.max_book_spread_bps,
                max_book_ticker_staleness_ms=args.max_book_ticker_staleness_ms,
                max_depth_snapshot_staleness_ms=args.max_depth_snapshot_staleness_ms,
                min_depth_imbalance=args.min_depth_imbalance,
                min_notional_multiplier=args.min_notional_multiplier,
                max_notional_multiplier=args.max_notional_multiplier,
                abstain_below_multiplier=args.abstain_below_multiplier,
                min_effective_notional_usdt=args.min_effective_notional_usdt,
                sizing_flow_weight=args.sizing_flow_weight,
                sizing_crowding_weight=args.sizing_crowding_weight,
                sizing_divergence_penalty_weight=args.sizing_divergence_penalty_weight,
                sizing_funding_penalty_weight=args.sizing_funding_penalty_weight,
                sizing_divergence_penalty_cap_bps=args.sizing_divergence_penalty_cap_bps,
                sizing_funding_penalty_cap_rate=args.sizing_funding_penalty_cap_rate,
                volatility_target_atr_fraction=args.volatility_target_atr_fraction,
                volatility_abstain_above_atr_fraction=args.volatility_abstain_above_atr_fraction,
                volatility_min_notional_multiplier=args.volatility_min_notional_multiplier,
                volatility_max_notional_multiplier=args.volatility_max_notional_multiplier,
                min_expected_fill_ratio=args.min_expected_fill_ratio,
                max_expected_queue_clear_seconds=args.max_expected_queue_clear_seconds,
                max_queue_ahead_to_order_ratio=args.max_queue_ahead_to_order_ratio,
                min_directional_queue_flow_qty_per_second=args.min_directional_queue_flow_qty_per_second,
                min_exit_depth_coverage_ratio=args.min_exit_depth_coverage_ratio,
                max_exit_depth_sweep_bps=args.max_exit_depth_sweep_bps,
                exit_depth_tail_penalty_bps=args.exit_depth_tail_penalty_bps,
                synthetic_tail_levels=args.synthetic_tail_levels,
                synthetic_tail_replenishment_ratio=args.synthetic_tail_replenishment_ratio,
                synthetic_tail_step_bps=args.synthetic_tail_step_bps,
                require_contract_trading_status=args.require_contract_trading_status,
                with_private=args.with_private,
                with_reconcile=args.with_reconcile,
                with_crowding=args.with_crowding,
                heal_on_reconcile=args.heal_on_reconcile,
                targeted_heal_on_reconcile=args.targeted_heal_on_reconcile,
                execution_drift_guard_path=args.execution_drift_guard_path,
                intraday_protection_guard_path=args.intraday_protection_guard_path,
                pnl_protection_guard_path=args.pnl_protection_guard_path,
                trade_reconciliation_guard_path=args.trade_reconciliation_guard_path,
                session_truth_guard_path=args.session_truth_guard_path,
                session_truth_trend_guard_path=args.session_truth_trend_guard_path,
                economics_regime_guard_path=args.economics_regime_guard_path,
                economics_dashboard_path=args.economics_dashboard_path,
                economics_feedback_enabled=args.economics_feedback_enabled,
                economics_feedback_min_active_day_count=args.economics_feedback_min_active_day_count,
                economics_feedback_min_multiplier=args.economics_feedback_min_multiplier,
                combined_protection_guard_path=args.combined_protection_guard_path,
                send=args.send,
                test_orders=args.test_orders,
            )
        )
    if args.command == "backtest-readiness":
        return cmd_backtest_readiness(
            config,
            start_date=args.start_date,
            end_date=args.end_date,
            mark_only=args.mark_only,
            crowding_period=args.crowding_period,
            depth_levels=args.depth_levels,
            use_rpi_depth_fills=args.use_rpi_depth_fills,
            ignore_contract_status=args.ignore_contract_status,
        )
    if args.command == "backtest-breakout":
        return cmd_backtest_breakout(
            config,
            start_date=args.start_date,
            end_date=args.end_date,
            strategy_kind=args.strategy,
            lookback=args.lookback,
            atr_window=args.atr_window,
            reversion_lookback=args.reversion_lookback,
            reversion_entry_atr_multiple=args.reversion_entry_atr_multiple,
            reversion_max_atr_fraction=args.reversion_max_atr_fraction,
            reversion_min_flow_flip=args.reversion_min_flow_flip,
            router_range_max_atr_fraction=args.router_range_max_atr_fraction,
            router_trend_min_atr_fraction=args.router_trend_min_atr_fraction,
            router_trend_min_abs_flow_imbalance=args.router_trend_min_abs_flow_imbalance,
            router_range_max_abs_flow_imbalance=args.router_range_max_abs_flow_imbalance,
            router_neutral_preference=args.router_neutral_preference,
            router_opportunistic_fallback=args.router_opportunistic_fallback,
            entry_timeout=args.entry_timeout,
            hold_seconds=args.hold_seconds,
            position_notional=args.position_notional,
            spread_bps=args.spread_bps,
            taker_slippage_bps=args.taker_slippage_bps,
            maker_fee_bps=args.maker_fee_bps,
            taker_fee_bps=args.taker_fee_bps,
            trade_flow_window_seconds=args.trade_flow_window_seconds,
            min_recent_agg_trades=args.min_recent_agg_trades,
            min_flow_imbalance=args.min_flow_imbalance,
            max_mark_trade_divergence_bps=args.max_mark_trade_divergence_bps,
            max_positive_funding_rate=args.max_positive_funding_rate,
            min_negative_funding_rate=args.min_negative_funding_rate,
            crowding_period=args.crowding_period,
            max_crowding_snapshot_age_seconds=args.max_crowding_snapshot_age_seconds,
            min_crowding_score=args.min_crowding_score,
            crowding_oi_expansion_weight=args.crowding_oi_expansion_weight,
            use_book_ticker_fills=args.use_book_ticker_fills,
            use_local_depth_fills=args.use_local_depth_fills,
            use_rpi_depth_fills=args.use_rpi_depth_fills,
            max_book_spread_bps=args.max_book_spread_bps,
            max_book_ticker_staleness_ms=args.max_book_ticker_staleness_ms,
            max_depth_snapshot_staleness_ms=args.max_depth_snapshot_staleness_ms,
            min_depth_imbalance=args.min_depth_imbalance,
            depth_levels=args.depth_levels,
            min_notional_multiplier=args.min_notional_multiplier,
            max_notional_multiplier=args.max_notional_multiplier,
            abstain_below_multiplier=args.abstain_below_multiplier,
            min_effective_notional_usdt=args.min_effective_notional_usdt,
            sizing_flow_weight=args.sizing_flow_weight,
            sizing_crowding_weight=args.sizing_crowding_weight,
            sizing_divergence_penalty_weight=args.sizing_divergence_penalty_weight,
            sizing_funding_penalty_weight=args.sizing_funding_penalty_weight,
            sizing_divergence_penalty_cap_bps=args.sizing_divergence_penalty_cap_bps,
            sizing_funding_penalty_cap_rate=args.sizing_funding_penalty_cap_rate,
            volatility_target_atr_fraction=args.volatility_target_atr_fraction,
            volatility_abstain_above_atr_fraction=args.volatility_abstain_above_atr_fraction,
            volatility_min_notional_multiplier=args.volatility_min_notional_multiplier,
            volatility_max_notional_multiplier=args.volatility_max_notional_multiplier,
            min_expected_fill_ratio=args.min_expected_fill_ratio,
            max_expected_queue_clear_seconds=args.max_expected_queue_clear_seconds,
            max_queue_ahead_to_order_ratio=args.max_queue_ahead_to_order_ratio,
            min_directional_queue_flow_qty_per_second=args.min_directional_queue_flow_qty_per_second,
            min_exit_depth_coverage_ratio=args.min_exit_depth_coverage_ratio,
            max_exit_depth_sweep_bps=args.max_exit_depth_sweep_bps,
            exit_depth_tail_penalty_bps=args.exit_depth_tail_penalty_bps,
            synthetic_tail_levels=args.synthetic_tail_levels,
            synthetic_tail_replenishment_ratio=args.synthetic_tail_replenishment_ratio,
            synthetic_tail_step_bps=args.synthetic_tail_step_bps,
            economics_lookback_days=args.economics_lookback_days,
            economics_feedback_enabled=args.economics_feedback_enabled,
            economics_feedback_min_active_day_count=args.economics_feedback_min_active_day_count,
            economics_feedback_min_multiplier=args.economics_feedback_min_multiplier,
            economics_regime_enabled=args.economics_regime_enabled,
            economics_regime_min_active_day_count=args.economics_regime_min_active_day_count,
            mark_only=args.mark_only,
            ignore_contract_status=args.ignore_contract_status,
        )
    if args.command == "walkforward-breakout":
        return cmd_walkforward_breakout(
            config,
            start_date=args.start_date,
            end_date=args.end_date,
            strategy_kind=args.strategy,
            strategy_grid=args.strategy_grid,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
            anchored_train=args.anchored_train,
            max_folds=args.max_folds,
            max_candidates=args.max_candidates,
            lookback=args.lookback,
            lookback_grid=args.lookback_grid,
            atr_window=args.atr_window,
            reversion_lookback=args.reversion_lookback,
            reversion_entry_atr_multiple=args.reversion_entry_atr_multiple,
            reversion_entry_atr_multiple_grid=args.reversion_entry_atr_multiple_grid,
            reversion_max_atr_fraction=args.reversion_max_atr_fraction,
            reversion_max_atr_fraction_grid=args.reversion_max_atr_fraction_grid,
            reversion_min_flow_flip=args.reversion_min_flow_flip,
            reversion_min_flow_flip_grid=args.reversion_min_flow_flip_grid,
            router_range_max_atr_fraction=args.router_range_max_atr_fraction,
            router_trend_min_atr_fraction=args.router_trend_min_atr_fraction,
            router_trend_min_abs_flow_imbalance=args.router_trend_min_abs_flow_imbalance,
            router_range_max_abs_flow_imbalance=args.router_range_max_abs_flow_imbalance,
            router_neutral_preference=args.router_neutral_preference,
            router_opportunistic_fallback=args.router_opportunistic_fallback,
            entry_timeout=args.entry_timeout,
            hold_seconds=args.hold_seconds,
            hold_seconds_grid=args.hold_seconds_grid,
            position_notional=args.position_notional,
            spread_bps=args.spread_bps,
            taker_slippage_bps=args.taker_slippage_bps,
            maker_fee_bps=args.maker_fee_bps,
            taker_fee_bps=args.taker_fee_bps,
            trade_flow_window_seconds=args.trade_flow_window_seconds,
            min_recent_agg_trades=args.min_recent_agg_trades,
            min_flow_imbalance=args.min_flow_imbalance,
            min_flow_imbalance_grid=args.min_flow_imbalance_grid,
            max_mark_trade_divergence_bps=args.max_mark_trade_divergence_bps,
            max_positive_funding_rate=args.max_positive_funding_rate,
            min_negative_funding_rate=args.min_negative_funding_rate,
            crowding_period=args.crowding_period,
            max_crowding_snapshot_age_seconds=args.max_crowding_snapshot_age_seconds,
            min_crowding_score=args.min_crowding_score,
            min_crowding_score_grid=args.min_crowding_score_grid,
            crowding_oi_expansion_weight=args.crowding_oi_expansion_weight,
            use_book_ticker_fills=args.use_book_ticker_fills,
            use_local_depth_fills=args.use_local_depth_fills,
            use_rpi_depth_fills=args.use_rpi_depth_fills,
            max_book_spread_bps=args.max_book_spread_bps,
            max_book_spread_bps_grid=args.max_book_spread_bps_grid,
            max_book_ticker_staleness_ms=args.max_book_ticker_staleness_ms,
            max_depth_snapshot_staleness_ms=args.max_depth_snapshot_staleness_ms,
            min_depth_imbalance=args.min_depth_imbalance,
            min_depth_imbalance_grid=args.min_depth_imbalance_grid,
            depth_levels=args.depth_levels,
            min_notional_multiplier=args.min_notional_multiplier,
            max_notional_multiplier=args.max_notional_multiplier,
            abstain_below_multiplier=args.abstain_below_multiplier,
            min_effective_notional_usdt=args.min_effective_notional_usdt,
            sizing_flow_weight=args.sizing_flow_weight,
            sizing_crowding_weight=args.sizing_crowding_weight,
            sizing_divergence_penalty_weight=args.sizing_divergence_penalty_weight,
            sizing_funding_penalty_weight=args.sizing_funding_penalty_weight,
            sizing_divergence_penalty_cap_bps=args.sizing_divergence_penalty_cap_bps,
            sizing_funding_penalty_cap_rate=args.sizing_funding_penalty_cap_rate,
            volatility_target_atr_fraction=args.volatility_target_atr_fraction,
            volatility_abstain_above_atr_fraction=args.volatility_abstain_above_atr_fraction,
            volatility_min_notional_multiplier=args.volatility_min_notional_multiplier,
            volatility_max_notional_multiplier=args.volatility_max_notional_multiplier,
            min_expected_fill_ratio=args.min_expected_fill_ratio,
            min_expected_fill_ratio_grid=args.min_expected_fill_ratio_grid,
            max_expected_queue_clear_seconds=args.max_expected_queue_clear_seconds,
            max_queue_ahead_to_order_ratio=args.max_queue_ahead_to_order_ratio,
            min_directional_queue_flow_qty_per_second=args.min_directional_queue_flow_qty_per_second,
            min_exit_depth_coverage_ratio=args.min_exit_depth_coverage_ratio,
            max_exit_depth_sweep_bps=args.max_exit_depth_sweep_bps,
            exit_depth_tail_penalty_bps=args.exit_depth_tail_penalty_bps,
            synthetic_tail_levels=args.synthetic_tail_levels,
            synthetic_tail_replenishment_ratio=args.synthetic_tail_replenishment_ratio,
            synthetic_tail_step_bps=args.synthetic_tail_step_bps,
            economics_lookback_days=args.economics_lookback_days,
            economics_feedback_enabled=args.economics_feedback_enabled,
            economics_feedback_min_active_day_count=args.economics_feedback_min_active_day_count,
            economics_feedback_min_multiplier=args.economics_feedback_min_multiplier,
            economics_regime_enabled=args.economics_regime_enabled,
            economics_regime_min_active_day_count=args.economics_regime_min_active_day_count,
            max_drawdown_penalty=args.max_drawdown_penalty,
            entry_timeout_rate_penalty=args.entry_timeout_rate_penalty,
            exit_depth_sweep_bps_penalty=args.exit_depth_sweep_bps_penalty,
            min_trade_count=args.min_trade_count,
            mark_only=args.mark_only,
            ignore_contract_status=args.ignore_contract_status,
        )

    if args.command == "submit-normal":
        return cmd_submit_normal(
            config,
            side=args.side,
            order_type=args.order_type,
            qty=args.qty,
            price=args.price,
            mark_price=args.mark_price,
            tif=args.tif,
            client_id=args.client_id,
            reduce_only=args.reduce_only,
            send=args.send,
            test=args.test,
        )
    if args.command == "submit-algo":
        return cmd_submit_algo(
            config,
            side=args.side,
            order_type=args.order_type,
            qty=args.qty,
            trigger_price=args.trigger_price,
            price=args.price,
            activate_price=args.activate_price,
            callback_rate=args.callback_rate,
            tif=args.tif,
            client_algo_id=args.client_algo_id,
            reduce_only=args.reduce_only,
            close_position=args.close_position,
            price_protect=args.price_protect,
            send=args.send,
        )
    if args.command == "cancel-normal":
        return cmd_cancel_normal(config, order_id=args.order_id, client_order_id=args.client_order_id, send=args.send)
    if args.command == "cancel-algo":
        return cmd_cancel_algo(config, algo_id=args.algo_id, client_algo_id=args.client_algo_id, send=args.send)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
