from __future__ import annotations

from tools.bybit_liquidation_side_semantics_audit import build_report


def report(long_flush: int, short_squeeze: int, mixed: int = 2) -> dict:
    return {
        "summary": {
            "events": 100,
            "aggregate_rows": long_flush + short_squeeze + mixed,
            "matched_price_bars": 50,
            "contexts": {
                "long_liquidation_flush": long_flush,
                "short_liquidation_squeeze": short_squeeze,
                "mixed": mixed,
            },
        }
    }


def test_exact_directional_swap_terminally_invalidates_legacy_contract() -> None:
    audit = build_report(report(30, 20), report(20, 30))
    assert audit["contract_failure_proven"] is True
    assert audit["decision"].endswith("terminal_contract_failure")
    assert len(audit["impacted_families"]) == 3
    assert audit["resolution"]["legacy_directional_results_valid"] is False
    assert audit["can_trade"] is False


def test_mismatched_sample_fails_closed_without_claiming_proof() -> None:
    legacy = report(30, 20)
    canonical = report(20, 30)
    canonical["summary"]["events"] = 101
    audit = build_report(legacy, canonical)
    assert audit["contract_failure_proven"] is False
    assert audit["decision"].endswith("inconclusive_fail_closed")
    assert audit["can_trade"] is False
