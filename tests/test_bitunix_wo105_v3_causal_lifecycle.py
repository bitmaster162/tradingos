from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator_v3 as evaluator
from tools import bitunix_wo105_packet_assembler_v3 as assembler
from tools import bitunix_wo105_v3_freeze as freeze


ROOT = Path(__file__).resolve().parents[1]
FROZEN_LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3_2026-07-14.json").read_text(encoding="utf-8")
)


def load_helpers():
    path = ROOT / "tests" / "test_bitunix_wo105_causal_shadow_evaluator_v2.py"
    spec = importlib.util.spec_from_file_location("_wo105_v3_test_helpers", path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def candidate_lock() -> dict:
    return freeze.build_lock(
        frozen_at="2026-07-14T06:00:00Z",
        forward_start="2026-07-14T14:00:00Z",
    )


def v3_packet() -> dict:
    helpers = load_helpers()
    lock = candidate_lock()
    packet = helpers.v2_packet()
    packet["cohort_id"] = lock["cohort_id"]
    setup = evaluator.detect_setup(packet["signal_bars"], lock["params"])
    assert setup is not None
    book = evaluator.select_entry_book(packet["books"], setup["signal_close_ms"], lock["params"])
    assert book is not None
    packet["source_manifest_sha256"] = evaluator.pre_entry_manifest(packet, setup, book)
    return packet


def test_v3_changes_runtime_contract_without_changing_strategy_parameters() -> None:
    lock = candidate_lock()
    v2_lock = json.loads(
        (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json").read_text(encoding="utf-8")
    )

    assert evaluator.validate_lock(lock) == []
    assert lock["params"] == v2_lock["params"]
    assert lock["parameter_cohort_sha256"] == v2_lock["parameter_cohort_sha256"]
    assert lock["strategy_parameters_mutated_from_v2"] is False
    assert lock["runtime_contract"]["receipt_selection"] == "earliest_received_record_per_close_ms"


def test_persisted_v3_lock_is_byte_bound_and_ready() -> None:
    assert evaluator.validate_lock(FROZEN_LOCK) == []
    assert FROZEN_LOCK["parameter_cohort_sha256"] == candidate_lock()["parameter_cohort_sha256"]
    assert FROZEN_LOCK["can_trade"] is False


def test_earliest_receipt_is_not_replaced_by_a_later_refetch() -> None:
    packet = v3_packet()
    signal_close = packet["signal_bars"][-1]["payload"]["close_ms"]
    early = json.loads(json.dumps(packet["signal_bars"][-1]))
    late = json.loads(json.dumps(packet["signal_bars"][-1]))
    early["source_id"] = "first-post-close-receipt"
    early["received_at"] = signal_close + 2_000
    late["source_id"] = "later-historical-refetch"
    late["received_at"] = signal_close + 602_000

    selected = assembler.earliest_by_payload_key([late, early], key="close_ms")

    assert selected == [early]
    assert selected[0]["received_at"] <= signal_close + candidate_lock()["params"]["entry"]["latency_ms"]


def test_archived_open_event_closes_after_new_signal_bar_without_identity_drift(tmp_path: Path) -> None:
    lock = candidate_lock()
    closed_packet = v3_packet()
    open_packet = json.loads(json.dumps(closed_packet))
    open_packet["books"] = open_packet["books"][:1]
    open_packet["outcome_bars"] = []
    open_packet["evaluation_at"] = (
        open_packet["books"][0]["received_at"] + lock["params"]["entry"]["maker_order_ttl_ms"] + 1_000
    )
    opened = evaluator.evaluate_packet(open_packet, lock)
    assert opened["state"] == "SHADOW_OPEN"

    archive_path = tmp_path / f"{opened['event_id']}.json"
    assembler.archive_packet(archive_path, event_id=opened["event_id"], packet=open_packet, lock=lock)
    archive = json.loads(archive_path.read_text(encoding="utf-8"))

    newer_signal_bar = json.loads(json.dumps(closed_packet["signal_bars"][-1]))
    newer_signal_bar["payload"]["close_ms"] += 3_600_000
    newer_signal_bar["observed_at"] += 3_600_000
    newer_signal_bar["received_at"] += 3_600_000
    newer_signal_bar["source_id"] = "newer-signal-bar-not-part-of-frozen-event"
    newer_signal_bar["source_hash"] = evaluator.canonical_sha256(newer_signal_bar["payload"])
    assert evaluator.detect_setup(open_packet["signal_bars"] + [newer_signal_bar], lock["params"]) is None

    refreshed, failures = assembler.refresh_archived_packet(
        archive,
        lock=lock,
        rest_view={"outcome_bars": closed_packet["outcome_bars"], "funding_events": []},
        ws={"books": closed_packet["books"], "trades": closed_packet["trades"]},
        evaluation_at=closed_packet["evaluation_at"],
    )
    assert failures == []
    assert refreshed is not None
    previous = {
        opened["event_id"]: {
            **opened,
            "cohort_binding_sha256": lock["parameter_cohort_sha256"],
        }
    }

    closed = evaluator.evaluate_packet(refreshed, lock, previous_events=previous)

    assert closed["state"] == "SHADOW_CLOSED"
    assert closed["event_id"] == opened["event_id"]
    assert closed["details"]["source_manifest_sha256"] == opened["details"]["source_manifest_sha256"]
    assert closed["can_trade"] is False


def test_tampered_event_archive_fails_before_evaluation(tmp_path: Path) -> None:
    lock = candidate_lock()
    packet = v3_packet()
    packet["outcome_bars"] = []
    event = evaluator.evaluate_packet(packet, lock)
    assert event["event_id"]
    path = tmp_path / f"{event['event_id']}.json"
    assembler.archive_packet(path, event_id=event["event_id"], packet=packet, lock=lock)
    archive = json.loads(path.read_text(encoding="utf-8"))
    archive["packet"]["evaluation_at"] += 1

    refreshed, failures = assembler.refresh_archived_packet(
        archive,
        lock=lock,
        rest_view={"outcome_bars": [], "funding_events": []},
        ws={"books": packet["books"], "trades": packet["trades"]},
        evaluation_at=packet["evaluation_at"] + 1,
    )

    assert refreshed is None
    assert failures == ["archive_packet_hash_mismatch"]
