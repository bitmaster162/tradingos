#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": str(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": str(path)}


def reset_work_dir(work_dir: Path) -> None:
    resolved_root = ROOT.resolve()
    resolved = work_dir.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise RuntimeError(f"refusing to reset path outside workspace: {resolved}")
    if "_dl" not in resolved.parts or "runtime_drills" not in resolved.parts:
        raise RuntimeError(f"refusing to reset non-drill path: {resolved}")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)


def baseline_forward_observer(lock_id: str) -> dict[str, Any]:
    return {
        "generated_at": now_iso(),
        "tool": "fixture_forward_observer",
        "decision": "bybit_liquidation_forward_observer_pending_resolution",
        "can_trade": False,
        "orders_allowed": False,
        "lock": {"lock_id": lock_id},
        "evidence": {
            "new_event_bars": 3,
            "new_liquidation_events": 66,
            "positive_horizons_after_cost_buffer": 0,
        },
        "blockers": ["fixture_pending_future_horizons"],
    }


def review_pack(lock_id: str, action: str) -> dict[str, Any]:
    action_to_decision = {
        "wait": "bybit_forward_review_pack_waiting_sample",
        "rerun_observer": "bybit_forward_review_pack_ready_to_rerun_observer",
        "manual_pass_review": "bybit_forward_review_pack_manual_pass_review_required",
        "manual_tombstone_review": "bybit_forward_review_pack_manual_tombstone_review_required",
    }
    return {
        "generated_at": now_iso(),
        "tool": "fixture_forward_review_pack",
        "decision": action_to_decision[action],
        "review_action": action,
        "lock_id": lock_id,
        "can_trade": False,
        "orders_allowed": False,
        "progress_summary": {
            "event_bars_current": 15 if action != "wait" else 3,
            "event_bars_required": 15,
            "resolved_records": 15 if action != "wait" else 1,
            "sample_ready": action != "wait",
            "resolution_ready": action != "wait",
        },
    }


def micro_transition(snapshot_ready: bool) -> dict[str, Any]:
    if snapshot_ready:
        return {
            "generated_at": now_iso(),
            "transition_state": "sealed_snapshot_ready_for_train_research_batch",
            "snapshot_id": "fixture_snapshot_001",
            "can_trade": False,
        }
    return {
        "generated_at": now_iso(),
        "transition_state": "microstructure_snapshot_blocked",
        "failed_checks": ["fixture_not_enough_book_coverage"],
        "can_trade": False,
    }


def micro_unblock(snapshot_ready: bool) -> dict[str, Any]:
    if snapshot_ready:
        return {
            "generated_at": now_iso(),
            "decision": "microstructure_snapshot_available",
            "snapshot_id": "fixture_snapshot_001",
            "coverage": {"book_coverage_pct": 97.0},
            "sla": {"cooldown_remaining_minutes": 0},
            "can_trade": False,
        }
    return {
        "generated_at": now_iso(),
        "decision": "microstructure_snapshot_blocked",
        "coverage": {"book_coverage_pct": 92.5},
        "sla": {"cooldown_remaining_minutes": 360},
        "blockers": ["fixture_not_enough_book_coverage"],
        "can_trade": False,
    }


def post_liq_absorption(decision: str, lock_id: str) -> dict[str, Any]:
    if decision == "pass":
        actual_decision = "post_liq_absorption_forward_observer_passed_for_manual_review"
        min_n = 31
        positive_horizons = 2
        blockers: list[str] = []
    elif decision == "tombstone":
        actual_decision = "post_liq_absorption_forward_observer_failed_gate_for_tombstone_review"
        min_n = 31
        positive_horizons = 0
        blockers = ["minimum_positive_horizons"]
    else:
        actual_decision = "post_liq_absorption_forward_observer_waiting_new_events"
        min_n = 0
        positive_horizons = 0
        blockers = ["no_selected_bucket_records_after_lock"]
    return {
        "generated_at": now_iso(),
        "tool": "fixture_post_liq_absorption_forward_observer",
        "decision": actual_decision,
        "can_trade": False,
        "orders_allowed": False,
        "lock": {"lock_id": lock_id},
        "evidence": {
            "selected_bucket_min_n": min_n,
            "positive_horizons": positive_horizons,
            "selected_symbols": ["BTCUSDT", "ETHUSDT"] if min_n else [],
        },
        "blockers": blockers,
    }


def liquidation_timing_vol(decision: str, lock_id: str) -> dict[str, Any]:
    if decision == "pass":
        actual_decision = "liquidation_timing_vol_continuation_forward_passed_for_manual_review"
        min_n = 31
        positive_horizons = 1
        blockers: list[str] = []
    elif decision == "tombstone":
        actual_decision = "liquidation_timing_vol_continuation_forward_failed_gate_for_tombstone_review"
        min_n = 31
        positive_horizons = 0
        blockers = ["minimum_positive_horizons"]
    else:
        actual_decision = "liquidation_timing_vol_continuation_forward_waiting_new_events"
        min_n = 0
        positive_horizons = 0
        blockers = ["no_selected_bucket_records_after_lock"]
    return {
        "generated_at": now_iso(),
        "tool": "fixture_liquidation_timing_vol_forward_observer",
        "decision": actual_decision,
        "can_trade": False,
        "orders_allowed": False,
        "lock": {"lock_id": lock_id},
        "evidence": {
            "selected_bucket_min_n": min_n,
            "positive_horizons": positive_horizons,
            "selected_symbols": ["BTCUSDT", "ETHUSDT"] if min_n else [],
        },
        "blockers": blockers,
    }


