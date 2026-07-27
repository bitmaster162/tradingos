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

from tools import bitunix_wo105_v3r3_forward_health as v3r3  # noqa: E402


TOOL_PATH = "tools/bitunix_wo105_v3r4_forward_health.py"
BASE_WS_QUALITY = v3r3.ws_quality
INCOMPLETE_PUBLIC_CAPTURE_FILES = {"RAW_FRAMES.jsonl", "RAW_FRAME_INDEX.jsonl", "TRADES.jsonl"}
INCOMPLETE_PUBLIC_CAPTURE_REQUIRED_DATA = {"RAW_FRAMES.jsonl", "RAW_FRAME_INDEX.jsonl"}
NETWORK_ONLY_HOLD_PREFIXES = ("recv_silence:", "reconnect_downtime:", "network:")
CAUSAL_PRE_ENTRY_PACKET_HOLD_BLOCKERS = {
    "latest_htf_bar_not_available_by_entry_cutoff",
    "signal_bar_not_available_by_entry_cutoff",
}


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def incomplete_public_capture_is_bounded(row: dict[str, Any], ws_intake: dict[str, Any]) -> bool:
    failures = row.get("failures") if isinstance(row.get("failures"), list) else []
    boundary = ws_intake.get("runtime_boundary") if isinstance(ws_intake.get("runtime_boundary"), dict) else {}
    run_dir = Path(str(row.get("run_dir") or ""))
    capture_root = Path(str(ws_intake.get("capture_root") or ""))
    if failures != ["capture_metadata_invalid:FileNotFoundError"]:
        return False
    if (
        boundary.get("public_read_only") is not True
        or boundary.get("signals_allowed") is not False
        or boundary.get("paper_entries_allowed") is not False
        or boundary.get("orders_allowed") is not False
        or boundary.get("can_trade") is not False
    ):
        return False
    if not run_dir.is_dir() or not capture_root:
        return False
    try:
        if not run_dir.resolve().is_relative_to(capture_root.resolve()):
            return False
    except OSError:
        return False
    manifest_path = run_dir / "PUBLIC_CAPTURE_MANIFEST.json"
    if manifest_path.exists():
        return False
    files = {path.name for path in run_dir.iterdir() if path.is_file()}
    if files != INCOMPLETE_PUBLIC_CAPTURE_FILES:
        return False
    return all((run_dir / name).stat().st_size > 0 for name in INCOMPLETE_PUBLIC_CAPTURE_REQUIRED_DATA)


def network_only_hold_is_bounded(manifest: dict[str, Any], errors: dict[str, Any]) -> bool:
    hold_reasons = manifest.get("hold_reasons") if isinstance(manifest.get("hold_reasons"), list) else []
    return (
        manifest.get("hold") is True
        and manifest.get("terminal_hold") is False
        and int(errors.get("NETWORK") or 0) > 0
        and all(int(errors.get(key) or 0) == 0 for key in ("PARSER", "LOCAL", "STORAGE"))
        and bool(hold_reasons)
        and all(str(reason).startswith(NETWORK_ONLY_HOLD_PREFIXES) for reason in hold_reasons)
    )


