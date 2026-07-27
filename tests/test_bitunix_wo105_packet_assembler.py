from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tools import bitunix_wo105_packet_assembler as module


ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads(
    (ROOT / "configs" / "BITUNIX_WO105_CAUSAL_SHADOW_PREREG_V2_2026-07-14.json").read_text(encoding="utf-8")
)


def helpers():
    path = ROOT / "tests" / "test_bitunix_wo105_causal_shadow_evaluator.py"
    spec = importlib.util.spec_from_file_location("_wo105_packet_helpers", path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def accepted_sources(tmp_path: Path, *, include_cvd: bool = True):
    helper = helpers()
    source = helper.build_packet()
    source["cohort_id"] = LOCK["cohort_id"]
    signal_close = source["signal_bars"][-1]["payload"]["close_ms"]
    funding = source["crowd"][0]
    funding["payload"].update(
        {
            "unit": "decimal_fraction",
            "raw_unit": "percentage_points",
            "normalization_rule": "api_percentage_points_divide_by_100",
        }
    )
    funding["source_hash"] = module.evaluator.canonical_sha256(funding["payload"])
    cvd = source["crowd"][2]
    cvd["payload"].update(
        {
            "unit": "signed_volume_share",
            "method": "sum(buy_size-sell_size)/sum(size)",
        }
    )
    cvd["source_hash"] = module.evaluator.canonical_sha256(cvd["payload"])
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    (ws_dir / "WS_INTAKE_MANIFEST.json").write_text(json.dumps({"accepted_runs": 1}), encoding="utf-8")
    write_jsonl(ws_dir / "WS_BOOKS.jsonl", source["books"])
    write_jsonl(ws_dir / "WS_TRADES.jsonl", source["trades"])
    write_jsonl(ws_dir / "CROWD_CVD.jsonl", [cvd] if include_cvd else [])
    rest = {
        "eligible": True,
        "snapshot_received_at": source["evaluation_at"],
        "signal_bars": source["signal_bars"],
        "htf_bars": source["htf_bars"],
        "outcome_bars": source["outcome_bars"],
        "funding": [funding],
        "funding_events": [],
    }
    liquidation_rows = []
    for index, side in enumerate(("BUY", "BUY", "SELL")):
        event_ms = signal_close - 30_000 + index * 1000
        liquidation_rows.append(
            {
                "event_time_ms": event_ms,
                "event_time": "unused",
                "trade_time_ms": event_ms,
                "symbol": "BTCUSDT",
                "side": side,
                "price": 100.0,
                "quantity": 10.0,
                "notional_usd": 1000.0,
                "source": module.liquidation.SOURCE,
                "is_real_liquidation_feed": True,
                "received_at_ns": (event_ms + 10) * 1_000_000,
                "received_at": "unused",
                "collector_host": "test",
                "collector_pid": 1,
                "ingest_schema_version": 2,
            }
        )
    return source, rest, ws_dir, liquidation_rows


def test_readiness_fails_closed_without_post_floor_sources() -> None:
    report = module.readiness_report(
        lock=LOCK,
        rest_runs=[],
        ws_report={"accepted_runs": 0},
        evaluation_at=module.now_ms(),
    )

    assert report["decision"] == "bitunix_wo105_packet_sources_hold"
    assert report["blockers"] == ["no_post_floor_rest_snapshot", "no_post_floor_accepted_ws_capture"]
    assert report["can_trade"] is False


def test_complete_three_source_quorum_can_assemble_but_never_trade(tmp_path: Path) -> None:
    source, rest, ws_dir, liquidation_rows = accepted_sources(tmp_path)

    packet, report = module.assemble(
        lock=LOCK,
        rest_runs=[rest],
        ws_dir=ws_dir,
        liquidation_rows=liquidation_rows,
        evaluation_at=source["evaluation_at"],
    )

    assert packet is not None
    assert report["decision"] == "bitunix_wo105_causal_packet_assembled"
    assert set(report["crowd_quorum"]["accepted_kinds"]) == {"funding_rate_8h", "cvd_norm", "liquidation_skew"}
    assert report["can_trade"] is False


def test_missing_cvd_keeps_packet_unwritten_even_with_other_real_sources(tmp_path: Path) -> None:
    source, rest, ws_dir, liquidation_rows = accepted_sources(tmp_path, include_cvd=False)

    packet, report = module.assemble(
        lock=LOCK,
        rest_runs=[rest],
        ws_dir=ws_dir,
        liquidation_rows=liquidation_rows,
        evaluation_at=source["evaluation_at"],
    )

    assert packet is None
    assert report["decision"] == "bitunix_wo105_packet_hold_incomplete_causal_inputs"
    assert "fresh_crowd_quorum_not_met" in report["blockers"]
