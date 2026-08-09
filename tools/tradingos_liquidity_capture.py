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
SCHEMA = "tradingos.binance_liquidity_capture.v1"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_LIMIT = 500
_ALLOWED_LIMITS = {5, 10, 20, 50, 100, 500, 1000}
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")


def default_fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "TradingOS-LiquidityLens/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310 - fixed Binance public host only
        return json.loads(response.read().decode("utf-8"))


def validate_symbols(symbols: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for raw in symbols:
        symbol = str(raw).strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol) or not symbol.endswith("USDT"):
            raise ValueError(f"unsupported symbol format: {raw}")
        if symbol not in out:
            out.append(symbol)
    if not out:
        raise ValueError("at least one symbol is required")
    if len(out) > 20:
        raise ValueError("liquidity watchlist is limited to 20 symbols")
    return out


def capture(
    symbols: list[str] | tuple[str, ...] = DEFAULT_SYMBOLS,
    limit: int = DEFAULT_LIMIT,
    fetch_json: Callable[[str], Any] = default_fetch_json,
    now: datetime | None = None,
) -> dict[str, Any]:
    if limit not in _ALLOWED_LIMITS:
        raise ValueError(f"unsupported depth limit: {limit}")
    symbols_v = validate_symbols(symbols)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("capture clock must be timezone-aware")
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "captured_at": clock.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "symbols": symbols_v,
        "limit": limit,
        "credentials_used": False,
        "private_api_used": False,
        "source": "binance_usds_futures_public_depth",
        "books": {},
    }
    for symbol in symbols_v:
        url = FAPI + "/fapi/v1/depth?" + urllib.parse.urlencode({"symbol": symbol, "limit": limit})
        payload["books"][symbol] = {
            "source_url": url,
            "snapshot": fetch_json(url),
        }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture public Binance USD-S futures depth for TradingOS Liquidity Lens")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = capture([x for x in args.symbols.split(",") if x.strip()], args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except Exception as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", "symbols": payload["symbols"], "output": str(args.output), "can_trade": False, "capital_permission": "DENY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
