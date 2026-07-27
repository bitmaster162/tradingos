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

from tools.edge_compression_guard_diagnostic import (  # noqa: E402
    atr_ratio,
    default_guards,
    guard_id,
    guard_keep,
    range_position,
)
from tools.edge_same_shape_shadow_observer import config_from_candidate  # noqa: E402
from tools.range_family_validator import generate_signals, load_interval_payload  # noqa: E402
from tools.range_watchlist_refiner import apply_filter_mode, make_filters  # noqa: E402


DEFAULT_DIAGNOSTIC = ROOT / "docs" / "EDGE_CANDIDATE_HARDENING_DIAGNOSTIC_2026-06-19.json"
DEFAULT_COMPRESSION_DIAGNOSTIC = ROOT / "docs" / "EDGE_COMPRESSION_GUARD_DIAGNOSTIC_2026-06-19.json"
DEFAULT_RANGE_REPORT = ROOT / "docs" / "RANGE_SWEEP_RECLAIM_REFINER_2026-06-18.json"
DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "binance_spot_perp_extended"
DEFAULT_JOURNAL = ROOT / "logs" / "forward_paper_feed" / "edge_compression_guard_shadow_observer.jsonl"
DEFAULT_STATE = ROOT / "logs" / "forward_paper_feed" / "edge_compression_guard_shadow_observer_state.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "EDGE_COMPRESSION_GUARD_SHADOW_OBSERVER_2026-06-19"


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


