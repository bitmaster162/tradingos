from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.bitunix_wo104_acceptance import (
    adjudicate_capture_manifest,
    adjudicate_edge_receipt,
    bind_sfp_detection,
    canonical_sha256,
    plan_entry_bound,
    validate_cohort,
    validate_crowd_records,
)


ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = ROOT / "HANDOFF" / "INCOMING" / "claude" / "20260713_bitunix_wo104_canonical"
POLICY = json.loads((ROOT / "configs" / "BITUNIX_WO104_INDEPENDENT_ACCEPTANCE_POLICY.json").read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(source_id: str, now: int, value: float = 0.1) -> dict:
    return {
        "source_id": source_id,
        "value": value,
        "observed_at": now - 1000,
        "received_at": now,
        "source_hash": hashlib.sha256(source_id.encode()).hexdigest(),
    }


def test_full_scope_is_bound_not_just_can_trade_and_orders(tmp_path: Path) -> None:
    clean = validate_cohort(PROPOSAL / "SETUP_A_PREREG_V3.json", POLICY)
    assert clean["decision"] == "bitunix_wo104_cohort_scope_bound"

    doc = json.loads((PROPOSAL / "SETUP_A_PREREG_V3.json").read_text(encoding="utf-8"))
    doc["scope"]["credentials_allowed"] = True
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(doc), encoding="utf-8")
    blocked = validate_cohort(tampered, POLICY)
    assert "full_scope_mismatch" in blocked["failures"]
    assert blocked["can_trade"] is False


def test_crowd_receipts_reject_future_empty_hash_and_accept_bound_quorum() -> None:
    cohort = validate_cohort(PROPOSAL / "SETUP_A_PREREG_V3.json", POLICY)
    now = 1_000_000_000_000
    valid = [source("funding_rate_8h", now), source("oi_delta_pct", now), source("cvd_norm", now)]
    assert validate_crowd_records(valid, cohort, now)["decision"] == "bitunix_wo104_crowd_receipts_bound"

    invalid = [dict(item) for item in valid]
    invalid[0]["source_hash"] = ""
    invalid[1]["received_at"] = now + 1
    report = validate_crowd_records(invalid, cohort, now)
    assert report["decision"] == "bitunix_wo104_crowd_receipts_blocked"
    assert any("source_hash_invalid" in item for item in report["failures"])
    assert any("received_in_future" in item for item in report["failures"])


def test_sfp_receipt_runs_bound_detector_on_no_future_bars() -> None:
    cohort = validate_cohort(PROPOSAL / "SETUP_A_PREREG_V3.json", POLICY)
    bars = [
        {"ts": 100, "high": 112, "low": 108, "close": 110},
        {"ts": 101, "high": 111, "low": 107, "close": 109},
        {"ts": 102, "high": 110, "low": 106, "close": 108},
        {"ts": 103, "high": 109, "low": 100, "close": 107},
        {"ts": 104, "high": 108, "low": 104, "close": 106},
        {"ts": 105, "high": 107, "low": 103, "close": 105},
        {"ts": 106, "high": 106, "low": 102, "close": 104},
        {"ts": 107, "high": 105, "low": 99, "close": 101},
    ]
    report = bind_sfp_detection(
        bars,
        as_of_ts=107,
        cohort=cohort,
        detector_path=PROPOSAL / "setup_a_gate.py",
    )
    assert report["decision"] == "bitunix_wo104_sfp_detector_receipt_bound"
    assert report["result"]["detected"] is True
    assert report["result"]["uses_future_bars"] is False


def test_unsorted_book_states_fail_closed() -> None:
    h = hashlib.sha256(b"book").hexdigest()
    report = plan_entry_bound(1000, [{"ts": 2000, "source_hash": h}, {"ts": 1500, "source_hash": h}], 250)
    assert report["decision"] == "bitunix_wo104_entry_hold"
    assert "book_states_non_monotonic" in report["failures"]


