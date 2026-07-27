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


def read_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.is_file():
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


def run_command(args: list[str], timeout_s: int) -> dict[str, Any]:
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
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def render_markdown(report: dict[str, Any]) -> str:
    evidence = report["evidence"]
    lines = [
        "# Liquidation Timing + Volatility Forward Observer Runner",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        "- Orders allowed: `false`",
        "",
        "## Lock",
        "",
        f"- Lock ID: `{report['lock'].get('lock_id')}`",
        f"- Setup: `{report['selected_setup'].get('setup')}`",
        f"- After bar: `{report['selected_setup'].get('after_bar_ts')}`",
        "",
        "## Evidence",
        "",
        f"- Context rows after refresh: `{evidence.get('context_rows')}`",
        f"- Forward records: `{evidence.get('records')}`",
        f"- Selected bucket min N: `{evidence.get('selected_bucket_min_n')}`",
        f"- Selected symbols: `{', '.join(evidence.get('selected_symbols') or []) or 'none'}`",
        f"- Positive horizons: `{evidence.get('positive_horizons')}`",
        "",
        "## Selected Groups",
        "",
        "| Horizon | N | Mean bps | Median bps | Winrate | Symbols |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for group in report.get("selected_groups") or []:
        cont = group.get("continuation") if isinstance(group.get("continuation"), dict) else {}
        lines.append(
            f"| `{group.get('horizon_bars')}` | `{cont.get('n')}` | `{cont.get('mean_bps')}` | "
            f"`{cont.get('median_bps')}` | `{cont.get('winrate_positive_pct')}` | "
            f"`{','.join(group.get('symbols') or [])}` |"
        )
    lines.extend(["", "## Blockers", ""])
    for blocker in report.get("blockers") or ["none"]:
        lines.append(f"- `{blocker}`")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Context intake: `{report['artifacts'].get('context_report')}`",
            f"- Context CSV: `{report['artifacts'].get('context_csv')}`",
            f"- Observer report: `{report['artifacts'].get('observer_report')}`",
            "",
            "## Boundary",
            "",
            "- Refreshes context and scores one locked bucket only.",
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
    parser = argparse.ArgumentParser(description="Safe runner for locked liquidation timing + volatility continuation observer.")
    parser.add_argument("--lock", default="configs/LIQUIDATION_TIMING_VOL_CONTINUATION_FORWARD_LOCK_2026-07-03.json")
    parser.add_argument("--context-out-prefix", default="docs/LIQUIDATION_TIMING_VOL_CONTEXT_REFRESH_2026-07-03_FORWARD")
    parser.add_argument("--observer-out-prefix", default="docs/LIQUIDATION_TIMING_VOL_CONTINUATION_FORWARD_OBSERVER_2026-07-03_REFRESH")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_TIMING_VOL_FORWARD_OBSERVER_RUNNER_2026-07-03")
    parser.add_argument("--state-path", default="logs/liquidation_bybit/liquidation_timing_vol_forward_observer_state.json")
    parser.add_argument("--history-path", default="logs/liquidation_bybit/liquidation_timing_vol_forward_observer_history.jsonl")
    parser.add_argument("--timeout-s", type=int, default=180)
    args = parser.parse_args()

    lock_path = resolve_path(args.lock)
    lock = read_json(lock_path)
    boundary = lock.get("runtime_boundary") if isinstance(lock.get("runtime_boundary"), dict) else {}
    if boundary.get("can_trade") is not False or boundary.get("orders_allowed") is not False:
        raise SystemExit("forward lock must keep can_trade=false and orders_allowed=false")
    selected = lock.get("selected_setup") if isinstance(lock.get("selected_setup"), dict) else {}
    fixed = lock.get("fixed_parameters") if isinstance(lock.get("fixed_parameters"), dict) else {}
    gate = lock.get("forward_gate") if isinstance(lock.get("forward_gate"), dict) else {}
    setup = str(selected.get("setup") or "")
    interval = str(selected.get("interval") or "1h")
    horizons = [int(item) for item in selected.get("horizons_bars", [])]
    symbols = ",".join(selected.get("symbols") or ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    after_bar_ts = str(selected.get("after_bar_ts") or "")
    if not setup or not horizons or not after_bar_ts:
        raise SystemExit("lock selected_setup must include setup, horizons_bars and after_bar_ts")

    context_prefix = resolve_path(args.context_out_prefix)
    observer_prefix = resolve_path(args.observer_out_prefix)
    context_cmd = [
        sys.executable,
        "tools/bybit_all_liquidation_context_intake.py",
        "--symbols",
        symbols,
        "--interval",
        interval,
        "--out-prefix",
        portable(context_prefix),
    ]
    context_result = run_command(context_cmd, args.timeout_s)
    context_report_path = context_prefix.with_suffix(".json")
    context_report = read_json(context_report_path)
    context_csv = context_report.get("aggregate_csv") or f"{portable(context_prefix)}_bar_context.csv"

    observer_cmd = [
        sys.executable,
        "tools/liquidation_timing_vol_continuation.py",
        "--context-csv",
        str(context_csv),
        "--symbols",
        symbols,
        "--interval",
        interval,
        "--horizons",
        ",".join(str(item) for item in horizons),
        "--lookback-bars",
        str(fixed.get("lookback_bars_for_prior_range") or 24),
        "--cost-bps",
        str(fixed.get("cost_bps") or 4.0),
        "--after-bar-ts",
        after_bar_ts,
        "--selected-setup",
        setup,
        "--selected-horizons",
        ",".join(str(item) for item in horizons),
        "--min-selected-events",
        str(gate.get("minimum_new_events") or 30),
        "--min-selected-symbols",
        str(gate.get("minimum_new_symbols") or 2),
        "--min-positive-horizons",
        str(gate.get("minimum_positive_horizons") or 1),
        "--minimum-mean-bps",
        str(gate.get("minimum_mean_bps_after_cost_buffer") or 15.0),
        "--minimum-winrate-pct",
        str(gate.get("minimum_winrate_pct") or 55.0),
        "--out-prefix",
        portable(observer_prefix),
    ]
    observer_result = run_command(observer_cmd, args.timeout_s)
    observer_report_path = observer_prefix.with_suffix(".json")
    observer = read_json(observer_report_path)
    evidence = observer.get("evidence") if isinstance(observer.get("evidence"), dict) else {}
    selected_groups = observer.get("selected_groups") if isinstance(observer.get("selected_groups"), list) else []
    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_timing_vol_forward_observer_runner.py",
        "decision": observer.get("decision") or "liquidation_timing_vol_forward_observer_unknown",
        "next_action": observer.get("next_action") or "inspect observer report",
        "can_trade": False,
        "orders_allowed": False,
        "lock_path": portable(lock_path),
        "lock": {"lock_id": lock.get("lock_id")},
        "selected_setup": selected,
        "evidence": evidence,
        "selected_groups": selected_groups,
        "blockers": observer.get("blockers") if isinstance(observer.get("blockers"), list) else [],
        "artifacts": {
            "context_report": portable(context_report_path),
            "context_csv": str(context_csv),
            "observer_report": portable(observer_report_path),
        },
        "commands": {
            "context_intake": context_result,
            "observer": observer_result,
        },
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
        "decision": report["decision"],
        "selected_bucket_min_n": evidence.get("selected_bucket_min_n"),
        "positive_horizons": evidence.get("positive_horizons"),
        "blockers": report["blockers"],
        "can_trade": False,
    }
    write_json(resolve_path(args.state_path), state)
    append_jsonl(resolve_path(args.history_path), state)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "selected_bucket_min_n": evidence.get("selected_bucket_min_n"),
                "positive_horizons": evidence.get("positive_horizons"),
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
