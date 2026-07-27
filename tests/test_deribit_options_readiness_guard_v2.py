from __future__ import annotations

import json
from pathlib import Path

from tools import deribit_options_readiness_guard_v2 as readiness


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads(
    (ROOT / "configs" / "DERIBIT_OPTIONS_READINESS_GUARD_V2.json").read_text(encoding="utf-8")
)


def healthy_row(timestamp_ms: int) -> dict:
    return {
        "collected_at_ms": timestamp_ms,
        "quality_pass": True,
        "quality": {
            "join_rate": 1.0,
            "mark_iv_coverage": 1.0,
            "open_interest_coverage": 1.0,
            "distinct_expiries": 5,
        },
        "can_trade": False,
    }


def clean_week(start_ms: int) -> list[dict]:
    interval_ms = 300_000
    return [healthy_row(start_ms + index * interval_ms) for index in range(2017)]


def test_clean_seven_day_cohort_passes_original_readiness_gates() -> None:
    rows = clean_week(1_800_000_000_000)
    report = readiness.evaluate(
        rows,
        len(rows),
        [],
        0,
        rows[-1]["collected_at_ms"] + 1_000,
        CONFIG,
        True,
    )

    assert report["decision"] == "deribit_options_v3_ready_for_observer_review"
    assert report["research_gate_ready"] is True
    assert report["metrics"]["span_days"] == 7.0
    assert report["metrics"]["scheduled_coverage"] == 1.0
    assert all(report["checks"].values())
    assert report["can_trade"] is False


def test_post_floor_gap_above_fifteen_minutes_is_terminal_for_that_cohort() -> None:
    rows = clean_week(1_800_000_000_000)
    del rows[100:104]
    report = readiness.evaluate(
        rows,
        len(rows),
        [],
        0,
        rows[-1]["collected_at_ms"] + 1_000,
        CONFIG,
        True,
    )

    assert report["metrics"]["maximum_gap_seconds"] == 1500.0
    assert report["checks"]["maximum_gap"] is False
    assert report["research_gate_ready"] is False
    assert report["can_trade"] is False


def test_pre_floor_rows_are_physically_excluded(tmp_path: Path) -> None:
    floor_ms = readiness.parse_iso_ms(CONFIG["forward_floor_utc"])
    assert floor_ms is not None
    path = tmp_path / "surface_metrics.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (healthy_row(floor_ms - 300_000), healthy_row(floor_ms), healthy_row(floor_ms + 300_000))
        )
        + "\n",
        encoding="utf-8",
    )

    rows, bad_lines = readiness.read_forward_jsonl(path, floor_ms)

    assert bad_lines == 0
    assert [row["collected_at_ms"] for row in rows] == [floor_ms, floor_ms + 300_000]
