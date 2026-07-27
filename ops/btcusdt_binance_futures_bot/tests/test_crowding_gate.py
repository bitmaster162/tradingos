import asyncio
from decimal import Decimal

from btcusdt_bot.backtest.engine import BreakoutBacktestConfig, ParityBreakoutBacktester
from btcusdt_bot.backtest.reader import BacktestEvent
from btcusdt_bot.config import BotConfig
from btcusdt_bot.execution.gateway import GatewayResult
from btcusdt_bot.live_breakout import LiveBreakoutConfig, LiveBreakoutRunner
from btcusdt_bot.reconcile_daemon import diff_runtime_states
from btcusdt_bot.reconcile_healer import TargetedReconcileHealer
from btcusdt_bot.state.store import RuntimeState, StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


class FakeGateway:
    def __init__(self) -> None:
        self.submit_calls = []

    def submit_normal(self, proposal, *, reference_price=None, dry_run=True, test=False):
        self.submit_calls.append((proposal, reference_price, dry_run, test))
        return GatewayResult(payload={"clientOrderId": proposal.client_id}, validation=None, sent=False)

    def cancel_normal(self, *, symbol, order_id=None, client_order_id=None, dry_run=True):  # pragma: no cover
        raise AssertionError("cancel_normal should not be called in this test")

    def submit_algo(self, proposal, *, dry_run=True):  # pragma: no cover
        raise AssertionError("submit_algo should not be called in this test")

    def cancel_algo(self, *, symbol, algo_id=None, client_algo_id=None, dry_run=True):  # pragma: no cover
        raise AssertionError("cancel_algo should not be called in this test")


class FakeNoQueryClient:
    def query_order(self, symbol, *, order_id=None, client_order_id=None):  # pragma: no cover
        raise AssertionError("query_order should not be called")

    def query_algo_order(self, *, algo_id=None, client_algo_id=None):  # pragma: no cover
        raise AssertionError("query_algo_order should not be called")



def _build_config(tmp_path) -> BotConfig:
    return BotConfig(
        env="demo",
        symbol="BTCUSDT",
        rest_base_url="https://demo-fapi.binance.com",
        ws_public_base_url="wss://fstream.binancefuture.com",
        ws_market_base_url="wss://fstream.binancefuture.com",
        ws_private_base_url="wss://fstream.binancefuture.com",
        api_key="",
        api_secret="",
        recv_window_ms=5000,
        timeout_s=10.0,
        position_mode="ONE_WAY",
        margin_mode="ISOLATED",
        max_leverage=3,
        max_position_notional_usdt=500.0,
        max_daily_loss_usdt=50.0,
        max_normal_open_orders=8,
        max_algo_open_orders=20,
        stale_data_limit_ms=4000,
        countdown_cancel_ms=120000,
        heartbeat_interval_ms=30000,
        user_stream_keepalive_ms=1800000,
        reconnect_initial_backoff_ms=1000,
        reconnect_max_backoff_ms=30000,
        kline_intervals=("1m",),
        private_events=("ORDER_TRADE_UPDATE",),
        enable_contract_info_stream=True,
        enable_force_order_stream=False,
        enable_countdown_heartbeat=False,
        state_flush_every_events=1,
        data_dir=tmp_path,
        log_level="INFO",
    )


async def _feed_breakout_prices(runner: LiveBreakoutRunner, prices: list[Decimal], *, funding_rate: Decimal = Decimal("0")) -> None:
    event_time_ms = 1_700_000_000_000
    for price in prices:
        await runner._on_mark_price_tick(event_time_ms=event_time_ms, mark_price=price, funding_rate=funding_rate)
        event_time_ms += 1000


async def _feed_agg_trades(runner: LiveBreakoutRunner, *, side: str, qtys: list[str]) -> None:
    event_time_ms = 1_699_999_999_000
    buyer_is_market_maker = side.upper() == "SELL"
    for qty in qtys:
        await runner._on_agg_trade(
            event_time_ms=event_time_ms,
            price=Decimal("100"),
            qty=Decimal(qty),
            buyer_is_market_maker=buyer_is_market_maker,
        )
        event_time_ms += 200



