from __future__ import annotations

import copy
import json
from pathlib import Path

from tools import bitunix_wo105_causal_shadow_evaluator as module


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_2026-07-14.json").read_text(encoding="utf-8")
)
HOUR = 3_600_000
MINUTE = 60_000


def record(schema: str, payload: dict, observed: int, received: int, source_id: str) -> dict:
    return {
        "source_id": source_id,
        "observed_at": observed,
        "received_at": received,
        "source_hash": module.canonical_sha256(payload),
        "schema_version": schema,
        "payload": payload,
    }


def bar(close_ms: int, open_: float, high: float, low: float, close: float, source_id: str) -> dict:
    payload = {"close_ms": close_ms, "open": open_, "high": high, "low": low, "close": close}
    return record("ohlcv-bar-v1", payload, close_ms, close_ms + 10, source_id)


def book(observed: int, received: int, bid: float, ask: float, source_id: str) -> dict:
    payload = {
        "bids": [[bid, 5.0], [round(bid - 0.1, 1), 5.0]],
        "asks": [[ask, 5.0], [round(ask + 0.1, 1), 5.0]],
    }
    return record("public-book-v1", payload, observed, received, source_id)


def build_packet(*, include_fill: bool = True, include_outcome: bool = True) -> dict:
    floor = module.parse_iso_ms(LOCK["forward_start_at"])
    assert floor is not None
    signal_close = floor + 24 * HOUR
    signal_bars = []
    for index in range(19):
        close_ms = signal_close - (23 - index) * HOUR
        high = 100.0 + index * 0.1
        signal_bars.append(bar(close_ms, 99.0, high, 95.0, 99.0, f"signal-{index}"))
    signal_bars.extend(
        [
            bar(signal_close - 4 * HOUR, 105.0, 110.0, 104.0, 105.0, "pivot"),
            bar(signal_close - 3 * HOUR, 106.0, 109.0, 104.5, 106.0, "confirm-1"),
            bar(signal_close - 2 * HOUR, 106.0, 108.0, 104.5, 106.0, "confirm-2"),
            bar(signal_close - HOUR, 109.0, 111.0, 108.5, 110.5, "sweep"),
            bar(signal_close, 110.0, 110.5, 108.0, 109.5, "reclaim"),
        ]
    )

    htf_bars = []
    htf_latest = signal_close
    for index in range(203):
        close_ms = htf_latest - (202 - index) * 4 * HOUR
        close = 220.0 - index * 0.5
        htf_bars.append(bar(close_ms, close + 0.2, close + 1.0, close - 1.0, close, f"htf-{index}"))

    crowd = []
    for index, kind in enumerate(("funding_rate_8h", "oi_delta_pct", "cvd_norm")):
        payload = {"kind": kind, "value": 0.0}
        crowd.append(record("crowd-point-v1", payload, signal_close - 1000 + index, signal_close + 100 + index, kind))

    entry_book = book(signal_close + 290, signal_close + 300, 109.4, 109.6, "entry-book")
    activation = signal_close + 300 + 15 * MINUTE
    outcome_close = activation + 5 * MINUTE
    exit_book = book(outcome_close + 290, outcome_close + 300, 106.2, 106.4, "exit-book")
    books = [entry_book, exit_book]

    trades = []
    if include_fill:
        payload = {"price": 109.6, "size": 2.0, "side": "buy"}
        trades.append(record("public-trade-v1", payload, signal_close + 1000, signal_close + 1010, "fill-trade"))

    outcome_bars = []
    if include_outcome:
        outcome_bars.append(bar(outcome_close, 108.0, 108.2, 106.0, 106.5, "outcome-target"))

    packet = {
        "schema": "bitunix-wo105-causal-shadow-input-v1",
        "cohort_id": LOCK["cohort_id"],
        "symbol": "BTCUSDT",
        "evaluation_at": outcome_close + 1000,
        "source_manifest_sha256": "",
        "signal_bars": signal_bars,
        "htf_bars": htf_bars,
        "crowd": crowd,
        "books": books,
        "trades": trades,
        "outcome_bars": outcome_bars,
        "funding_events": [],
    }
    setup = module.detect_setup(signal_bars, LOCK["params"])
    assert setup is not None
    selected_book = module.select_entry_book(books, setup["signal_close_ms"], LOCK["params"])
    assert selected_book is not None
    packet["source_manifest_sha256"] = module.pre_entry_manifest(packet, setup, selected_book)
    return packet


