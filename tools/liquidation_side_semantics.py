#!/usr/bin/env python3
from __future__ import annotations

from typing import Final


CANONICAL_SIDE_SCHEMA_VERSION: Final[int] = 1
LIQUIDATED_POSITION_SIDE_MAP: Final[dict[str, dict[str, str]]] = {
    "binance_force_order": {"BUY": "SHORT", "SELL": "LONG"},
    "bybit_all_liquidation": {"BUY": "LONG", "SELL": "SHORT"},
}
POSITION_CONTEXT: Final[dict[str, str]] = {
    "LONG": "long_liquidation_flush",
    "SHORT": "short_liquidation_squeeze",
}


def liquidated_position_side(source: str, raw_side: str) -> str:
    source_key = str(source).strip().lower()
    side_key = str(raw_side).strip().upper()
    mapping = LIQUIDATED_POSITION_SIDE_MAP.get(source_key)
    if mapping is None:
        raise ValueError(f"unsupported liquidation source: {source}")
    position_side = mapping.get(side_key)
    if position_side is None:
        raise ValueError(f"unsupported {source_key} raw side: {raw_side}")
    return position_side


def dominant_liquidation_context(
    long_liquidated_notional_usd: float,
    short_liquidated_notional_usd: float,
    *,
    dominance_threshold: float = 0.65,
) -> str:
    if not 0.5 < dominance_threshold <= 1.0:
        raise ValueError("dominance_threshold must be in (0.5, 1.0]")
    long_notional = max(0.0, float(long_liquidated_notional_usd))
    short_notional = max(0.0, float(short_liquidated_notional_usd))
    total = long_notional + short_notional
    if total <= 0:
        return "mixed"
    if long_notional / total >= dominance_threshold:
        return POSITION_CONTEXT["LONG"]
    if short_notional / total >= dominance_threshold:
        return POSITION_CONTEXT["SHORT"]
    return "mixed"
