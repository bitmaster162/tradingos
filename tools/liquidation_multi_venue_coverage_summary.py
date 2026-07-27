#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc), "_path": portable(path)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_object", "_path": portable(path)}


def source_summary(name: str, path: Path) -> dict[str, Any]:
    report = read_json(path)
    hard_failures = [
        item.get("name")
        for item in report.get("hard_failures", [])
        if isinstance(item, dict) and item.get("name")
    ]
    soft_failures = [
        item.get("name")
        for item in report.get("soft_failures", [])
        if isinstance(item, dict) and item.get("name")
    ]
    events_block = report.get("events") if isinstance(report.get("events"), dict) else {}
    research_block = (
        events_block.get("preregistered_sample")
        if isinstance(events_block.get("preregistered_sample"), dict)
        else events_block.get("research_universe")
        if isinstance(events_block.get("research_universe"), dict)
        else events_block
    )
    events = research_block.get("events")
    collector = report.get("collector") if isinstance(report.get("collector"), dict) else {}
    research_ready = not hard_failures and not soft_failures and isinstance(events, int) and events > 0
    has_events = not hard_failures and isinstance(events, int) and events > 0
    alive = not hard_failures and bool(collector)
    return {
        "name": name,
        "path": portable(path),
        "exists": bool(report) and not report.get("_read_error"),
        "decision": report.get("decision"),
        "can_trade": report.get("can_trade", False),
        "events": events,
        "all_market_events": events_block.get("events"),
        "research_universe_events": (events_block.get("research_universe") or {}).get("events"),
        "alive": alive,
        "ready_with_events": research_ready,
        "has_events": has_events,
        "hard_failures": hard_failures,
        "soft_failures": soft_failures,
        "collector": collector,
    }


def classify(sources: list[dict[str, Any]]) -> tuple[str, str]:
    if any(source.get("can_trade") is not False for source in sources):
        return "liquidation_coverage_unsafe_boundary", "fix can_trade boundaries before using liquidation data"
    hard = [source for source in sources if source.get("hard_failures")]
    if hard:
        return "liquidation_coverage_partial_hard_fail", "fix hard failures on liquidation feeds before merging sources"
    with_events = [source for source in sources if int(source.get("events") or 0) > 0]
    research_ready = [source for source in sources if source.get("ready_with_events")]
    alive = [source for source in sources if source.get("alive")]
    if len(research_ready) >= 2:
        return "liquidation_coverage_multi_venue_research_ready", "run preregistered cross-venue liquidation context research"
    if len(research_ready) == 1:
        return "liquidation_coverage_single_venue_research_ready", "single-source sample is ready for manual research review; do not infer cross-venue edge"
    if len(with_events) >= 2:
        return "liquidation_coverage_multi_venue_events_collecting_sample", "keep collecting until minimum sample and context-balance gates pass"
    if len(with_events) == 1:
        return "liquidation_coverage_single_venue_events_collecting_sample", "keep collecting; events exist but sample gates are not ready"
    if len(alive) >= 2:
        return "liquidation_coverage_multi_venue_alive_waiting_events", "keep both collectors running; bottleneck is real liquidation event arrival"
    if len(alive) == 1:
        return "liquidation_coverage_single_venue_alive", "bring another venue feed online or fix its data quality"
    return "liquidation_coverage_no_live_feed", "start at least one liquidation feed collector"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Multi-Venue Coverage Summary",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        "",
        "## Sources",
        "",
        "| Source | Decision | Alive | Events | Hard failures | Soft failures |",
        "|---|---|---:|---:|---|---|",
    ]
    for source in report["sources"]:
        lines.append(
            f"| `{source['name']}` | `{source.get('decision')}` | `{source.get('alive')}` | `{source.get('events')}` | "
            f"`{', '.join(source.get('hard_failures') or []) or 'none'}` | `{', '.join(source.get('soft_failures') or []) or 'none'}` |"
        )
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize liquidation coverage across Binance and Bybit feeds")
    parser.add_argument("--binance-data-quality", default="docs/LIQUIDATION_FORCE_ORDER_DATA_QUALITY_ALL_MARKET_CHECK_2026-07-01.json")
    parser.add_argument("--bybit-data-quality", default="docs/BYBIT_ALL_LIQUIDATION_DATA_QUALITY_2026-07-01.json")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_MULTI_VENUE_COVERAGE_SUMMARY_2026-07-01")
    args = parser.parse_args()

    sources = [
        source_summary("binance_usdm_forceOrder", resolve_path(args.binance_data_quality)),
        source_summary("bybit_v5_allLiquidation", resolve_path(args.bybit_data_quality)),
    ]
    decision, next_action = classify(sources)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_multi_venue_coverage_summary.py",
        "decision": decision,
        "can_trade": False,
        "boundary": {"summary_only": True, "sends_orders": False, "uses_private_credentials": False, "can_trade": False},
        "sources": sources,
        "next_action": next_action,
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "sources": len(sources), "events": {source["name"]: source.get("events") for source in sources}, "out": portable(out.with_suffix(".json")), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
