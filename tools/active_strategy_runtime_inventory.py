#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def cycle_steps(latest_cycle: dict[str, Any], prefixes: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, value in latest_cycle.items():
        if not isinstance(value, dict) or "exit_code" not in value:
            continue
        if prefixes and not any(name == prefix or name.startswith(prefix) for prefix in prefixes):
            continue
        rows.append({"name": name, "exit_code": value.get("exit_code")})
    return rows


def family_status(steps: list[dict[str, Any]], report_exists: bool) -> str:
    if not report_exists or not steps:
        return "missing_or_not_run"
    return "observer_running" if all(item.get("exit_code") == 0 for item in steps) else "degraded"


def build_report() -> dict[str, Any]:
    scheduler = read_json(ROOT / "docs" / "STRATEGY_MIX_FORWARD_SCHEDULER_2026-06-08.json")
    latest_cycle = scheduler.get("latest_cycle") if isinstance(scheduler.get("latest_cycle"), dict) else {}
    health = read_json(ROOT / "docs" / "FORWARD_RUNTIME_HEALTH_2026-06-16.json")

    feed = read_json(ROOT / "docs" / "STRATEGY_MIX_FORWARD_PAPER_FEED_2026-06-08.json")
    range_observer = read_json(ROOT / "docs" / "RANGE_REFINED_FORWARD_OBSERVER_2026-06-16.json")
    range_score = read_json(ROOT / "docs" / "RANGE_REFINED_OBSERVER_SCOREBOARD_2026-06-16.json")
    range_gate = read_json(ROOT / "docs" / "RANGE_REFINED_PROMOTION_GATE_2026-06-17.json")
    edge_export = read_json(ROOT / "docs" / "EDGE_FORWARD_CANDIDATE_REFINER_2026-06-18.json")
    edge_observer = read_json(ROOT / "docs" / "EDGE_FORWARD_RANGE_OBSERVER_2026-06-18.json")
    edge_score = read_json(ROOT / "docs" / "EDGE_FORWARD_RANGE_SCOREBOARD_2026-06-18.json")
    edge_gate = read_json(ROOT / "docs" / "EDGE_FORWARD_PROMOTION_GATE_2026-06-18.json")
    crowd_observer = read_json(ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19.json")
    crowd_score = read_json(ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_SCOREBOARD_2026-06-19.json")
    crowd_gate = read_json(ROOT / "docs" / "CROWD_FADE_POSITIONING_PROMOTION_GATE_2026-06-19.json")
    crowd_refresh = read_json(ROOT / "docs" / "CROWD_FADE_REFRESH_PACK_2026-06-19.json")
    trend_lock = read_json(ROOT / "configs" / "TREND_MIX_FORWARD_LOCK.json")
    crowd_lock = read_json(ROOT / "configs" / "CROWD_FADE_FORWARD_LOCK.json")

    main_steps = cycle_steps(latest_cycle, ("feed", "regime_observer", "oi_funding_", "scoreboard", "forward_outcome_", "telegram_notify"))
    range_steps = cycle_steps(latest_cycle, ("range_refined_",))
    edge_steps = cycle_steps(latest_cycle, ("edge_",))
    all_steps = cycle_steps(latest_cycle, ())
    crowd_steps = crowd_refresh.get("steps") if isinstance(crowd_refresh.get("steps"), list) else []
    crowd_steps = [item for item in crowd_steps if isinstance(item, dict) and "exit_code" in item]
    crowd_observer_status = nested(crowd_observer, "latest", "status")
    crowd_rejected = (
        crowd_lock.get("enabled") is False
        and str(crowd_lock.get("status") or "").startswith("historically_rejected")
        and nested(crowd_lock, "boundaries", "can_trade") is False
    )
    crowd_runtime_status = (
        "observer_paused_historical_rejection"
        if crowd_rejected or crowd_observer_status == "candidate_paused_by_lock"
        else family_status(crowd_steps, bool(crowd_observer))
    )
    range_observer_status = nested(range_observer, "latest_result", "status") or range_observer.get("status") or range_observer.get("decision")
    range_runtime_status = (
        "observer_paused_historical_rejection"
        if range_observer_status == "candidate_paused_historical_rejection"
        else family_status(range_steps, bool(range_observer))
    )
    trend_rejected = (
        trend_lock.get("family") == "TREND_MIX_4H"
        and trend_lock.get("enabled") is False
        and str(trend_lock.get("status") or "").startswith("historically_rejected")
    )

    strategies = [
        {
            "family": "TREND_MIX_4H",
            "role": "primary trend/breakout hypothesis",
            "strategy_id": nested(feed, "latest", "strategy_id") or feed.get("strategy_id"),
            "observer_status": nested(feed, "latest", "status") or feed.get("status"),
            "scoreboard": read_json(ROOT / "docs" / "STRATEGY_MIX_FORWARD_SCOREBOARD_2026-06-08.json").get("classification"),
            "promotion": read_json(ROOT / "docs" / "OI_GUARD_PROMOTION_GATE_2026-06-15.json").get("decision"),
            "runtime_status": "observer_paused_historical_rejection" if trend_rejected else family_status(main_steps, bool(feed)),
            "runtime_steps": main_steps,
        },
        {
            "family": "RANGE_REFINED_4H",
            "role": "range mean-reversion/reclaim hypothesis",
            "strategy_id": range_observer.get("strategy_id"),
            "observer_status": range_observer_status,
            "scoreboard": nested(range_score, "summary", "classification") or range_score.get("classification"),
            "promotion": range_gate.get("decision"),
            "runtime_status": range_runtime_status,
            "runtime_steps": range_steps,
        },
        {
            "family": "EDGE_FORWARD_4H",
            "role": "strict exported range edge under independent proof",
            "strategy_id": nested(edge_export, "selected_candidate", "strategy_id"),
            "observer_status": edge_observer.get("status") or edge_observer.get("decision"),
            "scoreboard": nested(edge_score, "summary", "classification") or edge_score.get("classification"),
            "promotion": edge_gate.get("decision"),
            "runtime_status": family_status(edge_steps, bool(edge_observer)),
            "runtime_steps": edge_steps,
        },
        {
            "family": "CROWD_FADE_1H",
            "role": "contrarian public long/short positioning hypothesis",
            "strategy_id": crowd_observer.get("strategy_id"),
            "observer_status": crowd_observer_status,
            "scoreboard": nested(crowd_score, "summary", "classification"),
            "promotion": crowd_gate.get("decision"),
            "runtime_status": crowd_runtime_status,
            "runtime_steps": [{"name": item.get("name"), "exit_code": item.get("exit_code")} for item in crowd_steps],
        },
    ]

    degraded = [
        item["family"]
        for item in strategies
        if item["runtime_status"] not in {"observer_running", "observer_paused_historical_rejection"}
    ]
    rejected = [item["family"] for item in strategies if item["runtime_status"] == "observer_paused_historical_rejection"]
    nonzero = [item for item in all_steps if item.get("exit_code") != 0]
    return {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "active_strategy_inventory_observer_only",
            "can_trade": False,
            "paper_execution_allowed": False,
            "live_execution_allowed": False,
        },
        "strategy_family_count": len(strategies),
        "active_observer_count": sum(item["runtime_status"] == "observer_running" for item in strategies),
        "rejected_family_count": len(rejected),
        "strategies": strategies,
        "overlays_not_independent_strategies": [
            "canonical_regime",
            "oi_funding_context_and_guard",
            "spot_perp_confirmation",
            "range_filter_shadow_ablation",
            "edge_same_shape_shadow",
            "edge_compression_guard_shadow",
        ],
        "watchdog_coverage": {
            "scheduler_executable_steps": len(all_steps),
            "scheduler_nonzero_steps": nonzero,
            "crowd_runtime_gates_present": any(str(item.get("name", "")).startswith("crowd_") for item in health.get("gates", []) if isinstance(item, dict)),
            "health_classification": health.get("classification"),
        },
        "decision": (
            "one_observer_running_three_historically_rejected"
            if not degraded and not nonzero and len(rejected) == 3
            else
            "two_observers_running_two_historically_rejected"
            if not degraded and not nonzero and len(rejected) == 2
            else
            "three_observers_running_one_historically_rejected"
            if not degraded and not nonzero and len(rejected) == 1
            else "four_observer_families_running"
            if not degraded and not nonzero
            else "strategy_runtime_attention_required"
        ),
        "degraded_families": degraded,
        "rejected_families": rejected,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Active Strategy Runtime Map",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Independent observer families: `{report.get('strategy_family_count')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Strategies",
        "",
        "| Family | Runtime | Observer | Scoreboard | Promotion |",
        "|---|---|---|---|---|",
    ]
    for item in report.get("strategies", []):
        lines.append(
            f"| `{item.get('family')}` | `{item.get('runtime_status')}` | `{item.get('observer_status')}` | "
            f"`{item.get('scoreboard')}` | `{item.get('promotion')}` |"
        )
    coverage = report.get("watchdog_coverage", {})
    lines.extend(
        [
            "",
            "## Watchdog",
            "",
            f"- Scheduler executable steps checked: `{coverage.get('scheduler_executable_steps')}`.",
            f"- Non-zero steps: `{coverage.get('scheduler_nonzero_steps')}`.",
            f"- Crowd runtime gates present: `{coverage.get('crowd_runtime_gates_present')}`.",
            f"- Health: `{coverage.get('health_classification')}`.",
            "",
            "## Overlays",
            "",
            "These are filters or shadow comparisons, not additional independent strategies:",
            "",
            *[f"- `{name}`" for name in report.get("overlays_not_independent_strategies", [])],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a factual inventory of active strategy observer families.")
    parser.add_argument("--out-prefix", default="docs/ACTIVE_STRATEGY_RUNTIME_MAP_2026-06-22")
    args = parser.parse_args()
    report = build_report()
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "strategies": report["strategy_family_count"], "health": report["watchdog_coverage"]["health_classification"], "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if report["decision"] in {
        "four_observer_families_running",
        "three_observers_running_one_historically_rejected",
        "two_observers_running_two_historically_rejected",
        "one_observer_running_three_historically_rejected",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
