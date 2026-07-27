from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "ops" / "autostart" / "Run-BitunixWO105V3R2ForwardLoop.ps1"


def test_v3r2_wrapper_is_bound_to_future_floor_lock_and_v4_assembler() -> None:
    source = WRAPPER.read_text(encoding="utf-8-sig")

    assert 'ForwardFloor "2026-07-14T18:00:00Z"' in source
    assert 'RuntimeTag "bitunix_wo105_v3r2"' in source
    assert 'BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R2_2026-07-14.json"' in source
    assert 'AssemblerScriptRelativePath "tools\\bitunix_wo105_packet_assembler_v4.py"' in source
    assert 'ShadowTag "bitunix_wo105_shadow_v3r2"' in source
    assert 'CohortLabel "BITUNIX_WO105_V3R2"' in source
    assert "Test-TradingOSRuntimeShutdownRequested" in source
