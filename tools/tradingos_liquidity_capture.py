#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA = "tradingos.binance_liquidity_capture.v1"
VERSION = "1.1.0"
SOURCE = "binance_usds_futures_public_depth"
FAPI_SCHEME = "https"
FAPI_HOST = "fapi.binance.com"
FAPI_PATH = "/fapi/v1/depth"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_LIMIT = 500
_ALLOWED_LIMITS = frozenset({5, 10, 20, 50, 100, 500, 1000})
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_SYMBOLS = 20
USER_AGENT = "TradingOS-LiquidityCapture/1.1"

FetchJson = Callable[[str], Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, "redirects are forbidden", headers, fp)


def _exact_public_url(symbol: str, limit: int) -> str:
    query = urllib.parse.urlencode({"symbol": symbol, "limit": limit})
    return urllib.parse.urlunsplit((FAPI_SCHEME, FAPI_HOST, FAPI_PATH, query, ""))


def _validate_exact_url(url: str) -> None:
    if not isinstance(url, str) or not url:
        raise ValueError("capture URL must be a non-empty string")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != FAPI_SCHEME or parsed.hostname != FAPI_HOST or parsed.port is not None:
        raise ValueError("capture URL host/scheme is not allowlisted")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("capture URL credentials are forbidden")
    if parsed.path != FAPI_PATH or parsed.fragment:
        raise ValueError("capture URL path/fragment is not allowlisted")
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if len(pairs) != 2 or {k for k, _ in pairs} != {"symbol", "limit"}:
        raise ValueError("capture URL query contract violated")
    values = dict(pairs)
    symbol = values.get("symbol", "")
    limit_text = values.get("limit", "")
    if not _SYMBOL_RE.fullmatch(symbol) or not symbol.endswith("USDT"):
        raise ValueError("capture URL symbol is invalid")
    try:
        limit = int(limit_text)
    except ValueError as exc:
        raise ValueError("capture URL limit is invalid") from exc
    if str(limit) != limit_text or limit not in _ALLOWED_LIMITS:
        raise ValueError("capture URL limit is unsupported")
    if url != _exact_public_url(symbol, limit):
        raise ValueError("capture URL must use canonical encoding/order")


def default_fetch_json(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    """Fetch one fixed-host public depth snapshot. No retries and no redirects."""
    _validate_exact_url(url)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be numeric")
    timeout_f = float(timeout)
    if not math.isfinite(timeout_f) or timeout_f <= 0 or timeout_f > MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout out of bounds")

    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    with opener.open(request, timeout=timeout_f) as response:  # nosec B310 - exact allowlisted HTTPS host/path only
        status = getattr(response, "status", None)
        if status != 200:
            raise ValueError(f"unexpected HTTP status: {status}")
        final_url = response.geturl()
        if final_url != url:
            raise ValueError("response URL changed unexpectedly")
        content_type = response.headers.get_content_type()
        if content_type not in {"application/json", "text/json"}:
            raise ValueError(f"unexpected content type: {content_type}")
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response exceeds byte limit")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("response is not valid UTF-8 JSON") from exc


def validate_symbols(symbols: list[str] | tuple[str, ...]) -> list[str]:
    if not isinstance(symbols, (list, tuple)):
        raise ValueError("symbols must be a list or tuple")
    out: list[str] = []
    for raw in symbols:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("symbol must be a non-empty string")
        symbol = raw.strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol) or not symbol.endswith("USDT"):
            raise ValueError(f"unsupported symbol format: {raw}")
        if symbol in out:
            raise ValueError(f"duplicate symbol: {symbol}")
        out.append(symbol)
    if not out:
        raise ValueError("at least one symbol is required")
    if len(out) > MAX_SYMBOLS:
        raise ValueError(f"liquidity watchlist is limited to {MAX_SYMBOLS} symbols")
    return out


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be positive and finite")
    return number


