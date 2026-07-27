import unittest
from datetime import datetime, timezone

from tools.liquidity_sweep_detector import OhlcvBar
from tools.session_opening_range_nested_holdout import SessionConfig, generate_signals, validation_gate


class SessionOpeningRangeNestedHoldoutTests(unittest.TestCase):
    def test_signal_occurs_after_completed_opening_range(self) -> None:
        bars = []
        for hour in range(30):
            ts = datetime(2024, 1, 1 + hour // 24, hour % 24, tzinfo=timezone.utc).isoformat()
            bars.append(OhlcvBar(hour, ts, 95.0, 100.0, 90.0, 95.0, 100.0))
        bars[4] = OhlcvBar(4, bars[4].ts, 99.0, 103.0, 98.0, 102.5, 200.0)
        features = [{"atr": 2.0, "volume_z": 1.0} for _ in bars]
        config = SessionConfig("test", "LONG", 4, 0.0, "none", 0.0, 0.65, 1.0, 2.0, 6)

        signals = generate_signals(config, bars, features, [95.0] * len(bars))

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["bar_index"], 4)

    def test_validation_gate_requires_cost_stress(self) -> None:
        window = {
            "summary": {"trades": 40, "expectancy_r": 0.2, "max_drawdown_r": -4.0},
            "stable_folds": 3,
            "cost_stress": {"summary": {"expectancy_r": -0.01}},
        }

        gate = validation_gate(window)

        self.assertFalse(gate["pass"])
        self.assertFalse(gate["checks"]["cost_stress_positive"])


if __name__ == "__main__":
    unittest.main()
