#!/usr/bin/env python3
from __future__ import annotations

import argparse
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_liquidation_canonical_forward_observer as v2
from tools.liquidity_sweep_detector import OhlcvBar


def synthetic_open_bar_diagnostic() -> dict[str, Any]:
    bars = [
        OhlcvBar(index=0, ts="2099-01-01T00:00:00Z", open=100, high=101, low=99, close=100, volume=1),
        OhlcvBar(index=1, ts="2099-01-01T01:00:00Z", open=100, high=102, low=99, close=101, volume=1),
        OhlcvBar(index=2, ts="2099-01-01T02:00:00Z", open=101, high=103, low=100, close=102, volume=1),
    ]
    rows = [
        {
            "symbol": "BTCUSDT",
            "bar_ts": "2099-01-01T00:00:00.000Z",
            "dominant_context": "long_liquidation_flush",
            "total_notional_usd": 1.0,
        }
    ]
    records, errors = v2.study.build_event_records(rows, {"BTCUSDT": bars}, [2])
    now = datetime(2099, 1, 1, 2, 30, tzinfo=timezone.utc)
    exit_open = v2.base.parse_ts(records[0]["exit_time"]) if records else None
    exit_closed = bool(exit_open and exit_open + v2.base.parse_interval("1h") <= now)
    v2_source = inspect.getsource(v2.load_forward_records)
    return {
        "observer_now": now.isoformat().replace("+00:00", "Z"),
        "records_emitted": len(records),
        "record_errors": errors,
        "exit_bar_open": records[0]["exit_time"] if records else None,
        "exit_bar_fully_closed": exit_closed,
        "v2_calls_generic_event_builder": "study.build_event_records" in v2_source,
        "v2_filters_fully_closed_bars": "filter_fully_closed" in v2_source or "last_fully_closed" in v2_source,
    }


def build_report(lock_path: Path, observer_report_path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    observer = json.loads(observer_report_path.read_text(encoding="utf-8-sig"))
    diagnostic = synthetic_open_bar_diagnostic()
    floor = v2.base.parse_ts(lock.get("forward_start_at"))
    resolved = int((observer.get("sample") or {}).get("resolved_events") or 0)
    terminal = bool((observer.get("terminal") or {}).get("reached"))
    no_v2_forward_outcomes_admitted = resolved == 0 and not terminal
    flaw_proven = (
        diagnostic["records_emitted"] == 1
        and diagnostic["exit_bar_fully_closed"] is False
        and diagnostic["v2_calls_generic_event_builder"] is True
        and diagnostic["v2_filters_fully_closed_bars"] is False
    )
    pre_floor = floor is not None and observed_at < floor
    decision = (
        "bybit_canonical_v2_design_tombstone_open_exit_bar_risk"
        if flaw_proven and no_v2_forward_outcomes_admitted
        else "bybit_canonical_v2_bar_closure_audit_manual_attention"
    )
    return {
        "generated_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": "tools/bybit_liquidation_canonical_v2_bar_closure_audit.py",
        "decision": decision,
        "terminal": decision.endswith("open_exit_bar_risk"),
        "can_trade": False,
        "orders_allowed": False,
        "v2": {
            "lock": v2.base.portable(lock_path),
            "observer_report": v2.base.portable(observer_report_path),
            "forward_floor_at": lock.get("forward_start_at"),
            "audit_completed_before_floor": pre_floor,
            "resolved_events_at_tombstone": resolved,
            "terminal_metrics_exposed": (observer.get("outcome_review") or {}).get("terminal_metrics") is not None,
            "forward_observations_admitted_to_successor": False,
        },
        "diagnostic": diagnostic,
        "contract_failure_proven": flaw_proven,
        "no_v2_forward_outcomes_admitted": no_v2_forward_outcomes_admitted,
        "policy": {
            "modify_v2_lock_or_observer": False,
            "resume_v2_observer": False,
            "reinterpret_v2_outcomes": False,
            "successor_requires_new_future_floor": True,
            "successor_requires_fully_closed_bars": True,
        },
        "next_action": "run only the V3 closed-bars observer after sealing a new future-floor lock",
        "boundary": {
            "design_audit_only": True,
            "outcome_metrics_computed": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Bybit Canonical Forward V2 Bar-Closure Audit",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Completed before V2 floor: `{report['v2']['audit_completed_before_floor']}`",
            f"- V2 resolved events: `{report['v2']['resolved_events_at_tombstone']}`",
            f"- Synthetic current-bar record emitted: `{report['diagnostic']['records_emitted']}`",
            f"- Synthetic exit fully closed: `{report['diagnostic']['exit_bar_fully_closed']}`",
            "- V2 is immutable and terminally retired. Its observations and outcomes are not admitted to V3.",
            "- Can trade: `false`",
            "",
        ]
    )


def write_report(out_prefix: Path, report: dict[str, Any]) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Terminal pre-floor design audit for the immutable Bybit canonical V2 observer")
    parser.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V2_2026-07-13.json")
    parser.add_argument("--observer-report", default="docs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V2_2026-07-13.json")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_V2_BAR_CLOSURE_AUDIT_2026-07-13")
    parser.add_argument("--tombstone-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_V2_DESIGN_TOMBSTONE_2026-07-13")
    args = parser.parse_args()
    out = v2.base.resolve_path(args.out_prefix)
    tombstone = v2.base.resolve_path(args.tombstone_prefix)
    if tombstone.with_suffix(".json").is_file():
        report = json.loads(tombstone.with_suffix(".json").read_text(encoding="utf-8-sig"))
        report["frozen_terminal_tombstone"] = True
    else:
        report = build_report(v2.base.resolve_path(args.lock), v2.base.resolve_path(args.observer_report))
        if report["terminal"]:
            write_report(tombstone, report)
    write_report(out, report)
    print(json.dumps({"decision": report["decision"], "terminal": report["terminal"], "can_trade": False}, indent=2))
    return 0 if report["terminal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
