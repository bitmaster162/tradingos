#!/usr/bin/env python3
"""TradingOS R77 — deterministic Market Decision Bridge.

Purpose:
    Convert exact-bound Watchtower + Market Radar evidence into the existing
    Decision Brief v2 market-snapshot contract without fabricating freshness,
    execution authority, or AI-derived market facts.

Boundary:
    Offline transform only. No network, credentials, signals, orders, trading,
    capital effects, deployment, runtime mutation, or AI inference.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

SCHEMA = "tradingos.market_decision_bridge.v1"
VERSION = "1.0.0"
SNAPSHOT_SCHEMA_VERSION = 1
SUPPORTED_SYMBOL = "BTCUSDT"
SUPPORTED_TIMEFRAME = "4h"

WATCHTOWER_SCHEMA = "tradingos.watchtower.v1"
WATCHTOWER_VERSION = "1.1.0"
WATCHTOWER_PRODUCER = "tools/tradingos_watchtower.py"
EXPECTED_WATCHTOWER_PRODUCER_SHA256 = (
    "92fd705634e33d098907a72199314f01fb73318c733f302abeb1cb6d6e9be4a1"
)

RADAR_SCHEMA = "tradingos.market_radar.v1"
RADAR_VERSION = "1.1.0"
EXPECTED_LIQUIDITY_PRODUCER_SHA256 = (
    "870f2734de73af0974433a0dccd7750fc932117ace1ab2819ca952840780e699"
)
MAX_RADAR_CAPTURE_SKEW_SECONDS = 120

CAPTURE_SCHEMA = "tradingos.binance_watchtower_capture.v1"
EXPECTED_TFS = ("1h", "4h", "1d")
ALLOWED_BIASES = {"WATCH_LONG", "WATCH_SHORT", "NO_ACTION"}
ALLOWED_TF_STATES = {"LONG", "SHORT", "NEUTRAL"}
ALLOWED_FLOW = {"up", "down", "flat"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

WATCHTOWER_SAFETY = {
    "read_only": True,
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
}
RADAR_SAFETY = {
    "read_only": True,
    "network_fetch": False,
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
}
BRIDGE_SAFETY = {
    "read_only": True,
    "network_fetch": False,
    "ai_generated_market_facts": False,
    "signals_allowed": False,
    "orders_allowed": False,
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
    "confers_authority": False,
}


def stable_sha256(value: Any) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonically serializable") from exc
    return hashlib.sha256(raw).hexdigest()


def finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field}: bool is not a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}: invalid number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field}: non-finite number")
    return number


def positive(value: Any, field: str) -> float:
    number = finite(value, field)
    if number <= 0:
        raise ValueError(f"{field}: must be positive")
    return number


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field}: invalid sha256")
    return value


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: timestamp must be non-empty string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field}: timezone required")
    return parsed.astimezone(timezone.utc)


def iso_ms(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{field}: invalid millisecond timestamp")
    try:
        ms = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}: invalid millisecond timestamp") from exc
    if ms < 0:
        raise ValueError(f"{field}: negative timestamp")
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _index(rows: Any, field: str) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{field}: non-empty list required")
    order: list[str] = []
    indexed: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{field}[{i}]: object required")
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"{field}[{i}].symbol: invalid")
        if symbol in indexed:
            raise ValueError(f"{field}: duplicate symbol {symbol}")
        order.append(symbol)
        indexed[symbol] = row
    return order, indexed


def _int_ms(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field}: invalid integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}: invalid integer") from exc
    if result < 0:
        raise ValueError(f"{field}: negative timestamp")
    return result


def _latest_closed_kline(
    rows: Any,
    captured_ms: int,
    field: str,
) -> list[Any]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{field}: non-empty kline list required")
    eligible: list[list[Any]] = []
    prior_close = -1
    for i, row in enumerate(rows):
        if not isinstance(row, (list, tuple)) or len(row) != 12:
            raise ValueError(f"{field}[{i}]: expected exactly 12 fields")
        close_ms = _int_ms(row[6], f"{field}[{i}].close_time")
        if close_ms <= prior_close:
            raise ValueError(f"{field}: close times must be strictly increasing")
        prior_close = close_ms
        if close_ms <= captured_ms:
            eligible.append(list(row))
    if not eligible:
        raise ValueError(f"{field}: no closed observation at/before capture")
    return eligible[-1]


def _capture_evidence(
    capture: Any,
    symbol: str,
    timeframe: str,
) -> tuple[datetime, dict[str, Any], dict[str, str]]:
    if not isinstance(capture, dict) or capture.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("unsupported watchtower capture schema")
    if capture.get("credentials_used") is not False:
        raise ValueError("capture credentials_used must be false")
    if capture.get("private_api_used") is not False:
        raise ValueError("capture private_api_used must be false")

    captured = parse_time(capture.get("captured_at"), "capture.captured_at")
    captured_ms = int(captured.timestamp() * 1000)

    symbols = capture.get("symbols")
    assets = capture.get("assets")
    if (
        not isinstance(symbols, list)
        or len(symbols) != len(set(symbols))
        or not isinstance(assets, dict)
        or set(symbols) != set(assets)
    ):
        raise ValueError("capture symbols/assets identity mismatch")
    if symbol not in assets:
        raise ValueError(f"capture missing selected symbol {symbol}")

    asset = assets[symbol]
    if not isinstance(asset, dict):
        raise ValueError(f"{symbol}: capture asset must be object")
    futures = asset.get("futures_klines")
    if not isinstance(futures, dict) or timeframe not in futures:
        raise ValueError(f"{symbol}: futures_klines.{timeframe} missing")

    futures_row = _latest_closed_kline(
        futures[timeframe],
        captured_ms,
        f"{symbol}.futures_klines.{timeframe}",
    )
    spot_row = _latest_closed_kline(
        asset.get("spot_klines_4h"),
        captured_ms,
        f"{symbol}.spot_klines_4h",
    )

    oi = asset.get("open_interest")
    mark = asset.get("mark_price")
    if not isinstance(oi, dict) or oi.get("symbol") != symbol:
        raise ValueError(f"{symbol}: open_interest identity mismatch")
    if not isinstance(mark, dict) or mark.get("symbol") != symbol:
        raise ValueError(f"{symbol}: mark_price identity mismatch")

    observed_ms = {
        "ohlcv": _int_ms(futures_row[6], f"{symbol}.ohlcv.close_time"),
        "open_interest": _int_ms(oi.get("time"), f"{symbol}.open_interest.time"),
        "funding": _int_ms(mark.get("time"), f"{symbol}.mark_price.time"),
        "spot_flow": _int_ms(spot_row[6], f"{symbol}.spot_flow.close_time"),
    }
    if any(value > captured_ms for value in observed_ms.values()):
        raise ValueError("source timestamp after capture")

    observed = {
        kind: iso_ms(value, f"{kind}.observed_at")
        for kind, value in observed_ms.items()
    }
    return captured, asset, observed


def _validate_watchtower(
    capture: dict[str, Any],
    watchtower: Any,
    symbol: str,
) -> dict[str, Any]:
    if not isinstance(watchtower, dict):
        raise ValueError("watchtower must be object")
    if (
        watchtower.get("schema") != WATCHTOWER_SCHEMA
        or watchtower.get("version") != WATCHTOWER_VERSION
    ):
        raise ValueError("unsupported watchtower contract")
    if watchtower.get("safety") != WATCHTOWER_SAFETY:
        raise ValueError("unsafe watchtower permissions")

    capture_time = parse_time(capture.get("captured_at"), "capture.captured_at")
    report_time = parse_time(watchtower.get("captured_at"), "watchtower.captured_at")
    if report_time != capture_time:
        raise ValueError("watchtower/capture timestamp mismatch")

    order, rows = _index(watchtower.get("matrix"), "watchtower.matrix")
    if watchtower.get("symbols") != order:
        raise ValueError("watchtower symbols/matrix identity mismatch")
    if symbol not in rows:
        raise ValueError(f"watchtower missing selected symbol {symbol}")

    provenance = watchtower.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("watchtower provenance missing")
    if provenance.get("producer") != WATCHTOWER_PRODUCER:
        raise ValueError("watchtower producer mismatch")
    producer_sha = require_sha256(
        provenance.get("producer_sha256"), "watchtower.producer_sha256"
    )
    if producer_sha != EXPECTED_WATCHTOWER_PRODUCER_SHA256:
        raise ValueError("watchtower producer sha256 mismatch")
    capture_sha = require_sha256(
        provenance.get("capture_sha256"), "watchtower.capture_sha256"
    )
    if capture_sha != stable_sha256(capture):
        raise ValueError("watchtower capture sha256 mismatch")

    row = rows[symbol]
    if row.get("bias") not in ALLOWED_BIASES or row.get("can_trade") is not False:
        raise ValueError(f"{symbol}: invalid/unsafe watchtower row")
    tfs = row.get("timeframes")
    if not isinstance(tfs, dict) or set(tfs) != set(EXPECTED_TFS):
        raise ValueError(f"{symbol}: invalid watchtower timeframe set")
    for tf in EXPECTED_TFS:
        tf_row = tfs[tf]
        if not isinstance(tf_row, dict) or tf_row.get("state") not in ALLOWED_TF_STATES:
            raise ValueError(f"{symbol}.{tf}: invalid state")
    return row


def _validate_radar(
    watchtower: dict[str, Any],
    radar: Any,
    symbol: str,
    watch_row: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(radar, dict):
        raise ValueError("radar must be object")
    if radar.get("schema") != RADAR_SCHEMA or radar.get("version") != RADAR_VERSION:
        raise ValueError("unsupported radar contract")
    if radar.get("safety") != RADAR_SAFETY:
        raise ValueError("unsafe radar permissions")

    order, rows = _index(radar.get("matrix"), "radar.matrix")
    if radar.get("symbols") != order or radar.get("top_priority") != order[0]:
        raise ValueError("radar symbol/order/top_priority mismatch")
    if symbol not in rows:
        raise ValueError(f"radar missing selected symbol {symbol}")

    provenance = radar.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("radar provenance missing")
    if provenance.get("watchtower_schema") != WATCHTOWER_SCHEMA:
        raise ValueError("radar watchtower schema mismatch")
    if provenance.get("watchtower_version") != WATCHTOWER_VERSION:
        raise ValueError("radar watchtower version mismatch")
    if require_sha256(
        provenance.get("watchtower_report_sha256"),
        "radar.watchtower_report_sha256",
    ) != stable_sha256(watchtower):
        raise ValueError("radar watchtower report binding mismatch")
    watch_prov = watchtower["provenance"]
    if provenance.get("watchtower_capture_sha256") != watch_prov.get("capture_sha256"):
        raise ValueError("radar watchtower capture binding mismatch")
    if provenance.get("watchtower_producer_sha256") != EXPECTED_WATCHTOWER_PRODUCER_SHA256:
        raise ValueError("radar watchtower producer binding mismatch")
    if require_sha256(
        provenance.get("liquidity_producer_sha256"),
        "radar.liquidity_producer_sha256",
    ) != EXPECTED_LIQUIDITY_PRODUCER_SHA256:
        raise ValueError("radar liquidity producer binding mismatch")
    require_sha256(
        provenance.get("liquidity_report_sha256"),
        "radar.liquidity_report_sha256",
    )
    require_sha256(
        provenance.get("liquidity_capture_sha256"),
        "radar.liquidity_capture_sha256",
    )
    if provenance.get("symbol_sets_exactly_bound") is not True:
        raise ValueError("radar symbol binding missing")
    if provenance.get("max_capture_skew_seconds") != MAX_RADAR_CAPTURE_SKEW_SECONDS:
        raise ValueError("radar capture-skew contract mismatch")

    contract = radar.get("contract")
    if (
        not isinstance(contract, dict)
        or contract.get("watchtower_bias_is_authoritative") is not True
        or contract.get("liquidity_can_create_directional_bias") is not False
    ):
        raise ValueError("radar directional-authority contract mismatch")

    row = rows[symbol]
    if row.get("bias") != watch_row.get("bias"):
        raise ValueError(f"{symbol}: radar/watchtower bias mismatch")
    if row.get("can_trade") is not False:
        raise ValueError(f"{symbol}: unsafe radar row")
    expected_states = {
        tf: watch_row["timeframes"][tf]["state"] for tf in EXPECTED_TFS
    }
    if row.get("timeframes") != expected_states:
        raise ValueError(f"{symbol}: radar/watchtower timeframe mismatch")
    return row


def _source_rows(observed: dict[str, str]) -> list[dict[str, str]]:
    source_ids = {
        "ohlcv": "binance-public-futures-klines-4h",
        "open_interest": "binance-public-futures-open-interest",
        "funding": "binance-public-futures-mark-price",
        "spot_flow": "binance-public-spot-klines-4h",
    }
    return [
        {
            "kind": kind,
            "source_id": source_ids[kind],
            "observed_at": observed[kind],
        }
        for kind in ("ohlcv", "open_interest", "funding", "spot_flow")
    ]


def build_bridge(
    capture: dict[str, Any],
    watchtower: dict[str, Any],
    radar: dict[str, Any],
    *,
    symbol: str = SUPPORTED_SYMBOL,
    timeframe: str = SUPPORTED_TIMEFRAME,
) -> dict[str, Any]:
    """Build one deterministic Decision Brief-compatible snapshot + evidence envelope."""
    if symbol != SUPPORTED_SYMBOL:
        raise ValueError(f"R77 supports only {SUPPORTED_SYMBOL}")
    if timeframe != SUPPORTED_TIMEFRAME:
        raise ValueError(f"R77 supports only {SUPPORTED_TIMEFRAME}")

    captured, _asset, observed = _capture_evidence(capture, symbol, timeframe)
    watch = _validate_watchtower(capture, watchtower, symbol)
    radar_row = _validate_radar(watchtower, radar, symbol, watch)

    four = watch["timeframes"][timeframe]
    derivatives = watch.get("derivatives")
    if not isinstance(derivatives, dict):
        raise ValueError(f"{symbol}: watchtower derivatives missing")

    last = positive(four.get("last"), f"{symbol}.4h.last")
    ema9 = positive(four.get("ema9"), f"{symbol}.4h.ema9")
    ema21 = positive(four.get("ema21"), f"{symbol}.4h.ema21")
    support = positive(four.get("support"), f"{symbol}.4h.support")
    resistance = positive(four.get("resistance"), f"{symbol}.4h.resistance")
    range_position = finite(four.get("range_position"), f"{symbol}.4h.range_position")
    if range_position < 0.0 or range_position > 1.0:
        raise ValueError(f"{symbol}.4h.range_position: out of range")

    spot_flow = watch.get("spot_flow_4h")
    perp_flow = four.get("perp_taker_flow")
    if spot_flow not in ALLOWED_FLOW or perp_flow not in ALLOWED_FLOW:
        raise ValueError(f"{symbol}: invalid flow direction")

    captured_at = captured.isoformat(timespec="seconds").replace("+00:00", "Z")
    capture_sha = stable_sha256(capture)
    snapshot_id = (
        f"{symbol}-{captured.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"-R77-{capture_sha[:12]}"
    )

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "as_of": captured_at,
        "symbol": symbol,
        "timeframe": timeframe,
        "can_trade": False,
        "provenance": {
            "producer": "tools/tradingos_market_decision_bridge.py",
            "sources": _source_rows(observed),
        },
        "price": {
            "last": last,
            "change_pct": finite(four.get("change_pct"), f"{symbol}.4h.change_pct"),
            "ema_fast": ema9,
            "ema_slow": ema21,
            "atr_pct": finite(four.get("atr_pct"), f"{symbol}.4h.atr_pct"),
        },
        "market_structure": {
            "trend": four.get("trend"),
            "support": support,
            "resistance": resistance,
            "range_position": range_position,
        },
        "derivatives": {
            "open_interest_change_pct": finite(
                derivatives.get("open_interest_change_pct"),
                f"{symbol}.open_interest_change_pct",
            ),
            "funding_rate": finite(
                derivatives.get("funding_rate"), f"{symbol}.funding_rate"
            ),
            "funding_z": finite(
                derivatives.get("funding_z"), f"{symbol}.funding_z"
            ),
            "basis_pct": finite(
                derivatives.get("basis_pct"), f"{symbol}.basis_pct"
            ),
            "basis_z": finite(
                derivatives.get("basis_z"), f"{symbol}.basis_z"
            ),
        },
        "flow": {
            "spot_cvd_direction": spot_flow,
            "perp_cvd_direction": perp_flow,
            "relative_volume": finite(
                four.get("relative_volume"), f"{symbol}.4h.relative_volume"
            ),
        },
        "data_quality": {
            "present_sources": [
                "ohlcv",
                "open_interest",
                "funding",
                "spot_flow",
            ],
            "conflicts": [],
        },
        "operator": {
            "prior_decision": "not_supplied",
            "changed_decision": "not_computed_by_bridge",
            "prevented_decision": "execution_not_permitted",
        },
    }

    radar_prov = radar["provenance"]
    attention = {
        "bias": radar_row["bias"],
        "decision_quality": radar_row.get("decision_quality"),
        "priority_score": finite(
            radar_row.get("priority_score"), f"{symbol}.radar.priority_score"
        ),
        "vetoes": sorted(set(radar_row.get("vetoes", []))),
        "notes": sorted(set(radar_row.get("notes", []))),
        "liquidity": radar_row.get("liquidity"),
        "confers_authority": False,
    }

    result = {
        "schema": SCHEMA,
        "version": VERSION,
        "symbol": symbol,
        "timeframe": timeframe,
        "snapshot": snapshot,
        "snapshot_sha256": stable_sha256(snapshot),
        "input_binding": {
            "watchtower_capture_sha256": capture_sha,
            "watchtower_report_sha256": stable_sha256(watchtower),
            "watchtower_producer_sha256": EXPECTED_WATCHTOWER_PRODUCER_SHA256,
            "radar_report_sha256": stable_sha256(radar),
            "liquidity_report_sha256": radar_prov["liquidity_report_sha256"],
            "liquidity_capture_sha256": radar_prov["liquidity_capture_sha256"],
            "liquidity_producer_sha256": EXPECTED_LIQUIDITY_PRODUCER_SHA256,
        },
        "attention_context": attention,
        "safety": dict(BRIDGE_SAFETY),
    }
    validate_bridge(result)
    return result


def validate_bridge(result: Any) -> None:
    """Fail closed on any permission drift or envelope/snapshot digest mismatch."""
    if not isinstance(result, dict):
        raise ValueError("bridge result must be object")
    if result.get("schema") != SCHEMA or result.get("version") != VERSION:
        raise ValueError("unsupported bridge contract")
    if result.get("safety") != BRIDGE_SAFETY:
        raise ValueError("unsafe bridge permissions")
    snapshot = result.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("can_trade") is not False:
        raise ValueError("unsafe/missing bridge snapshot")
    digest = require_sha256(result.get("snapshot_sha256"), "snapshot_sha256")
    if digest != stable_sha256(snapshot):
        raise ValueError("snapshot digest mismatch")
    context = result.get("attention_context")
    if not isinstance(context, dict) or context.get("confers_authority") is not False:
        raise ValueError("attention context must be advisory only")
