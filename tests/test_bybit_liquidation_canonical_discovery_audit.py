from __future__ import annotations

from tools.bybit_liquidation_canonical_discovery_audit import build_report


def row(index: int, value: float, *, context: str = "long_liquidation_flush", horizon: int = 8) -> dict:
    return {
        "symbol": f"S{index % 10}",
        "bar_ts": f"2026-01-{1 + index // 24:02d}T{index % 24:02d}:00:00.000Z",
        "independent_4h_block": f"B{index // 4}",
        "dominant_context": context,
        "horizon_bars": horizon,
        "continuation_return_bps": -value,
        "reversal_return_bps": value,
    }


def test_stable_discovery_candidate_is_not_mislabeled_as_forward_proof() -> None:
    rows = [row(index, 30.0) for index in range(120)]
    report = build_report(rows, 7.0)
    selected = report["selected_candidate"]
    assert report["decision"].endswith("requires_new_forward_lock")
    assert selected["candidate_id"] == "long_liquidation_flush__reversal__h8"
    assert selected["screen_pass"] is True
    assert report["selection_boundary"]["untouched_validation"] is False
    assert report["selection_boundary"]["can_trade"] is False


def test_negative_last_third_rejects_candidate() -> None:
    rows = [row(index, 30.0 if index < 80 else -30.0) for index in range(120)]
    report = build_report(rows, 7.0)
    target = next(item for item in report["leaderboard"] if item["candidate_id"] == "long_liquidation_flush__reversal__h8")
    assert target["screen_pass"] is False
    assert target["screen_checks"]["all_chronological_thirds_positive"] is False
