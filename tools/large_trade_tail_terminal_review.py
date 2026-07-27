#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "HANDOFF" / "INCOMING" / "codex" / "20260711_large_trade_tail_forward"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_report(source_root: Path) -> dict[str, Any]:
    observer_path = source_root / "observer.py"
    prereg_path = source_root / "PREREG.json"
    lock_path = source_root / "IMMUTABLE_LOCK.json"
    latest_path = source_root / "runtime" / "LATEST.json"
    events_path = source_root / "runtime" / "events.jsonl"
    outcomes_path = source_root / "runtime" / "outcomes.jsonl"

    prereg = read_json(prereg_path)
    lock = read_json(lock_path)
    latest = read_json(latest_path)
    horizons = [str(int(value)) + "m" for value in ((prereg.get("outcomes") or {}).get("horizons_minutes") or [])]
    summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
    latest_boundary = latest.get("runtime_boundary") if isinstance(latest.get("runtime_boundary"), dict) else {}
    minimum = int(((prereg.get("outcomes") or {}).get("minimum_resolved_events_per_horizon")) or 0)

    integrity_checks = {
        "source_files_present": all(path.is_file() for path in (observer_path, prereg_path, lock_path, latest_path)),
        "hypothesis_matches": bool(prereg.get("hypothesis_id"))
        and prereg.get("hypothesis_id") == lock.get("hypothesis_id") == latest.get("hypothesis_id"),
        "observer_hash_matches_lock": sha256_file(observer_path) == lock.get("script_sha256"),
        "prereg_hash_matches_lock": sha256_file(prereg_path) == lock.get("prereg_sha256"),
        "retuning_disabled": lock.get("retuning_allowed") is False,
        "orders_disabled": lock.get("orders_allowed") is False
        and (latest.get("orders_allowed") is False or latest_boundary.get("orders_allowed") is False),
        "can_trade_disabled": lock.get("can_trade") is False and latest.get("can_trade") is False,
    }
    sample_checks = {
        horizon: bool((summary.get(horizon) or {}).get("threshold_ready"))
        and int((summary.get(horizon) or {}).get("resolved") or 0) >= minimum
        for horizon in horizons
    }
    economics = {
        horizon: {
            "resolved": (summary.get(horizon) or {}).get("resolved"),
            "mean_net_base_bps": (summary.get(horizon) or {}).get("mean_net_base_bps"),
            "mean_net_stress_bps": (summary.get(horizon) or {}).get("mean_net_stress_bps"),
            "base_winrate_pct": (summary.get(horizon) or {}).get("base_winrate_pct"),
        }
        for horizon in horizons
    }
    all_nonpositive = bool(horizons) and all(
        float(economics[horizon]["mean_net_base_bps"]) <= 0.0
        and float(economics[horizon]["mean_net_stress_bps"]) <= 0.0
        for horizon in horizons
        if economics[horizon]["mean_net_base_bps"] is not None
        and economics[horizon]["mean_net_stress_bps"] is not None
    ) and all(
        economics[horizon]["mean_net_base_bps"] is not None
        and economics[horizon]["mean_net_stress_bps"] is not None
        for horizon in horizons
    )
    integrity_ok = all(integrity_checks.values())
    sample_ready = bool(sample_checks) and all(sample_checks.values())

    if not integrity_ok:
        decision = "large_trade_tail_terminal_review_integrity_blocked"
        status = "blocked"
        next_action = "repair provenance or lock mismatch; do not interpret outcomes"
    elif not sample_ready:
        decision = "large_trade_tail_terminal_review_waiting_sample"
        status = "waiting_forward"
        next_action = "keep the immutable observer unchanged until every registered horizon reaches the fixed sample floor"
    elif all_nonpositive:
        decision = "reject_large_trade_tail_nonpositive_forward_economics_tombstone"
        status = "tombstoned_no_retune"
        next_action = "stop the observer and do not reverse, retune or rename this hypothesis on the opened sample"
    else:
        decision = "large_trade_tail_terminal_review_manual_economics_review"
        status = "manual_review_only"
        next_action = "review the immutable evidence manually; this tool never promotes a strategy"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": "tools/large_trade_tail_terminal_review.py",
        "hypothesis_id": prereg.get("hypothesis_id"),
        "family": prereg.get("family"),
        "decision": decision,
        "status": status,
        "review_rule": "terminal rejection only when every preregistered horizon is sample-ready and net expectancy is non-positive under both base and stress costs; no automatic pass is possible",
        "integrity_checks": integrity_checks,
        "sample_checks": sample_checks,
        "sample_ready": sample_ready,
        "economics": economics,
        "all_registered_horizons_nonpositive": all_nonpositive,
        "evidence_hashes": {
            "observer_sha256": sha256_file(observer_path),
            "prereg_sha256": sha256_file(prereg_path),
            "lock_sha256": sha256_file(lock_path),
            "latest_sha256": sha256_file(latest_path),
            "events_sha256": sha256_file(events_path),
            "outcomes_sha256": sha256_file(outcomes_path),
        },
        "source_paths": {
            "root": portable(source_root),
            "prereg": portable(prereg_path),
            "lock": portable(lock_path),
            "latest": portable(latest_path),
        },
        "runtime_boundary": {
            "automatic_promotion_allowed": False,
            "retuning_allowed": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "next_action": next_action,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Large-Trade Tail Terminal Review",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Hypothesis: `{report.get('hypothesis_id')}`",
        f"- Decision: `{report['decision']}`",
        f"- Status: `{report['status']}`",
        "- Can trade: `false`",
        "",
        "## Evidence",
        "",
        "| Horizon | Resolved | Mean net base, bps | Mean net stress, bps | Base winrate |",
        "|---|---:|---:|---:|---:|",
    ]
    for horizon, row in report.get("economics", {}).items():
        lines.append(
            f"| `{horizon}` | {row.get('resolved')} | {row.get('mean_net_base_bps')} | "
            f"{row.get('mean_net_stress_bps')} | {row.get('base_winrate_pct')}% |"
        )
    lines.extend(
        [
            "",
            "## Decision Rule",
            "",
            f"- {report['review_rule']}.",
            "- This is a conservative necessary-condition rejection, not a post-hoc search for a winning horizon.",
            "- The opposite direction, new thresholds and renamed variants are not tested on this opened sample.",
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed terminal review for the isolated large-trade-tail observer")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE))
    parser.add_argument("--out-prefix", default="docs/LARGE_TRADE_TAIL_TERMINAL_REVIEW_2026-07-13")
    args = parser.parse_args()

    report = build_report(resolve_path(args.source_root))
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "status": report["status"], "out": portable(out.with_suffix('.json')), "can_trade": False}, ensure_ascii=False, indent=2))
    return 0 if "integrity_blocked" not in report["decision"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
