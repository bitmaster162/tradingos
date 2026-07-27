from __future__ import annotations

from tools.max_data_cache import read_records_csv


def test_read_records_csv_skips_oversized_invalid_timestamp(tmp_path) -> None:
    path = tmp_path / "oi.csv"
    path.write_text(
        "timestamp,open_interest\n"
        "1000,42.5\n"
        f"{'x' * 200_000},99\n"
        "2000,43.5\n",
        encoding="utf-8",
    )
    rows = read_records_csv(path)
    assert [row["timestamp"] for row in rows] == [1000, 2000]
    assert [row["open_interest"] for row in rows] == [42.5, 43.5]
