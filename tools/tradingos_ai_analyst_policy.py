"""Policy validation for TradingOS R78 AI Analyst."""
from tools.tradingos_ai_analyst_common import *

def validate_policy(policy: Any) -> None:
    if not isinstance(policy, dict):
        raise ValueError("policy must be object")
    if set(policy) != POLICY_KEYS:
        raise ValueError("analyst policy key set mismatch")
    if policy.get("schema_version") != 1 or policy.get("policy_id") != POLICY_ID:
        raise ValueError("unsupported analyst policy")
    if policy.get("input_contract") != "tradingos.decision_brief.v1-compatible":
        raise ValueError("unsupported analyst input contract")
    if policy.get("model_transport_in_core") is not False:
        raise ValueError("model transport must remain outside core")
    if policy.get("external_sources_allowed") is not False:
        raise ValueError("external sources must be denied")
    if policy.get("new_market_facts_allowed") is not False:
        raise ValueError("new market facts must be denied")
    if policy.get("new_numeric_literals_allowed") is not False:
        raise ValueError("new numeric literals must be denied")
    if policy.get("probability_claims_allowed") is not False:
        raise ValueError("probability claims must be denied")

    exact_limits = {"max_claims": 12, "max_questions": 8, "max_text_chars": 600}
    for field, expected in exact_limits.items():
        value = policy.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            raise ValueError(f"policy.{field}: exact v1 limit required")

    claim_kinds = policy.get("allowed_claim_kinds")
    blocked_kinds = policy.get("blocked_brief_allowed_claim_kinds")
    dispositions = policy.get("allowed_operator_dispositions")
    blocked_dispositions = policy.get("blocked_brief_allowed_dispositions")
    for field, value in (
        ("allowed_claim_kinds", claim_kinds),
        ("blocked_brief_allowed_claim_kinds", blocked_kinds),
        ("allowed_operator_dispositions", dispositions),
        ("blocked_brief_allowed_dispositions", blocked_dispositions),
    ):
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(x, str) or not x for x in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(f"policy.{field}: unique non-empty string list required")
    if claim_kinds != EXACT_ALLOWED_CLAIM_KINDS:
        raise ValueError("allowed claim kinds contract mismatch")
    if blocked_kinds != EXACT_BLOCKED_CLAIM_KINDS:
        raise ValueError("blocked claim kinds contract mismatch")
    if dispositions != EXACT_DISPOSITIONS:
        raise ValueError("operator dispositions contract mismatch")
    if blocked_dispositions != EXACT_BLOCKED_DISPOSITIONS:
        raise ValueError("blocked operator dispositions contract mismatch")

    expected = {
        "interpretation_only": True,
        "signals_allowed": False,
        "orders_allowed": False,
        "execution_authority": "NONE",
        "can_trade": False,
        "capital_permission": "DENY",
        "confers_authority": False,
    }
    if policy.get("output_permissions") != expected:
        raise ValueError("unsafe analyst output permissions")
