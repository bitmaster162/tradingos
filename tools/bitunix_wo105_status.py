#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_causal_shadow_evaluator as evaluator


TOOL_PATH = "tools/bitunix_wo105_status.py"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def read_ledger(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    if not path.is_file():
        return rows, failures
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            failures.append(f"ledger_decode:{line_number}")
            continue
        if not isinstance(row, dict):
            failures.append(f"ledger_not_object:{line_number}")
            continue
        rows.append(row)
    return rows, failures


def build_report(lock: dict[str, Any], replay: dict[str, Any], ledger_path: Path) -> dict[str, Any]:
    failures = evaluator.validate_lock(lock)
    if replay.get("canonical_replay") != "PASS" or replay.get("public_contract_confirmed") is not True:
        failures.append("canonical_replay_not_pass")
    rows, ledger_failures = read_ledger(ledger_path)
    failures.extend(ledger_failures)
    state_counts: Counter[str] = Counter()
    latest: dict[str, dict[str, Any]] = {}
    allowed_states = set((lock.get("params") or {}).get("lifecycle_states") or []) | {"HOLD"}
    for index, row in enumerate(rows, start=1):
        state = row.get("state")
        event_id = row.get("event_id")
        if state not in allowed_states:
            failures.append(f"ledger_state_invalid:{index}")
        if not isinstance(event_id, str) or len(event_id) != 64:
            failures.append(f"ledger_event_id_invalid:{index}")
            continue
        if row.get("cohort_binding_sha256") != lock.get("parameter_cohort_sha256"):
            failures.append(f"ledger_cohort_binding_mismatch:{index}")
        previous = latest.get(event_id)
        if previous and previous.get("state") in evaluator.TERMINAL_STATES:
            failures.append(f"ledger_update_after_terminal:{index}")
        if previous and previous.get("state") != "SHADOW_OPEN":
            failures.append(f"ledger_invalid_transition:{index}")
        latest[event_id] = row
        state_counts[str(state)] += 1
    minimum = int((lock.get("params") or {}).get("evaluation", {}).get("minimum_new_post_freeze_events") or 30)
    forward_events = len(latest)
    evaluator_ready = not failures
    review_ready = evaluator_ready and forward_events >= minimum
    if not evaluator_ready:
        decision = "bitunix_wo105_causal_shadow_hold_integrity_or_ledger_invalid"
    elif review_ready:
        decision = "bitunix_wo105_forward_sample_ready_for_independent_edge_review"
    else:
        decision = "bitunix_wo105_causal_shadow_ready_waiting_forward_events"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "phase": "WO105_EXACT_REPLAY_AND_CAUSAL_SHADOW",
        "public_contract_confirmed": replay.get("public_contract_confirmed") is True,
        "canonical_replay": replay.get("canonical_replay"),
        "causal_shadow_evaluator": "READY" if evaluator_ready else "HOLD",
        "forward_start_at": lock.get("forward_start_at"),
        "forward_events": forward_events,
        "minimum_forward_events": minimum,
        "forward_progress": f"{forward_events}/{minimum}",
        "ledger": str(ledger_path.resolve()),
        "ledger_rows": len(rows),
        "latest_state_counts": dict(sorted(Counter(row.get("state") for row in latest.values()).items())),
        "all_row_state_counts": dict(sorted(state_counts.items())),
        "independent_edge_review_ready": review_ready,
        "edge_evaluated": False,
        "promotion": "HOLD",
        "failures": sorted(set(failures)),
        "next_action": (
            "independent_outcome_review_without_retuning"
            if review_ready
            else "collect_new_post_freeze_raw_inputs_without_historical_backfill_or_retuning"
        ),
        "runtime_boundary": {
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Bitunix WO105 Status",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Public contract confirmed: `{str(report['public_contract_confirmed']).lower()}`",
            f"- Canonical replay: `{report['canonical_replay']}`",
            f"- Causal shadow evaluator: `{report['causal_shadow_evaluator']}`",
            f"- Forward floor: `{report['forward_start_at']}`",
            f"- Forward events: `{report['forward_progress']}`",
            f"- Latest states: `{report['latest_state_counts']}`",
            f"- Independent edge review ready: `{str(report['independent_edge_review_ready']).lower()}`",
            "- Edge evaluated: `false`",
            "- Signals/orders/capital: `DENY`",
            "- Can trade: `false`",
            f"- Failures: `{report['failures']}`",
            "",
            "Replay PASS proves parser/sample identity only. Evaluator READY proves a bounded causal implementation only. ",
            "Neither result proves positive expectancy; at least 30 new post-freeze events are required before a separate review.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Status proof for the isolated Bitunix WO105 causal-shadow lane")
    parser.add_argument("--lock", default="configs/BITUNIX_WO105_CAUSAL_SHADOW_PREREG_2026-07-14.json")
    parser.add_argument("--replay", default="docs/BITUNIX_WO105_EXACT_REPLAY_2026-07-14.json")
    parser.add_argument("--ledger", default="_dl/bitunix_wo105_shadow/EVENT_LEDGER.jsonl")
    parser.add_argument("--out-prefix", default="docs/BITUNIX_WO105_STATUS_2026-07-14")
    args = parser.parse_args()
    lock = read_object(resolve(args.lock))
    replay = read_object(resolve(args.replay))
    report = build_report(lock, replay, resolve(args.ledger))
    out = resolve(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "canonical_replay": report["canonical_replay"],
                "evaluator": report["causal_shadow_evaluator"],
                "forward_progress": report["forward_progress"],
                "can_trade": False,
            }
        )
    )
    return 0 if report["causal_shadow_evaluator"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
