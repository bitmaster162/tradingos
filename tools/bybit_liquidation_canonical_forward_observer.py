#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_context_intake as base
from tools import bybit_all_liquidation_context_intake_v2 as intake
from tools import force_order_liquidation_event_study as study
from tools import liquidation_side_semantics as semantics
from tools.liquidation_cross_venue_lead_lag_forward_observer import sha256_file, write_json


OBSERVER_PATH = Path(__file__).resolve()
DEPENDENCY_PATHS = [
    Path(intake.__file__).resolve(),
    Path(base.__file__).resolve(),
    Path(study.__file__).resolve(),
    Path(semantics.__file__).resolve(),
]


def validate_prereg(prereg: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if prereg.get("status") != "prospective_preregistration_before_forward_floor":
        failures.append("status")
    floor = base.parse_ts(prereg.get("forward_floor_at"))
    if floor is None:
        failures.append("forward_floor_at")
    candidate = prereg.get("candidate") if isinstance(prereg.get("candidate"), dict) else {}
    expected = {
        "candidate_id": "long_liquidation_flush__reversal__h8",
        "context": "long_liquidation_flush",
        "direction": "reversal",
        "side": "LONG",
        "interval": "1h",
        "horizon_bars": 8,
    }
    for key, value in expected.items():
        if candidate.get(key) != value:
            failures.append(f"candidate.{key}")
    if float(candidate.get("dominance_threshold") or 0.0) != 0.65:
        failures.append("candidate.dominance_threshold")
    if not isinstance(candidate.get("symbols"), list) or len(candidate["symbols"]) < 5:
        failures.append("candidate.symbols")
    side_contract = prereg.get("side_contract") if isinstance(prereg.get("side_contract"), dict) else {}
    if side_contract.get("raw_side_mapping") != {"BUY": "LONG", "SELL": "SHORT"}:
        failures.append("side_contract.raw_side_mapping")
    if side_contract.get("old_v1_context_rows_allowed") is not False:
        failures.append("side_contract.old_v1_context_rows_allowed")
    research = prereg.get("research_boundary") if isinstance(prereg.get("research_boundary"), dict) else {}
    for key in (
        "pre_floor_events_allowed",
        "interim_outcome_review_allowed",
        "old_v1_outcomes_admitted",
    ):
        if research.get(key) is not False:
            failures.append(f"research_boundary.{key}")
    boundary = prereg.get("runtime_boundary") if isinstance(prereg.get("runtime_boundary"), dict) else {}
    for key in ("signals_allowed", "paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary.{key}")
    if prereg.get("can_trade") is not False or prereg.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    return sorted(set(failures))


def build_lock(prereg_path: Path, *, created_at: str | None = None) -> dict[str, Any]:
    prereg = json.loads(prereg_path.read_text(encoding="utf-8-sig"))
    failures = validate_prereg(prereg)
    if failures:
        raise ValueError("invalid preregistration: " + ",".join(failures))
    created = created_at or base.now_iso()
    created_dt = base.parse_ts(created)
    floor_dt = base.parse_ts(prereg["forward_floor_at"])
    if created_dt is None or floor_dt is None or created_dt >= floor_dt:
        raise ValueError("lock must be sealed before forward_floor_at")
    discovery_path = base.resolve_path(prereg["discovery_provenance"]["report"])
    if not discovery_path.is_file():
        raise ValueError("discovery report is missing")
    return {
        "schema_version": 1,
        "lock_id": prereg["prereg_id"],
        "status": "prospective_forward_lock_before_outcome_review",
        "created_at": created,
        "forward_start_at": prereg["forward_floor_at"],
        "preregistration": {"path": base.portable(prereg_path), "sha256": sha256_file(prereg_path)},
        "observer": {"path": base.portable(OBSERVER_PATH), "sha256": sha256_file(OBSERVER_PATH)},
        "dependencies": [
            {"path": base.portable(path), "sha256": sha256_file(path)} for path in DEPENDENCY_PATHS
        ],
        "discovery_evidence": {"path": base.portable(discovery_path), "sha256": sha256_file(discovery_path)},
        "sources": prereg["sources"],
        "candidate": prereg["candidate"],
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
    if lock.get("status") != "prospective_forward_lock_before_outcome_review":
        failures.append("status")
    if base.parse_ts(lock.get("forward_start_at")) is None:
        failures.append("forward_start_at")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    boundary = lock.get("runtime_boundary") if isinstance(lock.get("runtime_boundary"), dict) else {}
    for key in ("signals_allowed", "paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary.{key}")
    side_contract = lock.get("side_contract") if isinstance(lock.get("side_contract"), dict) else {}
    if side_contract.get("raw_side_mapping") != {"BUY": "LONG", "SELL": "SHORT"}:
        failures.append("side_contract.raw_side_mapping")
    items = []
    for section in ("preregistration", "observer", "discovery_evidence"):
        item = lock.get(section) if isinstance(lock.get(section), dict) else {}
        items.append((section, item))
    dependencies = lock.get("dependencies") if isinstance(lock.get("dependencies"), list) else []
    items.extend((f"dependency_{index}", item) for index, item in enumerate(dependencies) if isinstance(item, dict))
    if len(dependencies) != len(DEPENDENCY_PATHS):
        failures.append("dependencies")
    for name, item in items:
        path = base.resolve_path(str(item.get("path") or ""))
        expected = str(item.get("sha256") or "")
        if not path.is_file() or not expected or sha256_file(path) != expected:
            failures.append(f"{name}_integrity")
    return sorted(set(failures))


def forward_start_bar(value: str, interval: str) -> str:
    parsed = base.parse_ts(value)
    if parsed is None:
        raise ValueError("invalid forward floor")
    floored = base.floor_time(parsed, interval)
    if parsed > floored:
        floored += base.parse_interval(interval)
    return floored.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_forward_records(lock: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate = lock["candidate"]
    symbols = [str(item).upper() for item in candidate["symbols"]]
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
    intake_report = intake.build_report(args)
    aggregate_rows = intake_report.pop("_aggregate_rows", [])
    start_bar = forward_start_bar(lock["forward_start_at"], candidate["interval"])
    eligible_rows = [
        row
        for row in aggregate_rows
        if row["bar_ts"] >= start_bar
        and row["matched_price_bar"] is True
        and row["dominant_context"] == candidate["context"]
        and int(row["side_semantics_version"]) == semantics.CANONICAL_SIDE_SCHEMA_VERSION
    ]
    bars_by_symbol, bar_paths = study.load_bars_by_symbol(
        symbols,
        candidate["interval"],
        base.resolve_path(lock["sources"]["bars_root"]),
    )
    records, errors = study.build_event_records(eligible_rows, bars_by_symbol, [int(candidate["horizon_bars"])])
    return records, {
        "forward_start_bar_ts": start_bar,
        "raw_events": intake_report["summary"]["events"],
        "aggregate_rows": intake_report["summary"]["aggregate_rows"],
        "eligible_event_bars": len(eligible_rows),
        "resolved_records": len(records),
        "bars_by_symbol": bar_paths,
        "errors": errors[:25],
    }


def sample_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    symbols = Counter(str(row["symbol"]) for row in records)
    blocks = {str(row["independent_4h_block"]) for row in records}
    days = {str(row["bar_ts"])[:10] for row in records}
    return {
        "resolved_events": len(records),
        "utc_days": len(days),
        "symbol_count": len(symbols),
        "independent_4h_blocks": len(blocks),
        "max_single_symbol_share": round(max(symbols.values()) / len(records), 8) if records else 0.0,
        "symbols": dict(sorted(symbols.items())),
        "first_event_bar_ts": min((str(row["bar_ts"]) for row in records), default=None),
        "last_event_bar_ts": max((str(row["bar_ts"]) for row in records), default=None),
    }


def sample_blockers(sample: dict[str, Any], gate: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if sample["resolved_events"] < int(gate["minimum_resolved_events"]):
        blockers.append("minimum_resolved_events_not_met")
    if sample["utc_days"] < int(gate["minimum_utc_days"]):
        blockers.append("minimum_utc_days_not_met")
    if sample["symbol_count"] < int(gate["minimum_symbols"]):
        blockers.append("minimum_symbols_not_met")
    if sample["independent_4h_blocks"] < int(gate["minimum_independent_4h_blocks"]):
        blockers.append("minimum_independent_4h_blocks_not_met")
    if sample["max_single_symbol_share"] > float(gate["maximum_single_symbol_share"]):
        blockers.append("maximum_single_symbol_share_exceeded")
    return blockers


def terminal_evaluation(
    records: list[dict[str, Any]], candidate: dict[str, Any], gate: dict[str, Any]
) -> tuple[str, dict[str, Any], list[str]]:
    field = f"{candidate['direction']}_return_bps"
    gross = [float(row[field]) for row in records]
    base_values = [value - float(candidate["base_cost_bps"]) for value in gross]
    stress_values = [value - float(candidate["stress_cost_bps"]) for value in gross]
    by_symbol: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(records, base_values):
        by_symbol[str(row["symbol"])].append(value)
    positive_symbols = sum(statistics.fmean(values) > 0 for values in by_symbol.values())
    metrics = {
        "n": len(records),
        "mean_gross_bps": round(statistics.fmean(gross), 6),
        "mean_net_bps": round(statistics.fmean(base_values), 6),
        "winrate_net_positive_pct": round(100.0 * sum(value > 0 for value in base_values) / len(base_values), 3),
        "stress_mean_net_bps": round(statistics.fmean(stress_values), 6),
        "positive_symbols": positive_symbols,
        "symbol_count": len(by_symbol),
    }
    checks = {
        "minimum_mean_net_bps": metrics["mean_net_bps"] >= float(gate["minimum_mean_net_bps"]),
        "minimum_winrate_net_positive_pct": metrics["winrate_net_positive_pct"] >= float(gate["minimum_winrate_net_positive_pct"]),
        "minimum_stress_mean_net_bps": metrics["stress_mean_net_bps"] >= float(gate["minimum_stress_mean_net_bps"]),
        "minimum_positive_symbols": metrics["positive_symbols"] >= int(gate["minimum_positive_symbols"]),
    }
    metrics["checks"] = checks
    failures = [name for name, passed in checks.items() if not passed]
    decision = (
        "bybit_liquidation_canonical_forward_candidate_accepted_manual_shadow_only"
        if not failures
        else "bybit_liquidation_canonical_forward_no_edge_tombstone"
    )
    return decision, metrics, failures


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Bybit Canonical Liquidation Forward Observer V2",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Forward floor: `{report['lock']['forward_start_at']}`",
        f"- Resolved events: `{report['sample']['resolved_events']}`",
        f"- Independent 4h blocks: `{report['sample']['independent_4h_blocks']}`",
        f"- Terminal: `{report['terminal']['reached']}`",
        "- Can trade: `false`",
        "",
        "## Outcome Boundary",
        "",
        f"- Interim outcomes hidden: `{report['outcome_review']['interim_outcomes_hidden']}`",
        "- Before every sample gate passes, this report exposes counts only and never return metrics.",
        "- A terminal pass permits manual shadow-design review only, not a paper or live entry.",
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
    return "\n".join(lines)


def run_observer(lock_path: Path, out_prefix: Path, terminal_receipt_path: Path) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    failures = validate_lock(lock)
    if failures:
        raise ValueError("invalid forward lock: " + ",".join(failures))
    if terminal_receipt_path.is_file():
        existing = json.loads(terminal_receipt_path.read_text(encoding="utf-8-sig"))
        if existing.get("lock_id") == lock.get("lock_id") and existing.get("terminal") is True:
            frozen = dict(existing["report"])
            frozen["generated_at"] = base.now_iso()
            frozen["terminal"]["frozen"] = True
            write_json(out_prefix.with_suffix(".json"), frozen)
            out_prefix.with_suffix(".md").write_text(render_markdown(frozen), encoding="utf-8")
            return frozen

    records, source_progress = load_forward_records(lock)
    sample = sample_summary(records)
    blockers = sample_blockers(sample, lock["sample_gate"])
    now = datetime.now(timezone.utc)
    floor = base.parse_ts(lock["forward_start_at"])
    assert floor is not None
    terminal_metrics: dict[str, Any] | None = None
    if now < floor:
        decision = "bybit_liquidation_canonical_forward_waiting_floor"
        blockers = ["forward_floor_not_reached"]
    elif blockers:
        decision = "bybit_liquidation_canonical_forward_collecting_hidden_outcomes"
    else:
        decision, terminal_metrics, outcome_failures = terminal_evaluation(
            records, lock["candidate"], lock["terminal_outcome_gate"]
        )
        blockers = outcome_failures
    terminal = decision in {
        "bybit_liquidation_canonical_forward_candidate_accepted_manual_shadow_only",
        "bybit_liquidation_canonical_forward_no_edge_tombstone",
    }
    next_action = (
        "keep the public collector and observer unchanged; do not inspect interim returns"
        if not terminal
        else (
            "manual shadow-design review is allowed; trading remains disabled"
            if decision.endswith("manual_shadow_only")
            else "tombstone this corrected formulation without reverse selection or retuning"
        )
    )
    report = {
        "generated_at": base.now_iso(),
        "tool": "tools/bybit_liquidation_canonical_forward_observer.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "lock": {
            "path": base.portable(lock_path),
            "sha256": sha256_file(lock_path),
            "lock_id": lock["lock_id"],
            "forward_start_at": lock["forward_start_at"],
        },
        "candidate": lock["candidate"],
        "source_progress": source_progress,
        "sample": sample,
        "outcome_review": {
            "interim_outcomes_hidden": not terminal,
            "terminal_metrics": terminal_metrics,
        },
        "terminal": {"reached": terminal, "frozen": False, "receipt": base.portable(terminal_receipt_path)},
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
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    if terminal:
        write_json(
            terminal_receipt_path,
            {"lock_id": lock["lock_id"], "terminal": True, "sealed_at": base.now_iso(), "report": report},
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Immutable corrected-label Bybit liquidation forward observer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser("seal-lock")
    seal.add_argument("--prereg", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V2_2026-07-13.json")
    seal.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V2_2026-07-13.json")
    seal.add_argument("--acknowledge-forward-only", action="store_true")
    run = subparsers.add_parser("run-once")
    run.add_argument("--lock", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_LOCK_V2_2026-07-13.json")
    run.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_OBSERVER_V2_2026-07-13")
    run.add_argument("--terminal-receipt", default="logs/bybit_liquidation_canonical_forward_v2/terminal_receipt.json")
    args = parser.parse_args()
    try:
        if args.command == "seal-lock":
            if not args.acknowledge_forward_only:
                raise ValueError("--acknowledge-forward-only is required")
            prereg_path = base.resolve_path(args.prereg)
            lock_path = base.resolve_path(args.lock)
            lock = build_lock(prereg_path)
            write_json(lock_path, lock)
            print(json.dumps({"decision": "bybit_canonical_forward_lock_sealed", "lock": base.portable(lock_path), "can_trade": False}, indent=2))
            return 0
        report = run_observer(
            base.resolve_path(args.lock),
            base.resolve_path(args.out_prefix),
            base.resolve_path(args.terminal_receipt),
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"decision": "bybit_canonical_forward_observer_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "resolved_events": report["sample"]["resolved_events"],
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
