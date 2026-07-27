#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import binance_rest_kline_tail_gap_filler as filler
from tools import bybit_all_liquidation_real_feed_collector as collector_v1
from tools import bybit_all_liquidation_real_feed_collector_v2 as collector
from tools import bybit_liquidation_canonical_forward_observer as core
from tools import bybit_liquidation_canonical_input_quality as quality_v3
from tools import bybit_liquidation_canonical_input_quality_v4 as quality_v4
from tools import bybit_liquidation_canonical_input_quality_v5 as quality
from tools import liquidity_sweep_detector


OBSERVER_PATH = Path(__file__).resolve()
DEPENDENCY_PATHS = list(
    dict.fromkeys(
        [
            Path(core.__file__).resolve(),
            *[Path(path).resolve() for path in core.DEPENDENCY_PATHS],
            Path(quality.__file__).resolve(),
            Path(quality_v4.__file__).resolve(),
            Path(quality_v3.__file__).resolve(),
            Path(collector.__file__).resolve(),
            Path(collector_v1.__file__).resolve(),
            Path(liquidity_sweep_detector.__file__).resolve(),
            Path(filler.__file__).resolve(),
        ]
    )
)


PACKET_GATE_FIELDS = {
    "maximum_packet_item_contract_errors",
    "maximum_duplicate_packet_item_identities",
    "maximum_packet_item_bounds_failures",
    "maximum_packet_item_count_conflicts",
    "maximum_packet_item_coverage_failures",
    "maximum_packet_item_payload_mismatches",
}


def validate_prereg(prereg: dict[str, Any]) -> list[str]:
    failures = core.validate_prereg(prereg)
    if int(prereg.get("schema_version") or 0) != 4:
        failures.append("schema_version")
    supersedes = prereg.get("supersedes") if isinstance(prereg.get("supersedes"), dict) else {}
    for key in (
        "v1_observations_admitted",
        "v2_observations_admitted",
        "v2_terminal_metrics_admitted",
        "v3_observations_admitted",
        "v3_terminal_metrics_admitted",
        "v4_observations_admitted",
        "v4_terminal_metrics_admitted",
    ):
        if supersedes.get(key) is not False:
            failures.append(f"supersedes.{key}")
    if supersedes.get("strategy_parameters_changed") is not False:
        failures.append("supersedes.strategy_parameters_changed")
    bars = prereg.get("bar_contract") if isinstance(prereg.get("bar_contract"), dict) else {}
    if bars.get("fully_closed_bars_only") is not True or bars.get("current_interval_bar_allowed") is not False:
        failures.append("bar_contract")
    receipt = prereg.get("receipt_contract") if isinstance(prereg.get("receipt_contract"), dict) else {}
    if int(receipt.get("required_ingest_schema_version") or 0) != 4:
        failures.append("receipt_contract.required_ingest_schema_version")
    if receipt.get("packet_item_identity") != "collector_session_id+packet_sequence+packet_item_index":
        failures.append("receipt_contract.packet_item_identity")
    if receipt.get("packet_atomic_write_required") is not True:
        failures.append("receipt_contract.packet_atomic_write_required")
    if receipt.get("market_tuple_uniqueness_required") is not False:
        failures.append("receipt_contract.market_tuple_uniqueness_required")
    gate = prereg.get("input_quality_gate") if isinstance(prereg.get("input_quality_gate"), dict) else {}
    if int(gate.get("required_ingest_schema_version") or 0) != 4 or not PACKET_GATE_FIELDS.issubset(gate):
        failures.append("input_quality_gate")
    research = prereg.get("research_boundary") if isinstance(prereg.get("research_boundary"), dict) else {}
    for key in (
        "v2_observations_admitted",
        "v2_terminal_metrics_admitted",
        "v3_observations_admitted",
        "v3_terminal_metrics_admitted",
        "v4_observations_admitted",
        "v4_terminal_metrics_admitted",
    ):
        if research.get(key) is not False:
            failures.append(f"research_boundary.{key}")
    if prereg.get("candidate", {}).get("exit_model") != "event_bar_plus_8_fully_closed_close":
        failures.append("candidate.exit_model")
    tombstone = core.base.resolve_path(str(prereg.get("data_quality_tombstone") or ""))
    if not tombstone.is_file():
        failures.append("data_quality_tombstone")
    return sorted(set(failures))


