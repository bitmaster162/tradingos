#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator  # noqa: E402
from tools import bitunix_wo105_liquidation_context as liquidation  # noqa: E402
from tools import bitunix_wo105_packet_assembler as v2_assembler  # noqa: E402
from tools import bitunix_wo105_ws_intake as ws_intake  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_packet_assembler_v3.py"
ACTIVE_STATES = {"HOLD", "SHADOW_OPEN"}


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_object(path: Path) -> dict[str, Any]:
    return v2_assembler.read_object(path)


def write_object(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def earliest_by_payload_key(rows: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    earliest: dict[Any, dict[str, Any]] = {}
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        identity = payload.get(key)
        if identity is None:
            continue
        previous = earliest.get(identity)
        rank = (int(row.get("received_at", 2**63 - 1)), str(row.get("source_id") or ""))
        previous_rank = (
            int(previous.get("received_at", 2**63 - 1)),
            str(previous.get("source_id") or ""),
        ) if previous is not None else None
        if previous is None or rank < previous_rank:
            earliest[identity] = row
    return sorted(earliest.values(), key=lambda row: (int(row["payload"][key]), int(row["received_at"]), str(row["source_id"])))


def merge_source_records(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in (item for group in groups for item in group):
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            continue
        previous = unique.get(source_id)
        if previous is None or int(row.get("received_at", 2**63 - 1)) < int(previous.get("received_at", 2**63 - 1)):
            unique[source_id] = row
    return sorted(unique.values(), key=lambda row: (int(row["received_at"]), int(row["observed_at"]), str(row["source_id"])))


def source_view(rest_runs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    eligible = [run for run in rest_runs if run.get("eligible")]
    return {
        "signal_bars": earliest_by_payload_key(
            [row for run in eligible for row in run.get("signal_bars", [])], key="close_ms"
        ),
        "htf_bars": earliest_by_payload_key(
            [row for run in eligible for row in run.get("htf_bars", [])], key="close_ms"
        ),
        "outcome_bars": earliest_by_payload_key(
            [row for run in eligible for row in run.get("outcome_bars", [])], key="close_ms"
        ),
        "funding": merge_source_records([row for run in eligible for row in run.get("funding", [])]),
        "funding_events": earliest_by_payload_key(
            [row for run in eligible for row in run.get("funding_events", [])], key="funding_ms"
        ),
    }


def read_ws_series(ws_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    cvd, cvd_failures = v2_assembler.read_jsonl(ws_dir / "CROWD_CVD.jsonl")
    books, book_failures = v2_assembler.read_jsonl(ws_dir / "WS_BOOKS.jsonl")
    trades, trade_failures = v2_assembler.read_jsonl(ws_dir / "WS_TRADES.jsonl")
    return {
        "cvd": merge_source_records(cvd),
        "books": merge_source_records(books),
        "trades": merge_source_records(trades),
    }, sorted(set(cvd_failures + book_failures + trade_failures))


def readiness_report(
    *, lock: dict[str, Any], rest_runs: list[dict[str, Any]], ws_report: dict[str, Any], evaluation_at: int
) -> dict[str, Any]:
    eligible_rest = [run for run in rest_runs if run.get("eligible")]
    blockers: list[str] = []
    lock_failures = evaluator.validate_lock(lock)
    if lock_failures:
        blockers.extend(f"v3_lock:{failure}" for failure in lock_failures)
    if not eligible_rest:
        blockers.append("no_post_floor_rest_snapshot")
    if int(ws_report.get("accepted_runs", 0)) <= 0:
        blockers.append("no_post_floor_accepted_ws_capture")
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": "bitunix_wo105_v3_packet_sources_hold" if blockers else "bitunix_wo105_v3_packet_sources_available",
        "cohort_id": lock.get("cohort_id"),
        "evaluation_at": evaluation_at,
        "forward_start_at": lock.get("forward_start_at"),
        "rest_candidate_runs": len(rest_runs),
        "rest_eligible_runs": len(eligible_rest),
        "ws_accepted_runs": int(ws_report.get("accepted_runs", 0)),
        "receipt_selection": "earliest_received_record_per_close_ms",
        "blockers": sorted(set(blockers)),
        "packet_written": False,
        "evaluation_run": False,
        "active_events_seen": 0,
        "active_events_continued": 0,
        "terminal_transitions": 0,
        "ledger_appends": 0,
        "signals_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }


def assemble_current(
    *,
    lock: dict[str, Any],
    rest_view: dict[str, list[dict[str, Any]]],
    ws: dict[str, list[dict[str, Any]]],
    liquidation_rows: list[dict[str, Any]],
    evaluation_at: int,
    report: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if report["blockers"]:
        return None, report
    signal_bars = rest_view["signal_bars"]
    setup = evaluator.detect_setup(signal_bars, lock["params"])
    if setup is None:
        report.update(decision="bitunix_wo105_v3_packet_no_current_causal_setup", setup_status="NO_SETUP")
        return None, report
    floor = evaluator.parse_iso_ms(lock["forward_start_at"])
    assert floor is not None
    if int(setup["signal_close_ms"]) < floor:
        report["blockers"].append("setup_before_forward_floor")
        report["decision"] = "bitunix_wo105_v3_packet_hold_pre_floor_setup"
        return None, report

    cutoff = int(setup["signal_close_ms"]) + int(lock["params"]["entry"]["latency_ms"])
    funding = v2_assembler.latest_causal(rest_view["funding"], cutoff_ms=cutoff, kind="funding_rate_8h")
    cvd = v2_assembler.latest_causal(ws["cvd"], cutoff_ms=cutoff, kind="cvd_norm")
    liq = liquidation.build_context(
        liquidation_rows,
        floor_ms=floor,
        cutoff_ms=cutoff,
        window_ms=int(lock["params"]["source_contracts"]["liquidation_skew"]["window_ms"]),
        minimum_events=int(lock["params"]["source_contracts"]["liquidation_skew"]["minimum_events"]),
        minimum_notional_usd=float(lock["params"]["source_contracts"]["liquidation_skew"]["minimum_notional_usd"]),
    )
    crowd = [row for row in (funding, cvd, liq["record"]) if row is not None]
    crowd.sort(key=lambda row: (int(row["received_at"]), int(row["observed_at"]), str(row["source_id"])))
    report["crowd_quorum"] = {
        "accepted_kinds": [row["payload"]["kind"] for row in crowd],
        "required": int(lock["params"]["crowd_funding"]["quorum_fresh_inputs_required"]),
        "liquidation_blockers": liq["blockers"],
    }
    if len({row["payload"]["kind"] for row in crowd}) < int(
        lock["params"]["crowd_funding"]["quorum_fresh_inputs_required"]
    ):
        report["blockers"].append("fresh_crowd_quorum_not_met")
    entry_book = evaluator.select_entry_book(ws["books"], int(setup["signal_close_ms"]), lock["params"])
    if entry_book is None:
        report["blockers"].append("eligible_entry_book_missing")
    if report["blockers"]:
        report["decision"] = "bitunix_wo105_v3_packet_hold_incomplete_causal_inputs"
        return None, report

    packet = {
        "schema": "bitunix-wo105-causal-shadow-input-v1",
        "cohort_id": lock["cohort_id"],
        "symbol": lock["params"]["symbol"],
        "evaluation_at": evaluation_at,
        "source_manifest_sha256": "",
        "signal_bars": signal_bars,
        "htf_bars": rest_view["htf_bars"],
        "crowd": crowd,
        "books": ws["books"],
        "trades": ws["trades"],
        "outcome_bars": rest_view["outcome_bars"],
        "funding_events": rest_view["funding_events"],
    }
    assert entry_book is not None
    packet["source_manifest_sha256"] = evaluator.pre_entry_manifest(packet, setup, entry_book)
    contract_failures = evaluator.v2.validate_unit_contracts(packet, lock)
    contract_failures.extend(evaluator.v2.validate_candle_availability(packet, lock))
    if contract_failures:
        report["blockers"].extend(sorted(set(contract_failures)))
        report["decision"] = "bitunix_wo105_v3_packet_hold_unit_or_causal_availability_invalid"
        return None, report
    report.update(
        decision="bitunix_wo105_v3_causal_packet_assembled",
        packet_written=True,
        setup=setup,
        source_manifest_sha256=packet["source_manifest_sha256"],
    )
    return packet, report


def archive_packet(path: Path, *, event_id: str, packet: dict[str, Any], lock: dict[str, Any]) -> None:
    if path.exists():
        existing = read_object(path)
        if existing.get("event_id") != event_id or existing.get("packet_sha256") != evaluator.canonical_sha256(packet):
            raise ValueError("immutable_event_packet_archive_mismatch")
        return
    envelope = {
        "schema": "bitunix-wo105-event-packet-archive-v1",
        "archived_at": now_iso(),
        "event_id": event_id,
        "cohort_id": lock["cohort_id"],
        "cohort_binding_sha256": lock["parameter_cohort_sha256"],
        "source_manifest_sha256": packet["source_manifest_sha256"],
        "packet_sha256": evaluator.canonical_sha256(packet),
        "packet": packet,
        "can_trade": False,
    }
    write_object(path, envelope)


def refresh_archived_packet(
    archive: dict[str, Any],
    *,
    lock: dict[str, Any],
    rest_view: dict[str, list[dict[str, Any]]],
    ws: dict[str, list[dict[str, Any]]],
    evaluation_at: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    failures: list[str] = []
    frozen = archive.get("packet")
    if not isinstance(frozen, dict):
        return None, ["archive_packet_missing"]
    if archive.get("packet_sha256") != evaluator.canonical_sha256(frozen):
        return None, ["archive_packet_hash_mismatch"]
    if archive.get("cohort_id") != lock.get("cohort_id"):
        return None, ["archive_cohort_mismatch"]
    if archive.get("cohort_binding_sha256") != lock.get("parameter_cohort_sha256"):
        return None, ["archive_parameter_binding_mismatch"]

    packet = copy.deepcopy(frozen)
    packet["evaluation_at"] = evaluation_at
    packet["books"] = merge_source_records(list(frozen.get("books") or []), ws["books"])
    packet["trades"] = merge_source_records(list(frozen.get("trades") or []), ws["trades"])
    packet["outcome_bars"] = earliest_by_payload_key(
        list(frozen.get("outcome_bars") or []) + rest_view["outcome_bars"], key="close_ms"
    )
    packet["funding_events"] = earliest_by_payload_key(
        list(frozen.get("funding_events") or []) + rest_view["funding_events"], key="funding_ms"
    )
    setup = evaluator.detect_setup(packet["signal_bars"], lock["params"])
    if setup is None:
        failures.append("archived_setup_not_reproducible")
        return None, failures
    entry_book = evaluator.select_entry_book(packet["books"], int(setup["signal_close_ms"]), lock["params"])
    if entry_book is None:
        failures.append("archived_entry_book_not_reproducible")
        return None, failures
    manifest = evaluator.pre_entry_manifest(packet, setup, entry_book)
    if manifest != archive.get("source_manifest_sha256") or manifest != packet.get("source_manifest_sha256"):
        failures.append("archived_pre_entry_manifest_changed")
    event_id = evaluator.base.event_identity(lock, packet, setup, manifest)
    if event_id != archive.get("event_id"):
        failures.append("archived_event_id_changed")
    return (packet if not failures else None), failures


def append_if_transition(
    *, ledger: Path, lock: dict[str, Any], previous: dict[str, Any] | None, evaluation: dict[str, Any]
) -> bool:
    if not evaluation.get("event_id") or evaluation.get("state") == "CAPTURE_INVALID":
        return False
    if previous is not None and previous.get("state") == evaluation.get("state"):
        return False
    evaluator.base.append_ledger(
        ledger,
        {**evaluation, "cohort_binding_sha256": lock.get("parameter_cohort_sha256")},
    )
    return True


def continue_active_events(
    *,
    lock: dict[str, Any],
    ledger: Path,
    archive_dir: Path,
    rest_view: dict[str, list[dict[str, Any]]],
    ws: dict[str, list[dict[str, Any]]],
    evaluation_at: int,
) -> tuple[list[dict[str, Any]], list[str], int]:
    previous_events = evaluator.base.load_previous_events(ledger)
    active = {event_id: row for event_id, row in previous_events.items() if row.get("state") in ACTIVE_STATES}
    evaluations: list[dict[str, Any]] = []
    blockers: list[str] = []
    appends = 0
    for event_id, previous in sorted(active.items()):
        path = archive_dir / f"{event_id}.json"
        if not path.is_file():
            blockers.append(f"active_event_archive_missing:{event_id}")
            continue
        archive = read_object(path)
        packet, refresh_failures = refresh_archived_packet(
            archive,
            lock=lock,
            rest_view=rest_view,
            ws=ws,
            evaluation_at=evaluation_at,
        )
        if refresh_failures or packet is None:
            blockers.extend(f"active_event:{event_id}:{failure}" for failure in refresh_failures)
            continue
        evaluation = evaluator.evaluate_packet(packet, lock, previous_events=previous_events)
        if evaluation.get("event_id") != event_id:
            blockers.append(f"active_event_id_mismatch:{event_id}")
            continue
        if evaluation.get("state") == "CAPTURE_INVALID":
            blockers.extend(f"active_event:{event_id}:{failure}" for failure in evaluation.get("failures", []))
            continue
        if append_if_transition(ledger=ledger, lock=lock, previous=previous, evaluation=evaluation):
            appends += 1
        evaluations.append(evaluation)
    return evaluations, sorted(set(blockers)), appends


def main() -> int:
    parser = argparse.ArgumentParser(description="WO105 V3 earliest-receipt packet assembler and event continuation sidecar")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json")
    parser.add_argument("--rest-root", default="data/forward/bitunix_wo105_v3_rest")
    parser.add_argument("--ws-capture-root", default="data/forward/bitunix_wo105_v3_ws")
    parser.add_argument("--ws-intake-dir", default="_dl/bitunix_wo105_v3_ws_intake")
    parser.add_argument("--liquidation-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--out-dir", default="_dl/bitunix_wo105_shadow_v3")
    args = parser.parse_args()

    lock = read_object(resolve(args.lock))
    floor = evaluator.parse_iso_ms(lock.get("forward_start_at"))
    if floor is None:
        raise SystemExit("lock forward floor invalid")
    evaluation_at = now_ms()
    policy = read_object(ROOT / "configs" / "BITUNIX_WO104_INDEPENDENT_ACCEPTANCE_POLICY.json")
    ws_report = ws_intake.build_intake(
        resolve(args.ws_capture_root),
        forward_floor_ms=floor,
        expected_parser_sha256=str(policy["proposal"]["parser_sha256"]),
        out_dir=resolve(args.ws_intake_dir),
    )
    rest_runs = [
        v2_assembler.inspect_rest_run(path, floor_ms=floor, evaluation_at=evaluation_at)
        for path in sorted(resolve(args.rest_root).glob("run_*"), key=lambda item: item.name)
        if path.is_dir()
    ]
    liquidation_rows, liquidation_load_failures = liquidation.load_rows(resolve(args.liquidation_dir))
    ws, ws_failures = read_ws_series(resolve(args.ws_intake_dir))
    view = source_view(rest_runs)
    report = readiness_report(lock=lock, rest_runs=rest_runs, ws_report=ws_report, evaluation_at=evaluation_at)
    report["source_read_failures"] = sorted(set(ws_failures + liquidation_load_failures))
    if report["source_read_failures"]:
        report["blockers"].append("source_decode_failure")

    out_dir = resolve(args.out_dir)
    ledger = out_dir / "EVENT_LEDGER.jsonl"
    archive_dir = out_dir / "EVENT_PACKETS"
    out_dir.mkdir(parents=True, exist_ok=True)
    previous_events = evaluator.base.load_previous_events(ledger)
    active_before = {event_id: row for event_id, row in previous_events.items() if row.get("state") in ACTIVE_STATES}
    report["active_events_seen"] = len(active_before)

    continued, continuation_blockers, continuation_appends = continue_active_events(
        lock=lock,
        ledger=ledger,
        archive_dir=archive_dir,
        rest_view=view,
        ws=ws,
        evaluation_at=evaluation_at,
    )
    report["active_events_continued"] = len(continued)
    report["terminal_transitions"] = sum(1 for item in continued if item.get("state") in evaluator.TERMINAL_STATES)
    report["ledger_appends"] += continuation_appends
    report["continuation_blockers"] = continuation_blockers
    report["blockers"].extend(continuation_blockers)

    packet, report = assemble_current(
        lock=lock,
        rest_view=view,
        ws=ws,
        liquidation_rows=liquidation_rows,
        evaluation_at=evaluation_at,
        report=report,
    )
    last_evaluation: dict[str, Any] | None = continued[-1] if continued else None
    if packet is not None:
        write_object(out_dir / "LAST_PACKET.json", packet)
        previous_events = evaluator.base.load_previous_events(ledger)
        evaluation = evaluator.evaluate_packet(packet, lock, previous_events=previous_events)
        report["evaluation_run"] = True
        report["evaluation_state"] = evaluation["state"]
        report["evaluation_decision"] = evaluation["decision"]
        event_id = evaluation.get("event_id")
        previous = previous_events.get(event_id) if isinstance(event_id, str) else None
        if evaluation.get("state") != "CAPTURE_INVALID" and isinstance(event_id, str):
            if evaluation.get("state") in ACTIVE_STATES:
                try:
                    archive_path = archive_dir / f"{event_id}.json"
                    if not archive_path.exists():
                        archive_packet(archive_path, event_id=event_id, packet=packet, lock=lock)
                except ValueError as exc:
                    report["blockers"].append(str(exc))
            if append_if_transition(ledger=ledger, lock=lock, previous=previous, evaluation=evaluation):
                report["ledger_appends"] += 1
        elif evaluation.get("state") == "CAPTURE_INVALID":
            report["blockers"].extend(f"current_event:{failure}" for failure in evaluation.get("failures", []))
        last_evaluation = evaluation

    if last_evaluation is not None:
        write_object(out_dir / "LAST_EVALUATION.json", last_evaluation)
    report["blockers"] = sorted(set(report["blockers"]))
    report["can_trade"] = False
    if report["blockers"] and report["decision"] == "bitunix_wo105_v3_causal_packet_assembled":
        report["decision"] = "bitunix_wo105_v3_packet_or_continuation_failed_closed"
    write_object(out_dir / "PACKET_ASSEMBLY_STATUS.json", report)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "blockers": report["blockers"],
                "packet_written": report["packet_written"],
                "evaluation_run": report["evaluation_run"],
                "active_events_continued": report["active_events_continued"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not report["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