def rehash(row: dict) -> None:
    row["source_hash"] = module.canonical_sha256(row["payload"])


def test_lock_is_bound_to_replay_v3_and_current_evaluator() -> None:
    assert module.validate_lock(LOCK) == []


def test_closed_shadow_event_is_computed_from_raw_inputs() -> None:
    report = module.evaluate_packet(build_packet(), LOCK)

    assert report["state"] == "SHADOW_CLOSED"
    assert report["decision"] == "bitunix_wo105_shadow_event_closed_not_edge_evaluated"
    assert report["details"]["setup"]["direction"] == "SHORT"
    assert report["details"]["htf"]["verdict"] == "down_strong"
    assert report["details"]["fill"]["fill_fraction"] == 1.0
    assert report["details"]["outcome"]["net_r"] > 0
    assert report["edge_evaluated"] is False
    assert report["can_trade"] is False


def test_missing_fill_becomes_terminal_no_fill_only_after_frozen_ttl() -> None:
    packet = build_packet(include_fill=False, include_outcome=False)
    packet["books"] = packet["books"][:1]
    packet["evaluation_at"] = packet["books"][0]["received_at"] + LOCK["params"]["entry"]["maker_order_ttl_ms"] + 1

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "NO_FILL"
    assert report["decision"] == "bitunix_wo105_terminal_no_conservative_maker_fill"
    assert report["can_trade"] is False


def test_filled_position_stays_open_without_closed_outcome_data() -> None:
    packet = build_packet(include_outcome=False)
    packet["books"] = packet["books"][:1]
    packet["evaluation_at"] = packet["books"][0]["received_at"] + LOCK["params"]["entry"]["maker_order_ttl_ms"] + 1000

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "SHADOW_OPEN"
    assert "outcome" not in report["details"]
    assert report["edge_evaluated"] is False


def test_future_receipt_and_source_hash_mismatch_fail_closed() -> None:
    packet = build_packet()
    packet["crowd"][0]["received_at"] = packet["evaluation_at"] + 1
    packet["crowd"][1]["source_hash"] = "0" * 64

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "CAPTURE_INVALID"
    assert any("future_input" in failure for failure in report["failures"])
    assert any("source_hash_mismatch" in failure for failure in report["failures"])


def test_caller_cannot_replace_raw_setup_with_a_boolean() -> None:
    packet = build_packet()
    packet["setup"] = True
    packet["signal_bars"][-1]["payload"]["close"] = 110.2
    rehash(packet["signal_bars"][-1])

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "NO_SETUP"
    assert report["decision"] == "bitunix_wo105_no_causal_sfp_setup"


def test_source_manifest_and_terminal_duplicate_are_blocked() -> None:
    packet = build_packet()
    mismatched = copy.deepcopy(packet)
    mismatched["source_manifest_sha256"] = "f" * 64
    invalid = module.evaluate_packet(mismatched, LOCK)
    assert invalid["state"] == "CAPTURE_INVALID"
    assert "source_manifest_sha256_mismatch" in invalid["failures"]

    closed = module.evaluate_packet(packet, LOCK)
    previous = {
        closed["event_id"]: {
            "state": "SHADOW_CLOSED",
            "cohort_binding_sha256": LOCK["parameter_cohort_sha256"],
        }
    }
    replay = module.evaluate_packet(packet, LOCK, previous_events=previous)
    assert replay["state"] == "CAPTURE_INVALID"
    assert replay["decision"] == "bitunix_wo105_hold_duplicate_terminal_event"


