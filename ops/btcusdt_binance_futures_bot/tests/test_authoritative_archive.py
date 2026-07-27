from decimal import Decimal
import json

from btcusdt_bot.authoritative.archive import (
    USER_TRADES_DATASET,
    AuthoritativeArchive,
)


def test_authoritative_archive_tracks_intervals_and_gaps(tmp_path) -> None:
    archive = AuthoritativeArchive(tmp_path, symbol="BTCUSDT")
    archive.upsert_rows(
        USER_TRADES_DATASET,
        [],
        coverage_intervals=[(0, 999), (2_000, 2_999)],
        updated_at_ms=1,
    )

    result = archive.load_rows_for_range(USER_TRADES_DATASET, start_ms=0, end_ms=3_999)

    assert result.rows == []
    assert result.gaps == [(1_000, 1_999), (3_000, 3_999)]
    assert result.covered_ms == 2_000
    assert result.requested_ms == 4_000
    assert result.coverage_ratio == Decimal("0.5")


def test_authoritative_archive_upserts_and_deduplicates_rows(tmp_path) -> None:
    archive = AuthoritativeArchive(tmp_path, symbol="BTCUSDT")
    archive.upsert_rows(
        USER_TRADES_DATASET,
        [
            {"id": 1, "orderId": 11, "time": 1_700_000_000_100, "price": "50000", "qty": "0.001", "realizedPnl": "0.10"},
            {"id": 2, "orderId": 12, "time": 1_700_000_000_200, "price": "50010", "qty": "0.001", "realizedPnl": "0.20"},
        ],
        coverage_intervals=[(1_700_000_000_000, 1_700_000_000_500)],
        updated_at_ms=2,
    )
    archive.upsert_rows(
        USER_TRADES_DATASET,
        [
            {"id": 2, "orderId": 12, "time": 1_700_000_000_200, "price": "50010", "qty": "0.001", "realizedPnl": "0.20"},
            {"id": 3, "orderId": 13, "time": 1_700_000_000_300, "price": "50020", "qty": "0.002", "realizedPnl": "0.30"},
        ],
        coverage_intervals=[(1_700_000_000_000, 1_700_000_000_500)],
        updated_at_ms=3,
    )

    result = archive.load_rows_for_range(USER_TRADES_DATASET, start_ms=1_700_000_000_000, end_ms=1_700_000_000_500)
    assert [int(row["id"]) for row in result.rows] == [1, 2, 3]
    manifest = json.loads(archive.manifest_path().read_text(encoding="utf-8"))
    bucket = next(iter(manifest["datasets"][USER_TRADES_DATASET].keys()))
    assert manifest["datasets"][USER_TRADES_DATASET][bucket]["row_count"] == 3
    assert result.gaps == []
