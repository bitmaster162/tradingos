"""Post-model fail-closed response validation for TradingOS R78 AI Analyst."""
from tools.tradingos_ai_analyst_common import *
from tools.tradingos_ai_analyst_policy import validate_policy
from tools.tradingos_ai_analyst_brief import validate_brief
from tools.tradingos_ai_analyst_request import validate_request, build_request
from tools.tradingos_ai_analyst_prompt import allowed_numeric_literals, _response_free_text, _normalize_number_token

def validate_response(
    request: dict[str, Any],
    response: Any,
    policy: dict[str, Any],
    source_brief: dict[str, Any],
) -> dict[str, Any]:
    validate_policy(policy)
    validate_brief(source_brief)
    validate_request(request, policy)
    expected_request = build_request(source_brief, policy)
    if request != expected_request:
        raise ValueError("request/source reconstruction mismatch")
    if request["brief"]["brief_sha256"] != stable_sha256(source_brief):
        raise ValueError("request/source brief digest mismatch")
    if not isinstance(response, dict):
        raise ValueError("response must be object")
    if set(response) != ROOT_KEYS:
        raise ValueError("response root key set mismatch")
    if response.get("schema") != RESPONSE_SCHEMA:
        raise ValueError("unsupported analyst response")
    if response.get("request_id") != request["request_id"]:
        raise ValueError("response request_id mismatch")
    if response.get("brief_sha256") != request["brief"]["brief_sha256"]:
        raise ValueError("response brief_sha256 mismatch")
    if response.get("analysis_mode") != request["analysis_mode"]:
        raise ValueError("response analysis_mode mismatch")

    ceiling = {
        "external_sources_used": False,
        "probability_claimed": False,
        "signals_allowed": False,
        "orders_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    for key, expected in ceiling.items():
        if response.get(key) != expected:
            raise ValueError(f"unsafe analyst response: {key}")

    allowed_ids = {row["evidence_id"] for row in request["evidence_catalog"]}
    evidence_kind_by_id = {
        row["evidence_id"]: row["kind"] for row in request["evidence_catalog"]
    }
    allowed_kinds = set(request["response_contract"]["allowed_claim_kinds"])
    max_chars = int(request["response_contract"]["max_text_chars"])

    claims = response.get("claims")
    if not isinstance(claims, list) or len(claims) > policy["max_claims"]:
        raise ValueError("claims count invalid")
    claim_ids: list[str] = []
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict) or set(claim) != CLAIM_KEYS:
            raise ValueError(f"claims[{i}] key set mismatch")
        claim_id = _require_text(claim.get("claim_id"), f"claims[{i}].claim_id")
        claim_ids.append(claim_id)
        if claim.get("kind") not in allowed_kinds:
            raise ValueError(f"claims[{i}].kind not allowed")
        _require_text(claim.get("text"), f"claims[{i}].text", max_chars=max_chars)
        refs = claim.get("evidence_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) or ref not in allowed_ids for ref in refs)
            or len(refs) != len(set(refs))
        ):
            raise ValueError(f"claims[{i}].evidence_refs invalid")
        if claim.get("claim_scope") != "INTERPRETATION_OF_REFERENCED_EVIDENCE":
            raise ValueError(f"claims[{i}].claim_scope invalid")
        if claim.get("novel_market_fact") is not False:
            raise ValueError(f"claims[{i}] attempts novel market fact")
        ref_kinds = {evidence_kind_by_id[ref] for ref in refs}
        if claim["kind"] == "SCENARIO_READ" and "scenario" not in ref_kinds:
            raise ValueError(f"claims[{i}] scenario read requires scenario evidence")
        if claim["kind"] == "INVALIDATION_READ" and not any(
            kind.startswith("invalidation_") for kind in ref_kinds
        ):
            raise ValueError(f"claims[{i}] invalidation read requires invalidation evidence")
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("duplicate claim_id")

    disposition = response.get("operator_disposition")
    if disposition not in request["response_contract"]["allowed_operator_dispositions"]:
        raise ValueError("operator disposition not allowed")

    questions = response.get("questions")
    if not isinstance(questions, list) or len(questions) > policy["max_questions"]:
        raise ValueError("questions count invalid")
    question_ids: list[str] = []
    for i, question in enumerate(questions):
        if not isinstance(question, dict) or set(question) != QUESTION_KEYS:
            raise ValueError(f"questions[{i}] key set mismatch")
        qid = _require_text(question.get("question_id"), f"questions[{i}].question_id")
        question_ids.append(qid)
        question_text = _require_text(
            question.get("text"), f"questions[{i}].text", max_chars=max_chars
        )
        if not question_text.endswith("?"):
            raise ValueError(f"questions[{i}].text must end with question mark")
        refs = question.get("evidence_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) or ref not in allowed_ids for ref in refs)
            or len(refs) != len(set(refs))
        ):
            raise ValueError(f"questions[{i}].evidence_refs invalid")
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("duplicate question_id")

    if source_brief["status"] == "BLOCKED":
        blocked_kinds = set(policy["blocked_brief_allowed_claim_kinds"])
        if any(claim["kind"] not in blocked_kinds for claim in claims):
            raise ValueError("BLOCKED brief cannot produce directional thesis claims")
        if disposition not in policy["blocked_brief_allowed_dispositions"]:
            raise ValueError("BLOCKED brief disposition must remain diagnostic")

    numeric_whitelist = allowed_numeric_literals(source_brief)
    for field, text in _response_free_text(response):
        if _URL_RE.search(text):
            raise ValueError(f"{field}: external URL/reference forbidden")
        if _FORBIDDEN_EXECUTION_RE.search(text):
            raise ValueError(f"{field}: execution/trade language forbidden")
        if _PROBABILITY_RE.search(text):
            raise ValueError(f"{field}: probability language forbidden")
        for token in _NUMBER_RE.findall(text):
            normalized = _normalize_number_token(token)
            if normalized not in numeric_whitelist:
                raise ValueError(f"{field}: new numeric literal forbidden: {token}")

    return {
        "passed": True,
        "request_id": request["request_id"],
        "brief_sha256": request["brief"]["brief_sha256"],
        "claim_count": len(claims),
        "question_count": len(questions),
        "analysis_mode": response["analysis_mode"],
        "operator_disposition": disposition,
        "can_trade": False,
        "capital_permission": "DENY",
    }
