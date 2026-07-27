from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from btcusdt_bot.connectors.signing import build_signed_query, now_ms
from btcusdt_bot.domain.models import APICallResult


class BinanceAPIError(RuntimeError):
    def __init__(self, status: int, code: int | None, message: str, body: Any = None):
        super().__init__(f"Binance API error status={status} code={code} message={message}")
        self.status = status
        self.code = code
        self.message = message
        self.body = body


@dataclass(slots=True)
class BinanceRESTClient:
    base_url: str
    api_key: str = ""
    api_secret: str = ""
    recv_window_ms: int = 5000
    timeout_s: float = 10.0

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        *,
        signed: bool = False,
        api_key_required: bool = False,
    ) -> APICallResult:
        params = dict(params or {})
        headers: dict[str, str] = {"User-Agent": "btcusdt-binance-futures-bot/0.1.0"}

        if api_key_required or signed:
            if not self.api_key:
                raise ValueError("API key is required for this endpoint.")
            headers["X-MBX-APIKEY"] = self.api_key

        query = ""
        if signed:
            if not self.api_secret:
                raise ValueError("API secret is required for signed endpoint.")
            params.setdefault("timestamp", now_ms())
            params.setdefault("recvWindow", self.recv_window_ms)
            query = build_signed_query(params, self.api_secret)
        elif params:
            query = urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)

        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        request = Request(url=url, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else None
                return APICallResult(data=data, headers=dict(response.headers))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            body: Any
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
            code = body.get("code") if isinstance(body, dict) else None
            msg = body.get("msg") if isinstance(body, dict) else raw
            raise BinanceAPIError(exc.code, code, msg, body) from exc
        except URLError as exc:
            raise RuntimeError(f"Network error calling Binance: {exc}") from exc

    def ping(self) -> APICallResult:
        return self._request("GET", "/fapi/v1/ping")

    def exchange_info(self) -> APICallResult:
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def server_time(self) -> APICallResult:
        return self._request("GET", "/fapi/v1/time")

    def depth(self, symbol: str, limit: int = 1000) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/depth",
            params={"symbol": symbol, "limit": limit},
        )

    def rpi_depth(self, symbol: str, limit: int = 1000) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/rpiDepth",
            params={"symbol": symbol, "limit": limit},
        )

    def symbol_config(self, symbol: str | None = None) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/symbolConfig",
            params={"symbol": symbol},
            signed=True,
        )

    def account_v3(self) -> APICallResult:
        return self._request("GET", "/fapi/v3/account", signed=True)

    def position_risk_v3(self, symbol: str | None = None) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v3/positionRisk",
            params={"symbol": symbol},
            signed=True,
        )

    def leverage_brackets(self, symbol: str | None = None) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/leverageBracket",
            params={"symbol": symbol},
            signed=True,
        )

    def api_trading_status(self, symbol: str | None = None) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/apiTradingStatus",
            params={"symbol": symbol},
            signed=True,
        )

    def adl_quantile(self, symbol: str | None = None) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/adlQuantile",
            params={"symbol": symbol},
            signed=True,
        )

    def user_trades(
        self,
        symbol: str,
        *,
        order_id: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        from_id: int | None = None,
        limit: int = 500,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/userTrades",
            params={
                "symbol": symbol,
                "orderId": order_id,
                "startTime": start_time,
                "endTime": end_time,
                "fromId": from_id,
                "limit": limit,
            },
            signed=True,
        )

    def income_history(
        self,
        *,
        symbol: str | None = None,
        income_type: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        page: int | None = None,
        limit: int = 100,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/income",
            params={
                "symbol": symbol,
                "incomeType": income_type,
                "startTime": start_time,
                "endTime": end_time,
                "page": page,
                "limit": limit,
            },
            signed=True,
        )

    def commission_rate(self, symbol: str) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/commissionRate",
            params={"symbol": symbol},
            signed=True,
        )

    def open_interest(self, symbol: str) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/openInterest",
            params={"symbol": symbol},
        )

    def open_interest_hist(
        self,
        symbol: str,
        *,
        period: str,
        limit: int = 30,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/futures/data/openInterestHist",
            params={
                "symbol": symbol,
                "period": period,
                "limit": limit,
                "startTime": start_time,
                "endTime": end_time,
            },
        )

    def global_long_short_account_ratio(
        self,
        symbol: str,
        *,
        period: str,
        limit: int = 30,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/futures/data/globalLongShortAccountRatio",
            params={
                "symbol": symbol,
                "period": period,
                "limit": limit,
                "startTime": start_time,
                "endTime": end_time,
            },
        )

    def top_long_short_account_ratio(
        self,
        symbol: str,
        *,
        period: str,
        limit: int = 30,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/futures/data/topLongShortAccountRatio",
            params={
                "symbol": symbol,
                "period": period,
                "limit": limit,
                "startTime": start_time,
                "endTime": end_time,
            },
        )

    def top_long_short_position_ratio(
        self,
        symbol: str,
        *,
        period: str,
        limit: int = 30,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/futures/data/topLongShortPositionRatio",
            params={
                "symbol": symbol,
                "period": period,
                "limit": limit,
                "startTime": start_time,
                "endTime": end_time,
            },
        )

    def taker_buy_sell_ratio(
        self,
        symbol: str,
        *,
        period: str,
        limit: int = 30,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/futures/data/takerlongshortRatio",
            params={
                "symbol": symbol,
                "period": period,
                "limit": limit,
                "startTime": start_time,
                "endTime": end_time,
            },
        )

    def start_user_stream(self) -> APICallResult:
        return self._request(
            "POST",
            "/fapi/v1/listenKey",
            api_key_required=True,
        )

    def keepalive_user_stream(self, listen_key: str) -> APICallResult:
        return self._request(
            "PUT",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            api_key_required=True,
        )

    def close_user_stream(self, listen_key: str) -> APICallResult:
        return self._request(
            "DELETE",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            api_key_required=True,
        )

    def open_orders(self, symbol: str) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/openOrders",
            params={"symbol": symbol},
            signed=True,
        )

    def query_order(
        self,
        symbol: str,
        *,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/order",
            params={"symbol": symbol, "orderId": order_id, "origClientOrderId": client_order_id},
            signed=True,
        )

    def open_algo_orders(self, symbol: str) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/openAlgoOrders",
            params={"symbol": symbol},
            signed=True,
        )

    def query_algo_order(
        self,
        *,
        algo_id: int | None = None,
        client_algo_id: str | None = None,
    ) -> APICallResult:
        return self._request(
            "GET",
            "/fapi/v1/algoOrder",
            params={"algoId": algo_id, "clientAlgoId": client_algo_id},
            signed=True,
        )

    def countdown_cancel_all(self, symbol: str, countdown_time_ms: int) -> APICallResult:
        return self._request(
            "POST",
            "/fapi/v1/countdownCancelAll",
            params={"symbol": symbol, "countdownTime": countdown_time_ms},
            signed=True,
        )

    def place_order(self, payload: dict[str, object], *, test: bool = False) -> APICallResult:
        path = "/fapi/v1/order/test" if test else "/fapi/v1/order"
        return self._request("POST", path, params=payload, signed=True)

    def place_algo_order(self, payload: dict[str, object]) -> APICallResult:
        return self._request("POST", "/fapi/v1/algoOrder", params=payload, signed=True)

    def cancel_order(
        self,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> APICallResult:
        params: dict[str, object] = {
            "symbol": symbol,
            "orderId": order_id,
            "origClientOrderId": client_order_id,
        }
        return self._request("DELETE", "/fapi/v1/order", params=params, signed=True)

    def cancel_algo_order(
        self,
        symbol: str,
        algo_id: int | None = None,
        client_algo_id: str | None = None,
    ) -> APICallResult:
        params: dict[str, object] = {"symbol": symbol, "algoId": algo_id, "clientAlgoId": client_algo_id}
        return self._request("DELETE", "/fapi/v1/algoOrder", params=params, signed=True)
