from __future__ import annotations

import copy
import unittest
from datetime import datetime

from tools.tradingos_shadow_integration import (
    SHADOW_SAFETY,
    ShadowIntegrationError,
    build_trade_case,
    build_trade_decision_packet,
    build_trade_outcome_receipt,
    build_trade_thesis,
    normalize_triaxis_adjudication,
    sha256_obj,
)
from tools.unified_shadow_domain_history_closure import build_domain_history_closure
from tools.unified_shadow_domain_subjects import (
    build_domain_history_verification,
    build_domain_subject_manifest,
    build_human_reveal_receipt,
)
from tools.unified_shadow_history_replay import build_history_replay_verification

TX = "6" * 64
CASE_BINDING = "3" * 64
GENESIS = "5" * 64
HISTORY_EFFECTS = {
    "registry_write": False,
    "ledger_write": False,
    "return_index_write": False,
    "current_truth_apply": False,
    "runtime_activation": False,
    "executor_dispatch": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}
REPLAY_EFFECTS = {
    "current_truth_apply": False,
    "continuity_write": False,
    "return_write": False,
    "archive_write": False,
    "runtime_activation": False,
    "model_call": False,
    "exchange_call": False,
    "signal": False,
    "order": False,
    "capital_effect": False,
}


def make_case(case_id="case-r4-001"):
    return build_trade_case(
        case_id=case_id,
        frozen_at="2026-08-20T02:00:00+07:00",
        symbol="BTCUSDT",
        venue="Binance",
        timeframe="1h",
        scenario="R4 domain subject binding fixture.",
        snapshot_ref={"source_id": "snapshot:r4", "sha256": "a" * 64, "schema": "market.snapshot/v1"},
        vision_ref={"source_id": "vision:r4", "sha256": "b" * 64, "schema": "vision.market/v1"},
    )


def make_replay_input(case):
    qualification = {
        "schema": "tradingos.shadow_temporal_replay_qualification.v1",
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "qualification_status": "QUALIFIED_FOR_OFFLINE_REPLAY_ONLY",
    }
    qualification["qualification_sha256"] = sha256_obj(qualification)
    body = {
        "schema": "tradingos.trusted_replay_input.v1",
        "trade_case": case,
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "qualification": qualification,
        "qualification_sha256": qualification["qualification_sha256"],
        "replay_mode": "OFFLINE_TRUSTED_REPLAY_ONLY",
        "external_expected_reference_consumed": True,
        "source_authenticity_created_here": False,
        "execution_authority": "NONE",
        "can_execute": False,
        "safety": dict(SHADOW_SAFETY),
        "effects": dict(REPLAY_EFFECTS),
    }
    body["replay_input_sha256"] = sha256_obj(body)
    return body


def make_twin(case):
    committed_at = datetime.fromisoformat("2026-08-20T02:01:00+07:00").timestamp()
    body = {
        "schema": "sct.prediction/v3",
        "case_id": case["case_id"],
        "arm": "sct",
        "options": tuple(case["options"]),
        "option_probabilities": {"LONG": 0.7, "SHORT": 0.1, "WAIT": 0.2},
        "predicted_choice": "LONG",
        "confidence": 0.7,
        "reasons": ("fixture",),
        "change_conditions": ("fixture invalidated",),
        "would_escalate": False,
        "committed_at": committed_at,
        "execution_authority": "NONE",
        "can_execute": False,
    }
    body["prediction_id"] = sha256_obj(body)
    return body


def make_packet(case, twin):
    thesis = build_trade_thesis(case, {"schema": "tradingos.decision_brief.v2", "status": "READY", "stance": "WATCH_LONG"})
    triaxis = normalize_triaxis_adjudication(
        case_id=case["case_id"],
        verdict="PASS",
        strongest_case=("support",),
        falsifiers=(),
        surviving_claims=("support",),
        evidence_refs=("snapshot:r4", "vision:r4"),
    )
    return build_trade_decision_packet(
        case,
        thesis,
        twin,
        triaxis,
        {"veto": False, "reasons": (), "can_trade": False, "capital_permission": "DENY"},
    )


