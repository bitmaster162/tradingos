import asyncio
from decimal import Decimal

from btcusdt_bot.config import BotConfig
from btcusdt_bot.execution.gateway import GatewayResult
from btcusdt_bot.execution.query_resolver import QueryResolution
from btcusdt_bot.live_breakout import LiveBreakoutConfig, LiveBreakoutRunner
from btcusdt_bot.state.store import StateStore
from btcusdt_bot.storage.jsonl import JSONLWriter


class FakeQueryGateway:
    def __init__(self, store: StateStore) -> None:
        self.store = store
        self.submit_calls = []
        self.cancel_calls = []
        self.query_calls = []

    def submit_normal(self, proposal, *, reference_price=None, dry_run=True, test=False):
        self.submit_calls.append((proposal, reference_price, dry_run, test))
        return GatewayResult(
            payload={"clientOrderId": proposal.client_id},
            validation=None,
            sent=False,
            execution_unknown=True,
            error={"status": 503, "message": "Unknown error, please check your request or try again later."},
        )

    def query_normal(self, *, symbol, order_id=None, client_order_id=None):
        self.query_calls.append((symbol, order_id, client_order_id))
        self.store.upsert_normal_order_from_rest(
            {
                "symbol": symbol,
                "orderId": 77,
                "clientOrderId": client_order_id,
                "side": "BUY",
                "positionSide": "BOTH",
                "type": "LIMIT",
                "status": "FILLED",
                "timeInForce": "GTX",
                "origQty": "0.001",
                "executedQty": "0.001",
                "price": "65000",
                "avgPrice": "64999.5",
                "updateTime": 123456,
            }
        )
        return QueryResolution(
            kind="normal",
            symbol=symbol,
            found=True,
            requested_by="client_id",
            identifier=str(client_order_id),
            response={"status": "FILLED"},
            updated_store=True,
        )

    def cancel_normal(self, *, symbol, order_id=None, client_order_id=None, dry_run=True):
        self.cancel_calls.append((symbol, order_id, client_order_id, dry_run))
        return GatewayResult(payload={"clientOrderId": client_order_id}, validation=None, sent=False)

    def submit_algo(self, proposal, *, dry_run=True):
        return GatewayResult(payload={"clientAlgoId": proposal.client_algo_id}, validation=None, sent=False)

    def query_algo(self, *, symbol, algo_id=None, client_algo_id=None):
        raise AssertionError("query_algo should not be called in this test")

    def cancel_algo(self, *, symbol, algo_id=None, client_algo_id=None, dry_run=True):
        raise AssertionError("cancel_algo should not be called in this test")


def _build_config(tmp_path) -> BotConfig:
    return BotConfig(
        env="demo",
        symbol="BTCUSDT",
        rest_base_url="https://demo-fapi.binance.com",
        ws_public_base_url="wss://fstream.binancefuture.com",
        ws_market_base_url="wss://fstream.binancefuture.com",
        ws_private_base_url="wss://fstream.binancefuture.com",
        api_key="demo-key",
        api_secret="demo-secret",
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


def test_live_breakout_queries_exchange_before_stale_cancel_when_submission_status_unknown(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    store.mark_reconcile_result(checked_at_ms=1_700_000_000_000, mismatch_count=0)
    gateway = FakeQueryGateway(store)

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
                max_reconcile_staleness_ms=60_000,
                send_orders=True,
                test_orders=False,
            ),
        )
        prices = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]
        event_time_ms = 1_700_000_000_000
        for price in prices:
            asyncio.run(runner._on_mark_price_tick(event_time_ms=event_time_ms, mark_price=price))
            event_time_ms += 1000

        assert runner.active_entry is not None
        assert runner.active_entry.pending_exchange_confirmation is True

        asyncio.run(runner._maybe_cancel_stale_entry(event_time_ms=1_700_000_010_000))

        assert len(gateway.query_calls) == 1
        assert len(gateway.cancel_calls) == 0
        assert store.state.normal_orders[runner.active_entry.client_id].status == "FILLED"
        assert runner.active_entry.pending_exchange_confirmation is False
        assert runner.status.targeted_queries == 1


def test_live_breakout_send_orders_fails_closed_without_reconcile_proof(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    gateway = FakeQueryGateway(store)

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
                position_notional_usdt=Decimal("100"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                max_reconcile_staleness_ms=60_000,
                send_orders=True,
                test_orders=False,
            ),
        )
        event_time_ms = 1_700_000_000_000
        for price in [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]:
            asyncio.run(runner._on_mark_price_tick(event_time_ms=event_time_ms, mark_price=price))
            event_time_ms += 1000

        assert runner.active_entry is None
        assert gateway.submit_calls == []
        assert runner.status.entries_rejected == 1


def test_live_breakout_send_orders_rejects_delayed_market_event(tmp_path) -> None:
    config = _build_config(tmp_path)
    store = StateStore()
    store.mark_reconcile_result(checked_at_ms=1_700_000_000_000, mismatch_count=0)
    gateway = FakeQueryGateway(store)

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
                position_notional_usdt=Decimal("100"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                max_reconcile_staleness_ms=60_000,
                send_orders=True,
                test_orders=False,
            ),
        )
        event_time_ms = 1_700_000_000_000
        prices = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]
        for index, price in enumerate(prices):
            received_at_ms = event_time_ms if index < 3 else event_time_ms + 5_000
            asyncio.run(
                runner._on_mark_price_tick(
                    event_time_ms=event_time_ms,
                    mark_price=price,
                    received_at_ms=received_at_ms,
                )
            )
            event_time_ms += 1000

        assert runner.active_entry is None
        assert gateway.submit_calls == []
        assert runner.status.entries_rejected == 1
