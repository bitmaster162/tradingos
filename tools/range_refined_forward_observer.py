#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.range_family_validator import generate_signals, load_interval_payload  # noqa: E402
from tools.range_watchlist_refiner import (  # noqa: E402
    FILTER_FUNCS,
    apply_filter_mode,
    config_from_row,
    safe_float,
)


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


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def historical_rejection(refiner_path: Path, nested_holdout_path: Path) -> dict[str, Any] | None:
    if not refiner_path.name.startswith("RANGE_WATCHLIST_REFINER") or not nested_holdout_path.exists():
        return None
    payload = read_json(nested_holdout_path)
    for row in payload.get("families", []):
        if isinstance(row, dict) and row.get("family") == "RANGE_REFINED_4H" and str(row.get("decision") or "").startswith("reject_oos"):
            return row
    return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"emitted_signal_keys": []}
    try:
        state = read_json(path)
    except (OSError, json.JSONDecodeError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("emitted_signal_keys", [])
    return state


def selected_candidate(refiner_report: dict[str, Any]) -> dict[str, Any]:
    selected = refiner_report.get("selected_candidate")
    if isinstance(selected, dict):
        return selected
    top = refiner_report.get("top_results")
    if isinstance(top, list) and top and isinstance(top[0], dict):
        return top[0]
    raise ValueError("missing_selected_range_refined_candidate")


def build_config(selected: dict[str, Any], source_report: dict[str, Any]) -> Any:
    settings = source_report.get("settings") if isinstance(source_report.get("settings"), dict) else {}
    base_strategy_id = str(selected.get("base_strategy_id") or "")
    rows = source_report.get("results") if isinstance(source_report.get("results"), list) else []
    base_row = next((row for row in rows if isinstance(row, dict) and row.get("strategy_id") == base_strategy_id), None)
    if not isinstance(base_row, dict):
        raise ValueError(f"base_range_row_not_found:{base_strategy_id}")
    config = config_from_row(base_row, settings)
    return replace(config, strategy_id=str(selected.get("strategy_id") or config.strategy_id))


def filter_diagnostics(config: Any, signal: dict[str, Any], filter_names: list[str]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    missing: list[str] = []
    for name in filter_names:
        func = FILTER_FUNCS.get(name)
        if func is None:
            checks[name] = None
            continue
        passed = bool(func(config, signal))
        checks[name] = passed
        snapshot = signal.get("feature_snapshot") if isinstance(signal.get("feature_snapshot"), dict) else {}
        if name.startswith("funding") and snapshot.get("funding") is None:
            missing.append("funding")
        if name.startswith("oi") and snapshot.get("oi_delta_pct") is None:
            missing.append("oi_delta_pct")
        if name.startswith("spot") and snapshot.get("spot_perp_divergence_pct") is None:
            missing.append("spot_perp_divergence_pct")
        if name.startswith("volume") and snapshot.get("volume_z") is None:
            missing.append("volume_z")
    return {
        "filter_checks": checks,
        "missing_filter_inputs": sorted(set(missing)),
        "data_degraded": bool(missing),
    }


def event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "ts_emitted": now_iso(),
        "can_trade": False,
        "sends_orders": False,
        "uses_private_credentials": False,
        **payload,
    }


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest_result") if isinstance(report.get("latest_result"), dict) else {}
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    lines = [
        "# Range Refined Forward Observer",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Observer-only forward comparison for the selected refined RANGE candidate.",
        "- Uses local forward cache and cached public derivatives/spot context.",
        "- Does not create paper-entry intents.",
        "- Does not send orders and does not grant live permission.",
        "",
        "## Selected Candidate",
        "",
        f"- Base: `{selected.get('base_strategy_id')}`.",
        f"- Strategy: `{selected.get('strategy_id')}`.",
        f"- Filter: `{selected.get('filter_mode')}` (`{'+'.join(selected.get('filters') or [])}`).",
        f"- TF / side / RR: `{selected.get('interval')}` / `{selected.get('side')}` / `{selected.get('rr')}`.",
        "",
        "## Latest Observation",
        "",
        f"- Status: `{latest.get('status')}`.",
        f"- Latest closed bar: `{latest.get('latest_closed_bar_ts')}` close `{latest.get('latest_closed_close')}`.",
        f"- Raw range signals on latest bar: `{latest.get('raw_signals_on_latest_bar')}`.",
        f"- Refined signals on latest bar: `{latest.get('refined_signals_on_latest_bar')}`.",
        f"- Data degraded: `{latest.get('data_degraded')}`.",
        f"- Missing filter inputs: `{latest.get('missing_filter_inputs')}`.",
        f"- Journal: `{report.get('journal_path')}`.",
        f"- State: `{report.get('state_path')}`.",
        "",
        "## Decision",
        "",
        f"- `{report.get('decision')}`.",
        "",
    ]
    return "\n".join(lines)


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    refiner_path = resolve_path(args.refiner_report)
    source_path = resolve_path(args.source_range_report)
    cache_dir = resolve_path(args.cache_dir)
    journal_path = resolve_path(args.journal_path)
    state_path = resolve_path(args.state_path)

    rejection = historical_rejection(refiner_path, resolve_path(args.nested_holdout))
    if rejection:
        selected = selected_candidate(read_json(refiner_path))
        return {
            "status": "candidate_paused_historical_rejection",
            "strategy_id": selected.get("strategy_id"),
            "base_strategy_id": selected.get("base_strategy_id"),
            "filter_mode": selected.get("filter_mode"),
            "filters": list(selected.get("filters") or []),
            "symbol": args.symbol.upper(),
            "interval": selected.get("interval"),
            "side": selected.get("side"),
            "latest_closed_bar_ts": None,
            "latest_closed_close": None,
            "raw_signals_on_latest_bar": 0,
            "refined_signals_on_latest_bar": 0,
            "data_degraded": False,
            "missing_filter_inputs": [],
            "events_written": 0,
            "latest_signal": None,
            "journal_path": str(journal_path),
            "state_path": str(state_path),
            "cache_dir": str(cache_dir),
            "historical_invalidation": rejection,
            "can_trade": False,
            "sends_orders": False,
        }

    refiner_report = read_json(refiner_path)
    source_report = read_json(source_path)
    selected = selected_candidate(refiner_report)
    config = build_config(selected, source_report)
    filter_names = list(selected.get("filters") or [])
    unknown_filters = [name for name in filter_names if name not in FILTER_FUNCS]
    if unknown_filters:
        raise ValueError(f"unknown_filters:{','.join(unknown_filters)}")

    bars, features, rsi14 = load_interval_payload(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError(f"no_bars_loaded:{rel_path(cache_dir)}:{config.interval}")
    latest_index = len(bars) - 1
    latest_bar = bars[latest_index]
    raw_signals = generate_signals(config, bars, features, rsi14, latest_index, latest_index + 1)
    refined_signals = apply_filter_mode(config, raw_signals, tuple(filter_names))

    state = load_state(state_path)
    previous_strategy_id = str(state.get("strategy_id") or "")
    strategy_changed = bool(previous_strategy_id and previous_strategy_id != config.strategy_id)
    if strategy_changed:
        state["emitted_signal_keys"] = []
        state["previous_strategy_id"] = previous_strategy_id
        state["strategy_rollover_at"] = now_iso()
    emitted_keys = set(str(item) for item in state.get("emitted_signal_keys", []))
    events: list[dict[str, Any]] = []
    status = "range_refined_no_signal"
    latest_signal_payload: dict[str, Any] | None = None
    data_degraded = False
    missing_filter_inputs: list[str] = []

    common = {
        "observer_id": "range_refined_forward_observer",
        "strategy_id": config.strategy_id,
        "base_strategy_id": selected.get("base_strategy_id"),
        "filter_mode": selected.get("filter_mode"),
        "filters": filter_names,
        "symbol": args.symbol.upper(),
        "interval": config.interval,
        "side": config.side,
        "trigger": config.trigger,
        "rr": selected.get("rr"),
        "max_hold_bars": config.max_hold_bars,
        "bar_ts": latest_bar.ts,
        "bar_index": latest_index,
        "close": round(float(latest_bar.close), 8),
    }

    if refined_signals:
        signal = refined_signals[0]
        diagnostics = filter_diagnostics(config, signal, filter_names)
        data_degraded = bool(diagnostics["data_degraded"])
        missing_filter_inputs = list(diagnostics["missing_filter_inputs"])
        signal_key = f"{config.strategy_id}|{latest_bar.ts}|observer"
        latest_signal_payload = {
            **common,
            "signal_key": signal_key,
            "atr": round(safe_float(signal.get("atr"), 0.0), 8),
            "feature_snapshot": signal.get("feature_snapshot", {}),
            **diagnostics,
        }
        if signal_key in emitted_keys:
            status = "range_refined_duplicate_signal"
            events.append(event("range_refined_duplicate_signal", **latest_signal_payload))
        else:
            status = "range_refined_signal_observed"
            events.append(event("range_refined_signal_observed", **latest_signal_payload))
            emitted_keys.add(signal_key)
    elif raw_signals:
        signal = raw_signals[0]
        diagnostics = filter_diagnostics(config, signal, filter_names)
        data_degraded = bool(diagnostics["data_degraded"])
        missing_filter_inputs = list(diagnostics["missing_filter_inputs"])
        latest_signal_payload = {
            **common,
            "atr": round(safe_float(signal.get("atr"), 0.0), 8),
            "feature_snapshot": signal.get("feature_snapshot", {}),
            **diagnostics,
        }
        status = "range_refined_filtered_out"
        events.append(event("range_refined_filtered_out", **latest_signal_payload))
    else:
        events.append(
            event(
                "range_refined_no_signal",
                **common,
                filter_checks={name: None for name in filter_names},
                missing_filter_inputs=[],
                data_degraded=False,
            )
        )

    append_jsonl(journal_path, events)
    state.update(
        {
            "last_run_at": now_iso(),
            "last_status": status,
            "last_closed_bar_ts": latest_bar.ts,
            "strategy_id": config.strategy_id,
            "filter_mode": selected.get("filter_mode"),
            "strategy_changed": strategy_changed,
            "previous_strategy_id": previous_strategy_id or None,
            "emitted_signal_keys": sorted(emitted_keys)[-args.max_state_keys :],
        }
    )
    write_json(state_path, state)
    return {
        "status": status,
        "strategy_id": config.strategy_id,
        "base_strategy_id": selected.get("base_strategy_id"),
        "filter_mode": selected.get("filter_mode"),
        "filters": filter_names,
        "strategy_changed": strategy_changed,
        "previous_strategy_id": previous_strategy_id or None,
        "symbol": args.symbol.upper(),
        "interval": config.interval,
        "side": config.side,
        "latest_closed_bar_ts": latest_bar.ts,
        "latest_closed_close": round(float(latest_bar.close), 8),
        "raw_signals_on_latest_bar": len(raw_signals),
        "refined_signals_on_latest_bar": len(refined_signals),
        "data_degraded": data_degraded,
        "missing_filter_inputs": missing_filter_inputs,
        "events_written": len(events),
        "latest_signal": latest_signal_payload,
        "journal_path": str(journal_path),
        "state_path": str(state_path),
        "cache_dir": str(cache_dir),
        "can_trade": False,
        "sends_orders": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Observer-only forward comparison for selected refined RANGE candidate")
    parser.add_argument("--refiner-report", default="docs/RANGE_WATCHLIST_REFINER_2026-06-16.json")
    parser.add_argument("--source-range-report", default="docs/RANGE_FAMILY_VALIDATOR_2026-06-16.json")
    parser.add_argument("--nested-holdout", default="docs/RANGE_EDGE_NESTED_HOLDOUT_2026-06-23.json")
    parser.add_argument("--cache-dir", default="_dl/forward_paper_feed/cache")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/range_refined_forward_observer.jsonl")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/range_refined_forward_observer_state.json")
    parser.add_argument("--max-state-keys", type=int, default=500)
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_FORWARD_OBSERVER_2026-06-16")
    args = parser.parse_args()

    latest = run_once(args)
    refiner_report = read_json(resolve_path(args.refiner_report))
    selected = selected_candidate(refiner_report)
    out_prefix = resolve_path(args.out_prefix)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_forward_observer_public_cache_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "selected_candidate": selected,
        "latest_result": latest,
        "journal_path": latest["journal_path"],
        "state_path": latest["state_path"],
        "decision": (
            "paused_historical_rejection_no_orders"
            if latest["status"] == "candidate_paused_historical_rejection"
            else "observer_only_no_orders_no_paper_entry"
        ),
        "next_action": (
            "preserve_evidence_and_do_not_reselect_on_open_oos"
            if latest["status"] == "candidate_paused_historical_rejection"
            else "accumulate_forward_observations_before_any_promotion_review"
        ),
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "latest_status": latest["status"],
                "raw_signals_on_latest_bar": latest["raw_signals_on_latest_bar"],
                "refined_signals_on_latest_bar": latest["refined_signals_on_latest_bar"],
                "data_degraded": latest["data_degraded"],
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
