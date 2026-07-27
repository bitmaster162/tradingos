from __future__ import annotations

import json
from pathlib import Path

from tools.arena_paper_edge_contract_audit import build_report, validate_contract


ROOT = Path(__file__).resolve().parents[1]


def test_repo_contract_passes() -> None:
    report = build_report(ROOT / "configs" / "ARENA_PAPER_EDGE_CONTRACT.json")
    assert report["decision"] == "pass_contract_safe_for_local_docs"
    assert report["can_trade"] is False
    assert report["summary"]["edge_tasks"] >= 6
    assert report["summary"]["open_p0"] == 0


def test_contract_rejects_live_order_boundary() -> None:
    contract = json.loads((ROOT / "configs" / "ARENA_PAPER_EDGE_CONTRACT.json").read_text(encoding="utf-8"))
    contract["paper_only"] = False
    contract["can_trade"] = True
    contract["execution_boundary"]["real_orders_allowed"] = True
    contract["risk_gate"]["max_size"] = 1.0
    findings = validate_contract(contract)
    ids = {item.id for item in findings}
    assert "paper_only_not_true" in ids
    assert "can_trade_not_false" in ids
    assert "real_orders_not_disabled" in ids
    assert "risk_size_cap_too_high" in ids