def read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = {}
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def select_shadow_guard(compression_report: dict[str, Any]) -> dict[str, Any] | None:
    rows = compression_report.get("results") if isinstance(compression_report.get("results"), list) else []
    scored: list[tuple[tuple[float, float, float, int], dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("guard_id") == "baseline_no_extra_guard":
            continue
        comp = row.get("comparison_to_baseline") if isinstance(row.get("comparison_to_baseline"), dict) else {}
        delta_holdout = comp.get("delta_holdout_expectancy_r")
        delta_full = comp.get("delta_full_expectancy_r")
        delta_cost = comp.get("delta_cost10_expectancy_r")
        try:
            dh = float(delta_holdout)
            df = float(delta_full)
            dc = float(delta_cost)
        except (TypeError, ValueError):
            continue
        if dh <= 0 or df < 0 or dc < 0:
            continue
        scored.append(((dh, dc, df, int(row.get("signals_after_guard") or 0)), row))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1].get("guard") if isinstance(scored[0][1].get("guard"), dict) else None
    for guard in default_guards():
        if guard_id(guard) == "veto_compression_any_ar0.85":
            return guard
    return None


def signal_payload(signal: dict[str, Any], bar: Any, index: int) -> dict[str, Any]:
    return {
        "bar_index": index,
        "bar_ts": str(bar.ts),
        "close": round(float(bar.close), 8),
        "atr": signal.get("atr"),
        "reason": signal.get("reason"),
        "range_position": None if range_position(signal) is None else round(float(range_position(signal)), 6),
        "atr_ratio": None if atr_ratio(signal) is None else round(float(atr_ratio(signal)), 6),
        "feature_snapshot": signal.get("feature_snapshot") if isinstance(signal.get("feature_snapshot"), dict) else {},
    }


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest") if isinstance(report.get("latest"), dict) else {}
    lines = [
        "# Edge Compression Guard Shadow Observer",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Observer-only compression guard shadow.",
        "- Does not change the active edge candidate.",
        "- Does not create paper-entry intents or send orders.",
        "",
        "## Latest",
        "",
        f"- Status: `{latest.get('status')}`.",
        f"- Latest bar: `{latest.get('latest_closed_bar_ts')}` close `{latest.get('latest_closed_close')}`.",
        f"- Guard: `{latest.get('guard_id')}`.",
        f"- Raw/refined signals: `{latest.get('raw_signals')}` / `{latest.get('refined_signals')}`.",
        f"- Guard action: `{latest.get('guard_action')}`.",
        f"- Journal: `{report.get('journal_path')}`.",
        "",
        "## Interpretation",
        "",
        "- `keep` means the guard would allow the active candidate's signal.",
        "- `veto` means the guard would block it; scoreboard later checks whether that would have avoided loss.",
        "- This is not trade permission.",
        "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    diagnostic_path = resolve_path(args.diagnostic)
    compression_path = resolve_path(args.compression_diagnostic)
    range_report_path = resolve_path(args.range_report)
    cache_dir = resolve_path(args.cache_dir)
    journal_path = resolve_path(args.journal_path)
    state_path = resolve_path(args.state_path)
    out_prefix = resolve_path(args.out_prefix)
    diagnostic = read_json(diagnostic_path)
    compression_report = read_json(compression_path)
    range_report = read_json(range_report_path)
    selected = diagnostic.get("selected_candidate") if isinstance(diagnostic.get("selected_candidate"), dict) else {}
    settings = range_report.get("settings") if isinstance(range_report.get("settings"), dict) else {}
    guard = select_shadow_guard(compression_report)
    if not selected:
        raise ValueError("selected_candidate_missing")
    if guard is None:
        raise ValueError("shadow_guard_missing")

    config = config_from_candidate(selected, settings)
    bars, features, rsi14 = load_interval_payload(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError(f"no_bars_for_interval:{config.interval}")
    latest_index = len(bars) - 1
    latest_bar = bars[latest_index]
    raw_signals = generate_signals(config, bars, features, rsi14, latest_index, latest_index + 1)
    filter_names = tuple(make_filters().get(str(selected.get("filter_mode") or ""), tuple(selected.get("filters") or ())))
    refined = apply_filter_mode(config, raw_signals, filter_names)
    if not raw_signals:
        status = "compression_guard_no_base_signal"
        guard_action = None
        latest_signal = None
    elif not refined:
        status = "compression_guard_base_filtered_out"
        guard_action = None
        latest_signal = None
    else:
        latest_signal = signal_payload(refined[0], latest_bar, latest_index)
        keep = guard_keep(refined[0], guard)
        guard_action = "keep" if keep else "veto"
        status = "compression_guard_keep_signal_observed" if keep else "compression_guard_veto_signal_observed"

    event = {
        "event_type": "edge_compression_guard_shadow_state",
        "ts_emitted": now_iso(),
        "strategy_id": selected.get("strategy_id"),
        "base_strategy_id": selected.get("base_strategy_id"),
        "guard_id": guard_id(guard),
        "guard": guard,
        "status": status,
        "guard_action": guard_action,
        "bar_ts": str(latest_bar.ts),
        "symbol": args.symbol.upper(),
        "interval": config.interval,
        "side": config.side,
        "rr": selected.get("rr"),
        "max_hold_bars": selected.get("max_hold_bars"),
        "raw_signals": len(raw_signals),
        "refined_signals": len(refined),
        "latest_signal": latest_signal,
        "historical_evidence": {
            "compression_diagnostic": rel_path(compression_path),
            "selected_guard_source": "positive_delta_low_sample_shadow" if compression_report else "fallback_default",
        },
        "can_trade": False,
        "creates_paper_entry_intents": False,
        "sends_orders": False,
        "uses_private_credentials": False,
    }
    append_jsonl(journal_path, [event])
    latest = {
        "status": status,
        "latest_closed_bar_ts": str(latest_bar.ts),
        "latest_closed_close": round(float(latest_bar.close), 8),
        "guard_id": guard_id(guard),
        "guard": guard,
        "raw_signals": len(raw_signals),
        "refined_signals": len(refined),
        "guard_action": guard_action,
        "latest_signal": latest_signal,
    }
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "mode": "observer_only",
            "can_trade": False,
            "creates_paper_entry_intents": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "changes_active_strategy": False,
        },
        "inputs": {
            "diagnostic": rel_path(diagnostic_path),
            "compression_diagnostic": rel_path(compression_path),
            "range_report": rel_path(range_report_path),
            "cache_dir": rel_path(cache_dir),
        },
        "latest": latest,
        "journal_path": rel_path(journal_path),
        "state_path": rel_path(state_path),
        "decision": "compression_guard_shadow_observer_no_trade_permission",
        "next_action": "score future keep/veto outcomes before considering guard promotion",
        "can_trade": False,
    }
    write_json(state_path, {"updated_at": now_iso(), "latest": latest, "can_trade": False})
    write_json(out_prefix.with_suffix(".json"), report)
    write_text(out_prefix.with_suffix(".md"), render_markdown(report))
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Observer-only shadow monitor for compression guard on current edge candidate.")
    parser.add_argument("--diagnostic", default=str(DEFAULT_DIAGNOSTIC))
    parser.add_argument("--compression-diagnostic", default=str(DEFAULT_COMPRESSION_DIAGNOSTIC))
    parser.add_argument("--range-report", default=str(DEFAULT_RANGE_REPORT))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--journal-path", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    args = parser.parse_args()
    report = run(args)
    latest = report["latest"]
    print(
        json.dumps(
            {
                "status": "ok",
                "observer_status": latest["status"],
                "guard_id": latest["guard_id"],
                "guard_action": latest["guard_action"],
                "raw_signals": latest["raw_signals"],
                "refined_signals": latest["refined_signals"],
                "json": rel_path(resolve_path(args.out_prefix).with_suffix(".json")),
                "md": rel_path(resolve_path(args.out_prefix).with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