def ws_quality(ws_intake: dict[str, Any]) -> dict[str, Any]:
    report = BASE_WS_QUALITY(ws_intake)
    runs = ws_intake.get("runs") if isinstance(ws_intake.get("runs"), list) else []
    fatal: list[dict[str, Any]] = []
    network_only: list[dict[str, Any]] = []
    abandoned_incomplete: list[dict[str, Any]] = []
    invalid_dirs = {str(row.get("run_dir")) for row in report["invalid_runs"]}
    intake_rows = {str(row.get("run_dir")): row for row in runs if isinstance(row, dict)}
    latest_run_accepted = bool(runs) and str(runs[-1].get("run_dir")) not in invalid_dirs
    run_positions = {str(row.get("run_dir")): index for index, row in enumerate(runs) if isinstance(row, dict)}
    for row in report["invalid_runs"]:
        run_dir = Path(str(row.get("run_dir") or ""))
        intake_row = intake_rows.get(str(row.get("run_dir")), row)
        manifest = v3r3.read_json(run_dir / "PUBLIC_CAPTURE_MANIFEST.json")
        errors = manifest.get("error_taxonomy") if isinstance(manifest.get("error_taxonomy"), dict) else {}
        has_fatal_error = any(int(errors.get(key) or 0) > 0 for key in ("PARSER", "LOCAL", "STORAGE"))
        non_public = any(int(manifest.get(key) or 0) > 0 for key in ("credentials_used", "private_calls", "order_calls"))
        bounded_network_hold = network_only_hold_is_bounded(manifest, errors)
        boundary_invalid = (
            manifest.get("can_trade") is not False
            or manifest.get("terminal_hold") is True
            or (manifest.get("hold") is True and not bounded_network_hold)
        )
        item = {**row, "intake_failures": intake_row.get("failures") or [], "error_taxonomy": errors}
        position = run_positions.get(str(row.get("run_dir")), -1)
        later_clean_recovery = any(
            index > position
            and candidate.get("accepted") is True
            and str(candidate.get("run_dir")) not in invalid_dirs
            for index, candidate in enumerate(runs)
            if isinstance(candidate, dict)
        )
        if incomplete_public_capture_is_bounded(intake_row, ws_intake) and later_clean_recovery:
            abandoned_incomplete.append({**item, "later_clean_recovery": True})
        elif has_fatal_error or non_public or boundary_invalid:
            fatal.append(item)
        elif int(errors.get("NETWORK") or 0) > 0:
            network_only.append(item)
        else:
            fatal.append(item)
    report.update(
        fatal_invalid_runs=fatal,
        network_only_excluded_runs=network_only,
        abandoned_incomplete_runs=abandoned_incomplete,
        latest_completed_run_accepted=latest_run_accepted,
    )
    return report


def build_report(**kwargs: Any) -> dict[str, Any]:
    original = v3r3.ws_quality
    v3r3.ws_quality = ws_quality
    try:
        report = v3r3.build_report(**kwargs)
    finally:
        v3r3.ws_quality = original

    ws = report["ws_quality"]
    failures = set(report["failures"])
    warnings = set(report["warnings"])
    lock = v3r3.read_json(Path(kwargs["lock_path"]))
    loop = v3r3.read_json(Path(kwargs["loop_status_path"]))
    status = v3r3.read_json(Path(kwargs["status_path"]))
    first_cycle = v3r3.read_json(Path(kwargs["first_cycle_path"]))
    floor = v3r3.parse_time(lock.get("forward_start_at"))
    first_cycle_accepted = (
        first_cycle.get("decision") == "bitunix_wo105_v3r4_first_cycle_accepted_shadow_only"
        and not first_cycle.get("failures")
    )
    if first_cycle_accepted:
        failures.discard("first_cycle_operational_gate_not_accepted")
        failures.discard("first_cycle_failures_nonempty")
    pre_floor_wait = (
        floor is not None
        and datetime.now(timezone.utc) < floor
        and loop.get("status") == "waiting_forward_floor"
        and status.get("phase") == "WAITING_FORWARD_FLOOR"
        and first_cycle.get("decision") == "bitunix_wo105_v3r4_first_cycle_waiting_forward_floor"
        and not first_cycle.get("failures")
    )
    if pre_floor_wait:
        failures.discard("first_cycle_operational_gate_not_accepted")
        failures.discard("ws_capture_quality_invalid")
        failures.discard("rest_acceptance_below_floor")
        warnings.add("forward_floor_not_reached")
    recoverable_network_only = (
        bool(ws["network_only_excluded_runs"])
        and not ws["fatal_invalid_runs"]
        and int(ws["accepted_runs"]) > 0
        and ws["latest_completed_run_accepted"] is True
    )
    if recoverable_network_only:
        failures.discard("ws_capture_quality_invalid")
        warnings.add("network_capture_run_excluded_after_clean_recovery")
    recoverable_abandoned_incomplete = (
        bool(ws["abandoned_incomplete_runs"])
        and not ws["fatal_invalid_runs"]
        and int(ws["accepted_runs"]) > 0
        and ws["latest_completed_run_accepted"] is True
    )
    if recoverable_abandoned_incomplete:
        failures.discard("ws_capture_quality_invalid")
        warnings.add("abandoned_incomplete_capture_excluded_after_clean_recovery")
    if ws["fatal_invalid_runs"]:
        failures.add("ws_capture_quality_invalid")
    if ws["network_only_excluded_runs"] and ws["latest_completed_run_accepted"] is not True:
        failures.add("ws_capture_not_recovered_after_network_error")

    packet = report.get("packet") if isinstance(report.get("packet"), dict) else {}
    packet_blockers = {str(item) for item in packet.get("blockers") or []}
    bounded_causal_packet_hold = (
        packet.get("decision") == "bitunix_wo105_v3_packet_hold_unit_or_causal_availability_invalid"
        and bool(packet_blockers)
        and packet_blockers <= CAUSAL_PRE_ENTRY_PACKET_HOLD_BLOCKERS
        and packet.get("packet_written") is False
        and packet.get("evaluation_run") is False
        and not packet.get("source_read_failures")
    )
    if bounded_causal_packet_hold:
        failures.discard("packet_assembly_blockers_nonempty")
        warnings.add("causal_pre_entry_packet_hold_excluded_before_event_admission")
    if packet:
        packet["bounded_causal_hold_excluded"] = bounded_causal_packet_hold

    report["failures"] = sorted(failures)
    report["warnings"] = sorted(warnings)
    report["tool"] = TOOL_PATH
    if report["failures"]:
        report["decision"] = "bitunix_wo105_v3r4_forward_health_blocked"
    elif report["warnings"]:
        report["decision"] = "bitunix_wo105_v3r4_forward_health_pass_with_exclusions"
    else:
        report["decision"] = "bitunix_wo105_v3r4_forward_health_pass"
    report["can_trade"] = False
    return report


