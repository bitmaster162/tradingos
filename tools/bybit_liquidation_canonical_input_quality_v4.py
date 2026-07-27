#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_context_intake as base
from tools import bybit_liquidation_canonical_input_quality as v3


TOOL_PATH = "tools/bybit_liquidation_canonical_input_quality_v4.py"
REQUIRED_RECEIPT_FIELDS = {
    "received_at_ns",
    "received_monotonic_ns",
    "corrected_received_at_ns",
    "collector_session_id",
    "packet_sequence",
    "clock_calibration_id",
    "clock_calibrated_at_ns",
    "clock_offset_ns",
    "clock_rtt_ns",
    "clock_uncertainty_ns",
    "clock_calibration_samples",
    "clock_calibration_source",
}


def integer(row: dict[str, Any], field: str) -> int | None:
    try:
        return int(row.get(field))
    except (TypeError, ValueError):
        return None


def iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def metric(values: list[float]) -> dict[str, float | None]:
    return {
        "min": round(min(values), 3) if values else None,
        "p50": round(statistics.median(values), 3) if values else None,
        "p95": round(v3.percentile(values, 0.95), 3) if values else None,
        "max": round(max(values), 3) if values else None,
    }


def scan_events(
    data_dir: Path,
    symbols: list[str],
    forward_floor_at: str,
    gate: dict[str, Any],
) -> dict[str, Any]:
    symbol_set = {item.upper() for item in symbols}
    floor = base.parse_ts(forward_floor_at)
    if floor is None:
        raise ValueError("invalid forward floor")
    files = sorted(data_dir.rglob("*.jsonl")) if data_dir.exists() else []
    required_schema = int(gate["required_ingest_schema_version"])
    grace_ns = int(float(gate["corrected_receipt_uncertainty_grace_ms"]) * 1_000_000)
    maximum_rtt_ns = int(float(gate["maximum_clock_calibration_rtt_ms"]) * 1_000_000)
    maximum_age_ns = int(float(gate["maximum_clock_calibration_age_s"]) * 1_000_000_000)
    maximum_offset_ns = int(float(gate["maximum_absolute_clock_offset_ms"]) * 1_000_000)
    maximum_event_lag_ms = float(gate["maximum_exchange_event_lag_ms"])
    maximum_receipt_lag_ms = float(gate["maximum_corrected_receipt_lag_ms"])
    minimum_samples = int(gate["minimum_clock_calibration_samples"])

    counters: Counter[str] = Counter()
    seen: set[tuple[Any, ...]] = set()
    packet_receipts: dict[tuple[str, int], tuple[int, int, int, str]] = {}
    sessions: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_symbol: Counter[str] = Counter()
    by_side: Counter[str] = Counter()
    raw_receipt_lags_ms: list[float] = []
    corrected_receipt_lags_ms: list[float] = []
    corrected_event_lags_ms: list[float] = []
    exchange_event_lags_ms: list[float] = []
    clock_offsets_ms: list[float] = []
    clock_rtts_ms: list[float] = []
    calibration_ages_s: list[float] = []
    first_liquidation_ms: int | None = None
    last_liquidation_ms: int | None = None
    error_sample: list[dict[str, Any]] = []

    for path in files:
        previous_ms: int | None = None
        file_nonmonotonic = False
        file_post_floor_nonmonotonic = False
        try:
            partition_date = datetime.strptime(path.stem, "%Y%m%d").date()
        except ValueError:
            partition_date = None
        partition_may_contain_post_floor = partition_date is None or partition_date >= floor.date()
        try:
            handle = path.open("r", encoding="utf-8-sig")
        except OSError as exc:
            counters["json_parse_errors"] += 1
            counters["post_floor_json_parse_errors"] += int(partition_may_contain_post_floor)
            if len(error_sample) < 25:
                error_sample.append({"path": base.portable(path), "line": None, "error": repr(exc)})
            continue
        with handle:
            for line_no, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError as exc:
                    counters["json_parse_errors"] += 1
                    counters["post_floor_json_parse_errors"] += int(partition_may_contain_post_floor)
                    if len(error_sample) < 25:
                        error_sample.append({"path": base.portable(path), "line": line_no, "error": f"json:{exc}"})
                    continue
                if not isinstance(row, dict):
                    counters["json_parse_errors"] += 1
                    counters["post_floor_json_parse_errors"] += int(partition_may_contain_post_floor)
                    continue
                symbol = str(row.get("symbol") or "").upper()
                if symbol not in symbol_set:
                    continue
                counters["events"] += 1
                liquidation_dt = base.ms_to_dt(row.get("liquidation_time_ms"))
                is_post_floor = liquidation_dt is not None and liquidation_dt >= floor
                counters["post_floor_events"] += int(is_post_floor)
                errors = base.validate_event_payload(row)
                if row.get("venue") != "bybit":
                    errors.append("venue")
                if integer(row, "ingest_schema_version") != required_schema:
                    errors.append("ingest_schema_version")
                errors.extend(field for field in REQUIRED_RECEIPT_FIELDS if row.get(field) in (None, ""))
                if errors:
                    counters["schema_errors"] += 1
                    counters["post_floor_schema_errors"] += int(is_post_floor)
                    if len(error_sample) < 25:
                        error_sample.append(
                            {"path": base.portable(path), "line": line_no, "error": ";".join(sorted(set(errors)))}
                        )
                else:
                    counters["schema_valid_events"] += 1
                    counters["post_floor_schema_valid_events"] += int(is_post_floor)
                if liquidation_dt is None:
                    continue

                liquidation_ms = int(row["liquidation_time_ms"])
                if path.parent.name.upper() != symbol or path.stem != liquidation_dt.strftime("%Y%m%d"):
                    counters["partition_mismatches"] += 1
                    counters["post_floor_partition_mismatches"] += int(is_post_floor)
                if previous_ms is not None and liquidation_ms < previous_ms:
                    file_nonmonotonic = True
                    file_post_floor_nonmonotonic = file_post_floor_nonmonotonic or is_post_floor
                previous_ms = liquidation_ms

                identity = v3.event_identity(row)
                if identity in seen:
                    counters["duplicate_event_identities"] += 1
                    counters["post_floor_duplicate_event_identities"] += int(is_post_floor)
                else:
                    seen.add(identity)
                by_symbol[symbol] += 1
                by_side[str(row.get("side") or "").upper()] += 1
                first_liquidation_ms = liquidation_ms if first_liquidation_ms is None else min(first_liquidation_ms, liquidation_ms)
                last_liquidation_ms = liquidation_ms if last_liquidation_ms is None else max(last_liquidation_ms, liquidation_ms)
                if not is_post_floor or errors:
                    continue

                received_ns = integer(row, "received_at_ns")
                monotonic_ns = integer(row, "received_monotonic_ns")
                corrected_ns = integer(row, "corrected_received_at_ns")
                calibrated_ns = integer(row, "clock_calibrated_at_ns")
                offset_ns = integer(row, "clock_offset_ns")
                rtt_ns = integer(row, "clock_rtt_ns")
                uncertainty_ns = integer(row, "clock_uncertainty_ns")
                calibration_samples = integer(row, "clock_calibration_samples")
                event_ms = integer(row, "event_time_ms")
                sequence = integer(row, "packet_sequence")
                session_id = str(row.get("collector_session_id") or "")
                calibration_id = str(row.get("clock_calibration_id") or "")
                integers = (
                    received_ns,
                    monotonic_ns,
                    corrected_ns,
                    calibrated_ns,
                    offset_ns,
                    rtt_ns,
                    uncertainty_ns,
                    calibration_samples,
                    event_ms,
                    sequence,
                )
                if any(value is None for value in integers) or not session_id or not calibration_id:
                    counters["post_floor_receipt_parse_errors"] += 1
                    continue
                assert received_ns is not None and monotonic_ns is not None and corrected_ns is not None
                assert calibrated_ns is not None and offset_ns is not None and rtt_ns is not None
                assert uncertainty_ns is not None and calibration_samples is not None and event_ms is not None
                assert sequence is not None
                if corrected_ns != received_ns + offset_ns:
                    counters["post_floor_correction_equation_failures"] += 1
                age_ns = received_ns - calibrated_ns
                if age_ns < -uncertainty_ns or age_ns > maximum_age_ns:
                    counters["post_floor_calibration_age_failures"] += 1
                if rtt_ns < 0 or rtt_ns > maximum_rtt_ns:
                    counters["post_floor_calibration_rtt_failures"] += 1
                if uncertainty_ns < 0 or uncertainty_ns > max(rtt_ns, 1):
                    counters["post_floor_calibration_uncertainty_failures"] += 1
                if abs(offset_ns) > maximum_offset_ns:
                    counters["post_floor_clock_offset_failures"] += 1
                if calibration_samples < minimum_samples:
                    counters["post_floor_calibration_sample_failures"] += 1
                if not str(row.get("clock_calibration_source") or "").startswith("https://api.bybit.com/"):
                    counters["post_floor_calibration_source_failures"] += 1
                exchange_lag_ms = event_ms - liquidation_ms
                corrected_event_lag_ms = corrected_ns / 1_000_000.0 - event_ms
                corrected_liquidation_lag_ms = corrected_ns / 1_000_000.0 - liquidation_ms
                if exchange_lag_ms < 0 or exchange_lag_ms > maximum_event_lag_ms:
                    counters["post_floor_exchange_event_lag_failures"] += 1
                if corrected_ns + uncertainty_ns + grace_ns < event_ms * 1_000_000:
                    counters["post_floor_corrected_before_event_failures"] += 1
                if corrected_event_lag_ms > maximum_receipt_lag_ms:
                    counters["post_floor_corrected_receipt_lag_failures"] += 1

                key = (session_id, sequence)
                receipt = (monotonic_ns, received_ns, corrected_ns, calibration_id)
                if key in packet_receipts and packet_receipts[key] != receipt:
                    counters["post_floor_packet_receipt_conflicts"] += 1
                else:
                    packet_receipts[key] = receipt
                sessions[session_id].append((sequence, monotonic_ns))
                raw_receipt_lags_ms.append(received_ns / 1_000_000.0 - liquidation_ms)
                corrected_receipt_lags_ms.append(corrected_liquidation_lag_ms)
                corrected_event_lags_ms.append(corrected_event_lag_ms)
                exchange_event_lags_ms.append(float(exchange_lag_ms))
                clock_offsets_ms.append(offset_ns / 1_000_000.0)
                clock_rtts_ms.append(rtt_ns / 1_000_000.0)
                calibration_ages_s.append(age_ns / 1_000_000_000.0)
        counters["nonmonotonic_files"] += int(file_nonmonotonic)
        counters["post_floor_nonmonotonic_files"] += int(file_post_floor_nonmonotonic)

    for values in sessions.values():
        unique_packets = sorted(set(values))
        previous_sequence: int | None = None
        previous_monotonic: int | None = None
        for sequence, monotonic_ns in unique_packets:
            if previous_sequence is not None and sequence <= previous_sequence:
                counters["post_floor_packet_sequence_failures"] += 1
            if previous_monotonic is not None and monotonic_ns < previous_monotonic:
                counters["post_floor_monotonic_receipt_failures"] += 1
            previous_sequence = sequence
            previous_monotonic = monotonic_ns

    return {
        "files": len(files),
        **dict(counters),
        "unique_event_identities": len(seen),
        "collector_sessions": len(sessions),
        "unique_packets": len(packet_receipts),
        "by_symbol": dict(sorted(by_symbol.items())),
        "by_side": dict(sorted(by_side.items())),
        "first_liquidation_time": iso(first_liquidation_ms),
        "last_liquidation_time": iso(last_liquidation_ms),
        "raw_receipt_lag_ms": metric(raw_receipt_lags_ms),
        "corrected_receipt_lag_ms": metric(corrected_receipt_lags_ms),
        "corrected_event_lag_ms": metric(corrected_event_lags_ms),
        "exchange_event_lag_ms": metric(exchange_event_lags_ms),
        "clock_offset_ms": metric(clock_offsets_ms),
        "clock_rtt_ms": metric(clock_rtts_ms),
        "calibration_age_s": metric(calibration_ages_s),
        "error_sample": error_sample,
        "gate_scope": {
            "event_checks": "liquidation_time_gte_forward_floor",
            "unassignable_json_errors": "partition_date_gte_forward_floor_date",
            "pre_floor_and_schema_v2_rows_are_diagnostic_only": True,
            "outcome_fields_computed": False,
        },
    }


