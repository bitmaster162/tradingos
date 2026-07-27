from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.cex_funding_freshness_watchdog import build_report


NOW = datetime(2026, 7, 13, 2, 0, 30, tzinfo=timezone.utc)


def contract() -> dict:
    return {
        "lock_id": "test",
        "health_contract": {
            "allowed_loop_statuses": ["running_collection_cycle", "sleeping"],
            "maximum_status_age_seconds": 180,
            "maximum_source_age_seconds": 180,
            "maximum_source_skew_minutes": 1,
            "recent_window_rows": 10,
            "maximum_recent_gap_minutes": 2,
            "require_pid_match": True,
            "require_zero_exit_codes": True,
            "fail_on_stderr_growth": True,
        },
        "runtime_boundary": {"orders_allowed": False, "can_trade": False},
    }


def status(**updates) -> dict:
    value = {
        "ts": "2026-07-13T02:00:00Z",
        "status": "sleeping",
        "pid": 123,
        "exit_code": 0,
        "primary_exit_code": 0,
        "direct_replication_exit_code": 0,
        "source_alignment_exit_code": 0,
        "cadence_policy": "anchored_start_to_start",
    }
    value.update(updates)
    return value


def rows(*minutes: int) -> list[dict]:
    base = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)
    result = []
    for minute in minutes:
        bucket = base + timedelta(minutes=minute)
        result.append(
            {
                "observed_at": (bucket + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "minute_bucket": bucket.isoformat().replace("+00:00", "Z"),
                "minute_bucket_ms": int(bucket.timestamp() * 1000),
            }
        )
    return result


def report(*, loop_status=None, aggregate=None, direct=None, stderr_size=0, previous=None, alive=True):
    return build_report(
        contract(),
        {"pid": 123},
        loop_status or status(),
        aggregate or rows(58, 59, 60),
        direct or rows(58, 59, 60),
        stderr_size,
        previous or {"stderr_size_bytes": stderr_size},
        NOW,
        pid_checker=lambda _pid: alive,
    )


def test_healthy_consecutive_sources_pass_fail_closed_watchdog() -> None:
    result = report(aggregate=rows(58, 59, 60), direct=rows(58, 59, 60))

    assert result["decision"] == "cex_funding_freshness_healthy"
    assert result["healthy"] is True
    assert result["sources"]["latest_bucket_skew_minutes"] == 0.0
    assert result["automatic_restart_attempted"] is False
    assert result["edge_evaluated"] is False
    assert result["can_trade"] is False


def test_dead_pid_fails_closed() -> None:
    result = report(alive=False)

    assert "pid_alive" in result["blockers"]
    assert result["can_trade"] is False


def test_stale_status_fails_closed() -> None:
    result = report(loop_status=status(ts="2026-07-13T01:50:00Z"))

    assert "status_fresh" in result["blockers"]


def test_direct_source_lag_is_reported_separately() -> None:
    result = report(aggregate=rows(58, 59, 60), direct=[{"observed_at": "2026-07-13T01:50:00Z", "minute_bucket": "2026-07-13T01:50:00Z", "minute_bucket_ms": 1783907400000}])

    assert "direct_source_fresh" in result["blockers"]
    assert "source_skew_within_limit" in result["blockers"]
    assert result["sources"]["aggregate"]["healthy"] is True
    assert result["sources"]["direct"]["healthy"] is False


def test_nonzero_collector_exit_fails_closed() -> None:
    result = report(loop_status=status(direct_replication_exit_code=1, exit_code=1))

    assert "zero_exit_codes" in result["blockers"]


def test_stderr_growth_fails_closed_without_restart() -> None:
    result = report(stderr_size=25, previous={"stderr_size_bytes": 10})

    assert "stderr_not_growing" in result["blockers"]
    assert result["stderr"]["growth_bytes"] == 15
    assert result["automatic_restart_attempted"] is False


def test_recent_gap_over_locked_limit_fails_source() -> None:
    result = report(aggregate=rows(55, 58, 59, 60), direct=rows(58, 59, 60))

    assert "aggregate_source_fresh" in result["blockers"]
    assert "aggregate_recent_gap_exceeded" in result["sources"]["aggregate"]["reasons"]
