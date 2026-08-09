from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "tradingos_decision_brief_v2.py"
POLICY = ROOT / "configs" / "TRADINGOS_DECISION_BRIEF_POLICY_V1.json"
SAMPLE = ROOT / "examples" / "tradingos_decision_brief" / "market_snapshot.sample.json"

spec = importlib.util.spec_from_file_location("tradingos_decision_brief_v2", MODULE_PATH)
assert spec and spec.loader
brief_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief_tool)

NOW = datetime(2026, 7, 29, 0, 30, tzinfo=timezone.utc)


def sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def policy() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def run_snapshot(tmp_path: Path, payload: dict):
    input_path = tmp_path / "market_snapshot.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    return brief_tool.generate(input_path, tmp_path / "out", POLICY, NOW)


def test_safe_sample_remains_ready_and_binds_v2_generator(tmp_path: Path) -> None:
    brief, paths, pilot_status = run_snapshot(tmp_path, sample())
    assert pilot_status is None
    assert brief["status"] == "READY"
    assert brief["decision"]["stance"] == "WATCH_LONG"
    assert brief["permissions"]["can_trade"] is False
    assert brief["provenance"]["generator"] == "tools/tradingos_decision_brief_v2.py"
    assert brief["provenance"]["generator_version"] == "2.0.0"
    assert len(brief["provenance"]["generator_sha256"]) == 64
    assert all(path.is_file() for path in paths.values())


def test_any_unsafe_policy_permission_fails_closed_before_generation(tmp_path: Path) -> None:
    cases = [
        ("signals_allowed", True),
        ("orders_allowed", True),
        ("credentials_allowed", True),
        ("can_trade", True),
        ("capital_permission", "ALLOW"),
        ("watch_stances_allowed", False),
    ]
    for index, (key, unsafe) in enumerate(cases):
        unsafe_policy = policy()
        unsafe_policy["output_permissions"][key] = unsafe
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        policy_path = case_dir / "unsafe_policy.json"
        policy_path.write_text(json.dumps(unsafe_policy), encoding="utf-8")
        input_path = case_dir / "market_snapshot.json"
        input_path.write_text(json.dumps(sample()), encoding="utf-8")

        try:
            brief_tool.generate(input_path, case_dir / "out", policy_path, NOW)
        except ValueError as exc:
            assert "unsafe policy permissions" in str(exc)
        else:
            raise AssertionError(f"unsafe policy permission was accepted: {key}={unsafe!r}")

        assert not (case_dir / "out" / "brief.json").exists()


def test_missing_source_timestamp_blocks_brief(tmp_path: Path) -> None:
    payload = sample()
    source = next(item for item in payload["provenance"]["sources"] if item["kind"] == "funding")
    source.pop("observed_at")
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert brief["decision"]["stance"] == "NO_ACTION"
    assert "missing_or_invalid_provenance_timestamp:funding" in brief["uncertainty"]["blockers"]
    assert brief["can_trade"] is False


def test_stale_required_source_blocks_brief(tmp_path: Path) -> None:
    payload = sample()
    source = next(item for item in payload["provenance"]["sources"] if item["kind"] == "open_interest")
    source["observed_at"] = "2026-07-28T20:00:00Z"
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert "stale_provenance:open_interest" in brief["uncertainty"]["blockers"]
    assert "fresh_provenance:open_interest" in brief["uncertainty"]["missing_data"]


def test_future_required_source_blocks_brief(tmp_path: Path) -> None:
    payload = sample()
    source = next(item for item in payload["provenance"]["sources"] if item["kind"] == "spot_flow")
    source["observed_at"] = "2026-07-29T00:40:01Z"
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert "future_provenance_clock_skew:spot_flow" in brief["uncertainty"]["blockers"]
    assert "provenance_after_snapshot:spot_flow" in brief["uncertainty"]["blockers"]


def test_duplicate_required_source_kind_is_ambiguous(tmp_path: Path) -> None:
    payload = sample()
    duplicate = dict(payload["provenance"]["sources"][0])
    duplicate["source_id"] = "sample:second-ohlcv-source"
    payload["provenance"]["sources"].append(duplicate)
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert "ambiguous_provenance:ohlcv" in brief["uncertainty"]["blockers"]
    assert "duplicate_provenance_kind:ohlcv" in brief["uncertainty"]["conflicts"]


def test_missing_source_id_blocks_brief(tmp_path: Path) -> None:
    payload = sample()
    source = next(item for item in payload["provenance"]["sources"] if item["kind"] == "ohlcv")
    source["source_id"] = ""
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert "missing_provenance_source_id:ohlcv" in brief["uncertainty"]["blockers"]
