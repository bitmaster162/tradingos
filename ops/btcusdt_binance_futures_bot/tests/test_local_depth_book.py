from decimal import Decimal

import pytest

from btcusdt_bot.simulator.depth_book import DepthBookSyncError, LocalDepthBook


def test_local_depth_book_bootstrap_and_apply_diff() -> None:
    snapshot = {
        "lastUpdateId": 100,
        "E": 1000,
        "T": 999,
        "bids": [["100.0", "2.0"], ["99.5", "1.0"]],
        "asks": [["100.5", "3.0"], ["101.0", "1.5"]],
    }
    buffered = [
        {"e": "depthUpdate", "E": 1001, "T": 1000, "U": 95, "u": 101, "pu": 94, "b": [["100.0", "1.5"]], "a": []},
        {"e": "depthUpdate", "E": 1002, "T": 1001, "U": 102, "u": 103, "pu": 101, "b": [], "a": [["100.5", "2.0"]]},
    ]
    book = LocalDepthBook(symbol="BTCUSDT", levels=5)

    applied = book.bootstrap_from_buffer(snapshot, buffered)
    view = book.snapshot(levels=2)

    assert applied == 2
    assert view.last_update_id == 103
    assert view.best_bid_price == Decimal("100.0")
    assert view.best_ask_price == Decimal("100.5")
    assert view.bids[0].qty == Decimal("1.5")
    assert view.asks[0].qty == Decimal("2.0")


def test_local_depth_book_detects_sequence_gap() -> None:
    snapshot = {
        "lastUpdateId": 100,
        "bids": [["100.0", "2.0"]],
        "asks": [["100.5", "3.0"]],
    }
    buffered = [
        {"e": "depthUpdate", "E": 1001, "T": 1000, "U": 100, "u": 101, "pu": 99, "b": [], "a": []},
    ]
    book = LocalDepthBook(symbol="BTCUSDT", levels=5)
    book.bootstrap_from_buffer(snapshot, buffered)

    with pytest.raises(DepthBookSyncError):
        book.apply_diff_event({"e": "depthUpdate", "E": 1002, "T": 1001, "U": 105, "u": 106, "pu": 999, "b": [], "a": []})
