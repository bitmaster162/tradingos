#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_PATH = Path(__file__).with_name("tradingos_decision_brief.py")
SPEC = importlib.util.spec_from_file_location("_tradingos_decision_brief_base_v2", BASE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import failure is fatal
    raise RuntimeError(f"cannot load base decision brief generator: {BASE_PATH}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)

GENERATOR_VERSION = "2.0.0"
GENERATOR_PATH = "tools/tradingos_decision_brief_v2.py"
base.GENERATOR_VERSION = GENERATOR_VERSION

_BASE_VALIDATE_SNAPSHOT = base.validate_snapshot
_BASE_GENERATE = base.generate

POLICY_SCHEMA_VERSION = 1
POLICY_ID = "TRADINGOS_DECISION_BRIEF_POLICY_V1"
SUPPORTED_SYMBOL = "BTCUSDT"
REQUIRED_SOURCES = ("ohlcv", "open_interest", "funding", "spot_flow")
SAFE_PERMISSIONS = {
    "watch_stances_allowed": True,
    "signals_allowed": False,
    "orders_allowed": False,
    "credentials_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
}
EDGE_GATE_FIELDS = {
    "minimum_direction_score",
    "minimum_score_margin",
    "minimum_independent_dimensions",
}


def _policy_error(code: str) -> ValueError:
    return ValueError(f"invalid_policy:{code}")


def _string_list(policy: dict[str, Any], field: str) -> list[str]:
    value = policy.get(field)
    if not isinstance(value, list) or not value:
        raise _policy_error(f"{field}_must_be_non_empty_list")
    if any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in value):
        raise _policy_error(f"{field}_contains_invalid_item")
    if len(value) != len(set(value)):
        raise _policy_error(f"{field}_contains_duplicate")
    return value


def _finite_number(
    policy: dict[str, Any],
    field: str,
    *,
    minimum: float = 0.0,
    strictly_greater: bool = False,
) -> float:
    value = policy.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _policy_error(f"{field}_must_be_finite_number")
    number = float(value)
    if not math.isfinite(number):
        raise _policy_error(f"{field}_must_be_finite_number")
    if strictly_greater:
        if number <= minimum:
            raise _policy_error(f"{field}_out_of_range")
    elif number < minimum:
        raise _policy_error(f"{field}_out_of_range")
    return number


def validate_policy(policy: dict[str, Any]) -> None:
    """Validate the complete v1 decision-control policy before generation.

    The base generator consumes policy values directly. This wrapper therefore
    treats the policy as untrusted control data and rejects malformed, non-finite,
    unknown, or semantically weakened values before `_BASE_GENERATE` can create
    any output.
    """

    if not isinstance(policy, dict):
        raise _policy_error("root_not_object")

    schema_version = policy.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != POLICY_SCHEMA_VERSION:
        raise _policy_error("unsupported_schema_version")
    if policy.get("policy_id") != POLICY_ID:
        raise _policy_error("unsupported_policy_id")
    if policy.get("supported_symbol") != SUPPORTED_SYMBOL:
        raise _policy_error("unsupported_symbol")

    required_sources = _string_list(policy, "required_sources")
    if set(required_sources) != set(REQUIRED_SOURCES) or len(required_sources) != len(REQUIRED_SOURCES):
        raise _policy_error("required_sources_contract_mismatch")

    _string_list(policy, "allowed_timeframes")

    permissions = policy.get("output_permissions")
    if not isinstance(permissions, dict):
        raise ValueError("policy output_permissions must be an object")

    permission_keys = set(permissions)
    expected_keys = set(SAFE_PERMISSIONS)
    missing = sorted(expected_keys - permission_keys)
    if missing:
        raise _policy_error("missing_permission:" + ",".join(missing))
    unknown = sorted(permission_keys - expected_keys)
    if unknown:
        raise _policy_error("unsupported_permission:" + ",".join(unknown))

    violations = [
        key
        for key, expected in SAFE_PERMISSIONS.items()
        if type(permissions.get(key)) is not type(expected) or permissions.get(key) != expected
    ]
    if violations:
        raise ValueError(
            "unsafe policy permissions: invalid_policy:unsafe_permission_vector:"
            + ",".join(sorted(violations))
        )

    _finite_number(policy, "max_snapshot_age_minutes", minimum=0.0)
    _finite_number(policy, "max_future_clock_skew_minutes", minimum=0.0)
    _finite_number(policy, "funding_z_extreme", minimum=0.0, strictly_greater=True)
    _finite_number(policy, "basis_z_extreme", minimum=0.0, strictly_greater=True)
    relative_confirm = _finite_number(
        policy, "relative_volume_confirm", minimum=0.0, strictly_greater=True
    )
    relative_weak = _finite_number(policy, "relative_volume_weak", minimum=0.0)
    if relative_weak >= relative_confirm:
        raise _policy_error("relative_volume_threshold_order")

    edge_gate = policy.get("edge_gate")
    if not isinstance(edge_gate, dict):
        raise _policy_error("edge_gate_must_be_object")
    edge_keys = set(edge_gate)
    missing_edge = sorted(EDGE_GATE_FIELDS - edge_keys)
    if missing_edge:
        raise _policy_error("edge_gate_missing_field:" + ",".join(missing_edge))
    unknown_edge = sorted(edge_keys - EDGE_GATE_FIELDS)
    if unknown_edge:
        raise _policy_error("edge_gate_unknown_field:" + ",".join(unknown_edge))

    for field in ("minimum_direction_score", "minimum_score_margin"):
        value = edge_gate.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _policy_error(f"edge_gate.{field}_must_be_finite_number")
        number = float(value)
        if not math.isfinite(number) or number < 0.0:
            raise _policy_error(f"edge_gate.{field}_out_of_range")

    dimensions = edge_gate.get("minimum_independent_dimensions")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
        raise _policy_error("edge_gate.minimum_independent_dimensions_out_of_range")


def _source_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    provenance = base.nested(snapshot, "provenance")
    rows = provenance.get("sources")
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)]


