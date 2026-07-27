from __future__ import annotations

from tools import bitunix_wo105_liquidation_context as base
from tools import bitunix_wo105_liquidation_context_v2 as v2
from tools import bitunix_wo105_liquidation_context_v3 as v3
from tools import bitunix_wo105_packet_assembler_v5 as assembler_v5


def test_v3_adds_required_loader_without_changing_v2_context_semantics() -> None:
    assert v3.load_rows is base.load_rows
    assert v3.validate_row is v2.validate_row
    assert v3.build_context is v2.build_context
    assert v3.DEFAULT_MAX_CLOCK_SKEW_MS == v2.DEFAULT_MAX_CLOCK_SKEW_MS == 5_000


def test_v5_assembler_binds_interface_complete_adapter(monkeypatch) -> None:
    monkeypatch.setattr(assembler_v5.assembler_v3, "liquidation", object())
    monkeypatch.setattr(assembler_v5.assembler_v3, "TOOL_PATH", "sentinel")

    configured = assembler_v5.configure_for_v5()

    assert configured.liquidation is v3
    assert configured.TOOL_PATH == "tools/bitunix_wo105_packet_assembler_v5.py"
