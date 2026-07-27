#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TOOL_PATH = "tools/bitunix_wo105_public_rest_collector.py"
BASE_URL = "https://fapi.bitunix.com"
ENDPOINTS = {
    "kline": "/api/v1/futures/market/kline",
    "funding": "/api/v1/futures/market/funding_rate",
    "depth": "/api/v1/futures/market/depth",
}
DOC_URLS = {
    "kline": "https://www.bitunix.com/api-docs/futures/market/get_kline.html",
    "funding": "https://www.bitunix.com/api-docs/futures/market/get_funding_rate.html",
    "depth": "https://www.bitunix.com/api-docs/futures/market/get_depth.html",
    "funding_unit": (
        "https://www.bitunix.com/hub/academy/course/"
        "futures-essential-course-understand-the-nouns-on-bitunix-futures-trading-page-in-one-article-web?id=83"
    ),
}
INTERVAL_MS = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000}
DEFAULT_REQUIRED = {"5m": 300, "1h": 100, "4h": 220}
FUNDING_API_UNIT = "percentage_points"
FUNDING_NORMALIZED_UNIT = "decimal_fraction"
FUNDING_NORMALIZATION_DIVISOR = 100.0


Requester = Callable[[str, dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso(value: int | None = None) -> str:
    milliseconds = value if value is not None else now_ms()
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def public_get(endpoint: str, params: dict[str, Any], *, timeout_s: float = 20.0) -> tuple[dict[str, Any], dict[str, Any]]:
    if endpoint not in ENDPOINTS:
        raise ValueError(f"endpoint_not_allowlisted:{endpoint}")
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{BASE_URL}{ENDPOINTS[endpoint]}?{query}"
    started = now_ms()
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "TradingOS-Bitunix-WO105-public-read-only/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read()
            status = int(response.status)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"public_get_failed:{endpoint}:{type(exc).__name__}:{exc}") from exc
    received = now_ms()
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"public_get_json_invalid:{endpoint}") from exc
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise RuntimeError(f"public_get_api_error:{endpoint}:{payload!r}")
    receipt = {
        "endpoint": endpoint,
        "method": "GET",
        "url": url,
        "http_status": status,
        "started_at": started,
        "received_at": received,
        "latency_ms": received - started,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "credentials_used": 0,
        "private_calls": 0,
        "order_calls": 0,
    }
    return payload, receipt


