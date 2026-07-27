from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_cex_dex_funding_collector_is_managed_by_runtime_lifecycle() -> None:
    loop = read("ops/autostart/Run-CexDexFundingCollectorLoop.ps1")
    watchdog = read("ops/autostart/Run-CexFundingFreshnessWatchdogLoop.ps1")
    start = read("ops/autostart/Start-TradingOSRuntime.ps1")
    stop = read("ops/autostart/Stop-TradingOSRuntime.ps1")
    status = read("ops/autostart/Get-TradingOSAutostartStatus.ps1")

    assert "tools\\hyperliquid_cross_venue_funding_collector.py" in loop
    assert "tools\\direct_cex_funding_replication_collector.py" in loop
    assert "direct_cex_funding_snapshots.jsonl" in loop
    assert "tools\\cex_funding_source_alignment_monitor.py" in loop
    assert "CEX_FUNDING_SOURCE_ALIGNMENT_LOCK_V3_2026-07-14.json" in loop
    assert "CEX_FUNDING_SOURCE_ALIGNMENT_V3_2026-07-14.json" in loop
    assert "source_alignment_exit_code" in loop
    assert "tools\\cex_funding_research_readiness_monitor.py" in loop
    assert "research_readiness_exit_code" in loop
    assert 'cadence_policy = "anchored_start_to_start"' in loop
    assert "CycleDurationMilliseconds" in loop
    assert "Start-Sleep -Milliseconds $NextSleepMilliseconds" in loop
    assert "signals_allowed = $false" in loop
    assert "orders_allowed = $false" in loop
    assert "can_trade = $false" in loop
    assert r"tools\cex_funding_freshness_watchdog.py" in watchdog
    assert r"tools\cex_funding_freshness_incident_alert.py" in watchdog
    assert "--send-telegram" in watchdog
    assert "health_exit_code" in watchdog
    assert "incident_alert_exit_code" in watchdog
    assert "automatic_restart_allowed = $false" in watchdog
    assert "signals_allowed = $false" in watchdog
    assert "orders_allowed = $false" in watchdog
    assert "can_trade = $false" in watchdog
    assert "Run-CexDexFundingCollectorLoop.ps1" in start
    assert "Run-CexFundingFreshnessWatchdogLoop.ps1" in start
    assert "CexDexFundingCollectorSleepSeconds = 60" in start
    assert "CexFundingFreshnessWatchdogSleepSeconds = 60" in start
    assert "cex_dex_funding_collector_loop.lock.json" in start
    assert "cex_dex_funding_freshness_watchdog_loop.lock.json" in start
    assert "cex_dex_funding_collector_loop.lock.json" in stop
    assert "cex_dex_funding_freshness_watchdog_loop.lock.json" in stop
    assert "CEX_DEX_FUNDING_LEAD_LAG_DATA_QUALITY_2026-07-13.json" in status
    assert "CEX_FUNDING_FRESHNESS_WATCHDOG_2026-07-13.json" in status
