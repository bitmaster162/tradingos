"""Decision Brief validation for TradingOS R78 AI Analyst."""
from tools.tradingos_ai_analyst_common import *

def validate_brief(brief: Any) -> None:
    if not isinstance(brief, dict):
        raise ValueError("brief must be object")
    if brief.get("schema_version") != 1:
        raise ValueError("unsupported brief schema")
    _require_text(brief.get("brief_id"), "brief_id")
    _require_text(brief.get("snapshot_id"), "snapshot_id")
    _require_text(brief.get("generated_at"), "generated_at")
    _require_text(brief.get("as_of"), "as_of")
    _require_text(brief.get("symbol"), "symbol")
    _require_text(brief.get("timeframe"), "timeframe")
    if brief.get("status") not in ALLOWED_BRIEF_STATUS:
        raise ValueError("unsupported brief status")
    if brief.get("can_trade") is not False:
        raise ValueError("brief can_trade drift")
    if brief.get("permissions") != EXPECTED_BRIEF_PERMISSIONS:
        raise ValueError("unsafe brief permissions")

    decision = brief.get("decision")
    if not isinstance(decision, dict) or decision.get("stance") not in ALLOWED_STANCES:
        raise ValueError("invalid brief decision")
    if not isinstance(decision.get("edge_sufficient"), bool):
        raise ValueError("decision.edge_sufficient must be bool")
    finite(decision.get("score_margin"), "decision.score_margin")
    lead = decision.get("lead_direction")
    if lead not in {None, "LONG", "SHORT"}:
        raise ValueError("decision.lead_direction invalid")

    uncertainty = brief.get("uncertainty")
    if not isinstance(uncertainty, dict):
        raise ValueError("uncertainty missing")
    if not isinstance(uncertainty.get("input_gate_passed"), bool):
        raise ValueError("uncertainty.input_gate_passed must be bool")
    for field in ("missing_data", "conflicts", "blockers", "caveats"):
        value = uncertainty.get(field)
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise ValueError(f"uncertainty.{field} must be string list")
    if uncertainty.get("model_probability_claimed") is not False:
        raise ValueError("brief probability claim drift")
    if (brief["status"] == "READY") != uncertainty["input_gate_passed"]:
        raise ValueError("brief status/input gate mismatch")
    if brief["status"] == "READY" and uncertainty["blockers"]:
        raise ValueError("READY brief cannot have blockers")
    if brief["status"] == "BLOCKED" and not uncertainty["blockers"]:
        raise ValueError("BLOCKED brief requires blocker")

    regime = brief.get("regime")
    if not isinstance(regime, dict):
        raise ValueError("regime missing")
    _require_text(regime.get("label"), "regime.label")
    _require_text(regime.get("volatility"), "regime.volatility")
    basis = regime.get("basis")
    if not isinstance(basis, list) or any(not isinstance(x, str) for x in basis):
        raise ValueError("regime.basis must be string list")

    hypotheses = brief.get("intent_hypotheses")
    if not isinstance(hypotheses, list) or len(hypotheses) != 2:
        raise ValueError("brief must contain exactly LONG and SHORT hypotheses")
    directions = {item.get("direction") for item in hypotheses if isinstance(item, dict)}
    if directions != {"LONG", "SHORT"}:
        raise ValueError("brief hypotheses direction set mismatch")
    for i, item in enumerate(hypotheses):
        if not isinstance(item, dict):
            raise ValueError(f"intent_hypotheses[{i}] invalid")
        finite(item.get("support_score"), f"intent_hypotheses[{i}].support_score")
        finite(item.get("counter_score"), f"intent_hypotheses[{i}].counter_score")
        dims = item.get("independent_support_dimensions")
        if isinstance(dims, bool) or not isinstance(dims, int) or dims < 0:
            raise ValueError(f"intent_hypotheses[{i}].independent_support_dimensions")
        for field in ("supporting_evidence", "contradicting_evidence"):
            rows = item.get(field)
            if not isinstance(rows, list):
                raise ValueError(f"intent_hypotheses[{i}].{field} must be list")
            for j, row in enumerate(rows):
                _validate_evidence_row(row, f"intent_hypotheses[{i}].{field}[{j}]")

    scenarios = brief.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("brief scenarios missing")
    names: list[str] = []
    for i, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ValueError(f"scenarios[{i}] invalid")
        name = _require_text(scenario.get("name"), f"scenarios[{i}].name")
        names.append(name)
        for field in ("trigger", "invalidation", "operator_use"):
            _require_text(scenario.get(field), f"scenarios[{i}].{field}")
    if len(names) != len(set(names)):
        raise ValueError("scenario names must be unique")

    invalidation = brief.get("invalidation")
    if not isinstance(invalidation, dict):
        raise ValueError("invalidation missing")
    for field in ("global", "long", "short"):
        _require_text(invalidation.get(field), f"invalidation.{field}")

    _require_text(brief.get("operator_next_action"), "operator_next_action")

    provenance = brief.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("brief provenance missing")
    for field in ("input_sha256", "policy_sha256", "generator_sha256", "base_generator_sha256"):
        value = provenance.get(field)
        if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
            raise ValueError(f"provenance.{field}: invalid sha256")
    if provenance.get("input_producer") != EXPECTED_INPUT_PRODUCER:
        raise ValueError("unexpected Decision Brief input producer")
    if provenance.get("generator") != EXPECTED_BRIEF_GENERATOR:
        raise ValueError("unexpected Decision Brief generator")
    if provenance.get("generator_version") != EXPECTED_BRIEF_GENERATOR_VERSION:
        raise ValueError("unexpected Decision Brief generator version")
    if provenance.get("base_generator") != "tools/tradingos_decision_brief.py":
        raise ValueError("unexpected Decision Brief base generator")
    if provenance.get("policy_id") != EXPECTED_BRIEF_POLICY_ID:
        raise ValueError("unexpected Decision Brief policy")
    sources = provenance.get("input_sources")
    if not isinstance(sources, list):
        raise ValueError("provenance.input_sources must be list")
    kinds = [row.get("kind") for row in sources if isinstance(row, dict)]
    if len(kinds) != len(sources) or set(kinds) != {"ohlcv", "open_interest", "funding", "spot_flow"}:
        raise ValueError("Decision Brief input source set mismatch")
    source_ids = []
    for i, row in enumerate(sources):
        _require_text(row.get("source_id"), f"provenance.input_sources[{i}].source_id")
        _require_text(row.get("observed_at"), f"provenance.input_sources[{i}].observed_at")
        source_ids.append(row["source_id"])
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Decision Brief input source_id reuse")


def _validate_evidence_row(row: Any, field: str) -> None:
    if not isinstance(row, dict):
        raise ValueError(f"{field}: object required")
    _require_text(row.get("dimension"), f"{field}.dimension")
    _require_text(row.get("label"), f"{field}.label")
    if row.get("direction") not in ALLOWED_DIRECTIONS:
        raise ValueError(f"{field}.direction invalid")
    finite(row.get("strength"), f"{field}.strength")
    _require_text(row.get("observation"), f"{field}.observation")
