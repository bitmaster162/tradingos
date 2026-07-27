from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import bitunix_raw_event_replenishment_oracle as module


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "configs" / "BITUNIX_RAW_EVENT_REPLENISHMENT_PREREG_2026-07-16.json"


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iso(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def make_config(tmp_path: Path, floor_ms: int, *, terminal_one: bool = False) -> tuple[Path, dict]:
    config = copy.deepcopy(json.loads(BASE_CONFIG.read_text(encoding="utf-8")))
    config["created_at_utc"] = iso(floor_ms - 60_000)
    config["forward_floor_utc"] = iso(floor_ms)
    if terminal_one:
        config["terminal_gate"].update(
            {
                "minimum_resolved_events": 1,
                "minimum_distinct_utc_days": 1,
                "minimum_independent_4h_blocks": 1,
                "maximum_single_4h_block_event_share": 1.0,
            }
        )
        config["terminal_gate"]["pass_rules"]["minimum_positive_utc_days"] = 1
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, config


def depth_rows(best_bid: float, best_ask: float, total_depth: float) -> dict[str, list[list[str]]]:
    quantity = total_depth / 5.0
    return {
        "b": [[f"{best_bid - level * 0.01:.4f}", f"{quantity:.6f}"] for level in range(5)],
        "a": [[f"{best_ask + level * 0.01:.4f}", f"{quantity:.6f}"] for level in range(5)],
    }


def write_capture(
    run_dir: Path,
    *,
    floor_ms: int,
    direction: str = "LONG",
    start_before_floor: bool = False,
    burst_notional: float = 1000.0,
    imbalance: float = 1.0,
    impact_price_shift: float = 0.02,
    replenishment_ratio: float = 1.5,
    corrupt_raw_hash: bool = False,
    truncate_index: bool = False,
    invalid_depth: bool = False,
    favorable_exit: bool = True,
) -> None:
    run_dir.mkdir(parents=True)
    manifest_start = floor_ms - 1_000 if start_before_floor else floor_ms + 1_000
    first_bucket = ((floor_ms + 4_999) // 5_000) * 5_000 + 5_000
    trigger = first_bucket + 24 * 5_000
    final_ms = trigger + 130_000
    rows: list[tuple[int, int, dict, dict]] = []
    sequence = 0

    for recv_ms in range(first_bucket, final_ms + 1, 500):
        bid, ask, depth = 100.0, 100.1, 10.0
        if trigger + 500 <= recv_ms <= trigger + 4_500:
            if direction == "LONG":
                bid, ask = 100.0 - impact_price_shift, 100.1 - impact_price_shift
            else:
                bid, ask = 100.0 + impact_price_shift, 100.1 + impact_price_shift
            depth = 4.0
        elif recv_ms == trigger + 5_000:
            if direction == "LONG":
                bid, ask = 100.0 - impact_price_shift, 100.1 - impact_price_shift
            else:
                bid, ask = 100.0 + impact_price_shift, 100.1 + impact_price_shift
            depth = 4.0 * replenishment_ratio
        elif recv_ms > trigger + 5_000:
            if favorable_exit:
                bid, ask = (100.5, 100.6) if direction == "LONG" else (99.5, 99.6)
            else:
                bid, ask = (99.5, 99.6) if direction == "LONG" else (100.5, 100.6)
        data = depth_rows(bid, ask, depth)
        if invalid_depth and recv_ms == first_bucket:
            data["b"] = data["b"][:1]
        frame = {"ch": "depth_book15", "symbol": "BTCUSDT", "ts": recv_ms, "data": data}
        index = {
            "recv_ns": recv_ms * 1_000_000,
            "parse_kind": "DepthUpdate",
            "dup": False,
            "ch": "depth_book15",
            "symbol": "BTCUSDT",
            "venue_ts": recv_ms,
        }
        rows.append((recv_ms, sequence, frame, index))
        sequence += 1

    for number in range(24):
        recv_ms = first_bucket + number * 5_000 + 2_000
        for side in ("buy", "sell"):
            frame = {
                "ch": "trade",
                "symbol": "BTCUSDT",
                "ts": recv_ms,
                "data": [{"t": iso(recv_ms), "p": "100.0", "v": "0.5", "s": side}],
            }
            index = {
                "recv_ns": recv_ms * 1_000_000,
                "parse_kind": "TradeBatch",
                "dup": False,
                "ch": "trade",
                "symbol": "BTCUSDT",
                "venue_ts": recv_ms,
            }
            rows.append((recv_ms, sequence, frame, index))
            sequence += 1

    buy_notional = burst_notional * (1.0 + imbalance) / 2.0
    sell_notional = burst_notional - buy_notional
    recv_ms = trigger + 2_000
    for side, notional in (("buy", buy_notional), ("sell", sell_notional)):
        if notional <= 0:
            continue
        frame = {
            "ch": "trade",
            "symbol": "BTCUSDT",
            "ts": recv_ms,
            "data": [{"t": iso(recv_ms), "p": "100.0", "v": f"{notional / 100.0:.8f}", "s": side}],
        }
        index = {
            "recv_ns": recv_ms * 1_000_000,
            "parse_kind": "TradeBatch",
            "dup": False,
            "ch": "trade",
            "symbol": "BTCUSDT",
            "venue_ts": recv_ms,
        }
        rows.append((recv_ms, sequence, frame, index))
        sequence += 1

    raw_lines: list[str] = []
    index_lines: list[str] = []
    for _recv_ms, _sequence, frame, index in sorted(rows):
        raw = json.dumps(frame, separators=(",", ":"))
        index["sha256"] = module.sha256_text(raw)
        raw_lines.append(raw)
        index_lines.append(json.dumps(index, separators=(",", ":")))
    if corrupt_raw_hash:
        first = json.loads(index_lines[0])
        first["sha256"] = "0" * 64
        index_lines[0] = json.dumps(first, separators=(",", ":"))
    if truncate_index:
        index_lines.pop()

    raw_path = run_dir / "RAW_FRAMES.jsonl"
    index_path = run_dir / "RAW_FRAME_INDEX.jsonl"
    raw_path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8", newline="\n")
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8", newline="\n")
    stream_hashes = {path.name: sha_file(path) for path in (raw_path, index_path)}
    manifest = {
        "schema": "bitunix-public-capture-v4",
        "started_utc": iso(manifest_start),
        "ended_utc": iso(final_ms),
        "symbols": ["BTCUSDT"],
        "channels": ["depth_book15", "trade"],
        "hold": False,
        "terminal_hold": False,
        "credentials_used": 0,
        "private_calls": 0,
        "order_calls": 0,
        "error_taxonomy": {"NETWORK": 0, "PARSER": 0, "LOCAL": 0, "STORAGE": 0},
        "receipts": {"streaming_output_sha256": stream_hashes},
    }
    manifest_path = run_dir / "PUBLIC_CAPTURE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipts = {
        name: {
            "actual_sha256": value,
            "declared_sha256": value,
            "close_ok": True,
            "fsync_ok": True,
        }
        for name, value in stream_hashes.items()
    }
    acceptance = {
        "decision": module.ACCEPTED_CAPTURE_DECISION,
        "failures": [],
        "can_trade": False,
        "manifest_sha256": sha_file(manifest_path),
        "file_receipts": receipts,
    }
    (run_dir / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json").write_text(json.dumps(acceptance), encoding="utf-8")


def parsed_fixture(tmp_path: Path, **kwargs):
    floor = 2_000_000_000_000
    config_path, config = make_config(tmp_path, floor)
    run = tmp_path / "run"
    write_capture(run, floor_ms=floor, **kwargs)
    return config_path, config, run, module.parse_capture(run, config)


def test_prereg_is_independent_and_fail_closed() -> None:
    config = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    assert module.validate_config(config) == []
    assert config["independence_contract"]["candles_allowed"] is False
    assert config["independence_contract"]["prior_outcomes_admitted"] is False
    assert config["runtime_boundary"]["can_trade"] is False
    assert config["runtime_boundary"]["network_calls_allowed"] is False


def test_valid_raw_capture_passes_quality(tmp_path: Path) -> None:
    _path, _config, _run, capture = parsed_fixture(tmp_path)
    assert capture["quality_pass"] is True
    assert capture["edge_eligible"] is True
    assert capture["book_count"] > 100
    assert capture["trade_print_count"] == 49
    assert capture["maximum_book_gap_ms"] == 500


def test_pre_floor_capture_is_quality_only(tmp_path: Path) -> None:
    _path, config, _run, capture = parsed_fixture(tmp_path, start_before_floor=True)
    assert capture["quality_pass"] is True
    assert capture["edge_eligible"] is False
    assert module.detect_events(capture, config) == []


def test_raw_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    _path, _config, _run, capture = parsed_fixture(tmp_path, corrupt_raw_hash=True)
    assert capture["quality_pass"] is False
    assert any(row.startswith("raw_frame_hash_mismatch:") for row in capture["failures"])


def test_raw_index_line_count_mismatch_fails_closed(tmp_path: Path) -> None:
    _path, _config, _run, capture = parsed_fixture(tmp_path, truncate_index=True)
    assert capture["quality_pass"] is False
    assert "raw_index_line_count_mismatch" in capture["failures"]


def test_incomplete_top_five_book_fails_closed(tmp_path: Path) -> None:
    _path, _config, _run, capture = parsed_fixture(tmp_path, invalid_depth=True)
    assert capture["quality_pass"] is False
    assert any(row.startswith("depth_book_invalid:") for row in capture["failures"])


@pytest.mark.parametrize(("direction", "imbalance"), (("LONG", -1.0), ("SHORT", 1.0)))
def test_detects_both_reversal_directions(tmp_path: Path, direction: str, imbalance: float) -> None:
    _path, config, _run, capture = parsed_fixture(tmp_path, direction=direction, imbalance=imbalance)
    events = module.detect_events(capture, config)
    assert len(events) == 1
    assert events[0]["direction"] == direction
    assert events[0]["resolved"] is True
    assert events[0]["replenishment_ratio"] >= 1.25
    assert events[0]["directional_impact_bps"] >= 1.0


@pytest.mark.parametrize(
    "overrides",
    (
        {"burst_notional": 250.0},
        {"imbalance": 0.1},
        {"impact_price_shift": 0.005},
        {"replenishment_ratio": 1.1},
    ),
)
def test_frozen_trigger_rejects_weak_components(tmp_path: Path, overrides: dict) -> None:
    _path, config, _run, capture = parsed_fixture(tmp_path, **overrides)
    assert module.detect_events(capture, config) == []


def test_touch_prices_and_costs_are_applied(tmp_path: Path) -> None:
    _path, config, _run, capture = parsed_fixture(tmp_path, direction="LONG", imbalance=-1.0)
    event = module.detect_events(capture, config)[0]
    outcome = event["outcomes"]["120000"]
    assert event["entry_touch_price"] == pytest.approx(100.08)
    assert outcome["exit_touch_price"] == pytest.approx(100.5)
    assert outcome["net_base_bps"] == pytest.approx(outcome["gross_bps"] - 12.0)
    assert outcome["net_stress_bps"] == pytest.approx(outcome["gross_bps"] - 20.0)


def test_blind_report_hides_interim_outcomes(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    config_path, _config = make_config(tmp_path, floor)
    run = tmp_path / "run"
    write_capture(run, floor_ms=floor, direction="LONG", imbalance=-1.0)
    report = module.build_report(config_path, [run], mode="blind-forward")
    assert report["edge_rows_admitted"] == 1
    assert report["decision"] == "BLIND_FORWARD_WAIT_NO_INTERIM_OUTCOME_METRICS"
    assert report["outcome_metrics"] == {"visibility": "HIDDEN_UNTIL_TERMINAL_GATE"}
    assert "outcomes" not in report["events"][0]
    assert report["runtime_boundary"]["can_trade"] is False


def test_quality_only_never_computes_edge_rows(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    config_path, _config = make_config(tmp_path, floor)
    run = tmp_path / "run"
    write_capture(run, floor_ms=floor)
    report = module.build_report(config_path, [run], mode="quality-only")
    assert report["decision"] == "SCHEMA_QUALITY_PASS_EDGE_NOT_EVALUATED"
    assert report["edge_rows_admitted"] == 0
    assert report["outcome_metrics"]["visibility"] == "NOT_COMPUTED_IN_QUALITY_ONLY_MODE"


def test_terminal_gate_reveals_only_after_all_sample_checks(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    config_path, _config = make_config(tmp_path, floor, terminal_one=True)
    run = tmp_path / "run"
    write_capture(run, floor_ms=floor, direction="LONG", imbalance=-1.0, favorable_exit=True)
    report = module.build_report(config_path, [run], mode="blind-forward")
    assert report["terminal_gate"]["ready"] is True
    assert report["decision"] == "TERMINAL_PASS_REQUIRES_SEPARATE_REVIEW"
    assert report["outcome_metrics"]["resolved_events"] == 1
    assert report["outcome_metrics"]["mean_net_stress_bps"] > 0
    assert report["runtime_boundary"]["can_trade"] is False


def test_terminal_failure_is_tombstoned_not_reversed(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    config_path, _config = make_config(tmp_path, floor, terminal_one=True)
    run = tmp_path / "run"
    write_capture(run, floor_ms=floor, direction="LONG", imbalance=-1.0, favorable_exit=False)
    report = module.build_report(config_path, [run], mode="blind-forward")
    assert report["decision"] == "TERMINAL_FAIL_TOMBSTONE"
    assert report["outcome_metrics"]["mean_net_base_bps"] < 0


def test_source_files_are_not_mutated_by_evaluation(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    config_path, _config = make_config(tmp_path, floor)
    run = tmp_path / "run"
    write_capture(run, floor_ms=floor)
    before = {path.name: sha_file(path) for path in run.iterdir() if path.is_file()}
    module.build_report(config_path, [run], mode="blind-forward")
    after = {path.name: sha_file(path) for path in run.iterdir() if path.is_file()}
    assert after == before


def test_lock_hash_mismatch_blocks_evaluation(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    config_path, _config = make_config(tmp_path, floor)
    run = tmp_path / "run"
    write_capture(run, floor_ms=floor)
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "bindings": {"prereg_sha256": sha_file(config_path), "oracle_sha256": "0" * 64},
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    report = module.build_report(config_path, [run], mode="blind-forward", lock_path=lock)
    assert report["quality_pass"] is False
    assert "lock_binding_hash_mismatch:oracle" in report["lock_failures"]
    assert report["decision"] == "SOURCE_INTEGRITY_FAIL_NO_EDGE_ROWS"


def test_oracle_has_no_network_or_trading_imports() -> None:
    source = (ROOT / module.ORACLE_PATH).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint({"aiohttp", "requests", "socket", "urllib", "websockets", "web3", "ccxt"})
    assert "create_order" not in source
    assert "send_order" not in source


def test_cli_emits_fail_closed_quality_report(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    config_path, _config = make_config(tmp_path, floor)
    run = tmp_path / "run"
    write_capture(run, floor_ms=floor, start_before_floor=True)
    out = tmp_path / "report"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / module.ORACLE_PATH),
            "--config",
            str(config_path),
            "--run-dir",
            str(run),
            "--mode",
            "blind-forward",
            "--out-prefix",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    report = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["edge_rows_admitted"] == 0
    assert report["outcome_metrics"]["visibility"] == "HIDDEN_UNTIL_TERMINAL_GATE"
    assert report["runtime_boundary"]["can_trade"] is False
