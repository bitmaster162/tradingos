from __future__ import annotations

import copy
import unittest

from tools.tradingos_shadow_integration import SHADOW_SAFETY, ShadowIntegrationError, build_trade_case, sha256_obj
from tools.unified_shadow_history_replay import build_history_replay_verification

CASE_BINDING = "3" * 64
REPLAY_INPUT = "4" * 64
GENESIS = "5" * 64
TX = "6" * 64


def case():
    return build_trade_case(
        case_id="case-r3-001",
        frozen_at="2026-08-20T02:00:00+07:00",
        symbol="BTCUSDT",
        venue="Binance",
        timeframe="1h",
        scenario="R3 replay-history fixture.",
        snapshot_ref={"source_id": "snapshot:r3", "sha256": "a" * 64, "schema": "market.snapshot/v1"},
        vision_ref={"source_id": "vision:r3", "sha256": "b" * 64, "schema": "vision.market/v1"},
    )


def admission(trade_case):
    entry = {
        "case_id": trade_case["case_id"],
        "case_sha256": trade_case["case_sha256"],
        "case_binding_sha256": CASE_BINDING,
        "replay_input_sha256": REPLAY_INPUT,
        "ledger_id": "ledger:r3-001",
    }
    next_registry = {
        "schema": "continuityos.shadow_replay_registry_snapshot.v1",
        "registry_id": "registry:r3",
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


def empty_ledger(trade_case):
    body = {
        "schema": "continuityos.shadow_case_ledger_snapshot.v1",
        "ledger_id": "ledger:r3-001",
        "case_id": trade_case["case_id"],
        "case_sha256": trade_case["case_sha256"],
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
    body = {
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
    body["append_candidate_sha256"] = sha256_obj(body)
    return body, next_ledger


def full_history(trade_case):
    ledger = empty_ledger(trade_case)
    initial = copy.deepcopy(ledger)
    candidates = []
    rows = (
        ("CASE_QUALIFIED", "c" * 64, "q", 10),
        ("TWIN_COMMITTED", "d" * 64, "t", 11),
        ("DECISION_PACKET", "e" * 64, "d", 12),
        ("HUMAN_REVEAL", "f" * 64, "r", 13),
        ("OUTCOME_RECEIPT", "7" * 64, "o", 14),
        ("RETURN_INTAKE", "8" * 64, "i", 15),
    )
    for row in rows:
        candidate, ledger = append_candidate(ledger, *row)
        candidates.append(candidate)
    return initial, candidates, ledger


def dedup_candidate(intake_sha="8" * 64):
    next_index = {
        "schema": "control_return_broker.shadow_return_index_snapshot.v1",
        "index_id": "return-index-r3",
        "authority_root_sha256": "1" * 64,
        "entries": ({
            "work_order_id": "WO-R3",
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
        "work_order_id": "WO-R3",
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


def verify(trade_case, initial, candidates, final, dedup=None, **overrides):
    kwargs = {
        "expected_initial_ledger_sha256": initial["ledger_sha256"],
        "expected_initial_head_event_sha256": initial["head_event_sha256"],
        "expected_final_ledger_sha256": final["ledger_sha256"],
        "expected_final_head_event_sha256": final["head_event_sha256"],
        "expected_transaction_sha256": TX,
        "generated_at": "2026-08-20T02:16:00+07:00",
    }
    kwargs.update(overrides)
    return build_history_replay_verification(
        trade_case,
        admission(trade_case),
        candidates,
        dedup_candidate() if dedup is None else dedup,
        **kwargs,
    )


class P0TortureReplayR3Tests(unittest.TestCase):
    def test_complete_history_verifies_without_effect(self):
        trade_case = case()
        initial, candidates, final = full_history(trade_case)
        result = verify(trade_case, initial, candidates, final)
        self.assertEqual(result["status"], "HISTORY_CHAIN_VERIFIED_SHADOW_ONLY")
        self.assertEqual(result["event_types"], ("CASE_QUALIFIED", "TWIN_COMMITTED", "DECISION_PACKET", "HUMAN_REVEAL", "OUTCOME_RECEIPT", "RETURN_INTAKE"))
        self.assertEqual(result["human_reveal_count"], 1)
        self.assertEqual(result["return_intake_count"], 1)
        self.assertFalse(result["history_write_performed"])
        self.assertTrue(all(value is False for value in result["effects"].values()))
        self.assertEqual(result["execution_authority"], "NONE")

    def test_forked_prior_ledger_is_rejected_even_if_candidate_rehashed(self):
        trade_case = case()
        initial, candidates, final = full_history(trade_case)
        forged = copy.deepcopy(candidates)
        forged[2]["prior_ledger_sha256"] = "0" * 64
        forged[2]["append_candidate_sha256"] = sha256_obj({k: v for k, v in forged[2].items() if k != "append_candidate_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "history_prior_ledger_fork_detected"):
            verify(trade_case, initial, forged, final)

    def test_duplicate_human_reveal_is_rejected(self):
        trade_case = case()
        ledger = empty_ledger(trade_case)
        initial = copy.deepcopy(ledger)
        candidates = []
        for row in (
            ("CASE_QUALIFIED", "c" * 64, "q", 10),
            ("TWIN_COMMITTED", "d" * 64, "t", 11),
            ("DECISION_PACKET", "e" * 64, "d", 12),
            ("HUMAN_REVEAL", "f" * 64, "r1", 13),
            ("HUMAN_REVEAL", "0" * 64, "r2", 14),
        ):
            candidate, ledger = append_candidate(ledger, *row)
            candidates.append(candidate)
        with self.assertRaisesRegex(ShadowIntegrationError, "history_event_order_regression|history_one_case_one_reveal_violation"):
            verify(trade_case, initial, candidates, ledger)

    def test_reordered_lifecycle_is_rejected(self):
        trade_case = case()
        ledger = empty_ledger(trade_case)
        initial = copy.deepcopy(ledger)
        candidates = []
        for row in (
            ("CASE_QUALIFIED", "c" * 64, "q", 10),
            ("DECISION_PACKET", "e" * 64, "d", 11),
            ("TWIN_COMMITTED", "d" * 64, "t", 12),
        ):
            candidate, ledger = append_candidate(ledger, *row)
            candidates.append(candidate)
        with self.assertRaisesRegex(ShadowIntegrationError, "history_event_order_regression"):
            verify(trade_case, initial, candidates, ledger)

    def test_rollback_to_older_valid_final_snapshot_is_rejected(self):
        trade_case = case()
        initial, candidates, final = full_history(trade_case)
        old_final = candidates[-2]["next_ledger_candidate"]
        with self.assertRaisesRegex(ShadowIntegrationError, "history_final_ledger_external_mismatch|history_incomplete_or_reordered"):
            verify(
                trade_case,
                initial,
                candidates[:-1],
                old_final,
                expected_final_ledger_sha256=final["ledger_sha256"],
                expected_final_head_event_sha256=final["head_event_sha256"],
            )

    def test_return_dedup_must_bind_same_intake_event(self):
        trade_case = case()
        initial, candidates, final = full_history(trade_case)
        wrong = dedup_candidate(intake_sha="0" * 64)
        with self.assertRaisesRegex(ShadowIntegrationError, "history_return_intake_mismatch"):
            verify(trade_case, initial, candidates, final, dedup=wrong)

    def test_rehashed_return_semantic_acceptance_is_rejected(self):
        trade_case = case()
        initial, candidates, final = full_history(trade_case)
        wrong = dedup_candidate()
        wrong["semantic_acceptance"] = "PASS"
        wrong["dedup_candidate_sha256"] = sha256_obj({k: v for k, v in wrong.items() if k != "dedup_candidate_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "history_return_semantic_acceptance_overclaim"):
            verify(trade_case, initial, candidates, final, dedup=wrong)

    def test_old_case_rewrapped_under_new_case_id_fails_admission_binding(self):
        trade_case = case()
        initial, candidates, final = full_history(trade_case)
        wrapped = copy.deepcopy(admission(trade_case))
        wrapped["case_id"] = "case-r3-rewrapped"
        wrapped["admission_candidate_sha256"] = sha256_obj({k: v for k, v in wrapped.items() if k != "admission_candidate_sha256"})
        with self.assertRaisesRegex(ShadowIntegrationError, "history_admission_case_mismatch"):
            build_history_replay_verification(
                trade_case,
                wrapped,
                candidates,
                dedup_candidate(),
                expected_initial_ledger_sha256=initial["ledger_sha256"],
                expected_initial_head_event_sha256=initial["head_event_sha256"],
                expected_final_ledger_sha256=final["ledger_sha256"],
                expected_final_head_event_sha256=final["head_event_sha256"],
                expected_transaction_sha256=TX,
                generated_at="2026-08-20T02:16:00+07:00",
            )


if __name__ == "__main__":
    unittest.main()
