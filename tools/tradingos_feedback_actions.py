#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tradingos_operator_impact as impact_tool
from tradingos_market_memory_state import sha

VERSION = "1.0.0"
SCHEMA = "tradingos.operator_impact.actions.v1"
ACTION_CODES = {"H": "HELPFUL", "I": "IGNORED", "F": "FALSE_ALARM", "R": "CAUSED_REVIEW", "A": "AVOIDED_ACTION"}
IMPACT_CODES = {value: key for key, value in ACTION_CODES.items()}
LABELS = {"HELPFUL": "Helpful", "IGNORED": "Ignored", "FALSE_ALARM": "False alarm", "CAUSED_REVIEW": "Caused review", "AVOIDED_ACTION": "Avoided action"}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def events(attribution: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if attribution.get("schema") != "tradingos.value_attribution.report.v1":
        raise ValueError("unsupported attribution report schema")
    rows = attribution.get("events")
    if not isinstance(rows, list):
        raise ValueError("attribution events must be a list")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("event_id"), str) or not row["event_id"]:
            raise ValueError("attribution contains invalid event")
        result[row["event_id"]] = row
    return result


def make_token(event_id: str, impact: str) -> str:
    impact = impact.upper().strip()
    if impact not in impact_tool.IMPACTS:
        raise ValueError("unsupported impact")
    if not event_id or ":" in event_id:
        raise ValueError("invalid event_id")
    code = IMPACT_CODES[impact]
    checksum = sha({"v": 1, "event_id": event_id, "impact": impact})[:8]
    token = f"oi1:{event_id}:{code}:{checksum}"
    if len(token.encode("utf-8")) > 64:
        raise ValueError("action token exceeds 64 bytes")
    return token


def parse_token(token: str) -> tuple[str, str]:
    parts = token.strip().split(":")
    if len(parts) != 4 or parts[0] != "oi1" or parts[2] not in ACTION_CODES:
        raise ValueError("invalid operator-impact action token")
    event_id, impact = parts[1], ACTION_CODES[parts[2]]
    if make_token(event_id, impact) != token.strip():
        raise ValueError("operator-impact action token checksum mismatch")
    return event_id, impact


def build(attribution: dict[str, Any]) -> dict[str, Any]:
    rows = events(attribution)
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "events": [
            {
                "event_id": event_id,
                "symbol": row.get("symbol"),
                "kind": row.get("kind"),
                "outcome": row.get("outcome"),
                "actions": [
                    {"impact": impact, "label": LABELS[impact], "action_token": make_token(event_id, impact)}
                    for impact in sorted(impact_tool.IMPACTS)
                ],
            }
            for event_id, row in rows.items()
        ],
        "contract": {
            "delivery_callback_ready": True,
            "maximum_token_bytes": 64,
            "token_integrity_not_authentication": True,
            "explicit_operator_action_required": True,
            "automatic_feedback_forbidden": True,
            "adapter_must_supply_recorded_at": True,
        },
        "safety": {"signals_allowed": False, "orders_allowed": False, "can_trade": False, "capital_permission": "DENY"},
    }


def generate(attribution_path: Path, out_dir: Path) -> tuple[dict[str, Any], Path]:
    payload = build(read_json(attribution_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "operator_feedback_actions.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return payload, path


def main() -> int:
    p = argparse.ArgumentParser(description="Generate callback-ready explicit operator-feedback actions or consume one action token")
    p.add_argument("--attribution", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--feedback-ledger", type=Path)
    p.add_argument("--action-token")
    p.add_argument("--recorded-at")
    p.add_argument("--note", default="")
    a = p.parse_args()
    try:
        attribution_path = a.attribution.resolve()
        attribution = read_json(attribution_path)
        record_status = None
        if a.action_token or a.recorded_at or a.feedback_ledger:
            if not all([a.action_token, a.recorded_at, a.feedback_ledger]):
                raise ValueError("feedback-ledger, action-token, and recorded-at are required together")
            event_id, impact = parse_token(a.action_token)
            record_status, _ = impact_tool.record_feedback(a.feedback_ledger.resolve(), attribution, event_id, impact, a.recorded_at, a.note)
        payload, path = generate(attribution_path, a.out_dir.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2)); return 2
    print(json.dumps({"result": "PASS", "record_status": record_status, "events": len(payload["events"]), "output": str(path), "can_trade": False, "capital_permission": "DENY"}, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
