from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from btcusdt_bot.connectors.rest_client import BinanceAPIError, BinanceRESTClient
from btcusdt_bot.state.store import StateStore


_NOT_FOUND_MARKERS = (
    "unknown order",
    "does not exist",
    "not found",
    "unknown client order",
    "unknown clientalgoid",
)
_UNKNOWN_EXECUTION_MARKERS = (
    "unknown error",
    "execution status unknown",
)


@dataclass(slots=True)
class QueryResolution:
    kind: str
    symbol: str
    found: bool
    requested_by: str
    identifier: str
    response: Any = None
    headers: dict[str, str] | None = None
    updated_store: bool = False
    error: dict[str, Any] | None = None

    @property
    def not_found(self) -> bool:
        return bool(self.error and self.error.get("not_found"))

    @property
    def execution_unknown(self) -> bool:
        return bool(self.error and self.error.get("execution_unknown"))


class QueryResolver:
    def __init__(self, *, client: BinanceRESTClient, store: StateStore | None = None) -> None:
        self.client = client
        self.store = store

    def query_normal(
        self,
        *,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> QueryResolution:
        requested_by, identifier = _select_identifier(order_id, client_order_id)
        try:
            result = self.client.query_order(symbol, order_id=order_id, client_order_id=client_order_id)
        except BinanceAPIError as exc:
            return QueryResolution(
                kind="normal",
                symbol=symbol,
                found=False,
                requested_by=requested_by,
                identifier=identifier,
                error=_classify_error(exc),
            )

        updated_store = False
        if self.store is not None and isinstance(result.data, dict):
            self.store.ingest_headers(result.headers)
            self.store.upsert_normal_order_from_rest(result.data)
            updated_store = True

        return QueryResolution(
            kind="normal",
            symbol=symbol,
            found=isinstance(result.data, dict),
            requested_by=requested_by,
            identifier=identifier,
            response=result.data,
            headers=result.headers,
            updated_store=updated_store,
        )

    def query_algo(
        self,
        *,
        symbol: str,
        algo_id: int | None = None,
        client_algo_id: str | None = None,
    ) -> QueryResolution:
        requested_by, identifier = _select_identifier(algo_id, client_algo_id)
        try:
            result = self.client.query_algo_order(algo_id=algo_id, client_algo_id=client_algo_id)
        except BinanceAPIError as exc:
            return QueryResolution(
                kind="algo",
                symbol=symbol,
                found=False,
                requested_by=requested_by,
                identifier=identifier,
                error=_classify_error(exc),
            )

        updated_store = False
        if self.store is not None and isinstance(result.data, dict):
            self.store.ingest_headers(result.headers)
            self.store.upsert_algo_order_from_rest(result.data)
            updated_store = True

        return QueryResolution(
            kind="algo",
            symbol=symbol,
            found=isinstance(result.data, dict),
            requested_by=requested_by,
            identifier=identifier,
            response=result.data,
            headers=result.headers,
            updated_store=updated_store,
        )


def _select_identifier(numeric_id: int | None, client_id: str | None) -> tuple[str, str]:
    if numeric_id is not None:
        return "id", str(numeric_id)
    return "client_id", str(client_id or "")


def _classify_error(exc: BinanceAPIError) -> dict[str, Any]:
    message = exc.message or ""
    lowered = message.lower()
    return {
        "status": exc.status,
        "code": exc.code,
        "message": message,
        "body": exc.body,
        "not_found": any(marker in lowered for marker in _NOT_FOUND_MARKERS),
        "execution_unknown": exc.status == 503 and any(marker in lowered for marker in _UNKNOWN_EXECUTION_MARKERS),
    }
