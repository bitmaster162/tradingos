#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def display_path(value: Any) -> str:
    if value is None:
        return "None"
    path = Path(str(value))
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(value)


def make_near_trigger_fixture(report: dict[str, Any]) -> dict[str, Any]:
    fixture = deepcopy(report)
    fixture["generated_at"] = now_iso()
    fixture["classification"] = "range_pending_near_trigger"
    fixture["next_action"] = "drill only: verify pending-watch Telegram pre-alert path"
    fixture["can_trade"] = False
    fixture["decision"] = "range_pending_watch_drill_fixture_no_trade_permission"

    runtime_boundary = fixture.setdefault("runtime_boundary", {})
    if isinstance(runtime_boundary, dict):
        runtime_boundary["can_trade"] = False
        runtime_boundary["sends_orders"] = False
        runtime_boundary["uses_private_credentials"] = False
        runtime_boundary["creates_paper_entry_intents"] = False

    latest = fixture.setdefault("latest", {})
    if isinstance(latest, dict):
        latest["context_ok"] = True
        latest["context_blockers"] = []
        latest["trigger_ok"] = False
        latest["refined_ready"] = False
        latest["filter_blockers"] = []
        trigger = latest.setdefault("trigger", {})
        if isinstance(trigger, dict):
            trigger.setdefault("trigger", "near_high")
            trigger["trigger_ok"] = False
            trigger["distance_to_trigger"] = 10.0
            trigger["distance_to_trigger_atr"] = 0.1
            trigger["distance_to_trigger_pct"] = 0.1
            trigger["trigger_progress_pct"] = 99.0
    return fixture


def run_notifier(
    *,
    fixture_path: Path,
    state_path: Path,
    card_json_path: Path,
    card_md_path: Path,
    out_prefix: Path,
    timeout_s: int,
    send: bool,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "tools/range_refined_pending_watch_telegram_notify.py",
        "--pending-watch-json-path",
        str(fixture_path),
        "--state-path",
        str(state_path),
        "--card-json-path",
        str(card_json_path),
        "--card-md-path",
        str(card_md_path),
        "--out-prefix",
        str(out_prefix),
        "--force",
        "--message-prefix",
        "DRILL ONLY - synthetic RANGE near-trigger transport test. Ignore as market signal.",
    ]
    if not send:
        command.append("--dry-run")
    env = dict(os.environ)
    if not send:
        env.setdefault("TELEGRAM_BOT_TOKEN", "DRILL_DRY_RUN_TOKEN")
        env.setdefault("TELEGRAM_CHAT_ID", "DRILL_DRY_RUN_CHAT")
    started = time.time()
    proc = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout_s, env=env)
    notify_report = read_json(out_prefix.with_suffix(".json"), {})
    return {
        "command": command,
        "exit_code": proc.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
        "notify_report": notify_report if isinstance(notify_report, dict) else {},
    }


def render_markdown(report: dict[str, Any]) -> str:
    result = report.get("notify_result") if isinstance(report.get("notify_result"), dict) else {}
    notify = result.get("notify_report") if isinstance(result.get("notify_report"), dict) else {}
    return "\n".join(
        [
            "# Range Pending-Watch Telegram Drill",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Drill only.",
            "- Uses a synthetic `range_pending_near_trigger` fixture.",
            f"- Send requested: `{report.get('send_requested')}`.",
            "- Default mode uses `--dry-run --force`; `--send` sends one clearly labelled drill message.",
            "- No signals, no paper-entry intents, no orders.",
            "",
            "## Result",
            "",
            f"- Decision: `{report.get('decision')}`.",
            f"- Notifier exit: `{result.get('exit_code')}`.",
            f"- Notifier decision: `{notify.get('decision')}`.",
            f"- Notifier classification: `{notify.get('classification')}`.",
            f"- Can trade: `{report.get('can_trade')}`.",
            "",
            "## Files",
            "",
            f"- Fixture: `{display_path(report.get('fixture_path'))}`.",
            f"- Notify report: `{display_path(report.get('notify_report_path'))}`.",
            f"- Drill card JSON: `{display_path(report.get('card_json_path'))}`.",
            f"- Drill card MD: `{display_path(report.get('card_md_path'))}`.",
            "",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Dry-run drill for RANGE pending-watch Telegram pre-alerts")
    parser.add_argument("--pending-watch-json-path", default="docs/RANGE_REFINED_PENDING_WATCH_2026-06-17.json")
    parser.add_argument("--work-dir", default="_dl/runtime_drills")
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_PENDING_WATCH_TELEGRAM_DRILL_2026-06-18")
    parser.add_argument("--timeout-s", type=int, default=30)
    parser.add_argument("--send", action="store_true", help="Send one clearly labelled synthetic drill Telegram message.")
    args = parser.parse_args()

    source_path = resolve_path(args.pending_watch_json_path)
    work_dir = resolve_path(args.work_dir)
    out_prefix = resolve_path(args.out_prefix)
    source = read_json(source_path, {})
    if not isinstance(source, dict) or not source:
        report = {
            "generated_at": now_iso(),
            "decision": "blocked_missing_pending_watch_report",
            "source_path": str(source_path),
            "can_trade": False,
        }
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    work_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = work_dir / "range_pending_watch_near_trigger_fixture.json"
    state_path = work_dir / "range_pending_watch_telegram_drill_state.json"
    card_json_path = work_dir / "latest_range_pending_watch_drill_card.json"
    card_md_path = work_dir / "latest_range_pending_watch_drill_card.md"
    notify_prefix = resolve_path("docs/RANGE_REFINED_PENDING_WATCH_TELEGRAM_DRILL_NOTIFY_2026-06-18")

    if state_path.exists():
        state_path.unlink()
    fixture = make_near_trigger_fixture(source)
    write_json(fixture_path, fixture)
    notify_result = run_notifier(
        fixture_path=fixture_path,
        state_path=state_path,
        card_json_path=card_json_path,
        card_md_path=card_md_path,
        out_prefix=notify_prefix,
        timeout_s=args.timeout_s,
        send=bool(args.send),
    )
    notify_report = notify_result.get("notify_report") if isinstance(notify_result, dict) else {}
    expected_notify_decision = "sent" if args.send else "dry_run_ready"
    passed = (
        notify_result.get("exit_code") == 0
        and isinstance(notify_report, dict)
        and notify_report.get("decision") == expected_notify_decision
        and notify_report.get("classification") == "range_pending_near_trigger"
        and notify_report.get("can_trade") is False
    )
    decision = "range_pending_watch_telegram_drill_passed" if passed else "range_pending_watch_telegram_drill_failed"
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_pending_watch_telegram_drill_send" if args.send else "range_pending_watch_telegram_drill_dry_run_only",
            "can_trade": False,
            "sends_orders": False,
            "sends_telegram": bool(args.send),
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "send_requested": bool(args.send),
        "source_path": str(source_path),
        "fixture_path": str(fixture_path),
        "state_path": str(state_path),
        "notify_report_path": str(notify_prefix.with_suffix(".json")),
        "card_json_path": str(card_json_path),
        "card_md_path": str(card_md_path),
        "notify_result": notify_result,
        "decision": decision,
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "decision": decision,
                "notify_decision": notify_report.get("decision") if isinstance(notify_report, dict) else None,
                "classification": notify_report.get("classification") if isinstance(notify_report, dict) else None,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
