from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "ops" / "autostart" / "Run-BitunixWO105V3R3ForwardLoop.ps1"


def test_v3r3_wrapper_binds_future_floor_lock_and_v5_assembler() -> None:
    source = WRAPPER.read_text(encoding="utf-8-sig")

    assert 'ForwardFloor "2026-07-14T19:30:00Z"' in source
    assert 'RuntimeTag "bitunix_wo105_v3r3"' in source
    assert 'BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R3_2026-07-14.json"' in source
    assert 'AssemblerScriptRelativePath "tools\\bitunix_wo105_packet_assembler_v5.py"' in source
    assert 'ShadowTag "bitunix_wo105_shadow_v3r3"' in source
    assert 'CohortLabel "BITUNIX_WO105_V3R3"' in source
    assert "Test-TradingOSRuntimeShutdownRequested" in source
