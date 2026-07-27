from decimal import Decimal

from btcusdt_bot.domain.enums import OrderStatus, PositionSide, Side
from btcusdt_bot.domain.models import OrderRecord
from btcusdt_bot.reconcile_daemon import diff_runtime_states
from btcusdt_bot.state.store import RuntimeState


def test_diff_runtime_states_detects_open_order_and_position_divergence() -> None:
    local_state = RuntimeState()
    exchange_state = RuntimeState()

    local_state.normal_orders["ENT-1"] = OrderRecord(
        symbol="BTCUSDT",
        side=Side.BUY,
        position_side=PositionSide.BOTH,
        order_type="LIMIT",
        status=OrderStatus.NEW,
        tif="GTX",
        order_id=1,
        client_order_id="ENT-1",
        qty=Decimal("0.002"),
        executed_qty=Decimal("0"),
        price=Decimal("65000"),
        avg_price=Decimal("0"),
        reduce_only=False,
        close_position=False,
        update_time_ms=1000,
    )
    local_state.account.balances["USDT"] = Decimal("1000")
    local_state.account.positions[("BTCUSDT", PositionSide.BOTH)] = type("Pos", (), {
        "symbol": "BTCUSDT",
        "position_side": PositionSide.BOTH,
        "amount": Decimal("0.001"),
        "entry_price": Decimal("65000"),
    })()

    exchange_state.account.balances["USDT"] = Decimal("995")
    exchange_state.account.positions[("BTCUSDT", PositionSide.BOTH)] = type("Pos", (), {
        "symbol": "BTCUSDT",
        "position_side": PositionSide.BOTH,
        "amount": Decimal("0"),
        "entry_price": Decimal("0"),
    })()

    report = diff_runtime_states(local_state, exchange_state, symbol="BTCUSDT")

    assert report.has_divergence is True
    assert report.normal_only_local == ["ENT-1"]
    assert len(report.position_mismatches) == 1
    assert report.balance_mismatches[0].asset == "USDT"


def test_diff_runtime_states_ignores_terminal_normal_orders() -> None:
    local_state = RuntimeState()
    exchange_state = RuntimeState()

    local_state.normal_orders["ENT-1"] = OrderRecord(
        symbol="BTCUSDT",
        side=Side.BUY,
        position_side=PositionSide.BOTH,
        order_type="LIMIT",
        status=OrderStatus.CANCELED,
        tif="GTX",
        order_id=1,
        client_order_id="ENT-1",
        qty=Decimal("0.002"),
        executed_qty=Decimal("0"),
        price=Decimal("65000"),
        avg_price=Decimal("0"),
        reduce_only=False,
        close_position=False,
        update_time_ms=1000,
    )

    report = diff_runtime_states(local_state, exchange_state, symbol="BTCUSDT")

    assert report.has_divergence is False
    assert report.normal_only_local == []
