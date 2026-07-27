import unittest

from tools.edge_liquidation_score_lock_builder import derive_train_thresholds, quantile


class EdgeLiquidationScoreLockBuilderTests(unittest.TestCase):
    def test_quantile_uses_linear_interpolation(self) -> None:
        self.assertEqual(quantile([1.0, 2.0, 3.0, 4.0], 0.25), 1.75)

    def test_derivation_ignores_oos_rows(self) -> None:
        rows = [
            {"window": "train", "strongest_context_score": float(index)}
            for index in range(1, 41)
        ]
        rows.extend(
            {"window": "oos", "strongest_context_score": 1_000_000.0}
            for _ in range(20)
        )

        result = derive_train_thresholds(rows)

        self.assertEqual(result["train_trades"], 40)
        self.assertEqual(result["positive_max_reference_only"], 40.0)
        self.assertEqual(result["positive_q50"], 20.5)


if __name__ == "__main__":
    unittest.main()
