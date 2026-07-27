#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
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


def read_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.exists():
        return {"_missing": portable(p)}
    try:
        payload = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(p)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(p)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def acquire_run_lock(lock_path: str, stale_seconds: int) -> tuple[Path | None, dict[str, Any]]:
    path = resolve_path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        age_s = max(0.0, time.time() - path.stat().st_mtime)
        existing: dict[str, Any]
        try:
            existing_raw = json.loads(path.read_text(encoding="utf-8-sig"))
            existing = existing_raw if isinstance(existing_raw, dict) else {"raw": existing_raw}
        except (OSError, json.JSONDecodeError) as exc:
            existing = {"read_error": str(exc)}
        existing["lock_path"] = portable(path)
        existing["age_s"] = round(age_s, 3)
        if age_s < stale_seconds:
            return None, existing
        path.unlink(missing_ok=True)

    run_id = f"{os.getpid()}-{time.time_ns()}"
    payload = {
        "pid": os.getpid(),
        "run_id": run_id,
        "started_at": now_iso(),
        "tool": "tools/liquidation_real_feed_status_refresh.py",
        "stale_seconds": stale_seconds,
    }
    write_json(path, payload)
    return path, payload


def release_run_lock(lock_path: Path | None, run_id: str | None) -> None:
    if lock_path is None or not run_id:
        return
    try:
        current = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(current, dict) and current.get("run_id") == run_id:
        lock_path.unlink(missing_ok=True)