def build_lock(prereg_path: Path, *, created_at: str | None = None) -> dict[str, Any]:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8-sig"))
    failures = validate_prereg(prereg)
    if failures:
        raise ValueError("invalid V5 preregistration: " + ",".join(failures))
    created = created_at or core.base.now_iso()
    created_dt = core.base.parse_ts(created)
    floor_dt = core.base.parse_ts(prereg["forward_floor_at"])
    if created_dt is None or floor_dt is None or created_dt >= floor_dt:
        raise ValueError("lock must be sealed before forward_floor_at")
    discovery_path = core.base.resolve_path(prereg["discovery_provenance"]["report"])
    tombstone_path = core.base.resolve_path(prereg["data_quality_tombstone"])
    if not discovery_path.is_file() or not tombstone_path.is_file():
        raise ValueError("discovery or tombstone evidence is missing")
    return {
        "schema_version": 4,
        "lock_id": prereg["prereg_id"],
        "status": "prospective_forward_lock_before_outcome_review",
        "created_at": created,
        "forward_start_at": prereg["forward_floor_at"],
        "preregistration": {"path": core.base.portable(prereg_path), "sha256": core.sha256_file(prereg_path)},
        "observer": {"path": core.base.portable(OBSERVER_PATH), "sha256": core.sha256_file(OBSERVER_PATH)},
        "dependencies": [
            {"path": core.base.portable(path), "sha256": core.sha256_file(path)} for path in DEPENDENCY_PATHS
        ],
        "discovery_evidence": {"path": core.base.portable(discovery_path), "sha256": core.sha256_file(discovery_path)},
        "data_quality_tombstone": {"path": core.base.portable(tombstone_path), "sha256": core.sha256_file(tombstone_path)},
        "supersedes": prereg["supersedes"],
        "sources": prereg["sources"],
        "candidate": prereg["candidate"],
        "bar_contract": prereg["bar_contract"],
        "receipt_contract": prereg["receipt_contract"],
        "input_quality_gate": prereg["input_quality_gate"],
        "sample_gate": prereg["sample_gate"],
        "terminal_outcome_gate": prereg["terminal_outcome_gate"],
        "side_contract": prereg["side_contract"],
        "research_boundary": prereg["research_boundary"],
        "runtime_boundary": prereg["runtime_boundary"],
        "orders_allowed": False,
        "can_trade": False,
    }


