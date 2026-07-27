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


def latest_observer_result(observer: dict[str, Any]) -> dict[str, Any]:
    latest = observer.get("latest_result")
    return latest if isinstance(latest, dict) else {}


def selected_candidate(observer: dict[str, Any]) -> dict[str, Any]:
    selected = observer.get("selected_candidate")
    return selected if isinstance(selected, dict) else {}


def make_signal_fixture(observer: dict[str, Any]) -> dict[str, Any]:
    fixture = deepcopy(observer)
    fixture["generated_at"] = now_iso()
    fixture["runtime_boundary"] = {
        "classification": "range_refined_signal_alert_drill_fixture",
        "can_trade": False,
        "sends_orders": False,
        "sends_telegram": False,
        "uses_private_credentials": False,
        "creates_paper_entry_intents": False,
    }

    selected = selected_candidate(observer)
    latest = latest_observer_result(observer)
    latest_bar = latest.get("latest_closed_bar_ts") or "2026-06-16T08:00:00+00:00"
    latest_close = latest.get("latest_closed_close") or 66456.0
    strategy_id = selected.get("strategy_id") or latest.get("strategy_id") or "range_refined_drill_strategy"
    base_strategy_id = selected.get("base_strategy_id") or latest.get("base_strategy_id") or "range_refined_drill_base"
    filter_mode = selected.get("filter_mode") or latest.get("filter_mode") or "funding_spot_oi_expansion"
    filters = selected.get("filters") if isinstance(selected.get("filters"), list) else ["funding_aligned", "spot_confirms", "oi_expansion"]
    interval = selected.get("interval") or latest.get("interval") or "4h"
    side = selected.get("side") or latest.get("side") or "SHORT"
    rr = selected.get("rr") or latest.get("rr") or "1:2"
    max_hold_bars = selected.get("max_hold_bars") or latest.get("max_hold_bars") or 16
    signal_key = f"drill|range_refined_signal_observed|{latest_bar}|{strategy_id}"
    funding = -0.0001 if side == "LONG" else 0.0001
    spot_divergence = 0.01 if side == "LONG" else -0.01
    oi_delta = -0.25 if "oi_contraction" in filters else 0.25
    filter_checks = {name: True for name in filters}

    signal = {
        "observer_id": "range_refined_forward_observer",
        "strategy_id": strategy_id,
        "base_strategy_id": base_strategy_id,
        "filter_mode": filter_mode,
        "filters": filters,
        "symbol": latest.get("symbol") or "BTCUSDT",
        "interval": interval,
        "side": side,
        "trigger": selected.get("trigger") or latest.get("trigger") or "near_high",
        "rr": rr,
        "max_hold_bars": max_hold_bars,
        "bar_ts": latest_bar,
        "bar_index": latest.get("latest_closed_bar_index") or latest.get("bar_index") or 318,
        "close": latest_close,
        "signal_key": signal_key,
        "atr": 1000.0,
        "feature_snapshot": {
            "range_high": 67000.0,
            "range_low": 64000.0,
            "width_atr": 3.0,
            "trend_atr": 0.2,
            "atr_ratio": 0.95,
            "rsi14": 42.0 if side == "LONG" else 58.0,
            "volume_z": 0.2,
            "funding": funding,
            "oi_delta_pct": oi_delta,
            "spot_perp_divergence_pct": spot_divergence,
        },
        "filter_checks": filter_checks,
        "missing_filter_inputs": [],
        "can_trade": False,
        "creates_paper_entry_intents": False,
        "sends_orders": False,
    }

    fixture["latest_result"] = {
        "generated_at": now_iso(),
        "status": "range_refined_signal_observed",
        "decision": "range_refined_signal_observed_observer_only",
        "next_action": "drill only: verify RANGE alert guard dry-run and duplicate-suppression path",
        "strategy_id": strategy_id,
        "base_strategy_id": base_strategy_id,
        "filter_mode": filter_mode,
        "filters": filters,
        "symbol": signal["symbol"],
        "interval": interval,
        "side": side,
        "latest_closed_bar_ts": latest_bar,
        "latest_closed_close": latest_close,
        "raw_signals_on_latest_bar": 1,
        "refined_signals_on_latest_bar": 1,
        "data_degraded": False,
        "missing_filter_inputs": [],
        "latest_signal": signal,
        "can_trade": False,
        "creates_paper_entry_intents": False,
        "sends_orders": False,
    }
    fixture["decision"] = "range_refined_signal_alert_drill_fixture_ready"
    fixture["can_trade"] = False
    return fixture


def run_guard(
    *,
    fixture_path: Path,
    state_path: Path,
    card_json_path: Path,
    card_md_path: Path,
    out_prefix: Path,
    timeout_s: int,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "tools/range_refined_signal_alert_guard.py",
        "--observer-json-path",
        rel_path(fixture_path),
        "--state-path",
        rel_path(state_path),
        "--card-json-path",
        rel_path(card_json_path),
        "--card-md-path",
        rel_path(card_md_path),
        "--out-prefix",
        rel_path(out_prefix),
        "--dry-run",
    ]
    started = time.time()
    env = dict(os.environ)
    env.setdefault("TELEGRAM_BOT_TOKEN", "DRILL_DRY_RUN_TOKEN")
    env.setdefault("TELEGRAM_CHAT_ID", "DRILL_DRY_RUN_CHAT")
    proc = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, timeout=timeout_s, env=env)
    guard_report = read_json(out_prefix.with_suffix(".json"), {})
    return {
        "command": command,
        "exit_code": proc.returncode,
        "duration_s": round(time.time() - started, 3),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
        "guard_report": guard_report if isinstance(guard_report, dict) else {},
    }


