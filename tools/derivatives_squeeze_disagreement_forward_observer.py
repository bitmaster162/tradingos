#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_squeeze_disagreement_research import (  # noqa: E402
    Config,
    atr_values,
    confirmations,
    load_bars,
    pct_change,
    rolling_range_pctiles,
    simulate_trade,
    summarize,
)
from tools.forward_sample_integrity import (  # noqa: E402
    canonical_nonoverlap_events,
    last_exit_index,
    parse_ts,
)


OBSERVER_ID = "derivatives_squeeze_disagreement_forward_observer"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"emitted_signal_keys": []}
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("emitted_signal_keys", [])
    return payload


def config_from_lock(lock: dict[str, Any]) -> Config:
    cfg = lock.get("selected_config")
    if not isinstance(cfg, dict):
        raise ValueError("lock_missing_selected_config")
    return Config(
        interval=str(cfg["interval"]),
        side=str(cfg["side"]),
        squeeze_window=int(cfg["squeeze_window"]),
        squeeze_pctile_max=float(cfg["squeeze_pctile_max"]),
        lookback=int(cfg["lookback"]),
        oi_build_pct=float(cfg["oi_build_pct"]),
        funding_min_abs=float(cfg["funding_min_abs"]),
        spot_lead_min_pct=float(cfg["spot_lead_min_pct"]),
        crowd_extreme=float(cfg["crowd_extreme"]),
        min_confirmations=int(cfg["min_confirmations"]),
        stop_atr=float(cfg["stop_atr"]),
        take_atr=float(cfg["take_atr"]),
        max_hold_bars=int(cfg["max_hold_bars"]),
    )


def signal_key(lock_id: str, strategy_id: str, signal_ts: str) -> str:
    return f"{lock_id}|{strategy_id}|{signal_ts}"


