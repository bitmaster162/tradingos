from types import SimpleNamespace

from btcusdt_bot.authoritative.fetchers import AuthoritativeHistoryFetcher


class IncomePagingClient:
    def __init__(self) -> None:
        self.income_calls = []

    def user_trades(self, symbol, *, start_time=None, end_time=None, limit=None):
        return SimpleNamespace(data=[])

    def income_history(self, *, symbol=None, income_type=None, start_time=None, end_time=None, page=None, limit=None):
        self.income_calls.append((symbol, income_type, start_time, end_time, page, limit))
        if page == 1:
            return SimpleNamespace(data=[
                {"incomeType": "REALIZED_PNL", "income": "1", "tranId": "1", "tradeId": "10", "symbol": symbol, "time": start_time},
            ])
        return SimpleNamespace(data=[])


def test_income_history_fetcher_partitions_and_pages(tmp_path) -> None:
    client = IncomePagingClient()
    fetcher = AuthoritativeHistoryFetcher(
        client,
        symbol="BTCUSDT",
        income_limit=1,
        income_window_ms=1_000,
    )

    rows = fetcher.fetch_income_history_partitioned(0, 1_999)

    assert len(rows) == 2
    assert fetcher.stats.income_windows == 2
    assert fetcher.stats.income_requests == 4
    assert client.income_calls[0][2:5] == (0, 999, 1)
    assert client.income_calls[2][2:5] == (1000, 1999, 1)