def provenance_gate(
    snapshot: dict[str, Any],
    policy: dict[str, Any],
    now: datetime,
) -> dict[str, list[str]]:
    """Validate source identity, cross-kind uniqueness, and freshness fail-closed."""

    blockers: list[str] = []
    missing_data: list[str] = []
    conflicts: list[str] = []
    rows = _source_rows(snapshot)
    required = [str(item) for item in policy.get("required_sources", [])]
    max_age = float(policy["max_snapshot_age_minutes"])
    max_future_skew = float(policy["max_future_clock_skew_minutes"])

    try:
        snapshot_as_of = base.parse_time(snapshot.get("as_of"), "as_of")
    except (TypeError, ValueError):
        snapshot_as_of = None

    # Reviewed WP001 invariant: across different required provenance kinds,
    # one normalized non-empty source_id may identify at most one kind.
    # Register it before timestamp parsing so malformed timestamps cannot hide
    # cross-kind source reuse.
    kinds_by_source_id: dict[str, set[str]] = {}

    for kind in required:
        matches = [item for item in rows if str(item.get("kind", "")) == kind]
        if not matches:
            continue  # base validator already records missing provenance kind
        if len(matches) != 1:
            blockers.append(f"ambiguous_provenance:{kind}")
            conflicts.append(f"duplicate_provenance_kind:{kind}")
            continue

        item = matches[0]
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            blockers.append(f"missing_provenance_source_id:{kind}")
            missing_data.append(f"provenance_source_id:{kind}")
        else:
            kinds_by_source_id.setdefault(source_id.strip(), set()).add(kind)

        observed_raw = item.get("observed_at")
        try:
            observed_at = base.parse_time(observed_raw, f"provenance[{kind}].observed_at")
        except (TypeError, ValueError):
            blockers.append(f"missing_or_invalid_provenance_timestamp:{kind}")
            missing_data.append(f"provenance_timestamp:{kind}")
            continue

        age_minutes = (now - observed_at).total_seconds() / 60.0
        if age_minutes > max_age:
            blockers.append(f"stale_provenance:{kind}")
            missing_data.append(f"fresh_provenance:{kind}")
        if age_minutes < -max_future_skew:
            blockers.append(f"future_provenance_clock_skew:{kind}")
            conflicts.append(f"future_provenance:{kind}")

        if snapshot_as_of is not None:
            ahead_of_snapshot = (observed_at - snapshot_as_of).total_seconds() / 60.0
            if ahead_of_snapshot > max_future_skew:
                blockers.append(f"provenance_after_snapshot:{kind}")
                conflicts.append(f"provenance_after_snapshot:{kind}")

    for source_id, kinds in kinds_by_source_id.items():
        if len(kinds) > 1:
            blockers.append(f"reused_provenance_source_id:{source_id}")
            conflicts.append(f"source_id_shared_across_kinds:{source_id}")

    return {
        "blockers": sorted(set(blockers)),
        "missing_data": sorted(set(missing_data)),
        "conflicts": sorted(set(conflicts)),
    }


def validate_snapshot(
    snapshot: dict[str, Any],
    policy: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    result = _BASE_VALIDATE_SNAPSHOT(snapshot, policy, now)
    provenance = provenance_gate(snapshot, policy, now)
    result["blockers"] = sorted(set(result["blockers"]) | set(provenance["blockers"]))
    result["missing_data"] = sorted(
        set(result["missing_data"]) | set(provenance["missing_data"])
    )
    result["conflicts"] = sorted(set(result["conflicts"]) | set(provenance["conflicts"]))
    result["passed"] = not result["blockers"]
    return result


def generate(
    input_path: Path,
    out_dir: Path,
    policy_path: Path,
    now: datetime,
    pilot_log: Path | None = None,
    pilot_day: str | None = None,
):
    policy = base.read_json(policy_path)
    validate_policy(policy)

    brief, paths, pilot_status = _BASE_GENERATE(
        input_path,
        out_dir,
        policy_path,
        now,
        pilot_log,
        pilot_day,
    )
    brief["provenance"]["generator"] = GENERATOR_PATH
    brief["provenance"]["generator_version"] = GENERATOR_VERSION
    brief["provenance"]["generator_sha256"] = base.sha256_file(Path(__file__))
    brief["provenance"]["base_generator"] = "tools/tradingos_decision_brief.py"
    brief["provenance"]["base_generator_sha256"] = base.sha256_file(BASE_PATH)

    base.write_json(paths["json"], brief)
    base.write_text(paths["markdown"], base.render_markdown(brief))
    base.write_text(paths["html"], base.render_html(brief))
    return brief, paths, pilot_status


# The base builder resolves these globals at runtime, so patch them only after
# retaining immutable references to the v1 implementations above.
base.validate_snapshot = validate_snapshot
base.generate = generate


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
