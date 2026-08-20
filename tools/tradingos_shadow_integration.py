#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

TRADE_CASE_SCHEMA = "tradingos.shadow_trade_case.v1"
TRADE_THESIS_SCHEMA = "tradingos.trade_thesis.v1"
DECISION_PACKET_SCHEMA = "tradingos.trade_decision_packet.v1"
OUTCOME_RECEIPT_SCHEMA = "tradingos.trade_outcome_receipt.v1"
TRIAXIS_SCHEMA = "triaxis.trade_adjudication.v1"
TRIAXIS_REQUEST_SCHEMA = "triaxis.trade_audit_request.v1"
SCT_PREDICTION_SCHEMA = "sct.prediction/v3"

ALLOWED_OPTIONS = ("LONG", "SHORT", "WAIT", "EXIT", "REDUCE", "ADD", "HEDGE")
ALLOWED_TRIAXIS_VERDICTS = {"PASS", "HOLD", "REJECT", "REVISE"}

SHADOW_SAFETY = {
    "mode": "SHADOW",
    "execution_authority": "NONE",
    "can_trade": False,
    "capital_permission": "DENY",
    "orders_allowed": False,
    "signals_allowed": False,
}


class ShadowIntegrationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_obj(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowIntegrationError(f"{field}_required")
    return value.strip()


def _sha256(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ShadowIntegrationError(f"{field}_must_be_sha256")
    return text


def _safe_vector(value: Mapping[str, Any], field: str = "safety") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError(f"{field}_must_be_object")
    for key, expected in SHADOW_SAFETY.items():
        if value.get(key) != expected or type(value.get(key)) is not type(expected):
            raise ShadowIntegrationError(f"unsafe_{field}:{key}")
    return dict(SHADOW_SAFETY)


def _source_ref(value: Mapping[str, Any], field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ShadowIntegrationError(f"{field}_must_be_object")
    return {
        "source_id": _text(value.get("source_id"), f"{field}.source_id"),
        "sha256": _sha256(value.get("sha256"), f"{field}.sha256"),
        "schema": _text(value.get("schema"), f"{field}.schema"),
    }


def _validate_options(options: Sequence[str]) -> tuple[str, ...]:
    if isinstance(options, (str, bytes)) or not isinstance(options, Sequence):
        raise ShadowIntegrationError("options_must_be_sequence")
    clean = tuple(dict.fromkeys(_text(item, "option").upper() for item in options))
    if len(clean) < 2:
        raise ShadowIntegrationError("at_least_two_options_required")
    unknown = sorted(set(clean) - set(ALLOWED_OPTIONS))
    if unknown:
        raise ShadowIntegrationError("unsupported_options:" + ",".join(unknown))
    if "WAIT" not in clean:
        raise ShadowIntegrationError("wait_option_required")
    return clean


def _verify_hash_bound(record: Mapping[str, Any], hash_field: str, error_code: str) -> None:
    if not isinstance(record, Mapping):
        raise ShadowIntegrationError(error_code)
    expected = sha256_obj({k: v for k, v in record.items() if k != hash_field})
    if record.get(hash_field) != expected:
        raise ShadowIntegrationError(error_code)


def _iso_epoch(value: Any, field: str) -> float:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise ShadowIntegrationError(f"{field}_must_be_iso8601") from exc
    if parsed.tzinfo is None:
        raise ShadowIntegrationError(f"{field}_timezone_required")
    return parsed.timestamp()


def validate_trade_case(case: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(case, Mapping) or case.get("schema") != TRADE_CASE_SCHEMA:
        raise ShadowIntegrationError("wrong_trade_case_schema")
    _safe_vector(case.get("safety", {}))
    _validate_options(case.get("options", ()))
    market = case.get("market_evidence")
    if not isinstance(market, Mapping):
        raise ShadowIntegrationError("market_evidence_must_be_object")
    _source_ref(market.get("snapshot", {}), "market_evidence.snapshot")
    if market.get("vision") is not None:
        _source_ref(market.get("vision", {}), "market_evidence.vision")
    if case.get("human_decision_status") != "UNREVEALED":
        raise ShadowIntegrationError("human_decision_must_be_unrevealed")
    _text(case.get("case_id"), "case_id")
    _iso_epoch(case.get("frozen_at"), "frozen_at")
    _text(case.get("symbol"), "symbol")
    _text(case.get("venue"), "venue")
    _text(case.get("timeframe"), "timeframe")
    _text(case.get("scenario"), "scenario")
    _verify_hash_bound(case, "case_sha256", "trade_case_hash_mismatch")
    return dict(case)


def build_trade_case(
    *,
    case_id: str,
    frozen_at: str,
    symbol: str,
    venue: str,
    timeframe: str,
    scenario: str,
    snapshot_ref: Mapping[str, Any],
    vision_ref: Mapping[str, Any] | None = None,
    options: Sequence[str] = ("LONG", "SHORT", "WAIT"),
) -> dict[str, Any]:
    frozen_text = _text(frozen_at, "frozen_at")
    _iso_epoch(frozen_text, "frozen_at")
    payload = {
        "schema": TRADE_CASE_SCHEMA,
        "case_id": _text(case_id, "case_id"),
        "frozen_at": frozen_text,
        "symbol": _text(symbol, "symbol"),
        "venue": _text(venue, "venue"),
        "timeframe": _text(timeframe, "timeframe"),
        "scenario": _text(scenario, "scenario"),
        "options": _validate_options(options),
        "market_evidence": {
            "snapshot": _source_ref(snapshot_ref, "snapshot_ref"),
            "vision": None if vision_ref is None else _source_ref(vision_ref, "vision_ref"),
        },
        "human_decision_status": "UNREVEALED",
        "safety": dict(SHADOW_SAFETY),
    }
    payload["case_sha256"] = sha256_obj(payload)
    return payload


def _stance_to_action(stance: str, options: Sequence[str]) -> str:
    normalized = stance.strip().upper()
    mapping = {
        "WATCH_LONG": "LONG",
        "LONG": "LONG",
        "WATCH_SHORT": "SHORT",
        "SHORT": "SHORT",
        "WAIT": "WAIT",
        "HOLD": "WAIT",
        "NO_TRADE": "WAIT",
        "ABSTAIN": "WAIT",
    }
    action = mapping.get(normalized, "WAIT")
    return action if action in options else "WAIT"


def build_trade_thesis(case: Mapping[str, Any], decision_brief: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_trade_case(case)
    if not isinstance(decision_brief, Mapping):
        raise ShadowIntegrationError("decision_brief_must_be_object")
    stance = str(decision_brief.get("stance", "WAIT"))
    thesis = {
        "schema": TRADE_THESIS_SCHEMA,
        "case_id": validated["case_id"],
        "source": {
            "schema": str(decision_brief.get("schema", decision_brief.get("schema_version", "unknown"))),
            "sha256": sha256_obj(decision_brief),
        },
        "stance": stance,
        "proposed_action": _stance_to_action(stance, validated["options"]),
        "status": str(decision_brief.get("status", "UNKNOWN")),
        "blockers": tuple(str(x) for x in decision_brief.get("blockers", ()) if str(x).strip()),
        "missing_data": tuple(str(x) for x in decision_brief.get("missing_data", ()) if str(x).strip()),
        "conflicts": tuple(str(x) for x in decision_brief.get("conflicts", ()) if str(x).strip()),
        "safety": dict(SHADOW_SAFETY),
    }
    thesis["thesis_sha256"] = sha256_obj(thesis)
    return thesis


def build_triaxis_trade_audit_request(
    case: Mapping[str, Any],
    thesis: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile an evidence-bound TRIAXIS request without simulating personas or calling tools."""
    validated = validate_trade_case(case)
    if not isinstance(thesis, Mapping) or thesis.get("schema") != TRADE_THESIS_SCHEMA:
        raise ShadowIntegrationError("wrong_trade_thesis_schema")
    if thesis.get("case_id") != validated["case_id"]:
        raise ShadowIntegrationError("thesis_case_mismatch")
    _safe_vector(thesis.get("safety", {}), "thesis_safety")
    _verify_hash_bound(thesis, "thesis_sha256", "trade_thesis_hash_mismatch")

    refs = [validated["market_evidence"]["snapshot"]]
    if validated["market_evidence"].get("vision") is not None:
        refs.append(validated["market_evidence"]["vision"])
    body = {
        "schema": TRIAXIS_REQUEST_SCHEMA,
        "case_id": validated["case_id"],
        "case_sha256": validated["case_sha256"],
        "thesis_sha256": thesis["thesis_sha256"],
        "candidate_action": thesis["proposed_action"],
        "evidence_refs": tuple(refs),
        "protocol": {
            "strongest_case": "Construct the strongest evidence-bound support for the candidate thesis.",
            "direct_falsification": "Attack hidden assumptions, stale evidence, regime mismatch, liquidity traps, contradictory flow, invalidation, sizing logic and operator-bias risk.",
            "countermodel_policy": "COUNTERMODEL_DEFAULT_OFF; use only when direct evidence leaves competing explanations live.",
            "trialectic": "State only what survives support and falsification without erasing uncertainty.",
            "evidence_audit": "Bind every surviving material claim to supplied evidence or mark it unsupported.",
        },
        "required_output": {
            "schema": TRIAXIS_SCHEMA,
            "verdict": tuple(sorted(ALLOWED_TRIAXIS_VERDICTS)),
            "fields": (
                "strongest_case",
                "falsifiers",
                "surviving_claims",
                "evidence_refs",
            ),
        },
        "constraints": {
            "independent_audit": True,
            "no_execution": True,
            "no_order": True,
            "no_signal": True,
            "do_not_convert_prediction_to_permission": True,
            "countermodel_default": False,
            "triaxis_is_oracle": False,
        },
        "execution_authority": "NONE",
        "can_execute": False,
    }
    body["request_sha256"] = sha256_obj(body)
    return body


def normalize_triaxis_adjudication(
    *,
    case_id: str,
    verdict: str,
    strongest_case: Sequence[str],
    falsifiers: Sequence[str],
    surviving_claims: Sequence[str],
    evidence_refs: Sequence[str],
) -> dict[str, Any]:
    verdict_clean = _text(verdict, "triaxis.verdict").upper()
    if verdict_clean not in ALLOWED_TRIAXIS_VERDICTS:
        raise ShadowIntegrationError("unsupported_triaxis_verdict")
    payload = {
        "schema": TRIAXIS_SCHEMA,
        "case_id": _text(case_id, "case_id"),
        "verdict": verdict_clean,
        "strongest_case": tuple(str(x).strip() for x in strongest_case if str(x).strip()),
        "falsifiers": tuple(str(x).strip() for x in falsifiers if str(x).strip()),
        "surviving_claims": tuple(str(x).strip() for x in surviving_claims if str(x).strip()),
        "evidence_refs": tuple(str(x).strip() for x in evidence_refs if str(x).strip()),
        "execution_authority": "NONE",
        "can_execute": False,
    }
    payload["adjudication_sha256"] = sha256_obj(payload)
    return payload


def _validate_twin(case: Mapping[str, Any], twin: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(twin, Mapping):
        raise ShadowIntegrationError("twin_prediction_must_be_object")
    if twin.get("schema") != SCT_PREDICTION_SCHEMA:
        raise ShadowIntegrationError("unsupported_twin_prediction_schema")
    if twin.get("case_id") != case["case_id"]:
        raise ShadowIntegrationError("twin_case_mismatch")
    if twin.get("arm") != "sct":
        raise ShadowIntegrationError("twin_arm_must_be_sct")
    supplied_options = twin.get("options")
    if not isinstance(supplied_options, (list, tuple)) or tuple(supplied_options) != tuple(case["options"]):
        raise ShadowIntegrationError("twin_options_mismatch")
    if twin.get("execution_authority") != "NONE" or twin.get("can_execute") is not False:
        raise ShadowIntegrationError("unsafe_twin_authority")

    probs = twin.get("option_probabilities")
    if not isinstance(probs, Mapping) or set(probs) != set(case["options"]):
        raise ShadowIntegrationError("twin_probability_keys_mismatch")
    clean: dict[str, float] = {}
    for option in case["options"]:
        value = probs[option]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ShadowIntegrationError("twin_probability_not_numeric")
        number = float(value)
        if not math.isfinite(number) or not 0.001 <= number <= 0.999:
            raise ShadowIntegrationError("twin_probability_out_of_range")
        clean[option] = number
    if abs(sum(clean.values()) - 1.0) > 1e-6:
        raise ShadowIntegrationError("twin_probabilities_must_sum_to_one")

    max_p = max(clean.values())
    leaders = tuple(option for option, p in clean.items() if abs(p - max_p) <= 1e-15)
    predicted = leaders[0] if len(leaders) == 1 else None
    supplied_predicted = twin.get("predicted_choice")
    if supplied_predicted is not None and supplied_predicted not in case["options"]:
        raise ShadowIntegrationError("twin_predicted_choice_outside_options")
    if supplied_predicted != predicted:
        raise ShadowIntegrationError("twin_predicted_choice_mismatch")
    supplied_confidence = twin.get("confidence")
    if isinstance(supplied_confidence, bool) or not isinstance(supplied_confidence, (int, float)):
        raise ShadowIntegrationError("twin_confidence_not_numeric")
    if abs(float(supplied_confidence) - max_p) > 1e-12:
        raise ShadowIntegrationError("twin_confidence_mismatch")

    reasons = tuple(str(x).strip() for x in twin.get("reasons", ()) if str(x).strip())
    change_conditions = tuple(str(x).strip() for x in twin.get("change_conditions", ()) if str(x).strip())
    would_escalate = twin.get("would_escalate", False)
    if not isinstance(would_escalate, bool):
        raise ShadowIntegrationError("twin_would_escalate_not_bool")
    committed_at = twin.get("committed_at")
    if isinstance(committed_at, bool) or not isinstance(committed_at, (int, float)):
        raise ShadowIntegrationError("twin_committed_at_not_numeric")
    committed_at = float(committed_at)
    if not math.isfinite(committed_at):
        raise ShadowIntegrationError("twin_committed_at_not_finite")
    if committed_at + 1e-6 < _iso_epoch(case["frozen_at"], "frozen_at"):
        raise ShadowIntegrationError("twin_prediction_precedes_case_freeze")

    body = {
        "schema": SCT_PREDICTION_SCHEMA,
        "case_id": case["case_id"],
        "arm": "sct",
        "options": tuple(case["options"]),
        "option_probabilities": clean,
        "predicted_choice": predicted,
        "confidence": max_p,
        "reasons": reasons,
        "change_conditions": change_conditions,
        "would_escalate": would_escalate,
        "committed_at": committed_at,
        "execution_authority": "NONE",
        "can_execute": False,
    }
    expected_prediction_id = sha256_obj(body)
    if twin.get("prediction_id") != expected_prediction_id:
        raise ShadowIntegrationError("twin_prediction_hash_mismatch")

    return {
        **body,
        "prediction_id": expected_prediction_id,
        "prediction_status": "UNIQUE" if predicted is not None else "TIE",
    }


def build_trade_decision_packet(
    case: Mapping[str, Any],
    thesis: Mapping[str, Any],
    twin_prediction: Mapping[str, Any],
    triaxis_adjudication: Mapping[str, Any],
    risk: Mapping[str, Any],
) -> dict[str, Any]:
    validated = validate_trade_case(case)
    if not isinstance(thesis, Mapping) or thesis.get("schema") != TRADE_THESIS_SCHEMA:
        raise ShadowIntegrationError("wrong_trade_thesis_schema")
    if thesis.get("case_id") != validated["case_id"]:
        raise ShadowIntegrationError("thesis_case_mismatch")
    _safe_vector(thesis.get("safety", {}), "thesis_safety")
    _verify_hash_bound(thesis, "thesis_sha256", "trade_thesis_hash_mismatch")

    twin = _validate_twin(validated, twin_prediction)

    if not isinstance(triaxis_adjudication, Mapping) or triaxis_adjudication.get("schema") != TRIAXIS_SCHEMA:
        raise ShadowIntegrationError("wrong_triaxis_schema")
    if triaxis_adjudication.get("case_id") != validated["case_id"]:
        raise ShadowIntegrationError("triaxis_case_mismatch")
    if triaxis_adjudication.get("execution_authority") != "NONE" or triaxis_adjudication.get("can_execute") is not False:
        raise ShadowIntegrationError("unsafe_triaxis_authority")
    verdict = triaxis_adjudication.get("verdict")
    if verdict not in ALLOWED_TRIAXIS_VERDICTS:
        raise ShadowIntegrationError("unsupported_triaxis_verdict")
    _verify_hash_bound(triaxis_adjudication, "adjudication_sha256", "triaxis_adjudication_hash_mismatch")

    if not isinstance(risk, Mapping) or risk.get("can_trade") is not False or risk.get("capital_permission") != "DENY":
        raise ShadowIntegrationError("unsafe_risk_vector")

    proposed = str(thesis.get("proposed_action", "WAIT"))
    if proposed not in validated["options"]:
        proposed = "WAIT"
    risk_veto = bool(risk.get("veto", False))
    system_recommendation = proposed
    reason = "THESIS_SURVIVED"
    if risk_veto:
        system_recommendation, reason = "WAIT", "RISK_VETO"
    elif verdict in {"HOLD", "REJECT", "REVISE"}:
        system_recommendation, reason = "WAIT", f"TRIAXIS_{verdict}"
    if system_recommendation not in validated["options"]:
        raise ShadowIntegrationError("system_recommendation_outside_case_options")

    divergence = None if twin["predicted_choice"] is None else twin["predicted_choice"] != system_recommendation
    payload = {
        "schema": DECISION_PACKET_SCHEMA,
        "case_id": validated["case_id"],
        "case_sha256": validated["case_sha256"],
        "options": tuple(validated["options"]),
        "thesis_sha256": thesis.get("thesis_sha256"),
        "twin": twin,
        "triaxis": {
            "verdict": verdict,
            "adjudication_sha256": triaxis_adjudication.get("adjudication_sha256"),
        },
        "risk": {
            "veto": risk_veto,
            "reasons": tuple(str(x) for x in risk.get("reasons", ()) if str(x).strip()),
            "can_trade": False,
            "capital_permission": "DENY",
        },
        "system_recommendation": system_recommendation,
        "recommendation_reason": reason,
        "divergence": divergence,
        "divergence_status": "UNDEFINED_TWIN_TIE" if divergence is None else "DEFINED",
        "human_decision_status": "UNREVEALED",
        "safety": dict(SHADOW_SAFETY),
    }
    payload["packet_sha256"] = sha256_obj(payload)
    return payload


def build_trade_outcome_receipt(
    packet: Mapping[str, Any],
    *,
    actual_choice: str,
    decided_at: str,
    market_outcome: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(packet, Mapping) or packet.get("schema") != DECISION_PACKET_SCHEMA:
        raise ShadowIntegrationError("wrong_decision_packet_schema")
    _safe_vector(packet.get("safety", {}), "packet_safety")
    _verify_hash_bound(packet, "packet_sha256", "decision_packet_hash_mismatch")
    options = _validate_options(packet.get("options", ()))
    choice = _text(actual_choice, "actual_choice").upper()
    if choice not in options:
        raise ShadowIntegrationError("actual_choice_outside_case_options")
    if not isinstance(market_outcome, Mapping):
        raise ShadowIntegrationError("market_outcome_must_be_object")
    twin_choice = packet["twin"].get("predicted_choice")
    payload = {
        "schema": OUTCOME_RECEIPT_SCHEMA,
        "case_id": packet["case_id"],
        "packet_sha256": packet["packet_sha256"],
        "actual_choice": choice,
        "decided_at": _text(decided_at, "decided_at"),
        "market_outcome": dict(market_outcome),
        "twin_fidelity_match": None if twin_choice is None else choice == twin_choice,
        "twin_fidelity_status": "UNSCORABLE_TIE" if twin_choice is None else "SCORABLE",
        "advisor_agreement": choice == packet["system_recommendation"],
        "safety": dict(SHADOW_SAFETY),
    }
    payload["receipt_sha256"] = sha256_obj(payload)
    return payload
