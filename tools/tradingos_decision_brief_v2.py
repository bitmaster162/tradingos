#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
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


def validate_policy(policy: dict[str, Any]) -> None:
    """Fail closed unless every execution-sensitive permission is frozen safe."""

    permissions = policy.get("output_permissions")
    if not isinstance(permissions, dict):
        raise ValueError("policy output_permissions must be an object")

    required_exact = {
        "watch_stances_allowed": True,
        "signals_allowed": False,
        "orders_allowed": False,
        "credentials_allowed": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }
    violations = [
        f"{key}={permissions.get(key)!r} expected {expected!r}"
        for key, expected in required_exact.items()
        if permissions.get(key) != expected
    ]
    if violations:
        raise ValueError("unsafe policy permissions: " + "; ".join(violations))


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
