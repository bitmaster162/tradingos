#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "configs" / "TRADINGOS_DECISION_BRIEF_POLICY_V1.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

snapshot_tool = load_module("tradingos_binance_public_snapshot", ROOT / "tools" / "tradingos_binance_public_snapshot.py")
brief_tool = load_module("tradingos_decision_brief_v2_daily", ROOT / "tools" / "tradingos_decision_brief_v2.py")
cockpit_tool = load_module("tradingos_decision_cockpit_daily", ROOT / "tools" / "tradingos_decision_cockpit.py")


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return brief_tool.base.parse_time(value, "now")


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize one read-only TradingOS Decision Brief packet per Bangkok day")
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--out-root", required=True, type=Path)
    parser.add_argument("--now")
    args = parser.parse_args()
    now = parse_now(args.now)
    day = now.astimezone(ZoneInfo("Asia/Bangkok")).date().isoformat()
    day_dir = args.out_root.resolve() / day
    receipt = day_dir / "RUN_RECEIPT.json"
    if receipt.exists():
        payload = json.loads(receipt.read_text(encoding="utf-8-sig"))
        print(json.dumps({"result": "DUPLICATE_DAY_SUPPRESSED", "day": day, "brief_id": payload.get("brief_id"), "can_trade": False}, indent=2))
        return 4

    try:
        capture = json.loads(args.capture.read_text(encoding="utf-8-sig"))
        snapshot = snapshot_tool.build_snapshot(capture)
        day_dir.mkdir(parents=True, exist_ok=False)
        snapshot_path = day_dir / "market_snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        brief, paths, _ = brief_tool.generate(snapshot_path, day_dir, POLICY, now)

        previous_brief = None
        previous_snapshot = None
        prior_days = sorted(
            [p for p in args.out_root.resolve().iterdir() if p.is_dir() and p.name < day],
            key=lambda p: p.name,
            reverse=True,
        ) if args.out_root.resolve().exists() else []
        for prior in prior_days:
            candidate_brief = prior / "brief.json"
            candidate_snapshot = prior / "market_snapshot.json"
            if candidate_brief.is_file() and candidate_snapshot.is_file():
                previous_brief = candidate_brief
                previous_snapshot = candidate_snapshot
                break
        cockpit_paths = cockpit_tool.generate(
            paths["json"], snapshot_path, day_dir, previous_brief, previous_snapshot
        )
        receipt_payload = {
            "schema_version": 1,
            "result": "PASS" if brief["status"] == "READY" else "FAIL_CLOSED",
            "bangkok_day": day,
            "brief_id": brief["brief_id"],
            "snapshot_id": brief["snapshot_id"],
            "status": brief["status"],
            "stance": brief["decision"]["stance"],
            "can_trade": False,
            "capital_permission": "DENY",
            "outputs": {
                **{key: path.name for key, path in paths.items()},
                **{f"cockpit_{key}": path.name for key, path in cockpit_paths.items()},
            },
        }
        receipt.write_text(json.dumps(receipt_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2

    print(json.dumps(receipt_payload, ensure_ascii=False, indent=2))
    return 0 if brief["status"] == "READY" else 3


if __name__ == "__main__":
    raise SystemExit(main())
