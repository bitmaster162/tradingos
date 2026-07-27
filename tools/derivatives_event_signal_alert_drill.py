#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def synthetic_observer() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "observer_id": "derivatives_event_forward_observer",
        "selected_config": {
            "strategy_id": "synthetic_deriv_oi_build_continuation_4h_long",
            "family": "oi_build_continuation",
            "side": "LONG",
            "interval": "4h",
            "lookback": 6,
            "price_atr": 0.8,
            "oi_pct": 0.25,
            "funding_abs": 0.0002,
            "volume_z": 0.0,
            "close_location": 0.55,
            "regime_filter": "ema50_stack",
            "stop_atr": 1.0,
            "take_atr": 3.0,
            "max_hold_bars": 8,
        },
        "latest_observation": {
            "status": "observer_signal_written",
            "signal": True,
            "duplicate_suppressed": False,
            "events_written": 1,
            "bar_ts": "2026-01-01T00:00:00+00:00",
            "bar_index": 999,
            "close": 100000.0,
            "price_move_atr": 1.25,
            "oi_delta_pct": 2.5,
            "funding": 0.00005,
            "volume_z": 1.1,
            "close_location": 0.82,
            "atr": 900.0,
        },
        "decision": "observer_signal_written",
        "runtime_boundary": {
            "observer_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def synthetic_scoreboard() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "observer_id": "derivatives_event_forward_observer",
        "summary": {
            "classification": "pending_only",
            "observer_signal_events": 1,
            "resolved": 0,
            "unresolved": 1,
            "winrate_pct": None,
            "expectancy_r": None,
            "can_trade": False,
        },
        "decision": "pending_only",
        "can_trade": False,
    }


def synthetic_gate() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": "blocked_waiting_derivatives_event_forward_evidence",
        "promotion": {
            "observer_allowed": True,
            "paper_design_review_allowed": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
            "manual_review_required": True,
            "can_trade": False,
        },
        "can_trade": False,
    }


def run_notify(drill_dir: Path, *, state_path: Path, suffix: str) -> dict[str, Any]:
    out_prefix = drill_dir / f"notify_{suffix}"
    command = [
        sys.executable,
        str(ROOT / "tools" / "derivatives_event_telegram_notify.py"),
        "--observer-json-path",
        str(drill_dir / "synthetic_observer.json"),
        "--scoreboard-json-path",
        str(drill_dir / "synthetic_scoreboard.json"),
        "--gate-json-path",
        str(drill_dir / "synthetic_gate.json"),
        "--state-path",
        str(state_path),
        "--card-json-path",
        str(drill_dir / f"latest_card_{suffix}.json"),
        "--card-md-path",
        str(drill_dir / f"latest_card_{suffix}.md"),
        "--out-prefix",
        str(out_prefix),
        "--dry-run",
    ]
    env = dict(os.environ)
    env.setdefault("TELEGRAM_BOT_TOKEN", "synthetic-token")
    env.setdefault("TELEGRAM_CHAT_ID", "123456")
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    return {
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "report_path": rel_path(out_prefix.with_suffix(".json")),
        "report": read_json(out_prefix.with_suffix(".json")),
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Derivatives Event Signal Alert Drill",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Synthetic dry-run only.",
            "- Does not send Telegram.",
            "- Does not create paper-entry intents.",
            "- Does not send exchange orders.",
            "",
            "## Results",
            "",
            f"- Decision: `{report.get('decision')}`.",
            f"- First notify decision: `{report.get('first_notify_decision')}`.",
            f"- Second notify decision: `{report.get('second_notify_decision')}`.",
            f"- First exit code: `{report.get('first_exit_code')}`.",
            f"- Second exit code: `{report.get('second_exit_code')}`.",
            f"- Can trade: `{report.get('can_trade')}`.",
            "",
            "## Meaning",
            "",
            "- If a real derivatives-event observer signal is written, the notification path can render and dedupe the alert.",
            "- A successful drill is not evidence of profitability.",
            "",
        ]
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    drill_dir = resolve_path(args.drill_dir)
    drill_dir.mkdir(parents=True, exist_ok=True)
    state_path = drill_dir / "notify_state.json"
    if state_path.exists():
        state_path.unlink()

    write_json(drill_dir / "synthetic_observer.json", synthetic_observer())
    write_json(drill_dir / "synthetic_scoreboard.json", synthetic_scoreboard())
    write_json(drill_dir / "synthetic_gate.json", synthetic_gate())

    first = run_notify(drill_dir, state_path=state_path, suffix="first")
    second = run_notify(drill_dir, state_path=state_path, suffix="second")
    first_decision = first.get("report", {}).get("decision")
    second_decision = second.get("report", {}).get("decision")
    passed = (
        first.get("exit_code") == 0
        and second.get("exit_code") == 0
        and first_decision == "dry_run_ready"
        and second_decision == "skipped_duplicate"
    )
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "drill_dir": rel_path(drill_dir),
        "decision": "derivatives_event_signal_alert_drill_passed" if passed else "derivatives_event_signal_alert_drill_failed",
        "first_exit_code": first.get("exit_code"),
        "second_exit_code": second.get("exit_code"),
        "first_notify_decision": first_decision,
        "second_notify_decision": second_decision,
        "first": first,
        "second": second,
        "runtime_boundary": {
            "synthetic_only": True,
            "telegram_dry_run": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "next_action": "keep real derivatives-event notify fail-closed; send only on newly written observer signals",
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Synthetic dry-run drill for derivatives-event Telegram alert path")
    parser.add_argument("--drill-dir", default="_dl/runtime_drills/derivatives_event_signal_alert_drill")
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_SIGNAL_ALERT_DRILL_2026-06-26")
    args = parser.parse_args()
    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "first": report["first_notify_decision"],
                "second": report["second_notify_decision"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] == "derivatives_event_signal_alert_drill_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