def write_scenario_inputs(
    base_dir: Path,
    scenario: str,
    action: str,
    snapshot_ready: bool,
    post_liq_decision: str = "waiting",
    timing_vol_decision: str = "waiting",
) -> dict[str, Path]:
    scenario_dir = base_dir / scenario
    lock_id = f"fixture_lock_{scenario}"
    paths = {
        "forward_observer": scenario_dir / "forward_observer.json",
        "forward_review": scenario_dir / "forward_review.json",
        "post_liq_absorption": scenario_dir / "post_liq_absorption.json",
        "liquidation_timing_vol": scenario_dir / "liquidation_timing_vol.json",
        "microstructure_transition": scenario_dir / "microstructure_transition.json",
        "microstructure_unblock": scenario_dir / "microstructure_unblock.json",
        "state": scenario_dir / "state.json",
        "out_prefix": scenario_dir / "monitor",
    }
    write_json(paths["forward_observer"], baseline_forward_observer(lock_id))
    write_json(paths["forward_review"], review_pack(lock_id, action))
    write_json(paths["post_liq_absorption"], post_liq_absorption(post_liq_decision, lock_id))
    write_json(paths["liquidation_timing_vol"], liquidation_timing_vol(timing_vol_decision, lock_id))
    write_json(paths["microstructure_transition"], micro_transition(snapshot_ready))
    write_json(paths["microstructure_unblock"], micro_unblock(snapshot_ready))
    return paths


def run_monitor(paths: dict[str, Path], timeout_s: int) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "tools" / "real_edge_transition_alert_monitor.py"),
        "--forward-observer",
        str(paths["forward_observer"]),
        "--forward-review",
        str(paths["forward_review"]),
        "--post-liq-absorption-runner",
        str(paths["post_liq_absorption"]),
        "--liquidation-timing-vol-runner",
        str(paths["liquidation_timing_vol"]),
        "--microstructure-transition",
        str(paths["microstructure_transition"]),
        "--microstructure-unblock",
        str(paths["microstructure_unblock"]),
        "--state",
        str(paths["state"]),
        "--out-prefix",
        str(paths["out_prefix"]),
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s, check=False)
    report = read_json(paths["out_prefix"].with_suffix(".json"))
    return {
        "command": [str(item) for item in command],
        "exit_code": result.returncode,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-8000:],
        "report": report,
    }


def event_kinds(report: dict[str, Any]) -> list[str]:
    return [str(item.get("event_kind")) for item in report.get("new_attention_events") or [] if item.get("event_kind")]


