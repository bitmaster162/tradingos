from __future__ import annotations

from pathlib import Path

from tools.derivatives_event_data_reconciler import build_report


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_reconciler_copies_with_backup(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    rel = "data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_klines.csv"
    write(source / rel, "time,open,high,low,close,volume\n2026-01-02T00:00:00+00:00,1,2,0,1,10\n")
    write(target / rel, "time,open,high,low,close,volume\n2026-01-01T00:00:00+00:00,1,2,0,1,10\n")

    report = build_report(source_root=source, target_root=target, data_paths=[rel], backup_root=backup, dry_run=False)

    assert report["decision"] == "data_reconciliation_completed"
    assert report["actions"][0]["status"] == "copied"
    assert (backup / rel).is_file()
    assert "2026-01-02" in (target / rel).read_text(encoding="utf-8")
    assert report["can_trade"] is False


def test_reconciler_dry_run_does_not_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    rel = "data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_klines.csv"
    write(source / rel, "time,open,high,low,close,volume\n2026-01-02T00:00:00+00:00,1,2,0,1,10\n")
    write(target / rel, "time,open,high,low,close,volume\n2026-01-01T00:00:00+00:00,1,2,0,1,10\n")

    report = build_report(source_root=source, target_root=target, data_paths=[rel], backup_root=backup, dry_run=True)

    assert report["decision"] == "data_reconciliation_dry_run"
    assert report["actions"][0]["status"] == "would_copy"
    assert not (backup / rel).exists()
    assert "2026-01-01" in (target / rel).read_text(encoding="utf-8")
