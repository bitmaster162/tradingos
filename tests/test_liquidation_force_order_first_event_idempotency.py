from __future__ import annotations

import json
from types import SimpleNamespace

from tools.liquidation_force_order_first_event_auto_run_guard import build_report, event_identity


def write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_first_event_identity_does_not_change_when_sample_grows() -> None:
    first = {"events": {"first_event_time": "2026-07-12T03:26:03Z", "last_event_time": "2026-07-12T03:26:20Z", "events": 8}}
    later = {"events": {"first_event_time": "2026-07-12T03:26:03Z", "last_event_time": "2026-07-12T03:30:00Z", "events": 100}}

    assert event_identity(first) == event_identity(later) == "2026-07-12T03:26:03Z"


def test_successful_first_event_run_is_once_ever_even_with_legacy_identity(tmp_path) -> None:
    dq_prefix = tmp_path / "dq"
    state_path = tmp_path / "state.json"
    pipeline_prefix = tmp_path / "pipeline"
    out_prefix = tmp_path / "guard"
    write_json(
        dq_prefix.with_suffix(".json"),
        {
            "hard_failures": [],
            "events": {
                "events": 78,
                "first_event_time": "2026-07-12T03:26:03Z",
                "last_event_time": "2026-07-12T03:29:00Z",
            },
        },
    )
    write_json(
        state_path,
        {
            "first_pipeline_event_identity": "2026-07-12T03:26:03Z|2026-07-12T03:26:20Z|8",
            "pipeline_ran": True,
            "pipeline_output": str(pipeline_prefix.with_suffix(".json")),
        },
    )
    args = SimpleNamespace(
        data_quality_prefix=str(dq_prefix),
        state_path=str(state_path),
        out_prefix=str(out_prefix),
        pipeline_out_prefix=str(pipeline_prefix),
        run_data_quality=False,
        data_dir=str(tmp_path / "events"),
        min_events_for_research=500,
        timeout_seconds=10,
        symbols="ALL",
        interval="1h",
        horizons="1,2,4,8",
        min_event_bars_for_research=50,
        min_context_bars=10,
    )

    report = build_report(args)

    assert report["decision"] == "first_event_auto_run_guard_already_ran"
    assert report["pipeline_ran"] is True
    assert report["pipeline_run"] is None
    assert report["events"] == 78
    assert report["can_trade"] is False
