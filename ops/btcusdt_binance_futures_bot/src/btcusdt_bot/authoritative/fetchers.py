from __future__ import annotations

from dataclasses import dataclass

_MAX_USER_TRADES_LIMIT = 1000
_MAX_INCOME_LIMIT = 1000
_MAX_USER_TRADE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
_DEFAULT_INCOME_WINDOW_MS = 7 * 24 * 60 * 60 * 1000


@dataclass(slots=True)
class FetchStats:
    user_trade_requests: int = 0
    income_requests: int = 0
    user_trade_windows: int = 0
    income_windows: int = 0


class AuthoritativeHistoryFetcher:
    def __init__(
        self,
        client: object,
        *,
        symbol: str,
        user_trade_limit: int = _MAX_USER_TRADES_LIMIT,
        income_limit: int = _MAX_INCOME_LIMIT,
        income_window_ms: int = _DEFAULT_INCOME_WINDOW_MS,
    ) -> None:
        self.client = client
        self.symbol = symbol.upper()
        self.user_trade_limit = max(1, min(user_trade_limit, _MAX_USER_TRADES_LIMIT))
        self.income_limit = max(1, min(income_limit, _MAX_INCOME_LIMIT))
        self.income_window_ms = max(1, int(income_window_ms))
        self.stats = FetchStats()

    @staticmethod
    def user_trade_key(row: dict[str, object]) -> tuple[object, ...]:
        trade_id = int(row.get("id", row.get("tradeId", 0)) or 0)
        if trade_id > 0:
            return ("trade", trade_id)
        return (
            "fallback",
            int(row.get("orderId", 0) or 0),
            int(row.get("time", 0) or 0),
            str(row.get("price", "")),
            str(row.get("qty", "")),
            str(row.get("realizedPnl", "")),
        )

    @staticmethod
    def income_key(row: dict[str, object]) -> tuple[object, ...]:
        return (
            str(row.get("incomeType", "")),
            str(row.get("tranId", "")),
            str(row.get("tradeId", "")),
            str(row.get("symbol", "")),
            int(row.get("time", 0) or 0),
            str(row.get("income", "")),
        )

    def fetch_user_trades_partitioned(self, start_time_ms: int, end_time_ms: int) -> list[dict[str, object]]:
        seen: set[tuple[object, ...]] = set()
        results: list[dict[str, object]] = []
        stack: list[tuple[int, int]] = [(start_time_ms, end_time_ms)]

        while stack:
            window_start_ms, window_end_ms = stack.pop()
            if window_start_ms > window_end_ms:
                continue
            if window_end_ms - window_start_ms > _MAX_USER_TRADE_WINDOW_MS:
                split_ms = min(window_start_ms + _MAX_USER_TRADE_WINDOW_MS - 1, window_end_ms)
                stack.append((split_ms + 1, window_end_ms))
                stack.append((window_start_ms, split_ms))
                continue

            self.stats.user_trade_windows += 1
            self.stats.user_trade_requests += 1
            rows = self.client.user_trades(
                self.symbol,
                start_time=window_start_ms,
                end_time=window_end_ms,
                limit=self.user_trade_limit,
            ).data
            if not isinstance(rows, list):
                raise RuntimeError("user_trades_payload_invalid")
            if len(rows) >= self.user_trade_limit and window_start_ms < window_end_ms:
                split_ms = (window_start_ms + window_end_ms) // 2
                if split_ms > window_start_ms and split_ms < window_end_ms:
                    stack.append((split_ms + 1, window_end_ms))
                    stack.append((window_start_ms, split_ms))
                    continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                key = self.user_trade_key(row)
                if key in seen:
                    continue
                seen.add(key)
                results.append(row)

        results.sort(key=lambda row: (int(row.get("time", 0) or 0), int(row.get("id", row.get("tradeId", 0)) or 0)))
        return results

    def fetch_income_history_partitioned(
        self,
        start_time_ms: int,
        end_time_ms: int,
        *,
        income_type: str | None = None,
    ) -> list[dict[str, object]]:
        seen: set[tuple[object, ...]] = set()
        results: list[dict[str, object]] = []
        window_start_ms = int(start_time_ms)

        while window_start_ms <= end_time_ms:
            window_end_ms = min(end_time_ms, window_start_ms + self.income_window_ms - 1)
            page = 1
            self.stats.income_windows += 1
            while True:
                self.stats.income_requests += 1
                params = {
                    "symbol": self.symbol,
                    "start_time": window_start_ms,
                    "end_time": window_end_ms,
                    "page": page,
                    "limit": self.income_limit,
                }
                if income_type is not None:
                    params["income_type"] = income_type
                rows = self.client.income_history(**params).data
                if not isinstance(rows, list):
                    raise RuntimeError("income_history_payload_invalid")
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    key = self.income_key(row)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(row)
                if len(rows) < self.income_limit:
                    break
                page += 1
            window_start_ms = window_end_ms + 1

        results.sort(key=lambda row: (int(row.get("time", 0) or 0), str(row.get("tranId", ""))))
        return results
