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

from tools import liquidation_cross_venue_lead_lag_forward_observer as base
from tools import liquidation_cross_venue_paired_leadership_forward_observer as paired


OBSERVER_PATH = Path(__file__).resolve()
DEPENDENCY_PATHS = [Path(paired.__file__).resolve(), Path(base.__file__).resolve()]
CANONICAL_SIDE_MAP = {
    "binance": {"BUY": "SHORT", "SELL": "LONG"},
    "bybit": {"BUY": "LONG", "SELL": "SHORT"},
}


def validate_prereg(prereg: dict[str, Any]) -> list[str]:
    failures = paired.validate_prereg(prereg)
    rules = prereg.get("fixed_rules") if isinstance(prereg.get("fixed_rules"), dict) else {}
    if rules.get("match_dimensions") != ["symbol", "liquidated_position_side"]:
        failures.append("match_dimensions")
    semantics = prereg.get("source_semantics") if isinstance(prereg.get("source_semantics"), dict) else {}
    for venue, expected in CANONICAL_SIDE_MAP.items():
        item = semantics.get(venue) if isinstance(semantics.get(venue), dict) else {}
        if item.get("mapping_to_liquidated_position_side") != expected:
            failures.append(f"source_semantics.{venue}")
    boundary = prereg.get("research_boundary") if isinstance(prereg.get("research_boundary"), dict) else {}
    if boundary.get("v2_observations_admitted") is not False:
        failures.append("research_boundary.v2_observations_admitted")
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
        "schema_version": 3,
        "lock_id": prereg["prereg_id"],
        "status": "prospective_forward_lock_before_outcome_review",
        "created_at": created,
        "forward_start_at": prereg["forward_floor_at"],
        "preregistration": {"path": base.portable(prereg_path), "sha256": base.sha256_file(prereg_path)},
        "observer": {"path": base.portable(OBSERVER_PATH), "sha256": base.sha256_file(OBSERVER_PATH)},
        "dependencies": [
            {"path": base.portable(path), "sha256": base.sha256_file(path)} for path in DEPENDENCY_PATHS
        ],
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
    failures = paired.validate_lock(lock)
    rules = lock.get("fixed_rules") if isinstance(lock.get("fixed_rules"), dict) else {}
    if rules.get("match_dimensions") != ["symbol", "liquidated_position_side"]:
        failures.append("match_dimensions")
    semantics = lock.get("source_semantics") if isinstance(lock.get("source_semantics"), dict) else {}
    for venue, expected in CANONICAL_SIDE_MAP.items():
        item = semantics.get(venue) if isinstance(semantics.get(venue), dict) else {}
        if item.get("mapping_to_liquidated_position_side") != expected:
            failures.append(f"source_semantics.{venue}")
    boundary = lock.get("research_boundary") if isinstance(lock.get("research_boundary"), dict) else {}
    if boundary.get("v2_observations_admitted") is not False:
        failures.append("research_boundary.v2_observations_admitted")
    return sorted(set(failures))


def canonicalize_events(venue: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping = CANONICAL_SIDE_MAP.get(venue)
    if mapping is None:
        raise ValueError(f"unsupported venue: {venue}")
    canonical: list[dict[str, Any]] = []
    for event in events:
        raw_side = str(event.get("side") or "").upper()
        if raw_side not in mapping:
            raise ValueError(f"unsupported {venue} source side: {raw_side}")
        row = dict(event)
        row["raw_source_side"] = raw_side
        row["side"] = mapping[raw_side]
        row["liquidated_position_side"] = mapping[raw_side]
        canonical.append(row)
    return canonical


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Cross-Venue Canonical Paired Receipt Leadership Forward Observer V3",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Forward floor: `{report['lock']['forward_start_at']}`",
        f"- Terminal frozen: `{report['terminal']['frozen']}`",
        "- Match side: `liquidated_position_side`",
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
            "## Canonical Side Contract",
            "",
            "- Binance forced-order `BUY` maps to liquidated `SHORT`; `SELL` maps to liquidated `LONG`.",
            "- Bybit position-side `BUY` maps to liquidated `LONG`; `SELL` maps to liquidated `SHORT`.",
            "- All V2 observations are excluded; only events received at or after this lock's floor are eligible.",
            "",
            "## Boundary",
            "",
            "- Only observed one-to-one event pairs are scored; unmatched events never count against either venue.",
            "- No future price return, signal direction, paper entry, live entry, credential or order is used.",
            "- A terminal pass permits only a new future-floor price-impact preregistration.",
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
    load_kwargs = {
        "floor_ns": floor_ns,
        "symbols": symbols,
        "required_host": str(rules["required_collector_host"]),
        "required_schema_version": int(rules["required_ingest_schema_version"]),
    }
    binance_raw, binance_counters = base.load_events(
        "binance", base.resolve_path(lock["sources"]["binance"]), **load_kwargs
    )
    bybit_raw, bybit_counters = base.load_events(
        "bybit", base.resolve_path(lock["sources"]["bybit"]), **load_kwargs
    )
    binance = canonicalize_events("binance", binance_raw)
    bybit = canonicalize_events("bybit", bybit_raw)
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
        "keep both public collectors and the canonical paired observer running without parameter changes"
        if not terminal
        else (
            "manually preregister a separate forward-only price-impact test with a new future floor"
            if decision.endswith("manual_price_impact_preregistration")
            else "tombstone this family; do not reverse, retune or recycle it"
        )
    )
    report = {
        "generated_at": base.now_iso(),
        "tool": "tools/liquidation_cross_venue_canonical_paired_leadership_forward_observer.py",
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
        "side_contract": {
            "match_dimension": "liquidated_position_side",
            "mapping": CANONICAL_SIDE_MAP,
            "v2_observations_admitted": False,
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
    parser = argparse.ArgumentParser(
        description="Immutable forward-only canonical paired Binance/Bybit liquidation receipt-leadership observer"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser("seal-lock")
    seal.add_argument(
        "--prereg",
        default="configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_PREREG_V3_2026-07-13.json",
    )
    seal.add_argument(
        "--lock", default="configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_LOCK_V3_2026-07-13.json"
    )
    seal.add_argument("--acknowledge-forward-only", action="store_true")
    run = subparsers.add_parser("run-once")
    run.add_argument(
        "--lock", default="configs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_LOCK_V3_2026-07-13.json"
    )
    run.add_argument(
        "--out-prefix", default="docs/LIQUIDATION_CROSS_VENUE_CANONICAL_PAIRED_LEADERSHIP_FORWARD_OBSERVER_V3_2026-07-13"
    )
    run.add_argument(
        "--terminal-receipt",
        default="logs/liquidation_cross_venue_canonical_paired_leadership_v3/terminal_receipt.json",
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
            print(
                json.dumps(
                    {"decision": "canonical_paired_forward_lock_sealed", "lock": base.portable(lock_path), "can_trade": False},
                    indent=2,
                )
            )
            return 0
        report = run_observer(
            base.resolve_path(args.lock), base.resolve_path(args.out_prefix), base.resolve_path(args.terminal_receipt)
        )
    except (OSError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {
                    "decision": "liquidation_cross_venue_canonical_paired_leadership_observer_error",
                    "error": str(exc),
                    "can_trade": False,
                },
                indent=2,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "primary_pairs": report["primary_sample"]["matched_pairs"],
                "terminal": report["terminal"]["reached"],
                "can_trade": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