def run_command(name: str, command: list[str], timeout_s: int) -> dict[str, Any]:
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "command": command,
            "status": "timeout",
            "exit_code": None,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }
    return {
        "name": name,
        "command": command,
        "status": "success" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def report_path(prefix: str) -> str:
    return portable(resolve_path(prefix).with_suffix(".json"))


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def source_report_freshness(
    reports: dict[str, dict[str, Any]], *, reference: datetime, maximum_age_minutes: float
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    freshness: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for name, payload in reports.items():
        generated = parse_utc(payload.get("generated_at"))
        age = (reference - generated).total_seconds() / 60.0 if generated is not None else None
        fresh = age is not None and -5.0 <= age <= maximum_age_minutes
        freshness[name] = {
            "generated_at": payload.get("generated_at"),
            "age_minutes": round(age, 3) if age is not None else None,
            "maximum_age_minutes": maximum_age_minutes,
            "fresh": fresh,
        }
        if not fresh:
            reason = "missing_or_invalid_timestamp" if age is None else "future" if age < -5.0 else "stale"
            blockers.append(f"{reason}_{name}_report")
    return freshness, blockers


def render_markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    lines = [
        "# Liquidation Real-Feed Status Refresh",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "",
        "## Evidence",
        "",
        f"- Binance forceOrder: `{evidence['binance_decision']}`, events `{evidence['binance_events']}`",
        f"- Bybit sample gate: `{evidence['bybit_sample_decision']}`, events `{evidence['bybit_events']}`, bars `{evidence['bybit_event_bars']}`",
        f"- Coverage: `{evidence['coverage_decision']}`",
        f"- Arrival-time readiness: `{evidence.get('arrival_time_decision')}`; overlap `{evidence.get('arrival_time_overlap_seconds')}`s; shared symbols `{evidence.get('arrival_time_shared_symbols')}`",
        f"- Real-edge matrix: `{evidence['real_edge_decision']}` / liquidation `{evidence['real_edge_liquidation_decision']}`",
        f"- Autopilot: `{evidence['autopilot_decision']}`",
        f"- Sample progress: `{evidence.get('sample_progress_decision')}`; velocity `{evidence.get('sample_progress_velocity')}`",
        f"- Sample ready trigger: `{evidence.get('sample_ready_trigger_decision')}`",
        f"- Forward observer: `{evidence.get('forward_observer_decision')}`; new bars `{evidence.get('forward_observer_new_event_bars')}`; positive horizons `{evidence.get('forward_observer_positive_horizons')}`",
        f"- Forward progress: `{evidence.get('forward_progress_decision')}`; max horizon deficit `{evidence.get('forward_progress_max_horizon_deficit')}`; ETA `{evidence.get('forward_progress_eta_hours')}`h",
        f"- Forward review pack: `{evidence.get('forward_review_decision')}`; action `{evidence.get('forward_review_action')}`",
        f"- Transition alert monitor: `{evidence.get('transition_alert_decision')}`; new events `{evidence.get('transition_alert_new_events')}`; telegram `{evidence.get('transition_alert_telegram_decision')}`",
        f"- Price tail gap fill: spot `{evidence.get('spot_tail_gap_decision')}`, futures `{evidence.get('futures_tail_gap_decision')}`",
        "",
        "## Steps",
        "",
        "| Step | Status | Exit | Duration s |",
        "|---|---|---:|---:|",
    ]
    for step in report["steps"]:
        lines.append(f"| `{step['name']}` | `{step['status']}` | `{step['exit_code']}` | `{step['duration_s']}` |")
    lines.extend(["", "## Blockers", ""])
    for blocker in report["blockers"] or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Status refresh only.",
            "- Runs data-quality, sample gate, coverage and readiness reports.",
            "- Does not emit trading signals, open paper entries, send Telegram alerts or place orders.",
            "- `can_trade=false` is preserved.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    py = sys.executable

    if args.refresh_collectors:
        steps.append(
            run_command(
                "binance_force_order_data_quality",
                [
                    py,
                    "tools/liquidation_force_order_data_quality.py",
                    "--out-prefix",
                    args.binance_data_quality_prefix,
                ],
                args.timeout_seconds,
            )
        )
        if args.refresh_price_tail:
            steps.append(
                run_command(
                    "binance_rest_spot_tail_gap_fill",
                    [
                        py,
                        "tools/binance_rest_kline_tail_gap_filler.py",
                        "--market",
                        "spot",
                        "--symbols",
                        args.price_tail_symbols,
                        "--interval",
                        args.price_tail_interval,
                        "--out-prefix",
                        args.spot_tail_gap_prefix,
                    ],
                    args.timeout_seconds,
                )
            )
            steps.append(
                run_command(
                    "binance_rest_futures_tail_gap_fill",
                    [
                        py,
                        "tools/binance_rest_kline_tail_gap_filler.py",
                        "--market",
                        "futures",
                        "--symbols",
                        args.price_tail_symbols,
                        "--interval",
                        args.price_tail_interval,
                        "--out-prefix",
                        args.futures_tail_gap_prefix,
                    ],
                    args.timeout_seconds,
                )
            )
        steps.append(
            run_command(
                "bybit_all_liquidation_watchdog",
                [
                    py,
                    "tools/bybit_all_liquidation_collector_watchdog.py",
                    "--out-prefix",
                    args.bybit_watchdog_prefix,
                    "--sample-gate-prefix",
                    args.bybit_sample_gate_prefix,
                ],
                args.timeout_seconds,
            )
        )

    steps.append(
        run_command(
            "liquidation_multi_venue_coverage_summary",
            [
                py,
                "tools/liquidation_multi_venue_coverage_summary.py",
                "--binance-data-quality",
                report_path(args.binance_data_quality_prefix),
                "--bybit-data-quality",
                report_path(args.bybit_data_quality_prefix),
                "--out-prefix",
                args.coverage_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "liquidation_cross_venue_arrival_time_readiness",
            [
                py,
                "tools/liquidation_cross_venue_arrival_time_readiness.py",
                "--config",
                args.arrival_time_contract,
                "--out-prefix",
                args.arrival_time_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "real_edge_readiness_matrix",
            [
                py,
                "tools/real_edge_readiness_matrix.py",
                "--liquidation-coverage",
                report_path(args.coverage_prefix),
                "--out-prefix",
                args.real_edge_matrix_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "real_edge_autopilot_guard",
            [
                py,
                "tools/real_edge_autopilot_guard.py",
                "--out-prefix",
                args.real_edge_autopilot_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "liquidation_sample_progress_monitor",
            [
                py,
                "tools/liquidation_sample_progress_monitor.py",
                "--sample-gate",
                report_path(args.bybit_sample_gate_prefix),
                "--coverage",
                report_path(args.coverage_prefix),
                "--out-prefix",
                args.sample_progress_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "liquidation_sample_ready_trigger",
            [
                py,
                "tools/liquidation_sample_ready_trigger.py",
                "--progress",
                report_path(args.sample_progress_prefix),
                "--out-prefix",
                args.sample_ready_trigger_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "bybit_liquidation_forward_observer",
            [
                py,
                "tools/bybit_liquidation_forward_observer.py",
                "--lock",
                args.forward_observer_lock,
                "--out-prefix",
                args.forward_observer_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "bybit_liquidation_forward_progress_monitor",
            [
                py,
                "tools/bybit_liquidation_forward_progress_monitor.py",
                "--forward-observer",
                report_path(args.forward_observer_prefix),
                "--lock",
                args.forward_observer_lock,
                "--out-prefix",
                args.forward_progress_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "bybit_liquidation_forward_review_pack",
            [
                py,
                "tools/bybit_liquidation_forward_review_pack.py",
                "--forward-observer",
                report_path(args.forward_observer_prefix),
                "--progress",
                report_path(args.forward_progress_prefix),
                "--lock",
                args.forward_observer_lock,
                "--out-prefix",
                args.forward_review_prefix,
            ],
            args.timeout_seconds,
        )
    )
    steps.append(
        run_command(
            "real_edge_transition_alert_monitor",
            [
                py,
                "tools/real_edge_transition_alert_monitor.py",
                "--forward-observer",
                report_path(args.forward_observer_prefix),
                "--forward-review",
                report_path(args.forward_review_prefix),
                "--out-prefix",
                args.transition_alert_prefix,
            ],
            args.timeout_seconds,
        )
    )

    binance = read_json(report_path(args.binance_data_quality_prefix))
    bybit_sample = read_json(report_path(args.bybit_sample_gate_prefix))
    coverage = read_json(report_path(args.coverage_prefix))
    arrival_time = read_json(report_path(args.arrival_time_prefix))
    matrix = read_json(report_path(args.real_edge_matrix_prefix))
    autopilot = read_json(report_path(args.real_edge_autopilot_prefix))
    progress = read_json(report_path(args.sample_progress_prefix))
    sample_trigger = read_json(report_path(args.sample_ready_trigger_prefix))
    forward_observer = read_json(report_path(args.forward_observer_prefix))
    forward_progress = read_json(report_path(args.forward_progress_prefix))
    forward_review = read_json(report_path(args.forward_review_prefix))
    transition_alert = read_json(report_path(args.transition_alert_prefix))
    spot_tail_gap = read_json(report_path(args.spot_tail_gap_prefix))
    futures_tail_gap = read_json(report_path(args.futures_tail_gap_prefix))

    bybit_evidence = bybit_sample.get("evidence") if isinstance(bybit_sample.get("evidence"), dict) else {}
    observer_evidence = forward_observer.get("evidence") if isinstance(forward_observer.get("evidence"), dict) else {}
    forward_progress_horizons = forward_progress.get("horizon_progress")
    if not isinstance(forward_progress_horizons, dict):
        forward_progress_horizons = {}
    forward_progress_deficits = [
        item.get("deficit")
        for item in forward_progress_horizons.values()
        if isinstance(item, dict) and isinstance(item.get("deficit"), (int, float))
    ]
    forward_progress_velocity = forward_progress.get("velocity") if isinstance(forward_progress.get("velocity"), dict) else {}
    transition_new_events = transition_alert.get("new_attention_events")
    if not isinstance(transition_new_events, list):
        transition_new_events = []
    matrix_liq = matrix.get("liquidation") if isinstance(matrix.get("liquidation"), dict) else {}
    arrival_cross = arrival_time.get("cross_venue") if isinstance(arrival_time.get("cross_venue"), dict) else {}
    failed_steps = [step for step in steps if step.get("exit_code") != 0]
    report_freshness, report_freshness_blockers = source_report_freshness(
        {"binance_data_quality": binance, "bybit_sample_gate": bybit_sample},
        reference=datetime.now(timezone.utc),
        maximum_age_minutes=float(args.max_source_report_age_minutes),
    )
    blockers = list(bybit_sample.get("blockers") or [])
    if failed_steps:
        blockers.append("status_refresh_step_failed")
    blockers.extend(report_freshness_blockers)
    if autopilot.get("can_trade") is not False or matrix.get("can_trade") is not False:
        blockers.append("unexpected_can_trade_boundary")

    if failed_steps or report_freshness_blockers:
        decision = "liquidation_real_feed_status_refresh_degraded"
        next_action = (
            "rerun with --refresh-collectors and inspect failed steps before relying on readiness reports"
            if report_freshness_blockers
            else "inspect failed refresh steps before relying on readiness reports"
        )
    elif bybit_sample.get("decision") == "bybit_liquidation_sample_ready_for_manual_review":
        decision = "liquidation_real_feed_sample_ready_for_manual_review"
        next_action = "manual review of fixed-horizon liquidation event study; do not promote automatically"
    else:
        decision = "liquidation_real_feed_collecting_sample"
        next_action = "keep collectors and watchdog loops running until sample and context-balance blockers clear"

    return {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_real_feed_status_refresh.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "boundary": {
            "status_refresh_only": True,
            "research_only": True,
            "emits_signals": False,
            "opens_paper_entries": False,
            "sends_alerts": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "steps": steps,
        "evidence": {
            "binance_decision": binance.get("decision"),
            "binance_events": binance.get("events", {}).get("events") if isinstance(binance.get("events"), dict) else None,
            "bybit_sample_decision": bybit_sample.get("decision"),
            "bybit_events": bybit_evidence.get("events"),
            "bybit_event_bars": bybit_evidence.get("aggregate_rows"),
            "bybit_contexts": bybit_evidence.get("contexts"),
            "coverage_decision": coverage.get("decision"),
            "arrival_time_decision": arrival_time.get("decision"),
            "arrival_time_overlap_seconds": arrival_cross.get("overlapping_receipt_span_seconds"),
            "arrival_time_shared_symbols": arrival_cross.get("shared_symbol_count"),
            "real_edge_decision": matrix.get("decision"),
            "real_edge_liquidation_decision": matrix_liq.get("decision"),
            "autopilot_decision": autopilot.get("decision"),
            "sample_progress_decision": progress.get("decision"),
            "sample_progress_velocity": progress.get("velocity"),
            "sample_ready_trigger_decision": sample_trigger.get("decision"),
            "forward_observer_decision": forward_observer.get("decision"),
            "forward_observer_new_event_bars": observer_evidence.get("new_event_bars"),
            "forward_observer_new_liquidation_events": observer_evidence.get("new_liquidation_events"),
            "forward_observer_positive_horizons": observer_evidence.get("positive_horizons_after_cost_buffer"),
            "forward_progress_decision": forward_progress.get("decision"),
            "forward_progress_max_horizon_deficit": max(forward_progress_deficits) if forward_progress_deficits else None,
            "forward_progress_eta_hours": forward_progress_velocity.get("estimated_hours_to_horizon_resolution"),
            "forward_review_decision": forward_review.get("decision"),
            "forward_review_action": forward_review.get("review_action"),
            "transition_alert_decision": transition_alert.get("decision"),
            "transition_alert_new_events": [event.get("event_kind") for event in transition_new_events if isinstance(event, dict)],
            "transition_alert_telegram_decision": transition_alert.get("telegram_decision"),
            "spot_tail_gap_decision": spot_tail_gap.get("decision"),
            "futures_tail_gap_decision": futures_tail_gap.get("decision"),
            "source_report_freshness": report_freshness,
        },
        "blockers": sorted(set(str(item) for item in blockers)),
        "source_reports": {
            "binance_data_quality": report_path(args.binance_data_quality_prefix),
            "bybit_sample_gate": report_path(args.bybit_sample_gate_prefix),
            "coverage": report_path(args.coverage_prefix),
            "arrival_time_readiness": report_path(args.arrival_time_prefix),
            "real_edge_matrix": report_path(args.real_edge_matrix_prefix),
            "real_edge_autopilot": report_path(args.real_edge_autopilot_prefix),
            "sample_progress": report_path(args.sample_progress_prefix),
            "sample_ready_trigger": report_path(args.sample_ready_trigger_prefix),
            "forward_observer": report_path(args.forward_observer_prefix),
            "forward_progress": report_path(args.forward_progress_prefix),
            "forward_review": report_path(args.forward_review_prefix),
            "transition_alert": report_path(args.transition_alert_prefix),
            "spot_tail_gap_fill": report_path(args.spot_tail_gap_prefix),
            "futures_tail_gap_fill": report_path(args.futures_tail_gap_prefix),
        },
        "next_action": next_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh liquidation real-feed status chain: collectors, sample gate, coverage, readiness.")
    parser.add_argument("--binance-data-quality-prefix", default="docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_ALL_MARKET_CHECK_2026-07-01")
    parser.add_argument("--bybit-data-quality-prefix", default="docs/BYBIT_ALL_LIQUIDATION_DATA_QUALITY_2026-07-01")
    parser.add_argument("--bybit-watchdog-prefix", default="docs/BYBIT_ALL_LIQUIDATION_COLLECTOR_WATCHDOG_2026-07-01")
    parser.add_argument("--bybit-sample-gate-prefix", default="docs/BYBIT_ALL_LIQUIDATION_SAMPLE_GATE_2026-07-02_AFTER_PRICE_GAP_FILL_EXPLICIT")
    parser.add_argument("--coverage-prefix", default="docs/LIQUIDATION_MULTI_VENUE_COVERAGE_SUMMARY_2026-07-02_AFTER_PRICE_GAP_FILL")
    parser.add_argument("--arrival-time-contract", default="configs/LIQUIDATION_CROSS_VENUE_ARRIVAL_TIME_CONTRACT_2026-07-13.json")
    parser.add_argument("--arrival-time-prefix", default="docs/LIQUIDATION_CROSS_VENUE_ARRIVAL_TIME_READINESS_2026-07-13")
    parser.add_argument("--real-edge-matrix-prefix", default="docs/REAL_EDGE_READINESS_MATRIX_2026-07-02_AFTER_PRICE_GAP_FILL")
    parser.add_argument("--real-edge-autopilot-prefix", default="docs/REAL_EDGE_AUTOPILOT_GUARD_2026-07-02_AFTER_PRICE_GAP_FILL")
    parser.add_argument("--sample-progress-prefix", default="docs/LIQUIDATION_SAMPLE_PROGRESS_2026-07-02_AFTER_PRICE_GAP_FILL")
    parser.add_argument("--sample-ready-trigger-prefix", default="docs/LIQUIDATION_SAMPLE_READY_TRIGGER_2026-07-02_AFTER_PRICE_GAP_FILL")
    parser.add_argument("--forward-observer-lock", default="configs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_LOCK_2026-07-02.json")
    parser.add_argument("--forward-observer-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_OBSERVER_2026-07-02")
    parser.add_argument("--forward-progress-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_PROGRESS_2026-07-02")
    parser.add_argument("--forward-review-prefix", default="docs/BYBIT_LIQUIDATION_FORWARD_REVIEW_PACK_2026-07-02")
    parser.add_argument("--transition-alert-prefix", default="docs/REAL_EDGE_TRANSITION_ALERT_MONITOR_2026-07-02")
    parser.add_argument("--price-tail-symbols", default="ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--price-tail-interval", default="1h")
    parser.add_argument("--spot-tail-gap-prefix", default="docs/BINANCE_REST_SPOT_TAIL_GAP_FILL_2026-07-02")
    parser.add_argument("--futures-tail-gap-prefix", default="docs/BINANCE_REST_FUTURES_TAIL_GAP_FILL_2026-07-02")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_REAL_FEED_STATUS_REFRESH_2026-07-02_AFTER_PRICE_GAP_FILL")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--max-source-report-age-minutes", type=float, default=30.0)
    parser.add_argument("--refresh-collectors", action="store_true")
    parser.add_argument("--refresh-price-tail", action="store_true")
    parser.add_argument("--run-lock-path", default="logs/liquidation_real_feed/liquidation_real_feed_status_refresh_run.lock.json")
    parser.add_argument("--run-lock-stale-seconds", type=int, default=1800)
    args = parser.parse_args()

    lock_path, lock_payload = acquire_run_lock(args.run_lock_path, args.run_lock_stale_seconds)
    if lock_path is None:
        print(
            json.dumps(
                {
                    "decision": "liquidation_real_feed_status_refresh_skipped_existing_run",
                    "existing_lock": lock_payload,
                    "can_trade": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    try:
        report = build_report(args)
        out = resolve_path(args.out_prefix)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out.with_suffix(".json"), report)
        out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        print(
            json.dumps(
                {
                    "decision": report["decision"],
                    "binance_events": report["evidence"]["binance_events"],
                    "bybit_events": report["evidence"]["bybit_events"],
                    "bybit_bars": report["evidence"]["bybit_event_bars"],
                    "forward_observer": report["evidence"]["forward_observer_decision"],
                    "forward_new_bars": report["evidence"]["forward_observer_new_event_bars"],
                    "blockers": report["blockers"],
                    "out": portable(out.with_suffix(".json")),
                    "can_trade": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if report["decision"] != "liquidation_real_feed_status_refresh_degraded" else 1
    finally:
        release_run_lock(lock_path, lock_payload.get("run_id"))


if __name__ == "__main__":
    raise SystemExit(main())
