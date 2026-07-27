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

from tools import bitunix_wo105_causal_shadow_evaluator_v2 as evaluator  # noqa: E402
from tools import bitunix_wo105_liquidation_context as liquidation  # noqa: E402
from tools import bitunix_wo105_ws_intake as ws_intake  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_packet_assembler.py"


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    if not path.is_file():
        return rows, [f"file_missing:{path.name}"]
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            failures.append(f"json_decode:{path.name}:{line_number}")
            continue
        if not isinstance(row, dict):
            failures.append(f"row_not_object:{path.name}:{line_number}")
            continue
        rows.append(row)
    return rows, failures


def record_integrity(row: dict[str, Any], *, expected_schema: str, evaluation_at: int, label: str) -> list[str]:
    return evaluator.base.validate_record(
        row,
        expected_schema=expected_schema,
        evaluation_at=evaluation_at,
        label=label,
    )


def inspect_rest_run(run_dir: Path, *, floor_ms: int, evaluation_at: int) -> dict[str, Any]:
    failures: list[str] = []
    try:
        manifest = read_object(run_dir / "PUBLIC_REST_SNAPSHOT_MANIFEST.json")
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {"run_dir": str(run_dir), "eligible": False, "failures": [f"manifest_invalid:{type(exc).__name__}"]}
    if manifest.get("snapshot_phase") != "FORWARD":
        failures.append("snapshot_not_forward")
    received = manifest.get("snapshot_received_at")
    if not isinstance(received, int) or isinstance(received, bool) or received < floor_ms or received > evaluation_at:
        failures.append("snapshot_received_time_invalid")
    if manifest.get("failures") not in ([], None):
        failures.append("snapshot_failures_nonempty")
    if manifest.get("can_trade") is not False:
        failures.append("snapshot_can_trade_not_false")
    contract = manifest.get("source_contract") if isinstance(manifest.get("source_contract"), dict) else {}
    if contract.get("native_bitunix_klines") is not True:
        failures.append("native_kline_contract_missing")
    if contract.get("funding_api_unit") != "percentage_points":
        failures.append("funding_raw_unit_invalid")
    if contract.get("funding_normalized_unit") != "decimal_fraction":
        failures.append("funding_normalized_unit_invalid")
    if contract.get("rest_depth_evaluator_admission_allowed") is not False:
        failures.append("rest_depth_not_diagnostic_only")
    files = {
        "signal_bars": ("BARS_1H.jsonl", "ohlcv-bar-v1"),
        "htf_bars": ("BARS_4H.jsonl", "ohlcv-bar-v1"),
        "outcome_bars": ("BARS_5M.jsonl", "ohlcv-bar-v1"),
    }
    series: dict[str, list[dict[str, Any]]] = {}
    for series_name, (file_name, schema) in files.items():
        rows, read_failures = read_jsonl(run_dir / file_name)
        failures.extend(read_failures)
        for index, row in enumerate(rows):
            failures.extend(record_integrity(row, expected_schema=schema, evaluation_at=evaluation_at, label=f"{series_name}[{index}]"))
        series[series_name] = rows
    funding = []
    funding_path = run_dir / "CROWD_FUNDING.json"
    event_path = run_dir / "FUNDING_EVENT.json"
    try:
        funding = [read_object(funding_path)]
        funding_events = [read_object(event_path)]
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"funding_receipt_invalid:{type(exc).__name__}")
        funding_events = []
    for index, row in enumerate(funding):
        failures.extend(record_integrity(row, expected_schema="crowd-point-v1", evaluation_at=evaluation_at, label=f"funding[{index}]"))
    for index, row in enumerate(funding_events):
        failures.extend(
            record_integrity(row, expected_schema="funding-event-v1", evaluation_at=evaluation_at, label=f"funding_events[{index}]")
        )
    return {
        "run_dir": str(run_dir),
        "eligible": not failures,
        "failures": sorted(set(failures)),
        "snapshot_received_at": received,
        "manifest": manifest,
        **series,
        "funding": funding,
        "funding_events": funding_events,
    }


def deduplicate(rows: list[dict[str, Any]], *, event_key: str) -> list[dict[str, Any]]:
    latest: dict[Any, dict[str, Any]] = {}
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        key = payload.get(event_key)
        previous = latest.get(key)
        if previous is None or int(row["received_at"]) > int(previous["received_at"]):
            latest[key] = row
    return sorted(latest.values(), key=lambda row: (int(row["payload"][event_key]), int(row["received_at"])))