def display_path(value: Any) -> str:
    if value is None:
        return "None"
    return rel_path(Path(str(value)))


def render_markdown(report: dict[str, Any]) -> str:
    first = report.get("first_guard") if isinstance(report.get("first_guard"), dict) else {}
    duplicate = report.get("duplicate_guard") if isinstance(report.get("duplicate_guard"), dict) else {}
    first_report = first.get("guard_report") if isinstance(first.get("guard_report"), dict) else {}
    duplicate_report = duplicate.get("guard_report") if isinstance(duplicate.get("guard_report"), dict) else {}
    return "\n".join(
        [
            "# Range Refined Signal Alert Drill",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Synthetic fixture drill only.",
            "- Uses `--dry-run`; no Telegram message is sent.",
            "- Uses isolated state/card files under `_dl/range_signal_drills/`.",
            "- No paper entry, no exchange order, no private exchange credentials, no live permission.",
            "",
            "## Result",
            "",
            f"- Decision: `{report.get('decision')}`.",
            f"- Can trade: `{report.get('can_trade')}`.",
            f"- First guard exit: `{first.get('exit_code')}`.",
            f"- First guard decision: `{first_report.get('decision')}`.",
            f"- Duplicate guard exit: `{duplicate.get('exit_code')}`.",
            f"- Duplicate guard decision: `{duplicate_report.get('decision')}`.",
            "",
            "## Files",
            "",
            f"- Fixture: `{display_path(report.get('fixture_path'))}`.",
            f"- First guard report: `{display_path(report.get('first_guard_report'))}`.",
            f"- Duplicate guard report: `{display_path(report.get('duplicate_guard_report'))}`.",
            f"- Drill state: `{display_path(report.get('state_path'))}`.",
            f"- Drill card MD: `{display_path(report.get('card_md_path'))}`.",
            "",
        ]
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Dry-run fixture drill for RANGE refined signal alert guard")
    parser.add_argument("--observer-json-path", default="docs/RANGE_REFINED_FORWARD_OBSERVER_2026-06-16.json")
    parser.add_argument("--work-dir", default="_dl/range_signal_drills")
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_SIGNAL_ALERT_DRILL_2026-06-17")
    parser.add_argument("--timeout-s", type=int, default=30)
    args = parser.parse_args()

    observer_path = resolve_path(args.observer_json_path)
    work_dir = resolve_path(args.work_dir)
    out_prefix = resolve_path(args.out_prefix)
    observer = read_json(observer_path, {})
    if not isinstance(observer, dict) or not observer:
        report = {
            "generated_at": now_iso(),
            "decision": "blocked_missing_observer_report",
            "observer_path": rel_path(observer_path),
            "can_trade": False,
        }
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    work_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = work_dir / "range_refined_signal_observed_fixture.json"
    state_path = work_dir / "range_refined_signal_alert_drill_state.json"
    card_json_path = work_dir / "latest_range_refined_signal_card.json"
    card_md_path = work_dir / "latest_range_refined_signal_card.md"
    first_prefix = out_prefix.with_name(out_prefix.name + "_FIRST_NOTIFY")
    duplicate_prefix = out_prefix.with_name(out_prefix.name + "_DUPLICATE")

    if state_path.exists():
        state_path.unlink()
    fixture = make_signal_fixture(observer)
    write_json(fixture_path, fixture)

    first_result = run_guard(
        fixture_path=fixture_path,
        state_path=state_path,
        card_json_path=card_json_path,
        card_md_path=card_md_path,
        out_prefix=first_prefix,
        timeout_s=args.timeout_s,
    )
    duplicate_result = run_guard(
        fixture_path=fixture_path,
        state_path=state_path,
        card_json_path=card_json_path,
        card_md_path=card_md_path,
        out_prefix=duplicate_prefix,
        timeout_s=args.timeout_s,
    )
    first_report = first_result.get("guard_report") if isinstance(first_result, dict) else {}
    duplicate_report = duplicate_result.get("guard_report") if isinstance(duplicate_result, dict) else {}
    first_ok = (
        first_result.get("exit_code") == 0
        and isinstance(first_report, dict)
        and first_report.get("decision") == "dry_run_ready"
        and first_report.get("can_trade") is False
    )
    duplicate_ok = (
        duplicate_result.get("exit_code") == 0
        and isinstance(duplicate_report, dict)
        and duplicate_report.get("decision") == "skipped_duplicate"
        and duplicate_report.get("can_trade") is False
    )
    decision = "range_signal_alert_drill_passed" if first_ok and duplicate_ok else "range_signal_alert_drill_failed"
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_signal_alert_drill_dry_run_only",
            "can_trade": False,
            "sends_orders": False,
            "sends_telegram": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "observer_path": rel_path(observer_path),
        "fixture_path": rel_path(fixture_path),
        "state_path": rel_path(state_path),
        "card_json_path": rel_path(card_json_path),
        "card_md_path": rel_path(card_md_path),
        "first_guard_report": rel_path(first_prefix.with_suffix(".json")),
        "duplicate_guard_report": rel_path(duplicate_prefix.with_suffix(".json")),
        "first_guard": first_result,
        "duplicate_guard": duplicate_result,
        "decision": decision,
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "decision": decision,
                "first_decision": first_report.get("decision") if isinstance(first_report, dict) else None,
                "duplicate_decision": duplicate_report.get("decision") if isinstance(duplicate_report, dict) else None,
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if decision == "range_signal_alert_drill_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