def make_return_intake(case, transaction_sha=TX):
    body = {
        "schema": "control_return_broker.shadow_intake_receipt.v1",
        "source_transaction_sha256": transaction_sha,
        "continuity_receipt_sha256": "c" * 64,
        "slot": "WORK",
        "work_order_id": f"WO-{case['case_id']}",
        "zip_sha256": "9" * 64,
        "zip_bytes": 128,
        "physical_verification": {"passed": True},
        "physical_status": "VERIFIED_READ_ONLY",
        "transport": {
            "publish_performed": False,
            "collect_performed": False,
            "incoming_write": False,
            "slot_pointer_write": False,
            "registry_write": False,
            "generation_promotion": False,
            "controller_bundle_sealed": False,
            "drive_write": False,
        },
        "semantic_acceptance": "NOT_PERFORMED",
        "content_acceptance_claimed": False,
        "source_bytes_unchanged": True,
        "authority": {"execution_authority": "NONE", "apply_authorized": False},
        "safety": dict(SHADOW_SAFETY),
    }
    body["shadow_intake_sha256"] = sha256_obj(body)
    return body


def admission(case, replay_input_sha):
    entry = {
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "case_binding_sha256": CASE_BINDING,
        "replay_input_sha256": replay_input_sha,
        "ledger_id": "ledger:r4-001",
    }
    next_registry = {
        "schema": "continuityos.shadow_replay_registry_snapshot.v1",
        "registry_id": "registry:r4",
        "authority_root_sha256": "1" * 64,
        "entries": (entry,),
        "entry_count": 1,
        "write_allowed": False,
        "apply_allowed": False,
        "safety": dict(SHADOW_SAFETY),
    }
    next_registry["registry_sha256"] = sha256_obj(next_registry)
    body = {
        "schema": "continuityos.shadow_replay_admission_candidate.v1",
        "prior_registry_sha256": "2" * 64,
        **entry,
        "status": "ADMITTABLE_NEW_CASE_SHADOW_ONLY",
        "next_registry_candidate": next_registry,
        "registry_write_performed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
    }
    body["admission_candidate_sha256"] = sha256_obj(body)
    return body


def empty_ledger(case):
    body = {
        "schema": "continuityos.shadow_case_ledger_snapshot.v1",
        "ledger_id": "ledger:r4-001",
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "case_binding_sha256": CASE_BINDING,
        "genesis_sha256": GENESIS,
        "events": (),
        "event_count": 0,
        "head_event_sha256": GENESIS,
        "human_reveal_count": 0,
        "outcome_count": 0,
        "return_intake_count": 0,
        "write_allowed": False,
        "apply_allowed": False,
        "safety": dict(SHADOW_SAFETY),
    }
    body["ledger_sha256"] = sha256_obj(body)
    return body


def append_candidate(ledger, event_type, subject_sha, idem, minute):
    event = {
        "schema": "continuityos.shadow_case_event.v1",
        "ledger_id": ledger["ledger_id"],
        "case_id": ledger["case_id"],
        "case_sha256": ledger["case_sha256"],
        "case_binding_sha256": ledger["case_binding_sha256"],
        "sequence": ledger["event_count"] + 1,
        "previous_event_sha256": ledger["head_event_sha256"],
        "event_type": event_type,
        "subject_sha256": subject_sha,
        "idempotency_key": idem,
        "recorded_at": f"2026-08-20T02:{minute:02d}:00+07:00",
        "write_allowed": False,
        "apply_allowed": False,
        "safety": dict(SHADOW_SAFETY),
    }
    event["event_sha256"] = sha256_obj(event)
    events = tuple([*ledger["events"], event])
    next_ledger = {
        **{k: v for k, v in ledger.items() if k not in {"events", "event_count", "head_event_sha256", "human_reveal_count", "outcome_count", "return_intake_count", "ledger_sha256"}},
        "events": events,
        "event_count": len(events),
        "head_event_sha256": event["event_sha256"],
        "human_reveal_count": ledger["human_reveal_count"] + int(event_type == "HUMAN_REVEAL"),
        "outcome_count": ledger["outcome_count"] + int(event_type == "OUTCOME_RECEIPT"),
        "return_intake_count": ledger["return_intake_count"] + int(event_type == "RETURN_INTAKE"),
    }
    next_ledger["ledger_sha256"] = sha256_obj(next_ledger)
    candidate = {
        "schema": "continuityos.shadow_case_append_candidate.v1",
        "prior_ledger_sha256": ledger["ledger_sha256"],
        "prior_head_event_sha256": ledger["head_event_sha256"],
        "event": event,
        "next_ledger_candidate": next_ledger,
        "status": "APPENDABLE_SHADOW_ONLY",
        "ledger_write_performed": False,
        "apply_allowed": False,
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
    }
    candidate["append_candidate_sha256"] = sha256_obj(candidate)
    return candidate, next_ledger


