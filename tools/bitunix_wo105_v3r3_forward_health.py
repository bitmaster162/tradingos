#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rest_quality(rest_root: Path) -> dict[str, Any]:
    manifests = sorted(rest_root.glob("run_*/PUBLIC_REST_SNAPSHOT_MANIFEST.json"))
    partial: list[dict[str, Any]] = []
    accepted = 0
    credentials = private_calls = order_calls = 0
    for path in manifests:
        payload = read_json(path)
        failures = payload.get("failures") if isinstance(payload.get("failures"), list) else []
        receipts = payload.get("http_receipts") if isinstance(payload.get("http_receipts"), list) else []
        credentials += sum(int(row.get("credentials_used") or 0) for row in receipts if isinstance(row, dict))
        private_calls += sum(int(row.get("private_calls") or 0) for row in receipts if isinstance(row, dict))
        order_calls += sum(int(row.get("order_calls") or 0) for row in receipts if isinstance(row, dict))
        if payload.get("decision") == "bitunix_wo105_public_rest_snapshot_collected" and not failures:
            accepted += 1
        else:
            partial.append(
                {
                    "path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
                    "decision": payload.get("decision"),
                    "failures": failures,
                    "bar_counts": payload.get("bar_counts"),
                }
            )
    total = len(manifests)
    return {
        "candidate_runs": total,
        "accepted_runs": accepted,
        "acceptance_pct": round(100.0 * accepted / total, 6) if total else 0.0,
        "excluded_runs": partial,
        "credentials_used": credentials,
        "private_calls": private_calls,
        "order_calls": order_calls,
    }


def ws_quality(ws_intake: dict[str, Any]) -> dict[str, Any]:
    runs = ws_intake.get("runs") if isinstance(ws_intake.get("runs"), list) else []
    error_totals = {key: 0 for key in ("NETWORK", "PARSER", "LOCAL", "STORAGE")}
    accepted = held = frames = trades = depth = reconnects = 0
    credentials = private_calls = order_calls = 0
    invalid_runs: list[dict[str, Any]] = []
    max_silence = 0.0
    for row in runs:
        if not isinstance(row, dict):
            continue
        run_dir = Path(str(row.get("run_dir") or ""))
        manifest = read_json(run_dir / "PUBLIC_CAPTURE_MANIFEST.json")
        subscription = manifest.get("subscription_acceptance") if isinstance(manifest.get("subscription_acceptance"), dict) else {}
        errors = manifest.get("error_taxonomy") if isinstance(manifest.get("error_taxonomy"), dict) else {}
        reasons: list[str] = []
        if row.get("accepted") is not True or subscription.get("accepted") is not True:
            reasons.append("capture_not_accepted")
        if manifest.get("hold") is True:
            reasons.append("capture_held")
        if any(int(errors.get(key) or 0) > 0 for key in error_totals):
            reasons.append("capture_error_taxonomy_nonzero")
        if any(int(manifest.get(key) or 0) > 0 for key in ("credentials_used", "private_calls", "order_calls")):
            reasons.append("non_public_effect_detected")
        if manifest.get("can_trade") is not False:
            reasons.append("capture_can_trade_not_false")
        if reasons:
            invalid_runs.append({"run_dir": str(run_dir), "reasons": sorted(set(reasons))})
        else:
            accepted += 1
        held += int(manifest.get("hold") is True)
        frames += int(manifest.get("frames_total") or 0)
        trades += int(manifest.get("trade_prints_total") or 0)
        depth += int((manifest.get("parse_kinds") or {}).get("DepthUpdate") or 0)
        reconnects += int(manifest.get("reconnects") or 0)
        max_silence = max(max_silence, float(manifest.get("max_recv_silence_ms") or 0.0))
        credentials += int(manifest.get("credentials_used") or 0)
        private_calls += int(manifest.get("private_calls") or 0)
        order_calls += int(manifest.get("order_calls") or 0)
        for key in error_totals:
            error_totals[key] += int(errors.get(key) or 0)
    return {
        "candidate_runs": len(runs),
        "accepted_runs": accepted,
        "acceptance_pct": round(100.0 * accepted / len(runs), 6) if runs else 0.0,
        "invalid_runs": invalid_runs,
        "held_runs": held,
        "frames_total": frames,
        "trade_prints_total": trades,
        "depth_updates_total": depth,
        "reconnects": reconnects,
        "max_recv_silence_ms": max_silence,
        "error_taxonomy": error_totals,
        "credentials_used": credentials,
        "private_calls": private_calls,
        "order_calls": order_calls,
    }


