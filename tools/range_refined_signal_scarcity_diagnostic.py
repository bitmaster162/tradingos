#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.range_family_validator import (  # noqa: E402
    load_interval_payload,
    previous_high_low,
    rsi_ok,
    trend_atr,
)
from tools.range_refined_forward_observer import build_config, selected_candidate  # noqa: E402
from tools.range_watchlist_refiner import FILTER_FUNCS  # noqa: E402


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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def trigger_checks(config: Any, bar: Any, high: float | None, low: float | None) -> dict[str, bool | None]:
    if high is None or low is None or high <= low:
        return {
            "near_low": None,
            "near_high": None,
            "sweep_down_reclaim": None,
            "sweep_up_reclaim": None,
            "selected_trigger": False,
            "side_trigger_valid": False,
        }
    width = high - low
    lower_edge = low + width * config.edge_pct
    upper_edge = high - width * config.edge_pct
    checks = {
        "near_low": bool(bar.close <= lower_edge),
        "near_high": bool(bar.close >= upper_edge),
        "sweep_down_reclaim": bool(bar.low < low and bar.close > low),
        "sweep_up_reclaim": bool(bar.high > high and bar.close < high),
    }
    selected = bool(checks.get(config.trigger, False))
    side_valid = not (
        (config.side == "LONG" and config.trigger in {"near_high", "sweep_up_reclaim"})
        or (config.side == "SHORT" and config.trigger in {"near_low", "sweep_down_reclaim"})
    )
    checks["selected_trigger"] = selected
    checks["side_trigger_valid"] = side_valid
    return checks


