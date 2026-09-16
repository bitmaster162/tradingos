#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R82_PATH = ROOT / "tools" / "binance_spot_event_time_quote_collector.py"
SPEC = importlib.util.spec_from_file_location("r93_r82", R82_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R82 quote module")
r82 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r82
SPEC.loader.exec_module(r82)

SCHEMA = "tradingos.r93_market_integrity.v1"
CONTRACT_ID = "R93_CROSS_VENUE_MARKET_INTEGRITY_V1_20260916"
BYBIT_HOST = "api.bybit.com"
BYBIT_PATH = "/v5/market/orderbook"
MAX_RAW_BYTES = 1_000_000
MAX_BINANCE_AGE_MS = 2000
MAX_REFERENCE_AGE_MS = 2000
MAX_REFERENCE_FUTURE_SKEW_MS = 500
MAX_BINANCE_SPREAD_BPS = Decimal("10")
MAX_REFERENCE_SPREAD_BPS = Decimal("12")
MAX_MID_DIVERGENCE_BPS = Decimal("20")


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def dec(value: Any, name: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{name}") from exc
    if not out.is_finite() or out <= 0:
        raise ValueError(f"invalid_{name}")
    return out


def spread_bps(bid: Decimal, ask: Decimal) -> Decimal:
    mid = (bid + ask) / Decimal("2")
    return (ask - bid) / mid * Decimal("10000")
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("reference_redirect_rejected")


def bybit_url(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol.isalnum():
        raise ValueError("symbol_invalid")
    query = urllib.parse.urlencode({"category": "spot", "symbol": symbol, "limit": "1"})
    return f"https://{BYBIT_HOST}{BYBIT_PATH}?{query}"


def fetch_bybit_orderbook(symbol: str, timeout_s: float = 5.0) -> dict[str, Any]:
    started = int(time.time() * 1000)
    req = urllib.request.Request(bybit_url(symbol), headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(req, timeout=timeout_s) as resp:
        body = resp.read(MAX_RAW_BYTES + 1)
        if len(body) > MAX_RAW_BYTES:
            raise RuntimeError("reference_body_too_large")
    received = int(time.time() * 1000)
    payload = json.loads(body.decode("utf-8"))
    if payload.get("retCode") != 0:
        raise RuntimeError("reference_provider_error")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("s") != symbol:
        raise RuntimeError("reference_symbol_mismatch")
    bids, asks = result.get("b"), result.get("a")
    if not isinstance(bids, list) or not bids or not isinstance(asks, list) or not asks:
        raise RuntimeError("reference_book_empty")
    bid = dec(bids[0][0], "reference_bid")
    ask = dec(asks[0][0], "reference_ask")
    if bid >= ask:
        raise RuntimeError("reference_crossed_or_locked")
    ts = result.get("ts")
    cts = result.get("cts")
    if type(ts) is not int or ts <= 0:
        raise RuntimeError("reference_timestamp_invalid")
    if cts is not None and (type(cts) is not int or cts <= 0):
        raise RuntimeError("reference_cts_invalid")
    return {
        "venue": "BYBIT_SPOT",
        "source": "bybit_v5_spot_orderbook_l1",
        "symbol": symbol,
        "bid": str(bid),
        "ask": str(ask),
        "provider_book_time_ms": ts,
        "provider_cross_sequence_time_ms": cts,
        "provider_response_time_ms": payload.get("time"),
        "request_started_at_ms": started,
        "received_at_ms": received,
        "update_id": result.get("u"),
        "sequence": result.get("seq"),
        "raw_sha256": sha256_bytes(body),
    }


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "contract_id": CONTRACT_ID, "status": "FAIL_CLOSED",
            "reason": reason, "details": details, "can_trade": False,
            "capital_permission": "DENY"}
def evaluate(binance_quote: dict[str, Any], reference: dict[str, Any], *, final_time_ms: int | None = None) -> dict[str, Any]:
    final = int(time.time() * 1000) if final_time_ms is None else final_time_ms
    try:
        symbol = binance_quote.get("symbol")
        if not isinstance(symbol, str) or reference.get("symbol") != symbol:
            raise ValueError("symbol_mismatch")
        bin_age = r82.validate_quote_at_decision(
            binance_quote, decision_time_ms=final, max_age_ms=MAX_BINANCE_AGE_MS
        )
        ref_ts = reference.get("provider_book_time_ms")
        if type(ref_ts) is not int or ref_ts <= 0:
            raise ValueError("reference_timestamp_invalid")
        ref_age = final - ref_ts
        if ref_age < -MAX_REFERENCE_FUTURE_SKEW_MS:
            raise ValueError("reference_future_skew")
        if ref_age > MAX_REFERENCE_AGE_MS:
            raise ValueError("reference_stale")
        bb = dec(binance_quote.get("bid"), "binance_bid")
        ba = dec(binance_quote.get("ask"), "binance_ask")
        rb = dec(reference.get("bid"), "reference_bid")
        ra = dec(reference.get("ask"), "reference_ask")
        if bb >= ba or rb >= ra:
            raise ValueError("crossed_or_locked")
    except Exception as exc:
        return fail("MARKET_INTEGRITY_INPUT_INVALID", error=str(exc))
    bin_spread = spread_bps(bb, ba)
    ref_spread = spread_bps(rb, ra)
    bin_mid = (bb + ba) / Decimal("2")
    ref_mid = (rb + ra) / Decimal("2")
    divergence = abs(bin_mid - ref_mid) / ((bin_mid + ref_mid) / Decimal("2")) * Decimal("10000")
    if bin_spread > MAX_BINANCE_SPREAD_BPS:
        return fail("BINANCE_SPREAD_TOO_WIDE", spread_bps=str(bin_spread))
    if ref_spread > MAX_REFERENCE_SPREAD_BPS:
        return fail("REFERENCE_SPREAD_TOO_WIDE", spread_bps=str(ref_spread))
    if divergence > MAX_MID_DIVERGENCE_BPS:
        return fail("CROSS_VENUE_DIVERGENCE_TOO_WIDE", divergence_bps=str(divergence))
    return {
        "schema": SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "PASS",
        "symbol": symbol,
        "final_integrity_time_ms": final,
        "binance_age_ms": bin_age,
        "reference_age_ms": ref_age,
        "binance_spread_bps": str(bin_spread),
        "reference_spread_bps": str(ref_spread),
        "mid_divergence_bps": str(divergence),
        "thresholds": {
            "max_binance_age_ms": MAX_BINANCE_AGE_MS,
            "max_reference_age_ms": MAX_REFERENCE_AGE_MS,
            "max_binance_spread_bps": str(MAX_BINANCE_SPREAD_BPS),
            "max_reference_spread_bps": str(MAX_REFERENCE_SPREAD_BPS),
            "max_mid_divergence_bps": str(MAX_MID_DIVERGENCE_BPS),
        },
        "reference": reference,
        "can_trade": False,
        "capital_permission": "DENY",
    }


def live_check(binance_quote: dict[str, Any]) -> dict[str, Any]:
    try:
        reference = fetch_bybit_orderbook(str(binance_quote.get("symbol")))
    except Exception as exc:
        return fail("REFERENCE_FETCH_FAILED", error=str(exc))
    return evaluate(binance_quote, reference, final_time_ms=int(time.time() * 1000))
