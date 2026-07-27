from __future__ import annotations

from tools.cross_venue_microstructure_readiness_progress_monitor import build_progress_report


def gate(*, span: float = 20.0, remaining: float = 148.0, trade_cov: float = 99.0, book_cov: float = 98.0, decision: str = "waiting_for_microstructure_readiness") -> dict:
    return {
        "decision": decision,
        "readiness_diagnostics": {
            "span_hours": span,
            "required_hours": 168.0,
            "remaining_hours": remaining,
            "trade_coverage_pct": trade_cov,
            "book_coverage_pct": book_cov,
            "required_trade_coverage_pct": 95.0,
            "required_book_coverage_pct": 95.0,
            "binance_missing_ids": 0,
            "coinbase_missing_ids": 0,
            "estimated_earliest_time_gate_at_utc": "2026-07-01T12:00:00+00:00",
        },
        "can_trade": False,
    }


def data_quality(*, generated_at: str = "2026-06-25T00:10:00+00:00", span: float = 20.0, trade_cov: float = 99.0, book_cov: float = 98.0) -> dict:
    return {
        "generated_at": generated_at,
        "coverage": {
            "span_hours": span,
            "both_trade_coverage_pct": trade_cov,
            "both_book_coverage_pct": book_cov,
        },
        "archive": {"trades": 1000, "book_snapshots": 100, "minute_feature_rows": 100},
        "research_readiness": {"minimum_hours": 168.0, "minimum_dual_venue_coverage_pct": 95.0},
        "trade_id_integrity": {"binance": {"missing_ids": 0}, "coinbase": {"missing_ids": 0}},
        "can_trade": False,
    }


def health(classification: str = "cross_venue_microstructure_healthy_collecting") -> dict:
    return {"classification": classification, "can_trade": False}


def previous(*, generated_at: str = "2026-06-25T00:00:00+00:00", span: float = 19.8, remaining: float = 148.2, trade_cov: float = 99.0, book_cov: float = 98.0) -> dict:
    return {
        "data_generated_at": generated_at,
        "span_hours": span,
        "remaining_hours": remaining,
        "trade_coverage_pct": trade_cov,
        "book_coverage_pct": book_cov,
        "can_trade": False,
    }


def test_progress_monitor_records_baseline_without_previous_state() -> None:
    report = build_progress_report(gate(), data_quality(), health(), {})

    assert report["decision"] == "readiness_progress_baseline_recorded"
    assert report["runtime_boundary"]["runs_research_batch"] is False
    assert report["can_trade"] is False


def test_progress_monitor_accepts_healthy_growth() -> None:
    report = build_progress_report(gate(span=20.1, remaining=147.9), data_quality(span=20.1), health(), previous())

    assert report["decision"] == "readiness_progress_waiting_healthy"
    assert report["span_delta_hours"] > 0


def test_progress_monitor_detects_stalled_span_growth() -> None:
    report = build_progress_report(gate(span=20.0), data_quality(span=20.0), health(), previous(span=20.0), stall_threshold_minutes=5.0)

    assert report["decision"] == "readiness_progress_stalled_no_span_growth"


def test_progress_monitor_detects_coverage_below_threshold() -> None:
    report = build_progress_report(gate(book_cov=90.0), data_quality(book_cov=90.0), health(), previous(book_cov=98.0))

    assert report["decision"] == "readiness_progress_coverage_below_threshold"
    assert report["checks"]["book_coverage_above_threshold"] is False


def test_progress_monitor_detects_time_window_met_but_not_sealed() -> None:
    report = build_progress_report(gate(span=168.0, remaining=0.0), data_quality(span=168.0), health(), previous(span=167.8, remaining=0.2))

    assert report["decision"] == "readiness_progress_time_window_met_but_not_sealed"


def test_progress_monitor_marks_snapshot_sealed() -> None:
    report = build_progress_report(
        gate(span=168.0, remaining=0.0, decision="microstructure_snapshot_sealed"),
        data_quality(span=168.0),
        health("cross_venue_microstructure_healthy_research_ready"),
        previous(span=167.8, remaining=0.2),
    )

    assert report["decision"] == "readiness_progress_snapshot_sealed"
    assert report["next_action"] == "handoff_to_research_runner"
