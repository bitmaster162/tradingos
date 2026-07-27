#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": portable(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(path)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_command(command: list[str], timeout_s: int) -> dict[str, Any]:
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
            "command": command,
            "status": "timeout",
            "exit_code": None,
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }
    return {
        "command": command,
        "status": "success" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def gate_names(report: dict[str, Any], severity: str, passed: bool) -> list[str]:
    return [
        str(item.get("name"))
        for item in report.get("gates", [])
        if isinstance(item, dict) and item.get("severity") == severity and item.get("passed") is passed and item.get("name")
    ]


def parse_context_counts(contexts: dict[str, Any]) -> dict[str, int]:
    return {
        "long_liquidation_flush": int(contexts.get("long_liquidation_flush") or 0),
        "short_liquidation_squeeze": int(contexts.get("short_liquidation_squeeze") or 0),
        "mixed": int(contexts.get("mixed") or 0),
    }


def classify(
    *,
    dq: dict[str, Any],
    intake: dict[str, Any],
    study: dict[str, Any],
    min_events: int,
    min_event_bars: int,
    min_context_bars: int,
) -> tuple[str, list[str], str]:
    hard_failures = gate_names(dq, "hard", False)
    if hard_failures:
        return (
            "bybit_liquidation_sample_gate_hard_fail",
            hard_failures,
            "fix Bybit collector/data-quality hard failures before using liquidation rows",
        )

    events = int(dq.get("events", {}).get("events") or intake.get("summary", {}).get("events") or 0)
    aggregate_rows = int(intake.get("summary", {}).get("aggregate_rows") or 0)
    matched_price_bars = int(intake.get("summary", {}).get("matched_price_bars") or 0)
    contexts = parse_context_counts(intake.get("summary", {}).get("contexts") or {})

    blockers: list[str] = []
    if events < min_events:
        blockers.append("minimum_events_for_research")
    if aggregate_rows < min_event_bars:
        blockers.append("minimum_distinct_event_bars")
    if matched_price_bars < min_event_bars:
        blockers.append("minimum_matched_price_bars")
    if contexts["long_liquidation_flush"] < min_context_bars:
        blockers.append("long_liquidation_flush_context_sample")
    if contexts["short_liquidation_squeeze"] < min_context_bars:
        blockers.append("short_liquidation_squeeze_context_sample")

    if blockers:
        return (
            "bybit_liquidation_sample_collecting",
            blockers,
            "keep Bybit collector running; rerun the gate after event bars and context balance improve",
        )

    if study.get("decision") != "force_order_event_study_ready_for_review":
        return (
            "bybit_liquidation_sample_gate_waiting_event_study",
            [str(study.get("decision") or "event_study_missing")],
            "rerun fixed-horizon event study after context intake is ready",
        )

    return (
        "bybit_liquidation_sample_ready_for_manual_review",
        [],
        "review the fixed-horizon event-study report; do not promote without separate forward evidence gate",
    )


def render_markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    thresholds = report["thresholds"]
    lines = [
        "# Bybit allLiquidation Sample Gate",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        "",
        "## Evidence",
        "",
        f"- Events: `{evidence['events']}` / `{thresholds['min_events_for_research']}`",
        f"- Distinct event bars: `{evidence['aggregate_rows']}` / `{thresholds['min_event_bars_for_research']}`",
        f"- Matched price bars: `{evidence['matched_price_bars']}` / `{thresholds['min_event_bars_for_research']}`",
        f"- Context counts: `{evidence['contexts']}`",
        f"- Event-study decision: `{evidence['event_study_decision']}`",
        "",
        "## Blockers",
        "",
    ]
    if report["blockers"]:
        for blocker in report["blockers"]:
            lines.append(f"- `{blocker}`")
    else:
        lines.append("- `none`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Gate only; research and data-quality reports only.",
            "- No alerts, no paper entry intents, no orders, no private credentials.",
            "- `can_trade=false` until a separate locked promotion process exists.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
            "## Source Reports",
            "",
        ]
    )
    for item in report["source_reports"]:
        lines.append(f"- `{item}`")
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    out_prefix = resolve_path(args.out_prefix)
    dq_prefix = resolve_path(args.data_quality_prefix)
    intake_prefix = resolve_path(args.intake_prefix)
    study_prefix = resolve_path(args.study_prefix)

    commands: dict[str, Any] = {}
    if args.refresh:
        commands["data_quality"] = run_command(
            [
                sys.executable,
                str(ROOT / "tools" / "bybit_all_liquidation_data_quality.py"),
                "--min-events-for-research",
                str(args.min_events_for_research),
                "--out-prefix",
                portable(dq_prefix),
            ],
            timeout_s=args.timeout_seconds,
        )
        commands["context_intake"] = run_command(
            [
                sys.executable,
                str(ROOT / "tools" / "bybit_all_liquidation_context_intake.py"),
                "--symbols",
                args.symbols,
                "--interval",
                args.interval,
                "--min-events-for-research",
                str(args.min_events_for_research),
                "--min-event-bars-for-research",
                str(args.min_event_bars_for_research),
                "--out-prefix",
                portable(intake_prefix),
            ],
            timeout_s=args.timeout_seconds,
        )

    context_csv = intake_prefix.with_name(intake_prefix.name + "_bar_context.csv")
    if args.refresh or not study_prefix.with_suffix(".json").exists():
        commands["event_study"] = run_command(
            [
                sys.executable,
                str(ROOT / "tools" / "force_order_liquidation_event_study.py"),
                "--context-csv",
                portable(context_csv),
                "--allowed-sources",
                "bybit_v5_allLiquidation_websocket",
                "--source-label",
                "Bybit allLiquidation",
                "--symbols",
                args.symbols,
                "--interval",
                args.interval,
                "--horizons",
                args.horizons,
                "--min-event-bars",
                str(args.min_event_bars_for_research),
                "--min-context-bars",
                str(args.min_context_bars),
                "--out-prefix",
                portable(study_prefix),
            ],
            timeout_s=args.timeout_seconds,
        )

    dq = read_json(dq_prefix.with_suffix(".json"))
    intake = read_json(intake_prefix.with_suffix(".json"))
    study = read_json(study_prefix.with_suffix(".json"))
    decision, blockers, next_action = classify(
        dq=dq,
        intake=intake,
        study=study,
        min_events=args.min_events_for_research,
        min_event_bars=args.min_event_bars_for_research,
        min_context_bars=args.min_context_bars,
    )
    contexts = parse_context_counts(intake.get("summary", {}).get("contexts") or {})
    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_all_liquidation_sample_gate.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {
            "gate_only": True,
            "research_only": True,
            "emits_alerts": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "thresholds": {
            "min_events_for_research": args.min_events_for_research,
            "min_event_bars_for_research": args.min_event_bars_for_research,
            "min_context_bars": args.min_context_bars,
        },
        "evidence": {
            "events": int(dq.get("events", {}).get("events") or intake.get("summary", {}).get("events") or 0),
            "aggregate_rows": int(intake.get("summary", {}).get("aggregate_rows") or 0),
            "matched_price_bars": int(intake.get("summary", {}).get("matched_price_bars") or 0),
            "contexts": contexts,
            "data_quality_decision": dq.get("decision"),
            "context_intake_decision": intake.get("decision"),
            "event_study_decision": study.get("decision"),
        },
        "blockers": blockers,
        "commands": commands,
        "source_reports": [
            portable(dq_prefix.with_suffix(".json")),
            portable(intake_prefix.with_suffix(".json")),
            portable(study_prefix.with_suffix(".json")),
        ],
        "next_action": next_action,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed sample-size and context-balance gate for Bybit allLiquidation research")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BCHUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--horizons", default="1,2,4")
    parser.add_argument("--min-events-for-research", type=int, default=500)
    parser.add_argument("--min-event-bars-for-research", type=int, default=50)
    parser.add_argument("--min-context-bars", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--data-quality-prefix", default="docs/BYBIT_ALL_LIQUIDATION_DATA_QUALITY_2026-07-01")
    parser.add_argument("--intake-prefix", default="docs/BYBIT_ALL_LIQUIDATION_CONTEXT_INTAKE_2026-07-02_AFTER_PRICE_GAP_FILL")
    parser.add_argument("--study-prefix", default="docs/BYBIT_ALL_LIQUIDATION_EVENT_STUDY_2026-07-02_AFTER_PRICE_GAP_FILL")
    parser.add_argument("--out-prefix", default="docs/BYBIT_ALL_LIQUIDATION_SAMPLE_GATE_2026-07-02_AFTER_PRICE_GAP_FILL_EXPLICIT")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    report = build_report(args)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["evidence"]["events"],
                "aggregate_rows": report["evidence"]["aggregate_rows"],
                "matched_price_bars": report["evidence"]["matched_price_bars"],
                "blockers": report["blockers"],
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
