from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.execution_realism_promotion_gate import build_report as build_gate
from tools.execution_realism_promotion_gate import latest_frontier
from tools.execution_realism_shadow_overlay import build_report as build_overlay


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_ledger(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["r_net", "side", "obi"])
        writer.writeheader()
        for _ in range(20):
            writer.writerow({"r_net": "0.20", "side": "LONG", "obi": "-0.80"})


def passing_portfolio_gate(tmp_path: Path) -> tuple[Path, Path]:
    snapshot = tmp_path / "paper_snapshot.json"
    write_json(
        snapshot,
        {
            "snapshot_id": "paper_snapshot_fixture",
            "snapshot_kind": "paper_account",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_mode": "local_paper_state",
            "synthetic": False,
            "can_trade": False,
        },
    )
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    gate = tmp_path / "portfolio_stress_gate.json"
    write_json(
        gate,
        {
            "decision": "portfolio_stress_promotion_gate_passed_manual_review_only",
            "inputs": {
                "snapshot_path": str(snapshot),
                "bound_snapshot_sha256": digest,
                "current_snapshot_sha256": digest,
            },
            "promotion": {
                "portfolio_stress_gate_passed": True,
                "paper_design_review_allowed": True,
                "paper_execution_allowed": False,
                "live_execution_allowed": False,
            },
            "runtime_boundary": {"orders_allowed": False, "can_trade": False},
            "can_trade": False,
        },
    )
    return gate, snapshot


def fixture(tmp_path: Path) -> tuple[Path, Path, Path, list[str]]:
    candidate = tmp_path / "candidate.json"
    write_json(
        candidate,
        {
            "decision": "oos_pass_synthetic_candidate",
            "can_trade": False,
            "runtime_boundary": {"orders_allowed": False, "can_trade": False},
        },
    )
    ledgers = []
    for index in range(3):
        ledger = tmp_path / f"candidate_{index}.csv"
        write_ledger(ledger)
        ledgers.append(str(ledger))
    frontier = tmp_path / "frontier.json"
    write_json(
        frontier,
        {
            "decision": "candidate_family_needs_forward_proof",
            "summary": {"promotable": 1, "observer_only": 0, "unsafe": 0},
            "families": [
                {
                    "family": "synthetic_candidate_family",
                    "status": "candidate_needs_forward_proof",
                    "path": str(candidate),
                    "can_trade": False,
                }
            ],
            "can_trade": False,
        },
    )
    overlay_path = tmp_path / "overlay.json"
    return candidate, frontier, overlay_path, ledgers


def test_matching_candidate_binding_allows_design_review_only(tmp_path: Path) -> None:
    candidate, frontier, overlay_path, ledgers = fixture(tmp_path)
    overlay = build_overlay(
        None,
        ledgers,
        0.0,
        candidate_report=candidate,
        candidate_family="synthetic_candidate_family",
    )
    write_json(overlay_path, overlay)
    stress_gate, _ = passing_portfolio_gate(tmp_path)

    report = build_gate(
        ROOT / "configs/EXECUTION_REALISM_PROMOTION_GATE_POLICY.json",
        overlay_path,
        frontier,
        portfolio_stress_gate_path=stress_gate,
    )

    assert overlay["candidate_binding"]["present"] is True
    assert report["decision"] == "execution_realism_gate_passed_manual_review_required"
    assert report["candidate_binding_audit"]["pass"] is True
    assert report["promotion"]["candidate_specific_overlay_present"] is True
    assert report["promotion"]["paper_design_review_allowed"] is True
    assert report["promotion"]["paper_execution_allowed"] is False
    assert report["promotion"]["live_execution_allowed"] is False
    assert report["can_trade"] is False


def test_candidate_report_tamper_blocks_design_review(tmp_path: Path) -> None:
    candidate, frontier, overlay_path, ledgers = fixture(tmp_path)
    overlay = build_overlay(
        None,
        ledgers,
        0.0,
        candidate_report=candidate,
        candidate_family="synthetic_candidate_family",
    )
    write_json(overlay_path, overlay)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["tampered_after_overlay"] = True
    write_json(candidate, payload)
    stress_gate, _ = passing_portfolio_gate(tmp_path)

    report = build_gate(
        ROOT / "configs/EXECUTION_REALISM_PROMOTION_GATE_POLICY.json",
        overlay_path,
        frontier,
        portfolio_stress_gate_path=stress_gate,
    )

    assert report["decision"] == "execution_realism_gate_blocks_promotable_candidate_until_candidate_specific_overlay"
    assert report["candidate_binding_audit"]["checks"]["candidate_report_hash_matches_binding"] is False
    assert report["promotion"]["execution_realism_gate_passed"] is False
    assert report["promotion"]["paper_design_review_allowed"] is False
    assert report["can_trade"] is False


