from __future__ import annotations

from tools import bybit_liquidation_canonical_v2_bar_closure_audit as module


def test_v2_generic_builder_can_emit_an_open_exit_bar() -> None:
    diagnostic = module.synthetic_open_bar_diagnostic()

    assert diagnostic["records_emitted"] == 1
    assert diagnostic["exit_bar_fully_closed"] is False
    assert diagnostic["v2_calls_generic_event_builder"] is True
    assert diagnostic["v2_filters_fully_closed_bars"] is False

