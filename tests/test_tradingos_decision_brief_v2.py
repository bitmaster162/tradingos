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


def source(payload: dict, kind: str) -> dict:
    return next(item for item in payload["provenance"]["sources"] if item["kind"] == kind)


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
    source(payload, "funding").pop("observed_at")
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert brief["decision"]["stance"] == "NO_ACTION"
    assert "missing_or_invalid_provenance_timestamp:funding" in brief["uncertainty"]["blockers"]
    assert brief["can_trade"] is False


def test_stale_required_source_blocks_brief(tmp_path: Path) -> None:
    payload = sample()
    source(payload, "open_interest")["observed_at"] = "2026-07-28T20:00:00Z"
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert "stale_provenance:open_interest" in brief["uncertainty"]["blockers"]
    assert "fresh_provenance:open_interest" in brief["uncertainty"]["missing_data"]


def test_future_required_source_blocks_brief(tmp_path: Path) -> None:
    payload = sample()
    source(payload, "spot_flow")["observed_at"] = "2026-07-29T00:40:01Z"
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
    source(payload, "ohlcv")["source_id"] = ""
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert "missing_provenance_source_id:ohlcv" in brief["uncertainty"]["blockers"]


def test_wp001_reused_source_id_across_required_kinds_blocks(tmp_path: Path) -> None:
    payload = sample()
    shared = source(payload, "funding")["source_id"].strip()
    source(payload, "open_interest")["source_id"] = shared
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert f"reused_provenance_source_id:{shared}" in brief["uncertainty"]["blockers"]
    assert f"source_id_shared_across_kinds:{shared}" in brief["uncertainty"]["conflicts"]


def test_wp001_whitespace_alias_is_normalized_and_blocks(tmp_path: Path) -> None:
    payload = sample()
    shared = source(payload, "funding")["source_id"].strip()
    source(payload, "spot_flow")["source_id"] = f"  {shared}  "
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert f"reused_provenance_source_id:{shared}" in brief["uncertainty"]["blockers"]
    assert f"source_id_shared_across_kinds:{shared}" in brief["uncertainty"]["conflicts"]


def test_wp001_invalid_timestamp_cannot_hide_source_reuse(tmp_path: Path) -> None:
    payload = sample()
    shared = source(payload, "funding")["source_id"].strip()
    reused = source(payload, "open_interest")
    reused["source_id"] = shared
    reused["observed_at"] = "not-a-timestamp"
    brief, _, _ = run_snapshot(tmp_path, payload)

    assert brief["status"] == "BLOCKED"
    assert "missing_or_invalid_provenance_timestamp:open_interest" in brief["uncertainty"]["blockers"]
    assert f"reused_provenance_source_id:{shared}" in brief["uncertainty"]["blockers"]
    assert f"source_id_shared_across_kinds:{shared}" in brief["uncertainty"]["conflicts"]


def assert_policy_rejected_before_generation(
    tmp_path: Path,
    case_name: str,
    policy_payload: dict,
    expected_fragment: str,
) -> None:
    case_dir = tmp_path / case_name
    case_dir.mkdir()
    policy_path = case_dir / "policy.json"
    policy_path.write_text(json.dumps(policy_payload), encoding="utf-8")
    input_path = case_dir / "market_snapshot.json"
    input_path.write_text(json.dumps(sample()), encoding="utf-8")
    out_dir = case_dir / "out"

    try:
        brief_tool.generate(input_path, out_dir, policy_path, NOW)
    except ValueError as exc:
        assert expected_fragment in str(exc)
    else:
        raise AssertionError(f"invalid policy was accepted: {case_name}")

    assert not out_dir.exists()


def test_policy_v1_rejects_unknown_permissions_even_when_false(tmp_path: Path) -> None:
    for index, value in enumerate((True, False)):
        payload = policy()
        payload["output_permissions"][f"future_permission_{index}"] = value
        assert_policy_rejected_before_generation(
            tmp_path,
            f"unknown-permission-{index}",
            payload,
            "invalid_policy:unsupported_permission:",
        )


def test_policy_v1_requires_every_known_permission_and_exact_types(tmp_path: Path) -> None:
    missing = policy()
    missing["output_permissions"].pop("orders_allowed")
    assert_policy_rejected_before_generation(
        tmp_path, "missing-permission", missing, "invalid_policy:missing_permission:orders_allowed"
    )

    type_confused = policy()
    type_confused["output_permissions"]["orders_allowed"] = 0
    assert_policy_rejected_before_generation(
        tmp_path,
        "type-confused-permission",
        type_confused,
        "invalid_policy:unsafe_permission_vector:orders_allowed",
    )


