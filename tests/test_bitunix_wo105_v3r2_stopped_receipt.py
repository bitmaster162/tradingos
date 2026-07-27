from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "autostart" / "Finalize-BitunixWO105V3R2StoppedReceipt.ps1"


def test_v3r2_stopped_receipt_requires_absent_process_and_zero_event_ledger() -> None:
    source = SCRIPT.read_text(encoding="utf-8-sig")

    assert 'ComponentId = "bitunix_wo105_v3r2_forward"' in source
    assert 'Run-BitunixWO105V3R2ForwardLoop.ps1"' in source
    assert 'stale_receipt_process_absent' in source
    assert 'if ($LedgerRows -ne 0)' in source
    assert 'bitunix_wo105_v3r2_runtime_stopped_verified_after_interface_failure' in source
    assert 'outcome_metrics_inspected = $false' in source
    assert 'can_trade = $false' in source
