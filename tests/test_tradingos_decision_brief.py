from __future__ import annotations

import copy
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "tradingos_decision_brief.py"
POLICY = ROOT / "configs" / "TRADINGOS_DECISION_BRIEF_POLICY_V1.json"
SAMPLE = ROOT / "examples" / "tradingos_decision_brief" / "market_snapshot.sample.json"

spec = importlib.util.spec_from_file_location("tradingos_decision_brief", MODULE_PATH)
assert spec and spec.loader
brief_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief_tool)


NOW = datetime(2026, 7, 29, 0, 30, tzinfo=timezone.utc)


def sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def run_snapshot(tmp_path: Path, payload: dict):
    input_path = tmp_path / "market_snapshot.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    return brief_tool.generate(input_path, tmp_path / "out", POLICY, NOW)


def test_complete_snapshot_generates_three_readable_artifacts(tmp_path: Path) -> None:
    brief, paths, pilot_status = run_snapshot(tmp_path, sample())
    assert pilot_status is None
    assert brief["status"] == "READY"
    assert brief["decision"]["stance"] == "WATCH_LONG"
    assert brief["decision"]["edge_sufficient"] is True
    assert len(brief["intent_hypotheses"]) == 2
    assert brief["permissions"]["can_trade"] is False
    assert all(path.is_file() and path.stat().st_size > 100 for path in paths.values())
    assert "One next action" in paths["markdown"].read_text(encoding="utf-8")
    assert "@media print" in paths["html"].read_text(encoding="utf-8")


def test_stale_snapshot_fails_closed(tmp_path: Path) -> None:
    payload = sample()
    payload["as_of"] = "2026-07-28T20:00:00Z"
    brief, _, _ = run_snapshot(tmp_path, payload)
    assert brief["status"] == "BLOCKED"
    assert brief["decision"]["stance"] == "NO_ACTION"
    assert "stale_snapshot" in brief["uncertainty"]["blockers"]


def test_missing_required_source_fails_closed(tmp_path: Path) -> None:
    payload = sample()
    payload["data_quality"]["present_sources"].remove("funding")
    brief, _, _ = run_snapshot(tmp_path, payload)
    assert brief["decision"]["stance"] == "NO_ACTION"
    assert "funding" in brief["uncertainty"]["missing_data"]
    assert "missing_required_sources" in brief["uncertainty"]["blockers"]


def test_conflicting_data_fails_closed(tmp_path: Path) -> None:
    payload = sample()
    payload["data_quality"]["conflicts"] = ["spot and perp timestamps are not aligned"]
    brief, _, _ = run_snapshot(tmp_path, payload)
    assert brief["status"] == "BLOCKED"
    assert brief["decision"]["stance"] == "NO_ACTION"
    assert "conflicting_data" in brief["uncertainty"]["blockers"]


def test_can_trade_true_fails_closed_and_output_remains_false(tmp_path: Path) -> None:
    payload = sample()
    payload["can_trade"] = True
    brief, _, _ = run_snapshot(tmp_path, payload)
    assert brief["status"] == "BLOCKED"
    assert brief["decision"]["stance"] == "NO_ACTION"
    assert "unsafe_permission:can_trade_must_be_false" in brief["uncertainty"]["blockers"]
    assert brief["can_trade"] is False
    assert brief["permissions"]["orders_allowed"] is False


def test_insufficient_edge_abstains_without_input_failure(tmp_path: Path) -> None:
    payload = sample()
    payload["market_structure"]["trend"] = "range"
    payload["price"]["ema_fast"] = payload["price"]["ema_slow"]
    payload["price"]["change_pct"] = 0.0
    payload["derivatives"]["open_interest_change_pct"] = 0.0
    payload["flow"]["spot_cvd_direction"] = "flat"
    payload["flow"]["perp_cvd_direction"] = "flat"
    payload["flow"]["relative_volume"] = 1.0
    brief, _, _ = run_snapshot(tmp_path, payload)
    assert brief["status"] == "READY"
    assert brief["decision"]["stance"] == "NO_ACTION"
    assert brief["decision"]["reason"] == "insufficient_independent_edge"


def test_inconsistent_levels_fail_closed(tmp_path: Path) -> None:
    payload = sample()
    payload["market_structure"]["support"] = payload["price"]["last"] + 1
    brief, _, _ = run_snapshot(tmp_path, payload)
    assert brief["decision"]["stance"] == "NO_ACTION"
    assert "conflicting_market_structure" in brief["uncertainty"]["blockers"]


def test_html_escapes_operator_text(tmp_path: Path) -> None:
    payload = sample()
    payload["operator"]["changed_decision"] = "<script>alert(1)</script>"
    _, paths, _ = run_snapshot(tmp_path, payload)
    rendered = paths["html"].read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_pilot_log_appends_once_per_brief_and_day(tmp_path: Path) -> None:
    input_path = tmp_path / "market_snapshot.json"
    input_path.write_text(json.dumps(sample()), encoding="utf-8")
    log = tmp_path / "pilot.jsonl"
    first = brief_tool.generate(input_path, tmp_path / "one", POLICY, NOW, log, "DAY_1")
    second = brief_tool.generate(input_path, tmp_path / "two", POLICY, NOW, log, "DAY_1")
    assert first[2] == "appended"
    assert second[2] == "duplicate_suppressed"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["changed_decision"]
    assert rows[0]["prevented_decision"]
    assert rows[0]["can_trade"] is False


def test_policy_permissions_fail_closed_before_generation(tmp_path: Path) -> None:
    unsafe_policy = json.loads(POLICY.read_text(encoding="utf-8"))
    unsafe_policy["output_permissions"]["can_trade"] = True
    policy_path = tmp_path / "unsafe_policy.json"
    policy_path.write_text(json.dumps(unsafe_policy), encoding="utf-8")
    input_path = tmp_path / "market_snapshot.json"
    input_path.write_text(json.dumps(sample()), encoding="utf-8")
    try:
        brief_tool.generate(input_path, tmp_path / "out", policy_path, NOW)
    except ValueError as exc:
        assert "policy can_trade must be false" in str(exc)
    else:
        raise AssertionError("unsafe policy was accepted")
