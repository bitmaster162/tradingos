#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.force_order_liquidation_context_intake import build_report as build_intake_report  # noqa: E402
from tools.force_order_liquidation_research_pipeline import locked_study, sha256_file  # noqa: E402


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def gate(name: str, actual: int, required: int) -> dict[str, Any]:
    return {
        "name": name,
        "passed": actual >= required,
        "actual": actual,
        "required": required,
    }


def independent_4h_blocks(rows: list[dict[str, Any]]) -> list[str]:
    blocks: set[str] = set()
    for row in rows:
        parsed = parse_ts(row.get("bar_ts")) if isinstance(row, dict) else None
        if parsed is None:
            continue
        block = parsed.replace(hour=(parsed.hour // 4) * 4, minute=0, second=0, microsecond=0)
        blocks.add(block.isoformat(timespec="seconds").replace("+00:00", "Z"))
    return sorted(blocks)


def matured_independent_blocks(
    block_ids: list[str],
    *,
    observed_at: datetime,
    cluster_hours: int,
    maximum_horizon_hours: int,
) -> list[str]:
    maturity_lag = timedelta(hours=cluster_hours + maximum_horizon_hours)
    return [
        block_id
        for block_id in block_ids
        if (parse_ts(block_id) is not None and parse_ts(block_id) + maturity_lag <= observed_at)
    ]


def evaluate_progress(
    lock: dict[str, Any],
    lock_path: Path,
    intake: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    params, lock_errors = locked_study(lock)
    evaluation = lock.get("evaluation_gate") if isinstance(lock.get("evaluation_gate"), dict) else {}
    summary = intake.get("summary") if isinstance(intake.get("summary"), dict) else {}
    contexts = intake.get("context_counts") if isinstance(intake.get("context_counts"), dict) else {}
    by_symbol = intake.get("by_symbol") if isinstance(intake.get("by_symbol"), dict) else {}
    events = int(summary.get("events") or 0)
    event_bars = int(summary.get("event_bars") or 0)
    matched = int(summary.get("matched_event_bars") or 0)
    min_events = int(params.get("min_events_for_research") or 0)
    min_event_bars = int(params.get("min_event_bars_for_research") or 0)
    min_context = int(params.get("min_context_bars") or 0)
    min_independent_blocks = int(params.get("min_independent_4h_blocks") or 0)
    cluster_hours = int(evaluation.get("cluster_hours") or 4)
    maximum_horizon_hours = max((int(item) for item in params.get("horizons") or []), default=0)
    min_symbols = int(evaluation.get("minimum_symbols_with_events") or 1)
    aggregate_rows = intake.get("_aggregate_rows") if isinstance(intake.get("_aggregate_rows"), list) else []
    independent_blocks = independent_4h_blocks(aggregate_rows)
    matured_blocks = matured_independent_blocks(
        independent_blocks,
        observed_at=observed_at,
        cluster_hours=cluster_hours,
        maximum_horizon_hours=maximum_horizon_hours,
    )
    symbols_with_events = sorted(
        symbol for symbol, values in by_symbol.items() if isinstance(values, dict) and int(values.get("events") or 0) > 0
    )
    price_cache_watermarks = (
        intake.get("price_bar_coverage_by_symbol")
        if isinstance(intake.get("price_bar_coverage_by_symbol"), dict)
        else {}
    )
    sample_gates = [
        gate("minimum_preregistered_events", events, min_events),
        gate("minimum_distinct_event_bars", event_bars, min_event_bars),
        gate("minimum_matched_price_bars", matched, min_event_bars),
        gate("minimum_long_liquidation_flush_bars", int(contexts.get("long_liquidation_flush") or 0), min_context),
        gate("minimum_short_liquidation_squeeze_bars", int(contexts.get("short_liquidation_squeeze") or 0), min_context),
        gate("minimum_symbols_with_events", len(symbols_with_events), min_symbols),
        gate("minimum_independent_4h_blocks", len(independent_blocks), min_independent_blocks),
        gate("minimum_matured_independent_4h_blocks", len(matured_blocks), min_independent_blocks),
    ]
    ready = not lock_errors and bool(intake) and all(item["passed"] for item in sample_gates)
    start = parse_ts(params.get("event_start_at"))
    elapsed_hours = max(0.0, (observed_at - start).total_seconds() / 3600.0) if start else None
    event_rate = events / elapsed_hours if elapsed_hours is not None and elapsed_hours >= 0.25 else None
    remaining_events = max(0, min_events - events)
    eta_hours = remaining_events / event_rate if event_rate and event_rate > 0 else None
    earliest_pipeline_at = None
    if independent_blocks and min_independent_blocks > 0:
        first_block = parse_ts(independent_blocks[0])
        if first_block is not None:
            earliest_pipeline_at = first_block + timedelta(
                hours=(min_independent_blocks - 1) * cluster_hours + cluster_hours + maximum_horizon_hours
            )
    if lock_errors:
        decision = "force_order_preregistered_progress_blocked_lock"
        next_action = "repair immutable preregistration lock"
    elif ready:
        decision = "force_order_preregistered_progress_ready_for_pipeline"
        next_action = "allow exactly-once locked research pipeline; still no automatic promotion"
    else:
        decision = "force_order_preregistered_progress_collecting"
        next_action = "keep collector and OHLCV cache running until every fixed sample gate passes"
    return {
        "generated_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": "tools/liquidation_force_order_preregistered_progress.py",
        "decision": decision,
        "ready_for_pipeline": ready,
        "can_trade": False,
        "orders_allowed": False,
        "lock": {
            "path": portable(lock_path),
            "lock_id": lock.get("lock_id"),
            "sha256": sha256_file(lock_path),
            "errors": lock_errors,
        },
        "sample": {
            "event_start_at": params.get("event_start_at"),
            "events": events,
            "event_bars": event_bars,
            "matched_price_bars": matched,
            "contexts": contexts,
            "symbols_with_events": symbols_with_events,
            "by_symbol": by_symbol,
            "independent_4h_blocks": len(independent_blocks),
            "independent_4h_block_ids": independent_blocks,
            "matured_independent_4h_blocks": len(matured_blocks),
            "matured_independent_4h_block_ids": matured_blocks,
            "horizon_maturity_lag_hours": cluster_hours + maximum_horizon_hours,
            "events_excluded_before_start": summary.get("events_excluded_before_start"),
            "price_cache_watermarks": price_cache_watermarks,
        },
        "gates": sample_gates,
        "blockers": [item["name"] for item in sample_gates if not item["passed"]] + lock_errors,
        "velocity": {
            "elapsed_hours": round(elapsed_hours, 6) if elapsed_hours is not None else None,
            "events_per_hour": round(event_rate, 6) if event_rate is not None else None,
            "estimated_hours_to_event_minimum": round(eta_hours, 3) if eta_hours is not None else None,
            "remaining_independent_4h_blocks": max(0, min_independent_blocks - len(independent_blocks)),
            "remaining_matured_independent_4h_blocks": max(0, min_independent_blocks - len(matured_blocks)),
            "theoretical_earliest_pipeline_at": (
                earliest_pipeline_at.isoformat(timespec="seconds").replace("+00:00", "Z")
                if earliest_pipeline_at is not None
                else None
            ),
        },
        "intake_decision": intake.get("decision"),
        "boundary": {
            "progress_only": True,
            "reads_outcomes": False,
            "runs_event_study": False,
            "automatic_promotion": False,
            "emits_alerts": False,
            "opens_paper_entries": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "next_action": next_action,
    }


def render_markdown(report: dict[str, Any]) -> str:
    sample = report["sample"]
    lines = [
        "# ForceOrder Preregistered Sample Progress",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Ready for pipeline: `{str(report['ready_for_pipeline']).lower()}`",
        f"- Events / event bars / matched bars: `{sample['events']}` / `{sample['event_bars']}` / `{sample['matched_price_bars']}`",
        f"- Symbols with events: `{sample['symbols_with_events']}`",
        f"- Contexts: `{sample['contexts']}`",
        f"- Independent 4h blocks: `{sample['independent_4h_blocks']}`",
        f"- Matured independent 4h blocks: `{sample['matured_independent_4h_blocks']}`",
        f"- Horizon maturity lag: `{sample['horizon_maturity_lag_hours']}` hours",
        f"- Price-cache watermarks: `{sample['price_cache_watermarks']}`",
        f"- Events/hour: `{report['velocity']['events_per_hour']}`",
        f"- ETA hours to event minimum: `{report['velocity']['estimated_hours_to_event_minimum']}`",
        f"- Theoretical earliest pipeline time: `{report['velocity']['theoretical_earliest_pipeline_at']}`",
        "- `can_trade=false`",
        "",
        "## Gates",
        "",
        "| Gate | Passed | Actual | Required |",
        "|---|---:|---:|---:|",
    ]
    for item in report["gates"]:
        lines.append(f"| `{item['name']}` | `{str(item['passed']).lower()}` | `{item['actual']}` | `{item['required']}` |")
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    lock_path = resolve_path(args.prereg_lock)
    lock = read_json(lock_path)
    params, _errors = locked_study(lock)
    intake_args = SimpleNamespace(
        data_dir=args.data_dir,
        symbols=",".join(params.get("symbols") or []),
        symbol="BTCUSDT",
        interval=params.get("interval") or "1h",
        event_start_at=params.get("event_start_at") or "",
        bars_csv="",
        min_events_for_research=int(params.get("min_events_for_research") or 1),
        min_event_bars_for_research=int(params.get("min_event_bars_for_research") or 1),
        max_bad_lines=args.max_bad_lines,
    )
    intake = build_intake_report(intake_args) if not _errors else {}
    return evaluate_progress(lock, lock_path, intake, now_utc())


def main() -> int:
    parser = argparse.ArgumentParser(description="Outcome-blind progress monitor for preregistered Binance forceOrder sample")
    parser.add_argument("--data-dir", default="data/live/liquidations/binance_force_order")
    parser.add_argument("--prereg-lock", default="configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json")
    parser.add_argument("--max-bad-lines", type=int, default=25)
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_PREREGISTERED_PROGRESS_2026-07-12")
    args = parser.parse_args()
    report = build_report(args)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "events": report["sample"]["events"],
                "event_bars": report["sample"]["event_bars"],
                "matched_price_bars": report["sample"]["matched_price_bars"],
                "ready_for_pipeline": report["ready_for_pipeline"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
