from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from observer import build_event, read_json, resolve_outcomes, row_change


HERE = Path(__file__).resolve().parent


def prereg() -> dict:
    return read_json(HERE / "PREREG.json")


def make_rows(count: int = 190) -> list[dict]:
    rows = []
    for index in range(count):
        rows.append(
            {
                "collected_at": f"slot-{index}",
                "collected_at_ms": index * 300_000,
                "underlying_price": 100.0,
                "quality_pass": True,
                "near_expiry": {
                    "expiry_ms": 9_999_999_999_999,
                    "dte": 14.0,
                    "atm_iv_pct": 30.0 + (index % 5) * 0.02,
                    "moneyness_skew_proxy_pp": 5.0 + (index % 7) * 0.02,
                    "two_sided_quote_coverage": 1.0,
                },
                "can_trade": False,
            }
        )
    return rows


def make_trigger(rows: list[dict], index: int) -> None:
    rows[index]["underlying_price"] = 99.5
    rows[index]["near_expiry"]["atm_iv_pct"] = 32.0
    rows[index]["near_expiry"]["moneyness_skew_proxy_pp"] = 8.0


def test_expiry_roll_blocks_change() -> None:
    rows = make_rows()
    config = prereg()
    index = 180
    rows[index]["near_expiry"]["expiry_ms"] += 1
    times = [row["collected_at_ms"] for row in rows]

    assert row_change(rows, times, index, config) is None


def test_event_requires_full_registered_confluence() -> None:
    rows = make_rows()
    config = prereg()
    index = 180
    make_trigger(rows, index)

    event = build_event(rows, index, config)

    assert event is not None
    assert event["direction"] == "SHORT"
    assert event["features"]["skew_change_z"] >= config["features"]["skew_change_z_min"]
    assert event["features"]["atm_iv_change_z"] >= config["features"]["atm_iv_change_z_min"]
    assert event["features"]["underlying_return_bps"] <= config["features"]["underlying_return_1h_max_bps"]

    rows[index]["underlying_price"] = 100.0
    assert build_event(rows, index, config) is None


def test_future_rows_do_not_change_event_features() -> None:
    rows = make_rows()
    config = prereg()
    index = 180
    make_trigger(rows, index)
    before = build_event(rows, index, config)
    mutated = deepcopy(rows)
    for row in mutated[index + 1 :]:
        row["underlying_price"] = 1_000_000.0
        row["near_expiry"]["atm_iv_pct"] = 1_000.0
        row["near_expiry"]["moneyness_skew_proxy_pp"] = 1_000.0

    after = build_event(mutated, index, config)

    assert before is not None and after is not None
    assert before["features"] == after["features"]


def test_outcome_uses_next_snapshot_and_registered_costs(tmp_path: Path) -> None:
    rows = make_rows(20)
    rows[1]["underlying_price"] = 100.0
    rows[13]["underlying_price"] = 90.0
    events_path = tmp_path / "events.jsonl"
    outcomes_path = tmp_path / "outcomes.jsonl"
    events_path.write_text(
        json.dumps({"event_id": "event-1", "event_snapshot_ms": 0, "direction": "SHORT", "can_trade": False}) + "\n",
        encoding="utf-8",
    )
    config = prereg()
    config["outcomes"]["horizons_minutes"] = [60]

    added = resolve_outcomes(rows, events_path, outcomes_path, config)
    outcome = json.loads(outcomes_path.read_text(encoding="utf-8").strip())

    assert added == 1
    assert outcome["entry_snapshot_ms"] == rows[1]["collected_at_ms"]
    assert outcome["exit_snapshot_ms"] == rows[13]["collected_at_ms"]
    assert outcome["gross_bps"] == 1000.0
    assert outcome["net_base_bps"] == 980.0
    assert outcome["net_stress_bps"] == 970.0
    assert outcome["can_trade"] is False