def build_quality(contract: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate = contract["candidate"]
    sources = contract["sources"]
    gate = contract["input_quality_gate"]
    symbols = [str(item).upper() for item in candidate["symbols"]]
    forward_start = str(contract.get("forward_start_at") or contract.get("forward_floor_at") or "")
    events = scan_events(base.resolve_path(sources["liquidations"]), symbols, forward_start, gate)
    bars, _ = v3.scan_bars(
        base.resolve_path(sources["bars_root"]),
        symbols,
        str(candidate["interval"]),
        now=observed_at,
    )
    counter_gates = {
        "post_floor_json_parse_errors": "maximum_json_parse_errors",
        "post_floor_schema_errors": "maximum_schema_errors",
        "post_floor_duplicate_event_identities": "maximum_duplicate_event_identities",
        "post_floor_partition_mismatches": "maximum_partition_mismatches",
        "post_floor_nonmonotonic_files": "maximum_nonmonotonic_files",
        "post_floor_receipt_parse_errors": "maximum_receipt_parse_errors",
        "post_floor_correction_equation_failures": "maximum_correction_equation_failures",
        "post_floor_calibration_age_failures": "maximum_calibration_age_failures",
        "post_floor_calibration_rtt_failures": "maximum_calibration_rtt_failures",
        "post_floor_calibration_uncertainty_failures": "maximum_calibration_uncertainty_failures",
        "post_floor_clock_offset_failures": "maximum_clock_offset_failures",
        "post_floor_calibration_sample_failures": "maximum_calibration_sample_failures",
        "post_floor_calibration_source_failures": "maximum_calibration_source_failures",
        "post_floor_exchange_event_lag_failures": "maximum_exchange_event_lag_failures",
        "post_floor_corrected_before_event_failures": "maximum_corrected_before_event_failures",
        "post_floor_corrected_receipt_lag_failures": "maximum_corrected_receipt_lag_failures",
        "post_floor_packet_receipt_conflicts": "maximum_packet_receipt_conflicts",
        "post_floor_packet_sequence_failures": "maximum_packet_sequence_failures",
        "post_floor_monotonic_receipt_failures": "maximum_monotonic_receipt_failures",
    }
    checks = {
        name: int(events.get(name) or 0) <= int(gate[limit]) for name, limit in counter_gates.items()
    }
    checks["required_bar_symbols"] = bars["bar_symbols_present"] >= int(gate["required_bar_symbols"])
    checks["closed_bar_freshness"] = (
        bars["maximum_closed_bar_lag_intervals"] is not None
        and int(bars["maximum_closed_bar_lag_intervals"]) <= int(gate["maximum_closed_bar_lag_intervals"])
    )
    hard_failures = [name for name, passed in checks.items() if not passed]
    return {
        "generated_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": TOOL_PATH,
        "decision": "bybit_canonical_v4_input_quality_pass" if not hard_failures else "bybit_canonical_v4_input_quality_blocked",
        "forward_floor_at": forward_start,
        "events": events,
        "bars": bars,
        "checks": checks,
        "hard_failures": hard_failures,
        "boundary": {
            "input_quality_only": True,
            "outcome_fields_computed": False,
            "return_metrics_visible": False,
            "uses_public_market_data_only": True,
            "uses_private_credentials": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Bybit Canonical Input Quality V4",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Forward floor: `{report['forward_floor_at']}`",
            f"- Post-floor events: `{report['events'].get('post_floor_events', 0)}`",
            f"- Post-floor schema-valid events: `{report['events'].get('post_floor_schema_valid_events', 0)}`",
            f"- Collector sessions: `{report['events'].get('collector_sessions', 0)}`",
            f"- Hard failures: `{', '.join(report['hard_failures']) or 'none'}`",
            "- Raw wall-clock lag is diagnostic; calibrated and monotonic receipts are the hard evidence.",
            "- Outcome fields computed: `false`",
            "- Can trade: `false`",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Outcome-blind calibrated-receipt gate for canonical Bybit V4")
    parser.add_argument("--prereg", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V4_2026-07-14.json")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_INPUT_QUALITY_V4_2026-07-14")
    args = parser.parse_args()
    contract = json.loads(base.resolve_path(args.prereg).read_text(encoding="utf-8-sig"))
    report = build_quality(contract)
    out = base.resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "hard_failures": report["hard_failures"], "can_trade": False}, indent=2))
    return 1 if report["hard_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
