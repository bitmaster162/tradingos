from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.cex_funding_successor_admission_gate import build_report


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json").read_text(encoding="utf-8")
)
OBSERVED = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def predecessor_report(lock_id: str | None = None) -> dict:
    return {
        "lock_id": lock_id or LOCK["lock_id"],
        "decision": "cex_funding_source_alignment_terminal_data_quality_failure",
        "terminal": {"reached": True},
    }


def rows(start: datetime, minutes: int) -> tuple[list[dict], list[dict]]:
    aggregate: list[dict] = []
    direct: list[dict] = []
    for index in range(minutes + 1):
        bucket = int((start + timedelta(minutes=index)).timestamp() * 1000)
        aggregate_symbols = {}
        direct_symbols = {}
        for symbol in LOCK["symbols"]:
            aggregate_symbols[symbol] = {
                "BinPerp": {"funding_rate_per_hour": 0.00001},
                "BybitPerp": {"funding_rate_per_hour": 0.00002},
            }
            direct_symbols[symbol] = {
                "BinanceDirect": {"funding_rate_per_hour": 0.00001},
                "BybitDirect": {"funding_rate_per_hour": 0.00002},
            }
        aggregate.append({"minute_bucket_ms": bucket, "symbols": aggregate_symbols})
        direct.append({"minute_bucket_ms": bucket, "symbols": direct_symbols})
    return aggregate, direct


def test_admission_waits_for_full_clean_window() -> None:
    aggregate, direct = rows(OBSERVED - timedelta(hours=12), 12 * 60)

    report = build_report(
        LOCK,
        predecessor_report(),
        aggregate,
        direct,
        0,
        0,
        observed_at=OBSERVED,
    )

    assert report["decision"] == "cex_funding_successor_admission_waiting_clean_window"
    assert report["eligible_for_manual_successor_lock_review"] is False
    assert report["diagnostic_window"]["earliest_recheck_at_utc"] == "2026-07-17T00:00:00Z"
    assert report["runtime_boundary"]["price_outcomes_read"] is False
    assert report["can_trade"] is False


def test_admission_opens_only_manual_review_after_original_gates_pass() -> None:
    aggregate, direct = rows(OBSERVED - timedelta(hours=24), 24 * 60)

    report = build_report(
        LOCK,
        predecessor_report(),
        aggregate,
        direct,
        0,
        0,
        observed_at=OBSERVED,
    )

    assert report["decision"] == "cex_funding_successor_admission_ready_for_manual_lock_review"
    assert report["eligible_for_manual_successor_lock_review"] is True
    assert all(report["rolling_alignment"]["readiness_gates"].values())
    assert report["successor_policy"]["automatic_successor_creation_allowed"] is False
    assert report["runtime_boundary"]["successor_created"] is False


def test_admission_blocks_mismatched_terminal_provenance() -> None:
    aggregate, direct = rows(OBSERVED - timedelta(hours=24), 24 * 60)

    report = build_report(
        LOCK,
        predecessor_report("wrong-lock"),
        aggregate,
        direct,
        0,
        0,
        observed_at=OBSERVED,
    )

    assert report["decision"] == "cex_funding_successor_admission_blocked_contract"
    assert report["checks"]["predecessor_terminal_proven"] is False
    assert report["eligible_for_manual_successor_lock_review"] is False
