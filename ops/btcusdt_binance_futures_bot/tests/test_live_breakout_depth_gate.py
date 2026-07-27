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
        raise AssertionError("submit_algo should not be called")

    def cancel_algo(self, *, symbol, algo_id=None, client_algo_id=None, dry_run=True):
        raise AssertionError("cancel_algo should not be called")


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


async def _feed_breakout_prices(runner: LiveBreakoutRunner, prices: list[Decimal]) -> None:
    event_time_ms = 1_700_000_000_000
    for price in prices:
        await runner._on_mark_price_tick(event_time_ms=event_time_ms, mark_price=price, funding_rate=Decimal("0"))
        event_time_ms += 1000


async def _feed_agg_trades(runner: LiveBreakoutRunner) -> None:
    event_time_ms = 1_699_999_999_000
    for qty in ("1.0", "1.2"):
        await runner._on_agg_trade(
            event_time_ms=event_time_ms,
            price=Decimal("100"),
            qty=Decimal(qty),
            buyer_is_market_maker=False,
        )
        event_time_ms += 200


def test_live_breakout_depth_gate_blocks_buy_when_bid_depth_is_weak(tmp_path) -> None:
    config = _build_config(tmp_path)
    gateway = FakeGateway()
    store = StateStore()
    store.patch_depth_snapshot(
        {
            "e": "localDepthSnapshot",
            "E": 1_699_999_999_500,
            "T": 1_699_999_999_500,
            "s": "BTCUSDT",
            "u": 101,
            "levels": 2,
            "imbalance": "-0.40",
            "bids": [["100.0", "1.0"]],
            "asks": [["100.5", "4.0"]],
        }
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
                min_depth_imbalance=Decimal("0.10"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                with_book_ticker_collector=False,
                with_depth_book_collector=False,
                with_crowding_collector=False,
                send_orders=False,
            ),
        )
        asyncio.run(_feed_agg_trades(runner))
        asyncio.run(_feed_breakout_prices(runner, [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]))

        assert len(gateway.submit_calls) == 0
        assert runner.status.depth_gate_rejections == 1
        assert runner.status.last_gate_reason == "depth_imbalance_not_confirmed_for_buy"
