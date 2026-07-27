from __future__ import annotations

import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator_v2 as evaluator
from tools import bitunix_wo105_v2_status as module


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json").read_text(encoding="utf-8")
)
TOMBSTONE = json.loads(
    (ROOT / "docs" / "BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.json").read_text(encoding="utf-8")
)


def test_pre_floor_status_is_ready_but_not_collecting_or_trading(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None

    report = module.build_report(
        LOCK,
        tombstone=TOMBSTONE,
        packet_status={"decision": "bitunix_wo105_packet_sources_hold"},
        ws_status={"decision": "bitunix_wo105_ws_intake_hold_no_post_floor_capture"},
        liquidation_status={"decision": "bitunix_wo105_liquidation_context_hold"},
        ledger_path=tmp_path / "missing.jsonl",
        current_ms=floor - 1,
    )

    assert report["decision"] == "bitunix_wo105_v2_ready_waiting_forward_floor"
    assert report["phase"] == "WAITING_FORWARD_FLOOR"
    assert report["evaluator"] == "READY"
    assert report["independent_edge_review_ready"] is False
    assert report["can_trade"] is False


def test_missing_tombstone_fails_status_integrity(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None

    report = module.build_report(
        LOCK,
        tombstone=None,
        packet_status=None,
        ws_status=None,
        liquidation_status=None,
        ledger_path=tmp_path / "missing.jsonl",
        current_ms=floor + 1,
    )

    assert report["evaluator"] == "HOLD"
    assert "v1_tombstone_missing_or_invalid" in report["failures"]
    assert report["can_trade"] is False


def ledger_row(event_number: int, state: str, signal_close_ms: int) -> dict:
    return {
        "state": state,
        "event_id": f"{event_number:064x}",
        "cohort_binding_sha256": LOCK["parameter_cohort_sha256"],
        "details": {"setup": {"signal_close_ms": signal_close_ms}},
        "can_trade": False,
    }


def test_open_events_do_not_unlock_terminal_review_gate(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(ledger_row(index, "SHADOW_OPEN", floor + index), separators=(",", ":")) + "\n" for index in range(30)),
        encoding="utf-8",
    )

    report = module.build_report(
        LOCK,
        tombstone=TOMBSTONE,
        packet_status={"decision": "ready"},
        ws_status={"decision": "ready"},
        liquidation_status={"decision": "ready"},
        ledger_path=ledger,
        current_ms=floor + 10_000,
    )
    blind = module.build_blind_review_gate(report, ledger_path=ledger)

    assert report["forward_events"] == 30
    assert report["terminal_forward_events"] == 0
    assert report["independent_edge_review_ready"] is False
    assert blind["independent_review_package_allowed"] is False
    assert blind["interim_outcome_metrics_disclosed"] is False


def test_thirty_post_floor_terminal_events_unlock_review_not_trading(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(ledger_row(index, "SHADOW_CLOSED", floor + index), separators=(",", ":")) + "\n" for index in range(30)),
        encoding="utf-8",
    )

    report = module.build_report(
        LOCK,
        tombstone=TOMBSTONE,
        packet_status={"decision": "ready"},
        ws_status={"decision": "ready"},
        liquidation_status={"decision": "ready"},
        ledger_path=ledger,
        current_ms=floor + 10_000,
    )
    blind = module.build_blind_review_gate(report, ledger_path=ledger)
    serialized = json.dumps(blind, sort_keys=True)

    assert report["terminal_forward_events"] == 30
    assert report["independent_edge_review_ready"] is True
    assert blind["independent_review_package_allowed"] is True
    assert blind["can_trade"] is False
    assert all(name not in serialized for name in ("winrate", "net_r", "pnl", "expectancy"))


def test_pre_floor_terminal_row_fails_closed(tmp_path: Path) -> None:
    floor = evaluator.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(ledger_row(1, "NO_FILL", floor - 1)) + "\n", encoding="utf-8")

    report = module.build_report(
        LOCK,
        tombstone=TOMBSTONE,
        packet_status=None,
        ws_status=None,
        liquidation_status=None,
        ledger_path=ledger,
        current_ms=floor + 1,
    )

    assert report["evaluator"] == "HOLD"
    assert "ledger_terminal_signal_before_floor:1" in report["failures"]
    assert report["independent_edge_review_ready"] is False
