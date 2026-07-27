from __future__ import annotations

import json
from pathlib import Path

from tools.derivatives_event_telegram_notify import main


def test_derivatives_event_telegram_notify_dry_run_and_dedupe(tmp_path: Path, monkeypatch) -> None:
    observer = {
        "observer_id": "derivatives_event_forward_observer",
        "selected_config": {
            "strategy_id": "deriv_test",
            "family": "oi_build_continuation",
            "interval": "4h",
            "side": "LONG",
            "regime_filter": "ema50_stack",
            "take_atr": 3.0,
            "max_hold_bars": 8,
        },
        "latest_observation": {
            "status": "observer_signal_written",
            "signal": True,
            "events_written": 1,
            "bar_ts": "2026-01-01T00:00:00+00:00",
            "close": 100,
        },
    }
    observer_path = tmp_path / "observer.json"
    scoreboard_path = tmp_path / "scoreboard.json"
    gate_path = tmp_path / "gate.json"
    state_path = tmp_path / "state.json"
    observer_path.write_text(json.dumps(observer), encoding="utf-8")
    scoreboard_path.write_text(json.dumps({"summary": {"classification": "pending_only"}}), encoding="utf-8")
    gate_path.write_text(json.dumps({"decision": "blocked_waiting_derivatives_event_forward_evidence"}), encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--observer-json-path",
            str(observer_path),
            "--scoreboard-json-path",
            str(scoreboard_path),
            "--gate-json-path",
            str(gate_path),
            "--state-path",
            str(state_path),
            "--card-json-path",
            str(tmp_path / "card.json"),
            "--card-md-path",
            str(tmp_path / "card.md"),
            "--out-prefix",
            str(tmp_path / "notify"),
            "--dry-run",
        ],
    )
    assert main() == 0
    report = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))
    assert report["decision"] == "dry_run_ready"
    assert report["can_trade"] is False

    assert main() == 0
    report = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))
    assert report["decision"] == "skipped_duplicate"


def test_derivatives_event_telegram_notify_skips_no_signal(tmp_path: Path, monkeypatch) -> None:
    observer_path = tmp_path / "observer.json"
    scoreboard_path = tmp_path / "scoreboard.json"
    gate_path = tmp_path / "gate.json"
    observer_path.write_text(json.dumps({"latest_observation": {"signal": False, "events_written": 0}}), encoding="utf-8")
    scoreboard_path.write_text("{}", encoding="utf-8")
    gate_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--observer-json-path",
            str(observer_path),
            "--scoreboard-json-path",
            str(scoreboard_path),
            "--gate-json-path",
            str(gate_path),
            "--state-path",
            str(tmp_path / "state.json"),
            "--out-prefix",
            str(tmp_path / "notify"),
        ],
    )
    assert main() == 0
    report = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))
    assert report["decision"] == "skipped_no_new_signal"
