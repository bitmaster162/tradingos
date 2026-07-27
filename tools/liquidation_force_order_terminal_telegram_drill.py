#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NOTIFIER = ROOT / "tools" / "liquidation_force_order_terminal_telegram_notify.py"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def descriptor(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": True, "sha256": sha256_file(path), "size": path.stat().st_size}


def build_terminal_chain(base: Path, terminal: str) -> dict[str, Path]:
    lock_path = base / "lock.json"
    intake_path = base / "intake.json"
    records_path = base / "records.csv"
    event_path = base / "event.json"
    evaluation_path = base / "evaluation.json"
    pipeline_path = base / "pipeline.json"
    guard_path = base / "guard.json"
    lock = {"lock_id": f"synthetic-{terminal}-lock", "can_trade": False}
    write_json(lock_path, lock)
    write_json(intake_path, {"decision": "synthetic_ready", "can_trade": False})
    records_path.write_text("symbol,reversal_return_bps\nBTCUSDT,12\n", encoding="utf-8")
    write_json(
        event_path,
        {
            "decision": "force_order_event_study_ready_for_review",
            "can_trade": False,
            "artifacts": {
                "records_csv": str(records_path),
                "records_csv_sha256": sha256_file(records_path),
                "records": 1,
            },
        },
    )
    pass_case = terminal == "pass_for_manual_forward_review"
    checks = {"mean": pass_case, "winrate": True, "ci": pass_case, "horizons": True}
    write_json(
        evaluation_path,
        {
            "decision": terminal,
            "can_trade": False,
            "integrity_errors": [],
            "preregistration": {"lock_id": lock["lock_id"], "sha256": sha256_file(lock_path)},
            "source": {
                "event_study_report": str(event_path),
                "records_csv": str(records_path),
                "records_csv_sha256": sha256_file(records_path),
            },
            "evaluation": {
                "sample_ready": True,
                "economic_checks": checks,
                "positive_horizons_after_cost": 3 if pass_case else 1,
                "primary": {
                    "horizon_bars": 2,
                    "records": 80,
                    "independent_4h_blocks": 20,
                    "cluster_after_cost": {
                        "mean_bps": 11.0 if pass_case else -3.0,
                        "winrate_positive_pct": 65.0 if pass_case else 40.0,
                    },
                    "cluster_bootstrap": {
                        "mean_ci_bps": [2.0, 20.0] if pass_case else [-12.0, 3.0],
                        "probability_mean_gt_zero": 0.99 if pass_case else 0.2,
                    },
                },
                "symbol_concentration_diagnostics": {
                    "informational_only_not_a_v3_gate": True,
                    "primary_largest_symbol_record_share_pct": 35.0,
                    "primary_leave_one_symbol_out_mean_bps": {"BTCUSDT": 8.0, "ETHUSDT": 10.0},
                    "primary_sign_flip_symbols": [],
                },
            },
        },
    )
    pipeline_decision = (
        "force_order_pipeline_pass_for_manual_forward_review"
        if pass_case
        else "force_order_pipeline_tombstone_review_required"
    )
    write_json(
        pipeline_path,
        {
            "decision": pipeline_decision,
            "can_trade": False,
            "preregistration": {"lock_id": lock["lock_id"], "sha256": sha256_file(lock_path)},
            "artifacts": {
                "intake_report": descriptor(intake_path),
                "event_study_report": descriptor(event_path),
                "event_records_csv": descriptor(records_path),
                "evaluation_report": descriptor(evaluation_path),
            },
        },
    )
    guard_decision = (
        "force_order_preregistered_guard_completed_pass_for_manual_forward_review"
        if pass_case
        else "force_order_preregistered_guard_completed_tombstone_review_required"
    )
    write_json(
        guard_path,
        {
            "decision": guard_decision,
            "state": {"completed": True, "pipeline_output": str(pipeline_path)},
            "can_trade": False,
        },
    )
    return {
        "lock": lock_path,
        "records": records_path,
        "pipeline": pipeline_path,
        "guard": guard_path,
        "receipt": base / "receipt.json",
        "ledger": base / "ledger.jsonl",
        "state": base / "telegram_state.json",
        "card": base / "card",
        "notify": base / "notify",
    }


