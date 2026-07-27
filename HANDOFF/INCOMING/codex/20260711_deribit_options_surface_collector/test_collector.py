from __future__ import annotations

import collector


def test_surface_metrics_are_descriptive_and_quality_gated() -> None:
    now = 1_800_000_000_000
    expiries = [now + days * collector.DAY_MS for days in (10, 30, 60)]
    instruments = []
    summaries = []
    for expiry_index, expiry in enumerate(expiries):
        expiry_underlying = 100.0 + expiry_index * 50.0
        for strike in (expiry_underlying * 0.9, expiry_underlying, expiry_underlying * 1.1):
            for option_type, suffix in (("call", "C"), ("put", "P")):
                name = f"BTC-X{expiry_index}-{int(strike)}-{suffix}"
                instruments.append({"instrument_name": name, "is_active": True, "expiration_timestamp": expiry, "strike": strike, "option_type": option_type})
                base_iv = 50.0 + expiry_index
                mark_iv = base_iv + (2.0 if option_type == "put" and strike == 90.0 else 0.0)
                summaries.append({"instrument_name": name, "underlying_price": expiry_underlying, "mark_iv": mark_iv, "open_interest": 10.0, "bid_price": 0.01, "ask_price": 0.02})
    contract = {
        "quality_gate": {
            "minimum_active_instruments": 18,
            "minimum_summary_rows": 18,
            "minimum_join_rate": 1.0,
            "minimum_mark_iv_coverage": 1.0,
            "minimum_open_interest_coverage": 1.0,
            "minimum_distinct_expiries": 3,
        }
    }
    surface = collector.derive_surface(instruments, summaries, now, contract)
    assert surface["quality_pass"] is True
    assert surface["quality"]["joined_rows"] == 18
    assert surface["near_expiry"]["dte"] == 10.0
    assert surface["near_expiry"]["underlying_price"] == 100.0
    assert surface["near_expiry"]["atm_strike"] == 100.0
    assert surface["near_expiry"]["atm_iv_pct"] == 50.0
    assert surface["near_expiry"]["moneyness_skew_proxy_pp"] == 2.0
    assert surface["term_atm_iv_spread_pp"] == 1.0
    assert surface["medium_expiry"]["underlying_price"] == 150.0
    assert surface["medium_expiry"]["atm_strike"] == 150.0
    assert surface["directional_signal"] is None
    assert surface["can_trade"] is False