def scenario_result(name: str, run: dict[str, Any], expected_decision: str, expected_kinds: list[str]) -> dict[str, Any]:
    report = run.get("report") if isinstance(run.get("report"), dict) else {}
    actual_decision = str(report.get("decision") or "")
    actual_kinds = event_kinds(report)
    checks = {
        "exit_zero": run.get("exit_code") == 0,
        "decision_match": actual_decision == expected_decision,
        "new_attention_kinds_match": sorted(actual_kinds) == sorted(expected_kinds),
        "can_trade_false": report.get("can_trade") is False,
        "orders_disallowed": report.get("orders_allowed") is False,
        "telegram_not_sent": report.get("telegram_decision") in {"not_requested", "send_not_enabled"},
    }
    return {
        "scenario": name,
        "expected_decision": expected_decision,
        "actual_decision": actual_decision,
        "expected_new_attention_kinds": expected_kinds,
        "actual_new_attention_kinds": actual_kinds,
        "checks": checks,
        "passed": all(checks.values()),
        "monitor_report": portable(Path(str(run["report"].get("_path", "")))) if run["report"].get("_read_error") else portable(Path(run["command"][-1]).with_suffix(".json")),
        "stdout_tail": run.get("stdout"),
        "stderr_tail": run.get("stderr"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Real Edge Transition Alert Monitor Drill",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Scenarios passed: `{report['scenarios_passed']}/{report['scenarios_total']}`",
        "",
        "## Boundary",
        "",
        "- Synthetic fixture drill only.",
        "- Writes only under `_dl/runtime_drills/real_edge_transition_alert_monitor` and `docs/`.",
        "- Does not send Telegram.",
        "- Does not create trade signals, paper entries or orders.",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Expected | Actual | Expected kinds | Actual kinds | Passed |",
        "|---|---|---|---|---|---:|",
    ]
    for item in report.get("scenarios", []):
        lines.append(
            f"| `{item['scenario']}` | `{item['expected_decision']}` | `{item['actual_decision']}` | "
            f"`{','.join(item['expected_new_attention_kinds']) or 'none'}` | "
            f"`{','.join(item['actual_new_attention_kinds']) or 'none'}` | `{item['passed']}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            report.get("next_action", ""),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic drill for real-edge transition alert monitor.")
    parser.add_argument("--work-dir", default="_dl/runtime_drills/real_edge_transition_alert_monitor")
    parser.add_argument("--out-prefix", default="docs/REAL_EDGE_TRANSITION_ALERT_MONITOR_DRILL_2026-07-02")
    parser.add_argument("--timeout-s", type=int, default=30)
    args = parser.parse_args()

    work_dir = resolve_path(args.work_dir)
    reset_work_dir(work_dir)

    specs = [
        {
            "name": "wait_no_attention",
            "action": "wait",
            "snapshot_ready": False,
            "expected_decision": "real_edge_transition_no_new_attention_event",
            "expected_kinds": [],
        },
        {
            "name": "rerun_observer_attention",
            "action": "rerun_observer",
            "snapshot_ready": False,
            "expected_decision": "real_edge_transition_attention_required",
            "expected_kinds": ["forward_review_ready_for_observer_rerun"],
        },
        {
            "name": "manual_pass_attention",
            "action": "manual_pass_review",
            "snapshot_ready": False,
            "expected_decision": "real_edge_transition_attention_required",
            "expected_kinds": ["forward_review_manual_pass_review_required"],
        },
        {
            "name": "manual_tombstone_attention",
            "action": "manual_tombstone_review",
            "snapshot_ready": False,
            "expected_decision": "real_edge_transition_attention_required",
            "expected_kinds": ["forward_review_manual_tombstone_review_required"],
        },
        {
            "name": "snapshot_attention",
            "action": "wait",
            "snapshot_ready": True,
            "expected_decision": "real_edge_transition_attention_required",
            "expected_kinds": ["microstructure_snapshot_available"],
        },
        {
            "name": "post_liq_pass_attention",
            "action": "wait",
            "snapshot_ready": False,
            "post_liq_decision": "pass",
            "expected_decision": "real_edge_transition_attention_required",
            "expected_kinds": ["post_liq_absorption_passed_for_manual_review"],
        },
        {
            "name": "post_liq_tombstone_attention",
            "action": "wait",
            "snapshot_ready": False,
            "post_liq_decision": "tombstone",
            "expected_decision": "real_edge_transition_attention_required",
            "expected_kinds": ["post_liq_absorption_failed_gate_for_tombstone_review"],
        },
        {
            "name": "liquidation_timing_vol_pass_attention",
            "action": "wait",
            "snapshot_ready": False,
            "timing_vol_decision": "pass",
            "expected_decision": "real_edge_transition_attention_required",
            "expected_kinds": ["liquidation_timing_vol_passed_for_manual_review"],
        },
        {
            "name": "liquidation_timing_vol_tombstone_attention",
            "action": "wait",
            "snapshot_ready": False,
            "timing_vol_decision": "tombstone",
            "expected_decision": "real_edge_transition_attention_required",
            "expected_kinds": ["liquidation_timing_vol_failed_gate_for_tombstone_review"],
        },
    ]

    results: list[dict[str, Any]] = []
    duplicate_paths: dict[str, Path] | None = None
    for spec in specs:
        paths = write_scenario_inputs(
            work_dir,
            spec["name"],
            spec["action"],
            spec["snapshot_ready"],
            spec.get("post_liq_decision", "waiting"),
            spec.get("timing_vol_decision", "waiting"),
        )
        run = run_monitor(paths, args.timeout_s)
        results.append(scenario_result(spec["name"], run, spec["expected_decision"], spec["expected_kinds"]))
        if spec["name"] == "manual_pass_attention":
            duplicate_paths = paths

    if duplicate_paths is None:
        raise RuntimeError("manual_pass_attention scenario did not initialize duplicate paths")
    duplicate_run = run_monitor(duplicate_paths, args.timeout_s)
    results.append(
        scenario_result(
            "manual_pass_dedup_second_run",
            duplicate_run,
            "real_edge_transition_attention_already_recorded",
            [],
        )
    )

    passed = all(item["passed"] for item in results)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/real_edge_transition_alert_monitor_drill.py",
        "decision": "real_edge_transition_alert_monitor_drill_passed" if passed else "real_edge_transition_alert_monitor_drill_failed",
        "can_trade": False,
        "orders_allowed": False,
        "boundary": {
            "synthetic_fixture_only": True,
            "sends_telegram": False,
            "emits_trade_signals": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "work_dir": portable(work_dir),
        "scenarios_total": len(results),
        "scenarios_passed": sum(1 for item in results if item["passed"]),
        "scenarios": results,
        "next_action": (
            "Transition alert routing is regression-tested; wait for real observer/review readiness."
            if passed
            else "Fix failed transition routing before relying on real-edge readiness alerts."
        ),
    }
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "scenarios_passed": report["scenarios_passed"],
                "scenarios_total": report["scenarios_total"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
