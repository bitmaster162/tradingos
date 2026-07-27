#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_context_intake as base
from tools import bybit_liquidation_canonical_forward_observer_v4 as observer
from tools import bybit_liquidation_canonical_input_quality_v4 as quality


TOOL_PATH = "tools/bybit_liquidation_canonical_v4_commissioning_audit.py"


def now_iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def millis_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def schema3_window(data_dir: Path, symbols: list[str]) -> dict[str, Any]:
    allowed = {item.upper() for item in symbols}
    first_ms: int | None = None
    last_ms: int | None = None
    rows = 0
    sessions: set[str] = set()
    packets: set[tuple[str, int]] = set()
    by_symbol: Counter[str] = Counter()
    for path in sorted(data_dir.rglob("*.jsonl")) if data_dir.exists() else []:
        try:
            handle = path.open("r", encoding="utf-8-sig")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or row.get("ingest_schema_version") != 3:
                    continue
                symbol = str(row.get("symbol") or "").upper()
                if symbol not in allowed:
                    continue
                try:
                    liquidation_ms = int(row.get("liquidation_time_ms"))
                    sequence = int(row.get("packet_sequence"))
                except (TypeError, ValueError):
                    continue
                session = str(row.get("collector_session_id") or "")
                rows += 1
                by_symbol[symbol] += 1
                first_ms = liquidation_ms if first_ms is None else min(first_ms, liquidation_ms)
                last_ms = liquidation_ms if last_ms is None else max(last_ms, liquidation_ms)
                if session:
                    sessions.add(session)
                    packets.add((session, sequence))
    return {
        "schema3_rows": rows,
        "first_liquidation_time": millis_iso(first_ms) if first_ms is not None else None,
        "last_liquidation_time": millis_iso(last_ms) if last_ms is not None else None,
        "first_liquidation_ms": first_ms,
        "last_liquidation_ms": last_ms,
        "collector_sessions": len(sessions),
        "unique_packets": len(packets),
        "by_symbol": dict(sorted(by_symbol.items())),
    }


QualityBuilder = Callable[..., dict[str, Any]]


def build_report(
    lock: dict[str, Any],
    data_dir: Path,
    *,
    observed_at: datetime | None = None,
    lock_failures: list[str] | None = None,
    quality_builder: QualityBuilder = quality.build_quality,
) -> dict[str, Any]:
    observed = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    actual_floor = base.parse_ts(lock.get("forward_start_at"))
    symbols = [str(item).upper() for item in (lock.get("candidate") or {}).get("symbols") or []]
    window = schema3_window(data_dir, symbols)
    failures = list(lock_failures or [])
    quality_snapshot: dict[str, Any] | None = None

    if actual_floor is None:
        failures.append("invalid_actual_forward_floor")
    if observed >= actual_floor if actual_floor is not None else False:
        decision = "bybit_canonical_v4_commissioning_window_closed"
        failures.append("commissioning_must_run_before_actual_forward_floor")
    elif failures:
        decision = "bybit_canonical_v4_commissioning_blocked_lock_integrity"
    elif not window["schema3_rows"]:
        decision = "bybit_canonical_v4_commissioning_waiting_schema3_events"
    elif base.parse_ts(window["last_liquidation_time"]) >= actual_floor:
        decision = "bybit_canonical_v4_commissioning_blocked_post_floor_event_present"
        failures.append("commissioning_window_not_strictly_pre_floor")
    else:
        diagnostic_contract = copy.deepcopy(lock)
        diagnostic_contract["forward_start_at"] = window["first_liquidation_time"]
        diagnostic_contract["sources"]["liquidations"] = str(data_dir.resolve())
        quality_snapshot = quality_builder(diagnostic_contract, now=observed)
        failures.extend(f"quality:{item}" for item in quality_snapshot.get("hard_failures") or [])
        boundary = quality_snapshot.get("boundary") if isinstance(quality_snapshot.get("boundary"), dict) else {}
        if boundary.get("outcome_fields_computed") is not False:
            failures.append("quality_boundary_outcome_fields")
        events = quality_snapshot.get("events") if isinstance(quality_snapshot.get("events"), dict) else {}
        if int(events.get("post_floor_events") or 0) != int(events.get("post_floor_schema_valid_events") or 0):
            failures.append("commissioning_schema_validity_gap")
        decision = (
            "bybit_canonical_v4_commissioning_pass"
            if not failures
            else "bybit_canonical_v4_commissioning_blocked_input_quality"
        )

    return {
        "schema_version": 1,
        "generated_at": now_iso(observed),
        "tool": TOOL_PATH,
        "decision": decision,
        "actual_forward_floor_at": lock.get("forward_start_at"),
        "commissioning_window": window,
        "lock_failures": list(lock_failures or []),
        "hard_failures": sorted(set(failures)),
        "quality_snapshot": quality_snapshot,
        "interpretation": (
            "Operational receipt evidence only. Commissioning rows are pre-floor diagnostics and are never admitted "
            "to the V4 strategy sample or outcome review."
        ),
        "runtime_boundary": {
            "pre_floor_commissioning_only": True,
            "sample_admission_allowed": False,
            "outcome_fields_computed": False,
            "return_metrics_visible": False,
            "strategy_parameters_mutable": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    window = report["commissioning_window"]
    quality_events = ((report.get("quality_snapshot") or {}).get("events") or {})
    return "\n".join(
        [
            "# Bybit Canonical V4 Pre-Floor Commissioning Audit",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Actual forward floor: `{report['actual_forward_floor_at']}`",
            f"- Schema-3 rows: `{window['schema3_rows']}`",
            f"- Collector sessions: `{window['collector_sessions']}`",
            f"- Unique packets: `{window['unique_packets']}`",
            f"- Commissioning range: `{window['first_liquidation_time']}` to `{window['last_liquidation_time']}`",
            f"- Quality hard failures: `{report['hard_failures']}`",
            f"- Corrected receipt lag ms: `{quality_events.get('corrected_receipt_lag_ms')}`",
            f"- Clock RTT ms: `{quality_events.get('clock_rtt_ms')}`",
            f"- Calibration age s: `{quality_events.get('calibration_age_s')}`",
            "- Outcome fields computed: `false`",
            "- Sample admission allowed: `false`",
            "- Can trade: `false`",
            "",
            "These rows are commissioning diagnostics only and are excluded from the prospective V4 sample.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Outcome-blind pre-floor commissioning audit for Bybit V4 receipts")
    parser.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V4_2026-07-14.json")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_V4_COMMISSIONING_2026-07-14")
    args = parser.parse_args()

    lock_path = base.resolve_path(args.lock)
    lock = read_json(lock_path)
    data_dir = base.resolve_path(str((lock.get("sources") or {}).get("liquidations") or ""))
    report = build_report(lock, data_dir, lock_failures=observer.validate_lock(lock))
    out = base.resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "schema3_rows": report["commissioning_window"]["schema3_rows"],
                "sessions": report["commissioning_window"]["collector_sessions"],
                "hard_failures": report["hard_failures"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] in {
        "bybit_canonical_v4_commissioning_pass",
        "bybit_canonical_v4_commissioning_waiting_schema3_events",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
