from __future__ import annotations

from ops.control_panel.control_panel import latest_autostart_summary


def test_autostart_api_exposes_microstructure_unblock_status() -> None:
    summary = latest_autostart_summary()
    loop = summary["microstructure_unblock_status_loop"]

    assert "pid_alive" in loop
    assert "book_coverage_pct" in loop
    assert "recent_1h_book_coverage_pct" in loop
    assert "recent_6h_book_coverage_pct" in loop
    assert "eta_utc" in loop
    assert loop["observability_only"] is True
    assert loop["signals_allowed"] is False
    assert loop["orders_allowed"] is False
    assert loop["can_trade"] is False


def test_runtime_and_optimizer_projection_include_unblock_loop() -> None:
    summary = latest_autostart_summary()

    assert "microstructure_unblock_status_alive" in summary["runtime"]
    assert "microstructure_unblock_status_loop_alive" in summary["optimizer"]