def _validate_levels(raw: Any, side: str, limit: int) -> list[list[str]]:
    if not isinstance(raw, list) or len(raw) < 5:
        raise ValueError(f"{side}: at least 5 levels required")
    if len(raw) > limit:
        raise ValueError(f"{side}: response exceeds requested limit")
    normalized: list[list[str]] = []
    prices: list[float] = []
    for index, row in enumerate(raw):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError(f"{side}[{index}]: malformed level")
        price = _positive_number(row[0], f"{side}[{index}].price")
        qty = _positive_number(row[1], f"{side}[{index}].qty")
        prices.append(price)
        normalized.append([str(row[0]), str(row[1])])
        if not math.isfinite(qty):  # defensive; _positive_number already enforces this
            raise ValueError(f"{side}[{index}].qty must be finite")
    if len(set(prices)) != len(prices):
        raise ValueError(f"{side}: duplicate price levels")
    if side == "bids" and any(prices[i] <= prices[i + 1] for i in range(len(prices) - 1)):
        raise ValueError("bids must be strictly descending")
    if side == "asks" and any(prices[i] >= prices[i + 1] for i in range(len(prices) - 1)):
        raise ValueError("asks must be strictly ascending")
    return normalized


def validate_snapshot(snapshot: Any, *, symbol: str, limit: int) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError(f"{symbol}: snapshot must be an object")
    if set(snapshot) != {"lastUpdateId", "bids", "asks"}:
        raise ValueError(f"{symbol}: unexpected depth payload fields")
    update_id = snapshot.get("lastUpdateId")
    if not isinstance(update_id, int) or isinstance(update_id, bool) or update_id < 0:
        raise ValueError(f"{symbol}: lastUpdateId must be a non-negative integer")
    bids = _validate_levels(snapshot.get("bids"), "bids", limit)
    asks = _validate_levels(snapshot.get("asks"), "asks", limit)
    if float(bids[0][0]) >= float(asks[0][0]):
        raise ValueError(f"{symbol}: crossed/locked book")
    return {"lastUpdateId": update_id, "bids": bids, "asks": asks}


def capture(
    symbols: list[str] | tuple[str, ...] = DEFAULT_SYMBOLS,
    limit: int = DEFAULT_LIMIT,
    fetch_json: FetchJson = default_fetch_json,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not callable(fetch_json):
        raise ValueError("fetch_json must be callable")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit not in _ALLOWED_LIMITS:
        raise ValueError(f"unsupported depth limit: {limit}")
    symbols_v = validate_symbols(symbols)
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("capture clock must be timezone-aware")
    captured_at = clock.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    books: dict[str, Any] = {}
    for symbol in symbols_v:
        url = _exact_public_url(symbol, limit)
        _validate_exact_url(url)
        raw = fetch_json(url)
        snapshot = validate_snapshot(raw, symbol=symbol, limit=limit)
        books[symbol] = {"source_url": url, "snapshot": snapshot}

    return {
        "schema": SCHEMA,
        "version": VERSION,
        "captured_at": captured_at,
        "symbols": symbols_v,
        "limit": limit,
        "credentials_used": False,
        "private_api_used": False,
        "source": SOURCE,
        "books": books,
        "transport_policy": {
            "scheme": FAPI_SCHEME,
            "host": FAPI_HOST,
            "path": FAPI_PATH,
            "redirects_allowed": False,
            "retries": 0,
            "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "credentials_allowed": False,
        },
        "safety": {
            "public_market_data_only": True,
            "credentials_used": False,
            "private_api_used": False,
            "telegram_send": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture public Binance USD-S futures depth for TradingOS Liquidity Lens")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = capture([x for x in args.symbols.split(",") if x.strip()], args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except Exception as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False, "capital_permission": "DENY"}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "result": "PASS",
                "symbols": payload["symbols"],
                "output": str(args.output),
                "can_trade": False,
                "capital_permission": "DENY",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
