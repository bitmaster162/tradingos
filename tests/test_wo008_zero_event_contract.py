from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator as base
from tools import bitunix_wo105_causal_shadow_evaluator_v4 as evaluator_v4
from tools import bitunix_wo105_packet_assembler_v3 as assembler_v3
from tools import bitunix_wo105_packet_assembler_v6 as assembler_v6
from tools import bitunix_wo105_liquidation_context_v3 as liquidation_v3
from tools import bitunix_wo105_ws_intake as ws_intake
from tools.wo008_forensic_reducer import reduce_packet


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json").read_text(encoding="utf-8")
)


def load_v4_test_helpers():
    path = ROOT / "tests" / "test_bitunix_wo105_causal_shadow_evaluator_v4.py"
    spec = importlib.util.spec_from_file_location("_wo008_v4_helpers", path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_vetoed_candidate_has_no_event_identity_and_cannot_append(tmp_path: Path) -> None:
    helpers = load_v4_test_helpers()
    packet = helpers.assembled_packet(tmp_path, LOCK)
    setup = evaluator_v4.detect_setup(packet["signal_bars"], LOCK["params"])
    assert setup is not None
    liquidation = next(row for row in packet["crowd"] if row["payload"]["kind"] == "liquidation_skew")
    liquidation["payload"]["value"] = 0.95 if setup["direction"] == "SHORT" else -0.95
    liquidation["source_hash"] = base.canonical_sha256(liquidation["payload"])

    report = evaluator_v4.evaluate_packet(packet, LOCK)
    ledger = tmp_path / "EVENT_LEDGER.jsonl"

    assert report["state"] == "NO_SETUP"
    assert report["decision"] == "bitunix_wo105_setup_vetoed_by_crowd_or_funding"
    assert report["event_id"] is None
    assert assembler_v3.append_if_transition(ledger=ledger, lock=LOCK, previous=None, evaluation=report) is False
    assert not ledger.exists()
    assert report["can_trade"] is False


def test_minimal_closed_raw_snapshot_reproduces_semantic_no_setup(tmp_path: Path) -> None:
    rest_root = ROOT / "runtime_data" / "rest"
    ws_root = ROOT / "runtime_data" / "ws"
    liquidation_root = ROOT / "runtime_data" / "liquidations"
    if not rest_root.exists() or not ws_root.exists() or not liquidation_root.exists():
        return

    original_evaluator = assembler_v3.evaluator
    original_liquidation = assembler_v3.liquidation
    original_tool_path = assembler_v3.TOOL_PATH
    try:
        configured = assembler_v6.configure_for_v6()
        floor = evaluator_v4.parse_iso_ms(LOCK["forward_start_at"])
        assert floor is not None
        policy = json.loads((ROOT / "configs" / "BITUNIX_WO104_INDEPENDENT_ACCEPTANCE_POLICY.json").read_text(encoding="utf-8"))
        intake_dir = tmp_path / "ws_intake"
        ws_report = ws_intake.build_intake(
            ws_root,
            forward_floor_ms=floor,
            expected_parser_sha256=policy["proposal"]["parser_sha256"],
            out_dir=intake_dir,
        )
        evaluation_at = 1_784_386_300_000
        rest_runs = [
            configured.v2_assembler.inspect_rest_run(path, floor_ms=floor, evaluation_at=evaluation_at)
            for path in sorted(rest_root.glob("run_*"))
        ]
        ws, ws_failures = configured.read_ws_series(intake_dir)
        liquidation_rows, liquidation_failures = liquidation_v3.load_rows(liquidation_root)
        report = configured.readiness_report(
            lock=LOCK,
            rest_runs=rest_runs,
            ws_report=ws_report,
            evaluation_at=evaluation_at,
        )
        packet, report = configured.assemble_current(
            lock=LOCK,
            rest_view=configured.source_view(rest_runs),
            ws=ws,
            liquidation_rows=liquidation_rows,
            evaluation_at=evaluation_at,
            report=report,
        )
    finally:
        assembler_v3.evaluator = original_evaluator
        assembler_v3.liquidation = original_liquidation
        assembler_v3.TOOL_PATH = original_tool_path

    assert ws_failures == []
    assert liquidation_failures == []
    assert packet is None
    assert report["decision"] == "bitunix_wo105_v3_packet_no_current_causal_setup"
    assert report["blockers"] == []
    assert report["can_trade"] is False


def test_reducer_preserves_exact_pre_book_arrays(tmp_path: Path) -> None:
    source = tmp_path / "packet.json"
    packet = {
        "schema": "bitunix-wo105-causal-shadow-input-v1",
        "cohort_id": "fixture",
        "symbol": "BTCUSDT",
        "evaluation_at": 2_000_000,
        "source_manifest_sha256": "0" * 64,
        "signal_bars": [
            {
                "source_id": "signal",
                "observed_at": 1_000_000,
                "received_at": 1_000_010,
                "source_hash": "0" * 64,
                "schema_version": "ohlcv-bar-v1",
                "payload": {"close_ms": 1_000_000, "close": 100.0},
            }
        ],
        "htf_bars": [{"source_id": "htf"}],
        "crowd": [{"source_id": "crowd"}],
        "books": [{"large": "tail"}],
        "trades": [],
        "outcome_bars": [],
        "funding_events": [],
    }
    source.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    reduced = reduce_packet(source)

    assert reduced["signal_bars"] == packet["signal_bars"]
    assert reduced["htf_bars"] == packet["htf_bars"]
    assert reduced["crowd"] == packet["crowd"]
    assert reduced["books"] != packet["books"]
    assert reduced["funding_events"] == []
