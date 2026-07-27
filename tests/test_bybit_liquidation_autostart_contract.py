from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_main_runtime_starts_public_bybit_liquidation_watchdog() -> None:
    source = read("ops/autostart/Start-TradingOSRuntime.ps1")
    assert "Start-BybitAllLiquidationWatchdogLoop.ps1" in source
    assert "BybitAllLiquidationWatchdogSleepSeconds" in source
    assert "bybit_all_liquidation_watchdog_alive" in source
    assert "bybit_all_liquidation_watchdog_public_data_only = $true" in source


def test_optimizer_requires_bybit_watchdog_liveness() -> None:
    source = read("ops/autostart/Optimize-TradingOSRuntime.ps1")
    assert "BybitAllLiquidationWatchdogLockPath" in source
    assert "-not $BybitAllLiquidationWatchdogAliveBefore" in source
    assert "bybit_all_liquidation_watchdog_loop_alive" in source


def test_stop_and_status_paths_cover_bybit_watchdog() -> None:
    stop = read("ops/autostart/Stop-TradingOSRuntime.ps1")
    status = read("ops/autostart/Get-TradingOSAutostartStatus.ps1")
    lock_path = "logs\\liquidation_bybit\\bybit_all_liquidation_watchdog_loop.lock.json"
    assert lock_path in stop
    assert "bybit_all_liquidation_watchdog" in status
    assert "BYBIT_ALL_LIQUIDATION_COLLECTOR_WATCHDOG_2026-07-01.json" in status
