import asyncio
from decimal import Decimal

from btcusdt_bot.config import BotConfig
from btcusdt_bot.execution.gateway import GatewayResult
from btcusdt_bot.live_breakout import LiveBreakoutConfig, LiveBreakoutRunner
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


class FakeGateway:
    def __init__(self) -> None:
        self.submit_calls = []

    def submit_normal(self, proposal, *, reference_price=None, dry_run=True, test=False):
        self.submit_calls.append((proposal, reference_price, dry_run, test))
        return GatewayResult(payload={"clientOrderId": proposal.client_id}, validation=None, sent=False)

    def cancel_normal(self, *, symbol, order_id=None, client_order_id=None, dry_run=True):
        return GatewayResult(payload={"clientOrderId": client_order_id}, validation=None, sent=False)

    def submit_algo(self, proposal, *, dry_run=True):
        return GatewayResult(payload={"clientAlgoId": proposal.client_algo_id}, validation=None, sent=False)

    def cancel_algo(self, *, symbol, algo_id=None, client_algo_id=None, dry_run=True):
        return GatewayResult(payload={"clientAlgoId": client_algo_id}, validation=None, sent=False)


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


def test_live_breakout_runner_supports_router_strategy_kind(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    gateway = FakeGateway()

    with JSONLWriter(tmp_path) as writer:
        runner = LiveBreakoutRunner(
            config,
            client=object(),
            store=store,
            writer=writer,
            gateway=gateway,
            live_config=LiveBreakoutConfig(
                strategy_kind="router",
                lookback_ticks=3,
                atr_window_ticks=2,
                reversion_entry_atr_multiple=Decimal("0.50"),
                reversion_max_atr_fraction=Decimal("0.0500"),
                router_range_max_atr_fraction=Decimal("0.0060"),
                router_trend_min_atr_fraction=Decimal("0.0100"),
                entry_timeout_seconds=5,
                position_notional_usdt=Decimal("100"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                send_orders=False,
            ),
        )

        for idx, price in enumerate((Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")), start=1):
            asyncio.run(runner._on_mark_price_tick(event_time_ms=1_700_000_000_000 + idx * 1000, mark_price=price))

        assert runner.model.strategy_kind == "router"
        assert runner.status.strategy_kind == "router"
        assert runner.status.router_reversion_signal_count == 1
        assert runner.status.last_router_selected_strategy_kind == "reversion"
        assert len(gateway.submit_calls) == 1
