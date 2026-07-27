from __future__ import annotations

import json
from pathlib import Path

from tools.cross_venue_microstructure_prereg_queue import audit_queue, corrected_threshold, list_product


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_prereg_queue_grid_products_match_declared_totals() -> None:
    queue = load("configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    for item in queue["hypotheses"]:
        assert list_product(item["grid"]) == item["grid"]["total_configurations"]


def test_prereg_queue_audit_passes_before_first_seal() -> None:
    queue = load("configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    report = audit_queue(queue, {"decision": "waiting_for_microstructure_readiness", "snapshot_id": None})
    assert report["decision"] == "microstructure_prereg_queue_valid"
    assert report["execution_state"] == "waiting_for_first_sealed_snapshot"
    assert report["summary"]["registered"] == 4
    assert report["summary"]["pending_first_seal"] == 4
    assert report["summary"]["configurations_used"] == 0
    assert report["summary"]["configurations_max"] == 774
    assert report["can_trade"] is False


def test_prereg_queue_detects_unregistered_budget_mutation() -> None:
    queue = load("configs/CROSS_VENUE_MICROSTRUCTURE_PREREG_QUEUE.json")
    queue["portfolio_budget"]["used_configurations"] = 1
    report = audit_queue(queue, {"decision": "waiting_for_microstructure_readiness"})
    assert report["decision"] == "microstructure_prereg_queue_invalid"
    assert report["checks"]["portfolio_used_configurations_zero"] is False


def test_prereg_queue_bonferroni_threshold_is_strict() -> None:
    threshold = corrected_threshold(774, 0.05)
    assert threshold["per_trial_alpha"] == 0.05 / 774
    assert threshold["required_bootstrap_probability_min"] > 0.99993