def test_pre_floor_signal_is_rejected_as_backfill() -> None:
    packet = build_packet()
    shift = 48 * HOUR
    for series in ("signal_bars", "htf_bars", "crowd", "books", "trades", "outcome_bars"):
        for row in packet[series]:
            row["observed_at"] -= shift
            row["received_at"] -= shift
            if "close_ms" in row["payload"]:
                row["payload"]["close_ms"] -= shift
            rehash(row)
    packet["evaluation_at"] -= shift
    setup = module.detect_setup(packet["signal_bars"], LOCK["params"])
    assert setup is not None
    selected_book = module.select_entry_book(packet["books"], setup["signal_close_ms"], LOCK["params"])
    assert selected_book is not None
    packet["source_manifest_sha256"] = module.pre_entry_manifest(packet, setup, selected_book)

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "CAPTURE_INVALID"
    assert report["decision"] == "bitunix_wo105_hold_pre_floor_backfill"


def test_stale_crowd_quorum_is_input_hold_not_market_veto() -> None:
    packet = build_packet()
    signal_close = packet["signal_bars"][-1]["payload"]["close_ms"]
    for index, row in enumerate(packet["crowd"]):
        row["observed_at"] = signal_close - 9 * HOUR + index

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "CAPTURE_INVALID"
    assert report["decision"] == "bitunix_wo105_hold_crowd_or_funding_input_stale_or_missing"
    assert "fresh_crowd_funding_quorum_not_met" in report["failures"]


def test_reordered_and_nonfinite_inputs_fail_closed() -> None:
    reordered = build_packet()
    reordered["signal_bars"][1], reordered["signal_bars"][2] = reordered["signal_bars"][2], reordered["signal_bars"][1]
    report = module.evaluate_packet(reordered, LOCK)
    assert report["state"] == "CAPTURE_INVALID"
    assert "signal_bars:event_time_reordered_or_duplicate" in report["failures"]

    nonfinite = build_packet()
    nonfinite["trades"][0]["payload"]["price"] = float("nan")
    rehash(nonfinite["trades"][0])
    report = module.evaluate_packet(nonfinite, LOCK)
    assert report["state"] == "CAPTURE_INVALID"
    assert any("trade_numeric_invalid" in failure for failure in report["failures"])


def test_matured_position_without_complete_outcome_bars_fails_closed() -> None:
    packet = build_packet(include_outcome=False)
    packet["books"] = packet["books"][:1]
    activation = packet["books"][0]["received_at"] + LOCK["params"]["entry"]["maker_order_ttl_ms"]
    packet["evaluation_at"] = activation + LOCK["params"]["outcome"]["max_holding_ms"] + 1

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "CAPTURE_INVALID"
    assert report["decision"] == "bitunix_wo105_hold_matured_outcome_data_missing"


def test_crossed_funding_boundary_requires_exact_causal_receipt() -> None:
    packet = build_packet()
    entry_book = packet["books"][0]
    activation = entry_book["received_at"] + LOCK["params"]["entry"]["maker_order_ttl_ms"]
    interval = LOCK["params"]["funding_treatment"]["interval_h"] * HOUR
    funding_boundary = ((activation // interval) + 1) * interval
    outcome_close = funding_boundary + 5 * MINUTE
    packet["outcome_bars"] = [bar(outcome_close, 108.0, 108.2, 106.0, 106.5, "late-target")]
    packet["books"] = [entry_book, book(outcome_close + 290, outcome_close + 300, 106.2, 106.4, "late-exit-book")]
    packet["evaluation_at"] = outcome_close + 1000

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "CAPTURE_INVALID"
    assert report["decision"] == "bitunix_wo105_hold_funding_receipt_missing"
    assert any(str(funding_boundary) in failure for failure in report["failures"])


def test_partial_fill_is_preserved_instead_of_promoted_to_full_fill() -> None:
    packet = build_packet()
    packet["trades"][0]["payload"]["size"] = 0.5
    rehash(packet["trades"][0])

    report = module.evaluate_packet(packet, LOCK)

    assert report["state"] == "SHADOW_CLOSED"
    assert 0 < report["details"]["fill"]["fill_fraction"] < 1
    assert report["details"]["outcome"]["filled_qty"] == 0.5
