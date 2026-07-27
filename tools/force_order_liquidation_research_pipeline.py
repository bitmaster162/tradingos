#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESEARCH_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT"
DEFAULT_PREREG_LOCK = "configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json"


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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(path)}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_descriptor(value: str | Path | None) -> dict[str, Any] | None:
    if not value:
        return None
    path = resolve_path(value)
    return {
        "path": portable(path),
        "exists": path.is_file(),
        "sha256": sha256_file(path),
        "size": path.stat().st_size if path.is_file() else None,
    }


def locked_study(lock: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    fixed = lock.get("fixed_study") if isinstance(lock.get("fixed_study"), dict) else {}
    hypothesis = lock.get("hypothesis") if isinstance(lock.get("hypothesis"), dict) else {}
    evaluation = lock.get("evaluation_gate") if isinstance(lock.get("evaluation_gate"), dict) else {}
    symbols = [str(item).upper() for item in fixed.get("symbols", []) if str(item).strip()] if isinstance(fixed.get("symbols"), list) else []
    horizons = sorted({int(item) for item in fixed.get("horizons_bars", [])}) if isinstance(fixed.get("horizons_bars"), list) else []
    params = {
        "symbols": symbols,
        "interval": str(fixed.get("interval") or ""),
        "signal_time": str(fixed.get("signal_time") or ""),
        "entry_time": str(fixed.get("entry_time") or ""),
        "return_measurement": str(fixed.get("return_measurement") or ""),
        "horizons": horizons,
        "event_start_at": str(fixed.get("event_start_at") or ""),
        "min_events_for_research": int(fixed.get("minimum_events") or 0),
        "min_event_bars_for_research": int(fixed.get("minimum_event_bars") or 0),
        "min_context_bars": int(fixed.get("minimum_context_bars") or 0),
        "primary_metric": str(hypothesis.get("primary_metric") or ""),
        "primary_horizon_bars": int(hypothesis.get("primary_horizon_bars") or 0),
        "cost_buffer_bps": float(evaluation.get("cost_buffer_bps") or 0.0),
        "cluster_key": str(evaluation.get("cluster_key") or ""),
        "cluster_hours": int(evaluation.get("cluster_hours") or 0),
        "cluster_aggregation": str(evaluation.get("cluster_aggregation") or ""),
        "bootstrap_method": str(evaluation.get("bootstrap_method") or ""),
        "bootstrap_iterations": int(evaluation.get("bootstrap_iterations") or 0),
        "bootstrap_seed": int(evaluation.get("bootstrap_seed") or 0),
        "confidence_level": float(evaluation.get("confidence_level") or 0.0),
        "primary_cluster_ci_lower_must_exceed_bps": float(evaluation.get("primary_cluster_ci_lower_must_exceed_bps") or 0.0),
        "minimum_positive_horizons_after_cost": int(evaluation.get("minimum_positive_horizons_after_cost") or 0),
        "primary_winrate_must_exceed_pct": float(evaluation.get("primary_winrate_must_exceed_pct") or 0.0),
        "minimum_symbols_with_events": int(evaluation.get("minimum_symbols_with_events") or 0),
        "min_independent_4h_blocks": int(evaluation.get("minimum_independent_4h_blocks") or 0),
        "terminal_pass_decision": str(evaluation.get("terminal_pass_decision") or ""),
        "terminal_fail_decision": str(evaluation.get("terminal_fail_decision") or ""),
    }
    if lock.get("status") != "accepted_preregistered_research_only":
        errors.append("lock_status_not_accepted")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        errors.append("lock_boundary_not_false")
    if fixed.get("source") != "binance_usdm_forceOrder_websocket":
        errors.append("lock_source_not_approved")
    if not symbols:
        errors.append("lock_symbols_missing")
    if params["interval"] != "1h":
        errors.append("lock_interval_not_1h")
    if params["signal_time"] != "event_bar_close":
        errors.append("lock_signal_time_invalid")
    if params["entry_time"] != "next_bar_open":
        errors.append("lock_entry_time_invalid")
    if params["return_measurement"] != "next_bar_open_to_horizon_close":
        errors.append("lock_return_measurement_invalid")
    if not horizons or any(item <= 0 for item in horizons):
        errors.append("lock_horizons_invalid")
    if not params["event_start_at"]:
        errors.append("lock_event_start_missing")
    if any(params[key] <= 0 for key in ("min_events_for_research", "min_event_bars_for_research", "min_context_bars")):
        errors.append("lock_minimums_invalid")
    if params["min_independent_4h_blocks"] <= 0:
        errors.append("lock_independence_minimum_invalid")
    if params["primary_metric"] != "reversal_return_bps" or params["primary_horizon_bars"] not in horizons:
        errors.append("lock_primary_hypothesis_invalid")
    if params["cost_buffer_bps"] < 0:
        errors.append("lock_cost_buffer_invalid")
    if params["cluster_key"] != "market_wide_nonoverlap_4h_block_from_event_bar" or params["cluster_hours"] != 4:
        errors.append("lock_cluster_contract_invalid")
    if params["cluster_aggregation"] != "mean_reversal_return_after_cost_within_block":
        errors.append("lock_cluster_aggregation_invalid")
    if params["bootstrap_method"] != "nonparametric_cluster_resample_with_replacement":
        errors.append("lock_bootstrap_method_invalid")
    if params["bootstrap_iterations"] < 1000 or not 0.0 < params["confidence_level"] < 1.0:
        errors.append("lock_bootstrap_contract_invalid")
    if not 1 <= params["minimum_positive_horizons_after_cost"] <= len(horizons):
        errors.append("lock_positive_horizons_invalid")
    if not 0.0 <= params["primary_winrate_must_exceed_pct"] < 100.0:
        errors.append("lock_primary_winrate_invalid")
    if params["minimum_symbols_with_events"] <= 0:
        errors.append("lock_symbol_minimum_invalid")
    if evaluation.get("primary_mean_after_cost_must_be_positive") is not True:
        errors.append("lock_primary_mean_gate_invalid")
    if evaluation.get("primary_winrate_unit") != "independent_4h_block_mean_after_cost":
        errors.append("lock_primary_winrate_unit_invalid")
    if evaluation.get("minimum_symbols_with_events_scope") != "each_horizon":
        errors.append("lock_symbol_scope_invalid")
    if evaluation.get("minimum_independent_4h_blocks_scope") != "each_horizon":
        errors.append("lock_independence_scope_invalid")
    if params["terminal_pass_decision"] != "pass_for_manual_forward_review" or params["terminal_fail_decision"] != "tombstone_review_required":
        errors.append("lock_terminal_decisions_invalid")
    if evaluation.get("no_parameter_changes") is not True or evaluation.get("no_pooling_with_pre_lock_events") is not True:
        errors.append("lock_anti_retune_boundary_invalid")
    if evaluation.get("manual_review_before_any_forward_observer") is not True or evaluation.get("paper_entries_allowed") is not False:
        errors.append("lock_review_boundary_invalid")
    return params, errors


def override_mismatches(args: argparse.Namespace, params: dict[str, Any]) -> list[str]:
    requested_symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    requested_horizons = sorted({int(item.strip()) for item in args.horizons.split(",") if item.strip()})
    checks = {
        "symbols": requested_symbols == params["symbols"],
        "interval": args.interval == params["interval"],
        "horizons": requested_horizons == params["horizons"],
        "min_events_for_research": args.min_events_for_research == params["min_events_for_research"],
        "min_event_bars_for_research": args.min_event_bars_for_research == params["min_event_bars_for_research"],
        "min_context_bars": args.min_context_bars == params["min_context_bars"],
    }
    return [f"cli_override_mismatch:{name}" for name, passed in checks.items() if not passed]


def event_study_allowed(intake: dict[str, Any]) -> bool:
    return bool(
        intake.get("decision") == "force_order_context_ready_for_preregistered_research"
        and intake.get("aggregate_csv")
    )


def run_command(command: list[str], timeout_s: int) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }


