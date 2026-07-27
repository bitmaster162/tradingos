from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_real_edge_pulse_targets_only_active_v3r4_cycle() -> None:
    source = (ROOT / "tools" / "real_edge_observer_pulse.py").read_text(encoding="utf-8")

    assert "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R4_2026-07-15.json" in source
    assert "logs/bitunix_wo105_v3r4/bitunix_wo105_v3r4_forward_loop_status.json" in source
    assert "docs/BITUNIX_WO105_V3R4_FIRST_CYCLE_GATE_2026-07-15.json" in source
    assert "tools/bitunix_wo105_v3r4_forward_health.py" in source
    assert "docs/BITUNIX_WO105_V3R4_FORWARD_HEALTH_2026-07-15.json" in source
    assert "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R3_2026-07-14.json" not in source
    assert "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R2_2026-07-14.json" not in source
    assert "docs/BITUNIX_WO105_V3R2_FIRST_CYCLE_GATE_2026-07-14.json" not in source
    assert "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R1_2026-07-14.json" not in source
    assert "docs/BITUNIX_WO105_V3R1_FIRST_CYCLE_GATE_2026-07-14.json" not in source


def test_state_map_separates_v3r3_tombstone_from_active_v3r4() -> None:
    source = (ROOT / "tools" / "anti_loop_state_map.py").read_text(encoding="utf-8")

    assert '"name": "bitunix_wo105_v3r1_clock_contract_tombstone"' in source
    assert '"name": "bitunix_wo105_v3r2_adapter_interface_tombstone"' in source
    assert '"name": "bitunix_wo105_v3r3_receipt_order_tombstone"' in source
    assert '"name": "bitunix_wo105_v3r4_causal_shadow"' in source
    assert "bitunix_wo105_v3r4_causal_shadow_waiting_forward_sample" in source
    assert '"name": "bitunix_wo105_v3r1_causal_shadow"' not in source
    assert '"name": "bitunix_wo105_v3r2_causal_shadow"' not in source