def render_markdown(report: dict[str, Any]) -> str:
    rendered = v3r3.render_markdown(report).replace("V3R3", "V3R4").rstrip()
    excluded = len(report["ws_quality"].get("abandoned_incomplete_runs") or [])
    causal_hold = bool((report.get("packet") or {}).get("bounded_causal_hold_excluded"))
    return (
        f"{rendered}\n"
        f"- Abandoned incomplete WS captures excluded after clean recovery: `{excluded}`.\n"
        f"- Bounded causal pre-entry packet hold excluded: `{str(causal_hold).lower()}`.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only health audit for the active Bitunix WO105 V3R4 lane")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json")
    parser.add_argument("--loop-status", default="logs/bitunix_wo105_v3r4/bitunix_wo105_v3r4_forward_loop_status.json")
    parser.add_argument("--status", default="docs/BITUNIX_WO105_V3R4_STATUS_2026-07-15.json")
    parser.add_argument("--first-cycle", default="docs/BITUNIX_WO105_V3R4_FIRST_CYCLE_GATE_2026-07-15.json")
    parser.add_argument("--packet-status", default="_dl/bitunix_wo105_shadow_v3r4/PACKET_ASSEMBLY_STATUS.json")
    parser.add_argument("--ws-intake", default="_dl/bitunix_wo105_v3r4_ws_intake/WS_INTAKE_MANIFEST.json")
    parser.add_argument("--rest-root", default="data/forward/bitunix_wo105_v3r4_rest")
    parser.add_argument("--ledger", default="_dl/bitunix_wo105_shadow_v3r4/EVENT_LEDGER.jsonl")
    parser.add_argument("--loop-freshness-seconds", type=int, default=900)
    parser.add_argument("--minimum-rest-acceptance-pct", type=float, default=95.0)
    parser.add_argument("--out-prefix", default="docs/BITUNIX_WO105_V3R4_FORWARD_HEALTH_2026-07-15")
    args = parser.parse_args()
    report = build_report(
        lock_path=resolve(args.lock),
        loop_status_path=resolve(args.loop_status),
        status_path=resolve(args.status),
        first_cycle_path=resolve(args.first_cycle),
        packet_path=resolve(args.packet_status),
        ws_intake_path=resolve(args.ws_intake),
        rest_root=resolve(args.rest_root),
        ledger_path=resolve(args.ledger),
        loop_freshness_seconds=args.loop_freshness_seconds,
        minimum_rest_acceptance_pct=args.minimum_rest_acceptance_pct,
    )
    out = resolve(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "rest": f"{report['rest_quality']['accepted_runs']}/{report['rest_quality']['candidate_runs']}",
                "ws": f"{report['ws_quality']['accepted_runs']}/{report['ws_quality']['candidate_runs']}",
                "events": report["forward_sample"]["events"],
                "failures": report["failures"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
