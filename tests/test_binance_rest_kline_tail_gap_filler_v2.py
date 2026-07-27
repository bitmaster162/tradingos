from __future__ import annotations

from pathlib import Path

from tools import binance_rest_kline_tail_gap_filler as v1
from tools import binance_rest_kline_tail_gap_filler_v2 as v2


def test_v2_retries_transient_replace_contention_and_cleans_own_temp(tmp_path, monkeypatch) -> None:
    output = tmp_path / "futures" / "BTCUSDT" / "1h_klines.csv"
    stale_shared_temp = output.with_suffix(output.suffix + ".tmp")
    stale_shared_temp.parent.mkdir(parents=True)
    stale_shared_temp.write_text("stale concurrent temp", encoding="utf-8")
    rows = [
        {
            "time": "1970-01-01T01:00:00+00:00",
            "time_ms": "3600000",
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100",
            "volume": "10",
        }
    ]
    original_replace = Path.replace
    attempts = 0

    def replace_once_blocked(path: Path, target: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("simulated transient reader contention")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", replace_once_blocked)
    monkeypatch.setattr(v2.time, "sleep", lambda _seconds: None)

    v2.write_rows(output, rows, create_backup=False)

    assert attempts == 2
    assert v1.read_existing(output)[0]["time_ms"] == "3600000"
    assert stale_shared_temp.read_text(encoding="utf-8") == "stale concurrent temp"
    assert list(output.parent.glob(f".{output.name}.*.tmp")) == []
