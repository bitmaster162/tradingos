from __future__ import annotations

import json

from ops.control_panel import control_panel


def test_microstructure_summary_exposes_bounded_retention_proof(tmp_path, monkeypatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_GUARD_2026-06-25.json").write_text(
        json.dumps(
            {
                "decision": "collector_sla_healthy",
                "feature_retention_drop_rows": 2,
                "feature_retention_drop_allowance_rows": 6,
                "feature_retention_drop_bounded": True,
                "can_trade": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(control_panel, "ROOT", tmp_path)

    summary = control_panel.latest_microstructure_collector_sla_summary()

    assert summary["feature_retention_drop_rows"] == 2
    assert summary["feature_retention_drop_allowance_rows"] == 6
    assert summary["feature_retention_drop_bounded"] is True
    assert summary["can_trade"] is False


def test_dashboard_renders_retention_drop_and_allowance() -> None:
    source = (control_panel.ROOT / "ops" / "control_panel" / "control_panel.py").read_text(encoding="utf-8")

    assert "retentionDropF:${fmt(microstructureCollectorSla.feature_retention_drop_rows)}/${fmt(microstructureCollectorSla.feature_retention_drop_allowance_rows)}" in source
    assert "bounded:${fmt(microstructureCollectorSla.feature_retention_drop_bounded)}" in source