def dedup_candidate(intake_sha):
    next_index = {
        "schema": "control_return_broker.shadow_return_index_snapshot.v1",
        "index_id": "return-index-r4",
        "authority_root_sha256": "1" * 64,
        "entries": ({
            "work_order_id": "WO-R4",
            "source_transaction_sha256": TX,
            "zip_sha256": "9" * 64,
            "shadow_intake_sha256": intake_sha,
        },),
        "entry_count": 1,
        "write_allowed": False,
        "apply_allowed": False,
        "safety": dict(SHADOW_SAFETY),
    }
    next_index["index_sha256"] = sha256_obj(next_index)
    body = {
        "schema": "control_return_broker.shadow_return_dedup_candidate.v1",
        "prior_index_sha256": "a" * 64,
        "source_transaction_sha256": TX,
        "shadow_intake_sha256": intake_sha,
        "zip_sha256": "9" * 64,
        "work_order_id": "WO-R4",
        "status": "UNIQUE_RETURN_CANDIDATE_SHADOW_ONLY",
        "next_index_candidate": next_index,
        "index_write_performed": False,
        "semantic_acceptance": "NOT_PERFORMED",
        "apply_allowed": False,
        "execution_authority": "NONE",
        "safety": dict(SHADOW_SAFETY),
    }
    body["dedup_candidate_sha256"] = sha256_obj(body)
    return body


def build_fixture(subject_overrides=None, admission_replay_override=None):
    case = make_case()
    replay = make_replay_input(case)
    twin = make_twin(case)
    packet = make_packet(case, twin)
    reveal = build_human_reveal_receipt(case, packet, actual_choice="LONG", decided_at="2026-08-20T02:10:00+07:00")
    outcome = build_trade_outcome_receipt(
        packet,
        actual_choice="LONG",
        decided_at="2026-08-20T02:10:00+07:00",
        market_outcome={"pnl_r": -0.5, "status": "LOSS"},
    )
    intake = make_return_intake(case)
    manifest = build_domain_subject_manifest(case, replay, twin, packet, reveal, outcome, intake, expected_transaction_sha256=TX)

    subjects = {row["event_type"]: row["subject_sha256"] for row in manifest["subjects"]}
    subjects.update(subject_overrides or {})
    adm = admission(case, admission_replay_override or replay["replay_input_sha256"])
    ledger = empty_ledger(case)
    initial = copy.deepcopy(ledger)
    candidates = []
    for event_type, idem, minute in (
        ("CASE_QUALIFIED", "q", 10),
        ("TWIN_COMMITTED", "t", 11),
        ("DECISION_PACKET", "d", 12),
        ("HUMAN_REVEAL", "r", 13),
        ("OUTCOME_RECEIPT", "o", 14),
        ("RETURN_INTAKE", "i", 15),
    ):
        candidate, ledger = append_candidate(ledger, event_type, subjects[event_type], idem, minute)
        candidates.append(candidate)

    history = build_history_replay_verification(
        case,
        adm,
        candidates,
        dedup_candidate(intake["shadow_intake_sha256"]),
        expected_initial_ledger_sha256=initial["ledger_sha256"],
        expected_initial_head_event_sha256=initial["head_event_sha256"],
        expected_final_ledger_sha256=ledger["ledger_sha256"],
        expected_final_head_event_sha256=ledger["head_event_sha256"],
        expected_transaction_sha256=TX,
        generated_at="2026-08-20T02:16:00+07:00",
    )
    return case, replay, twin, packet, reveal, outcome, intake, manifest, adm, candidates, history