def validate_lock(lock: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if int(lock.get("schema_version") or 0) != 4:
        failures.append("schema_version")
    if lock.get("status") != "prospective_forward_lock_before_outcome_review":
        failures.append("status")
    if core.base.parse_ts(lock.get("forward_start_at")) is None:
        failures.append("forward_start_at")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    boundary = lock.get("runtime_boundary") if isinstance(lock.get("runtime_boundary"), dict) else {}
    for key in (
        "signals_allowed",
        "paper_entries_allowed",
        "live_entries_allowed",
        "orders_allowed",
        "uses_private_credentials",
        "can_trade",
    ):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary.{key}")
    receipt = lock.get("receipt_contract") if isinstance(lock.get("receipt_contract"), dict) else {}
    if int(receipt.get("required_ingest_schema_version") or 0) != 4:
        failures.append("receipt_contract.required_ingest_schema_version")
    if receipt.get("packet_item_identity") != "collector_session_id+packet_sequence+packet_item_index":
        failures.append("receipt_contract.packet_item_identity")
    research = lock.get("research_boundary") if isinstance(lock.get("research_boundary"), dict) else {}
    for key in (
        "v2_observations_admitted",
        "v2_terminal_metrics_admitted",
        "v3_observations_admitted",
        "v3_terminal_metrics_admitted",
        "v4_observations_admitted",
        "v4_terminal_metrics_admitted",
    ):
        if research.get(key) is not False:
            failures.append(f"research_boundary.{key}")
    items: list[tuple[str, dict[str, Any]]] = []
    for section in ("preregistration", "observer", "discovery_evidence", "data_quality_tombstone"):
        item = lock.get(section) if isinstance(lock.get(section), dict) else {}
        items.append((section, item))
    dependencies = lock.get("dependencies") if isinstance(lock.get("dependencies"), list) else []
    if len(dependencies) != len(DEPENDENCY_PATHS):
        failures.append("dependencies")
    items.extend((f"dependency_{index}", item) for index, item in enumerate(dependencies) if isinstance(item, dict))
    for name, item in items:
        path = core.base.resolve_path(str(item.get("path") or ""))
        expected = str(item.get("sha256") or "")
        if not path.is_file() or not expected or core.sha256_file(path) != expected:
            failures.append(f"{name}_integrity")
    return sorted(set(failures))


def resolvable_metadata(
    rows: list[dict[str, Any]],
    closed_by_symbol: dict[str, list[Any]],
    horizon_bars: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    indexes = {symbol: core.study.build_bar_index(bars) for symbol, bars in closed_by_symbol.items()}
    metadata: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        symbol = str(row["symbol"])
        bars = closed_by_symbol.get(symbol, [])
        index = indexes.get(symbol, {}).get(row["bar_ts"])
        if index is None:
            if len(errors) < 25:
                errors.append(f"missing_bar:{symbol}:{row['bar_ts']}")
            continue
        if index + 1 >= len(bars) or index + horizon_bars >= len(bars):
            continue
        metadata.append(
            {
                "symbol": symbol,
                "bar_ts": row["bar_ts"],
                "independent_4h_block": core.study.independent_4h_block_id(row["bar_ts"]),
            }
        )
    return metadata, errors


def load_forward_progress(
    lock: dict[str, Any],
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, list[Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    candidate = lock["candidate"]
    symbols = [str(item).upper() for item in candidate["symbols"]]
    quality_report = quality.build_quality(lock, now=now)
    args = argparse.Namespace(
        data_dir=lock["sources"]["liquidations"],
        symbol=symbols[0],
        symbols=",".join(symbols),
        interval=candidate["interval"],
        bars_csv="",
        min_events_for_research=0,
        min_event_bars_for_research=0,
        max_bad_lines=25,
    )
    intake_report = core.intake.build_report(args)
    aggregate_rows = intake_report.pop("_aggregate_rows", [])
    start_bar = core.forward_start_bar(lock["forward_start_at"], candidate["interval"])
    eligible_rows = [
        row
        for row in aggregate_rows
        if row["bar_ts"] >= start_bar
        and row["matched_price_bar"] is True
        and row["dominant_context"] == candidate["context"]
        and int(row["side_semantics_version"]) == core.semantics.CANONICAL_SIDE_SCHEMA_VERSION
    ]
    bars_by_symbol, bar_paths = core.study.load_bars_by_symbol(
        symbols,
        candidate["interval"],
        core.base.resolve_path(lock["sources"]["bars_root"]),
    )
    closed_by_symbol: dict[str, list[Any]] = {}
    excluded_open_rows: dict[str, int] = {}
    for symbol, bars in bars_by_symbol.items():
        closed, excluded = quality_v3.filter_fully_closed_bars(bars, now=now, interval=candidate["interval"])
        closed_by_symbol[symbol] = closed
        excluded_open_rows[symbol] = excluded
    metadata, errors = resolvable_metadata(eligible_rows, closed_by_symbol, int(candidate["horizon_bars"]))
    return eligible_rows, closed_by_symbol, metadata, {
        "forward_start_bar_ts": start_bar,
        "closed_cutoff_bar_open": quality_report["bars"]["closed_cutoff_bar_open"],
        "raw_events": intake_report["summary"]["events"],
        "post_floor_raw_events": quality_report["events"].get("post_floor_events", 0),
        "post_floor_schema_valid_events": quality_report["events"].get("post_floor_schema_valid_events", 0),
        "post_floor_packet_rows": quality_report["events"].get("post_floor_packet_rows", 0),
        "post_floor_packets": quality_report["events"].get("post_floor_packets", 0),
        "market_tuple_collisions_diagnostic": quality_report["events"].get("market_tuple_collisions", 0),
        "aggregate_rows": intake_report["summary"]["aggregate_rows"],
        "eligible_event_bars": len(eligible_rows),
        "resolvable_without_outcome_computation": len(metadata),
        "bars_by_symbol": bar_paths,
        "open_or_invalid_bar_rows_excluded": excluded_open_rows,
        "errors": errors,
    }, quality_report


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Bybit Canonical Liquidation Forward Observer V5",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Forward floor: `{report['lock']['forward_start_at']}`",
            f"- Resolvable events: `{report['sample']['resolved_events']}`",
            f"- Input quality: `{report['input_quality']['decision']}`",
            f"- Outcome fields computed: `{report['outcome_review']['outcome_fields_computed']}`",
            f"- Terminal: `{report['terminal']['reached']}`",
            "- V1/V2/V3/V4 observations admitted: `false`",
            "- Can trade: `false`",
            "",
            "## Blockers",
            "",
            *[f"- `{item}`" for item in report["blockers"]],
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )


def run_observer(
    lock_path: Path,
    out_prefix: Path,
    terminal_receipt_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    failures = validate_lock(lock)
    if failures:
        raise ValueError("invalid V5 forward lock: " + ",".join(failures))
    if terminal_receipt_path.is_file():
        existing = json.loads(terminal_receipt_path.read_text(encoding="utf-8-sig"))
        if existing.get("lock_id") == lock.get("lock_id") and existing.get("terminal") is True:
            frozen = dict(existing["report"])
            frozen["generated_at"] = observed_at.isoformat(timespec="seconds").replace("+00:00", "Z")
            frozen["terminal"]["frozen"] = True
            core.write_json(out_prefix.with_suffix(".json"), frozen)
            out_prefix.with_suffix(".md").write_text(render_markdown(frozen), encoding="utf-8")
            return frozen

    eligible_rows, closed_by_symbol, metadata, source_progress, quality_report = load_forward_progress(lock, now=observed_at)
    sample = core.sample_summary(metadata)
    sample_blockers = core.sample_blockers(sample, lock["sample_gate"])
    quality_blockers = [f"input_quality:{item}" for item in quality_report["hard_failures"]]
    floor = core.base.parse_ts(lock["forward_start_at"])
    assert floor is not None
    terminal_metrics: dict[str, Any] | None = None
    outcome_fields_computed = False
    if observed_at < floor:
        decision = "bybit_liquidation_canonical_v5_waiting_floor"
        blockers = ["forward_floor_not_reached", *quality_blockers]
    elif quality_blockers:
        decision = "bybit_liquidation_canonical_v5_blocked_input_quality"
        blockers = quality_blockers
    elif sample_blockers:
        decision = "bybit_liquidation_canonical_v5_collecting_outcome_blind_sample"
        blockers = sample_blockers
    else:
        records, record_errors = core.study.build_event_records(
            eligible_rows,
            closed_by_symbol,
            [int(lock["candidate"]["horizon_bars"])],
        )
        outcome_fields_computed = True
        if record_errors or len(records) != len(metadata):
            decision = "bybit_liquidation_canonical_v5_blocked_terminal_record_construction"
            blockers = ["terminal_record_construction_mismatch", *record_errors[:25]]
        else:
            base_decision, terminal_metrics, outcome_failures = core.terminal_evaluation(
                records,
                lock["candidate"],
                lock["terminal_outcome_gate"],
            )
            decision = (
                "bybit_liquidation_canonical_v5_candidate_accepted_manual_shadow_only"
                if base_decision.endswith("accepted_manual_shadow_only")
                else "bybit_liquidation_canonical_v5_no_edge_tombstone"
            )
            blockers = outcome_failures
    terminal = decision in {
        "bybit_liquidation_canonical_v5_candidate_accepted_manual_shadow_only",
        "bybit_liquidation_canonical_v5_no_edge_tombstone",
    }
    next_action = (
        "keep schema-v4 collection and V5 observer unchanged; do not compute or inspect interim returns"
        if not terminal
        else (
            "manual shadow-design review is allowed; trading remains disabled"
            if decision.endswith("manual_shadow_only")
            else "tombstone this formulation without reverse selection or retuning"
        )
    )
    report = {
        "generated_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": "tools/bybit_liquidation_canonical_forward_observer_v5.py",
        "decision": decision,
        "lock": {
            "path": core.base.portable(lock_path),
            "sha256": core.sha256_file(lock_path),
            "lock_id": lock["lock_id"],
            "forward_start_at": lock["forward_start_at"],
        },
        "supersedes": lock["supersedes"],
        "candidate": lock["candidate"],
        "bar_contract": lock["bar_contract"],
        "receipt_contract": lock["receipt_contract"],
        "source_progress": source_progress,
        "input_quality": quality_report,
        "sample": sample,
        "outcome_review": {
            "interim_outcomes_hidden": not terminal,
            "outcome_fields_computed": outcome_fields_computed,
            "terminal_metrics": terminal_metrics,
        },
        "terminal": {"reached": terminal, "frozen": False, "receipt": core.base.portable(terminal_receipt_path)},
        "blockers": sorted(set(blockers)),
        "next_action": next_action,
        "runtime_boundary": {
            "observer_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "orders_allowed": False,
        "can_trade": False,
    }
    core.write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    if terminal:
        core.write_json(
            terminal_receipt_path,
            {"lock_id": lock["lock_id"], "terminal": True, "sealed_at": core.base.now_iso(), "report": report},
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Packet-ordinal outcome-blind Bybit liquidation observer V5")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser("seal-lock")
    seal.add_argument("--prereg", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V5_2026-07-15.json")
    seal.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V5_2026-07-15.json")
    seal.add_argument("--acknowledge-forward-only", action="store_true")
    run = subparsers.add_parser("run-once")
    run.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V5_2026-07-15.json")
    run.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V5_2026-07-15")
    run.add_argument("--terminal-receipt", default="logs/bybit_liquidation_canonical_forward_v5/terminal_receipt.json")
    args = parser.parse_args()
    try:
        if args.command == "seal-lock":
            if not args.acknowledge_forward_only:
                raise ValueError("--acknowledge-forward-only is required")
            prereg_path = core.base.resolve_path(args.prereg)
            lock_path = core.base.resolve_path(args.lock)
            lock = build_lock(prereg_path)
            core.write_json(lock_path, lock)
            print(json.dumps({"decision": "bybit_canonical_v5_forward_lock_sealed", "lock": core.base.portable(lock_path), "can_trade": False}, indent=2))
            return 0
        report = run_observer(
            core.base.resolve_path(args.lock),
            core.base.resolve_path(args.out_prefix),
            core.base.resolve_path(args.terminal_receipt),
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"decision": "bybit_canonical_v5_observer_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "resolved_events": report["sample"]["resolved_events"],
                "input_quality": report["input_quality"]["decision"],
                "outcome_fields_computed": report["outcome_review"]["outcome_fields_computed"],
                "terminal": report["terminal"]["reached"],
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