def build_signal_like_payload(config: Any, bars: list[Any], features: list[dict[str, Any]], rsi14: list[float | None], index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    bar = bars[index]
    high, low = previous_high_low(bars, index, config.lookback)
    feature = features[index]
    atr = finite_float(feature.get("atr"))
    width_atr: float | None = None
    if high is not None and low is not None and atr is not None and atr > 0:
        width_atr = (high - low) / atr
    trend = trend_atr(bars, features, index, config.lookback)
    atr_ratio = finite_float(feature.get("atr_ratio"))
    rsi_value = rsi14[index] if index < len(rsi14) else None
    triggers = trigger_checks(config, bar, high, low)
    checks = {
        "lookback_ready": high is not None and low is not None and high > low,
        "atr_available": atr is not None and atr > 0,
        "width_atr_in_bounds": width_atr is not None and config.min_width_atr <= width_atr <= config.max_width_atr,
        "trend_atr_in_bounds": trend is not None and abs(trend) <= config.max_abs_trend_atr,
        "atr_ratio_ok": atr_ratio is not None and atr_ratio <= config.max_atr_ratio,
        "rsi_ok": rsi_ok(config, rsi_value),
        "trigger_ok": bool(triggers.get("selected_trigger")),
        "side_trigger_valid": bool(triggers.get("side_trigger_valid")),
    }
    snapshot = {
        "bar_index": index,
        "bar_ts": str(bar.ts),
        "close": round(float(bar.close), 8),
        "range_high": None if high is None else round(float(high), 8),
        "range_low": None if low is None else round(float(low), 8),
        "width_atr": None if width_atr is None else round(width_atr, 6),
        "trend_atr": None if trend is None else round(float(trend), 6),
        "atr_ratio": None if atr_ratio is None else round(atr_ratio, 6),
        "rsi14": None if rsi_value is None else round(float(rsi_value), 6),
        "volume_z": feature.get("volume_z"),
        "funding": feature.get("funding"),
        "oi_delta_pct": feature.get("oi_delta_pct"),
        "spot_perp_divergence_pct": feature.get("spot_perp_divergence_pct"),
        "triggers": triggers,
    }
    return checks, snapshot


def base_passed(checks: dict[str, Any]) -> bool:
    return all(bool(value) for value in checks.values())


def filter_checks(config: Any, snapshot: dict[str, Any], filter_names: list[str]) -> dict[str, bool | None]:
    signal = {
        "bar_index": snapshot.get("bar_index"),
        "atr": None,
        "reason": config.trigger,
        "feature_snapshot": snapshot,
    }
    out: dict[str, bool | None] = {}
    for name in filter_names:
        func = FILTER_FUNCS.get(name)
        out[name] = None if func is None else bool(func(config, signal))
    return out


def stats_from_counter(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    rows = []
    for name, count in counter.most_common():
        rows.append({"condition": name, "blocked_bars": count, "blocked_pct": round((count / total) * 100.0, 3) if total else 0.0})
    return rows


def render_markdown(report: dict[str, Any]) -> str:
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    latest = report.get("latest_bar") if isinstance(report.get("latest_bar"), dict) else {}
    lines = [
        "# Range Refined Signal Scarcity Diagnostic",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Diagnostic only.",
        "- Does not relax strategy parameters.",
        "- Does not create paper-entry intents.",
        "- Does not send orders and does not grant live permission.",
        "",
        "## Selected Candidate",
        "",
        f"- Strategy: `{selected.get('strategy_id')}`.",
        f"- Base: `{selected.get('base_strategy_id')}`.",
        f"- Filter: `{selected.get('filter_mode')}` (`{'+'.join(selected.get('filters') or [])}`).",
        f"- TF / side / trigger / RR: `{selected.get('interval')}` / `{selected.get('side')}` / `{selected.get('trigger')}` / `{selected.get('rr')}`.",
        "",
        "## Summary",
        "",
        f"- Classification: `{report.get('classification')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        f"- Bars analyzed: `{summary.get('bars_analyzed')}`.",
        f"- Base setup bars: `{summary.get('base_setup_bars')}`.",
        f"- Refined setup bars: `{summary.get('refined_setup_bars')}`.",
        f"- Latest bar status: `{latest.get('status')}`.",
        f"- Latest bar: `{latest.get('bar_ts')}` close `{latest.get('close')}`.",
        f"- Latest base blockers: `{latest.get('base_blockers')}`.",
        f"- Latest filter blockers: `{latest.get('filter_blockers')}`.",
        "",
        "## Base Setup Blockers",
        "",
        "| Condition | Blocked Bars | Blocked % |",
        "|---|---:|---:|",
    ]
    for item in report.get("base_blockers", []):
        lines.append(f"| `{item.get('condition')}` | `{item.get('blocked_bars')}` | `{item.get('blocked_pct')}` |")
    lines.extend(["", "## Filter Blockers On Base Setup Bars", "", "| Filter | Blocked Bars | Blocked % of Base Setups |", "|---|---:|---:|"])
    for item in report.get("filter_blockers", []):
        lines.append(f"| `{item.get('condition')}` | `{item.get('blocked_bars')}` | `{item.get('blocked_pct')}` |")
    lines.extend(["", "## Recent Bars", "", "| Bar TS | Close | Base | Refined | Base Blockers | Filter Blockers |", "|---|---:|---|---|---|---|"])
    for item in report.get("recent_bars", [])[-12:]:
        lines.append(
            f"| `{item.get('bar_ts')}` | `{item.get('close')}` | `{item.get('base_passed')}` | `{item.get('refined_passed')}` | "
            f"`{', '.join(item.get('base_blockers') or [])}` | `{', '.join(item.get('filter_blockers') or [])}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `Base setup bars` means the original RANGE condition triggered before OI/funding/spot filters.",
            "- `Refined setup bars` means the selected filter stack also passed.",
            "- If base setups are present but refined setups are absent, the issue is derivatives/spot filter strictness, not range detection.",
            "- This report does not promote any relaxation. Any relaxation must go back through historical, holdout, cost-stress and forward gates.",
            "",
        ]
    )
    return "\n".join(lines)


def classify(base_count: int, refined_count: int, base_blockers: list[dict[str, Any]], filter_blockers: list[dict[str, Any]]) -> tuple[str, str]:
    if refined_count > 0:
        return "range_refined_recent_signals_exist", "keep_observer_running_and_wait_for_outcome_resolution"
    if base_count > 0:
        primary = filter_blockers[0]["condition"] if filter_blockers else "unknown_filter"
        return "range_base_signal_present_filters_blocking", f"diagnose_filter_bottleneck:{primary}"
    primary = base_blockers[0]["condition"] if base_blockers else "unknown_base_condition"
    return "range_base_signal_scarce", f"diagnose_base_bottleneck:{primary}"


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    refiner_path = resolve_path(args.refiner_report)
    source_path = resolve_path(args.source_range_report)
    cache_dir = resolve_path(args.cache_dir)
    refiner_report = read_json(refiner_path)
    source_report = read_json(source_path)
    selected = selected_candidate(refiner_report)
    config = build_config(selected, source_report)
    filter_names = list(selected.get("filters") or [])
    bars, features, rsi14 = load_interval_payload(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError(f"no_bars_loaded:{rel_path(cache_dir)}:{config.interval}")

    start = max(0, len(bars) - args.analyze_bars)
    indexes = list(range(start, len(bars)))
    base_block_counter: Counter[str] = Counter()
    filter_block_counter: Counter[str] = Counter()
    recent_rows: list[dict[str, Any]] = []
    base_count = 0
    refined_count = 0
    refined_signal_bars: list[dict[str, Any]] = []

    for index in indexes:
        checks, snapshot = build_signal_like_payload(config, bars, features, rsi14, index)
        base_blockers = [name for name, passed in checks.items() if not passed]
        base_ok = base_passed(checks)
        f_checks = filter_checks(config, snapshot, filter_names) if base_ok else {name: None for name in filter_names}
        filter_blockers = [name for name, passed in f_checks.items() if passed is False]
        refined_ok = base_ok and not filter_blockers and all(value is True for value in f_checks.values())
        if base_ok:
            base_count += 1
            filter_block_counter.update(filter_blockers)
        else:
            base_block_counter.update(base_blockers)
        if refined_ok:
            refined_count += 1
            refined_signal_bars.append({"bar_index": index, "bar_ts": snapshot["bar_ts"], "close": snapshot["close"]})
        recent_rows.append(
            {
                "bar_index": index,
                "bar_ts": snapshot["bar_ts"],
                "close": snapshot["close"],
                "base_passed": base_ok,
                "refined_passed": refined_ok,
                "base_blockers": base_blockers,
                "filter_checks": f_checks,
                "filter_blockers": filter_blockers,
                "snapshot": snapshot,
            }
        )

    base_blockers = stats_from_counter(base_block_counter, len(indexes))
    filter_blockers = stats_from_counter(filter_block_counter, max(1, base_count))
    classification, next_action = classify(base_count, refined_count, base_blockers, filter_blockers)
    latest = recent_rows[-1] if recent_rows else {}
    latest_status = "refined_signal" if latest.get("refined_passed") else "base_signal_filtered" if latest.get("base_passed") else "no_base_signal"

    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "range_refined_signal_scarcity_diagnostic_public_cache_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "inputs": {
            "refiner_report": rel_path(refiner_path),
            "source_range_report": rel_path(source_path),
            "cache_dir": rel_path(cache_dir),
            "analyze_bars": args.analyze_bars,
            "oi_lag": args.oi_lag,
            "spot_perp_lookback": args.spot_perp_lookback,
        },
        "selected_candidate": selected,
        "summary": {
            "bars_loaded": len(bars),
            "bars_analyzed": len(indexes),
            "base_setup_bars": base_count,
            "base_setup_pct": round((base_count / len(indexes)) * 100.0, 3) if indexes else 0.0,
            "refined_setup_bars": refined_count,
            "refined_setup_pct": round((refined_count / len(indexes)) * 100.0, 3) if indexes else 0.0,
        },
        "latest_bar": {
            "status": latest_status,
            "bar_index": latest.get("bar_index"),
            "bar_ts": latest.get("bar_ts"),
            "close": latest.get("close"),
            "base_passed": latest.get("base_passed"),
            "refined_passed": latest.get("refined_passed"),
            "base_blockers": latest.get("base_blockers"),
            "filter_blockers": latest.get("filter_blockers"),
            "filter_checks": latest.get("filter_checks"),
            "snapshot": latest.get("snapshot"),
        },
        "base_blockers": base_blockers,
        "filter_blockers": filter_blockers,
        "refined_signal_bars": refined_signal_bars[-20:],
        "recent_bars": recent_rows[-args.recent_bars :],
        "classification": classification,
        "next_action": next_action,
        "decision": "range_signal_scarcity_diagnostic_no_trade_permission",
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Diagnose why the selected refined RANGE observer emits few/no signals")
    parser.add_argument("--refiner-report", default="docs/RANGE_WATCHLIST_REFINER_2026-06-16.json")
    parser.add_argument("--source-range-report", default="docs/RANGE_FAMILY_VALIDATOR_2026-06-16.json")
    parser.add_argument("--cache-dir", default="_dl/forward_paper_feed/cache")
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--analyze-bars", type=int, default=320)
    parser.add_argument("--recent-bars", type=int, default=40)
    parser.add_argument("--out-prefix", default="docs/RANGE_REFINED_SIGNAL_SCARCITY_DIAGNOSTIC_2026-06-17")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "classification": report.get("classification"),
                "base_setup_bars": report.get("summary", {}).get("base_setup_bars"),
                "refined_setup_bars": report.get("summary", {}).get("refined_setup_bars"),
                "latest_status": report.get("latest_bar", {}).get("status"),
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
