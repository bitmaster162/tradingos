"""Evidence catalog construction for TradingOS R78 AI Analyst."""
from tools.tradingos_ai_analyst_common import *

def _catalog_id(prefix: str, payload: Any) -> str:
    return f"{prefix}-{stable_sha256(payload)[:12]}"


def build_evidence_catalog(brief: dict[str, Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    seen_payloads: set[str] = set()

    def add(prefix: str, kind: str, payload: Any) -> None:
        digest = stable_sha256(payload)
        if digest in seen_payloads:
            return
        seen_payloads.add(digest)
        catalog.append({
            "evidence_id": f"{prefix}-{digest[:12]}",
            "kind": kind,
            "payload": payload,
        })

    add("DEC", "decision", brief["decision"])
    add("REG", "regime", brief["regime"])
    add("DER", "derivatives_context", brief["derivatives_context"])
    for hypothesis in brief["intent_hypotheses"]:
        direction = hypothesis["direction"]
        for row in hypothesis["supporting_evidence"]:
            add("EVD", f"{direction.lower()}_support", row)
        for row in hypothesis["contradicting_evidence"]:
            add("EVD", f"{direction.lower()}_counter", row)
    for scenario in brief["scenarios"]:
        add("SCN", "scenario", scenario)
    for key in ("global", "long", "short"):
        add("INV", f"invalidation_{key}", {"scope": key, "text": brief["invalidation"][key]})
    uncertainty_payload = {
        "status": brief["status"],
        "input_gate_passed": brief["uncertainty"]["input_gate_passed"],
        "missing_data": brief["uncertainty"]["missing_data"],
        "conflicts": brief["uncertainty"]["conflicts"],
        "blockers": brief["uncertainty"]["blockers"],
        "caveats": brief["uncertainty"]["caveats"],
    }
    add("UNC", "uncertainty", uncertainty_payload)
    add("NXT", "operator_next_action", {"text": brief["operator_next_action"]})
    catalog.sort(key=lambda row: row["evidence_id"])
    return catalog