def test_valid_candidate_binding_without_portfolio_stress_is_blocked(tmp_path: Path) -> None:
    candidate, frontier, overlay_path, ledgers = fixture(tmp_path)
    overlay = build_overlay(
        None,
        ledgers,
        0.0,
        candidate_report=candidate,
        candidate_family="synthetic_candidate_family",
    )
    write_json(overlay_path, overlay)

    report = build_gate(ROOT / "configs/EXECUTION_REALISM_PROMOTION_GATE_POLICY.json", overlay_path, frontier)

    assert report["decision"] == "execution_realism_gate_blocks_promotable_candidate_until_portfolio_stress"
    assert report["promotion"]["candidate_specific_overlay_present"] is True
    assert report["promotion"]["portfolio_stress_gate_present"] is False
    assert report["promotion"]["paper_design_review_allowed"] is False
    assert report["can_trade"] is False


def test_snapshot_tamper_after_portfolio_gate_blocks_design_review(tmp_path: Path) -> None:
    candidate, frontier, overlay_path, ledgers = fixture(tmp_path)
    overlay = build_overlay(
        None,
        ledgers,
        0.0,
        candidate_report=candidate,
        candidate_family="synthetic_candidate_family",
    )
    write_json(overlay_path, overlay)
    stress_gate, snapshot = passing_portfolio_gate(tmp_path)
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["tampered"] = True
    write_json(snapshot, payload)

    report = build_gate(
        ROOT / "configs/EXECUTION_REALISM_PROMOTION_GATE_POLICY.json",
        overlay_path,
        frontier,
        portfolio_stress_gate_path=stress_gate,
    )

    assert report["decision"] == "execution_realism_gate_blocks_promotable_candidate_until_portfolio_stress"
    assert report["portfolio_stress_gate_audit"]["checks"]["snapshot_hash_matches_bound"] is False
    assert report["promotion"]["paper_design_review_allowed"] is False
    assert report["can_trade"] is False


def test_no_promotable_candidate_keeps_current_gate_ready_but_review_closed(tmp_path: Path) -> None:
    _, _, overlay_path, ledgers = fixture(tmp_path)
    overlay = build_overlay(None, ledgers, 0.0)
    write_json(overlay_path, overlay)
    frontier = tmp_path / "empty_frontier.json"
    write_json(
        frontier,
        {
            "decision": "no_promotable_strategy_family",
            "summary": {"promotable": 0, "observer_only": 0, "unsafe": 0},
            "families": [],
            "can_trade": False,
        },
    )

    report = build_gate(ROOT / "configs/EXECUTION_REALISM_PROMOTION_GATE_POLICY.json", overlay_path, frontier)

    assert report["decision"] == "execution_realism_gate_ready_no_promotable_candidate"
    assert report["promotion"]["generic_execution_realism_gate_passed"] is True
    assert report["promotion"]["paper_design_review_allowed"] is False
    assert report["can_trade"] is False


def test_latest_frontier_uses_generated_at_not_filename_or_mtime(tmp_path: Path) -> None:
    older = tmp_path / "STRATEGY_RESEARCH_FRONTIER_MATRIX_2099-12-31.json"
    newer = tmp_path / "STRATEGY_RESEARCH_FRONTIER_MATRIX_2026-01-01.json"
    write_json(older, {"generated_at": "2026-01-01T00:00:00Z", "can_trade": False})
    write_json(newer, {"generated_at": "2026-01-02T00:00:00Z", "can_trade": False})

    assert latest_frontier(tmp_path) == newer


def test_stale_explicit_frontier_fails_closed(tmp_path: Path) -> None:
    _, _, overlay_path, ledgers = fixture(tmp_path)
    overlay = build_overlay(None, ledgers, 0.0)
    write_json(overlay_path, overlay)
    stale = tmp_path / "STRATEGY_RESEARCH_FRONTIER_MATRIX_STALE.json"
    current = tmp_path / "STRATEGY_RESEARCH_FRONTIER_MATRIX_CURRENT.json"
    frontier_payload = {
        "decision": "no_promotable_strategy_family",
        "summary": {"promotable": 0, "observer_only": 0, "unsafe": 0},
        "families": [],
        "can_trade": False,
    }
    write_json(stale, {**frontier_payload, "generated_at": "2026-01-01T00:00:00Z"})
    write_json(current, {**frontier_payload, "generated_at": "2026-01-02T00:00:00Z"})

    report = build_gate(
        ROOT / "configs/EXECUTION_REALISM_PROMOTION_GATE_POLICY.json",
        overlay_path,
        stale,
        latest_frontier_path=current,
    )

    assert report["decision"] == "execution_realism_promotion_gate_failed"
    assert report["checks"]["frontier_is_latest"] is False
    assert report["frontier_binding"]["selected_is_latest"] is False
    assert report["promotion"]["paper_design_review_allowed"] is False
    assert report["can_trade"] is False
