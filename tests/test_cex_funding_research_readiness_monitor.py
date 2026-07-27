from __future__ import annotations

from tools.cex_funding_research_readiness_monitor import build_report


def contracts() -> tuple[dict, dict, dict]:
    primary = {
        "lock_id": "primary",
        "status": "fixed_forward_data_collection_contract",
        "future_research_lock": {
            "minimum_forward_span_days": 1,
            "minimum_unique_minute_snapshots": 2,
            "minimum_independent_utc_days": 1,
            "minimum_required_point_coverage": 0.98,
            "observer_creation_allowed_before_gate": False,
            "parameter_search_allowed": False,
        },
        "runtime_boundary": {"orders_allowed": False},
        "can_trade": False,
    }
    direct = {
        "lock_id": "direct",
        "status": "fixed_forward_data_collection_contract",
        "replication_gate": {
            "primary_lock_id": "primary",
            "minimum_forward_span_days": 1,
            "minimum_unique_minute_snapshots": 2,
            "minimum_independent_utc_days": 1,
            "minimum_required_point_coverage": 0.98,
            "parameter_search_allowed": False,
            "paper_review_allowed": False,
        },
        "runtime_boundary": {"orders_allowed": False},
        "can_trade": False,
    }
    alignment = {
        "lock_id": "alignment-v2",
        "status": "fixed_source_alignment_contract",
        "forward_start_at": "2026-07-13T06:25:00Z",
        "runtime_boundary": {"orders_allowed": False},
        "can_trade": False,
    }
    return primary, direct, alignment


def quality_report(lock_id: str, *, ready: bool) -> dict:
    return {
        "lock_id": lock_id,
        "decision": "healthy",
        "snapshot_quality": {"quality_pass": True},
        "sample": {
            "bad_lines": 0,
            "span_minutes": 1440 if ready else 60,
            "unique_minute_buckets": 2 if ready else 1,
            "independent_utc_days": 1,
            "required_point_coverage": 1.0,
            "first_minute_bucket": "2026-07-13T00:00:00Z",
            "last_minute_bucket": "2026-07-14T00:00:00Z" if ready else "2026-07-13T01:00:00Z",
        },
        "can_trade": False,
    }


def alignment_report(*, ready: bool = False, terminal: bool = False) -> dict:
    return {
        "lock_id": "alignment-v2",
        "decision": (
            "cex_funding_source_alignment_terminal_data_quality_failure"
            if terminal
            else "cex_funding_source_alignment_ready_for_manual_semantic_review"
            if ready
            else "cex_funding_source_alignment_collecting"
        ),
        "blockers": [] if ready else ["minimum_matching_minute_buckets"],
        "terminal": {"reached": terminal},
        "edge_evaluated": False,
        "can_trade": False,
    }


def freshness(healthy: bool = True) -> dict:
    return {
        "decision": "cex_funding_freshness_healthy" if healthy else "cex_funding_freshness_blocked",
        "healthy": healthy,
        "blockers": [] if healthy else ["source_stale"],
        "can_trade": False,
    }


def test_readiness_waits_without_creating_observer_before_primary_gate() -> None:
    primary, direct, alignment = contracts()
    report = build_report(
        primary,
        direct,
        alignment,
        quality_report("primary", ready=False),
        quality_report("direct", ready=False),
        alignment_report(),
        freshness(),
    )

    assert report["decision"] == "cex_funding_research_readiness_waiting_forward_gate"
    assert report["stages"]["observer_creation_review_allowed"] is False
    assert report["runtime_boundary"]["observer_created"] is False
    assert report["runtime_boundary"]["price_outcomes_read"] is False
    assert report["can_trade"] is False


def test_primary_gate_can_open_manual_design_review_while_replication_still_waits() -> None:
    primary, direct, alignment = contracts()
    report = build_report(
        primary,
        direct,
        alignment,
        quality_report("primary", ready=True),
        quality_report("direct", ready=False),
        alignment_report(),
        freshness(),
    )

    assert report["decision"] == "cex_funding_primary_gate_ready_waiting_replication_review"
    assert report["stages"]["observer_creation_review_allowed"] is True
    assert report["stages"]["full_replication_stack_ready"] is False
    assert report["stages"]["paper_review_allowed"] is False


def test_full_stack_ready_still_does_not_claim_edge_or_paper_permission() -> None:
    primary, direct, alignment = contracts()
    report = build_report(
        primary,
        direct,
        alignment,
        quality_report("primary", ready=True),
        quality_report("direct", ready=True),
        alignment_report(ready=True),
        freshness(),
    )

    assert report["decision"] == "cex_funding_research_stack_ready_for_manual_observer_creation_review"
    assert report["stages"]["full_replication_stack_ready"] is True
    assert report["stages"]["edge_evaluated"] is False
    assert report["stages"]["paper_review_allowed"] is False
    assert report["can_trade"] is False


def test_terminal_alignment_lock_blocks_readiness_without_stopping_collection() -> None:
    primary, direct, alignment = contracts()
    report = build_report(
        primary,
        direct,
        alignment,
        quality_report("primary", ready=False),
        quality_report("direct", ready=False),
        alignment_report(terminal=True),
        freshness(),
    )

    assert report["decision"] == "cex_funding_research_readiness_blocked_alignment_terminal"
    assert report["alignment"]["terminal"] is True
    assert report["stages"]["observer_creation_review_allowed"] is False
    assert report["can_trade"] is False


def test_lock_identity_mismatch_fails_closed() -> None:
    primary, direct, alignment = contracts()
    wrong_alignment = alignment_report()
    wrong_alignment["lock_id"] = "wrong"
    report = build_report(
        primary,
        direct,
        alignment,
        quality_report("primary", ready=False),
        quality_report("direct", ready=False),
        wrong_alignment,
        freshness(),
    )

    assert report["decision"] == "cex_funding_research_readiness_blocked_contract"
    assert "alignment_report_lock_id" in report["contract_failures"]
    assert report["can_trade"] is False
