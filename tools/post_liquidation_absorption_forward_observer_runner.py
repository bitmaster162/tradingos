#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def interval_delta(interval: str) -> timedelta:
    text = interval.strip().lower()
    if text.endswith("m"):
        return timedelta(minutes=int(text[:-1]))
    if text.endswith("h"):
        return timedelta(hours=int(text[:-1]))
    if text.endswith("d"):
        return timedelta(days=int(text[:-1]))
    raise ValueError(f"unsupported interval: {interval}")


def next_complete_bar_ts(value: Any, interval: str) -> str | None:
    parsed = parse_ts(value)
    if parsed is None:
        return None
    seconds = int(interval_delta(interval).total_seconds())
    epoch = int(parsed.timestamp())
    floored = epoch - (epoch % seconds)
    start = datetime.fromtimestamp(floored, tz=timezone.utc) + interval_delta(interval)
    return start.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run_command(args: list[str], timeout_s: int) -> dict[str, Any]:
    started_at = now_iso()
    completed = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    return {
        "args": args,
        "started_at": started_at,
        "finished_at": now_iso(),
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def report_is_fresh(report: dict[str, Any], command_result: dict[str, Any]) -> bool:
    if command_result.get("returncode") != 0:
        return False
    generated_at = parse_ts(report.get("generated_at"))
    started_at = parse_ts(command_result.get("started_at"))
    return generated_at is not None and started_at is not None and generated_at >= started_at


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def selected_groups(observer: dict[str, Any], setup: str, horizons: list[int]) -> list[dict[str, Any]]:
    groups = observer.get("groups") if isinstance(observer.get("groups"), list) else []
    selected: list[dict[str, Any]] = []
    horizon_set = {int(item) for item in horizons}
    for row in groups:
        if not isinstance(row, dict):
            continue
        if row.get("setup") != setup:
            continue
        if row.get("spot_confirmed") is not True:
            continue
        if as_int(row.get("horizon_bars")) not in horizon_set:
            continue
        selected.append(row)
    selected.sort(key=lambda item: as_int(item.get("horizon_bars")))
    return selected


def classify(groups: list[dict[str, Any]], gate: dict[str, Any]) -> tuple[str, list[str], str]:
    min_events = as_int(gate.get("minimum_new_events"), 30)
    min_symbols = as_int(gate.get("minimum_new_symbols"), 2)
    min_positive = as_int(gate.get("minimum_positive_horizons"), 2)
    min_mean = as_float(gate.get("minimum_mean_bps_after_cost_buffer"), 15.0)
    min_winrate = as_float(gate.get("minimum_winrate_pct"), 55.0)

    if not groups:
        return (
            "post_liq_absorption_forward_observer_waiting_new_events",
            ["no_selected_bucket_records_after_lock"],
            "keep collecting real liquidation context rows after the lock timestamp",
        )

    blockers: list[str] = []
    positive_horizons = 0
    min_n = min(as_int((row.get("summary") or {}).get("n")) for row in groups)
    symbols = sorted({symbol for row in groups for symbol in row.get("symbols", []) if isinstance(symbol, str)})
    for row in groups:
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        n = as_int(summary.get("n"))
        mean = as_float(summary.get("mean_bps"), -999999.0)
        winrate = as_float(summary.get("winrate_positive_pct"), 0.0)
        if n >= min_events and mean >= min_mean and winrate >= min_winrate:
            positive_horizons += 1
    if min_n < min_events:
        blockers.append("minimum_new_events")
    if len(symbols) < min_symbols:
        blockers.append("minimum_new_symbols")
    if positive_horizons < min_positive:
        blockers.append("minimum_positive_horizons")

    if "minimum_new_events" in blockers or "minimum_new_symbols" in blockers:
        return (
            "post_liq_absorption_forward_observer_collecting_sample",
            blockers,
            "keep observing untouched events until locked sample thresholds are met",
        )
    if blockers:
        return (
            "post_liq_absorption_forward_observer_failed_gate_for_tombstone_review",
            blockers,
            "forward gate failed after sample threshold; manual tombstone review before any retest",
        )
    return (
        "post_liq_absorption_forward_observer_passed_for_manual_review",
        [],
        "manual review required; this is still not paper/live permission",
    )


def apply_independence_gate(
    decision: str,
    blockers: list[str],
    next_action: str,
    audit: dict[str, Any],
) -> tuple[str, list[str], str]:
    passed = "post_liq_absorption_forward_observer_passed_for_manual_review"
    if decision != passed:
        return decision, blockers, next_action

    audit_decision = str(audit.get("decision") or "missing")
    boundary = audit.get("runtime_boundary") if isinstance(audit.get("runtime_boundary"), dict) else {}
    audit_integrity_ok = (
        audit.get("source_lock_verified") is True
        and audit.get("can_trade") is False
        and audit.get("automatic_promotion_allowed") is False
        and boundary.get("orders_allowed") is False
        and boundary.get("can_trade") is False
    )
    if (
        audit_decision == "post_liq_independence_audit_sample_ready_for_manual_review"
        and audit.get("eligible_for_manual_review") is True
        and audit_integrity_ok
    ):
        return decision, blockers, next_action

    merged = list(blockers)
    if audit_decision == "post_liq_independence_audit_sample_ready_but_cost_gate_failed":
        merged.append("independence_cost_gate_failed")
        return (
            "post_liq_absorption_forward_observer_failed_gate_for_tombstone_review",
            sorted(set(merged)),
            "independence-adjusted cost gate failed; manual tombstone review before any retest",
        )

    blocker = "independence_sample_not_ready" if audit_integrity_ok else "independence_audit_integrity_blocked"
    merged.append(blocker)
    return (
        "post_liq_absorption_forward_observer_collecting_sample",
        sorted(set(merged)),
        "keep collecting untouched events until the locked independence gate is ready",
    )


def apply_operational_freshness_gate(
    decision: str,
    blockers: list[str],
    next_action: str,
    *,
    context_fresh: bool,
    observer_fresh: bool,
    independence_fresh: bool,
) -> tuple[str, list[str], str]:
    operational_blockers: list[str] = []
    if not context_fresh:
        operational_blockers.append("context_refresh_failed_or_stale")
    elif not observer_fresh:
        operational_blockers.append("observer_refresh_failed_or_stale")
    elif not independence_fresh:
        operational_blockers.append("independence_audit_failed_or_stale")
    if not operational_blockers:
        return decision, blockers, next_action
    return (
        "post_liq_absorption_forward_observer_collecting_sample",
        sorted(set([*blockers, *operational_blockers])),
        "inspect the failed or stale observer step; stale artifacts cannot satisfy the forward gate",
    )


def render_markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    lines = [
        "# Post-Liquidation Absorption Forward Observer Runner",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "",
        "## Lock",
        "",
        f"- Lock ID: `{report['lock'].get('lock_id')}`",
        f"- Setup: `{report['selected_bucket'].get('setup')}`",
        f"- Forward start bar: `{report['forward_start_bar_ts']}`",
        "",
        "## Evidence",
        "",
        f"- Context rows after refresh: `{evidence['context_rows_total']}`",
        f"- Selected bucket min N: `{evidence['selected_bucket_min_n']}`",
        f"- Selected symbols: `{', '.join(evidence['selected_symbols']) or 'none'}`",
        f"- Positive horizons: `{evidence['positive_horizons']}`",
        f"- Independence decision: `{evidence['independence_decision']}`",
        f"- Independent 4h blocks, minimum across horizons: `{evidence['independent_blocks_min']}` / `{evidence['required_independent_blocks']}`",
        f"- Independence eligible for manual review: `{evidence['independence_eligible_for_manual_review']}`",
        "",
        "## Selected Horizons",
        "",
        "| Horizon | N | Mean bps | Median bps | Winrate |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in report["selected_groups"]:
        summary = row.get("summary") or {}
        lines.append(
            f"| `{row.get('horizon_bars')}` | `{summary.get('n')}` | `{summary.get('mean_bps')}` | "
            f"`{summary.get('median_bps')}` | `{summary.get('winrate_positive_pct')}` |"
        )
    lines.extend(["", "## Blockers", ""])
    for blocker in report.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Generated Artifacts",
            "",
            f"- Intake report: `{report['artifacts'].get('intake_report')}`",
            f"- Observer report: `{report['artifacts'].get('observer_report')}`",
            f"- Independence audit: `{report['artifacts'].get('independence_audit')}`",
            "",
            "## Boundary",
            "",
            "- Refreshes context and scores the locked observer bucket only.",
            "- Does not create alerts, paper entries, live entries or orders.",
            "- `can_trade=false` regardless of result.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe runner for the locked post-liquidation absorption forward observer.")
    parser.add_argument("--lock", default="configs/POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_LOCK_2026-07-03.json")
    parser.add_argument("--context-out-prefix", default="docs/POST_LIQUIDATION_ABSORPTION_CONTEXT_REFRESH_2026-07-03_FORWARD")
    parser.add_argument("--observer-out-prefix", default="docs/POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_2026-07-03_REFRESH")
    parser.add_argument("--independence-config", default="configs/POST_LIQUIDATION_ABSORPTION_FORWARD_INDEPENDENCE_AUDIT_2026-07-12.json")
    parser.add_argument("--independence-out-prefix", default="docs/POST_LIQUIDATION_ABSORPTION_FORWARD_INDEPENDENCE_AUDIT_2026-07-12")
    parser.add_argument("--out-prefix", default="docs/POST_LIQUIDATION_ABSORPTION_FORWARD_OBSERVER_RUNNER_2026-07-03")
    parser.add_argument("--state-path", default="logs/liquidation_bybit/post_liq_absorption_forward_observer_state.json")
    parser.add_argument("--history-path", default="logs/liquidation_bybit/post_liq_absorption_forward_observer_history.jsonl")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--timeout-s", type=int, default=120)
    args = parser.parse_args()

    lock_path = resolve_path(args.lock)
    lock = read_json(lock_path)
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        raise SystemExit("forward lock must keep can_trade=false and orders_allowed=false")
    selected = lock.get("selected_bucket") if isinstance(lock.get("selected_bucket"), dict) else {}
    gate = lock.get("forward_gate_required") if isinstance(lock.get("forward_gate_required"), dict) else {}
    setup = str(selected.get("setup") or "")
    interval = str(selected.get("interval") or "1h")
    horizons = [int(item) for item in selected.get("horizons_bars", [])]
    created_at = str(lock.get("created_at") or "")
    forward_start_bar = next_complete_bar_ts(created_at, interval)

    fixed = selected.get("fixed_conditions") if isinstance(selected.get("fixed_conditions"), dict) else {}
    absorption_close_location = as_float(fixed.get("futures_close_location_min"), 0.6)
    spot_confirm_min_bps = as_float(fixed.get("spot_minus_perp_event_ret_bps_min"), 0.0)

    context_prefix = resolve_path(args.context_out_prefix)
    observer_prefix = resolve_path(args.observer_out_prefix)
    context_cmd = [
        sys.executable,
        "tools/bybit_all_liquidation_context_intake.py",
        "--symbols",
        args.symbols,
        "--interval",
        interval,
        "--out-prefix",
        portable(context_prefix),
    ]
    context_result = run_command(context_cmd, args.timeout_s)
    context_report_path = context_prefix.with_suffix(".json")
    context_candidate = read_json(context_report_path)
    context_fresh = report_is_fresh(context_candidate, context_result)
    context_result["report_fresh"] = context_fresh
    context_report = context_candidate if context_fresh else {}
    context_csv = context_report.get("aggregate_csv") or f"{portable(context_prefix)}_bar_context.csv"

    observer_cmd = [
        sys.executable,
        "tools/post_liquidation_absorption_spot_perp_confirmation.py",
        "--context-csv",
        str(context_csv),
        "--symbols",
        args.symbols,
        "--interval",
        interval,
        "--horizons",
        ",".join(str(item) for item in horizons),
        "--after-bar-ts",
        created_at,
        "--absorption-close-location",
        str(absorption_close_location),
        "--spot-confirm-min-bps",
        str(spot_confirm_min_bps),
        "--min-events-per-bucket",
        str(gate.get("minimum_new_events") or 30),
        "--min-mean-bps",
        str(gate.get("minimum_mean_bps_after_cost_buffer") or 15.0),
        "--min-winrate-pct",
        str(gate.get("minimum_winrate_pct") or 55.0),
        "--out-prefix",
        portable(observer_prefix),
    ]
    observer_report_path = observer_prefix.with_suffix(".json")
    if context_fresh:
        observer_result = run_command(observer_cmd, args.timeout_s)
        observer_candidate = read_json(observer_report_path)
        observer_fresh = report_is_fresh(observer_candidate, observer_result)
        observer_result["report_fresh"] = observer_fresh
        observer = observer_candidate if observer_fresh else {}
    else:
        observer_result = {
            "args": observer_cmd,
            "started_at": None,
            "finished_at": now_iso(),
            "returncode": None,
            "report_fresh": False,
            "skipped": "context_refresh_failed_or_stale",
            "stdout_tail": "",
            "stderr_tail": "",
        }
        observer_fresh = False
        observer = {}

    groups = selected_groups(observer, setup, horizons)
    raw_decision, blockers, next_action = classify(groups, gate)

    independence_config_path = resolve_path(args.independence_config)
    independence_config = read_json(independence_config_path)
    independence_prefix = resolve_path(args.independence_out_prefix)
    configured_context = resolve_path(str(independence_config.get("context_csv") or ""))
    actual_context = resolve_path(str(context_csv))
    if observer_fresh and configured_context.resolve() == actual_context.resolve():
        independence_cmd = [
            sys.executable,
            "tools/post_liquidation_absorption_forward_independence_audit.py",
            "--config",
            portable(independence_config_path),
            "--out-prefix",
            portable(independence_prefix),
        ]
        independence_result = run_command(independence_cmd, args.timeout_s)
    else:
        independence_result = {
            "args": [],
            "started_at": None,
            "finished_at": now_iso(),
            "returncode": 2,
            "stdout_tail": "",
            "stderr_tail": "independence config context_csv does not match refreshed context artifact",
        }
    independence_report_path = independence_prefix.with_suffix(".json")
    independence_candidate = read_json(independence_report_path)
    independence_fresh = report_is_fresh(independence_candidate, independence_result)
    independence_result["report_fresh"] = independence_fresh
    independence = independence_candidate if independence_fresh else {}
    decision, blockers, next_action = apply_independence_gate(raw_decision, blockers, next_action, independence)
    decision, blockers, next_action = apply_operational_freshness_gate(
        decision,
        blockers,
        next_action,
        context_fresh=context_fresh,
        observer_fresh=observer_fresh,
        independence_fresh=independence_fresh,
    )
    selected_symbols = sorted({symbol for row in groups for symbol in row.get("symbols", []) if isinstance(symbol, str)})
    positive_horizons = 0
    min_n = min([as_int((row.get("summary") or {}).get("n")) for row in groups], default=0)
    for row in groups:
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        if (
            as_int(summary.get("n")) >= as_int(gate.get("minimum_new_events"), 30)
            and as_float(summary.get("mean_bps"), -999999.0) >= as_float(gate.get("minimum_mean_bps_after_cost_buffer"), 15.0)
            and as_float(summary.get("winrate_positive_pct"), 0.0) >= as_float(gate.get("minimum_winrate_pct"), 55.0)
        ):
            positive_horizons += 1
    independence_horizons = independence.get("horizons") if isinstance(independence.get("horizons"), list) else []
    independent_blocks_min = min(
        [as_int(row.get("independent_4h_blocks")) for row in independence_horizons if isinstance(row, dict)],
        default=0,
    )
    independence_gate = independence_config.get("review_gate") if isinstance(independence_config.get("review_gate"), dict) else {}

    report = {
        "generated_at": now_iso(),
        "tool": "tools/post_liquidation_absorption_forward_observer_runner.py",
        "decision": decision,
        "raw_decision_before_independence_gate": raw_decision,
        "can_trade": False,
        "orders_allowed": False,
        "lock_path": portable(lock_path),
        "lock": {"lock_id": lock.get("lock_id"), "status": lock.get("status")},
        "selected_bucket": selected,
        "forward_start_at": created_at,
        "forward_start_bar_ts": forward_start_bar,
        "evidence": {
            "context_rows_total": (context_report.get("summary") or {}).get("aggregate_rows"),
            "observer_records_total": (observer.get("summary") or {}).get("records"),
            "selected_bucket_min_n": min_n,
            "selected_symbols": selected_symbols,
            "positive_horizons": positive_horizons,
            "required_new_events": gate.get("minimum_new_events"),
            "required_new_symbols": gate.get("minimum_new_symbols"),
            "required_positive_horizons": gate.get("minimum_positive_horizons"),
            "independence_decision": independence.get("decision") or "missing_or_failed",
            "independence_sample_ready": independence.get("sample_ready", False),
            "independence_eligible_for_manual_review": independence.get("eligible_for_manual_review", False),
            "independent_blocks_min": independent_blocks_min,
            "required_independent_blocks": independence_gate.get("minimum_independent_blocks_per_horizon"),
            "independence_source_errors": len(independence.get("source_errors") or []),
            "context_report_fresh": context_fresh,
            "observer_report_fresh": observer_fresh,
            "independence_report_fresh": independence_fresh,
        },
        "selected_groups": groups,
        "blockers": blockers,
        "artifacts": {
            "intake_report": portable(context_report_path),
            "intake_context_csv": str(context_csv),
            "observer_report": portable(observer_report_path),
            "independence_audit": portable(independence_report_path),
        },
        "commands": {
            "context_intake": context_result,
            "observer": observer_result,
            "independence_audit": independence_result,
        },
        "next_action": next_action,
        "runtime_boundary": {
            "observer_only": True,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    state = {
        "ts": report["generated_at"],
        "decision": decision,
        "selected_bucket_min_n": min_n,
        "positive_horizons": positive_horizons,
        "independence_decision": independence.get("decision") or "missing_or_failed",
        "independent_blocks_min": independent_blocks_min,
        "blockers": blockers,
        "can_trade": False,
    }
    write_json(resolve_path(args.state_path), state)
    append_jsonl(resolve_path(args.history_path), state)
    print(
        json.dumps(
            {
                "decision": decision,
                "selected_bucket_min_n": min_n,
                "positive_horizons": positive_horizons,
                "independence_decision": independence.get("decision") or "missing_or_failed",
                "independent_blocks_min": independent_blocks_min,
                "blockers": blockers,
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
