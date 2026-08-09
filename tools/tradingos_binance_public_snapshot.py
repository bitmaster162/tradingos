#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SYMBOL = "BTCUSDT"
TIMEFRAME = "4h"
PRODUCER_VERSION = "1.0.0"


def parse_time(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number: {field}") from exc
    if not math.isfinite(number):
        raise ValueError(f"non-finite number: {field}")
    return number


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need at least {period} closes")
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def zscore(value: float, history: list[float]) -> float:
    if len(history) < 10:
        raise ValueError("z-score history too short")
    sigma = statistics.pstdev(history)
    return 0.0 if sigma == 0 else (value - statistics.mean(history)) / sigma


def direction_from_taker(volume: float, taker_buy: float) -> str:
    net = 2.0 * taker_buy - volume
    if net > 0:
        return "up"
    if net < 0:
        return "down"
    return "flat"


def closed_rows(rows: list[list[Any]], captured_ms: int) -> list[list[Any]]:
    result = [row for row in rows if int(row[6]) <= captured_ms]
    if len(result) < 21:
        raise ValueError("not enough closed 4h bars")
    return result


def build_snapshot(capture: dict[str, Any]) -> dict[str, Any]:
    if capture.get("schema") != "tradingos.binance_public_capture.v1":
        raise ValueError("unsupported capture schema")
    if capture.get("symbol") != SYMBOL:
        raise ValueError("unsupported symbol")
    if capture.get("credentials_used") is not False or capture.get("private_api_used") is not False:
        raise ValueError("capture must be public and credential-free")

    captured_at = parse_time(str(capture["captured_at"]))
    captured_ms = int(captured_at.timestamp() * 1000)
    futures_rows = closed_rows(capture["futures_klines_4h"], captured_ms)
    spot_rows = closed_rows(capture["spot_klines_4h"], captured_ms)

    closes = [finite(row[4], "futures.close") for row in futures_rows]
    latest = futures_rows[-1]
    last = closes[-1]
    ema_fast = ema(closes, 9)
    ema_slow = ema(closes, 21)

    true_ranges: list[float] = []
    prev_close = closes[0]
    for row in futures_rows[1:]:
        high = finite(row[2], "futures.high")
        low = finite(row[3], "futures.low")
        close = finite(row[4], "futures.close")
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    atr14 = statistics.mean(true_ranges[-14:])

    structure_rows = futures_rows[-20:]
    support = min(finite(row[3], "futures.low") for row in structure_rows)
    resistance = max(finite(row[2], "futures.high") for row in structure_rows)
    if not support < last < resistance:
        raise ValueError("market structure does not bracket last price")
    range_position = (last - support) / (resistance - support)
    if last > ema_fast > ema_slow:
        trend = "up"
    elif last < ema_fast < ema_slow:
        trend = "down"
    else:
        trend = "range"

    ticker = capture["futures_24h"]
    oi = capture["open_interest"]
    oi_stats = capture["open_interest_stats_4h"]
    if not oi_stats:
        raise ValueError("open interest history missing")
    oi_current = finite(oi["openInterest"], "open_interest.openInterest")
    oi_reference = finite(oi_stats[-1]["sumOpenInterest"], "open_interest_stats[-1].sumOpenInterest")
    oi_change_pct = (oi_current / oi_reference - 1.0) * 100.0

    mark = capture["mark_price"]
    mark_price = finite(mark["markPrice"], "mark_price.markPrice")
    index_price = finite(mark["indexPrice"], "mark_price.indexPrice")
    funding_rate = finite(mark["lastFundingRate"], "mark_price.lastFundingRate")
    funding_hist = [finite(item["fundingRate"], "funding_history.fundingRate") for item in capture["funding_history"]]
    funding_z = zscore(funding_rate, funding_hist)
    basis_fraction = mark_price / index_price - 1.0
    premium_hist = [finite(row[4], "premium.close") for row in closed_rows(capture["premium_index_4h"], captured_ms)]
    basis_z = zscore(basis_fraction, premium_hist)

    latest_spot = spot_rows[-1]
    spot_volume = finite(latest_spot[5], "spot.volume")
    spot_taker_buy = finite(latest_spot[9], "spot.taker_buy")
    perp_volume = finite(latest[5], "futures.volume")
    perp_taker_buy = finite(latest[9], "futures.taker_buy")
    prior_spot_volumes = [finite(row[5], "spot.volume") for row in spot_rows[-21:-1]]
    relative_volume = spot_volume / statistics.mean(prior_spot_volumes)

    ohlcv_observed = iso_ms(int(latest[6]))
    spot_observed = iso_ms(int(latest_spot[6]))
    oi_observed = iso_ms(int(oi["time"]))
    funding_observed = iso_ms(int(mark["time"]))

    return {
        "schema_version": 1,
        "snapshot_id": f"BTCUSDT-{captured_at.isoformat(timespec='seconds').replace('+00:00','Z')}-binance-public",
        "as_of": captured_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "can_trade": False,
        "provenance": {
            "producer": "tools/tradingos_binance_public_snapshot.py",
            "producer_version": PRODUCER_VERSION,
            "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "calculation_contract": "4h_closed_only; EMA9/EMA21; ATR14; 20-bar support/resistance; OI=current/latest_4h_stat; funding/premium population-z30; taker-flow sign; spot-volume/current_vs_prior20_mean",
            "capture_schema": capture["schema"],
            "sources": [
                {"kind": "ohlcv", "source_id": "binance:fapi:v1:klines:BTCUSDT:4h", "observed_at": ohlcv_observed},
                {"kind": "open_interest", "source_id": "binance:fapi:v1:openInterest:BTCUSDT", "observed_at": oi_observed},
                {"kind": "funding", "source_id": "binance:fapi:v1:premiumIndex:BTCUSDT", "observed_at": funding_observed},
                {"kind": "spot_flow", "source_id": "binance:api:v3:klines:BTCUSDT:4h:taker_buy", "observed_at": spot_observed},
            ],
        },
        "price": {
            "last": round(last, 2),
            "change_pct": round(finite(ticker["priceChangePercent"], "futures_24h.priceChangePercent"), 4),
            "ema_fast": round(ema_fast, 2),
            "ema_slow": round(ema_slow, 2),
            "atr_pct": round(atr14 / last * 100.0, 4),
        },
        "market_structure": {
            "trend": trend,
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "range_position": round(range_position, 4),
        },
        "derivatives": {
            "open_interest_change_pct": round(oi_change_pct, 4),
            "funding_rate": round(funding_rate, 8),
            "funding_z": round(funding_z, 4),
            "basis_pct": round(basis_fraction * 100.0, 5),
            "basis_z": round(basis_z, 4),
            "liquidation_bias": "not_observed",
        },
        "flow": {
            "spot_cvd_direction": direction_from_taker(spot_volume, spot_taker_buy),
            "perp_cvd_direction": direction_from_taker(perp_volume, perp_taker_buy),
            "relative_volume": round(relative_volume, 4),
        },
        "data_quality": {
            "present_sources": ["ohlcv", "open_interest", "funding", "spot_flow"],
            "conflicts": [],
        },
        "operator": {
            "prior_decision": "NO_ACTION",
            "changed_decision": "UNASSESSED_BEFORE_BRIEF",
            "prevented_decision": "UNASSESSED_BEFORE_BRIEF",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a credential-free Binance public capture into a deterministic Decision Brief snapshot")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        capture = json.loads(args.input.read_text(encoding="utf-8-sig"))
        snapshot = build_snapshot(capture)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", "snapshot_id": snapshot["snapshot_id"], "output": str(args.output), "can_trade": False, "capital_permission": "DENY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
