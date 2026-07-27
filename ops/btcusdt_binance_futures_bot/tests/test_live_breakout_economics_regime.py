import asyncio
import json
from decimal import Decimal

from btcusdt_bot.config import BotConfig
from btcusdt_bot.execution.gateway import GatewayResult
from btcusdt_bot.execution.planner import ExecutionPlanner, PlannerConfig
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
        enable_contract_info_stream=False,
        enable_force_order_stream=False,
        enable_countdown_heartbeat=False,
        state_flush_every_events=1,
        data_dir=tmp_path,
        log_level="INFO",
    )


async def _feed_buy_agg_trades(runner: LiveBreakoutRunner) -> None:
    event_time_ms = 1_699_999_999_000
    for qty in ("1.0", "1.2"):
        await runner._on_agg_trade(
            event_time_ms=event_time_ms,
            price=Decimal("101"),
            qty=Decimal(qty),
            buyer_is_market_maker=False,
        )
        event_time_ms += 200


async def _feed_breakout_prices(runner: LiveBreakoutRunner, prices: list[Decimal]) -> None:
    event_time_ms = 1_700_000_000_000
    for price in prices:
        await runner._on_mark_price_tick(event_time_ms=event_time_ms, mark_price=price, funding_rate=Decimal("0"))
        event_time_ms += 1000



def test_live_breakout_direct_economics_regime_observe_only_blocks_entries(tmp_path) -> None:
    guard_path = tmp_path / "economics_regime_guard.json"
    guard_path.write_text(json.dumps({"action": "observe_only", "size_multiplier": "0"}), encoding="utf-8")
    config = _build_config(tmp_path)
    gateway = FakeGateway()

    with JSONLWriter(tmp_path) as writer:
        runner = LiveBreakoutRunner(
            config,
            client=object(),
            store=StateStore(),
            writer=writer,
            gateway=gateway,
            planner=ExecutionPlanner(PlannerConfig(passive_offset_bps=Decimal("0"))),
            live_config=LiveBreakoutConfig(
                lookback_ticks=3,
                atr_window_ticks=2,
                min_recent_agg_trades=2,
                min_flow_imbalance=Decimal("0.25"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                with_book_ticker_collector=False,
                with_depth_book_collector=False,
                with_crowding_collector=False,
                economics_regime_guard_path=str(guard_path),
                combined_protection_guard_path=None,
                volatility_target_atr_fraction=None,
                send_orders=False,
            ),
        )
        asyncio.run(_feed_buy_agg_trades(runner))
        asyncio.run(_feed_breakout_prices(runner, [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]))

        assert len(gateway.submit_calls) == 0
        assert runner.status.economics_regime_observe_rejections == 1
        assert runner.status.last_gate_reason == "economics_regime_observe_only"



def test_live_breakout_economics_feedback_scales_down_target_notional(tmp_path) -> None:
    dashboard_dir = tmp_path / "live" / "reports"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    (dashboard_dir / "latest_economics_dashboard.json").write_text(
        json.dumps(
            {
                "dashboard": {
                    "symbol": "BTCUSDT",
                    "start_date": "2026-04-01",
                    "end_date": "2026-04-07",
                    "lookback_days": 7,
                    "active_day_count": 5,
                    "negative_day_count": 3,
                    "negative_day_ratio": "0.60",
                    "recent_day_net_realized_bps": "-2.0",
                    "recent_two_day_net_realized_bps": "-1.25",
                    "average_maker_ratio": "0.20",
                    "average_commission_bps": "8.0",
                    "average_funding_bps": "-1.0",
                }
            }
        ),
        encoding="utf-8",
    )
    config = _build_config(tmp_path)
    gateway = FakeGateway()

    with JSONLWriter(tmp_path) as writer:
        runner = LiveBreakoutRunner(
            config,
            client=object(),
            store=StateStore(),
            writer=writer,
            gateway=gateway,
            planner=ExecutionPlanner(PlannerConfig(passive_offset_bps=Decimal("0"))),
            live_config=LiveBreakoutConfig(
                lookback_ticks=3,
                atr_window_ticks=2,
                min_recent_agg_trades=2,
                min_flow_imbalance=Decimal("0.25"),
                with_private_consumer=False,
                with_reconcile_daemon=False,
                with_book_ticker_collector=False,
                with_depth_book_collector=False,
                with_crowding_collector=False,
                economics_dashboard_path=str(dashboard_dir / "latest_economics_dashboard.json"),
                min_notional_multiplier=Decimal("1.0"),
                max_notional_multiplier=Decimal("1.0"),
                sizing_flow_weight=Decimal("0"),
                sizing_crowding_weight=Decimal("0"),
                sizing_divergence_penalty_weight=Decimal("0"),
                sizing_funding_penalty_weight=Decimal("0"),
                volatility_target_atr_fraction=None,
                send_orders=False,
            ),
        )
        asyncio.run(_feed_buy_agg_trades(runner))
        asyncio.run(_feed_breakout_prices(runner, [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("101")]))

        assert len(gateway.submit_calls) == 1
        assert Decimal(runner.status.last_economics_feedback_multiplier) < Decimal("1")
        assert Decimal(runner.status.last_target_notional_usdt) < Decimal("100")