def run_notifier(paths: dict[str, Path]) -> tuple[dict[str, Any], dict[str, Any]]:
    command = [
        sys.executable,
        str(NOTIFIER),
        "--guard-report",
        str(paths["guard"]),
        "--prereg-lock",
        str(paths["lock"]),
        "--receipt",
        str(paths["receipt"]),
        "--ledger",
        str(paths["ledger"]),
        "--state",
        str(paths["state"]),
        "--card-prefix",
        str(paths["card"]),
        "--out-prefix",
        str(paths["notify"]),
    ]
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
    return {
        "exit_code": run.returncode,
        "stdout": run.stdout[-4000:],
        "stderr": run.stderr[-4000:],
    }, read_json(paths["notify"].with_suffix(".json"))


def build_report(work_dir: Path) -> dict[str, Any]:
    waiting_dir = work_dir / "waiting"
    waiting_dir.mkdir(parents=True, exist_ok=True)
    waiting_guard = waiting_dir / "guard.json"
    write_json(waiting_guard, {"decision": "force_order_preregistered_guard_waiting_sample", "state": {}, "can_trade": False})
    waiting_paths = {
        "guard": waiting_guard,
        "lock": waiting_dir / "lock.json",
        "receipt": waiting_dir / "receipt.json",
        "ledger": waiting_dir / "ledger.jsonl",
        "state": waiting_dir / "state.json",
        "card": waiting_dir / "card",
        "notify": waiting_dir / "notify",
    }
    waiting_run, waiting = run_notifier(waiting_paths)

    passed_paths = build_terminal_chain(work_dir / "pass", "pass_for_manual_forward_review")
    pass_run, passed = run_notifier(passed_paths)
    pass_repeat_run, pass_repeat = run_notifier(passed_paths)
    pass_state = read_json(passed_paths["state"])

    tombstone_paths = build_terminal_chain(work_dir / "tombstone", "tombstone_review_required")
    tombstone_run, tombstone = run_notifier(tombstone_paths)

    with passed_paths["records"].open("a", encoding="utf-8") as handle:
        handle.write("ETHUSDT,99\n")
    tamper_run, tamper = run_notifier(passed_paths)

    checks = {
        "waiting_sends_nothing": waiting_run["exit_code"] == 0 and waiting.get("decision") == "skipped_waiting_terminal_receipt",
        "waiting_creates_no_card": not waiting_paths["card"].with_suffix(".json").exists(),
        "pass_dry_run_ready": pass_run["exit_code"] == 0 and passed.get("decision") == "dry_run_ready",
        "pass_card_created": passed_paths["card"].with_suffix(".json").is_file(),
        "dry_run_does_not_consume_dedupe": pass_repeat.get("decision") == "dry_run_ready" and not pass_state.get("notified_keys"),
        "tombstone_dry_run_ready": tombstone_run["exit_code"] == 0 and tombstone.get("decision") == "dry_run_ready",
        "tombstone_card_created": tombstone_paths["card"].with_suffix(".json").is_file(),
        "tamper_blocks_notification": tamper_run["exit_code"] == 2 and tamper.get("decision") == "blocked_terminal_receipt_integrity",
        "no_telegram_send_requested": all(
            item.get("send_requested") is False for item in (waiting, passed, pass_repeat, tombstone, tamper)
        ),
    }
    return {
        "tool": "tools/liquidation_force_order_terminal_telegram_drill.py",
        "decision": "force_order_terminal_telegram_drill_passed" if all(checks.values()) else "force_order_terminal_telegram_drill_failed",
        "checks": checks,
        "cases": {
            "waiting": waiting.get("decision"),
            "pass": passed.get("decision"),
            "pass_repeat": pass_repeat.get("decision"),
            "tombstone": tombstone.get("decision"),
            "tamper": tamper.get("decision"),
        },
        "boundary": {
            "synthetic_plumbing_only": True,
            "edge_evidence": False,
            "sends_telegram": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic no-send drill for receipt-gated forceOrder terminal Telegram")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_TERMINAL_TELEGRAM_DRILL_2026-07-12")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="force-order-terminal-telegram-drill-") as temp_name:
        report = build_report(Path(temp_name))
    out = Path(args.out_prefix)
    if not out.is_absolute():
        out = ROOT / out
    write_json(out.with_suffix(".json"), report)
    print(json.dumps({"decision": report["decision"], "checks": report["checks"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if report["decision"] == "force_order_terminal_telegram_drill_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
