from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from datetime import datetime, timezone
from typing import Any

CAPTURE_SCHEMA = "tradingos.binance_liquidity_capture.v1"
CAPTURE_VERSION = "1.1.0"
CAPTURE_SOURCE = "binance_usds_futures_public_depth"
CAPTURE_FIELDS = {
    "schema",
    "version",
    "captured_at",
    "symbols",
    "limit",
    "credentials_used",
    "private_api_used",
    "source",
    "books",
    "transport_policy",
    "safety",
}
EXPECTED_TRANSPORT_POLICY = {
    "scheme": "https",
    "host": "fapi.binance.com",
    "path": "/fapi/v1/depth",
    "redirects_allowed": False,
    "retries": 0,
    "default_timeout_seconds": 5.0,
    "max_timeout_seconds": 10.0,
    "max_response_bytes": 2_000_000,
    "credentials_allowed": False,
}
SCHEMA = "tradingos.liquidity_lens.v1"
VERSION = "1.1.0"
BANDS_BPS = (10, 25, 50)
WALL_MULTIPLIER = 3.0
MIN_LEVELS = 5
_ALLOWED_LIMITS = {5, 10, 20, 50, 100, 500, 1000}
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")


def _num(value: Any, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number: {field}") from exc
    if not math.isfinite(out) or out <= 0:
        raise ValueError(f"non-positive/non-finite number: {field}")
    return out


def _time(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty timestamp")
    text = value.strip()
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if dt.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _symbols(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("symbols must be a non-empty list")
    out: list[str] = []
    for i, raw in enumerate(value):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"symbols[{i}] must be a non-empty string")
        symbol = raw.strip()
        if not _SYMBOL_RE.fullmatch(symbol) or not symbol.endswith("USDT"):
            raise ValueError(f"unsupported symbol format: {raw}")
        if symbol in out:
            raise ValueError(f"duplicate symbol: {symbol}")
        out.append(symbol)
    return out


def _levels(raw: Any, side: str) -> list[tuple[float, float]]:
    if not isinstance(raw, list) or len(raw) < MIN_LEVELS:
        raise ValueError(f"{side}: at least {MIN_LEVELS} levels required")
    out: list[tuple[float, float]] = []
    for i, row in enumerate(raw):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError(f"{side}[{i}]: malformed level")
        out.append((_num(row[0], f"{side}[{i}].price"), _num(row[1], f"{side}[{i}].qty")))
    prices = [p for p, _ in out]
    if len(set(prices)) != len(prices):
        raise ValueError(f"{side}: duplicate price levels")
    if side == "bids" and any(prices[i] <= prices[i + 1] for i in range(len(prices) - 1)):
        raise ValueError("bids must be strictly descending")
    if side == "asks" and any(prices[i] >= prices[i + 1] for i in range(len(prices) - 1)):
        raise ValueError("asks must be strictly ascending")
    return out


def _depth(levels: list[tuple[float, float]], mid: float, side: str, band: int) -> float:
    limit = band / 10_000.0
    total = 0.0
    for price, qty in levels:
        distance = (mid - price) / mid if side == "bids" else (price - mid) / mid
        if distance <= limit + 1e-12:
            total += price * qty
    return total


def _imbalance(bid: float, ask: float) -> float:
    total = bid + ask
    return 0.0 if total <= 0 else (bid - ask) / total


def _walls(levels: list[tuple[float, float]], mid: float, side: str) -> list[dict[str, float]]:
    notionals = [p * q for p, q in levels]
    med = statistics.median(notionals)
    if med <= 0:
        return []
    threshold = med * WALL_MULTIPLIER
    rows: list[dict[str, float]] = []
    for (price, qty), value in zip(levels, notionals):
        if value < threshold:
            continue
        distance = ((mid - price) / mid if side == "bid" else (price - mid) / mid) * 10_000.0
        rows.append(
            {
                "price": round(price, 8),
                "qty": round(qty, 8),
                "notional": round(value, 2),
                "multiple_of_median": round(value / med, 3),
                "distance_bps": round(distance, 3),
            }
        )
    return sorted(rows, key=lambda x: (x["distance_bps"], -x["notional"]))


def _state(value: float) -> str:
    return "BID_HEAVY" if value >= 0.20 else "ASK_HEAVY" if value <= -0.20 else "BALANCED"


def _capture_sha256(capture: dict[str, Any]) -> str:
    payload = json.dumps(
        capture,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def analyze_book(symbol: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    if not isinstance(snapshot, dict):
        raise ValueError(f"{symbol}: snapshot must be an object")
    if set(snapshot) != {"lastUpdateId", "bids", "asks"}:
        raise ValueError(f"{symbol}: unexpected depth payload fields")
    update_id = snapshot.get("lastUpdateId")
    if not isinstance(update_id, int) or isinstance(update_id, bool) or update_id < 0:
        raise ValueError(f"{symbol}: lastUpdateId must be a non-negative integer")
    bids = _levels(snapshot.get("bids"), "bids")
    asks = _levels(snapshot.get("asks"), "asks")
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid >= best_ask:
        raise ValueError(f"{symbol}: crossed/locked book")
    mid = (best_bid + best_ask) / 2.0
    spread = (best_ask - best_bid) / mid * 10_000.0
    bid_coverage = (mid - bids[-1][0]) / mid * 10_000.0
    ask_coverage = (asks[-1][0] - mid) / mid * 10_000.0

    bands: dict[str, Any] = {}
    values: list[float] = []
    for band in BANDS_BPS:
        bid_n = _depth(bids, mid, "bids", band)
        ask_n = _depth(asks, mid, "asks", band)
        im = _imbalance(bid_n, ask_n)
        complete = bid_coverage >= band and ask_coverage >= band
        if complete:
            values.append(im)
        bands[str(band)] = {
            "bid_notional": round(bid_n, 2),
            "ask_notional": round(ask_n, 2),
            "imbalance": round(im, 4),
            "coverage_complete": complete,
        }

    complete_bands = [band for band in BANDS_BPS if bands[str(band)]["coverage_complete"]]
    full_coverage = len(complete_bands) == len(BANDS_BPS)
    composite = statistics.mean(values) if full_coverage else None

    bid_walls = _walls(bids, mid, "bid")
    ask_walls = _walls(asks, mid, "ask")
    nearest_bid = bid_walls[0] if bid_walls else None
    nearest_ask = ask_walls[0] if ask_walls else None

    flags: list[str] = []
    if composite is not None and abs(composite) >= 0.45:
        flags.append("EXTREME_DEPTH_IMBALANCE")
    if not full_coverage:
        flags.append("INSUFFICIENT_DEPTH_COVERAGE")
    if nearest_bid and nearest_bid["distance_bps"] <= 10:
        flags.append("NEAR_BID_WALL")
    if nearest_ask and nearest_ask["distance_bps"] <= 10:
        flags.append("NEAR_ASK_WALL")
    if spread >= 5:
        flags.append("WIDE_SPREAD")

    attention = min(
        100.0,
        (abs(composite) * 70.0 if composite is not None else 0.0)
        + (15.0 if flags else 0.0)
        + min(spread / 5.0, 1.0) * 15.0,
    )

    return {
        "symbol": symbol,
        "last_update_id": update_id,
        "quality": "PASS" if full_coverage else "PARTIAL",
        "mid": round(mid, 8),
        "best_bid": round(best_bid, 8),
        "best_ask": round(best_ask, 8),
        "spread_bps": round(spread, 4),
        "depth_bands_bps": bands,
        "book_coverage_bps": {"bid": round(bid_coverage, 3), "ask": round(ask_coverage, 3)},
        "complete_bands_bps": complete_bands,
        "composite_imbalance": round(composite, 4) if composite is not None else None,
        "state": _state(composite) if composite is not None else "INSUFFICIENT_DEPTH_COVERAGE",
        "nearest_bid_wall": nearest_bid,
        "nearest_ask_wall": nearest_ask,
        "bid_wall_count": len(bid_walls),
        "ask_wall_count": len(ask_walls),
        "flags": sorted(set(flags)),
        "attention_score": round(attention, 2),
        "interpretation": "Visible order-book liquidity snapshot only; not a liquidation map, hidden-liquidity model, or execution signal.",
        "can_trade": False,
    }


def build_lens(capture: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(capture, dict):
        raise ValueError("capture must be an object")
    if set(capture) != CAPTURE_FIELDS:
        raise ValueError("liquidity capture fields do not match v1.1.0 contract")
    if capture.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("unsupported liquidity capture schema")
    if capture.get("version") != CAPTURE_VERSION:
        raise ValueError("unsupported liquidity capture version")
    if capture.get("credentials_used") is not False or capture.get("private_api_used") is not False:
        raise ValueError("liquidity capture must be public and credential-free")
    if capture.get("transport_policy") != EXPECTED_TRANSPORT_POLICY:
        raise ValueError("liquidity capture transport policy mismatch")
    capture_safety = capture.get("safety")
    if (
        not isinstance(capture_safety, dict)
        or capture_safety.get("public_market_data_only") is not True
        or capture_safety.get("credentials_used") is not False
        or capture_safety.get("private_api_used") is not False
        or capture_safety.get("signals_allowed") is not False
        or capture_safety.get("orders_allowed") is not False
        or capture_safety.get("can_trade") is not False
        or capture_safety.get("capital_permission") != "DENY"
    ):
        raise ValueError("liquidity capture safety contract mismatch")

    captured_at = _time(capture.get("captured_at"), "captured_at")
    if capture.get("source") != CAPTURE_SOURCE:
        raise ValueError("unsupported liquidity capture source")
    limit = capture.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit not in _ALLOWED_LIMITS:
        raise ValueError("unsupported liquidity capture limit")
    symbols = _symbols(capture.get("symbols"))
    books = capture.get("books")
    if not isinstance(books, dict):
        raise ValueError("capture books missing")
    if set(books) != set(symbols):
        raise ValueError("books keys must exactly match symbols")

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        book = books.get(symbol)
        if not isinstance(book, dict) or set(book) != {"source_url", "snapshot"}:
            raise ValueError(f"{symbol}: book wrapper must exactly contain source_url and snapshot")
        expected_url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit={limit}"
        if book.get("source_url") != expected_url:
            raise ValueError(f"{symbol}: source_url does not match canonical capture URL")
        rows.append(analyze_book(symbol, book.get("snapshot")))
    if not rows:
        raise ValueError("liquidity capture has no analyzable symbols")

    rows.sort(key=lambda x: (-x["attention_score"], x["symbol"]))
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "captured_at": captured_at,
        "matrix": rows,
        "top_attention": rows[0]["symbol"],
        "provenance": {
            "capture_schema": CAPTURE_SCHEMA,
            "capture_source": CAPTURE_SOURCE,
            "capture_limit": limit,
            "capture_sha256": _capture_sha256(capture),
            "symbol_count": len(symbols),
            "books_exactly_bound_to_symbols": True,
            "timestamp_timezone_required": True,
        },
        "contract": {
            "bands_bps": list(BANDS_BPS),
            "wall_multiplier": WALL_MULTIPLIER,
            "wall_baseline": "median visible level notional per side",
            "imbalance_formula": "(bid_notional-ask_notional)/(bid_notional+ask_notional)",
            "overall_directional_state_requires_all_bands_complete": True,
            "partial_coverage_directional_state": "INSUFFICIENT_DEPTH_COVERAGE",
        },
        "safety": {
            "visible_book_only": True,
            "liquidation_map": False,
            "hidden_liquidity_inferred": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
    }
