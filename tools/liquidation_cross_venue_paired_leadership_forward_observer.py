#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import liquidation_cross_venue_lead_lag_forward_observer as base


OBSERVER_PATH = Path(__file__).resolve()
DEPENDENCY_PATH = Path(base.__file__).resolve()


def validate_prereg(prereg: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if prereg.get("status") != "prospective_preregistration_before_forward_floor":
        failures.append("status")
    if base.parse_iso_ns(prereg.get("forward_floor_at")) is None:
        failures.append("forward_floor_at")
    sources = prereg.get("sources") if isinstance(prereg.get("sources"), dict) else {}
    if not sources.get("binance") or not sources.get("bybit"):
        failures.append("sources")
    rules = prereg.get("fixed_rules") if isinstance(prereg.get("fixed_rules"), dict) else {}
    windows = rules.get("pair_windows_seconds")
    if not isinstance(windows, list) or windows != sorted(windows) or any(int(value) <= 0 for value in windows):
        failures.append("pair_windows_seconds")
    if int(rules.get("primary_window_seconds") or 0) not in [int(value) for value in windows or []]:
        failures.append("primary_window_seconds")
    boundary = prereg.get("runtime_boundary") if isinstance(prereg.get("runtime_boundary"), dict) else {}
    for key in ("paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary.{key}")
    if prereg.get("can_trade") is not False or prereg.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    return failures


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
        "schema_version": 2,
        "lock_id": prereg["prereg_id"],
        "status": "prospective_forward_lock_before_outcome_review",
        "created_at": created,
        "forward_start_at": prereg["forward_floor_at"],
        "preregistration": {"path": base.portable(prereg_path), "sha256": base.sha256_file(prereg_path)},
        "observer": {"path": base.portable(OBSERVER_PATH), "sha256": base.sha256_file(OBSERVER_PATH)},
        "dependencies": [
            {"path": base.portable(DEPENDENCY_PATH), "sha256": base.sha256_file(DEPENDENCY_PATH)}
        ],
        "sources": prereg["sources"],
        "shared_symbols": prereg["shared_symbols"],
        "fixed_rules": prereg["fixed_rules"],
        "terminal_gate": prereg["terminal_gate"],
        "runtime_boundary": prereg["runtime_boundary"],
        "can_trade": False,
        "orders_allowed": False,
    }


