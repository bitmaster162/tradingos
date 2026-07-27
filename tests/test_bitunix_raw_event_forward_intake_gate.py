from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools import bitunix_raw_event_forward_intake_gate as gate
from tools import bitunix_raw_event_replenishment_oracle as oracle


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / gate.DEFAULT_CONFIG
LOCK_PATH = ROOT / gate.DEFAULT_LOCK


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def top_five() -> dict[str, list[list[str]]]:
    return {
        "b": [[f"{100.0 - level * 0.01:.2f}", "1.0"] for level in range(5)],
        "a": [[f"{100.1 + level * 0.01:.2f}", "1.0"] for level in range(5)],
    }


def write_minimal_capture(
    run_dir: Path,
    *,
    started_ms: int,
    corrupt_hash: bool = False,
    malformed_manifest: bool = False,
) -> None:
    run_dir.mkdir(parents=True)
    depth = {
        "ch": "depth_book15",
        "symbol": "BTCUSDT",
        "ts": started_ms + 1000,
        "data": top_five(),
    }
    trade = {
        "ch": "trade",
        "symbol": "BTCUSDT",
        "ts": started_ms + 1200,
        "data": [{"t": iso(started_ms + 1200), "p": "100.0", "v": "0.1", "s": "buy"}],
    }
    frames = [depth, trade]
    kinds = ["DepthUpdate", "TradeBatch"]
    channels = ["depth_book15", "trade"]
    raw_lines: list[str] = []
    index_lines: list[str] = []
    for number, (frame, kind, channel) in enumerate(zip(frames, kinds, channels), start=1):
        raw = json.dumps(frame, separators=(",", ":"))
        raw_lines.append(raw)
        index_lines.append(
            json.dumps(
                {
                    "recv_ns": (started_ms + 800 + number * 200) * 1_000_000,
                    "sha256": "0" * 64 if corrupt_hash and number == 1 else oracle.sha256_text(raw),
                    "parse_kind": kind,
                    "dup": False,
                    "ch": channel,
                    "symbol": "BTCUSDT",
                    "venue_ts": frame["ts"],
                },
                separators=(",", ":"),
            )
        )
    raw_path = run_dir / "RAW_FRAMES.jsonl"
    index_path = run_dir / "RAW_FRAME_INDEX.jsonl"
    raw_path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8", newline="\n")
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8", newline="\n")
    hashes = {path.name: sha_file(path) for path in (raw_path, index_path)}
    manifest_path = run_dir / "PUBLIC_CAPTURE_MANIFEST.json"
    if malformed_manifest:
        manifest_path.write_text("{bad", encoding="utf-8")
        return
    manifest = {
        "schema": "bitunix-public-capture-v4",
        "started_utc": iso(started_ms),
        "ended_utc": iso(started_ms + 2_000),
        "symbols": ["BTCUSDT"],
        "channels": ["depth_book15", "trade"],
        "hold": False,
        "terminal_hold": False,
        "credentials_used": 0,
        "private_calls": 0,
        "order_calls": 0,
        "error_taxonomy": {"NETWORK": 0, "PARSER": 0, "LOCAL": 0, "STORAGE": 0},
        "receipts": {"streaming_output_sha256": hashes},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipts = {
        name: {
            "actual_sha256": value,
            "declared_sha256": value,
            "close_ok": True,
            "fsync_ok": True,
        }
        for name, value in hashes.items()
    }
    acceptance = {
        "decision": oracle.ACCEPTED_CAPTURE_DECISION,
        "failures": [],
        "can_trade": False,
        "manifest_sha256": sha_file(manifest_path),
        "file_receipts": receipts,
    }
    (run_dir / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json").write_text(json.dumps(acceptance), encoding="utf-8")


def floor_ms() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    value = oracle.parse_iso_ms(config["forward_floor_utc"])
    assert value is not None
    return value


def test_no_completed_post_floor_capture_returns_wait(tmp_path: Path) -> None:
    report = gate.build_report(config_path=CONFIG_PATH, lock_path=LOCK_PATH, capture_root=tmp_path)
    assert report["decision"] == "WAIT_NO_COMPLETED_POST_FLOOR_CAPTURE"
    assert report["selected_run_count"] == 0
    assert report["edge_rows_admitted"] == 0
    assert report["outcome_visibility"] == "HIDDEN_UNTIL_TERMINAL_GATE"
    assert report["can_trade"] is False


def test_pre_floor_and_in_progress_runs_are_excluded(tmp_path: Path) -> None:
    write_minimal_capture(tmp_path / "run_pre", started_ms=floor_ms() - 1)
    (tmp_path / "run_active").mkdir()
    report = gate.build_report(config_path=CONFIG_PATH, lock_path=LOCK_PATH, capture_root=tmp_path)
    assert report["selected_run_count"] == 0
    assert len(report["discovery"]["pre_floor"]) == 1
    assert len(report["discovery"]["in_progress"]) == 1


def test_completed_post_floor_capture_is_selected_and_blind(tmp_path: Path) -> None:
    run = tmp_path / "run_post"
    write_minimal_capture(run, started_ms=floor_ms() + 1_000)
    report = gate.build_report(config_path=CONFIG_PATH, lock_path=LOCK_PATH, capture_root=tmp_path)
    assert report["selected_run_count"] == 1
    assert report["decision"] == "BLIND_FORWARD_WAIT_NO_INTERIM_OUTCOME_METRICS"
    assert report["edge_rows_admitted"] == 0
    assert report["oracle_report"]["quality_pass"] is True
    assert report["input_immutable"] is True


def test_corrupt_post_floor_capture_blocks_fail_closed(tmp_path: Path) -> None:
    write_minimal_capture(tmp_path / "run_corrupt", started_ms=floor_ms() + 1_000, corrupt_hash=True)
    report = gate.build_report(config_path=CONFIG_PATH, lock_path=LOCK_PATH, capture_root=tmp_path)
    assert report["decision"] == "FORWARD_INTAKE_BLOCKED_FAIL_CLOSED"
    assert "oracle_source_quality_fail" in report["failures"]
    assert report["edge_rows_admitted"] == 0


def test_invalid_completed_manifest_blocks_entire_intake(tmp_path: Path) -> None:
    run = tmp_path / "run_invalid"
    write_minimal_capture(run, started_ms=floor_ms() + 1_000, malformed_manifest=True)
    (run / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json").write_text("{}", encoding="utf-8")
    report = gate.build_report(config_path=CONFIG_PATH, lock_path=LOCK_PATH, capture_root=tmp_path)
    assert report["decision"] == "FORWARD_INTAKE_BLOCKED_FAIL_CLOSED"
    assert "completed_capture_metadata_invalid" in report["failures"]


def test_evaluation_does_not_mutate_capture_inputs(tmp_path: Path) -> None:
    run = tmp_path / "run_post"
    write_minimal_capture(run, started_ms=floor_ms() + 1_000)
    before = {path.name: sha_file(path) for path in run.iterdir()}
    report = gate.build_report(config_path=CONFIG_PATH, lock_path=LOCK_PATH, capture_root=tmp_path)
    after = {path.name: sha_file(path) for path in run.iterdir()}
    assert report["input_hashes_before"] == report["input_hashes_after"]
    assert after == before


def test_lock_drift_blocks_before_oracle_intake(tmp_path: Path) -> None:
    bad_lock = tmp_path / "lock.json"
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    lock["bindings"]["oracle_sha256"] = "0" * 64
    bad_lock.write_text(json.dumps(lock), encoding="utf-8")
    report = gate.build_report(config_path=CONFIG_PATH, lock_path=bad_lock, capture_root=tmp_path)
    assert report["decision"] == "FORWARD_INTAKE_BLOCKED_FAIL_CLOSED"
    assert "lock_binding_hash_mismatch:oracle" in report["failures"]


def test_gate_has_no_network_or_trading_surface() -> None:
    source = (ROOT / "tools" / "bitunix_raw_event_forward_intake_gate.py").read_text(encoding="utf-8")
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
    assert "telegram_bot" not in source
    assert "api.telegram.org" not in source


def test_cli_wait_path_is_successful_and_bounded(tmp_path: Path) -> None:
    out = tmp_path / "intake"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "bitunix_raw_event_forward_intake_gate.py"),
            "--capture-root",
            str(tmp_path),
            "--out-prefix",
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    report = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["decision"] == "WAIT_NO_COMPLETED_POST_FLOOR_CAPTURE"
    assert report["runtime_boundary"]["manual_invocation_only"] is True
    assert report["runtime_boundary"]["autoload_changed"] is False
    assert report["runtime_boundary"]["can_trade"] is False