def pipeline_decision(
    intake: dict[str, Any],
    event_study: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    intake_run: dict[str, Any],
    event_run: dict[str, Any] | None,
    evaluation_run: dict[str, Any] | None,
) -> tuple[str, str]:
    if intake_run.get("exit_code") != 0:
        return "force_order_pipeline_blocked_intake_failed", "fix intake runtime before any research use"
    intake_decision = str(intake.get("decision") or "")
    if intake_decision == "waiting_for_real_force_order_events":
        return "force_order_pipeline_waiting_real_events", "keep collector running until real forceOrder rows arrive"
    if intake_decision.startswith("collecting_"):
        return "force_order_pipeline_collecting_sample", "continue collecting until preregistered event/bar minimums are met"
    if intake_decision == "blocked_force_order_context_no_matching_price_bars":
        return "force_order_pipeline_blocked_missing_price_bars", "refresh matching OHLCV cache before event study"
    if event_run is None:
        return "force_order_pipeline_waiting_context_csv", "rerun intake after aggregate context CSV is produced"
    if event_run.get("exit_code") != 0:
        return "force_order_pipeline_blocked_event_study_failed", "fix event-study runtime before any research use"
    event_decision = str((event_study or {}).get("decision") or "")
    if event_decision == "force_order_event_study_ready_for_review":
        if evaluation_run is None:
            return "force_order_pipeline_blocked_missing_cluster_evaluator", "run the locked cluster evaluator before interpreting outcomes"
        if evaluation_run.get("exit_code") != 0:
            return "force_order_pipeline_blocked_cluster_evaluator_failed", "repair evaluator provenance or runtime; do not interpret outcomes"
        evaluation_decision = str((evaluation or {}).get("decision") or "")
        if evaluation_decision == "pass_for_manual_forward_review":
            return "force_order_pipeline_pass_for_manual_forward_review", "manual forward-review only; no automatic promotion"
        if evaluation_decision == "tombstone_review_required":
            return "force_order_pipeline_tombstone_review_required", "record the failed preregistered hypothesis without retuning this sample"
        if evaluation_decision == "force_order_cluster_evaluator_waiting_independent_sample":
            return "force_order_pipeline_waiting_cluster_evaluator_sample", "keep collecting untouched independent 4h blocks"
        if evaluation_decision == "force_order_cluster_evaluator_integrity_blocked":
            return "force_order_pipeline_blocked_cluster_evaluator_integrity", "repair evaluator provenance before any result review"
        return "force_order_pipeline_blocked_unknown_evaluator_decision", "manual integrity review required"
    if "waiting" in event_decision or "collecting" in event_decision:
        return "force_order_pipeline_waiting_event_study_sample", "keep collecting real forceOrder rows until event-study minimums are met"
    if "blocked" in event_decision:
        return "force_order_pipeline_blocked_event_study_data", "fix event-study data requirements before research review"
    return "force_order_pipeline_research_only_review_needed", "manual review required; do not promote automatically"


