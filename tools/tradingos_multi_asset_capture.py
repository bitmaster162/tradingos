#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_INTERVALS = ("1h", "4h", "1d")
ALLOWED_INTERVALS = {"1h", "4h", "1d"}
SCHEMA = "tradingos.binance_watchtower_capture.v1"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")


def default_fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "TradingOS-Watchtower/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310 - fixed Binance public hosts only
        return json.loads(response.read().decode("utf-8"))


def endpoint_url(host: str, path: str, params: dict[str, Any]) -> str:
    return host + path + "?" + urllib.parse.urlencode(params)


def validate_symbols(symbols: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for raw in symbols:
        symbol = str(raw).strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol) or not symbol.endswith("USDT"):
            raise ValueError(f"unsupported symbol format: {raw}")
        if symbol not in result:
            result.append(symbol)
    if not result:
        raise ValueError("at least one symbol is required")
    if len(result) > 20:
        raise ValueError("watchlist is limited to 20 symbols")
    return result


def validate_intervals(intervals: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for raw in intervals:
        interval = str(raw).strip()
        if interval not in ALLOWED_INTERVALS:
            raise ValueError(f"unsupported interval: {raw}")
        if interval not in result:
            result.append(interval)
    if set(result) != set(DEFAULT_INTERVALS):
        raise ValueError("watchtower v1 requires exactly 1h,4h,1d")
    return list(DEFAULT_INTERVALS)


def capture(
    symbols: list[str] | tuple[str, ...] = DEFAULT_SYMBOLS,
    intervals: list[str] | tuple[str, ...] = DEFAULT_INTERVALS,
    fetch_json: Callable[[str], Any] = default_fetch_json,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("capture clock must be timezone-aware")
    symbols_v = validate_symbols(symbols)
    intervals_v = validate_intervals(intervals)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "captured_at": clock.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "symbols": symbols_v,
        "intervals": intervals_v,
        "credentials_used": False,
        "private_api_used": False,
        "assets": {},
    }
    for symbol in symbols_v:
        asset: dict[str, Any] = {"source_urls": {}, "futures_klines": {}}
        fixed = {
            "futures_24h": (FAPI, "/fapi/v1/ticker/24hr", {"symbol": symbol}),
            "mark_price": (FAPI, "/fapi/v1/premiumIndex", {"symbol": symbol}),
            "open_interest": (FAPI, "/fapi/v1/openInterest", {"symbol": symbol}),
            "open_interest_stats_4h": (FAPI, "/futures/data/openInterestHist", {"symbol": symbol, "period": "4h", "limit": 30}),
            "funding_history": (FAPI, "/fapi/v1/fundingRate", {"symbol": symbol, "limit": 30}),
            "premium_index_4h": (FAPI, "/fapi/v1/premiumIndexKlines", {"symbol": symbol, "interval": "4h", "limit": 30}),
            "spot_klines_4h": (SPOT, "/api/v3/klines", {"symbol": symbol, "interval": "4h", "limit": 30}),
        }
        for key, (host, path, params) in fixed.items():
            url = endpoint_url(host, path, params)
            asset["source_urls"][key] = url
            asset[key] = fetch_json(url)
        for interval in intervals_v:
            limit = 60 if interval != "1d" else 50
            url = endpoint_url(FAPI, "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
            asset["source_urls"][f"futures_klines_{interval}"] = url
            asset["futures_klines"][interval] = fetch_json(url)
        payload["assets"][symbol] = asset
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture credential-free Binance public inputs for the TradingOS multi-asset watchtower")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        symbols = [item for item in args.symbols.split(",") if item.strip()]
        payload = capture(symbols=symbols)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except Exception as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", "symbols": payload["symbols"], "output": str(args.output), "can_trade": False, "capital_permission": "DENY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
