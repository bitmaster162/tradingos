"""Prompt rendering and numeric-fact guards for TradingOS R78 AI Analyst."""
from tools.tradingos_ai_analyst_common import *
from tools.tradingos_ai_analyst_request import validate_request

def render_model_prompt(request: dict[str, Any], policy: dict[str, Any]) -> str:
    """Render a deterministic, provider-agnostic prompt. It does not call a model."""
    validate_request(request, policy)
    evidence_json = json.dumps(
        request["evidence_catalog"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    tasks_json = json.dumps(
        request["tasks"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    contract_json = json.dumps(
        request["response_contract"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        "TRADINGOS R78 AI ANALYST — INTERPRETATION ONLY\n"
        f"REQUEST_ID={request['request_id']}\n"
        f"BRIEF_SHA256={request['brief']['brief_sha256']}\n"
        f"ANALYSIS_MODE={request['analysis_mode']}\n\n"
        "Rules:\n"
        "1. Use only the EVIDENCE_CATALOG below. Do not use memory, web, news, or outside market knowledge.\n"
        "2. Do not invent market facts or numeric literals absent from the Decision Brief.\n"
        "3. Do not give probabilities, signals, entries, exits, orders, leverage, sizing, or execution instructions.\n"
        "4. Every claim and question must cite one or more evidence_id values.\n"
        "5. A WATCH stance is attention priority, not an entry signal.\n"
        "6. Output one JSON object matching RESPONSE_CONTRACT. No prose outside JSON.\n\n"
        f"EVIDENCE_CATALOG={evidence_json}\n"
        f"TASKS={tasks_json}\n"
        f"RESPONSE_CONTRACT={contract_json}\n"
    )


def _walk_scalars(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_scalars(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_scalars(item)
    else:
        yield value


def _normalize_number_token(token: str) -> str:
    text = token.rstrip("%")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return text
    if value == 0:
        value = Decimal(0)
    normalized = format(value.normalize(), "f")
    return normalized


def allowed_numeric_literals(brief: dict[str, Any]) -> set[str]:
    """Whitelist only deterministic decision/market numbers, excluding dates/IDs/provenance."""
    allowed: set[str] = set()
    fact_surface = {
        "decision": brief.get("decision"),
        "regime": brief.get("regime"),
        "intent_hypotheses": brief.get("intent_hypotheses"),
        "derivatives_context": brief.get("derivatives_context"),
        "scenarios": brief.get("scenarios"),
        "invalidation": brief.get("invalidation"),
        "uncertainty": {
            "snapshot_age_minutes": brief.get("uncertainty", {}).get("snapshot_age_minutes"),
            "missing_data": brief.get("uncertainty", {}).get("missing_data"),
            "conflicts": brief.get("uncertainty", {}).get("conflicts"),
            "blockers": brief.get("uncertainty", {}).get("blockers"),
            "caveats": brief.get("uncertainty", {}).get("caveats"),
        },
        "operator_next_action": brief.get("operator_next_action"),
    }
    for scalar in _walk_scalars(fact_surface):
        if isinstance(scalar, bool) or scalar is None:
            continue
        if isinstance(scalar, (int, float)):
            if isinstance(scalar, float) and not math.isfinite(scalar):
                continue
            allowed.add(_normalize_number_token(str(scalar)))
        elif isinstance(scalar, str):
            for token in _NUMBER_RE.findall(scalar):
                allowed.add(_normalize_number_token(token))
    return allowed


def _response_free_text(response: dict[str, Any]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for i, claim in enumerate(response.get("claims", [])):
        if isinstance(claim, dict) and isinstance(claim.get("text"), str):
            rows.append((f"claims[{i}].text", claim["text"]))
    for i, question in enumerate(response.get("questions", [])):
        if isinstance(question, dict) and isinstance(question.get("text"), str):
            rows.append((f"questions[{i}].text", question["text"]))
    return rows
