from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from btcusdt_bot.config import BotConfig
from btcusdt_bot.connectors.rest_client import BinanceAPIError, BinanceRESTClient
from btcusdt_bot.domain.enums import OrderType
from btcusdt_bot.domain.models import AlgoOrderProposal, APICallResult, OrderProposal, ValidationResult
from btcusdt_bot.execution.payloads import build_algo_order_payload, build_normal_order_payload
from btcusdt_bot.execution.query_resolver import QueryResolution, QueryResolver
from btcusdt_bot.execution.validator import ExecutionValidator
from btcusdt_bot.state.store import StateStore


_UNKNOWN_EXECUTION_MARKERS = ("unknown error", "execution status unknown")


@dataclass(slots=True)
class GatewayResult:
    payload: dict[str, Any]
    validation: ValidationResult | None
    sent: bool
    response: Any = None
    headers: dict[str, str] | None = None
    execution_unknown: bool = False
    error: dict[str, Any] | None = None


class ExecutionGateway:
    def __init__(
        self,
        config: BotConfig,
        *,
        client: BinanceRESTClient,
        store: StateStore | None = None,
        validator: ExecutionValidator | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.validator = validator
        self.query_resolver = QueryResolver(client=client, store=store)

    def _ingest_headers(self, result: APICallResult) -> None:
        if self.store is not None:
            self.store.ingest_headers(result.headers)

    def query_normal(
        self,
        *,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> QueryResolution:
        return self.query_resolver.query_normal(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
        )

    def query_algo(
        self,
        *,
        symbol: str,
        algo_id: int | None = None,
        client_algo_id: str | None = None,
    ) -> QueryResolution:
        return self.query_resolver.query_algo(
            symbol=symbol,
            algo_id=algo_id,
            client_algo_id=client_algo_id,
        )

    def submit_normal(
        self,
        proposal: OrderProposal,
        *,
        reference_price: Decimal | None = None,
        dry_run: bool = True,
        test: bool = False,
    ) -> GatewayResult:
        validation: ValidationResult | None = None
        if self.validator is not None:
            if proposal.order_type == OrderType.LIMIT:
                if proposal.price is None:
                    raise ValueError("LIMIT order requires price")
                validation = self.validator.validate_limit(
                    price=proposal.price,
                    qty=proposal.qty,
                    reference_price=reference_price,
                )
                if not validation.ok:
                    return GatewayResult(payload={}, validation=validation, sent=False)
                proposal = OrderProposal(
                    symbol=proposal.symbol,
                    side=proposal.side,
                    position_side=proposal.position_side,
                    order_type=proposal.order_type,
                    tif=proposal.tif,
                    qty=validation.normalized_qty or proposal.qty,
                    price=validation.normalized_price,
                    reduce_only=proposal.reduce_only,
                    close_position=proposal.close_position,
                    working_type=proposal.working_type,
                    client_id=proposal.client_id,
                )
            elif proposal.order_type == OrderType.MARKET and reference_price is not None:
                validation = self.validator.validate_market(qty=proposal.qty, mark_price=reference_price)
                if not validation.ok:
                    return GatewayResult(payload={}, validation=validation, sent=False)
                proposal = OrderProposal(
                    symbol=proposal.symbol,
                    side=proposal.side,
                    position_side=proposal.position_side,
                    order_type=proposal.order_type,
                    tif=proposal.tif,
                    qty=validation.normalized_qty or proposal.qty,
                    price=None,
                    reduce_only=proposal.reduce_only,
                    close_position=proposal.close_position,
                    working_type=proposal.working_type,
                    client_id=proposal.client_id,
                )

        payload = build_normal_order_payload(
            proposal,
            include_position_side=self.config.position_mode != "ONE_WAY",
        )
        if dry_run:
            return GatewayResult(payload=payload, validation=validation, sent=False)

        try:
            result = self.client.place_order(payload, test=test)
        except BinanceAPIError as exc:
            return GatewayResult(
                payload=payload,
                validation=validation,
                sent=False,
                execution_unknown=_is_unknown_execution(exc),
                error=_error_payload(exc),
            )
        self._ingest_headers(result)
        return GatewayResult(
            payload=payload,
            validation=validation,
            sent=True,
            response=result.data,
            headers=result.headers,
        )

    def submit_algo(self, proposal: AlgoOrderProposal, *, dry_run: bool = True) -> GatewayResult:
        payload = build_algo_order_payload(
            proposal,
            include_position_side=self.config.position_mode != "ONE_WAY",
        )
        if dry_run:
            return GatewayResult(payload=payload, validation=None, sent=False)

        try:
            result = self.client.place_algo_order(payload)
        except BinanceAPIError as exc:
            return GatewayResult(
                payload=payload,
                validation=None,
                sent=False,
                execution_unknown=_is_unknown_execution(exc),
                error=_error_payload(exc),
            )
        self._ingest_headers(result)
        return GatewayResult(payload=payload, validation=None, sent=True, response=result.data, headers=result.headers)

    def cancel_normal(
        self,
        *,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
        dry_run: bool = True,
    ) -> GatewayResult:
        payload = {"symbol": symbol, "orderId": order_id, "origClientOrderId": client_order_id}
        payload = {key: value for key, value in payload.items() if value is not None}
        if dry_run:
            return GatewayResult(payload=payload, validation=None, sent=False)

        try:
            result = self.client.cancel_order(symbol=symbol, order_id=order_id, client_order_id=client_order_id)
        except BinanceAPIError as exc:
            return GatewayResult(
                payload=payload,
                validation=None,
                sent=False,
                execution_unknown=_is_unknown_execution(exc),
                error=_error_payload(exc),
            )
        self._ingest_headers(result)
        return GatewayResult(payload=payload, validation=None, sent=True, response=result.data, headers=result.headers)

    def cancel_algo(
        self,
        *,
        symbol: str,
        algo_id: int | None = None,
        client_algo_id: str | None = None,
        dry_run: bool = True,
    ) -> GatewayResult:
        payload = {"symbol": symbol, "algoId": algo_id, "clientAlgoId": client_algo_id}
        payload = {key: value for key, value in payload.items() if value is not None}
        if dry_run:
            return GatewayResult(payload=payload, validation=None, sent=False)

        try:
            result = self.client.cancel_algo_order(symbol=symbol, algo_id=algo_id, client_algo_id=client_algo_id)
        except BinanceAPIError as exc:
            return GatewayResult(
                payload=payload,
                validation=None,
                sent=False,
                execution_unknown=_is_unknown_execution(exc),
                error=_error_payload(exc),
            )
        self._ingest_headers(result)
        return GatewayResult(payload=payload, validation=None, sent=True, response=result.data, headers=result.headers)

    def refresh_countdown(self, *, dry_run: bool = True) -> GatewayResult:
        payload = {"symbol": self.config.symbol, "countdownTime": self.config.countdown_cancel_ms}
        if dry_run:
            return GatewayResult(payload=payload, validation=None, sent=False)

        try:
            result = self.client.countdown_cancel_all(self.config.symbol, self.config.countdown_cancel_ms)
        except BinanceAPIError as exc:
            return GatewayResult(
                payload=payload,
                validation=None,
                sent=False,
                execution_unknown=_is_unknown_execution(exc),
                error=_error_payload(exc),
            )
        self._ingest_headers(result)
        return GatewayResult(payload=payload, validation=None, sent=True, response=result.data, headers=result.headers)


def _is_unknown_execution(exc: BinanceAPIError) -> bool:
    lowered = (exc.message or "").lower()
    return exc.status == 503 and any(marker in lowered for marker in _UNKNOWN_EXECUTION_MARKERS)


def _error_payload(exc: BinanceAPIError) -> dict[str, Any]:
    return {
        "status": exc.status,
        "code": exc.code,
        "message": exc.message,
        "body": exc.body,
    }
