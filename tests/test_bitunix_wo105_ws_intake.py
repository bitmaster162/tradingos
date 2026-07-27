from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from tools import bitunix_wo105_ws_intake as module


PARSER_SHA = "8" * 64


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_capture(run_dir: Path, *, start_ms: int, corrupt_raw_hash: bool = False, trades: int = 60) -> None:
    run_dir.mkdir(parents=True)
    raw: list[str] = []
    index: list[dict] = []
    depth = {
        "ch": "depth_book15",
        "symbol": "BTCUSDT",
        "ts": start_ms + 500,
        "data": {"b": [["100.0", "2.0"]], "a": [["100.1", "3.0"]]},
    }
    raw_depth = json.dumps(depth, separators=(",", ":"))
    raw.append(raw_depth)
    index.append(
        {
            "recv_ns": start_ms * 1_000_000,
            "sha256": "0" * 64 if corrupt_raw_hash else module.sha256_text(raw_depth),
            "parse_kind": "DepthUpdate",
            "dup": False,
            "ch": "depth_book15",
            "symbol": "BTCUSDT",
            "venue_ts": start_ms + 500,
        }
    )
    for number in range(trades):
        venue_ms = start_ms + number * 21_000
        frame = {
            "ch": "trade",
            "symbol": "BTCUSDT",
            "ts": venue_ms,
            "data": [
                {
                    "t": iso(venue_ms),
                    "p": "100.0",
                    "v": "1.0",
                    "s": "buy" if number % 3 else "sell",
                }
            ],
        }
        raw_line = json.dumps(frame, separators=(",", ":"))
        raw.append(raw_line)
        index.append(
            {
                "recv_ns": (venue_ms - 250) * 1_000_000,
                "sha256": module.sha256_text(raw_line),
                "parse_kind": "TradeBatch",
                "dup": False,
                "ch": "trade",
                "symbol": "BTCUSDT",
                "venue_ts": venue_ms,
            }
        )
    raw_path = run_dir / "RAW_FRAMES.jsonl"
    index_path = run_dir / "RAW_FRAME_INDEX.jsonl"
    trades_path = run_dir / "TRADES.jsonl"
    raw_path.write_text("\n".join(raw) + "\n", encoding="utf-8", newline="\n")
    index_path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in index) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    trades_path.write_text("{}\n", encoding="utf-8", newline="\n")
    stream_hashes = {name: sha_file(run_dir / name) for name in module.REQUIRED_FILES}
    manifest = {
        "schema": module.CAPTURE_SCHEMA,
        "started_utc": iso(start_ms),
        "ended_utc": iso(start_ms + max(1, trades - 1) * 21_000),
        "symbols": ["BTCUSDT"],
        "channels": ["depth_book15", "trade"],
        "hold": False,
        "terminal_hold": False,
        "credentials_used": 0,
        "private_calls": 0,
        "order_calls": 0,
        "error_taxonomy": {"NETWORK": 0, "PARSER": 0, "LOCAL": 0, "STORAGE": 0},
        "receipts": {
            "code_sha256": {"public_ws_venue.py": PARSER_SHA},
            "streaming_output_sha256": stream_hashes,
        },
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
        "decision": module.ACCEPTED_DECISION,
        "failures": [],
        "can_trade": False,
        "manifest_sha256": sha_file(manifest_path),
        "file_receipts": receipts,
    }
    (run_dir / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json").write_text(json.dumps(acceptance), encoding="utf-8")


def test_accepted_capture_maps_clock_skew_conservatively_and_derives_cvd(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    run = tmp_path / "run_valid"
    write_capture(run, start_ms=floor + 1_000)

    result = module.admit_capture_run(
        run,
        forward_floor_ms=floor,
        expected_parser_sha256=PARSER_SHA,
        cvd_window_ms=20 * 60 * 1000,
        cvd_min_prints=50,
    )

    assert result["accepted"] is True
    assert result["counts"] == {"books": 1, "trades": 60, "cvd": 1}
    first = result["trades"][0]
    assert first["observed_at"] == first["payload"]["venue_ts_ms"]
    assert first["received_at"] >= first["payload"]["local_receive_ms"]
    assert result["cvd"]["payload"]["unit"] == "signed_volume_share"
    assert -1.0 <= result["cvd"]["payload"]["value"] <= 1.0


def test_pre_floor_capture_is_not_admitted(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    run = tmp_path / "run_old"
    write_capture(run, start_ms=floor - 1)

    result = module.admit_capture_run(run, forward_floor_ms=floor, expected_parser_sha256=PARSER_SHA)

    assert result["accepted"] is False
    assert "capture_started_before_forward_floor" in result["failures"]


def test_raw_index_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    run = tmp_path / "run_corrupt"
    write_capture(run, start_ms=floor + 1_000, corrupt_raw_hash=True)

    result = module.admit_capture_run(run, forward_floor_ms=floor, expected_parser_sha256=PARSER_SHA)

    assert result["accepted"] is False
    assert "raw_frame_hash_mismatch:1" in result["failures"]


def test_short_capture_can_supply_book_and_trades_but_not_cvd(tmp_path: Path) -> None:
    floor = 2_000_000_000_000
    run = tmp_path / "run_short"
    write_capture(run, start_ms=floor + 1_000, trades=10)

    result = module.admit_capture_run(run, forward_floor_ms=floor, expected_parser_sha256=PARSER_SHA)

    assert result["accepted"] is True
    assert result["cvd"] is None
    assert result["cvd_failures"] == ["cvd_insufficient_trade_prints:10<50"]
