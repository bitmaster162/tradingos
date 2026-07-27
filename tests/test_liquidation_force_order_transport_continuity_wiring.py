from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_real_edge_pulse_runs_force_order_transport_continuity_audit() -> None:
    source = (ROOT / "tools" / "real_edge_observer_pulse.py").read_text(encoding="utf-8")

    assert '"force_order_transport_continuity"' in source
    assert '"tools/liquidation_force_order_transport_continuity.py"' in source
    assert "LIQUIDATION_FORCE_ORDER_TRANSPORT_CONTINUITY_2026-07-15.json" in source
