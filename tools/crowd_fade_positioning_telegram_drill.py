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
DEFAULT_WORK_DIR = ROOT / "_dl" / "runtime_drills"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CROWD_FADE_POSITIONING_TELEGRAM_DRILL_2026-06-19"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


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


def synthetic_observer() -> dict[str, Any]:
    return {
        "generated_at": now_iso(),
        "engine": "CROWD_FADE_POSITIONING_SHADOW_OBSERVER",
        "engine_version": "synthetic-drill",
        "strategy_id": "crowd_fade_1h_global_long_short_ratio_crowded_longs_fade_short_z1.25_w24_oi0_fund0_s1_t2_h8",
        "candidate_classification": "candidate_watchlist_limited_history",
        "latest": {
            "status": "observer_signal",
            "signal_found": True,
            "signal_time": "2099-01-01T00:00:00+00:00",
            "side_hint": "SHORT",
            "ratio": 2.15,
            "ratio_z": 1.42,
            "funding": 0.00008,
            "oi_delta": 0.012,
        },
        "journal_path": str(ROOT / "logs" / "forward_paper_feed" / "crowd_fade_positioning_shadow_observer.jsonl"),
        "state_path": str(ROOT / "logs" / "forward_paper_feed" / "crowd_fade_positioning_shadow_observer_state.json"),
        "can_trade": False,
    }


def synthetic_scoreboard() -> dict[str, Any]:
    return {
        "generated_at": now_iso(),
        "engine": "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD",
        "engine_version": "synthetic-drill",
        "summary": {
            "classification": "no_observer_signals_yet",
            "observer_signal_events": 0,
            "resolved": 0,
            "unresolved": 0,
            "winrate_pct": None,
            "expectancy_r": None,
            "can_trade": False,
        },
        "outcomes": [],
        "decision": "synthetic_scoreboard_fixture_no_trade_permission",
        "can_trade": False,
    }


def run_notify(
    *,
    observer_path: Path,
    scoreboard_path: Path,
    state_path: Path,
    card_json_path: Path,
    card_md_path: Path,
    notify_prefix: Path,
    timeout_s: int,
    send: bool,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "tools/crowd_fade_positioning_telegram_notify.py",
        "--observer-json-path",
        str(observer_path),
        "--scoreboard-json-path",
        str(scoreboard_path),
        "--state-path",
        str(state_path),
        "--card-json-path",
        str(card_json_path),
        "--card-md-path",
        str(card_md_path),
        "--out-prefix",
        str(notify_prefix),
        "--force",
        "--message-prefix",
        "DRILL ONLY - synthetic CROWD-FADE watch alert. Ignore as market signal.",
    ]
    env = dict(os.environ)
    if not send:
        command.append("--dry-run")
        env["TELEGRAM_BOT_TOKEN"] = "DRILL_DRY_RUN_TOKEN"
        env["TELEGRAM_CHAT_ID"] = "DRILL_DRY_RUN_CHAT"
    started = time.time()
    proc = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout_s, env=env)
    notify_report = read_json(notify_prefix.with_suffix(".json"), {})
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
            "# Crowd-Fade Telegram Drill",
            "",
            f"- Generated: `{report.get('generated_at')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Send requested: `{report.get('send_requested')}`",
            f"- Can trade: `{report.get('can_trade')}`",
            "",
            "## Result",
            "",
            f"- Notifier exit: `{result.get('exit_code')}`",
            f"- Notifier decision: `{notify.get('decision')}`",
            f"- Signal found: `{notify.get('signal_found')}`",
            f"- Telegram response ok: `{notify.get('telegram_response_ok')}`",
            "",
            "## Files",
            "",
            f"- Observer fixture: `{rel_path(Path(str(report.get('observer_fixture_path'))))}`",
            f"- Scoreboard fixture: `{rel_path(Path(str(report.get('scoreboard_fixture_path'))))}`",
            f"- Notify report: `{rel_path(Path(str(report.get('notify_report_path'))))}`",
            f"- Drill card: `{rel_path(Path(str(report.get('card_md_path'))))}`",
            "",
            "## Boundary",
            "",
            "- Synthetic drill only.",
            "- Default mode is dry-run and does not send Telegram.",
            "- `--send` sends one clearly labelled synthetic drill message.",
            "- No entry, no paper intent, no orders.",
            "",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Dry-run drill for crowd-fade Telegram watch alerts.")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--timeout-s", type=int, default=30)
    parser.add_argument("--send", action="store_true", help="Send one clearly labelled synthetic drill Telegram message.")
    args = parser.parse_args()

    work_dir = resolve_path(args.work_dir)
    out_prefix = resolve_path(args.out_prefix)
    notify_prefix = ROOT / "docs" / "CROWD_FADE_POSITIONING_TELEGRAM_DRILL_NOTIFY_2026-06-19"
    work_dir.mkdir(parents=True, exist_ok=True)

    observer_path = work_dir / "crowd_fade_observer_signal_fixture.json"
    scoreboard_path = work_dir / "crowd_fade_scoreboard_fixture.json"
    state_path = work_dir / "crowd_fade_telegram_drill_state.json"
    card_json_path = work_dir / "latest_crowd_fade_drill_card.json"
    card_md_path = work_dir / "latest_crowd_fade_drill_card.md"
    if state_path.exists():
        state_path.unlink()

    write_json(observer_path, synthetic_observer())
    write_json(scoreboard_path, synthetic_scoreboard())
    notify_result = run_notify(
        observer_path=observer_path,
        scoreboard_path=scoreboard_path,
        state_path=state_path,
        card_json_path=card_json_path,
        card_md_path=card_md_path,
        notify_prefix=notify_prefix,
        timeout_s=args.timeout_s,
        send=bool(args.send),
    )
    notify_report = notify_result.get("notify_report") if isinstance(notify_result, dict) else {}
    expected_decision = "sent" if args.send else "dry_run_ready"
    passed = (
        notify_result.get("exit_code") == 0
        and isinstance(notify_report, dict)
        and notify_report.get("decision") == expected_decision
        and notify_report.get("signal_found") is True
        and notify_report.get("can_trade") is False
    )
    decision = "crowd_fade_telegram_drill_passed" if passed else "crowd_fade_telegram_drill_failed"
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "crowd_fade_telegram_drill_send" if args.send else "crowd_fade_telegram_drill_dry_run_only",
            "can_trade": False,
            "sends_orders": False,
            "sends_telegram": bool(args.send),
            "uses_exchange_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "send_requested": bool(args.send),
        "observer_fixture_path": str(observer_path),
        "scoreboard_fixture_path": str(scoreboard_path),
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
                "decision": decision,
                "notify_decision": notify_report.get("decision") if isinstance(notify_report, dict) else None,
                "signal_found": notify_report.get("signal_found") if isinstance(notify_report, dict) else None,
                "send_requested": bool(args.send),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
