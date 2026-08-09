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
alert_tool = load_module("tradingos_decision_alerts_daily", ROOT / "tools" / "tradingos_decision_alerts.py")
memory_tool = load_module("tradingos_market_memory_daily", ROOT / "tools" / "tradingos_market_memory.py")
attribution_tool = load_module("tradingos_value_attribution_daily", ROOT / "tools" / "tradingos_value_attribution.py")
impact_tool = load_module("tradingos_operator_impact_daily", ROOT / "tools" / "tradingos_operator_impact.py")


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return brief_tool.base.parse_time(value, "now")


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize one read-only TradingOS Decision Brief packet per Bangkok day")
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--out-root", required=True, type=Path)
    parser.add_argument("--now")
    parser.add_argument("--memory-ledger", type=Path, help="Optional persistent market-memory ledger path; defaults to <out-root>/market_memory.ndjson")
    parser.add_argument("--attribution-ledger", type=Path, help="Optional persistent event-attribution ledger; defaults to <out-root>/value_attribution.ndjson")
    parser.add_argument("--impact-ledger", type=Path, help="Optional explicit operator-feedback ledger; defaults to <out-root>/operator_impact.ndjson")
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
        previous_cockpit = None
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
                candidate_cockpit = prior / "cockpit.json"
                previous_cockpit = candidate_cockpit if candidate_cockpit.is_file() else None
                break
        cockpit_paths = cockpit_tool.generate(
            paths["json"], snapshot_path, day_dir, previous_brief, previous_snapshot
        )
        alert_paths = alert_tool.generate(cockpit_paths["json"], day_dir, previous_cockpit)
        alert_payload = json.loads(alert_paths["json"].read_text(encoding="utf-8-sig"))
        memory_ledger = args.memory_ledger.resolve() if args.memory_ledger else args.out_root.resolve() / "market_memory.ndjson"
        memory_status, memory_paths, memory_replay = memory_tool.generate(
            memory_ledger, day_dir / "memory", cockpit_path=cockpit_paths["json"], alert_path=alert_paths["json"]
        )
        attribution_ledger = args.attribution_ledger.resolve() if args.attribution_ledger else args.out_root.resolve() / "value_attribution.ndjson"
        attribution_payload, attribution_paths = attribution_tool.generate(
            attribution_ledger, day_dir / "attribution", cockpit_paths["json"], alert_paths["json"]
        )
        impact_ledger = args.impact_ledger.resolve() if args.impact_ledger else args.out_root.resolve() / "operator_impact.ndjson"
        impact_payload, impact_paths = impact_tool.generate(
            attribution_paths["json"], impact_ledger, day_dir / "impact"
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
            "alert_decision": alert_payload["decision"],
            "alert_priority": alert_payload["priority"],
            "alert_dedupe_key": alert_payload["dedupe_key"],
            "memory_append_status": memory_status,
            "memory_sequence": memory_replay["current_sequence"],
            "memory_windows": {key: value["status"] for key, value in memory_replay["windows"].items()},
            "attribution_summary": attribution_payload["summary"],
            "directional_proof": attribution_payload["directional_proof"],
            "operator_impact_summary": impact_payload["summary"],
            "outputs": {
                **{key: path.name for key, path in paths.items()},
                **{f"cockpit_{key}": path.name for key, path in cockpit_paths.items()},
                **{f"alert_{key}": path.name for key, path in alert_paths.items()},
                **{f"memory_{key}": str(path.relative_to(day_dir)) for key, path in memory_paths.items()},
                **{f"attribution_{key}": str(path.relative_to(day_dir)) for key, path in attribution_paths.items()},
                **{f"impact_{key}": str(path.relative_to(day_dir)) for key, path in impact_paths.items()},
                "memory_ledger": str(memory_ledger),
                "attribution_ledger": str(attribution_ledger),
                "impact_ledger": str(impact_ledger),
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