def scan_new_resolved_events(lock: dict[str, Any], args: argparse.Namespace, state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cfg = config_from_lock(lock)
    cache_dir = resolve_path(args.cache_dir)
    bars = load_bars(cache_dir, cfg.interval)
    if len(bars) < max(250, cfg.lookback + cfg.squeeze_window + cfg.max_hold_bars + 5):
        return [], {"status": "blocked_not_enough_bars", "bars": len(bars)}

    atrs = atr_values(bars)
    squeeze = rolling_range_pctiles(bars, cfg.squeeze_window)
    emitted = set(str(item) for item in state.get("emitted_signal_keys", []))
    lock_id = str(lock.get("lock_id"))
    forward_start_at = str(lock.get("forward_start_at") or "")
    new_rows: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    last_exit = last_exit_index(bars, state.get("last_exit_ts"))
    start_index = max(220, cfg.squeeze_window, cfg.lookback, 14)

    for index in range(start_index, len(bars) - 1):
        bar = bars[index]
        if bar.ts <= forward_start_at:
            continue
        if index <= last_exit:
            continue
        atr = atrs[index]
        squeeze_rank = squeeze[index]
        if atr is None or atr <= 0 or squeeze_rank is None or squeeze_rank > cfg.squeeze_pctile_max:
            continue
        lookback_bar = bars[index - cfg.lookback]
        fut_ret = pct_change(bar.close, lookback_bar.close)
        spot_ret = pct_change(bar.spot_close, lookback_bar.spot_close)
        oi_chg = pct_change(bar.oi, lookback_bar.oi)
        count, feature_snapshot = confirmations(bar, lookback_bar, cfg, fut_ret, spot_ret, oi_chg)
        if count < cfg.min_confirmations:
            continue
        key = signal_key(lock_id, cfg.strategy_id, bar.ts)
        if key in emitted:
            continue
        # Signal eligibility uses only information available at the signal bar.
        if index + 1 + cfg.max_hold_bars > len(bars) - 1:
            pending.append({"signal_key": key, "signal_ts": bar.ts, "reason": "not_matured_max_hold_window"})
            continue
        trade = simulate_trade(bars, index + 1, cfg.side, atr, cfg.stop_atr, cfg.take_atr, cfg.max_hold_bars)
        event = {
            "observer_id": OBSERVER_ID,
            "lock_id": lock_id,
            "strategy_id": cfg.strategy_id,
            "generated_at": now_iso(),
            "signal_key": key,
            "signal_ts": bar.ts,
            "entry_ts": trade["entry_ts"],
            "exit_ts": trade["exit_ts"],
            "side": trade["side"],
            "entry": trade["entry"],
            "exit": trade["exit"],
            "r": trade["r"],
            "exit_reason": trade["exit_reason"],
            "hold_bars": trade["hold_bars"],
            "squeeze_rank": round(float(squeeze_rank), 6),
            "feature_snapshot": feature_snapshot,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        }
        new_rows.append(event)
        emitted.add(key)
        last_exit = int(trade["exit_index"])
        state["last_exit_ts"] = trade["exit_ts"]

    latest_bar_ts = bars[-1].ts if bars else None
    scan = {
        "status": "scan_ok",
        "bars": len(bars),
        "latest_bar_ts": latest_bar_ts,
        "pending": pending[-5:],
        "new_resolved_events": len(new_rows),
    }
    state["emitted_signal_keys"] = sorted(emitted)
    state["last_scan_at"] = now_iso()
    state["latest_bar_ts"] = latest_bar_ts
    return new_rows, scan


def decide(lock: dict[str, Any], all_events: list[dict[str, Any]], scan: dict[str, Any]) -> tuple[str, list[str], str]:
    gate = lock.get("forward_gate_required") if isinstance(lock.get("forward_gate_required"), dict) else {}
    min_events = int(gate.get("minimum_new_resolved_signals") or 30)
    min_exp = float(gate.get("minimum_expectancy_r") or 0.05)
    min_pf = float(gate.get("minimum_profit_factor") or 1.08)
    max_dd = float(gate.get("maximum_drawdown_r") or -12.0)
    summary = summarize(all_events)
    blockers: list[str] = []
    if scan.get("status") != "scan_ok":
        return str(scan.get("status")), [str(scan.get("status"))], "fix input/cache before forward observation"
    if int(summary.get("trades") or 0) < min_events:
        blockers.append("minimum_new_resolved_signals")
        return "derivatives_squeeze_forward_observer_collecting_sample", blockers, "keep observing; no paper/live discussion before minimum forward sample"
    if float(summary.get("expectancy_r") or 0.0) < min_exp:
        blockers.append("minimum_expectancy_r")
    if summary.get("profit_factor") is None or float(summary["profit_factor"]) < min_pf:
        blockers.append("minimum_profit_factor")
    if float(summary.get("max_drawdown_r") or 0.0) < max_dd:
        blockers.append("maximum_drawdown_r")
    if blockers:
        return "derivatives_squeeze_forward_observer_failed_gate_for_tombstone_review", blockers, "manual tombstone review; do not retune on forward failure"
    return "derivatives_squeeze_forward_observer_passed_for_manual_review", [], "manual review required before any paper-design discussion"


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    latest = report.get("latest_scan", {})
    return "\n".join([
        "# Derivatives Squeeze Disagreement Forward Observer",
        "",
        f"Generated: `{report.get('generated_at')}`",
        f"Decision: `{report.get('decision')}`",
        "",
        "## Boundary",
        "",
        "- Observer-only forward evidence collection.",
        "- No alerts, no paper-entry intents, no orders, no private credentials.",
        "- Parameters are frozen by lock; no retuning on forward outcomes.",
        "",
        "## Candidate",
        "",
        f"- Lock: `{report.get('lock_id')}`.",
        f"- Strategy: `{report.get('strategy_id')}`.",
        f"- Forward start: `{report.get('forward_start_at')}`.",
        "",
        "## Forward Sample",
        "",
        f"- Resolved events: `{summary.get('trades')}`.",
        f"- Winrate: `{summary.get('winrate_pct')}`.",
        f"- Expectancy R: `{summary.get('expectancy_r')}`.",
        f"- Profit factor: `{summary.get('profit_factor')}`.",
        f"- Max drawdown R: `{summary.get('max_drawdown_r')}`.",
        f"- New events this run: `{latest.get('new_resolved_events')}`.",
        f"- Latest bar: `{latest.get('latest_bar_ts')}`.",
        f"- Raw/canonical/excluded rows: `{report.get('sample_integrity', {}).get('journal_rows')}` / "
        f"`{report.get('sample_integrity', {}).get('canonical_nonoverlap_rows')}` / "
        f"`{report.get('sample_integrity', {}).get('excluded_rows')}`.",
        "",
        "## Gate",
        "",
        f"- Blockers: `{report.get('blockers')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        "",
    ])


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    lock_path = resolve_path(args.lock)
    journal_path = resolve_path(args.journal_path)
    state_path = resolve_path(args.state_path)
    if not lock_path.is_file():
        raise FileNotFoundError(f"missing lock: {lock_path}")
    lock = read_json(lock_path)
    if lock.get("status") != "accepted_forward_observer_only":
        raise ValueError(f"lock_not_accepted_forward_observer_only: {lock.get('status')}")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        raise ValueError("unsafe_lock_boundary")
    cfg = config_from_lock(lock)
    state = load_state(state_path)
    existing_rows = [
        row for row in read_jsonl(journal_path)
        if row.get("lock_id") == lock.get("lock_id") and row.get("strategy_id") == cfg.strategy_id
    ]
    existing_canonical, _ = canonical_nonoverlap_events(existing_rows)
    if existing_canonical:
        state["last_exit_ts"] = existing_canonical[-1].get("exit_ts")
    new_rows, scan = scan_new_resolved_events(lock, args, state)
    if new_rows:
        append_jsonl(journal_path, new_rows)
    write_json(state_path, state)
    journal_rows = [
        row for row in read_jsonl(journal_path)
        if row.get("lock_id") == lock.get("lock_id") and row.get("strategy_id") == cfg.strategy_id
    ]
    canonical_events, excluded_events = canonical_nonoverlap_events(journal_rows)
    if canonical_events:
        state["last_exit_ts"] = canonical_events[-1].get("exit_ts")
        write_json(state_path, state)
    decision, blockers, next_action = decide(lock, canonical_events, scan)
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "observer_id": OBSERVER_ID,
        "decision": decision,
        "lock_id": lock.get("lock_id"),
        "strategy_id": cfg.strategy_id,
        "forward_start_at": lock.get("forward_start_at"),
        "source_research_report": lock.get("source_research_report"),
        "lock_path": portable(lock_path),
        "journal_path": portable(journal_path),
        "state_path": portable(state_path),
        "latest_scan": scan,
        "summary": summarize(canonical_events),
        "sample_integrity": {
            "policy": "append_only_journal_with_canonical_nonoverlap_scoring_v2",
            "journal_rows": len(journal_rows),
            "canonical_nonoverlap_rows": len(canonical_events),
            "excluded_rows": len(excluded_events),
            "excluded": [
                {
                    "signal_key": row.get("signal_key"),
                    "signal_ts": row.get("signal_ts"),
                    "exit_ts": row.get("exit_ts"),
                    "reason": row.get("sample_exclusion_reason"),
                }
                for row in excluded_events
            ],
            "last_exit_ts": state.get("last_exit_ts"),
            "raw_journal_mutated": False,
        },
        "blockers": blockers,
        "next_action": next_action,
        "runtime_boundary": {
            "observer_only": True,
            "alerts_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def write_outputs(report: dict[str, Any], out_prefix: str) -> None:
    prefix = resolve_path(out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Observer-only forward runner for locked derivatives squeeze disagreement candidate")
    parser.add_argument("--lock", default="configs/DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_LOCK_2026-07-03.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/derivatives_squeeze_disagreement_forward_observer.jsonl")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/derivatives_squeeze_disagreement_forward_observer_state.json")
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_SQUEEZE_DISAGREEMENT_FORWARD_OBSERVER_2026-07-03")
    args = parser.parse_args()
    report = run_once(args)
    write_outputs(report, args.out_prefix)
    print(json.dumps({
        "decision": report["decision"],
        "resolved": report["summary"]["trades"],
        "new_resolved_events": report["latest_scan"].get("new_resolved_events"),
        "can_trade": report["can_trade"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
