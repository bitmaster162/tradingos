from __future__ import annotations

from tools.cross_venue_microstructure_storage_guard import DEGRADED, OK, evaluate_storage_guard


def locked_policy(tmp_path):
    return {
        "status": "locked",
        "target_hours": 168,
        "min_free_bytes_hard": 1,
        "min_free_pct_hard": 0.0,
        "max_authoritative_bytes_hard": 10_000_000,
        "max_estimated_target_bytes_hard": 10_000_000,
        "warn_free_bytes": 1,
        "warn_free_pct": 0.0,
        "warn_estimated_target_bytes": 10_000_000,
        "source_cache_relative": str(tmp_path),
    }


def test_storage_guard_passes_with_bounded_sqlite_growth(tmp_path) -> None:
    source = tmp_path / "micro"
    source.mkdir()
    (source / "microstructure.sqlite3").write_bytes(b"x" * 100)
    (source / "minute_features_v2.csv").write_text("minute_ms\n1\n", encoding="utf-8")
    report = {"coverage": {"span_hours": 10}}

    result = evaluate_storage_guard(source_dir=source, report=report, policy=locked_policy(source))

    assert result["classification"] == OK
    assert result["failed_hard_gates"] == []
    assert result["observed"]["estimated_target_bytes"] > result["observed"]["authoritative_bytes"]
    assert result["can_trade"] is False


def test_storage_guard_fails_when_projected_archive_exceeds_hard_ceiling(tmp_path) -> None:
    source = tmp_path / "micro"
    source.mkdir()
    (source / "microstructure.sqlite3").write_bytes(b"x" * 1000)
    policy = locked_policy(source)
    policy["max_estimated_target_bytes_hard"] = 2000
    report = {"coverage": {"span_hours": 1}}

    result = evaluate_storage_guard(source_dir=source, report=report, policy=policy)

    assert result["classification"] == DEGRADED
    assert "estimated_target_bytes_below_hard_ceiling" in result["failed_hard_gates"]
