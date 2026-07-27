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

from tools.strategy_mix_combo_tester import generate_signals, load_interval_data  # noqa: E402
from tools.strategy_mix_deep_validator import signal_config  # noqa: E402
from tools.strategy_mix_holdout_validator import ReplayConfig, result_to_config  # noqa: E402
from tools.strategy_mix_paper_replay import select_candidates  # noqa: E402


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


def parse_verdicts(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def candidate_from_source(path: Path, verdicts: set[str], top: int) -> ReplayConfig:
    source = read_json(path)
    candidates = select_candidates(source, verdicts, top)
    if not candidates:
        raise ValueError("no_candidate_found_for_entry_scarcity_diagnostic")
    return result_to_config(candidates[0])


def bool_at(matrix: dict[str, list[bool]], condition: str, index: int) -> bool:
    values = matrix.get(condition) or []
    if index < 0 or index >= len(values):
        return False
    return bool(values[index])


def pass_count(matrix: dict[str, list[bool]], conditions: tuple[str, ...], indexes: list[int]) -> int:
    return sum(1 for index in indexes if all(bool_at(matrix, condition, index) for condition in conditions))


def latest_true_ts(bars: list[Any], matrix: dict[str, list[bool]], condition: str, indexes: list[int]) -> str | None:
    for index in reversed(indexes):
        if bool_at(matrix, condition, index):
            return str(bars[index].ts)
    return None


def current_streak(matrix: dict[str, list[bool]], condition: str, indexes: list[int], target: bool) -> int:
    streak = 0
    for index in reversed(indexes):
        if bool_at(matrix, condition, index) == target:
            streak += 1
        else:
            break
    return streak


def condition_stats(
    *,
    bars: list[Any],
    matrix: dict[str, list[bool]],
    conditions: tuple[str, ...],
    indexes: list[int],
) -> list[dict[str, Any]]:
    total = len(indexes)
    rows: list[dict[str, Any]] = []
    for condition in conditions:
        true_count = sum(1 for index in indexes if bool_at(matrix, condition, index))
        false_count = total - true_count
        marginal_missing = 0
        for index in indexes:
            if bool_at(matrix, condition, index):
                continue
            others = [item for item in conditions if item != condition]
            if all(bool_at(matrix, other, index) for other in others):
                marginal_missing += 1
        latest_value = bool_at(matrix, condition, indexes[-1]) if indexes else False
        rows.append(
            {
                "condition": condition,
                "true_count": true_count,
                "false_count": false_count,
                "true_pct": round((true_count / total) * 100.0, 3) if total else 0.0,
                "marginal_missing_count": marginal_missing,
                "latest_value": latest_value,
                "latest_true_bar_ts": latest_true_ts(bars, matrix, condition, indexes),
                "current_true_streak": current_streak(matrix, condition, indexes, True),
                "current_false_streak": current_streak(matrix, condition, indexes, False),
            }
        )
    rows.sort(key=lambda item: (item["marginal_missing_count"], item["false_count"]), reverse=True)
    return rows


def funnel_stats(matrix: dict[str, list[bool]], conditions: tuple[str, ...], indexes: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = len(indexes)
    prefix: list[str] = []
    for condition in conditions:
        prefix.append(condition)
        count = pass_count(matrix, tuple(prefix), indexes)
        rows.append(
            {
                "required_conditions": list(prefix),
                "added_condition": condition,
                "pass_count": count,
                "pass_pct": round((count / total) * 100.0, 3) if total else 0.0,
            }
        )
    return rows


def variant_stats(
    *,
    matrix: dict[str, list[bool]],
    conditions: tuple[str, ...],
    indexes: list[int],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, ...]] = set()
    variants: list[tuple[str, tuple[str, ...]]] = [("locked", conditions)]
    if len(conditions) > 3:
        variants.append(("base_core_first_3", tuple(conditions[:3])))
    for condition in conditions:
        reduced = tuple(item for item in conditions if item != condition)
        if reduced:
            variants.append((f"drop_{condition}", reduced))

    rows: list[dict[str, Any]] = []
    total = len(indexes)
    for label, variant_conditions in variants:
        if variant_conditions in seen:
            continue
        seen.add(variant_conditions)
        count = pass_count(matrix, variant_conditions, indexes)
        latest_pass = all(bool_at(matrix, condition, indexes[-1]) for condition in variant_conditions) if indexes else False
        latest_blockers = [condition for condition in variant_conditions if indexes and not bool_at(matrix, condition, indexes[-1])]
        rows.append(
            {
                "variant": label,
                "conditions": list(variant_conditions),
                "signal_like_bars": count,
                "signal_like_pct": round((count / total) * 100.0, 3) if total else 0.0,
                "latest_pass": latest_pass,
                "latest_blockers": latest_blockers,
            }
        )
    rows.sort(key=lambda item: (item["variant"] != "locked", item["signal_like_bars"]), reverse=True)
    rows.sort(key=lambda item: item["variant"] == "locked", reverse=True)
    return rows


def recent_blockers(
    *,
    bars: list[Any],
    matrix: dict[str, list[bool]],
    features: list[dict[str, Any]],
    conditions: tuple[str, ...],
    indexes: list[int],
    limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in indexes[-limit:]:
        passed = [condition for condition in conditions if bool_at(matrix, condition, index)]
        blocked = [condition for condition in conditions if not bool_at(matrix, condition, index)]
        feature = features[index] if 0 <= index < len(features) else {}
        rows.append(
            {
                "bar_index": index,
                "bar_ts": str(bars[index].ts),
                "close": round(float(bars[index].close), 8),
                "passed_conditions": passed,
                "blocking_conditions": blocked,
                "atr": feature.get("atr"),
                "atr_ratio": feature.get("atr_ratio"),
                "body_pct": feature.get("body_pct"),
                "volume_z": feature.get("volume_z"),
                "funding": feature.get("funding"),
                "oi_delta_pct": feature.get("oi_delta_pct"),
                "spot_perp_divergence_pct": feature.get("spot_perp_divergence_pct"),
            }
        )
    return rows


def classify_report(
    *,
    locked_count: int,
    variant_rows: list[dict[str, Any]],
    condition_rows: list[dict[str, Any]],
) -> tuple[str, str]:
    if locked_count > 0:
        return "locked_strategy_has_recent_signals", "keep_observing_forward_outcomes"
    non_locked = [item for item in variant_rows if item.get("variant") != "locked"]
    if any(int(item.get("signal_like_bars") or 0) > 0 for item in non_locked):
        primary = condition_rows[0]["condition"] if condition_rows else "unknown_condition"
        return "entry_scarcity_with_relaxation_candidates", f"diagnose_primary_bottleneck:{primary}"
    return "entry_scarcity_no_shadow_signals", "wait_or_expand_strategy_family_research_only"


def render_markdown(report: dict[str, Any]) -> str:
    candidate = report.get("candidate") if isinstance(report.get("candidate"), dict) else {}
    data = report.get("data") if isinstance(report.get("data"), dict) else {}
    rows = report.get("condition_stats") or []
    funnel = report.get("funnel") or []
    variants = report.get("shadow_variants") or []
    locked_signal_bars = report.get("locked_signal_bars") or []
    recent = report.get("recent_blockers") or []
    lines = [
        "# Forward Entry Scarcity Diagnostic",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Report-only diagnostic over cached closed-bar forward data.",
        "- Does not change strategy parameters.",
        "- Does not allow live trading or paper promotion.",
        "",
        "## Decision",
        "",
        f"- Classification: `{report.get('classification')}`.",
        f"- Next action: `{report.get('next_action')}`.",
        f"- Can trade: `{report.get('can_trade')}`.",
        "",
        "## Candidate",
        "",
        f"- Strategy: `{candidate.get('strategy_id')}`.",
        f"- Symbol / TF: `{report.get('symbol')}` / `{candidate.get('interval')}`.",
        f"- Side: `{candidate.get('side')}`.",
        f"- Conditions: `{', '.join(candidate.get('conditions') or [])}`.",
        f"- RR / hold: `{candidate.get('rr')}` / `{candidate.get('max_hold_bars')}` bars.",
        "",
        "## Data Window",
        "",
        f"- Cache: `{data.get('cache_dir')}`.",
        f"- Bars loaded: `{data.get('bars_loaded')}`.",
        f"- Bars analyzed: `{data.get('bars_analyzed')}`.",
        f"- First analyzed bar: `{data.get('first_analyzed_bar_ts')}`.",
        f"- Latest analyzed bar: `{data.get('latest_analyzed_bar_ts')}` close `{data.get('latest_close')}`.",
        f"- Locked signal-like bars in window: `{data.get('locked_signal_like_bars')}`.",
        "",
        "## Condition Bottlenecks",
        "",
        "| Condition | True | False | True % | Marginal Missing | Latest | Latest True | False Streak |",
        "|---|---:|---:|---:|---:|---|---|---:|",
    ]
    for item in rows:
        lines.append(
            f"| `{item['condition']}` | `{item['true_count']}` | `{item['false_count']}` | "
            f"`{item['true_pct']}` | `{item['marginal_missing_count']}` | `{item['latest_value']}` | "
            f"`{item['latest_true_bar_ts']}` | `{item['current_false_streak']}` |"
        )
    lines.extend(["", "## Ordered Funnel", "", "| Added Condition | Pass Count | Pass % | Prefix |", "|---|---:|---:|---|"])
    for item in funnel:
        lines.append(
            f"| `{item['added_condition']}` | `{item['pass_count']}` | `{item['pass_pct']}` | "
            f"`{', '.join(item['required_conditions'])}` |"
        )
    lines.extend(["", "## Shadow Variants For Research Only", "", "| Variant | Bars | % | Latest Pass | Latest Blockers |", "|---|---:|---:|---|---|"])
    for item in variants:
        lines.append(
            f"| `{item['variant']}` | `{item['signal_like_bars']}` | `{item['signal_like_pct']}` | "
            f"`{item['latest_pass']}` | `{', '.join(item['latest_blockers'])}` |"
        )
    lines.extend(["", "## Locked Signal-Like Bars In Window", "", "| Bar TS | Bar Index | ATR | ATR Ratio |", "|---|---:|---:|---:|"])
    for item in locked_signal_bars:
        lines.append(
            f"| `{item['bar_ts']}` | `{item['bar_index']}` | `{item.get('atr')}` | `{item.get('atr_ratio')}` |"
        )
    lines.extend(["", "## Recent Blockers", "", "| Bar TS | Close | Passed | Blocked | ATR Ratio | Body % |", "|---|---:|---|---|---:|---:|"])
    for item in recent:
        lines.append(
            f"| `{item['bar_ts']}` | `{item['close']}` | `{', '.join(item['passed_conditions'])}` | "
            f"`{', '.join(item['blocking_conditions'])}` | `{item.get('atr_ratio')}` | `{item.get('body_pct')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `Marginal Missing` means bars where all other locked conditions passed and only this condition blocked the setup.",
            "- Shadow variants are diagnostics only. They are not promoted until historical, holdout, cost-stress and forward evidence gates pass.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    source_report = resolve_path(args.source_report)
    cache_dir = resolve_path(args.cache_dir)
    verdicts = parse_verdicts(args.candidate_verdicts)
    config = candidate_from_source(source_report, verdicts, args.top)
    bars, features, matrix = load_interval_data(cache_dir, config.interval, args.oi_lag, args.spot_perp_lookback)
    if not bars:
        raise ValueError("no_bars_loaded")
    start = max(0, len(bars) - args.analyze_bars)
    indexes = list(range(start, len(bars)))
    locked_signals = [
        item
        for item in generate_signals(signal_config(config), bars, features, matrix)
        if int(item.get("bar_index") or -1) in set(indexes)
    ]
    conditions = tuple(config.conditions)
    stats = condition_stats(bars=bars, matrix=matrix, conditions=conditions, indexes=indexes)
    variants = variant_stats(matrix=matrix, conditions=conditions, indexes=indexes)
    classification, next_action = classify_report(
        locked_count=len(locked_signals),
        variant_rows=variants,
        condition_rows=stats,
    )
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "mode": "report_only",
            "public_data_only": True,
            "sends_orders": False,
            "can_trade": False,
        },
        "inputs": {
            "source_report": rel_path(source_report),
            "cache_dir": rel_path(cache_dir),
            "candidate_verdicts": sorted(verdicts),
            "top": args.top,
            "analyze_bars": args.analyze_bars,
            "oi_lag": args.oi_lag,
            "spot_perp_lookback": args.spot_perp_lookback,
        },
        "symbol": args.symbol.upper(),
        "candidate": {
            "strategy_id": config.strategy_id,
            "interval": config.interval,
            "side": config.side,
            "conditions": list(config.conditions),
            "rr": f"{config.stop_atr:g}:{config.take_atr:g}",
            "max_hold_bars": config.max_hold_bars,
        },
        "data": {
            "cache_dir": rel_path(cache_dir),
            "bars_loaded": len(bars),
            "bars_analyzed": len(indexes),
            "first_analyzed_bar_ts": str(bars[indexes[0]].ts) if indexes else None,
            "latest_analyzed_bar_ts": str(bars[indexes[-1]].ts) if indexes else None,
            "latest_close": round(float(bars[indexes[-1]].close), 8) if indexes else None,
            "locked_signal_like_bars": len(locked_signals),
        },
        "condition_stats": stats,
        "funnel": funnel_stats(matrix, conditions, indexes),
        "shadow_variants": variants,
        "locked_signal_bars": [
            {
                "bar_index": int(item.get("bar_index") or -1),
                "bar_ts": str(bars[int(item.get("bar_index") or -1)].ts) if 0 <= int(item.get("bar_index") or -1) < len(bars) else None,
                "atr": item.get("atr"),
                "atr_ratio": item.get("feature_snapshot", {}).get("atr_ratio") if isinstance(item.get("feature_snapshot"), dict) else None,
            }
            for item in locked_signals
        ],
        "recent_blockers": recent_blockers(
            bars=bars,
            matrix=matrix,
            features=features,
            conditions=conditions,
            indexes=indexes,
            limit=args.recent_limit,
        ),
        "classification": classification,
        "next_action": next_action,
        "can_trade": False,
        "decision": "diagnostic_only_no_orders_no_parameter_change",
    }
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Diagnose why the locked forward strategy is not emitting entries")
    parser.add_argument("--source-report", default="docs/STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-06-29_4H_GUARDED_SHORT.json")
    parser.add_argument("--cache-dir", default="_dl/forward_paper_feed/cache")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--candidate-verdicts", default="paper_replay_candidate_locked")
    parser.add_argument("--top", type=int, default=1)
    parser.add_argument("--analyze-bars", type=int, default=320)
    parser.add_argument("--recent-limit", type=int, default=20)
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--out-prefix", default="docs/FORWARD_ENTRY_SCARCITY_DIAGNOSTIC_2026-06-16")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": "ok", "classification": report["classification"], "next_action": report["next_action"], "out": rel_path(out_prefix.with_suffix(".json"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