def validate_lock(lock: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if lock.get("status") != "prospective_forward_lock_before_outcome_review":
        failures.append("status")
    if base.parse_iso_ns(lock.get("forward_start_at")) is None:
        failures.append("forward_start_at")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    boundary = lock.get("runtime_boundary") if isinstance(lock.get("runtime_boundary"), dict) else {}
    for key in ("paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary.{key}")
    integrity_items = []
    for section in ("preregistration", "observer"):
        item = lock.get(section) if isinstance(lock.get(section), dict) else {}
        integrity_items.append((section, item))
    dependencies = lock.get("dependencies") if isinstance(lock.get("dependencies"), list) else []
    integrity_items.extend((f"dependency_{index}", item) for index, item in enumerate(dependencies) if isinstance(item, dict))
    if not dependencies:
        failures.append("dependencies")
    for name, item in integrity_items:
        path = base.resolve_path(str(item.get("path") or ""))
        expected = str(item.get("sha256") or "")
        if not path.is_file() or not expected or base.sha256_file(path) != expected:
            failures.append(f"{name}_integrity")
    return failures


def build_pairs(
    binance_events: list[dict[str, Any]],
    bybit_events: list[dict[str, Any]],
    *,
    cutoff_ns: int,
    maximum_window_ns: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {
        "binance": defaultdict(list),
        "bybit": defaultdict(list),
    }
    for venue, events in (("binance", binance_events), ("bybit", bybit_events)):
        for event in events:
            if event["received_at_ns"] <= cutoff_ns:
                grouped[venue][(event["symbol"], event["side"])].append(event)

    pairs: list[dict[str, Any]] = []
    for key in sorted(set(grouped["binance"]) & set(grouped["bybit"])):
        left = grouped["binance"][key]
        right = grouped["bybit"][key]
        right_times = [item["received_at_ns"] for item in right]
        candidates: list[tuple[int, int, int]] = []
        for left_index, left_event in enumerate(left):
            left_ns = left_event["received_at_ns"]
            start = bisect.bisect_left(right_times, left_ns - maximum_window_ns)
            stop = bisect.bisect_right(right_times, left_ns + maximum_window_ns)
            for right_index in range(start, stop):
                delay = abs(right_times[right_index] - left_ns)
                if delay > 0:
                    candidates.append((delay, left_index, right_index))
        candidates.sort(
            key=lambda item: (
                item[0],
                min(left[item[1]]["received_at_ns"], right[item[2]]["received_at_ns"]),
                left[item[1]]["received_at_ns"],
                right[item[2]]["received_at_ns"],
            )
        )
        used_left: set[int] = set()
        used_right: set[int] = set()
        for delay_ns, left_index, right_index in candidates:
            if left_index in used_left or right_index in used_right:
                continue
            binance = left[left_index]
            bybit = right[right_index]
            used_left.add(left_index)
            used_right.add(right_index)
            leader = "binance" if binance["received_at_ns"] < bybit["received_at_ns"] else "bybit"
            first_ns = min(binance["received_at_ns"], bybit["received_at_ns"])
            pairs.append(
                {
                    "symbol": key[0],
                    "side": key[1],
                    "leader_venue": leader,
                    "first_received_at_ns": first_ns,
                    "first_received_at": base.iso_from_ns(first_ns),
                    "binance_received_at_ns": binance["received_at_ns"],
                    "bybit_received_at_ns": bybit["received_at_ns"],
                    "absolute_delay_ms": round(delay_ns / 1_000_000, 6),
                    "same_collector_host": True,
                }
            )
    pairs.sort(key=lambda item: (item["first_received_at_ns"], item["symbol"], item["side"]))
    return pairs


def summarize_pairs(pairs: list[dict[str, Any]], windows_seconds: list[int]) -> dict[str, Any]:
    windows: dict[str, Any] = {}
    for seconds in windows_seconds:
        selected = [row for row in pairs if row["absolute_delay_ms"] <= seconds * 1000]
        total = len(selected)
        counts = Counter(row["leader_venue"] for row in selected)
        venue_metrics: dict[str, Any] = {}
        for venue in ("binance", "bybit"):
            leaders = counts.get(venue, 0)
            lower, upper = base.wilson_interval(leaders, total)
            venue_metrics[venue] = {
                "leader_count": leaders,
                "leader_share": round(leaders / total, 8) if total else 0.0,
                "wilson_95": {"lower": round(lower, 8), "upper": round(upper, 8)},
            }
        windows[str(seconds)] = {"matched_pairs": total, "leader": venue_metrics}
    return windows


def primary_sample(pairs: list[dict[str, Any]], primary_seconds: int) -> dict[str, Any]:
    selected = [row for row in pairs if row["absolute_delay_ms"] <= primary_seconds * 1000]
    symbols = Counter(row["symbol"] for row in selected)
    days = {str(row["first_received_at"])[:10] for row in selected}
    return {
        "matched_pairs": len(selected),
        "utc_days": len(days),
        "symbol_count": len(symbols),
        "symbols": dict(sorted(symbols.items())),
        "max_single_symbol_share": round(max(symbols.values()) / len(selected), 8) if selected else 0.0,
        "first_pair_at": selected[0]["first_received_at"] if selected else None,
        "last_pair_at": selected[-1]["first_received_at"] if selected else None,
    }


def evaluate_terminal(
    sample: dict[str, Any], windows: dict[str, Any], gate: dict[str, Any]
) -> tuple[str, list[str], dict[str, Any]]:
    blockers: list[str] = []
    if sample["matched_pairs"] < int(gate["minimum_primary_window_pairs"]):
        blockers.append("minimum_primary_window_pairs_not_met")
    if sample["utc_days"] < int(gate["minimum_utc_days"]):
        blockers.append("minimum_utc_days_not_met")
    if sample["symbol_count"] < int(gate["minimum_symbols"]):
        blockers.append("minimum_symbols_not_met")
    if sample["max_single_symbol_share"] > float(gate["maximum_single_symbol_share"]):
        blockers.append("single_symbol_concentration_exceeded")
    if blockers:
        return "liquidation_cross_venue_paired_leadership_collecting_forward_sample", blockers, {}

    primary = windows[str(int(gate["primary_window_seconds"]))]["leader"]
    candidate = max(("binance", "bybit"), key=lambda venue: primary[venue]["leader_share"])
    metrics = primary[candidate]
    evidence = {
        "candidate_venue": candidate,
        "leader_share": metrics["leader_share"],
        "wilson_95": metrics["wilson_95"],
        "primary_window_seconds": int(gate["primary_window_seconds"]),
    }
    if (
        metrics["leader_share"] >= float(gate["minimum_candidate_leader_share"])
        and metrics["wilson_95"]["lower"] > 0.5
    ):
        return (
            "liquidation_cross_venue_paired_leadership_candidate_for_manual_price_impact_preregistration",
            [],
            evidence,
        )
    return "liquidation_cross_venue_paired_leadership_no_stable_leader_tombstone", [], evidence


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Cross-Venue Paired Receipt Leadership Forward Observer",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Forward floor: `{report['lock']['forward_start_at']}`",
        f"- Terminal frozen: `{report['terminal']['frozen']}`",
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
    binance, binance_counters = base.load_events("binance", base.resolve_path(lock["sources"]["binance"]), **load_kwargs)
    bybit, bybit_counters = base.load_events("bybit", base.resolve_path(lock["sources"]["bybit"]), **load_kwargs)
    if binance and bybit:
        latest_common = min(binance[-1]["received_at_ns"], bybit[-1]["received_at_ns"], current_ns)
        cutoff_ns = latest_common - max_window_ns
    else:
        cutoff_ns = floor_ns - 1
    pairs = build_pairs(binance, bybit, cutoff_ns=cutoff_ns, maximum_window_ns=max_window_ns)
    windows = summarize_pairs(pairs, windows_seconds)
    sample = primary_sample(pairs, int(rules["primary_window_seconds"]))
    if current_ns < floor_ns:
        decision = "liquidation_cross_venue_paired_leadership_waiting_forward_floor"
        blockers = ["forward_floor_not_reached"]
        evidence: dict[str, Any] = {}
    else:
        decision, blockers, evidence = evaluate_terminal(sample, windows, lock["terminal_gate"])
    terminal = decision in {
        "liquidation_cross_venue_paired_leadership_candidate_for_manual_price_impact_preregistration",
        "liquidation_cross_venue_paired_leadership_no_stable_leader_tombstone",
    }
    next_action = (
        "keep both public collectors and the paired observer running without parameter changes"
        if not terminal
        else (
            "manually preregister a separate forward-only price-impact test with a new future floor"
            if decision.endswith("manual_price_impact_preregistration")
            else "tombstone this family; do not reverse, retune or recycle it"
        )
    )
    report = {
        "generated_at": base.now_iso(),
        "tool": "tools/liquidation_cross_venue_paired_leadership_forward_observer.py",
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
        base.write_json(terminal_receipt_path, {"lock_id": lock["lock_id"], "terminal": True, "sealed_at": base.now_iso(), "report": report})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Immutable forward-only paired Binance/Bybit liquidation receipt-leadership observer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser("seal-lock")
    seal.add_argument("--prereg", default="configs/LIQUIDATION_CROSS_VENUE_PAIRED_LEADERSHIP_PREREG_2026-07-13.json")
    seal.add_argument("--lock", default="configs/LIQUIDATION_CROSS_VENUE_PAIRED_LEADERSHIP_LOCK_2026-07-13.json")
    seal.add_argument("--acknowledge-forward-only", action="store_true")
    run = subparsers.add_parser("run-once")
    run.add_argument("--lock", default="configs/LIQUIDATION_CROSS_VENUE_PAIRED_LEADERSHIP_LOCK_2026-07-13.json")
    run.add_argument("--out-prefix", default="docs/LIQUIDATION_CROSS_VENUE_PAIRED_LEADERSHIP_FORWARD_OBSERVER_2026-07-13")
    run.add_argument("--terminal-receipt", default="logs/liquidation_cross_venue_paired_leadership/terminal_receipt.json")
    args = parser.parse_args()
    try:
        if args.command == "seal-lock":
            if not args.acknowledge_forward_only:
                raise ValueError("--acknowledge-forward-only is required")
            prereg_path = base.resolve_path(args.prereg)
            lock_path = base.resolve_path(args.lock)
            lock = build_lock(prereg_path)
            base.write_json(lock_path, lock)
            print(json.dumps({"decision": "paired_forward_lock_sealed", "lock": base.portable(lock_path), "can_trade": False}, indent=2))
            return 0
        report = run_observer(base.resolve_path(args.lock), base.resolve_path(args.out_prefix), base.resolve_path(args.terminal_receipt))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"decision": "liquidation_cross_venue_paired_leadership_observer_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"decision": report["decision"], "primary_pairs": report["primary_sample"]["matched_pairs"], "terminal": report["terminal"]["reached"], "can_trade": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
