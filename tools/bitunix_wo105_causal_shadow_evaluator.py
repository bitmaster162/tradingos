#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TOOL_PATH = "tools/bitunix_wo105_causal_shadow_evaluator.py"
RECORD_FIELDS = {"source_id", "observed_at", "received_at", "source_hash", "schema_version", "payload"}
SERIES = ("signal_bars", "htf_bars", "crowd", "books", "trades", "outcome_bars", "funding_events")
TERMINAL_STATES = {"CAPTURE_INVALID", "NO_SETUP", "NO_FILL", "SHADOW_CLOSED", "COHORT_TOMBSTONED"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def sha256_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


def millis_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_iso_ms(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def validate_lock(lock: dict[str, Any], *, tool_path: Path | None = None) -> list[str]:
    failures: list[str] = []
    if lock.get("schema") != "bitunix-wo105-causal-shadow-prereg-v1":
        failures.append("lock_schema_invalid")
    if lock.get("status") != "FROZEN_CAUSAL_SHADOW_EVALUATOR":
        failures.append("lock_status_invalid")
    if lock.get("can_trade") is not False:
        failures.append("lock_can_trade_not_false")
    scope = lock.get("scope") if isinstance(lock.get("scope"), dict) else {}
    required_scope = {
        "signals_allowed": False,
        "paper_entries_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }
    if any(scope.get(key) != value for key, value in required_scope.items()):
        failures.append("lock_scope_not_fail_closed")
    params = lock.get("params") if isinstance(lock.get("params"), dict) else {}
    if lock.get("parameter_cohort_sha256") != canonical_sha256(params):
        failures.append("parameter_cohort_hash_mismatch")
    bindings = lock.get("bindings") if isinstance(lock.get("bindings"), dict) else {}
    actual_tool = tool_path or Path(__file__)
    if bindings.get("evaluator_sha256") != sha256_file(actual_tool):
        failures.append("evaluator_hash_mismatch")
    replay_path = resolve(str(bindings.get("exact_replay_report") or ""))
    if not replay_path.is_file() or bindings.get("exact_replay_report_sha256") != sha256_file(replay_path):
        failures.append("exact_replay_binding_mismatch")
    else:
        replay = read_object(replay_path)
        if replay.get("canonical_replay") != "PASS" or replay.get("public_contract_confirmed") is not True:
            failures.append("exact_replay_not_pass")
    v3_path = resolve(str(bindings.get("v3_path") or ""))
    if not v3_path.is_file() or bindings.get("v3_sha256") != sha256_file(v3_path):
        failures.append("v3_binding_mismatch")
    floor = parse_iso_ms(lock.get("forward_start_at"))
    if floor is None:
        failures.append("forward_start_invalid")
    return sorted(set(failures))


def validate_record(record: Any, *, expected_schema: str, evaluation_at: int, label: str) -> list[str]:
    failures: list[str] = []
    if not isinstance(record, dict):
        return [f"{label}:record_not_object"]
    missing = sorted(RECORD_FIELDS - set(record))
    if missing:
        failures.extend(f"{label}:missing_{field}" for field in missing)
        return failures
    if not isinstance(record.get("source_id"), str) or not record["source_id"].strip():
        failures.append(f"{label}:source_id_invalid")
    if record.get("schema_version") != expected_schema:
        failures.append(f"{label}:schema_version_invalid")
    observed = record.get("observed_at")
    received = record.get("received_at")
    if not isinstance(observed, int) or isinstance(observed, bool):
        failures.append(f"{label}:observed_at_invalid")
    if not isinstance(received, int) or isinstance(received, bool):
        failures.append(f"{label}:received_at_invalid")
    if isinstance(observed, int) and isinstance(received, int):
        if observed > received:
            failures.append(f"{label}:observed_after_received")
        if observed > evaluation_at or received > evaluation_at:
            failures.append(f"{label}:future_input")
    source_hash = record.get("source_hash")
    if not sha256_text(source_hash):
        failures.append(f"{label}:source_hash_invalid")
    elif source_hash != canonical_sha256(record.get("payload")):
        failures.append(f"{label}:source_hash_mismatch")
    if not isinstance(record.get("payload"), dict):
        failures.append(f"{label}:payload_not_object")
    return failures


def validate_bar_payload(payload: dict[str, Any], label: str) -> list[str]:
    failures: list[str] = []
    close_ms = payload.get("close_ms")
    if not isinstance(close_ms, int) or isinstance(close_ms, bool):
        failures.append(f"{label}:close_ms_invalid")
    values = {name: payload.get(name) for name in ("open", "high", "low", "close")}
    for name, value in values.items():
        if not finite(value):
            failures.append(f"{label}:{name}_invalid")
    if all(finite(value) for value in values.values()):
        if float(values["high"]) < max(float(values["open"]), float(values["close"]), float(values["low"])):
            failures.append(f"{label}:high_inconsistent")
        if float(values["low"]) > min(float(values["open"]), float(values["close"]), float(values["high"])):
            failures.append(f"{label}:low_inconsistent")
    return failures


def validate_book_payload(payload: dict[str, Any], label: str) -> list[str]:
    failures: list[str] = []
    for side in ("bids", "asks"):
        levels = payload.get(side)
        if not isinstance(levels, list) or not levels:
            failures.append(f"{label}:{side}_missing")
            continue
        parsed: list[tuple[float, float]] = []
        for level in levels:
            if not isinstance(level, list) or len(level) != 2 or not finite(level[0]) or not finite(level[1]):
                failures.append(f"{label}:{side}_level_invalid")
                continue
            price, size = float(level[0]), float(level[1])
            if price <= 0 or size <= 0:
                failures.append(f"{label}:{side}_level_nonpositive")
            parsed.append((price, size))
        prices = [price for price, _ in parsed]
        expected = sorted(prices, reverse=side == "bids")
        if prices != expected or len(set(prices)) != len(prices):
            failures.append(f"{label}:{side}_not_strictly_sorted")
    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    if bids and asks and finite(bids[0][0]) and finite(asks[0][0]) and float(bids[0][0]) >= float(asks[0][0]):
        failures.append(f"{label}:crossed_book")
    return failures


def validate_packet(packet: dict[str, Any], lock: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    failures: list[str] = []
    evaluation_at = packet.get("evaluation_at")
    if not isinstance(evaluation_at, int) or isinstance(evaluation_at, bool):
        return {}, ["evaluation_at_invalid"]
    if packet.get("schema") != "bitunix-wo105-causal-shadow-input-v1":
        failures.append("packet_schema_invalid")
    if packet.get("cohort_id") != lock.get("cohort_id"):
        failures.append("packet_cohort_mismatch")
    if packet.get("symbol") != (lock.get("params") or {}).get("symbol"):
        failures.append("packet_symbol_mismatch")
    records: dict[str, list[dict[str, Any]]] = {}
    schemas = (lock.get("params") or {}).get("record_schemas") or {}
    for series in SERIES:
        rows = packet.get(series, [])
        if not isinstance(rows, list):
            failures.append(f"{series}:not_list")
            rows = []
        records[series] = [row for row in rows if isinstance(row, dict)]
        if series in ("signal_bars", "htf_bars", "crowd") and not rows:
            failures.append(f"{series}:missing")
        for index, row in enumerate(rows):
            label = f"{series}[{index}]"
            failures.extend(validate_record(row, expected_schema=str(schemas.get(series) or ""), evaluation_at=evaluation_at, label=label))
            if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
                continue
            payload = row["payload"]
            if series in ("signal_bars", "htf_bars", "outcome_bars"):
                failures.extend(validate_bar_payload(payload, label))
            elif series == "books":
                failures.extend(validate_book_payload(payload, label))
            elif series == "trades":
                if not finite(payload.get("price")) or not finite(payload.get("size")):
                    failures.append(f"{label}:trade_numeric_invalid")
                if payload.get("side") not in ("buy", "sell"):
                    failures.append(f"{label}:trade_side_invalid")
            elif series == "crowd":
                if not isinstance(payload.get("kind"), str) or not finite(payload.get("value")):
                    failures.append(f"{label}:crowd_payload_invalid")
            elif series == "funding_events":
                if not isinstance(payload.get("funding_ms"), int) or not finite(payload.get("rate")):
                    failures.append(f"{label}:funding_payload_invalid")

        chronology = [
            int(row["payload"].get("close_ms", row["payload"].get("funding_ms", row.get("observed_at", -1))))
            for row in records[series]
            if isinstance(row.get("payload"), dict)
        ]
        receipts = [int(row.get("received_at", -1)) for row in records[series]]
        if chronology != sorted(chronology) or len(set(chronology)) != len(chronology):
            failures.append(f"{series}:event_time_reordered_or_duplicate")
        if receipts != sorted(receipts):
            failures.append(f"{series}:receipt_time_reordered")
    return records, sorted(set(failures))


def bar_values(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row["payload"], _source_hash=row["source_hash"], _received_at=row["received_at"]) for row in rows]


def detect_setup(rows: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any] | None:
    bars = bar_values(rows)
    pivot = params["pivot"]
    lookback = int(pivot["lookback_bars"])
    delay = int(pivot["confirmation_delay_bars"])
    reclaim_window = int(params["reclaim_window_bars"])
    tick = float(params["contract"]["tick_size"])
    threshold = int(params["sweep_threshold_ticks"]) * tick
    candidates: list[dict[str, Any]] = []
    for index in range(lookback - 1, len(bars) - delay - 1):
        window = bars[index - lookback + 1 : index + 1]
        high = float(bars[index]["high"])
        low = float(bars[index]["low"])
        high_is_pivot = high == max(float(item["high"]) for item in window) and next(
            item for item, value in enumerate(window) if float(value["high"]) == high
        ) == len(window) - 1
        low_is_pivot = low == min(float(item["low"]) for item in window) and next(
            item for item, value in enumerate(window) if float(value["low"]) == low
        ) == len(window) - 1
        confirmed_high = high_is_pivot and all(float(item["high"]) < high for item in bars[index + 1 : index + 1 + delay])
        confirmed_low = low_is_pivot and all(float(item["low"]) > low for item in bars[index + 1 : index + 1 + delay])
        sweep_start = index + delay + 1
        for sweep_index in range(sweep_start, len(bars)):
            sweep = bars[sweep_index]
            matched = False
            if confirmed_high and float(sweep["high"]) >= high + threshold:
                for reclaim_index in range(sweep_index, min(len(bars), sweep_index + reclaim_window)):
                    if float(bars[reclaim_index]["close"]) < high:
                        candidates.append(
                            {
                                "direction": "SHORT",
                                "pivot_price": high,
                                "pivot_close_ms": bars[index]["close_ms"],
                                "sweep_extreme": float(sweep["high"]),
                                "sweep_close_ms": sweep["close_ms"],
                                "reclaim_index": reclaim_index,
                                "signal_close_ms": bars[reclaim_index]["close_ms"],
                                "reclaim_close": float(bars[reclaim_index]["close"]),
                            }
                        )
                        matched = True
                        break
            if matched:
                break
            if confirmed_low and float(sweep["low"]) <= low - threshold:
                for reclaim_index in range(sweep_index, min(len(bars), sweep_index + reclaim_window)):
                    if float(bars[reclaim_index]["close"]) > low:
                        candidates.append(
                            {
                                "direction": "LONG",
                                "pivot_price": low,
                                "pivot_close_ms": bars[index]["close_ms"],
                                "sweep_extreme": float(sweep["low"]),
                                "sweep_close_ms": sweep["close_ms"],
                                "reclaim_index": reclaim_index,
                                "signal_close_ms": bars[reclaim_index]["close_ms"],
                                "reclaim_close": float(bars[reclaim_index]["close"]),
                            }
                        )
                        matched = True
                        break
            if matched:
                break
    if not candidates:
        return None
    last_index = len(bars) - 1
    latest = [item for item in candidates if item["reclaim_index"] == last_index]
    if len(latest) != 1:
        return None
    latest[0].pop("reclaim_index", None)
    return latest[0]


def ema(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def htf_verdict(rows: list[dict[str, Any]], *, signal_close_ms: int, params: dict[str, Any]) -> dict[str, Any]:
    bars = [bar for bar in bar_values(rows) if int(bar["close_ms"]) <= signal_close_ms]
    minimum = int(params["htf"]["minimum_bars"])
    if len(bars) < minimum:
        return {"valid": False, "reason": f"htf_insufficient_bars:{len(bars)}<{minimum}"}
    latest = bars[-1]
    max_age = int(params["htf"]["latest_close_max_age_ms"])
    if signal_close_ms - int(latest["close_ms"]) > max_age:
        return {"valid": False, "reason": "htf_latest_bar_stale"}
    closes = [float(bar["close"]) for bar in bars]
    fast = ema(closes, int(params["htf"]["ema_fast"]))
    slow = ema(closes, int(params["htf"]["ema_slow"]))
    slope_lookback = int(params["htf"]["slope_lookback_bars"])
    slope = fast[-1] - fast[-1 - slope_lookback]
    if closes[-1] > slow[-1] and fast[-1] > slow[-1] and slope > 0:
        verdict = "up_strong"
    elif closes[-1] < slow[-1] and fast[-1] < slow[-1] and slope < 0:
        verdict = "down_strong"
    elif slope > 0:
        verdict = "up_mild"
    elif slope < 0:
        verdict = "down_mild"
    else:
        verdict = "flat"
    return {
        "valid": True,
        "verdict": verdict,
        "latest_close_ms": latest["close_ms"],
        "close": closes[-1],
        "ema_fast": fast[-1],
        "ema_slow": slow[-1],
        "ema_fast_slope": slope,
    }


def crowd_gate(
    rows: list[dict[str, Any]], *, direction: str, cutoff_ms: int, params: dict[str, Any]
) -> dict[str, Any]:
    definitions = params["crowd_funding"]["sources"]
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = row["payload"]
        kind = str(payload.get("kind") or "")
        if kind not in definitions or int(row["received_at"]) > cutoff_ms or int(row["observed_at"]) > cutoff_ms:
            continue
        latest[kind] = row
    accepted: dict[str, float] = {}
    stale: list[str] = []
    vetoes: list[str] = []
    for kind, row in latest.items():
        spec = definitions[kind]
        if cutoff_ms - int(row["observed_at"]) > int(spec["freshness_max_age_ms"]):
            stale.append(kind)
            continue
        value = float(row["payload"]["value"])
        accepted[kind] = value
        positive_side = str(spec["sign_positive_means"]).upper()
        crowded_side = positive_side if value > 0 else ("SHORT" if positive_side == "LONG" else "LONG")
        if abs(value) > float(spec["abs_max"]) and crowded_side == direction:
            vetoes.append(kind)
    quorum = int(params["crowd_funding"]["quorum_fresh_inputs_required"])
    return {
        "input_valid": len(accepted) >= quorum,
        "passed": len(accepted) >= quorum and not vetoes,
        "accepted": accepted,
        "stale": sorted(stale),
        "vetoes": sorted(vetoes),
        "quorum": f"{len(accepted)}/{quorum}",
    }


def round_tick(value: float, tick: float, direction: str) -> float:
    units = Decimal(str(value)) / Decimal(str(tick))
    rounding = ROUND_FLOOR if direction == "LONG" else ROUND_CEILING
    return float(units.to_integral_value(rounding=rounding) * Decimal(str(tick)))


def floor_step(value: float, step: float) -> float:
    units = Decimal(str(value)) / Decimal(str(step))
    return float(units.to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(step)))


def select_entry_book(rows: list[dict[str, Any]], signal_close_ms: int, params: dict[str, Any]) -> dict[str, Any] | None:
    eligible = signal_close_ms + int(params["entry"]["latency_ms"])
    max_age = int(params["entry"]["book_age_max_ms"])
    for row in rows:
        if int(row["received_at"]) < eligible:
            continue
        if int(row["received_at"]) - int(row["observed_at"]) > max_age:
            continue
        return row
    return None


def pre_entry_manifest(packet: dict[str, Any], setup: dict[str, Any], book: dict[str, Any]) -> str:
    cutoff = int(book["received_at"])
    bound = {
        "cohort_id": packet["cohort_id"],
        "symbol": packet["symbol"],
        "signal_close_ms": setup["signal_close_ms"],
        "series": {
            "signal_bars": [row["source_hash"] for row in packet["signal_bars"]],
            "htf_bars": [row["source_hash"] for row in packet["htf_bars"]],
            "crowd": [row["source_hash"] for row in packet["crowd"] if int(row["received_at"]) <= cutoff],
            "entry_book": book["source_hash"],
        },
    }
    return canonical_sha256(bound)


def event_identity(lock: dict[str, Any], packet: dict[str, Any], setup: dict[str, Any], manifest_hash: str) -> str:
    return canonical_sha256(
        {
            "cohort_id": lock["cohort_id"],
            "symbol": packet["symbol"],
            "signal_close_ms": setup["signal_close_ms"],
            "pivot_price": setup["pivot_price"],
            "direction": setup["direction"],
            "source_manifest_sha256": manifest_hash,
        }
    )


def entry_plan(setup: dict[str, Any], book: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    direction = setup["direction"]
    tick = float(params["contract"]["tick_size"])
    lot = float(params["contract"]["lot_step"])
    price = round_tick(float(setup["reclaim_close"]), tick, direction)
    bids = [(float(price_), float(size)) for price_, size in book["payload"]["bids"]]
    asks = [(float(price_), float(size)) for price_, size in book["payload"]["asks"]]
    maker = price < asks[0][0] if direction == "LONG" else price > bids[0][0]
    quantity = floor_step(float(params["shadow_sizing"]["notional_usdt"]) / price, lot)
    same_side = bids if direction == "LONG" else asks
    queue_ahead = sum(size for level, size in same_side if abs(level - price) < tick / 10.0)
    buffer = int(params["invalidation_buffer_ticks"]) * tick
    stop = setup["sweep_extreme"] - buffer if direction == "LONG" else setup["sweep_extreme"] + buffer
    risk_per_unit = abs(price - stop)
    target_r = float(params["outcome"]["target_r"])
    target = price + target_r * risk_per_unit if direction == "LONG" else price - target_r * risk_per_unit
    return {
        "valid": maker and quantity >= float(params["contract"]["min_qty"]) and risk_per_unit > 0,
        "maker": maker,
        "entry_price": price,
        "quantity": quantity,
        "queue_ahead": queue_ahead,
        "stop_price": stop,
        "target_price": target,
        "risk_per_unit": risk_per_unit,
        "eligible_at": int(book["received_at"]),
        "order_expires_at": int(book["received_at"]) + int(params["entry"]["maker_order_ttl_ms"]),
    }


def maker_fill(plan: dict[str, Any], rows: list[dict[str, Any]], direction: str, params: dict[str, Any]) -> dict[str, Any]:
    tick = float(params["contract"]["tick_size"])
    threshold = plan["entry_price"] - tick if direction == "LONG" else plan["entry_price"] + tick
    required_side = "sell" if direction == "LONG" else "buy"
    through = 0.0
    first_fill_at: int | None = None
    queue_required = plan["queue_ahead"] * float(params["execution_model"]["queue_penalty_multiplier"])
    for row in rows:
        if int(row["received_at"]) <= plan["eligible_at"] or int(row["received_at"]) > plan["order_expires_at"]:
            continue
        payload = row["payload"]
        price = float(payload["price"])
        crosses = price <= threshold if direction == "LONG" else price >= threshold
        if payload["side"] != required_side or not crosses:
            continue
        previous = max(0.0, through - queue_required)
        through += float(payload["size"])
        current = max(0.0, through - queue_required)
        if first_fill_at is None and current > previous:
            first_fill_at = int(row["received_at"])
    fill_qty = min(plan["quantity"], max(0.0, through - queue_required))
    return {
        "filled_qty": fill_qty,
        "fill_fraction": fill_qty / plan["quantity"] if plan["quantity"] else 0.0,
        "first_fill_at": first_fill_at,
        "position_activates_at": plan["order_expires_at"] if fill_qty > 0 else None,
        "through_volume": through,
        "queue_required": queue_required,
    }


def find_exit_trigger(
    rows: list[dict[str, Any]], plan: dict[str, Any], activation_ms: int, deadline_ms: int, direction: str
) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    for row in rows:
        bar = row["payload"]
        close_ms = int(bar["close_ms"])
        if close_ms <= activation_ms or close_ms > deadline_ms:
            continue
        last = row
        stop_hit = float(bar["low"]) <= plan["stop_price"] if direction == "LONG" else float(bar["high"]) >= plan["stop_price"]
        target_hit = float(bar["high"]) >= plan["target_price"] if direction == "LONG" else float(bar["low"]) <= plan["target_price"]
        if stop_hit:
            return {"reason": "stop", "trigger_at": close_ms, "reference_price": plan["stop_price"]}
        if target_hit:
            return {"reason": "target", "trigger_at": close_ms, "reference_price": plan["target_price"]}
    if last is not None and int(last["payload"]["close_ms"]) >= deadline_ms:
        return {"reason": "time", "trigger_at": deadline_ms, "reference_price": float(last["payload"]["close"])}
    return None


def walk_exit_book(
    rows: list[dict[str, Any]], *, trigger_at: int, quantity: float, direction: str, params: dict[str, Any]
) -> dict[str, Any] | None:
    eligible = trigger_at + int(params["exit"]["latency_ms"])
    max_age = int(params["exit"]["book_age_max_ms"])
    selected = next(
        (
            row
            for row in rows
            if int(row["received_at"]) >= eligible
            and int(row["received_at"]) - int(row["observed_at"]) <= max_age
        ),
        None,
    )
    if selected is None:
        return None
    levels = selected["payload"]["bids" if direction == "LONG" else "asks"]
    remaining = quantity
    notional = 0.0
    for price, size in levels:
        take = min(remaining, float(size))
        notional += take * float(price)
        remaining -= take
        if remaining <= 1e-12:
            break
    if remaining > 1e-12:
        return None
    return {
        "exit_price": notional / quantity,
        "book_received_at": selected["received_at"],
        "book_source_hash": selected["source_hash"],
    }


def funding_cost(
    rows: list[dict[str, Any]],
    *,
    activation_ms: int,
    exit_ms: int,
    notional: float,
    direction: str,
    interval_h: int,
) -> tuple[float, list[dict[str, Any]], list[int]]:
    total = 0.0
    applied: list[dict[str, Any]] = []
    multiplier = 1.0 if direction == "LONG" else -1.0
    interval_ms = interval_h * 3_600_000
    first_boundary = ((activation_ms // interval_ms) + 1) * interval_ms
    expected = list(range(first_boundary, exit_ms + 1, interval_ms))
    supplied: set[int] = set()
    for row in rows:
        payload = row["payload"]
        funding_ms = int(payload["funding_ms"])
        if activation_ms < funding_ms <= exit_ms:
            supplied.add(funding_ms)
            cost = notional * float(payload["rate"]) * multiplier
            total += cost
            applied.append({"funding_ms": funding_ms, "rate": payload["rate"], "cost": cost})
    return total, applied, sorted(set(expected) - supplied)


def state_report(
    *,
    state: str,
    decision: str,
    failures: list[str] | None = None,
    event_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "state": state,
        "decision": decision,
        "event_id": event_id,
        "failures": sorted(set(failures or [])),
        "details": details or {},
        "edge_evaluated": False,
        "runtime_boundary": {
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def evaluate_packet(
    packet: dict[str, Any], lock: dict[str, Any], *, previous_events: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    lock_failures = validate_lock(lock)
    if lock_failures:
        return state_report(state="CAPTURE_INVALID", decision="bitunix_wo105_hold_lock_invalid", failures=lock_failures)
    records, failures = validate_packet(packet, lock)
    if failures:
        return state_report(state="CAPTURE_INVALID", decision="bitunix_wo105_hold_input_invalid", failures=failures)
    params = lock["params"]
    setup = detect_setup(records["signal_bars"], params)
    if setup is None:
        return state_report(state="NO_SETUP", decision="bitunix_wo105_no_causal_sfp_setup")
    floor = parse_iso_ms(lock["forward_start_at"])
    if floor is None or int(setup["signal_close_ms"]) < floor:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_hold_pre_floor_backfill",
            failures=["signal_close_before_forward_floor"],
            details={"signal_close_ms": setup["signal_close_ms"], "forward_start_at": lock["forward_start_at"]},
        )
    htf = htf_verdict(records["htf_bars"], signal_close_ms=int(setup["signal_close_ms"]), params=params)
    if not htf.get("valid"):
        return state_report(
            state="CAPTURE_INVALID", decision="bitunix_wo105_hold_htf_invalid", failures=[str(htf.get("reason"))], details={"setup": setup}
        )
    if (setup["direction"] == "SHORT" and htf["verdict"] == "up_strong") or (
        setup["direction"] == "LONG" and htf["verdict"] == "down_strong"
    ):
        return state_report(
            state="NO_SETUP",
            decision="bitunix_wo105_setup_vetoed_by_computed_htf",
            details={"setup": setup, "htf": htf},
        )
    cutoff = int(setup["signal_close_ms"]) + int(params["entry"]["latency_ms"])
    crowd = crowd_gate(records["crowd"], direction=setup["direction"], cutoff_ms=cutoff, params=params)
    if not crowd["input_valid"]:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_hold_crowd_or_funding_input_stale_or_missing",
            failures=["fresh_crowd_funding_quorum_not_met"],
            details={"setup": setup, "htf": htf, "crowd": crowd},
        )
    if crowd["vetoes"]:
        return state_report(
            state="NO_SETUP",
            decision="bitunix_wo105_setup_vetoed_by_crowd_or_funding",
            details={"setup": setup, "htf": htf, "crowd": crowd},
        )
    book = select_entry_book(records["books"], int(setup["signal_close_ms"]), params)
    if book is None:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_hold_no_fresh_eligible_entry_book",
            failures=["eligible_entry_book_missing_or_stale"],
            details={"setup": setup, "htf": htf, "crowd": crowd},
        )
    manifest_hash = pre_entry_manifest(packet, setup, book)
    if packet.get("source_manifest_sha256") != manifest_hash:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_hold_source_manifest_mismatch",
            failures=["source_manifest_sha256_mismatch"],
            details={"computed_source_manifest_sha256": manifest_hash},
        )
    event_id = event_identity(lock, packet, setup, manifest_hash)
    previous = (previous_events or {}).get(event_id)
    if previous and previous.get("state") in TERMINAL_STATES:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_hold_duplicate_terminal_event",
            failures=["event_id_already_terminal"],
            event_id=event_id,
        )
    if previous and previous.get("cohort_binding_sha256") != lock.get("parameter_cohort_sha256"):
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_hold_opened_event_parameter_drift",
            failures=["opened_event_cohort_binding_mismatch"],
            event_id=event_id,
        )
    plan = entry_plan(setup, book, params)
    if not plan["valid"]:
        return state_report(
            state="NO_FILL",
            decision="bitunix_wo105_entry_not_valid_maker_order",
            event_id=event_id,
            details={"setup": setup, "htf": htf, "crowd": crowd, "entry": plan},
        )
    fill = maker_fill(plan, records["trades"], setup["direction"], params)
    evaluation_at = int(packet["evaluation_at"])
    common = {
        "cohort_binding_sha256": lock["parameter_cohort_sha256"],
        "source_manifest_sha256": manifest_hash,
        "setup": setup,
        "htf": htf,
        "crowd": crowd,
        "entry": plan,
        "fill": fill,
    }
    if fill["filled_qty"] <= 0:
        if evaluation_at < plan["order_expires_at"]:
            return state_report(
                state="HOLD",
                decision="bitunix_wo105_entry_pending_no_fill_yet",
                event_id=event_id,
                details=common,
            )
        return state_report(
            state="NO_FILL",
            decision="bitunix_wo105_terminal_no_conservative_maker_fill",
            event_id=event_id,
            details=common,
        )
    activation = int(fill["position_activates_at"])
    deadline = activation + int(params["outcome"]["max_holding_ms"])
    trigger = find_exit_trigger(records["outcome_bars"], plan, activation, deadline, setup["direction"])
    if trigger is None:
        if evaluation_at >= deadline:
            return state_report(
                state="CAPTURE_INVALID",
                decision="bitunix_wo105_hold_matured_outcome_data_missing",
                failures=["closed_outcome_bars_missing_through_deadline"],
                event_id=event_id,
                details={**common, "activation_ms": activation, "deadline_ms": deadline},
            )
        return state_report(
            state="SHADOW_OPEN",
            decision="bitunix_wo105_shadow_position_open_outcome_hidden",
            event_id=event_id,
            details={**common, "activation_ms": activation, "deadline_ms": deadline},
        )
    exit_book = walk_exit_book(
        records["books"],
        trigger_at=int(trigger["trigger_at"]),
        quantity=float(fill["filled_qty"]),
        direction=setup["direction"],
        params=params,
    )
    if exit_book is None:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_hold_exit_book_missing_or_insufficient",
            failures=["causal_exit_book_missing_stale_or_insufficient"],
            event_id=event_id,
            details={**common, "exit_trigger": trigger},
        )
    quantity = float(fill["filled_qty"])
    entry_price = float(plan["entry_price"])
    exit_price = float(exit_book["exit_price"])
    gross = quantity * (exit_price - entry_price) * (1.0 if setup["direction"] == "LONG" else -1.0)
    entry_notional = quantity * entry_price
    exit_notional = quantity * exit_price
    fees = entry_notional * float(params["fees"]["maker_bps"]) / 10000.0
    fees += exit_notional * float(params["fees"]["taker_bps"]) / 10000.0
    funding, applied_funding, missing_funding = funding_cost(
        records["funding_events"],
        activation_ms=activation,
        exit_ms=int(trigger["trigger_at"]),
        notional=entry_notional,
        direction=setup["direction"],
        interval_h=int(params["funding_treatment"]["interval_h"]),
    )
    if missing_funding:
        return state_report(
            state="CAPTURE_INVALID",
            decision="bitunix_wo105_hold_funding_receipt_missing",
            failures=[f"funding_receipt_missing:{value}" for value in missing_funding],
            event_id=event_id,
            details={**common, "exit_trigger": trigger, "exit_book": exit_book},
        )
    net = gross - fees - funding
    initial_risk = quantity * float(plan["risk_per_unit"])
    outcome = {
        "exit_trigger": trigger,
        "exit_book": exit_book,
        "filled_qty": quantity,
        "gross_pnl_usdt": gross,
        "fees_usdt": fees,
        "funding_cost_usdt": funding,
        "funding_events": applied_funding,
        "net_pnl_usdt": net,
        "initial_risk_usdt": initial_risk,
        "net_r": net / initial_risk if initial_risk > 0 else None,
    }
    return state_report(
        state="SHADOW_CLOSED",
        decision="bitunix_wo105_shadow_event_closed_not_edge_evaluated",
        event_id=event_id,
        details={**common, "activation_ms": activation, "deadline_ms": deadline, "outcome": outcome},
    )


def load_previous_events(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return latest
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("event_id"), str):
            latest[row["event_id"]] = row
    return latest


def append_ledger(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed causal shadow evaluator for the Bitunix WO105 cohort")
    parser.add_argument("packet")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_2026-07-14.json")
    parser.add_argument("--ledger", default="_dl/bitunix_wo105_shadow/EVENT_LEDGER.jsonl")
    parser.add_argument("--out", default="_dl/bitunix_wo105_shadow/LAST_EVALUATION.json")
    args = parser.parse_args()
    lock = read_object(resolve(args.lock))
    packet = read_object(resolve(args.packet))
    ledger = resolve(args.ledger)
    report = evaluate_packet(packet, lock, previous_events=load_previous_events(ledger))
    output = resolve(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report.get("event_id") and report.get("state") != "CAPTURE_INVALID":
        append_ledger(ledger, {**report, "cohort_binding_sha256": lock.get("parameter_cohort_sha256")})
    print(json.dumps({"decision": report["decision"], "state": report["state"], "can_trade": False}))
    return 0 if report["state"] != "CAPTURE_INVALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
