from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator as v1
from tools import bitunix_wo105_causal_shadow_evaluator_v2 as module


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json").read_text(encoding="utf-8")
)
V1_LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_2026-07-14.json").read_text(encoding="utf-8")
)


def load_v1_test_helpers():
    path = ROOT / "tests" / "test_bitunix_wo105_causal_shadow_evaluator.py"
    spec = importlib.util.spec_from_file_location("_wo105_v1_test_helpers", path)
    assert spec and spec.loader
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    return helpers


def v2_packet() -> dict:
    helpers = load_v1_test_helpers()
    packet = helpers.build_packet()
    packet["cohort_id"] = LOCK["cohort_id"]
    signal_close = packet["signal_bars"][-1]["payload"]["close_ms"]
    entry_received = signal_close + LOCK["params"]["entry"]["latency_ms"] + 20
    entry_book = helpers.book(entry_received - 10, entry_received, 109.4, 109.6, "v2-entry-book")
    activation = entry_received + LOCK["params"]["entry"]["maker_order_ttl_ms"]
    outcome_close = activation + 5 * helpers.MINUTE
    exit_received = outcome_close + LOCK["params"]["exit"]["latency_ms"] + 20
    exit_book = helpers.book(exit_received - 10, exit_received, 106.2, 106.4, "v2-exit-book")
    trade_payload = {"price": 109.6, "size": 2.0, "side": "buy"}
    packet["books"] = [entry_book, exit_book]
    packet["trades"] = [
        helpers.record(
            "public-trade-v1",
            trade_payload,
            entry_received + 100,
            entry_received + 110,
            "v2-fill-trade",
        )
    ]
    packet["outcome_bars"] = [helpers.bar(outcome_close, 108.0, 108.2, 106.0, 106.5, "v2-outcome-target")]
    packet["evaluation_at"] = exit_received + 1000
    units = {
        "funding_rate_8h": "decimal_fraction",
        "oi_delta_pct": "percent_change",
        "cvd_norm": "signed_volume_share",
    }
    for row in packet["crowd"]:
        kind = row["payload"]["kind"]
        row["payload"]["unit"] = units[kind]
        if kind == "funding_rate_8h":
            row["payload"].update(
                {
                    "raw_unit": "percentage_points",
                    "normalization_rule": "api_percentage_points_divide_by_100",
                }
            )
        if kind == "cvd_norm":
            row["payload"]["method"] = "sum(buy_size-sell_size)/sum(size)"
        row["source_hash"] = module.canonical_sha256(row["payload"])
    setup = module.detect_setup(packet["signal_bars"], LOCK["params"])
    assert setup is not None
    selected = module.select_entry_book(packet["books"], setup["signal_close_ms"], LOCK["params"])
    assert selected is not None
    packet["source_manifest_sha256"] = module.pre_entry_manifest(packet, setup, selected)
    return packet


def test_v2_lock_binds_tombstone_collectors_and_evaluators() -> None:
    assert module.validate_lock(LOCK) == []


def test_v1_remains_byte_bound_and_valid_as_historical_tombstoned_contract() -> None:
    assert v1.validate_lock(V1_LOCK) == []
    tombstone = json.loads(
        (ROOT / "docs" / "BITUNIX_WO105_V1_PRE_FLOOR_UNIT_TOMBSTONE_2026-07-14.json").read_text(encoding="utf-8")
    )
    assert tombstone["original_lock_sha256"] == v1.sha256_file(
        ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_2026-07-14.json"
    )
    assert tombstone["events_observed"] == 0


def test_v2_accepts_explicit_units_and_preserves_shadow_only_result() -> None:
    report = module.evaluate_packet(v2_packet(), LOCK)

    assert report["state"] == "SHADOW_CLOSED"
    assert report["evaluator_contract"] == "WO105_V2_EXPLICIT_SOURCE_UNITS"
    assert report["can_trade"] is False


def test_missing_or_wrong_units_fail_closed_before_base_evaluation() -> None:
    packet = v2_packet()
    packet["crowd"][0]["payload"]["unit"] = "percentage_points"
    packet["crowd"][0]["source_hash"] = module.canonical_sha256(packet["crowd"][0]["payload"])

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "CAPTURE_INVALID"
    assert report["decision"] == "bitunix_wo105_v2_hold_lock_or_unit_contract_invalid"
    assert any("unit_mismatch:funding_rate_8h" in failure for failure in report["failures"])


def test_unregistered_crowd_kind_cannot_pad_quorum() -> None:
    packet = v2_packet()
    packet["crowd"][2]["payload"]["kind"] = "invented_source"
    packet["crowd"][2]["source_hash"] = module.canonical_sha256(packet["crowd"][2]["payload"])

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "CAPTURE_INVALID"
    assert any("kind_not_preregistered" in failure for failure in report["failures"])


def test_signal_bar_received_after_frozen_latency_fails_closed() -> None:
    packet = v2_packet()
    signal_close = packet["signal_bars"][-1]["payload"]["close_ms"]
    packet["signal_bars"][-1]["received_at"] = signal_close + LOCK["params"]["entry"]["latency_ms"] + 1

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "CAPTURE_INVALID"
    assert "signal_bar_not_available_by_entry_cutoff" in report["failures"]
