#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import binance_rest_kline_tail_gap_filler as filler
from tools import bybit_liquidation_canonical_forward_observer as v2
from tools import bybit_liquidation_canonical_input_quality as quality
from tools import liquidity_sweep_detector


OBSERVER_PATH = Path(__file__).resolve()
DEPENDENCY_PATHS = list(
    dict.fromkeys(
        [
            Path(v2.__file__).resolve(),
            *[Path(path).resolve() for path in v2.DEPENDENCY_PATHS],
            Path(quality.__file__).resolve(),
            Path(liquidity_sweep_detector.__file__).resolve(),
            Path(filler.__file__).resolve(),
        ]
    )
)


def validate_prereg(prereg: dict[str, Any]) -> list[str]:
    failures = v2.validate_prereg(prereg)
    if int(prereg.get("schema_version") or 0) != 2:
        failures.append("schema_version")
    supersedes = prereg.get("supersedes") if isinstance(prereg.get("supersedes"), dict) else {}
    if supersedes.get("v2_forward_observations_admitted") is not False:
        failures.append("supersedes.v2_forward_observations_admitted")
    if supersedes.get("v2_terminal_metrics_admitted") is not False:
        failures.append("supersedes.v2_terminal_metrics_admitted")
    bars = prereg.get("bar_contract") if isinstance(prereg.get("bar_contract"), dict) else {}
    if bars.get("fully_closed_bars_only") is not True:
        failures.append("bar_contract.fully_closed_bars_only")
    if bars.get("current_interval_bar_allowed") is not False:
        failures.append("bar_contract.current_interval_bar_allowed")
    if bars.get("public_tail_refresh_required") is not True:
        failures.append("bar_contract.public_tail_refresh_required")
    gate = prereg.get("input_quality_gate") if isinstance(prereg.get("input_quality_gate"), dict) else {}
    required_gate_fields = {
        "maximum_json_parse_errors",
        "maximum_schema_errors",
        "maximum_duplicate_event_identities",
        "maximum_partition_mismatches",
        "maximum_nonmonotonic_files",
        "maximum_negative_receipt_lags",
        "required_bar_symbols",
        "maximum_closed_bar_lag_intervals",
    }
    if not required_gate_fields.issubset(gate):
        failures.append("input_quality_gate")
    research = prereg.get("research_boundary") if isinstance(prereg.get("research_boundary"), dict) else {}
    for key in ("v2_observations_admitted", "v2_terminal_metrics_admitted"):
        if research.get(key) is not False:
            failures.append(f"research_boundary.{key}")
    if prereg.get("candidate", {}).get("exit_model") != "event_bar_plus_8_fully_closed_close":
        failures.append("candidate.exit_model")
    return sorted(set(failures))


def build_lock(prereg_path: Path, *, created_at: str | None = None) -> dict[str, Any]:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8-sig"))
    failures = validate_prereg(prereg)
    if failures:
        raise ValueError("invalid V3 preregistration: " + ",".join(failures))
    created = created_at or v2.base.now_iso()
    created_dt = v2.base.parse_ts(created)
    floor_dt = v2.base.parse_ts(prereg["forward_floor_at"])
    if created_dt is None or floor_dt is None or created_dt >= floor_dt:
        raise ValueError("lock must be sealed before forward_floor_at")
    discovery_path = v2.base.resolve_path(prereg["discovery_provenance"]["report"])
    if not discovery_path.is_file():
        raise ValueError("discovery report is missing")
    return {
        "schema_version": 2,
        "lock_id": prereg["prereg_id"],
        "status": "prospective_forward_lock_before_outcome_review",
        "created_at": created,
        "forward_start_at": prereg["forward_floor_at"],
        "preregistration": {"path": v2.base.portable(prereg_path), "sha256": v2.sha256_file(prereg_path)},
        "observer": {"path": v2.base.portable(OBSERVER_PATH), "sha256": v2.sha256_file(OBSERVER_PATH)},
        "dependencies": [
            {"path": v2.base.portable(path), "sha256": v2.sha256_file(path)} for path in DEPENDENCY_PATHS
        ],
        "discovery_evidence": {"path": v2.base.portable(discovery_path), "sha256": v2.sha256_file(discovery_path)},
        "supersedes": prereg["supersedes"],
        "sources": prereg["sources"],
        "candidate": prereg["candidate"],
        "bar_contract": prereg["bar_contract"],
        "input_quality_gate": prereg["input_quality_gate"],
        "sample_gate": prereg["sample_gate"],
        "terminal_outcome_gate": prereg["terminal_outcome_gate"],
        "side_contract": prereg["side_contract"],
        "research_boundary": prereg["research_boundary"],
        "runtime_boundary": prereg["runtime_boundary"],
        "can_trade": False,
        "orders_allowed": False,
    }


