#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import liquidation_cross_venue_canonical_paired_leadership_forward_observer as v3
from tools import liquidation_cross_venue_lead_lag_forward_observer as base
from tools import liquidation_cross_venue_paired_leadership_forward_observer as paired


OBSERVER_PATH = Path(__file__).resolve()
DEPENDENCY_PATHS = [Path(v3.__file__).resolve(), Path(paired.__file__).resolve(), Path(base.__file__).resolve()]
PACKET_IDENTITY = "collector_session_id+packet_sequence+packet_item_index"


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_operational_contract(payload: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    rules = payload.get("fixed_rules") if isinstance(payload.get("fixed_rules"), dict) else {}
    schemas = rules.get("required_ingest_schema_versions")
    if schemas != {"binance": 2, "bybit": 4}:
        failures.append("fixed_rules.required_ingest_schema_versions")
    sources = rules.get("required_sources")
    if sources != {
        "binance": "binance_usdm_forceOrder_websocket",
        "bybit": "bybit_v5_allLiquidation_websocket",
    }:
        failures.append("fixed_rules.required_sources")
    if rules.get("bybit_packet_identity") != PACKET_IDENTITY:
        failures.append("fixed_rules.bybit_packet_identity")
    if rules.get("bybit_market_tuple_deduplication") is not False:
        failures.append("fixed_rules.bybit_market_tuple_deduplication")
    boundary = payload.get("research_boundary") if isinstance(payload.get("research_boundary"), dict) else {}
    for key in ("v3_observations_admitted", "pre_v4_bybit_rows_admitted", "outcomes_inherited_from_v3"):
        if boundary.get(key) is not False:
            failures.append(f"research_boundary.{key}")
    return failures


def _validate_parent_inheritance(prereg: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    supersedes = prereg.get("supersedes") if isinstance(prereg.get("supersedes"), dict) else {}
    parent_path = base.resolve_path(str(supersedes.get("preregistration_path") or ""))
    expected_hash = str(supersedes.get("preregistration_sha256") or "")
    if not parent_path.is_file() or not expected_hash or base.sha256_file(parent_path) != expected_hash:
        return ["supersedes.preregistration_integrity"]
    if supersedes.get("strategy_parameters_changed") is not False:
        failures.append("supersedes.strategy_parameters_changed")
    if supersedes.get("outcomes_admitted") is not False:
        failures.append("supersedes.outcomes_admitted")
    parent = base.read_json(parent_path)
    if prereg.get("shared_symbols") != parent.get("shared_symbols"):
        failures.append("shared_symbols_changed")
    if prereg.get("source_semantics") != parent.get("source_semantics"):
        failures.append("source_semantics_changed")
    if prereg.get("terminal_gate") != parent.get("terminal_gate"):
        failures.append("terminal_gate_changed")
    inherited_rule_names = (
        "required_collector_host",
        "match_dimensions",
        "pair_windows_seconds",
        "primary_window_seconds",
        "pairing",
        "exact_receipt_ties",
        "unmatched_events",
        "resolution",
        "notional_usage",
        "pre_floor_events",
        "no_parameter_changes",
    )
    rules = prereg.get("fixed_rules") if isinstance(prereg.get("fixed_rules"), dict) else {}
    parent_rules = parent.get("fixed_rules") if isinstance(parent.get("fixed_rules"), dict) else {}
    for name in inherited_rule_names:
        if rules.get(name) != parent_rules.get(name):
            failures.append(f"fixed_rules.{name}_changed")
    return failures


def validate_prereg(prereg: dict[str, Any]) -> list[str]:
    failures = v3.validate_prereg(prereg)
    failures.extend(_validate_operational_contract(prereg))
    failures.extend(_validate_parent_inheritance(prereg))
    return sorted(set(failures))


def build_lock(prereg_path: Path, *, created_at: str | None = None) -> dict[str, Any]:
    prereg = base.read_json(prereg_path)
    failures = validate_prereg(prereg)
    if failures:
        raise ValueError("invalid preregistration: " + ",".join(failures))
    created = created_at or base.now_iso()
    created_ns = base.parse_iso_ns(created)
    floor_ns = base.parse_iso_ns(prereg["forward_floor_at"])
    if created_ns is None or floor_ns is None or created_ns >= floor_ns:
        raise ValueError("lock must be sealed before forward_floor_at")
    return {
        "schema_version": 4,
        "lock_id": prereg["prereg_id"],
        "status": "prospective_forward_lock_before_outcome_review",
        "created_at": created,
        "forward_start_at": prereg["forward_floor_at"],
        "preregistration": {"path": base.portable(prereg_path), "sha256": base.sha256_file(prereg_path)},
        "observer": {"path": base.portable(OBSERVER_PATH), "sha256": base.sha256_file(OBSERVER_PATH)},
        "dependencies": [
            {"path": base.portable(path), "sha256": base.sha256_file(path)} for path in DEPENDENCY_PATHS
        ],
        "supersedes": prereg["supersedes"],
        "sources": prereg["sources"],
        "source_semantics": prereg["source_semantics"],
        "shared_symbols": prereg["shared_symbols"],
        "fixed_rules": prereg["fixed_rules"],
        "terminal_gate": prereg["terminal_gate"],
        "research_boundary": prereg["research_boundary"],
        "runtime_boundary": prereg["runtime_boundary"],
        "can_trade": False,
        "orders_allowed": False,
    }


def validate_lock(lock: dict[str, Any]) -> list[str]:
    failures = v3.validate_lock(lock)
    failures.extend(_validate_operational_contract(lock))
    supersedes = lock.get("supersedes") if isinstance(lock.get("supersedes"), dict) else {}
    if supersedes.get("strategy_parameters_changed") is not False:
        failures.append("supersedes.strategy_parameters_changed")
    if supersedes.get("outcomes_admitted") is not False:
        failures.append("supersedes.outcomes_admitted")
    return sorted(set(failures))


def load_events(
    venue: str,
    root: Path,
    *,
    floor_ns: int,
    symbols: set[str],
    required_host: str,
    required_schema_version: int,
    required_source: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counters: Counter[str] = Counter()
    events: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for path, line_number, row in base.iter_jsonl(root):
        if row is None:
            counters["invalid_json_or_io"] += 1
            continue
        received_ns = _integer(row.get("received_at_ns"))
        if received_ns is None:
            counters["missing_received_at_ns"] += 1
            continue
        if received_ns < floor_ns:
            counters["before_forward_floor"] += 1
            continue
        symbol = str(row.get("symbol") or "").upper()
        side = str(row.get("side") or "").upper()
        if symbol not in symbols or side not in {"BUY", "SELL"}:
            counters["outside_fixed_universe_or_side"] += 1
            continue
        if row.get("is_real_liquidation_feed") is not True:
            counters["not_real_liquidation_feed"] += 1
            continue
        if _integer(row.get("ingest_schema_version")) != required_schema_version:
            counters["wrong_schema_version"] += 1
            continue
        if str(row.get("source") or "") != required_source:
            counters["wrong_source"] += 1
            continue
        if str(row.get("collector_host") or "") != required_host:
            counters["wrong_or_missing_collector_host"] += 1
            continue
        if venue == "bybit":
            session_id = str(row.get("collector_session_id") or "")
            packet_sequence = _integer(row.get("packet_sequence"))
            item_index = _integer(row.get("packet_item_index"))
            item_count = _integer(row.get("packet_item_count"))
            if (
                not session_id
                or packet_sequence is None
                or item_index is None
                or item_count is None
                or item_count < 1
                or item_index < 0
                or item_index >= item_count
            ):
                counters["invalid_packet_item_identity"] += 1
                continue
            key = (venue, session_id, packet_sequence, item_index)
        else:
            key = base.source_event_key(venue, row)
        if key in seen:
            counters["duplicate_physical_event"] += 1
            continue
        seen.add(key)
        events.append(
            {
                "venue": venue,
                "symbol": symbol,
                "side": side,
                "received_at_ns": received_ns,
                "received_at": base.iso_from_ns(received_ns),
                "notional_usd": max(0.0, float(row.get("notional_usd") or 0.0)),
                "source_path": base.portable(path),
                "source_line": line_number,
            }
        )
        counters["accepted"] += 1
    events.sort(key=lambda item: (item["received_at_ns"], item["symbol"], item["side"]))
    return events, dict(sorted(counters.items()))


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Cross-Venue Canonical Paired Receipt Leadership Forward Observer V4",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Forward floor: `{report['lock']['forward_start_at']}`",
        "- Binance ingest schema: `2`",
        "- Bybit ingest schema: `4`",
        f"- Bybit physical identity: `{PACKET_IDENTITY}`",
        "- V3 observations inherited: `false`",
        "- Can trade: `false`",
        "",
        "| Window | Matched pairs | Binance first | Binance share | Bybit first | Bybit share |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for seconds, item in report["windows_seconds"].items():
        leaders = item["leader"]
        lines.append(
            f"| `{seconds}s` | `{item['matched_pairs']}` | `{leaders['binance']['leader_count']}` | "
            f"`{leaders['binance']['leader_share']}` | `{leaders['bybit']['leader_count']}` | "
            f"`{leaders['bybit']['leader_share']}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Analytical windows, pairing rule and terminal gate are inherited unchanged from V3.",
            "- The rollover changes only the ingest/source/physical-identity contract.",
            "- No V3 observation or outcome is admitted; only post-floor rows are eligible.",
            "- No price outcome, signal, paper entry, credential or order is used.",
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
    return "\n".join(lines)


def run_observer(lock_path: Path, out_prefix: Path, terminal_receipt_path: Path) -> dict[str, Any]:
    lock = base.read_json(lock_path)
    failures = validate_lock(lock)
    if failures:
        raise ValueError("invalid forward lock: " + ",".join(failures))
    existing_terminal = base.read_json(terminal_receipt_path)
    if existing_terminal.get("lock_id") == lock.get("lock_id") and existing_terminal.get("terminal") is True:
        frozen = dict(existing_terminal["report"])
        frozen["generated_at"] = base.now_iso()
        frozen["terminal"]["frozen"] = True
        base.write_json(out_prefix.with_suffix(".json"), frozen)
        out_prefix.with_suffix(".md").write_text(render_markdown(frozen), encoding="utf-8")
        return frozen

    floor_ns = base.parse_iso_ns(lock["forward_start_at"])
    assert floor_ns is not None
    current_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    rules = lock["fixed_rules"]
    windows_seconds = [int(value) for value in rules["pair_windows_seconds"]]
    max_window_ns = max(windows_seconds) * 1_000_000_000
    symbols = {str(value).upper() for value in lock["shared_symbols"]}
    schemas = rules["required_ingest_schema_versions"]
    sources = rules["required_sources"]
    common = {"floor_ns": floor_ns, "symbols": symbols, "required_host": str(rules["required_collector_host"])}
    binance_raw, binance_counters = load_events(
        "binance",
        base.resolve_path(lock["sources"]["binance"]),
        required_schema_version=int(schemas["binance"]),
        required_source=str(sources["binance"]),
        **common,
    )
    bybit_raw, bybit_counters = load_events(
        "bybit",
        base.resolve_path(lock["sources"]["bybit"]),
        required_schema_version=int(schemas["bybit"]),
        required_source=str(sources["bybit"]),
        **common,
    )
    binance = v3.canonicalize_events("binance", binance_raw)
    bybit = v3.canonicalize_events("bybit", bybit_raw)
    if binance and bybit:
        latest_common = min(binance[-1]["received_at_ns"], bybit[-1]["received_at_ns"], current_ns)
        cutoff_ns = latest_common - max_window_ns
    else:
        cutoff_ns = floor_ns - 1
    pairs = paired.build_pairs(binance, bybit, cutoff_ns=cutoff_ns, maximum_window_ns=max_window_ns)
    windows = paired.summarize_pairs(pairs, windows_seconds)
    sample = paired.primary_sample(pairs, int(rules["primary_window_seconds"]))
    if current_ns < floor_ns:
        decision = "liquidation_cross_venue_paired_leadership_waiting_forward_floor"
        blockers = ["forward_floor_not_reached"]
        evidence: dict[str, Any] = {}
    else:
        decision, blockers, evidence = paired.evaluate_terminal(sample, windows, lock["terminal_gate"])
    terminal = decision in {
        "liquidation_cross_venue_paired_leadership_candidate_for_manual_price_impact_preregistration",
        "liquidation_cross_venue_paired_leadership_no_stable_leader_tombstone",
    }
    next_action = (
        "keep both public collectors and this V4 observer running without parameter changes"
        if not terminal
        else (
            "manually preregister a separate forward-only price-impact test with a new future floor"
            if decision.endswith("manual_price_impact_preregistration")
            else "tombstone this family; do not reverse, retune or recycle it"
        )
    )
    report = {
        "generated_at": base.now_iso(),
        "tool": "tools/liquidation_cross_venue_canonical_paired_leadership_forward_observer_v4.py",
        "decision": decision,
        "can_trade": False,
        "automatic_promotion_allowed": False,
        "lock": {
            "path": base.portable(lock_path),
            "sha256": base.sha256_file(lock_path),
            "lock_id": lock["lock_id"],
            "status": lock["status"],
            "forward_start_at": lock["forward_start_at"],
        },
        "source_contract": {
            "required_ingest_schema_versions": schemas,
            "required_sources": sources,
            "bybit_packet_identity": PACKET_IDENTITY,
            "bybit_market_tuple_deduplication": False,
        },
        "side_contract": {
            "match_dimension": "liquidated_position_side",
            "mapping": v3.CANONICAL_SIDE_MAP,
            "v3_observations_admitted": False,
        },
        "source_counters": {"binance": binance_counters, "bybit": bybit_counters},
        "evaluation_cutoff": base.iso_from_ns(cutoff_ns),
        "primary_sample": sample,
        "windows_seconds": windows,
        "terminal_evidence": evidence,
        "terminal": {"reached": terminal, "frozen": False, "receipt": base.portable(terminal_receipt_path)},
        "blockers": sorted(set(blockers)),
        "next_action": next_action,
        "runtime_boundary": {
            "observer_only": True,
            "unmatched_events_scored": False,
            "price_outcomes_read": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }
    base.write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    if terminal:
        base.write_json(
            terminal_receipt_path,
            {"lock_id": lock["lock_id"], "terminal": True, "sealed_at": base.now_iso(), "report": report},
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward-only canonical paired observer with venue-specific schemas")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser("seal-lock")
    seal.add_argument(
        "--prereg",
        default="configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_PREREG_V4_2026-07-15.json",
    )
    seal.add_argument(
        "--lock",
        default="configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_LOCK_V4_2026-07-15.json",
    )
    seal.add_argument("--acknowledge-forward-only", action="store_true")
    run = subparsers.add_parser("run-once")
    run.add_argument(
        "--lock",
        default="configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_LOCK_V4_2026-07-15.json",
    )
    run.add_argument(
        "--out-prefix",
        default="docs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V4_2026-07-15",
    )
    run.add_argument(
        "--terminal-receipt",
        default="logs/liquidation_cross_venue_canonical_paired_leadership_v4/terminal_receipt.json",
    )
    args = parser.parse_args()
    try:
        if args.command == "seal-lock":
            if not args.acknowledge_forward_only:
                raise ValueError("--acknowledge-forward-only is required")
            prereg_path = base.resolve_path(args.prereg)
            lock_path = base.resolve_path(args.lock)
            lock = build_lock(prereg_path)
            base.write_json(lock_path, lock)
            print(json.dumps({"decision": "canonical_paired_v4_forward_lock_sealed", "lock": base.portable(lock_path), "can_trade": False}, indent=2))
            return 0
        report = run_observer(
            base.resolve_path(args.lock), base.resolve_path(args.out_prefix), base.resolve_path(args.terminal_receipt)
        )
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"decision": "canonical_paired_v4_observer_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"decision": report["decision"], "primary_pairs": report["primary_sample"]["matched_pairs"], "terminal": report["terminal"]["reached"], "can_trade": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