def test_temporal_policy_thresholds_must_be_finite_numeric_and_non_negative(tmp_path: Path) -> None:
    cases = (
        ("age-nan", "max_snapshot_age_minutes", float("nan"), "must_be_finite_number"),
        ("age-inf", "max_snapshot_age_minutes", float("inf"), "must_be_finite_number"),
        ("age-string", "max_snapshot_age_minutes", "nan", "must_be_finite_number"),
        ("age-negative", "max_snapshot_age_minutes", -1, "out_of_range"),
        ("skew-nan", "max_future_clock_skew_minutes", float("nan"), "must_be_finite_number"),
        ("skew-inf", "max_future_clock_skew_minutes", float("inf"), "must_be_finite_number"),
        ("skew-bool", "max_future_clock_skew_minutes", False, "must_be_finite_number"),
        ("skew-negative", "max_future_clock_skew_minutes", -1, "out_of_range"),
    )
    for name, field, value, expected in cases:
        payload = policy()
        payload[field] = value
        assert_policy_rejected_before_generation(tmp_path, name, payload, expected)


def test_required_sources_v1_contract_cannot_be_weakened_or_extended(tmp_path: Path) -> None:
    cases = (
        ("empty", []),
        ("missing-kind", ["ohlcv", "open_interest", "funding"]),
        ("duplicate", ["ohlcv", "ohlcv", "funding", "spot_flow"]),
        ("bad-item", ["ohlcv", "open_interest", 3, "spot_flow"]),
        ("unknown-kind", ["ohlcv", "open_interest", "funding", "spot_flow", "liquidations"]),
        ("whitespace", ["ohlcv", "open_interest", "funding", " spot_flow "]),
    )
    for name, required in cases:
        payload = policy()
        payload["required_sources"] = required
        assert_policy_rejected_before_generation(
            tmp_path, f"required-{name}", payload, "invalid_policy:required_sources"
        )


def test_other_numeric_policy_controls_reject_non_finite_values(tmp_path: Path) -> None:
    cases = (
        ("funding-nan", "funding_z_extreme", float("nan")),
        ("basis-inf", "basis_z_extreme", float("inf")),
        ("volume-confirm-nan", "relative_volume_confirm", float("nan")),
        ("volume-weak-inf", "relative_volume_weak", float("inf")),
    )
    for name, field, value in cases:
        payload = policy()
        payload[field] = value
        assert_policy_rejected_before_generation(
            tmp_path, name, payload, f"invalid_policy:{field}_must_be_finite_number"
        )


def test_relative_volume_threshold_order_is_validated(tmp_path: Path) -> None:
    payload = policy()
    payload["relative_volume_weak"] = payload["relative_volume_confirm"]
    assert_policy_rejected_before_generation(
        tmp_path, "volume-order", payload, "invalid_policy:relative_volume_threshold_order"
    )


def test_edge_gate_is_closed_and_numeric(tmp_path: Path) -> None:
    malformed = policy()
    malformed["edge_gate"] = []
    assert_policy_rejected_before_generation(
        tmp_path, "edge-not-object", malformed, "invalid_policy:edge_gate_must_be_object"
    )

    missing = policy()
    missing["edge_gate"].pop("minimum_score_margin")
    assert_policy_rejected_before_generation(
        tmp_path, "edge-missing", missing, "invalid_policy:edge_gate_missing_field:"
    )

    unknown = policy()
    unknown["edge_gate"]["future_gate"] = 1
    assert_policy_rejected_before_generation(
        tmp_path, "edge-unknown", unknown, "invalid_policy:edge_gate_unknown_field:"
    )

    nan_score = policy()
    nan_score["edge_gate"]["minimum_direction_score"] = float("nan")
    assert_policy_rejected_before_generation(
        tmp_path,
        "edge-nan",
        nan_score,
        "invalid_policy:edge_gate.minimum_direction_score_out_of_range",
    )

    bad_dimensions = policy()
    bad_dimensions["edge_gate"]["minimum_independent_dimensions"] = True
    assert_policy_rejected_before_generation(
        tmp_path,
        "edge-dimensions",
        bad_dimensions,
        "invalid_policy:edge_gate.minimum_independent_dimensions_out_of_range",
    )


def test_policy_identity_and_list_structure_fail_closed(tmp_path: Path) -> None:
    wrong_schema = policy()
    wrong_schema["schema_version"] = 2
    assert_policy_rejected_before_generation(
        tmp_path, "schema", wrong_schema, "invalid_policy:unsupported_schema_version"
    )

    wrong_symbol = policy()
    wrong_symbol["supported_symbol"] = "ETHUSDT"
    assert_policy_rejected_before_generation(
        tmp_path, "symbol", wrong_symbol, "invalid_policy:unsupported_symbol"
    )

    duplicate_timeframes = policy()
    duplicate_timeframes["allowed_timeframes"] = ["1h", "1h"]
    assert_policy_rejected_before_generation(
        tmp_path,
        "timeframes",
        duplicate_timeframes,
        "invalid_policy:allowed_timeframes_contains_duplicate",
    )


def test_current_canonical_policy_passes_complete_preflight() -> None:
    brief_tool.validate_policy(policy())