def render_markdown(report: dict[str, Any]) -> str:
    intake_summary = report.get("intake", {}).get("summary", {})
    event_summary = report.get("event_study", {}).get("summary", {}) if report.get("event_study") else {}
    evaluation = report.get("evaluation") or {}
    lines = [
        "# ForceOrder Liquidation Research Pipeline",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        "",
        "## Boundary",
        "",
        "- Runs local research plumbing only: intake, fixed-horizon event study, then locked cluster evaluation.",
        "- Reads approved Binance USD-M forceOrder rows only through the intake script.",
        "- Does not create alerts, intents, paper entries or orders.",
        "- Does not optimize parameters or promote strategies.",
        "",
        "## Intake",
        "",
        f"- Decision: `{report.get('intake', {}).get('decision')}`",
        f"- Events: `{intake_summary.get('events')}`",
        f"- Event bars: `{intake_summary.get('event_bars')}`",
        f"- Matched event bars: `{intake_summary.get('matched_event_bars')}`",
        f"- Aggregate CSV: `{report.get('intake', {}).get('aggregate_csv')}`",
        "",
        "## Event Study",
        "",
        f"- Decision: `{report.get('event_study', {}).get('decision') if report.get('event_study') else None}`",
        f"- Context rows: `{event_summary.get('context_rows')}`",
        f"- Event-study records: `{event_summary.get('event_study_records')}`",
        "",
        "## Cluster Evaluation",
        "",
        f"- Decision: `{evaluation.get('decision')}`",
        f"- Sample ready: `{(evaluation.get('evaluation') or {}).get('sample_ready')}`",
        "- Costs, 4H clustering and bootstrap rules come only from the preregistration lock.",
        "",
        "## Next Action",
        "",
        f"- {report['next_action']}",
        "",
    ]
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    out_prefix = resolve_path(args.out_prefix)
    intake_prefix = out_prefix.with_name(out_prefix.name + "_INTAKE")
    event_prefix = out_prefix.with_name(out_prefix.name + "_EVENT_STUDY")
    evaluation_prefix = out_prefix.with_name(out_prefix.name + "_EVALUATION")
    lock_path = resolve_path(args.prereg_lock)
    lock = read_json(lock_path)
    params, lock_errors = locked_study(lock)
    lock_errors.extend(override_mismatches(args, params) if not lock_errors else [])
    if lock_errors:
        return {
            "generated_at": now_iso(),
            "tool": "tools/force_order_liquidation_research_pipeline.py",
            "decision": "force_order_pipeline_blocked_preregistration_lock",
            "can_trade": False,
            "boundary": {"research_only": True, "sends_orders": False, "uses_private_credentials": False, "can_trade": False},
            "preregistration": {
                "path": portable(lock_path),
                "lock_id": lock.get("lock_id"),
                "sha256": sha256_file(lock_path),
                "errors": lock_errors,
            },
            "inputs": params,
            "runs": {"intake": None, "event_study": None, "evaluation": None},
            "artifacts": {
                "intake_report": None,
                "event_study_report": None,
                "event_records_csv": None,
                "evaluation_report": None,
            },
            "intake": {},
            "event_study": None,
            "evaluation": None,
            "next_action": "repair the immutable preregistration contract; do not run an unlocked event study",
        }

    intake_command = [
        sys.executable,
        str(ROOT / "tools" / "force_order_liquidation_context_intake.py"),
        "--data-dir",
        args.data_dir,
        "--symbols",
        ",".join(params["symbols"]),
        "--interval",
        params["interval"],
        "--event-start-at",
        params["event_start_at"],
        "--min-events-for-research",
        str(params["min_events_for_research"]),
        "--min-event-bars-for-research",
        str(params["min_event_bars_for_research"]),
        "--out-prefix",
        portable(intake_prefix),
    ]
    intake_run = run_command(intake_command, timeout_s=args.timeout_seconds)
    intake = read_json(intake_prefix.with_suffix(".json"))

    event_run = None
    event_study = None
    evaluation_run = None
    evaluation = None
    aggregate_csv = intake.get("aggregate_csv")
    if event_study_allowed(intake):
        event_command = [
            sys.executable,
            str(ROOT / "tools" / "force_order_liquidation_event_study.py"),
            "--context-csv",
            str(aggregate_csv),
            "--symbols",
            ",".join(params["symbols"]),
            "--interval",
            params["interval"],
            "--horizons",
            ",".join(str(item) for item in params["horizons"]),
            "--min-event-bars",
            str(params["min_event_bars_for_research"]),
            "--min-context-bars",
            str(params["min_context_bars"]),
            "--out-prefix",
            portable(event_prefix),
        ]
        event_run = run_command(event_command, timeout_s=args.timeout_seconds)
        event_study = read_json(event_prefix.with_suffix(".json"))

    if event_study and event_study.get("decision") == "force_order_event_study_ready_for_review":
        records_csv = str((event_study.get("artifacts") or {}).get("records_csv") or "")
        evaluation_command = [
            sys.executable,
            str(ROOT / "tools" / "liquidation_force_order_cluster_evaluator.py"),
            "--prereg-lock",
            portable(lock_path),
            "--event-study-report",
            portable(event_prefix.with_suffix(".json")),
            "--records-csv",
            records_csv,
            "--out-prefix",
            portable(evaluation_prefix),
        ]
        evaluation_run = run_command(evaluation_command, timeout_s=args.timeout_seconds)
        evaluation = read_json(evaluation_prefix.with_suffix(".json"))

    decision, next_action = pipeline_decision(
        intake,
        event_study,
        evaluation,
        intake_run,
        event_run,
        evaluation_run,
    )
    event_records_csv = str((event_study.get("artifacts") or {}).get("records_csv") or "") if event_study else ""
    return {
        "generated_at": now_iso(),
        "tool": "tools/force_order_liquidation_research_pipeline.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "research_only": True,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "preregistration": {
            "path": portable(lock_path),
            "lock_id": lock.get("lock_id"),
            "sha256": sha256_file(lock_path),
            "errors": [],
        },
        "inputs": {
            "data_dir": args.data_dir,
            **params,
        },
        "runs": {
            "intake": intake_run,
            "event_study": event_run,
            "evaluation": evaluation_run,
        },
        "artifacts": {
            "intake_report": artifact_descriptor(intake_prefix.with_suffix(".json")),
            "event_study_report": artifact_descriptor(event_prefix.with_suffix(".json")) if event_run else None,
            "event_records_csv": artifact_descriptor(event_records_csv) if event_records_csv else None,
            "evaluation_report": artifact_descriptor(evaluation_prefix.with_suffix(".json")) if evaluation_run else None,
        },
        "intake": intake,
        "event_study": event_study,
        "evaluation": evaluation,
        "next_action": next_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only forceOrder liquidation context pipeline")
    parser.add_argument("--data-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--prereg-lock", default=DEFAULT_PREREG_LOCK)
    parser.add_argument("--symbols", default=DEFAULT_RESEARCH_SYMBOLS)
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--horizons", default="1,2,4,8")
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--min-event-bars-for-research", type=int, default=50)
    parser.add_argument("--min-context-bars", type=int, default=15)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--out-prefix", default="docs/FORCE_ORDER_LIQUIDATION_RESEARCH_PIPELINE_2026-07-01")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "intake_decision": report.get("intake", {}).get("decision"),
                "event_study_decision": report.get("event_study", {}).get("decision") if report.get("event_study") else None,
                "evaluation_decision": report.get("evaluation", {}).get("decision") if report.get("evaluation") else None,
                "out": portable(out_prefix.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
