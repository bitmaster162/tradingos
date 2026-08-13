#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SYMBOL = "BTCUSDT"
FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"

ENDPOINTS = {
    "futures_24h": (FAPI, "/fapi/v1/ticker/24hr", {"symbol": SYMBOL}),
    "mark_price": (FAPI, "/fapi/v1/premiumIndex", {"symbol": SYMBOL}),
    "open_interest": (FAPI, "/fapi/v1/openInterest", {"symbol": SYMBOL}),
    "open_interest_stats_4h": (FAPI, "/futures/data/openInterestHist", {"symbol": SYMBOL, "period": "4h", "limit": 30}),
    "funding_history": (FAPI, "/fapi/v1/fundingRate", {"symbol": SYMBOL, "limit": 30}),
    "futures_klines_4h": (FAPI, "/fapi/v1/klines", {"symbol": SYMBOL, "interval": "4h", "limit": 30}),
    "spot_klines_4h": (SPOT, "/api/v3/klines", {"symbol": SYMBOL, "interval": "4h", "limit": 30}),
    "premium_index_4h": (FAPI, "/fapi/v1/premiumIndexKlines", {"symbol": SYMBOL, "interval": "4h", "limit": 30}),
}


def default_fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "TradingOS-Decision-Brief/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310 - frozen public Binance hosts only
        return json.loads(response.read().decode("utf-8"))


def endpoint_url(host: str, path: str, params: dict[str, Any]) -> str:
    return host + path + "?" + urllib.parse.urlencode(params)


def capture(fetch_json: Callable[[str], Any] = default_fetch_json, now: datetime | None = None) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("capture clock must be timezone-aware")
    result: dict[str, Any] = {
        "schema": "tradingos.binance_public_capture.v1",
        "symbol": SYMBOL,
        "captured_at": clock.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "credentials_used": False,
        "private_api_used": False,
        "source_urls": {},
    }
    for key, (host, path, params) in ENDPOINTS.items():
        url = endpoint_url(host, path, params)
        result["source_urls"][key] = url
        result[key] = fetch_json(url)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture credential-free public BTCUSDT Binance inputs for TradingOS Decision Brief")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = capture()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except Exception as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", "output": str(args.output), "can_trade": False, "capital_permission": "DENY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
