from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_real_edge_pulse_uses_versioned_operational_writer_without_mutating_v4_dependency() -> None:
    pulse = (ROOT / "tools" / "real_edge_observer_pulse.py").read_text(encoding="utf-8")
    lock = (ROOT / "configs" / "BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V4_2026-07-14.json").read_text(
        encoding="utf-8"
    )

    assert '"tools/binance_rest_kline_tail_gap_filler_v2.py"' in pulse
    assert '"path": "tools/binance_rest_kline_tail_gap_filler.py"' in lock
    assert '"path": "tools/binance_rest_kline_tail_gap_filler_v2.py"' not in lock
