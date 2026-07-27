#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from copy import deepcopy
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


def make_watch_card(source_card: dict[str, Any], profile: str) -> dict[str, Any]:
    card = deepcopy(source_card)
    if not card:
        card = {
            "hypothesis_id": "DOC_RULE_SPOT_CONFIRM_1H_VOLZ05_OI1_RR1X3_V1",
            "strategy_id": "doc_rule_ad70abbc50_spot_confirm_1h",
            "symbol": "BTCUSDT",
            "interval": "1h",
            "side": "LONG",
            "signal_bar_ts": now_iso(),
            "planned_entry_policy": "watch_only_next_1h_open_after_signal_close",
            "reference_entry": 58400.0,
            "reference_stop": 58015.0,
            "reference_take": 59554.0,
            "stop_atr": 1.0,
            "take_atr": 3.0,
            "max_hold_bars": 24,
        }
    hypothesis_id = str(card.get("hypothesis_id") or "DOC_RULE_SPOT_CONFIRM_1H_VOLZ05_OI1_RR1X3_V1")
    bar_ts = str(card.get("signal_bar_ts") or card.get("observation", {}).get("bar_ts") or now_iso())
    drill_suffix = now_iso().replace(":", "").replace("-", "")
    card["generated_at"] = now_iso()
    card["status"] = "watch_signal"
    card["signal_key"] = f"DRILL|{profile}|{hypothesis_id}|{bar_ts}|{drill_suffix}"
    card["boundary"] = {
        "watch_only": True,
        "paper_allowed": False,
        "live_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
        "drill_only": True,
    }
    obs = card.get("observation") if isinstance(card.get("observation"), dict) else {}
    obs.update(
        {
            "status": "watch_signal",
            "signal": True,
            "raw_signal_on_bar": True,
            "volume_regime": "volume_active",
            "volume_z": max(float(obs.get("volume_z") or 0.0), 0.75),
            "oi_delta_pct": max(float(obs.get("oi_delta_pct") or 0.0), 1.25),
            "reason": "synthetic_drill_not_a_real_market_signal",
        }
    )
    card["observation"] = obs
    conditions = card.get("conditions") if isinstance(card.get("conditions"), dict) else {}
    conditions.update(
        {
            "spot_confirmed_breakout_long": True,
            "guard_profile": profile,
            "guard_text": "volume_z>=0.5 & oi_delta_pct>=1.0" if profile == "volume_z_oi_delta" else "volume_regime=volume_active",
            "guard_checks": {
                "spot_confirmed_breakout_long": True,
                "volume_regime_active": True,
                "volume_z_ge_0_5": True,
                "oi_delta_pct_ge_1_0": True,
            },
            "data_not_stale": True,
            "synthetic_drill": True,
        }
    )
    card["conditions"] = conditions
    return card


def run_notify(card_path: Path, state_path: Path, out_prefix: Path, timeout_s: int) -> dict[str, Any]:
    command = [
        sys.executable,
        "tools/document_rule_forward_telegram_notify.py",
        "--card-path",
        str(card_path),
        "--state-path",
        str(state_path),
        "--out-prefix",
        str(out_prefix),
        "--dry-run",
        "--force",
    ]
    env = dict(os.environ)
    env.setdefault("TELEGRAM_BOT_TOKEN", "DRILL_DRY_RUN_TOKEN")
    env.setdefault("TELEGRAM_CHAT_ID", "DRILL_DRY_RUN_CHAT")
    proc = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout_s, env=env)
    notify_report = read_json(out_prefix.with_suffix(".json"), {})
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
        "notify_report": notify_report if isinstance(notify_report, dict) else {},
    }


def render_markdown(report: dict[str, Any]) -> str:
    notify = report.get("notify", {}) if isinstance(report.get("notify"), dict) else {}
    notify_report = notify.get("notify_report", {}) if isinstance(notify.get("notify_report"), dict) else {}
    return "\n".join(
        [
            "# Document Rule Forward Signal Drill",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Synthetic drill only.",
            "- Uses a fixture card in `_dl`, not the live observer card.",
            "- Runs Telegram notify with `--dry-run --force`; no message is sent.",
            "- No orders, no paper/live permission, `can_trade=false`.",
            "",
            "## Result",
            "",
            f"- Decision: `{report.get('decision')}`",
            f"- Profile: `{report.get('profile')}`",
            f"- Notify exit code: `{notify.get('exit_code')}`",
            f"- Notify decision: `{notify_report.get('decision')}`",
            f"- Card status seen by notify: `{notify_report.get('card_status')}`",
            f"- Signal key: `{report.get('signal_key')}`",
            "",
            "## Files",
            "",
            f"- Fixture card: `{report.get('fixture_card_path')}`",
            f"- Drill state: `{report.get('drill_state_path')}`",
            f"- Notify report: `{report.get('notify_report_path')}`",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic watch-signal drill for document-rule forward notify path")
    parser.add_argument("--profile", choices=["volume_active", "volume_z_oi_delta"], default="volume_z_oi_delta")
    parser.add_argument("--source-card-path", default="logs/document_rule_forward_observer/latest_signal_card_volume_z_oi_delta.json")
    parser.add_argument("--work-dir", default="_dl/document_rule_forward_signal_drill")
    parser.add_argument("--timeout-s", type=int, default=30)
    parser.add_argument("--out-prefix", default="docs/DOCUMENT_RULE_FORWARD_SIGNAL_DRILL_VOLZ05_OI1_2026-06-30")
    args = parser.parse_args()

    source_card_path = resolve_path(args.source_card_path)
    work_dir = resolve_path(args.work_dir)
    out_prefix = resolve_path(args.out_prefix)
    source_card = read_json(source_card_path, {})
    if not isinstance(source_card, dict):
        source_card = {}
    card = make_watch_card(source_card, args.profile)
    fixture_card_path = work_dir / f"{args.profile}_watch_signal_fixture.json"
    drill_state_path = work_dir / f"{args.profile}_telegram_notify_state.json"
    notify_prefix = resolve_path(f"docs/DOCUMENT_RULE_FORWARD_SIGNAL_DRILL_NOTIFY_{args.profile.upper()}_2026-06-30")
    if drill_state_path.exists():
        drill_state_path.unlink()
    write_json(fixture_card_path, card)
    notify = run_notify(fixture_card_path, drill_state_path, notify_prefix, args.timeout_s)
    notify_report = notify.get("notify_report", {}) if isinstance(notify.get("notify_report"), dict) else {}
    passed = notify.get("exit_code") == 0 and notify_report.get("decision") == "dry_run_ready" and notify_report.get("card_status") == "watch_signal"
    report = {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_forward_signal_drill.py",
        "runtime_boundary": {
            "classification": "synthetic_watch_signal_notify_drill",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "telegram_send": False,
        },
        "profile": args.profile,
        "source_card_path": portable(source_card_path),
        "fixture_card_path": portable(fixture_card_path),
        "drill_state_path": portable(drill_state_path),
        "notify_report_path": portable(notify_prefix.with_suffix(".json")),
        "signal_key": card.get("signal_key"),
        "notify": notify,
        "decision": "synthetic_watch_signal_notify_path_passed" if passed else "synthetic_watch_signal_notify_path_failed",
        "next_action": "live observer can rely on notify dry-run path; wait for real watch_signal" if passed else "fix notify path before relying on observer alerts",
        "can_trade": False,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "profile": args.profile,
                "notify_decision": notify_report.get("decision"),
                "notify_card_status": notify_report.get("card_status"),
                "json": portable(json_path),
                "md": portable(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
