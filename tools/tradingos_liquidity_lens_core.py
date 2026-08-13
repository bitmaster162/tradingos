from __future__ import annotations

import math
import statistics
from typing import Any

CAPTURE_SCHEMA = "tradingos.binance_liquidity_capture.v1"
SCHEMA = "tradingos.liquidity_lens.v1"
VERSION = "1.0.0"
BANDS_BPS = (10, 25, 50)
WALL_MULTIPLIER = 3.0
MIN_LEVELS = 5


def _num(value: Any, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number: {field}") from exc
    if not math.isfinite(out) or out <= 0:
        raise ValueError(f"non-positive/non-finite number: {field}")
    return out


def _levels(raw: Any, side: str) -> list[tuple[float, float]]:
    if not isinstance(raw, list) or len(raw) < MIN_LEVELS:
        raise ValueError(f"{side}: at least {MIN_LEVELS} levels required")
    out: list[tuple[float, float]] = []
    for i, row in enumerate(raw):
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise ValueError(f"{side}[{i}]: malformed level")
        out.append((_num(row[0], f"{side}[{i}].price"), _num(row[1], f"{side}[{i}].qty")))
    prices = [p for p, _ in out]
    if side == "bids" and any(prices[i] < prices[i + 1] for i in range(len(prices) - 1)):
        raise ValueError("bids must be sorted descending")
    if side == "asks" and any(prices[i] > prices[i + 1] for i in range(len(prices) - 1)):
        raise ValueError("asks must be sorted ascending")
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
        rows.append({
            "price": round(price, 8), "qty": round(qty, 8), "notional": round(value, 2),
            "multiple_of_median": round(value / med, 3), "distance_bps": round(distance, 3),
        })
    return sorted(rows, key=lambda x: (x["distance_bps"], -x["notional"]))


def _state(value: float) -> str:
    return "BID_HEAVY" if value >= 0.20 else "ASK_HEAVY" if value <= -0.20 else "BALANCED"


def analyze_book(symbol: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError(f"{symbol}: snapshot must be an object")
    bids, asks = _levels(snapshot.get("bids"), "bids"), _levels(snapshot.get("asks"), "asks")
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid >= best_ask:
        raise ValueError(f"{symbol}: crossed/locked book")
    mid = (best_bid + best_ask) / 2.0
    spread = (best_ask - best_bid) / mid * 10_000.0
    bid_coverage = (mid - bids[-1][0]) / mid * 10_000.0
    ask_coverage = (asks[-1][0] - mid) / mid * 10_000.0

    bands: dict[str, Any] = {}
    complete_values: list[float] = []
    for band in BANDS_BPS:
        bid_n, ask_n = _depth(bids, mid, "bids", band), _depth(asks, mid, "asks", band)
        im = _imbalance(bid_n, ask_n)
        complete = bid_coverage >= band and ask_coverage >= band
        if complete:
            complete_values.append(im)
        bands[str(band)] = {
            "bid_notional": round(bid_n, 2), "ask_notional": round(ask_n, 2),
            "imbalance": round(im, 4), "coverage_complete": complete,
        }
    composite = statistics.mean(complete_values) if complete_values else None
    bid_walls, ask_walls = _walls(bids, mid, "bid"), _walls(asks, mid, "ask")
    nearest_bid = bid_walls[0] if bid_walls else None
    nearest_ask = ask_walls[0] if ask_walls else None
    flags: list[str] = []
    if composite is not None and abs(composite) >= 0.45:
        flags.append("EXTREME_DEPTH_IMBALANCE")
    if composite is None:
        flags.append("INSUFFICIENT_DEPTH_COVERAGE")
    if nearest_bid and nearest_bid["distance_bps"] <= 10:
        flags.append("NEAR_BID_WALL")
    if nearest_ask and nearest_ask["distance_bps"] <= 10:
        flags.append("NEAR_ASK_WALL")
    if spread >= 5:
        flags.append("WIDE_SPREAD")
    attention = min(100.0, (abs(composite) * 70.0 if composite is not None else 0.0) + (15.0 if flags else 0.0) + min(spread / 5.0, 1.0) * 15.0)
    complete_bands = [b for b in BANDS_BPS if bands[str(b)]["coverage_complete"]]
    return {
        "symbol": symbol,
        "quality": "PASS" if complete_bands else "PARTIAL",
        "mid": round(mid, 8), "best_bid": round(best_bid, 8), "best_ask": round(best_ask, 8),
        "spread_bps": round(spread, 4), "depth_bands_bps": bands,
        "book_coverage_bps": {"bid": round(bid_coverage, 3), "ask": round(ask_coverage, 3)},
        "complete_bands_bps": complete_bands,
        "composite_imbalance": round(composite, 4) if composite is not None else None,
        "state": _state(composite) if composite is not None else "INSUFFICIENT_DEPTH_COVERAGE",
        "nearest_bid_wall": nearest_bid, "nearest_ask_wall": nearest_ask,
        "bid_wall_count": len(bid_walls), "ask_wall_count": len(ask_walls),
        "flags": sorted(set(flags)), "attention_score": round(attention, 2),
        "interpretation": "Visible order-book liquidity snapshot only; not a liquidation map, hidden-liquidity model, or execution signal.",
        "can_trade": False,
    }


def build_lens(capture: dict[str, Any]) -> dict[str, Any]:
    if capture.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("unsupported liquidity capture schema")
    if capture.get("credentials_used") is not False or capture.get("private_api_used") is not False:
        raise ValueError("liquidity capture must be public and credential-free")
    books = capture.get("books")
    if not isinstance(books, dict):
        raise ValueError("capture books missing")
    rows = [analyze_book(symbol, books[symbol]["snapshot"]) for symbol in capture.get("symbols", [])]
    rows.sort(key=lambda x: (-x["attention_score"], x["symbol"]))
    return {
        "schema": SCHEMA, "version": VERSION, "captured_at": capture.get("captured_at"),
        "matrix": rows, "top_attention": rows[0]["symbol"] if rows else None,
        "contract": {
            "bands_bps": list(BANDS_BPS), "wall_multiplier": WALL_MULTIPLIER,
            "wall_baseline": "median visible level notional per side",
            "imbalance_formula": "(bid_notional-ask_notional)/(bid_notional+ask_notional)",
            "band_state_requires_full_coverage": True,
        },
        "safety": {
            "visible_book_only": True, "liquidation_map": False, "hidden_liquidity_inferred": False,
            "signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY",
        },
    }
