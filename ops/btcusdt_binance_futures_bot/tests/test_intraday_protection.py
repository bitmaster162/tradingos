from decimal import Decimal

from btcusdt_bot.monitoring.intraday_protection import (
    IntradayProtectionThresholds,
    evaluate_intraday_protection,
    normalize_adl_quantile,
    normalize_api_trading_status,
)


def test_normalize_api_trading_status_handles_documented_list_shape() -> None:
    payload = {
        "indicators": {
            "BTCUSDT": [
                {
                    "isLocked": True,
                    "plannedRecoverTime": 1_700_000_300_000,
                    "indicator": "UFR",
                    "value": "0.92",
                    "triggerValue": "0.995",
                },
                {
                    "isLocked": False,
                    "plannedRecoverTime": 1_700_000_100_000,
                    "indicator": "DR",
                    "value": "0.50",
                    "triggerValue": "0.90",
                },
            ]
        }
    }

    snapshot = normalize_api_trading_status(payload, "BTCUSDT")

    assert snapshot.is_locked is True
    assert snapshot.planned_recover_time_ms == 1_700_000_300_000
    assert snapshot.max_utilization == Decimal("0.92") / Decimal("0.995")
    assert len(snapshot.indicators) == 2


def test_intraday_protection_reduce_size_on_high_quant_utilization_and_adl() -> None:
    quant_rules = normalize_api_trading_status(
        {
            "indicators": {
                "BTCUSDT": [
                    {
                        "isLocked": False,
                        "plannedRecoverTime": 0,
                        "indicator": "UFR",
                        "value": "0.91",
                        "triggerValue": "0.995",
                    }
                ]
            }
        },
        "BTCUSDT",
    )
    adl_quantile = normalize_adl_quantile(
        [{"symbol": "BTCUSDT", "adlQuantile": {"BOTH": 3}}],
        "BTCUSDT",
    )

    decision = evaluate_intraday_protection(
        quant_rules=quant_rules,
        adl_quantile=adl_quantile,
        thresholds=IntradayProtectionThresholds(),
        compared_at_ms=1_700_000_000_000,
    )

    assert decision.action == "reduce_size"
    assert decision.size_multiplier == Decimal("0.35")
    assert decision.moderate_breaches == 2
    assert decision.severe_breaches == 0
    assert "quant_utilization_above_reduce_threshold" in decision.reasons
    assert "adl_quantile_above_reduce_threshold" in decision.reasons


def test_intraday_protection_observe_only_when_lock_active() -> None:
    quant_rules = normalize_api_trading_status(
        {
            "indicators": {
                "BTCUSDT": [
                    {
                        "isLocked": True,
                        "plannedRecoverTime": 1_700_000_300_000,
                        "indicator": "GCR",
                        "value": "0.99",
                        "triggerValue": "0.99",
                    }
                ]
            }
        },
        "BTCUSDT",
    )

    decision = evaluate_intraday_protection(
        quant_rules=quant_rules,
        adl_quantile=None,
        compared_at_ms=1_700_000_000_000,
    )

    assert decision.action == "observe_only"
    assert decision.size_multiplier == Decimal("0")
    assert decision.severe_breaches >= 1
    assert "quant_rules_locked" in decision.reasons
