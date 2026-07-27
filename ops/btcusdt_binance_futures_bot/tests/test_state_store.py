from decimal import Decimal
from btcusdt_bot.state.store import StateStore


def test_state_store_tracks_normal_and_algo_orders_separately() -> None:
    store = StateStore()

    store.apply_order_trade_update(
        {
            "E": 1000,
            "o": {
                "c": "ENT-1",
                "s": "BTCUSDT",
                "S": "BUY",
                "ps": "BOTH",
                "o": "LIMIT",
                "X": "NEW",
                "f": "GTX",
                "i": 123,
                "q": "0.002",
                "z": "0",
                "p": "65000",
                "ap": "0",
                "R": False,
                "cp": False,
            },
        }
    )
    store.apply_algo_update(
        {
            "E": 1001,
            "o": {
                "caid": "STP-1",
                "aid": 456,
                "s": "BTCUSDT",
                "S": "SELL",
                "ps": "BOTH",
                "o": "STOP_MARKET",
                "X": "NEW",
                "f": "GTC",
                "q": "0.002",
                "aq": "0",
                "p": "0",
                "ap": "0",
                "tp": "64000",
                "wt": "CONTRACT_PRICE",
                "R": True,
                "cp": False,
            },
        }
    )

    assert store.state.open_normal_orders == 1
    assert store.state.open_algo_orders == 1
    assert "ENT-1" in store.state.normal_orders
    assert "STP-1" in store.state.algo_orders



def test_state_store_ingests_trade_fill_from_order_trade_update() -> None:
    store = StateStore()

    store.apply_order_trade_update(
        {
            "E": 1_700_000_000_123,
            "o": {
                "c": "ENT-2",
                "s": "BTCUSDT",
                "S": "BUY",
                "ps": "BOTH",
                "o": "LIMIT",
                "X": "PARTIALLY_FILLED",
                "x": "TRADE",
                "f": "GTX",
                "i": 789,
                "q": "0.002",
                "z": "0.001",
                "l": "0.001",
                "p": "65000",
                "L": "65010",
                "ap": "65010",
                "t": 456123,
                "m": True,
                "R": False,
                "cp": False,
                "n": "0.050",
                "N": "USDT",
                "rp": "1.250",
                "T": 1_700_000_000_120,
            },
        }
    )

    fill = store.state.trade_fills[456123]
    assert fill.client_order_id == "ENT-2"
    assert fill.order_id == 789
    assert fill.qty == Decimal("0.001")
    assert fill.price == Decimal("65010")
    assert fill.quote_qty == Decimal("65.010")
    assert fill.maker is True
    assert fill.commission == Decimal("0.050")
    assert fill.realized_pnl == Decimal("1.250")
