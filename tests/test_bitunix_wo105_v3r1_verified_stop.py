from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "autostart" / "Stop-BitunixWO105V3R1ForRollover.ps1"


def test_verified_stop_is_bound_to_exact_job_script_and_completed_first_cycle() -> None:
    source = SCRIPT.read_text(encoding="utf-8-sig")

    assert 'ComponentId = "bitunix_wo105_v3r1_forward"' in source
    assert 'Run-BitunixWO105V3R1ForwardLoop.ps1"' in source
    assert "bitunix_wo105_v3_first_cycle_accepted_shadow_only" in source
    assert "running_verified_job_contained" in source
    assert "Stop-TradingOSRuntimeJobReceipt" in source
    assert "V3R1 lock PID does not match job receipt" in source
    assert "exact_script_pids_remaining" in source
    assert "outcome_metrics_inspected = $false" in source
    assert "orders_allowed = $false" in source
    assert "can_trade = $false" in source


def test_verified_stop_never_uses_ambient_process_kill() -> None:
    source = SCRIPT.read_text(encoding="utf-8-sig")

    assert "Stop-Process" not in source
    assert ".Kill()" not in source
    assert "taskkill" not in source.lower()