def latest_causal(rows: list[dict[str, Any]], *, cutoff_ms: int, kind: str | None = None) -> dict[str, Any] | None:
    eligible = []
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if kind is not None and payload.get("kind") != kind:
            continue
        if int(row.get("observed_at", cutoff_ms + 1)) <= cutoff_ms and int(row.get("received_at", cutoff_ms + 1)) <= cutoff_ms:
            eligible.append(row)
    return max(eligible, key=lambda row: (int(row["received_at"]), str(row["source_id"]))) if eligible else None


def readiness_report(
    *,
    lock: dict[str, Any],
    rest_runs: list[dict[str, Any]],
    ws_report: dict[str, Any],
    evaluation_at: int,
) -> dict[str, Any]:
    eligible_rest = [run for run in rest_runs if run.get("eligible")]
    blockers: list[str] = []
    if evaluator.validate_lock(lock):
        blockers.append("v2_lock_invalid")
    if not eligible_rest:
        blockers.append("no_post_floor_rest_snapshot")
    if int(ws_report.get("accepted_runs", 0)) <= 0:
        blockers.append("no_post_floor_accepted_ws_capture")
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": "bitunix_wo105_packet_sources_hold" if blockers else "bitunix_wo105_packet_sources_available",
        "cohort_id": lock.get("cohort_id"),
        "evaluation_at": evaluation_at,
        "forward_start_at": lock.get("forward_start_at"),
        "rest_candidate_runs": len(rest_runs),
        "rest_eligible_runs": len(eligible_rest),
        "ws_accepted_runs": int(ws_report.get("accepted_runs", 0)),
        "blockers": blockers,
        "packet_written": False,
        "evaluation_run": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }


def assemble(
    *,
    lock: dict[str, Any],
    rest_runs: list[dict[str, Any]],
    ws_dir: Path,
    liquidation_rows: list[dict[str, Any]],
    evaluation_at: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    ws_manifest = read_object(ws_dir / "WS_INTAKE_MANIFEST.json")
    report = readiness_report(lock=lock, rest_runs=rest_runs, ws_report=ws_manifest, evaluation_at=evaluation_at)
    eligible_rest = [run for run in rest_runs if run.get("eligible")]
    if report["blockers"]:
        return None, report
    latest_rest = max(eligible_rest, key=lambda run: int(run["snapshot_received_at"]))
    signal_bars = deduplicate(latest_rest["signal_bars"], event_key="close_ms")
    htf_bars = deduplicate(latest_rest["htf_bars"], event_key="close_ms")
    outcome_bars = deduplicate(latest_rest["outcome_bars"], event_key="close_ms")
    setup = evaluator.detect_setup(signal_bars, lock["params"])
    if setup is None:
        report.update(decision="bitunix_wo105_packet_no_current_causal_setup", setup_status="NO_SETUP")
        return None, report
    floor = evaluator.parse_iso_ms(lock["forward_start_at"])
    assert floor is not None
    if int(setup["signal_close_ms"]) < floor:
        report["blockers"].append("setup_before_forward_floor")
        report["decision"] = "bitunix_wo105_packet_hold_pre_floor_setup"
        return None, report
    cutoff = int(setup["signal_close_ms"]) + int(lock["params"]["entry"]["latency_ms"])
    funding_rows = [row for run in eligible_rest for row in run["funding"]]
    funding = latest_causal(funding_rows, cutoff_ms=cutoff, kind="funding_rate_8h")
    cvd_rows, cvd_failures = read_jsonl(ws_dir / "CROWD_CVD.jsonl")
    books, book_failures = read_jsonl(ws_dir / "WS_BOOKS.jsonl")
    trades, trade_failures = read_jsonl(ws_dir / "WS_TRADES.jsonl")
    report["source_read_failures"] = sorted(set(cvd_failures + book_failures + trade_failures))
    cvd = latest_causal(cvd_rows, cutoff_ms=cutoff, kind="cvd_norm")
    liq = liquidation.build_context(
        liquidation_rows,
        floor_ms=floor,
        cutoff_ms=cutoff,
        window_ms=int(lock["params"]["source_contracts"]["liquidation_skew"]["window_ms"]),
        minimum_events=int(lock["params"]["source_contracts"]["liquidation_skew"]["minimum_events"]),
        minimum_notional_usd=float(lock["params"]["source_contracts"]["liquidation_skew"]["minimum_notional_usd"]),
    )
    crowd = [row for row in (funding, cvd, liq["record"]) if row is not None]
    crowd.sort(key=lambda row: (int(row["received_at"]), str(row["source_id"])))
    report["crowd_quorum"] = {
        "accepted_kinds": [row["payload"]["kind"] for row in crowd],
        "required": int(lock["params"]["crowd_funding"]["quorum_fresh_inputs_required"]),
        "liquidation_blockers": liq["blockers"],
    }
    if len({row["payload"]["kind"] for row in crowd}) < int(lock["params"]["crowd_funding"]["quorum_fresh_inputs_required"]):
        report["blockers"].append("fresh_crowd_quorum_not_met")
    entry_book = evaluator.select_entry_book(books, int(setup["signal_close_ms"]), lock["params"])
    if entry_book is None:
        report["blockers"].append("eligible_entry_book_missing")
    if report.get("source_read_failures"):
        report["blockers"].append("source_read_failures")
    if report["blockers"]:
        report["decision"] = "bitunix_wo105_packet_hold_incomplete_causal_inputs"
        return None, report
    funding_events = deduplicate(
        [row for run in eligible_rest for row in run["funding_events"]],
        event_key="funding_ms",
    )
    packet = {
        "schema": "bitunix-wo105-causal-shadow-input-v1",
        "cohort_id": lock["cohort_id"],
        "symbol": lock["params"]["symbol"],
        "evaluation_at": evaluation_at,
        "source_manifest_sha256": "",
        "signal_bars": signal_bars,
        "htf_bars": htf_bars,
        "crowd": crowd,
        "books": books,
        "trades": trades,
        "outcome_bars": outcome_bars,
        "funding_events": funding_events,
    }
    assert entry_book is not None
    packet["source_manifest_sha256"] = evaluator.pre_entry_manifest(packet, setup, entry_book)
    unit_failures = evaluator.validate_unit_contracts(packet, lock)
    causal_failures = evaluator.validate_candle_availability(packet, lock)
    if unit_failures or causal_failures:
        report["blockers"].extend(unit_failures + causal_failures)
        report["decision"] = "bitunix_wo105_packet_hold_unit_or_causal_availability_invalid"
        return None, report
    report.update(
        decision="bitunix_wo105_causal_packet_assembled",
        packet_written=True,
        setup=setup,
        source_manifest_sha256=packet["source_manifest_sha256"],
    )
    return packet, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed prospective packet assembler for Bitunix WO105 V2")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json")
    parser.add_argument("--rest-root", default="data/forward/bitunix_wo105_rest")
    parser.add_argument("--ws-capture-root", default="data/forward/bitunix_wo105_ws")
    parser.add_argument("--ws-intake-dir", default="_dl/bitunix_wo105_ws_intake")
    parser.add_argument("--liquidation-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--out-dir", default="_dl/bitunix_wo105_shadow_v2")
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
        inspect_rest_run(path, floor_ms=floor, evaluation_at=evaluation_at)
        for path in sorted(resolve(args.rest_root).glob("run_*"), key=lambda item: item.name)
        if path.is_dir()
    ]
    liquidation_rows, liquidation_load_failures = liquidation.load_rows(resolve(args.liquidation_dir))
    packet, report = assemble(
        lock=lock,
        rest_runs=rest_runs,
        ws_dir=resolve(args.ws_intake_dir),
        liquidation_rows=liquidation_rows,
        evaluation_at=evaluation_at,
    )
    report["ws_intake_decision"] = ws_report["decision"]
    report["liquidation_load_failures"] = liquidation_load_failures
    if liquidation_load_failures:
        report["blockers"].append("liquidation_source_decode_failure")
        report["decision"] = "bitunix_wo105_packet_hold_source_decode_failure"
        packet = None
    out_dir = resolve(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if packet is not None:
        packet_path = out_dir / "LAST_PACKET.json"
        packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        evaluation = evaluator.evaluate_packet(packet, lock, previous_events=evaluator.base.load_previous_events(out_dir / "EVENT_LEDGER.jsonl"))
        (out_dir / "LAST_EVALUATION.json").write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        report["evaluation_run"] = True
        report["evaluation_state"] = evaluation["state"]
        report["evaluation_decision"] = evaluation["decision"]
        if evaluation.get("event_id") and evaluation.get("state") != "CAPTURE_INVALID":
            evaluator.base.append_ledger(
                out_dir / "EVENT_LEDGER.jsonl",
                {**evaluation, "cohort_binding_sha256": lock.get("parameter_cohort_sha256")},
            )
    report["can_trade"] = False
    (out_dir / "PACKET_ASSEMBLY_STATUS.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "blockers": report["blockers"],
                "packet_written": report["packet_written"],
                "evaluation_run": report["evaluation_run"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
