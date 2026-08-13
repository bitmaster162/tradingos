#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MVP_PATH = ROOT / "tools" / "tradingos_pilot_mvp_status.py"


def load_mvp():
    spec = importlib.util.spec_from_file_location("tradingos_pilot_mvp_status_7d", MVP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MVP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_day(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Bangkok")).date()


def read_pilot_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"invalid pilot row {line_no}")
        rows.append(item)
    return rows


def build(daily_root: Path, pilot_ledger: Path, end_day: date) -> dict[str, Any]:
    days = [end_day - timedelta(days=offset) for offset in reversed(range(7))]
    rows: list[dict[str, Any]] = []
    missing_days: list[str] = []
    blocker_counter: Counter[str] = Counter()
    conflict_counter: Counter[str] = Counter()
    missing_counter: Counter[str] = Counter()

    for day in days:
        day_text = day.isoformat()
        day_dir = daily_root / day_text
        receipt_path = day_dir / "RUN_RECEIPT.json"
        brief_path = day_dir / "brief.json"
        if not receipt_path.exists() or not brief_path.exists():
            missing_days.append(day_text)
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        brief = json.loads(brief_path.read_text(encoding="utf-8-sig"))
        if receipt.get("bangkok_day") != day_text:
            raise ValueError(f"receipt day mismatch: {day_text}")
        if receipt.get("brief_id") != brief.get("brief_id"):
            raise ValueError(f"brief_id mismatch: {day_text}")
        if receipt.get("can_trade") is not False or brief.get("can_trade") is not False:
            raise ValueError(f"unsafe packet permission: {day_text}")
        uncertainty = brief.get("uncertainty") if isinstance(brief.get("uncertainty"), dict) else {}
        blockers = [str(x) for x in uncertainty.get("blockers", [])]
        conflicts = [str(x) for x in uncertainty.get("conflicts", [])]
        missing = [str(x) for x in uncertainty.get("missing_data", [])]
        blocker_counter.update(blockers)
        conflict_counter.update(conflicts)
        missing_counter.update(missing)
        rows.append({
            "bangkok_day": day_text,
            "brief_id": brief.get("brief_id"),
            "status": brief.get("status"),
            "stance": (brief.get("decision") or {}).get("stance"),
            "blockers": blockers,
            "conflicts": conflicts,
            "missing_data": missing,
            "operator_next_action": brief.get("operator_next_action"),
            "can_trade": False,
        })

    mvp = load_mvp().evaluate(read_pilot_rows(pilot_ledger))
    decision_counts = dict(sorted(Counter(str(row["stance"]) for row in rows).items()))
    status_counts = dict(sorted(Counter(str(row["status"]) for row in rows).items()))

    if mvp["status"] == "MVP_PASS":
        next_action = "MVP gate satisfied; preserve evidence and wait for explicit authorization before any next-phase permission change."
    elif mvp["observed_pilots"] < mvp["target_pilots"]:
        remaining = mvp["target_pilots"] - mvp["observed_pilots"]
        next_action = f"Recruit or activate {remaining} more real pilot(s), then record actual paid/renewed evidence; do not simulate pilot outcomes."
    else:
        needed = max(0, mvp["qualifying_required"] - mvp["qualifying_pilots"])
        next_action = f"Follow up with existing pilots; {needed} additional qualifying paid/renewed outcome(s) are required for the MVP gate."

    return {
        "schema": "tradingos.operator_7d_status.v1",
        "window": {
            "timezone": "Asia/Bangkok",
            "start_day": days[0].isoformat(),
            "end_day": days[-1].isoformat(),
            "expected_days": 7,
            "materialized_days": len(rows),
            "missing_days": missing_days,
        },
        "daily_packets": rows,
        "decision_counts": decision_counts,
        "status_counts": status_counts,
        "risk_gates": {
            "daily_completeness": "PASS" if not missing_days else "INCOMPLETE",
            "blockers": dict(sorted(blocker_counter.items())),
            "conflicts": dict(sorted(conflict_counter.items())),
            "missing_data": dict(sorted(missing_counter.items())),
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
            "capital_permission": "DENY",
        },
        "mvp": mvp,
        "next_action": next_action,
        "terminal": "MVP_PASS" if mvp["status"] == "MVP_PASS" else "IN_PROGRESS",
        "can_trade": False,
        "capital_permission": "DENY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic 7-day TradingOS operator status from daily packets and real pilot evidence")
    parser.add_argument("--daily-root", required=True, type=Path)
    parser.add_argument("--pilot-ledger", required=True, type=Path)
    parser.add_argument("--end-day")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = build(args.daily_root.resolve(), args.pilot_ledger.resolve(), parse_day(args.end_day))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
