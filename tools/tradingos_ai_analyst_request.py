"""Request construction and validation for TradingOS R78 AI Analyst."""
from tools.tradingos_ai_analyst_common import *
from tools.tradingos_ai_analyst_policy import validate_policy
from tools.tradingos_ai_analyst_brief import validate_brief
from tools.tradingos_ai_analyst_evidence import build_evidence_catalog

def build_request(brief: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    validate_policy(policy)
    validate_brief(brief)
    brief_sha = stable_sha256(brief)
    policy_sha = stable_sha256(policy)
    catalog = build_evidence_catalog(brief)
    analysis_mode = "INTERPRETATION_ONLY" if brief["status"] == "READY" else "DIAGNOSTIC_ONLY"
    request_id = hashlib.sha256(
        f"{SCHEMA}:{VERSION}:{brief_sha}:{policy_sha}".encode("utf-8")
    ).hexdigest()[:24]
    tasks = [
        {
            "task_id": "T1",
            "kind": "THESIS",
            "instruction": "Explain the strongest case for the brief's lead view using only cited evidence IDs.",
        },
        {
            "task_id": "T2",
            "kind": "COUNTERTHESIS",
            "instruction": "Steelman the strongest opposing interpretation using only cited evidence IDs.",
        },
        {
            "task_id": "T3",
            "kind": "BLIND_SPOT",
            "instruction": "Identify what the brief may underweight, without adding outside market facts.",
        },
        {
            "task_id": "T4",
            "kind": "PREMORTEM",
            "instruction": "Explain how the watched thesis could fail, using scenarios, invalidations, conflicts, and counterevidence.",
        },
        {
            "task_id": "T5",
            "kind": "SCENARIO_READ",
            "instruction": "Compare the brief's scenarios without assigning probabilities.",
        },
        {
            "task_id": "T6",
            "kind": "OPERATOR_QUESTION",
            "instruction": "Ask the smallest set of questions that would reduce decision uncertainty.",
        },
        {
            "task_id": "T7",
            "kind": "INVALIDATION_READ",
            "instruction": "Explain which existing invalidations matter most without creating new thresholds.",
        },
    ]
    if brief["status"] == "BLOCKED":
        allowed = set(policy["blocked_brief_allowed_claim_kinds"])
        tasks = [task for task in tasks if task["kind"] in allowed]

    request = {
        "schema": SCHEMA,
        "version": VERSION,
        "request_id": request_id,
        "analysis_mode": analysis_mode,
        "brief": {
            "brief_id": brief["brief_id"],
            "brief_sha256": brief_sha,
            "snapshot_id": brief["snapshot_id"],
            "symbol": brief["symbol"],
            "timeframe": brief["timeframe"],
            "status": brief["status"],
            "decision": brief["decision"],
            "regime": brief["regime"],
            "uncertainty": brief["uncertainty"],
            "operator_next_action": brief["operator_next_action"],
            "provenance": brief["provenance"],
        },
        "evidence_catalog": catalog,
        "tasks": tasks,
        "response_contract": {
            "schema": RESPONSE_SCHEMA,
            "allowed_claim_kinds": (
                policy["allowed_claim_kinds"]
                if brief["status"] == "READY"
                else policy["blocked_brief_allowed_claim_kinds"]
            ),
            "allowed_operator_dispositions": (
                policy["allowed_operator_dispositions"]
                if brief["status"] == "READY"
                else policy["blocked_brief_allowed_dispositions"]
            ),
            "max_claims": policy["max_claims"],
            "max_questions": policy["max_questions"],
            "max_text_chars": policy["max_text_chars"],
            "every_claim_requires_evidence_refs": True,
            "every_question_requires_evidence_refs": True,
            "claim_scope_exact": "INTERPRETATION_OF_REFERENCED_EVIDENCE",
            "novel_market_fact_must_be_false": True,
            "new_numeric_literals_allowed": False,
            "external_sources_allowed": False,
            "probability_claims_allowed": False,
        },
        "safety": dict(REQUEST_SAFETY),
    }
    validate_request(request, policy)
    return request


def validate_request(request: Any, policy: dict[str, Any]) -> None:
    validate_policy(policy)
    if not isinstance(request, dict):
        raise ValueError("request must be object")
    if request.get("schema") != SCHEMA or request.get("version") != VERSION:
        raise ValueError("unsupported analyst request")
    if request.get("safety") != REQUEST_SAFETY:
        raise ValueError("analyst request safety drift")
    brief = request.get("brief")
    if not isinstance(brief, dict):
        raise ValueError("request brief missing")
    expected_brief_keys = {
        "brief_id", "brief_sha256", "snapshot_id", "symbol", "timeframe",
        "status", "decision", "regime", "uncertainty", "operator_next_action", "provenance"
    }
    if set(brief) != expected_brief_keys:
        raise ValueError("request brief key set mismatch")
    if not isinstance(brief.get("brief_sha256"), str) or _SHA_RE.fullmatch(brief["brief_sha256"]) is None:
        raise ValueError("request brief sha256 invalid")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or re.fullmatch(r"[0-9a-f]{24}", request_id) is None:
        raise ValueError("request_id invalid")
    status = brief.get("status")
    expected_mode = "INTERPRETATION_ONLY" if status == "READY" else "DIAGNOSTIC_ONLY"
    if request.get("analysis_mode") != expected_mode:
        raise ValueError("request analysis_mode/status mismatch")
    catalog = request.get("evidence_catalog")
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("evidence catalog missing")
    ids: list[str] = []
    allowed_prefixes = {"DEC", "REG", "DER", "EVD", "SCN", "INV", "UNC", "NXT"}
    for i, row in enumerate(catalog):
        if not isinstance(row, dict) or set(row) != {"evidence_id", "kind", "payload"}:
            raise ValueError(f"evidence_catalog[{i}] key set mismatch")
        evidence_id = row.get("evidence_id")
        if not isinstance(evidence_id, str) or "-" not in evidence_id:
            raise ValueError("evidence catalog id invalid")
        prefix, suffix = evidence_id.split("-", 1)
        if prefix not in allowed_prefixes or suffix != stable_sha256(row["payload"])[:12]:
            raise ValueError("evidence catalog digest/id mismatch")
        _require_text(row.get("kind"), f"evidence_catalog[{i}].kind")
        ids.append(evidence_id)
    if len(ids) != len(set(ids)):
        raise ValueError("evidence catalog duplicate id")
    if ids != sorted(ids):
        raise ValueError("evidence catalog must be sorted")

    tasks = request.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("request tasks invalid")
    task_ids: list[str] = []
    allowed_task_kinds = set(
        policy["allowed_claim_kinds"] if status == "READY"
        else policy["blocked_brief_allowed_claim_kinds"]
    )
    for i, task in enumerate(tasks):
        if not isinstance(task, dict) or set(task) != {"task_id", "kind", "instruction"}:
            raise ValueError(f"tasks[{i}] key set mismatch")
        task_id = _require_text(task.get("task_id"), f"tasks[{i}].task_id")
        task_ids.append(task_id)
        if task.get("kind") not in allowed_task_kinds:
            raise ValueError(f"tasks[{i}].kind not allowed")
        _require_text(task.get("instruction"), f"tasks[{i}].instruction")
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("duplicate task_id")

    contract = request.get("response_contract")
    if not isinstance(contract, dict):
        raise ValueError("response contract missing")
    expected_contract = {
        "schema": RESPONSE_SCHEMA,
        "allowed_claim_kinds": (
            policy["allowed_claim_kinds"]
            if status == "READY"
            else policy["blocked_brief_allowed_claim_kinds"]
        ),
        "allowed_operator_dispositions": (
            policy["allowed_operator_dispositions"]
            if status == "READY"
            else policy["blocked_brief_allowed_dispositions"]
        ),
        "max_claims": policy["max_claims"],
        "max_questions": policy["max_questions"],
        "max_text_chars": policy["max_text_chars"],
        "every_claim_requires_evidence_refs": True,
        "every_question_requires_evidence_refs": True,
        "claim_scope_exact": "INTERPRETATION_OF_REFERENCED_EVIDENCE",
        "novel_market_fact_must_be_false": True,
        "new_numeric_literals_allowed": False,
        "external_sources_allowed": False,
        "probability_claims_allowed": False,
    }
    if contract != expected_contract:
        raise ValueError("response contract/policy mismatch")