def test_edge_receipt_binds_identity_and_never_accepts() -> None:
    cohort = validate_cohort(PROPOSAL / "SETUP_A_PREREG_V3.json", POLICY)
    h = hashlib.sha256(b"x").hexdigest()
    receipt = {
        "cohort_binding_sha256": cohort["cohort_binding_sha256"],
        "evaluator_id": "independent_eval_v1",
        "evaluator_sha256": h,
        "cost_model_id": "shadow_cost_v1",
        "cost_model_sha256": h,
        "sample_size": 30,
        "data_start": "2026-07-13T00:00:00Z",
        "data_end": "2026-07-14T00:00:00Z",
        "source_manifest_sha256": h,
        "net_edge_R": 0.5,
    }
    report = adjudicate_edge_receipt(receipt, cohort, POLICY)
    assert report["decision"] == "bitunix_wo104_edge_receipt_evaluated_not_accepted"
    assert report["edge_evaluated"] is True
    assert report["edge_accepted"] is False
    assert report["promotion"] == "HOLD"


def capture_fixture(tmp_path: Path) -> tuple[Path, Path]:
    outputs = {}
    close_files = {}
    for name in POLICY["capture"]["required_output_files"]:
        path = tmp_path / name
        path.write_text(f"{name}\n", encoding="utf-8")
        digest = sha(path)
        outputs[name] = digest
        close_files[name] = {"close_ok": True, "fsync_ok": True, "sha256": digest}
    manifest = {
        "schema": "bitunix-public-capture-v4",
        "symbols": ["BTCUSDT"],
        "channels": ["depth_book15", "trade"],
        "duration_requested_s": 1800,
        "duration_actual_s": 1710,
        "frames_total": 100,
        "reconnects": 0,
        "reconnect_downtime_ms": 0,
        "unknown_schema_ledger": {},
        "future_skew_frames": 0,
        "out_of_order_total": 0,
        "stale_events": {},
        "max_depth_gap_ms": {"BTCUSDT": 1000},
        "max_recv_silence_ms": 1000,
        "final_depth_age_ms": {"BTCUSDT": 100},
        "final_trade_age_ms": {"BTCUSDT": 100},
        "subscription_acceptance": {
            "accepted": True,
            "covered": ["BTCUSDT:depth", "BTCUSDT:trade"],
            "missing": [],
        },
        "error_taxonomy": {"NETWORK": 0, "PARSER": 0, "LOCAL": 0, "STORAGE": 0},
        "credentials_used": 0,
        "private_calls": 0,
        "order_calls": 0,
        "terminal_hold": False,
        "hold": False,
        "receipts": {
            "streaming_output_sha256": outputs,
            "code_sha256": {
                "public_ws_venue.py": POLICY["proposal"]["parser_sha256"],
                "bitunix_public_capture.py": POLICY["proposal"]["capture_harness_sha256"],
            },
        },
        "remote_effect_permission": "PUBLIC_READ_ONLY_CAPTURE_ONLY",
        "can_trade": False,
    }
    manifest_path = tmp_path / "PUBLIC_CAPTURE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    close_path = tmp_path / "TRADINGOS_CLOSE_RECEIPTS.json"
    close_path.write_text(
        json.dumps(
            {
                "schema": "tradingos-bitunix-close-fsync-receipts-v1",
                "method": "wrapper_records_successful_return_from_writer_flush_fsync_close",
                "writer_newline_policy": "LF",
                "runner_sha256": sha(ROOT / "tools" / "bitunix_wo104_public_capture_runner.py"),
                "acceptance_sha256": sha(ROOT / "tools" / "bitunix_wo104_acceptance.py"),
                "policy_sha256": canonical_sha256(POLICY),
                "files": close_files,
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    return manifest_path, close_path


def test_capture_requires_independent_stale_gap_and_close_receipt_gates(tmp_path: Path) -> None:
    manifest_path, close_path = capture_fixture(tmp_path)
    clean = adjudicate_capture_manifest(manifest_path, close_path, POLICY)
    assert clean["decision"] == "bitunix_wo104_public_contract_confirmed_shadow_hold"
    assert clean["promotion"] == "HOLD"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stale_events"] = {"BTCUSDT": 1}
    manifest["max_depth_gap_ms"] = {"BTCUSDT": 20000}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    blocked = adjudicate_capture_manifest(manifest_path, close_path, POLICY)
    assert blocked["decision"] == "bitunix_wo104_capture_invalid_hold"
    assert "stale_events_nonzero" in blocked["failures"]
    assert "maximum_depth_gap_exceeded" in blocked["failures"]
    assert blocked["can_trade"] is False