class P0TortureReplayR4Tests(unittest.TestCase):
    def test_full_domain_history_closes_without_effect(self):
        case, replay, twin, packet, reveal, outcome, intake, manifest, adm, candidates, history = build_fixture()
        domain = build_domain_history_verification(
            case,
            history,
            candidates,
            manifest,
            expected_history_verification_sha256=history["history_verification_sha256"],
            generated_at="2026-08-20T02:17:00+07:00",
        )
        closure = build_domain_history_closure(
            case,
            adm,
            history,
            manifest,
            domain,
            generated_at="2026-08-20T02:18:00+07:00",
        )
        self.assertEqual(domain["status"], "DOMAIN_SUBJECTS_BOUND_SHADOW_ONLY")
        self.assertEqual(closure["status"], "DOMAIN_HISTORY_CLOSED_SHADOW_ONLY")
        self.assertTrue(domain["subject_binding_complete"])
        self.assertTrue(closure["admission_binding_complete"])
        self.assertTrue(all(value is False for value in closure["effects"].values()))
        self.assertEqual(closure["execution_authority"], "NONE")

    def test_generic_r3_can_accept_wrong_twin_subject_but_r4_rejects_it(self):
        case, replay, twin, packet, reveal, outcome, intake, manifest, adm, candidates, history = build_fixture(
            subject_overrides={"TWIN_COMMITTED": "0" * 64}
        )
        self.assertEqual(history["status"], "HISTORY_CHAIN_VERIFIED_SHADOW_ONLY")
        with self.assertRaisesRegex(ShadowIntegrationError, "domain_subject_event_subject_mismatch:TWIN_COMMITTED"):
            build_domain_history_verification(
                case,
                history,
                candidates,
                manifest,
                expected_history_verification_sha256=history["history_verification_sha256"],
                generated_at="2026-08-20T02:17:00+07:00",
            )

    def test_admission_replay_input_must_equal_case_qualified_subject(self):
        case, replay, twin, packet, reveal, outcome, intake, manifest, adm, candidates, history = build_fixture(
            admission_replay_override="0" * 64
        )
        domain = build_domain_history_verification(
            case,
            history,
            candidates,
            manifest,
            expected_history_verification_sha256=history["history_verification_sha256"],
            generated_at="2026-08-20T02:17:00+07:00",
        )
        with self.assertRaisesRegex(ShadowIntegrationError, "domain_history_admission_replay_input_mismatch"):
            build_domain_history_closure(case, adm, history, manifest, domain, generated_at="2026-08-20T02:18:00+07:00")

    def test_reveal_and_outcome_choice_must_match(self):
        case = make_case()
        replay = make_replay_input(case)
        twin = make_twin(case)
        packet = make_packet(case, twin)
        reveal = build_human_reveal_receipt(case, packet, actual_choice="LONG", decided_at="2026-08-20T02:10:00+07:00")
        outcome = build_trade_outcome_receipt(
            packet,
            actual_choice="SHORT",
            decided_at="2026-08-20T02:10:00+07:00",
            market_outcome={"pnl_r": 1.0},
        )
        intake = make_return_intake(case)
        with self.assertRaisesRegex(ShadowIntegrationError, "domain_subject_outcome_reveal_choice_mismatch"):
            build_domain_subject_manifest(case, replay, twin, packet, reveal, outcome, intake, expected_transaction_sha256=TX)

    def test_cross_case_twin_cannot_be_subject_artifact(self):
        case = make_case()
        replay = make_replay_input(case)
        twin = make_twin(case)
        twin["case_id"] = "other-case"
        twin["prediction_id"] = sha256_obj({k: v for k, v in twin.items() if k != "prediction_id"})
        packet = make_packet(case, make_twin(case))
        reveal = build_human_reveal_receipt(case, packet, actual_choice="LONG", decided_at="2026-08-20T02:10:00+07:00")
        outcome = build_trade_outcome_receipt(packet, actual_choice="LONG", decided_at="2026-08-20T02:10:00+07:00", market_outcome={})
        intake = make_return_intake(case)
        with self.assertRaisesRegex(ShadowIntegrationError, "domain_subject_twin_case_or_arm_mismatch"):
            build_domain_subject_manifest(case, replay, twin, packet, reveal, outcome, intake, expected_transaction_sha256=TX)

    def test_external_history_digest_is_required(self):
        case, replay, twin, packet, reveal, outcome, intake, manifest, adm, candidates, history = build_fixture()
        with self.assertRaisesRegex(ShadowIntegrationError, "domain_subject_history_external_digest_mismatch"):
            build_domain_history_verification(
                case,
                history,
                candidates,
                manifest,
                expected_history_verification_sha256="0" * 64,
                generated_at="2026-08-20T02:17:00+07:00",
            )

    def test_return_intake_must_bind_exact_transaction(self):
        case = make_case()
        replay = make_replay_input(case)
        twin = make_twin(case)
        packet = make_packet(case, twin)
        reveal = build_human_reveal_receipt(case, packet, actual_choice="LONG", decided_at="2026-08-20T02:10:00+07:00")
        outcome = build_trade_outcome_receipt(packet, actual_choice="LONG", decided_at="2026-08-20T02:10:00+07:00", market_outcome={})
        intake = make_return_intake(case, transaction_sha="0" * 64)
        with self.assertRaisesRegex(ShadowIntegrationError, "domain_subject_return_transaction_mismatch"):
            build_domain_subject_manifest(case, replay, twin, packet, reveal, outcome, intake, expected_transaction_sha256=TX)


if __name__ == "__main__":
    unittest.main()
