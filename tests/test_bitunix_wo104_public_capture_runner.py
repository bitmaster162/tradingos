from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.bitunix_wo104_public_capture_runner import (
    make_audited_close,
    newline_stable_writer_init,
    validate_duration,
)


class DummyFile:
    closed = False


class DummyWriter:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.f = DummyFile()


def test_capture_duration_is_bounded_to_30_60_minutes() -> None:
    validate_duration(30)
    validate_duration(60)
    with pytest.raises(ValueError):
        validate_duration(29.99)
    with pytest.raises(ValueError):
        validate_duration(60.01)


def test_audited_close_binds_each_successful_fsync_close_to_file_hash(tmp_path: Path) -> None:
    path = tmp_path / "RAW_FRAMES.jsonl"
    path.write_text("frame\n", encoding="utf-8")
    writer = DummyWriter(path)
    receipts = {}

    def original(item: DummyWriter) -> None:
        item.f.closed = True

    make_audited_close(original, receipts)(writer)
    assert receipts[path.name]["close_ok"] is True
    assert receipts[path.name]["fsync_ok"] is True
    assert receipts[path.name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_audited_close_records_failure_and_reraises(tmp_path: Path) -> None:
    path = tmp_path / "TRADES.jsonl"
    path.write_text("trade\n", encoding="utf-8")
    writer = DummyWriter(path)
    receipts = {}

    def original(_item: DummyWriter) -> None:
        raise OSError("fsync failed")

    with pytest.raises(OSError):
        make_audited_close(original, receipts)(writer)
    assert receipts[path.name]["close_ok"] is False
    assert receipts[path.name]["fsync_ok"] is False


def test_newline_stable_writer_hashes_exact_windows_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / "RAW_FRAME_INDEX.jsonl"
    writer = DummyWriter(path)
    newline_stable_writer_init(writer, str(path))
    line = "one\n"
    writer.f.write(line)
    writer._h.update(line.encode("utf-8"))
    writer.f.flush()
    writer.f.close()

    assert path.read_bytes() == b"one\n"
    assert writer._h.hexdigest() == hashlib.sha256(path.read_bytes()).hexdigest()
