from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator as base
from tools import bitunix_wo105_causal_shadow_evaluator_v4 as v4
from tools import bitunix_wo105_packet_assembler_v6 as v6
from tools import bitunix_wo105_v3r4_rollover as rollover


ROOT = Path(__file__).resolve().parents[1]


def helpers():
    path = ROOT / "tests" / "test_bitunix_wo105_packet_assembler.py"
    spec = importlib.util.spec_from_file_location("_wo105_v4_packet_helpers", path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def build_lock() -> dict:
    predecessor = json.loads(
        (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R3_2026-07-14.json").read_text(
            encoding="utf-8"
        )
    )
    audit = {
        "decision": "bitunix_wo105_v3r3_zero_event_candle_receipt_order_contradiction_confirmed",
        "candidate_setups_observed": 1,
    }
    bindings = {
        "evaluator_v4": (v4.TOOL_PATH, base.sha256_file(ROOT / v4.TOOL_PATH)),
        "packet_assembler_v6": (v6.TOOL_PATH, base.sha256_file(ROOT / v6.TOOL_PATH)),
    }
    _, lock = rollover.build_artifacts(
        predecessor=predecessor,
        audit=audit,
        frozen_at="2026-07-15T03:10:00Z",
        forward_start="2026-07-15T04:00:00Z",
        source_bindings=bindings,
    )
    return lock


def assembled_packet(tmp_path: Path, lock: dict) -> dict:
    source, rest, ws_dir, liquidation_rows = helpers().accepted_sources(tmp_path)
    original_evaluator = v6.assembler_v3.evaluator
    original_liquidation = v6.assembler_v3.liquidation
    original_tool_path = v6.assembler_v3.TOOL_PATH
    try:
        assembler = v6.configure_for_v6()
        ws, failures = assembler.read_ws_series(ws_dir)
        assert failures == []
        view = assembler.source_view([rest])
        report = assembler.readiness_report(
            lock=lock,
            rest_runs=[rest],
            ws_report={"accepted_runs": 1},
            evaluation_at=source["evaluation_at"],
        )
        packet, report = assembler.assemble_current(
            lock=lock,
            rest_view=view,
            ws=ws,
            liquidation_rows=liquidation_rows,
            evaluation_at=source["evaluation_at"],
            report=report,
        )
    finally:
        v6.assembler_v3.evaluator = original_evaluator
        v6.assembler_v3.liquidation = original_liquidation
        v6.assembler_v3.TOOL_PATH = original_tool_path
    assert packet is not None
    assert report["blockers"] == []
    return packet


def rebind_manifest(packet: dict, lock: dict) -> None:
    setup = v4.detect_setup(packet["signal_bars"], lock["params"])
    assert setup is not None
    book = v4.select_entry_book(packet["books"], setup["signal_close_ms"], lock["params"])
    assert book is not None
    packet["source_manifest_sha256"] = v4.pre_entry_manifest(packet, setup, book)


def test_v4_lock_preserves_parameter_hash_and_validates() -> None:
    lock = build_lock()

    assert lock["parameter_cohort_sha256"] == base.canonical_sha256(lock["params"])
    assert v4.validate_lock(lock) == []
    assert lock["can_trade"] is False


def test_v4_allows_candle_receipt_jitter_but_keeps_event_order_strict(tmp_path: Path) -> None:
    lock = build_lock()
    packet = assembled_packet(tmp_path, lock)
    packet["htf_bars"][0]["received_at"] = packet["evaluation_at"] - 100
    packet["htf_bars"][1]["received_at"] = packet["evaluation_at"] - 200
    rebind_manifest(packet, lock)

    report = v4.evaluate_packet(packet, lock)
    assert "htf_bars:receipt_time_reordered" not in report.get("failures", [])
    assert report["state"] != "CAPTURE_INVALID"
    assert report["can_trade"] is False

    packet["htf_bars"][0], packet["htf_bars"][1] = packet["htf_bars"][1], packet["htf_bars"][0]
    rebind_manifest(packet, lock)
    invalid = v4.evaluate_packet(packet, lock)
    assert invalid["state"] == "CAPTURE_INVALID"
    assert "htf_bars:event_time_reordered_or_duplicate" in invalid["failures"]


def test_v4_does_not_relax_future_receipt_guard(tmp_path: Path) -> None:
    lock = build_lock()
    packet = assembled_packet(tmp_path, lock)
    packet["crowd"][0]["received_at"] = packet["evaluation_at"] + 1

    report = v4.evaluate_packet(packet, lock)

    assert report["state"] == "CAPTURE_INVALID"
    assert any("future_input" in failure for failure in report["failures"])
