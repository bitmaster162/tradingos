#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_context_intake as base
from tools import bybit_liquidation_canonical_input_quality as v3
from tools import bybit_liquidation_canonical_input_quality_v4 as v4


TOOL_PATH = "tools/bybit_liquidation_canonical_input_quality_v5.py"
PACKET_FIELDS = {"packet_item_index", "packet_item_count"}


def integer(row: dict[str, Any], field: str) -> int | None:
    try:
        return int(row.get(field))
    except (TypeError, ValueError):
        return None


def decimal_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def packet_contract_scan(
    data_dir: Path,
    symbols: list[str],
    forward_floor_at: str,
    required_schema: int,
) -> dict[str, Any]:
    symbol_set = {item.upper() for item in symbols}
    floor = base.parse_ts(forward_floor_at)
    if floor is None:
        raise ValueError("invalid forward floor")
    counters: Counter[str] = Counter()
    seen_items: set[tuple[str, int, int]] = set()
    packets: dict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {"expected_counts": set(), "ordinals": [], "rows": 0}
    )
    issue_sample: list[dict[str, Any]] = []

    for path in sorted(data_dir.rglob("*.jsonl")) if data_dir.exists() else []:
        try:
            handle = path.open("r", encoding="utf-8-sig")
        except OSError:
            continue
        with handle:
            for line_no, line in enumerate(handle, start=1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or str(row.get("symbol") or "").upper() not in symbol_set:
                    continue
                liquidation_dt = base.ms_to_dt(row.get("liquidation_time_ms"))
                if liquidation_dt is None or liquidation_dt < floor:
                    continue
                if integer(row, "ingest_schema_version") != required_schema:
                    continue
                counters["post_floor_packet_rows"] += 1
                missing = sorted(field for field in PACKET_FIELDS if row.get(field) in (None, ""))
                session_id = str(row.get("collector_session_id") or "")
                sequence = integer(row, "packet_sequence")
                item_index = integer(row, "packet_item_index")
                item_count = integer(row, "packet_item_count")
                if missing or not session_id or sequence is None or item_index is None or item_count is None:
                    counters["post_floor_packet_item_contract_errors"] += 1
                    if len(issue_sample) < 20:
                        issue_sample.append({"path": base.portable(path), "line": line_no, "error": "packet_fields"})
                    continue
                if item_count < 1 or item_index < 0 or item_index >= item_count:
                    counters["post_floor_packet_item_bounds_failures"] += 1
                    if len(issue_sample) < 20:
                        issue_sample.append({"path": base.portable(path), "line": line_no, "error": "packet_bounds"})
                    continue

                item_identity = (session_id, sequence, item_index)
                if item_identity in seen_items:
                    counters["post_floor_duplicate_packet_item_identities"] += 1
                    if len(issue_sample) < 20:
                        issue_sample.append(
                            {"path": base.portable(path), "line": line_no, "error": "duplicate_packet_item_identity"}
                        )
                else:
                    seen_items.add(item_identity)
                packet = packets[(session_id, sequence)]
                packet["expected_counts"].add(item_count)
                packet["ordinals"].append(item_index)
                packet["rows"] += 1

                raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
                data = raw.get("data") if isinstance(raw.get("data"), list) else []
                payload_ok = len(data) == item_count and item_index < len(data) and isinstance(data[item_index], dict)
                if payload_ok:
                    item = data[item_index]
                    payload_ok = (
                        str(item.get("s") or "").upper() == str(row.get("symbol") or "").upper()
                        and str(item.get("S") or "").upper() == str(row.get("side") or "").upper()
                        and integer({"value": item.get("T")}, "value") == integer(row, "liquidation_time_ms")
                        and decimal_equal(item.get("p"), row.get("price"))
                        and decimal_equal(item.get("v"), row.get("quantity"))
                    )
                if not payload_ok:
                    counters["post_floor_packet_item_payload_mismatches"] += 1
                    if len(issue_sample) < 20:
                        issue_sample.append({"path": base.portable(path), "line": line_no, "error": "payload_ordinal"})

    for (session_id, sequence), packet in packets.items():
        expected_counts = packet["expected_counts"]
        if len(expected_counts) != 1:
            counters["post_floor_packet_item_count_conflicts"] += 1
            continue
        expected = next(iter(expected_counts))
        ordinals = packet["ordinals"]
        if len(ordinals) != expected or set(ordinals) != set(range(expected)):
            counters["post_floor_packet_item_coverage_failures"] += 1
            if len(issue_sample) < 20:
                issue_sample.append(
                    {
                        "collector_session_id": session_id,
                        "packet_sequence": sequence,
                        "error": "packet_coverage",
                        "expected": expected,
                        "observed": sorted(ordinals),
                    }
                )

    return {
        **dict(counters),
        "post_floor_unique_packet_item_identities": len(seen_items),
        "post_floor_packets": len(packets),
        "packet_identity": "collector_session_id+packet_sequence+packet_item_index",
        "market_tuple_uniqueness_required": False,
        "issue_sample": issue_sample,
    }


def build_quality(contract: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate = contract["candidate"]
    sources = contract["sources"]
    gate = contract["input_quality_gate"]
    symbols = [str(item).upper() for item in candidate["symbols"]]
    forward_start = str(contract.get("forward_start_at") or contract.get("forward_floor_at") or "")
    data_dir = base.resolve_path(sources["liquidations"])
    events = v4.scan_events(data_dir, symbols, forward_start, gate)
    events["market_tuple_collisions"] = events.get("post_floor_duplicate_event_identities", 0)
    events["market_tuple_collision_policy"] = "diagnostic_only_bybit_has_no_unique_liquidation_id"
    events.update(packet_contract_scan(data_dir, symbols, forward_start, int(gate["required_ingest_schema_version"])))
    bars, _ = v3.scan_bars(
        base.resolve_path(sources["bars_root"]),
        symbols,
        str(candidate["interval"]),
        now=observed_at,
    )
    counter_gates = {
        "post_floor_json_parse_errors": "maximum_json_parse_errors",
        "post_floor_schema_errors": "maximum_schema_errors",
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
        "post_floor_packet_item_contract_errors": "maximum_packet_item_contract_errors",
        "post_floor_duplicate_packet_item_identities": "maximum_duplicate_packet_item_identities",
        "post_floor_packet_item_bounds_failures": "maximum_packet_item_bounds_failures",
        "post_floor_packet_item_count_conflicts": "maximum_packet_item_count_conflicts",
        "post_floor_packet_item_coverage_failures": "maximum_packet_item_coverage_failures",
        "post_floor_packet_item_payload_mismatches": "maximum_packet_item_payload_mismatches",
    }
    checks = {name: int(events.get(name) or 0) <= int(gate[limit]) for name, limit in counter_gates.items()}
    checks["required_bar_symbols"] = bars["bar_symbols_present"] >= int(gate["required_bar_symbols"])
    checks["closed_bar_freshness"] = (
        bars["maximum_closed_bar_lag_intervals"] is not None
        and int(bars["maximum_closed_bar_lag_intervals"]) <= int(gate["maximum_closed_bar_lag_intervals"])
    )
    hard_failures = [name for name, passed in checks.items() if not passed]
    return {
        "generated_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": TOOL_PATH,
        "decision": "bybit_canonical_v5_input_quality_pass" if not hard_failures else "bybit_canonical_v5_input_quality_blocked",
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
            "# Bybit Canonical Input Quality V5",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Forward floor: `{report['forward_floor_at']}`",
            f"- Schema-v4 post-floor rows: `{report['events'].get('post_floor_packet_rows', 0)}`",
            f"- Complete packets: `{report['events'].get('post_floor_packets', 0)}`",
            f"- Market tuple collisions (diagnostic): `{report['events'].get('market_tuple_collisions', 0)}`",
            f"- Hard failures: `{', '.join(report['hard_failures']) or 'none'}`",
            "- Physical identity: `collector_session_id + packet_sequence + packet_item_index`",
            "- Outcome fields computed: `false`",
            "- Can trade: `false`",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Packet-ordinal input gate for canonical Bybit V5")
    parser.add_argument("--prereg", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V5_2026-07-15.json")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_INPUT_QUALITY_V5_2026-07-15")
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
