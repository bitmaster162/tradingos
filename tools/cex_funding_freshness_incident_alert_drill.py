#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cex_funding_freshness_incident_alert import evaluate_transition  # noqa: E402


def sample(healthy: bool) -> dict:
    return {
        "decision": "cex_funding_freshness_healthy" if healthy else "cex_funding_freshness_blocked",
        "healthy": healthy,
        "blockers": [] if healthy else ["direct_source_fresh"],
        "sources": {
            "aggregate": {"bucket_age_seconds": 20.0},
            "direct": {"bucket_age_seconds": 240.0 if not healthy else 20.0},
            "latest_bucket_skew_minutes": 4.0 if not healthy else 0.0,
        },
        "can_trade": False,
    }


def build_drill() -> dict:
    state: dict = {}
    kinds: list[str] = []
    events: list[dict | None] = []
    for index, healthy in enumerate((True, False, False, True), start=1):
        kind, event, state = evaluate_transition(sample(healthy), state, f"2026-07-13T02:1{index}:00Z")
        kinds.append(kind)
        events.append(event)
    checks = {
        "baseline_silent": kinds[0] == "baseline_recorded" and events[0] is None,
        "blocked_transition_once": kinds[1] == "funding_freshness_blocked" and events[1] is not None,
        "repeat_suppressed": kinds[2] == "no_transition" and events[2] is None,
        "recovery_transition_once": kinds[3] == "funding_freshness_recovered" and events[3] is not None,
        "same_incident_id": bool(events[1] and events[3] and events[1]["incident_id"] == events[3]["incident_id"] == 1),
        "incident_closed": state.get("incident_open") is False,
        "no_trade_boundary": all(event is None or event.get("can_trade") is False for event in events),
    }
    return {
        "decision": "cex_funding_freshness_incident_alert_drill_passed" if all(checks.values()) else "cex_funding_freshness_incident_alert_drill_failed",
        "kinds": kinds,
        "checks": checks,
        "telegram_send_attempted": False,
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic transition-only funding freshness incident drill")
    parser.add_argument("--out", default="docs/CEX_FUNDING_FRESHNESS_INCIDENT_ALERT_DRILL_2026-07-13.json")
    args = parser.parse_args()
    report = build_drill()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["decision"].endswith("_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