def data_items(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    data = envelope.get("data")
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return list(data)
    raise ValueError("response_data_shape_invalid")


def source_record(
    *, source_id: str, observed_at: int, received_at: int, schema_version: str, payload: dict[str, Any]
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "observed_at": observed_at,
        "received_at": received_at,
        "source_hash": canonical_sha256(payload),
        "schema_version": schema_version,
        "payload": payload,
    }


def parse_kline_item(
    item: dict[str, Any], *, symbol: str, interval: str, received_at: int
) -> tuple[int, dict[str, Any]] | None:
    open_ms = integer(item.get("time"))
    if open_ms is None or open_ms <= 0:
        return None
    values = {name: finite_number(item.get(name)) for name in ("open", "high", "low", "close")}
    if any(value is None or value <= 0 for value in values.values()):
        return None
    high = float(values["high"])
    low = float(values["low"])
    open_price = float(values["open"])
    close = float(values["close"])
    if high < max(open_price, close, low) or low > min(open_price, close, high):
        return None
    close_ms = open_ms + INTERVAL_MS[interval]
    if close_ms > received_at:
        return None
    payload = {
        "open_ms": open_ms,
        "close_ms": close_ms,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "symbol": symbol,
        "interval": interval,
        "price_type": "LAST_PRICE",
    }
    record = source_record(
        source_id=f"bitunix:futures:kline:{symbol}:{interval}:{open_ms}",
        observed_at=close_ms,
        received_at=received_at,
        schema_version="ohlcv-bar-v1",
        payload=payload,
    )
    return open_ms, record


def fetch_closed_klines(
    requester: Requester,
    *,
    symbol: str,
    interval: str,
    required: int,
    max_pages: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported_interval:{interval}")
    records: dict[int, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    failures: list[str] = []
    end_time: int | None = None
    for page in range(max_pages):
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(200, max(required - len(records), 1)),
            "type": "LAST_PRICE",
            "endTime": end_time,
        }
        try:
            envelope, receipt = requester("kline", params)
            items = data_items(envelope)
        except (RuntimeError, ValueError) as exc:
            failures.append(f"{interval}:page_{page}:{exc}")
            break
        receipts.append(receipt)
        page_open_times: list[int] = []
        for item in items:
            parsed = parse_kline_item(item, symbol=symbol, interval=interval, received_at=int(receipt["received_at"]))
            raw_open_ms = integer(item.get("time"))
            if raw_open_ms is not None:
                page_open_times.append(raw_open_ms)
            if parsed is not None:
                open_ms, record = parsed
                existing = records.get(open_ms)
                if existing is not None and existing["source_hash"] != record["source_hash"]:
                    failures.append(f"{interval}:closed_bar_hash_conflict:{open_ms}")
                elif existing is None or int(record["received_at"]) < int(existing["received_at"]):
                    records[open_ms] = record
        if len(records) >= required:
            break
        if not page_open_times:
            failures.append(f"{interval}:empty_or_invalid_page:{page}")
            break
        next_end = min(page_open_times) - 1
        if end_time is not None and next_end >= end_time:
            failures.append(f"{interval}:pagination_not_advancing:{page}")
            break
        end_time = next_end
        if len(items) < int(params["limit"]):
            break
    ordered = [records[key] for key in sorted(records)][-required:]
    if len(ordered) < required:
        failures.append(f"{interval}:insufficient_closed_bars:{len(ordered)}<{required}")
    return ordered, receipts, sorted(set(failures))


def funding_records(
    envelope: dict[str, Any], receipt: dict[str, Any], *, symbol: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    items = data_items(envelope)
    if len(items) != 1:
        raise ValueError("funding_expected_one_item")
    item = items[0]
    if item.get("symbol") != symbol:
        raise ValueError("funding_symbol_mismatch")
    raw_rate = finite_number(item.get("fundingRate"))
    funding_ms = integer(item.get("nextFundingTime"))
    interval_h = integer(item.get("fundingInterval"))
    if raw_rate is None or funding_ms is None or interval_h is None or interval_h <= 0:
        raise ValueError("funding_fields_invalid")
    normalized = raw_rate / FUNDING_NORMALIZATION_DIVISOR
    received = int(receipt["received_at"])
    raw_payload = {
        "symbol": symbol,
        "funding_rate_api": raw_rate,
        "api_unit": FUNDING_API_UNIT,
        "normalized_decimal_rate": normalized,
        "normalized_unit": FUNDING_NORMALIZED_UNIT,
        "normalization_rule": "api_percentage_points_divide_by_100",
        "next_funding_ms": funding_ms,
        "funding_interval_h": interval_h,
        "mark_price": finite_number(item.get("markPrice")),
        "last_price": finite_number(item.get("lastPrice")),
        "index_price": finite_number(item.get("indexPrice")),
    }
    raw = source_record(
        source_id=f"bitunix:futures:funding_raw:{symbol}:{received}",
        observed_at=received,
        received_at=received,
        schema_version="bitunix-funding-rest-v1",
        payload=raw_payload,
    )
    crowd_payload = {
        "kind": "funding_rate_8h",
        "value": normalized,
        "unit": FUNDING_NORMALIZED_UNIT,
        "raw_value": raw_rate,
        "raw_unit": FUNDING_API_UNIT,
        "normalization_rule": "api_percentage_points_divide_by_100",
        "funding_ms": funding_ms,
    }
    crowd = source_record(
        source_id=f"bitunix:futures:funding_crowd:{symbol}:{received}",
        observed_at=received,
        received_at=received,
        schema_version="crowd-point-v1",
        payload=crowd_payload,
    )
    event_payload = {
        "funding_ms": funding_ms,
        "rate": normalized,
        "unit": FUNDING_NORMALIZED_UNIT,
        "raw_value": raw_rate,
        "raw_unit": FUNDING_API_UNIT,
        "normalization_rule": "api_percentage_points_divide_by_100",
    }
    event = source_record(
        source_id=f"bitunix:futures:funding_event:{symbol}:{funding_ms}:{received}",
        observed_at=received,
        received_at=received,
        schema_version="funding-event-v1",
        payload=event_payload,
    )
    return raw, crowd, event


def depth_diagnostic(envelope: dict[str, Any], receipt: dict[str, Any], *, symbol: str) -> dict[str, Any]:
    items = data_items(envelope)
    if len(items) != 1:
        raise ValueError("depth_expected_one_item")
    item = items[0]
    bids = item.get("bids")
    asks = item.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
        raise ValueError("depth_levels_missing")
    normalized: dict[str, list[list[float]]] = {"bids": [], "asks": []}
    for side, levels in (("bids", bids), ("asks", asks)):
        for level in levels:
            if not isinstance(level, list) or len(level) != 2:
                raise ValueError("depth_level_shape_invalid")
            price = finite_number(level[0])
            size = finite_number(level[1])
            if price is None or size is None or price <= 0 or size <= 0:
                raise ValueError("depth_level_value_invalid")
            normalized[side].append([price, size])
    received = int(receipt["received_at"])
    payload = {
        "symbol": symbol,
        **normalized,
        "time_basis": "local_http_receive_only_no_venue_timestamp",
        "evaluator_admission_allowed": False,
    }
    return source_record(
        source_id=f"bitunix:futures:depth_rest_diagnostic:{symbol}:{received}",
        observed_at=received,
        received_at=received,
        schema_version="bitunix-depth-rest-diagnostic-v1",
        payload=payload,
    )


def build_snapshot(
    requester: Requester,
    *,
    symbol: str = "BTCUSDT",
    required: dict[str, int] | None = None,
    forward_floor_ms: int | None = None,
) -> dict[str, Any]:
    requirements = dict(required or DEFAULT_REQUIRED)
    all_receipts: list[dict[str, Any]] = []
    failures: list[str] = []
    bars: dict[str, list[dict[str, Any]]] = {}
    for interval in ("5m", "1h", "4h"):
        rows, receipts, interval_failures = fetch_closed_klines(
            requester, symbol=symbol, interval=interval, required=int(requirements[interval])
        )
        bars[interval] = rows
        all_receipts.extend(receipts)
        failures.extend(interval_failures)
    raw_funding: dict[str, Any] | None = None
    crowd_funding: dict[str, Any] | None = None
    funding_event: dict[str, Any] | None = None
    depth: dict[str, Any] | None = None
    try:
        funding_envelope, funding_receipt = requester("funding", {"symbol": symbol})
        all_receipts.append(funding_receipt)
        raw_funding, crowd_funding, funding_event = funding_records(funding_envelope, funding_receipt, symbol=symbol)
    except (RuntimeError, ValueError) as exc:
        failures.append(f"funding:{exc}")
    try:
        depth_envelope, depth_receipt = requester("depth", {"symbol": symbol, "limit": 15})
        all_receipts.append(depth_receipt)
        depth = depth_diagnostic(depth_envelope, depth_receipt, symbol=symbol)
    except (RuntimeError, ValueError) as exc:
        failures.append(f"depth:{exc}")
    received_values = [int(item["received_at"]) for item in all_receipts if integer(item.get("received_at")) is not None]
    snapshot_received = max(received_values) if received_values else now_ms()
    post_floor = forward_floor_ms is not None and snapshot_received >= forward_floor_ms
    return {
        "schema_version": 1,
        "generated_at": now_iso(snapshot_received),
        "tool": TOOL_PATH,
        "decision": (
            "bitunix_wo105_public_rest_snapshot_collected"
            if not failures
            else "bitunix_wo105_public_rest_snapshot_partial_hold"
        ),
        "symbol": symbol,
        "bars": bars,
        "funding_raw": raw_funding,
        "crowd_funding": crowd_funding,
        "funding_event": funding_event,
        "depth_diagnostic": depth,
        "http_receipts": all_receipts,
        "failures": sorted(set(failures)),
        "forward_floor_ms": forward_floor_ms,
        "snapshot_received_at": snapshot_received,
        "snapshot_phase": "FORWARD" if post_floor else "COMMISSIONING_PRE_FLOOR",
        "source_contract": {
            "native_bitunix_klines": True,
            "funding_api_unit": FUNDING_API_UNIT,
            "funding_normalized_unit": FUNDING_NORMALIZED_UNIT,
            "funding_normalization_rule": "divide_by_100",
            "rest_depth_diagnostic_only": True,
            "rest_depth_evaluator_admission_allowed": False,
            "native_public_oi_available": False,
            "credentials_used": 0,
            "private_calls": 0,
            "order_calls": 0,
        },
        "evaluator_packet_ready": False,
        "missing_for_packet": [
            "accepted_public_ws_books",
            "accepted_public_ws_trades",
            "cvd_norm_receipt",
            "at_least_one_additional_fresh_crowd_receipt_for_quorum",
        ],
        "runtime_boundary": {
            "public_read_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_snapshot(snapshot: dict[str, Any], outbase: Path) -> Path:
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S_%fZ")
    run_dir = outbase / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for interval, filename in (("5m", "BARS_5M.jsonl"), ("1h", "BARS_1H.jsonl"), ("4h", "BARS_4H.jsonl")):
        write_jsonl(run_dir / filename, snapshot["bars"][interval])
    write_jsonl(run_dir / "HTTP_RECEIPTS.jsonl", snapshot["http_receipts"])
    for key, filename in (
        ("funding_raw", "FUNDING_RAW.json"),
        ("crowd_funding", "CROWD_FUNDING.json"),
        ("funding_event", "FUNDING_EVENT.json"),
        ("depth_diagnostic", "DEPTH_REST_DIAGNOSTIC.json"),
    ):
        if snapshot.get(key) is not None:
            atomic_json(run_dir / filename, snapshot[key])
    manifest = {key: value for key, value in snapshot.items() if key != "bars"}
    manifest["bar_counts"] = {interval: len(rows) for interval, rows in snapshot["bars"].items()}
    manifest["run_dir"] = str(run_dir.resolve())
    atomic_json(run_dir / "PUBLIC_REST_SNAPSHOT_MANIFEST.json", manifest)
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Public read-only Bitunix WO105 REST source collector")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--outbase", default="data/forward/bitunix_wo105_rest")
    parser.add_argument("--forward-floor", default="2026-07-14T12:00:00Z")
    parser.add_argument("--timeout-s", type=float, default=20.0)
    args = parser.parse_args()
    try:
        floor = int(datetime.fromisoformat(args.forward_floor.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError as exc:
        raise SystemExit(f"invalid --forward-floor: {exc}") from exc

    def requester(endpoint: str, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        return public_get(endpoint, params, timeout_s=args.timeout_s)

    snapshot = build_snapshot(requester, symbol=args.symbol.upper(), forward_floor_ms=floor)
    run_dir = write_snapshot(snapshot, resolve(args.outbase))
    print(
        json.dumps(
            {
                "decision": snapshot["decision"],
                "phase": snapshot["snapshot_phase"],
                "bar_counts": {key: len(value) for key, value in snapshot["bars"].items()},
                "failures": snapshot["failures"],
                "evaluator_packet_ready": False,
                "run_dir": str(run_dir),
                "can_trade": False,
            }
        )
    )
    return 0 if not snapshot["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