def ledger_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def build_report(
    *,
    lock_path: Path,
    loop_status_path: Path,
    status_path: Path,
    first_cycle_path: Path,
    packet_path: Path,
    ws_intake_path: Path,
    rest_root: Path,
    ledger_path: Path,
    loop_freshness_seconds: int,
    minimum_rest_acceptance_pct: float,
) -> dict[str, Any]:
    lock = read_json(lock_path)
    loop = read_json(loop_status_path)
    status = read_json(status_path)
    first_cycle = read_json(first_cycle_path)
    packet = read_json(packet_path)
    ws_intake = read_json(ws_intake_path)
    rest = rest_quality(rest_root)
    ws = ws_quality(ws_intake)
    failures: list[str] = []
    warnings: list[str] = []

    loop_time = parse_time(loop.get("ts"))
    loop_age = (now_utc() - loop_time).total_seconds() if loop_time else None
    allowed_loop_states = {
        "waiting_forward_floor",
        "starting_public_ws_capture",
        "public_ws_capture_running",
        "assembling_and_continuing_shadow_events",
        "cycle_complete_shadow_only",
    }
    if lock.get("can_trade") is not False:
        failures.append("frozen_lock_can_trade_not_false")
    if not isinstance(loop.get("pid"), int) or loop.get("status") not in allowed_loop_states:
        failures.append("managed_loop_state_invalid")
    if loop_age is None or loop_age > loop_freshness_seconds:
        failures.append("managed_loop_status_stale")
    if first_cycle.get("decision") != "bitunix_wo105_v3_first_cycle_accepted_shadow_only":
        failures.append("first_cycle_operational_gate_not_accepted")
    if first_cycle.get("failures"):
        failures.append("first_cycle_failures_nonempty")
    if ws["candidate_runs"] <= 0 or ws["invalid_runs"]:
        failures.append("ws_capture_quality_invalid")
    if rest["candidate_runs"] <= 0 or rest["acceptance_pct"] < minimum_rest_acceptance_pct:
        failures.append("rest_acceptance_below_floor")
    if packet.get("blockers"):
        failures.append("packet_assembly_blockers_nonempty")
    if packet.get("source_read_failures"):
        failures.append("packet_source_read_failures_nonempty")
    if any(int(rest[key]) > 0 for key in ("credentials_used", "private_calls", "order_calls")):
        failures.append("rest_non_public_effect_detected")
    if any(int(ws[key]) > 0 for key in ("credentials_used", "private_calls", "order_calls")):
        failures.append("ws_non_public_effect_detected")
    if rest["excluded_runs"]:
        warnings.append("rest_snapshots_excluded_fail_closed")
    if int(status.get("forward_events") or 0) == 0:
        warnings.append("no_forward_setup_events_yet")

    decision = "bitunix_wo105_v3r3_forward_health_blocked"
    if not failures:
        decision = (
            "bitunix_wo105_v3r3_forward_health_pass_with_excluded_snapshots"
            if warnings
            else "bitunix_wo105_v3r3_forward_health_pass"
        )
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": "tools/bitunix_wo105_v3r3_forward_health.py",
        "decision": decision,
        "cohort_id": lock.get("cohort_id"),
        "parameter_cohort_sha256": lock.get("parameter_cohort_sha256"),
        "forward_start_at": lock.get("forward_start_at"),
        "loop": {
            "status": loop.get("status"),
            "pid": loop.get("pid"),
            "status_age_seconds": round(loop_age, 3) if loop_age is not None else None,
            "freshness_limit_seconds": loop_freshness_seconds,
        },
        "operational_gate": {
            "decision": first_cycle.get("decision"),
            "checks": first_cycle.get("checks"),
            "failures": first_cycle.get("failures"),
        },
        "rest_quality": rest,
        "ws_quality": ws,
        "packet": {
            "decision": packet.get("decision"),
            "blockers": packet.get("blockers"),
            "source_read_failures": packet.get("source_read_failures"),
            "setup_status": packet.get("setup_status"),
            "packet_written": packet.get("packet_written"),
            "evaluation_run": packet.get("evaluation_run"),
        },
        "forward_sample": {
            "events": int(status.get("forward_events") or 0),
            "terminal_events": int(status.get("terminal_forward_events") or 0),
            "terminal_progress": status.get("terminal_forward_progress"),
            "ledger_rows": ledger_rows(ledger_path),
            "edge_evaluated": status.get("edge_evaluated"),
        },
        "provenance": {
            "lock_path": lock_path.relative_to(ROOT).as_posix() if lock_path.is_relative_to(ROOT) else str(lock_path),
            "lock_sha256": sha256_file(lock_path),
        },
        "failures": sorted(set(failures)),
        "warnings": sorted(set(warnings)),
        "promotion": "HOLD",
        "signals_allowed": False,
        "paper_entries_allowed": False,
        "orders_allowed": False,
        "capital_permission": "DENY",
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    rest = report["rest_quality"]
    ws = report["ws_quality"]
    sample = report["forward_sample"]
    return "\n".join(
        [
            "# Bitunix WO105 V3R3 forward health",
            "",
            f"- Decision: `{report['decision']}`",
            f"- Loop: `{report['loop']['status']}`, PID `{report['loop']['pid']}`, age `{report['loop']['status_age_seconds']}s`",
            f"- REST: `{rest['accepted_runs']}/{rest['candidate_runs']}` accepted (`{rest['acceptance_pct']}%`)",
            f"- WS: `{ws['accepted_runs']}/{ws['candidate_runs']}` accepted; frames `{ws['frames_total']}`; trades `{ws['trade_prints_total']}`; depth `{ws['depth_updates_total']}`",
            f"- WS errors: `{ws['error_taxonomy']}`; reconnects `{ws['reconnects']}`",
            f"- Packet: `{report['packet']['decision']}`; blockers `{report['packet']['blockers']}`",
            f"- Forward sample: events `{sample['events']}`, terminal `{sample['terminal_events']}`, ledger rows `{sample['ledger_rows']}`",
            f"- Failures: `{report['failures']}`",
            f"- Warnings: `{report['warnings']}`",
            "- This is an operational data-quality audit, not evidence of trading edge.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only health audit for the active Bitunix WO105 V3R3 lane")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R3_2026-07-14.json")
    parser.add_argument("--loop-status", default="logs/bitunix_wo105_v3r3/bitunix_wo105_v3r3_forward_loop_status.json")
    parser.add_argument("--status", default="docs/BITUNIX_WO105_V3R3_STATUS_2026-07-14.json")
    parser.add_argument("--first-cycle", default="docs/BITUNIX_WO105_V3R3_FIRST_CYCLE_GATE_2026-07-14.json")
    parser.add_argument("--packet-status", default="_dl/bitunix_wo105_shadow_v3r3/PACKET_ASSEMBLY_STATUS.json")
    parser.add_argument("--ws-intake", default="_dl/bitunix_wo105_v3r3_ws_intake/WS_INTAKE_MANIFEST.json")
    parser.add_argument("--rest-root", default="data/forward/bitunix_wo105_v3r3_rest")
    parser.add_argument("--ledger", default="_dl/bitunix_wo105_shadow_v3r3/EVENT_LEDGER.jsonl")
    parser.add_argument("--loop-freshness-seconds", type=int, default=900)
    parser.add_argument("--minimum-rest-acceptance-pct", type=float, default=95.0)
    parser.add_argument("--out-prefix", default="docs/BITUNIX_WO105_V3R3_FORWARD_HEALTH_2026-07-15")
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
