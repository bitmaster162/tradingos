import asyncio
from decimal import Decimal

from btcusdt_bot.config import BotConfig
from btcusdt_bot.execution.gateway import GatewayResult
from btcusdt_bot.live_breakout import LiveBreakoutConfig, LiveBreakoutRunner, RollingBreakoutModel
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


class FakeGateway:
    def __init__(self) -> None:
        self.submit_calls = []
        self.cancel_calls = []

    def submit_normal(self, proposal, *, reference_price=None, dry_run=True, test=False):
        self.submit_calls.append((proposal, reference_price, dry_run, test))
        return GatewayResult(payload={"clientOrderId": proposal.client_id}, validation=None, sent=False)

    def cancel_normal(self, *, symbol, order_id=None, client_order_id=None, dry_run=True):
        self.cancel_calls.append((symbol, order_id, client_order_id, dry_run))
        return GatewayResult(payload={"clientOrderId": client_order_id}, validation=None, sent=False)

    def submit_algo(self, proposal, *, dry_run=True):
        raise AssertionError("submit_algo should not be called in this dry-run test")

    def cancel_algo(self, *, symbol, algo_id=None, client_algo_id=None, dry_run=True):
        raise AssertionError("cancel_algo should not be called in this dry-run test")


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


def test_rolling_breakout_model_emits_buy_signal() -> None:
    model = RollingBreakoutModel(lookback_ticks=3, atr_window_ticks=2)
    assert model.on_price(event_time_ms=1, price=Decimal("100")) is None
    assert model.on_price(event_time_ms=2, price=Decimal("100")) is None
    assert model.on_price(event_time_ms=3, price=Decimal("100")) is None
    signal = model.on_price(event_time_ms=4, price=Decimal("101"))
    assert signal is not None
    assert signal.side == "BUY"
    assert signal.atr > 0


def test_live_breakout_runner_submits_and_then_cancels_stale_dry_run_entry(tmp_path) -> None:
    config = _build_config(tmp_path)
    gateway = FakeGateway()
    store = StateStore()

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
                entry_timeout_seconds=5,
                position_notional_usdt=Decimal("100"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                send_orders=False,
            ),
        )
        prices = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]
        event_time_ms = 1_700_000_000_000
        for price in prices:
            asyncio.run(runner._on_mark_price_tick(event_time_ms=event_time_ms, mark_price=price))
            event_time_ms += 1000

        assert len(gateway.submit_calls) == 1
        assert runner.active_entry is not None

        asyncio.run(
            runner._on_mark_price_tick(
                event_time_ms=1_700_000_009_000,
                mark_price=Decimal("101.2"),
            )
        )

        assert len(gateway.cancel_calls) == 1
        assert runner.active_entry is None
        assert runner.status.stale_cancels == 1