def _crowding_snapshot(*, snapshot_time_ms: int, global_ratio: str, top_account_ratio: str, top_position_ratio: str, taker_ratio: str, open_interest: str, open_interest_hist: str) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "period": "5m",
        "snapshot_time_ms": snapshot_time_ms,
        "open_interest": {"symbol": "BTCUSDT", "openInterest": open_interest, "time": snapshot_time_ms},
        "open_interest_hist": {"symbol": "BTCUSDT", "sumOpenInterest": open_interest_hist, "timestamp": snapshot_time_ms},
        "global_long_short_account_ratio": {"symbol": "BTCUSDT", "longShortRatio": global_ratio, "timestamp": snapshot_time_ms},
        "top_long_short_account_ratio": {"symbol": "BTCUSDT", "longShortRatio": top_account_ratio, "timestamp": snapshot_time_ms},
        "top_long_short_position_ratio": {"symbol": "BTCUSDT", "longShortRatio": top_position_ratio, "timestamp": snapshot_time_ms},
        "taker_buy_sell_ratio": {"buySellRatio": taker_ratio, "timestamp": snapshot_time_ms},
    }



def test_live_breakout_gate_blocks_buy_when_crowding_score_too_low(tmp_path) -> None:
    config = _build_config(tmp_path)
    gateway = FakeGateway()
    store = StateStore()
    store.patch_crowding_snapshot(
        _crowding_snapshot(
            snapshot_time_ms=1_700_000_000_000,
            global_ratio="1.60",
            top_account_ratio="1.50",
            top_position_ratio="1.40",
            taker_ratio="1.10",
            open_interest="130",
            open_interest_hist="100",
        )
    )

    with JSONLWriter(tmp_path) as writer:
        runner = LiveBreakoutRunner(
            config,
            client=object(),
            store=store,
            writer=writer,
            gateway=gateway,
            live_config=LiveBreakoutConfig(
                lookback_ticks=3,
                atr_window_ticks=2,
                min_recent_agg_trades=2,
                min_flow_imbalance=Decimal("0.25"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                with_crowding_collector=False,
                max_crowding_snapshot_age_seconds=600,
                min_crowding_score=Decimal("0.05"),
                send_orders=False,
            ),
        )
        asyncio.run(_feed_agg_trades(runner, side="BUY", qtys=["1.0", "1.2"]))
        asyncio.run(_feed_breakout_prices(runner, [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]))

        assert len(gateway.submit_calls) == 0
        assert runner.status.signal_gate_rejections == 1
        assert runner.status.crowding_gate_rejections == 1
        assert runner.status.last_gate_reason == "crowding_score_below_threshold"



def test_live_breakout_gate_allows_buy_when_crowding_score_positive(tmp_path) -> None:
    config = _build_config(tmp_path)
    gateway = FakeGateway()
    store = StateStore()
    store.patch_crowding_snapshot(
        _crowding_snapshot(
            snapshot_time_ms=1_700_000_000_000,
            global_ratio="1.02",
            top_account_ratio="1.00",
            top_position_ratio="1.01",
            taker_ratio="1.20",
            open_interest="120",
            open_interest_hist="100",
        )
    )

    with JSONLWriter(tmp_path) as writer:
        runner = LiveBreakoutRunner(
            config,
            client=object(),
            store=store,
            writer=writer,
            gateway=gateway,
            live_config=LiveBreakoutConfig(
                lookback_ticks=3,
                atr_window_ticks=2,
                min_recent_agg_trades=2,
                min_flow_imbalance=Decimal("0.25"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                with_crowding_collector=False,
                max_crowding_snapshot_age_seconds=600,
                min_crowding_score=Decimal("0.05"),
                send_orders=False,
            ),
        )
        asyncio.run(_feed_agg_trades(runner, side="BUY", qtys=["1.0", "1.2"]))
        asyncio.run(_feed_breakout_prices(runner, [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]))

        assert len(gateway.submit_calls) == 1
        assert runner.status.crowding_gate_rejections == 0
        assert Decimal(runner.status.last_crowding_side_score) > 0



def test_parity_backtester_rejects_crowded_breakout() -> None:
    backtester = ParityBreakoutBacktester(
        symbol="BTCUSDT",
        config=BreakoutBacktestConfig(
            breakout_lookback_ticks=3,
            atr_window_ticks=2,
            position_notional_usdt=Decimal("100"),
            synthetic_spread_bps=Decimal("1.0"),
            taker_slippage_bps=Decimal("0"),
            maker_fee_bps=Decimal("0"),
            taker_fee_bps=Decimal("0"),
            min_recent_agg_trades=2,
            min_flow_imbalance=Decimal("0.25"),
            max_crowding_snapshot_age_seconds=600,
            min_crowding_score=Decimal("0.05"),
        ),
        filters=None,
    )

    ts = 1_700_000_000_000
    events = [
        BacktestEvent(
            event_time_ms=ts,
            stream="crowding/5m",
            event_type="crowdingSnapshot",
            payload=_crowding_snapshot(
                snapshot_time_ms=ts,
                global_ratio="1.60",
                top_account_ratio="1.50",
                top_position_ratio="1.40",
                taker_ratio="1.10",
                open_interest="130",
                open_interest_hist="100",
            ),
            crowding_snapshot=_crowding_snapshot(
                snapshot_time_ms=ts,
                global_ratio="1.60",
                top_account_ratio="1.50",
                top_position_ratio="1.40",
                taker_ratio="1.10",
                open_interest="130",
                open_interest_hist="100",
            ),
        ),
        BacktestEvent(event_time_ms=ts + 100, stream="btcusdt@aggTrade", event_type="aggTrade", payload={"e": "aggTrade"}, price=Decimal("100"), qty=Decimal("1.0"), buyer_is_market_maker=False),
        BacktestEvent(event_time_ms=ts + 300, stream="btcusdt@aggTrade", event_type="aggTrade", payload={"e": "aggTrade"}, price=Decimal("100"), qty=Decimal("1.2"), buyer_is_market_maker=False),
        BacktestEvent(event_time_ms=ts + 1000, stream="btcusdt@markPrice@1s", event_type="markPriceUpdate", payload={"e": "markPriceUpdate"}, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=ts + 28_800_000),
        BacktestEvent(event_time_ms=ts + 2000, stream="btcusdt@markPrice@1s", event_type="markPriceUpdate", payload={"e": "markPriceUpdate"}, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=ts + 28_800_000),
        BacktestEvent(event_time_ms=ts + 3000, stream="btcusdt@markPrice@1s", event_type="markPriceUpdate", payload={"e": "markPriceUpdate"}, price=Decimal("100"), funding_rate=Decimal("0"), next_funding_time_ms=ts + 28_800_000),
        BacktestEvent(event_time_ms=ts + 4000, stream="btcusdt@markPrice@1s", event_type="markPriceUpdate", payload={"e": "markPriceUpdate"}, price=Decimal("101"), funding_rate=Decimal("0"), next_funding_time_ms=ts + 28_800_000),
    ]

    report = backtester.run(events)

    assert report.trade_count == 0
    assert report.signal_gate_rejections == 1
    assert report.crowding_gate_rejections == 1
    assert report.last_crowding_side_score is not None
    assert report.last_crowding_side_score < Decimal("0.05")



def test_targeted_reconcile_healer_patches_contract_info_drift(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    store.patch_contract_info({"s": "BTCUSDT", "cs": "TRADING", "bks": [{"bracket": 1}]})
    exchange_state = RuntimeState(latest_contract_info={"s": "BTCUSDT", "cs": "SETTLING", "bks": [{"bracket": 1}, {"bracket": 2}]})

    report = diff_runtime_states(store.snapshot(), exchange_state, symbol="BTCUSDT")
    healer = TargetedReconcileHealer(config, client=FakeNoQueryClient(), store=store)
    result = healer.heal(report, exchange_state)

    assert result.applied is True
    assert store.state.latest_contract_info["cs"] == "SETTLING"
    assert len(store.state.latest_contract_info["bks"]) == 2
    assert any(action.key == "contract_info" for action in result.actions)
