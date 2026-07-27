from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tools import bitunix_wo105_packet_assembler_v5 as v5


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V3R3_2026-07-14.json").read_text(
        encoding="utf-8"
    )
)


def load_packet_helpers():
    path = ROOT / "tests" / "test_bitunix_wo105_packet_assembler.py"
    spec = importlib.util.spec_from_file_location("_wo105_v5_packet_helpers", path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_v5_assembles_complete_post_floor_three_source_packet_without_trade_permission(
    tmp_path: Path, monkeypatch
) -> None:
    helpers = load_packet_helpers()
    source, rest, ws_dir, liquidation_rows = helpers.accepted_sources(tmp_path)
    monkeypatch.setattr(v5.assembler_v3, "liquidation", v5.liquidation_v3)
    monkeypatch.setattr(v5.assembler_v3, "TOOL_PATH", v5.TOOL_PATH)
    assembler = v5.configure_for_v5()

    ws, ws_failures = assembler.read_ws_series(ws_dir)
    view = assembler.source_view([rest])
    report = assembler.readiness_report(
        lock=LOCK,
        rest_runs=[rest],
        ws_report={"accepted_runs": 1},
        evaluation_at=source["evaluation_at"],
    )
    packet, report = assembler.assemble_current(
        lock=LOCK,
        rest_view=view,
        ws=ws,
        liquidation_rows=liquidation_rows,
        evaluation_at=source["evaluation_at"],
        report=report,
    )

    assert packet is not None
    assert report["decision"] == "bitunix_wo105_v3_causal_packet_assembled"
    assert report["blockers"] == []
    assert ws_failures == []
    assert set(report["crowd_quorum"]["accepted_kinds"]) == {
        "funding_rate_8h",
        "cvd_norm",
        "liquidation_skew",
    }
    assert report["crowd_quorum"]["required"] == 3
    assert report["can_trade"] is False