def validate_lock(lock: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if int(lock.get("schema_version") or 0) != 2:
        failures.append("schema_version")
    if lock.get("status") != "prospective_forward_lock_before_outcome_review":
        failures.append("status")
    if v2.base.parse_ts(lock.get("forward_start_at")) is None:
        failures.append("forward_start_at")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    boundary = lock.get("runtime_boundary") if isinstance(lock.get("runtime_boundary"), dict) else {}
    for key in ("signals_allowed", "paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary.{key}")
    bars = lock.get("bar_contract") if isinstance(lock.get("bar_contract"), dict) else {}
    if bars.get("fully_closed_bars_only") is not True or bars.get("current_interval_bar_allowed") is not False:
        failures.append("bar_contract")
    research = lock.get("research_boundary") if isinstance(lock.get("research_boundary"), dict) else {}
    if research.get("v2_observations_admitted") is not False or research.get("v2_terminal_metrics_admitted") is not False:
        failures.append("v2_exclusion")
    items: list[tuple[str, dict[str, Any]]] = []
    for section in ("preregistration", "observer", "discovery_evidence"):
        item = lock.get(section) if isinstance(lock.get(section), dict) else {}
        items.append((section, item))
    dependencies = lock.get("dependencies") if isinstance(lock.get("dependencies"), list) else []
    if len(dependencies) != len(DEPENDENCY_PATHS):
        failures.append("dependencies")
    items.extend((f"dependency_{index}", item) for index, item in enumerate(dependencies) if isinstance(item, dict))
    for name, item in items:
        path = v2.base.resolve_path(str(item.get("path") or ""))
        expected = str(item.get("sha256") or "")
        if not path.is_file() or not expected or v2.sha256_file(path) != expected:
            failures.append(f"{name}_integrity")
    return sorted(set(failures))


def load_forward_records(
    lock: dict[str, Any],
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    candidate = lock["candidate"]
    symbols = [str(item).upper() for item in candidate["symbols"]]
    quality_report = quality.build_quality(lock, now=now)
    args = argparse.Namespace(
        data_dir=lock["sources"]["liquidations"],
        symbol=symbols[0],
        symbols=",".join(symbols),
        interval=candidate["interval"],
        bars_csv="",
        min_events_for_research=0,
        min_event_bars_for_research=0,
        max_bad_lines=25,
    )
    intake_report = v2.intake.build_report(args)
    aggregate_rows = intake_report.pop("_aggregate_rows", [])
    start_bar = v2.forward_start_bar(lock["forward_start_at"], candidate["interval"])
    eligible_rows = [
        row
        for row in aggregate_rows
        if row["bar_ts"] >= start_bar
        and row["matched_price_bar"] is True
        and row["dominant_context"] == candidate["context"]
        and int(row["side_semantics_version"]) == v2.semantics.CANONICAL_SIDE_SCHEMA_VERSION
    ]
    bars_by_symbol, bar_paths = v2.study.load_bars_by_symbol(
        symbols,
        candidate["interval"],
        v2.base.resolve_path(lock["sources"]["bars_root"]),
    )
    closed_by_symbol: dict[str, list[Any]] = {}
    excluded_open_rows: dict[str, int] = {}
    for symbol, bars in bars_by_symbol.items():
        closed, excluded = quality.filter_fully_closed_bars(
            bars,
            now=now,
            interval=candidate["interval"],
        )
        closed_by_symbol[symbol] = closed
        excluded_open_rows[symbol] = excluded
    records, errors = v2.study.build_event_records(
        eligible_rows,
        closed_by_symbol,
        [int(candidate["horizon_bars"])],
    )
    return records, {
        "forward_start_bar_ts": start_bar,
        "closed_cutoff_bar_open": quality_report["bars"]["closed_cutoff_bar_open"],
        "raw_events": intake_report["summary"]["events"],
        "post_floor_raw_events": quality_report["events"]["post_floor_events"],
        "aggregate_rows": intake_report["summary"]["aggregate_rows"],
        "eligible_event_bars": len(eligible_rows),
        "resolved_records": len(records),
        "bars_by_symbol": bar_paths,
        "open_or_invalid_bar_rows_excluded": excluded_open_rows,
        "errors": errors[:25],
    }, quality_report


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Bybit Canonical Liquidation Forward Observer V3",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Forward floor: `{report['lock']['forward_start_at']}`",
            f"- Resolved events: `{report['sample']['resolved_events']}`",
            f"- Independent 4h blocks: `{report['sample']['independent_4h_blocks']}`",
            f"- Input quality: `{report['input_quality']['decision']}`",
            f"- Closed cutoff: `{report['source_progress']['closed_cutoff_bar_open']}`",
            f"- Interim outcomes hidden: `{report['outcome_review']['interim_outcomes_hidden']}`",
            f"- Terminal: `{report['terminal']['reached']}`",
            "- V2 observations admitted: `false`",
            "- Can trade: `false`",
            "",
            "## Blockers",
            "",
            *[f"- `{item}`" for item in report["blockers"]],
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )


def run_observer(
    lock_path: Path,
    out_prefix: Path,
    terminal_receipt_path: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    failures = validate_lock(lock)
    if failures:
        raise ValueError("invalid V3 forward lock: " + ",".join(failures))
    if terminal_receipt_path.is_file():
        existing = json.loads(terminal_receipt_path.read_text(encoding="utf-8-sig"))
        if existing.get("lock_id") == lock.get("lock_id") and existing.get("terminal") is True:
            frozen = dict(existing["report"])
            frozen["generated_at"] = observed_at.isoformat(timespec="seconds").replace("+00:00", "Z")
            frozen["terminal"]["frozen"] = True
            v2.write_json(out_prefix.with_suffix(".json"), frozen)
            out_prefix.with_suffix(".md").write_text(render_markdown(frozen), encoding="utf-8")
            return frozen

    records, source_progress, quality_report = load_forward_records(lock, now=observed_at)
    sample = v2.sample_summary(records)
    sample_blockers = v2.sample_blockers(sample, lock["sample_gate"])
    quality_blockers = [f"input_quality:{item}" for item in quality_report["hard_failures"]]
    floor = v2.base.parse_ts(lock["forward_start_at"])
    assert floor is not None
    terminal_metrics: dict[str, Any] | None = None
    if observed_at < floor:
        decision = "bybit_liquidation_canonical_v3_waiting_floor"
        blockers = ["forward_floor_not_reached", *quality_blockers]
    elif quality_blockers:
        decision = "bybit_liquidation_canonical_v3_blocked_input_quality"
        blockers = quality_blockers
    elif sample_blockers:
        decision = "bybit_liquidation_canonical_v3_collecting_hidden_outcomes"
        blockers = sample_blockers
    else:
        v2_decision, terminal_metrics, outcome_failures = v2.terminal_evaluation(
            records,
            lock["candidate"],
            lock["terminal_outcome_gate"],
        )
        decision = (
            "bybit_liquidation_canonical_v3_candidate_accepted_manual_shadow_only"
            if v2_decision.endswith("accepted_manual_shadow_only")
            else "bybit_liquidation_canonical_v3_no_edge_tombstone"
        )
        blockers = outcome_failures
    terminal = decision in {
        "bybit_liquidation_canonical_v3_candidate_accepted_manual_shadow_only",
        "bybit_liquidation_canonical_v3_no_edge_tombstone",
    }
    next_action = (
        "keep the public refresh, collector and V3 observer unchanged; do not inspect interim returns"
        if not terminal
        else (
            "manual shadow-design review is allowed; trading remains disabled"
            if decision.endswith("manual_shadow_only")
            else "tombstone this formulation without reverse selection or retuning"
        )
    )
    report = {
        "generated_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": "tools/bybit_liquidation_canonical_forward_observer_v3.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "lock": {
            "path": v2.base.portable(lock_path),
            "sha256": v2.sha256_file(lock_path),
            "lock_id": lock["lock_id"],
            "forward_start_at": lock["forward_start_at"],
        },
        "supersedes": lock["supersedes"],
        "candidate": lock["candidate"],
        "bar_contract": lock["bar_contract"],
        "source_progress": source_progress,
        "input_quality": quality_report,
        "sample": sample,
        "outcome_review": {
            "interim_outcomes_hidden": not terminal,
            "terminal_metrics": terminal_metrics,
        },
        "terminal": {"reached": terminal, "frozen": False, "receipt": v2.base.portable(terminal_receipt_path)},
        "blockers": sorted(set(blockers)),
        "next_action": next_action,
        "runtime_boundary": {
            "observer_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }
    v2.write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    if terminal:
        v2.write_json(
            terminal_receipt_path,
            {"lock_id": lock["lock_id"], "terminal": True, "sealed_at": v2.base.now_iso(), "report": report},
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Immutable corrected-label, fully-closed-bars Bybit liquidation forward observer V3")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser("seal-lock")
    seal.add_argument("--prereg", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V3_2026-07-13.json")
    seal.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V3_2026-07-13.json")
    seal.add_argument("--acknowledge-forward-only", action="store_true")
    run = subparsers.add_parser("run-once")
    run.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V3_2026-07-13.json")
    run.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V3_2026-07-13")
    run.add_argument("--terminal-receipt", default="logs/bybit_liquidation_canonical_forward_v3/terminal_receipt.json")
    args = parser.parse_args()
    try:
        if args.command == "seal-lock":
            if not args.acknowledge_forward_only:
                raise ValueError("--acknowledge-forward-only is required")
            prereg_path = v2.base.resolve_path(args.prereg)
            lock_path = v2.base.resolve_path(args.lock)
            lock = build_lock(prereg_path)
            v2.write_json(lock_path, lock)
            print(json.dumps({"decision": "bybit_canonical_v3_forward_lock_sealed", "lock": v2.base.portable(lock_path), "can_trade": False}, indent=2))
            return 0
        report = run_observer(
            v2.base.resolve_path(args.lock),
            v2.base.resolve_path(args.out_prefix),
            v2.base.resolve_path(args.terminal_receipt),
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"decision": "bybit_canonical_v3_observer_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "resolved_events": report["sample"]["resolved_events"],
                "input_quality": report["input_quality"]["decision"],
                "terminal": report["terminal"]["reached"],
                "interim_outcomes_hidden": report["outcome_review"]["interim_outcomes_hidden"],
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
