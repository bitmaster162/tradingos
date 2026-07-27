#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


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


def iso_to_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1_000_000_000)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 6)


def event_time_ms(row: dict[str, Any]) -> int:
    for key in ("trade_time_ms", "liquidation_time_ms", "event_time_ms"):
        value = row.get(key)
        if value not in (None, ""):
            return int(value)
    return 0


def iter_rows(root: Path) -> Iterable[tuple[Path, int, dict[str, Any] | None]]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, start=1):
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        yield path, line_number, None
                        continue
                    yield path, line_number, payload if isinstance(payload, dict) else None
        except OSError:
            yield path, 0, None


def source_stats(name: str, root: Path, floor_ns: int, required_fields: list[str], gates: dict[str, Any]) -> dict[str, Any]:
    floor_ms = floor_ns // 1_000_000
    eligible = 0
    valid = 0
    invalid_json_or_io = 0
    missing_fields: dict[str, int] = {}
    hosts: set[str] = set()
    symbols: set[str] = set()
    receipts: list[int] = []
    delays_ms: list[float] = []
    large_negative = 0

    for _, _, row in iter_rows(root):
        if row is None:
            invalid_json_or_io += 1
            continue
        source_event_ms = event_time_ms(row)
        if source_event_ms < floor_ms:
            continue
        eligible += 1
        missing = [field for field in required_fields if row.get(field) in (None, "")]
        for field in missing:
            missing_fields[field] = missing_fields.get(field, 0) + 1
        if missing:
            continue
        if int(row.get("ingest_schema_version") or 0) != int(gates["required_schema_version"]):
            missing_fields["wrong_ingest_schema_version"] = missing_fields.get("wrong_ingest_schema_version", 0) + 1
            continue
        if row.get("collector_clock_source") != gates["required_clock_source"]:
            missing_fields["wrong_collector_clock_source"] = missing_fields.get("wrong_collector_clock_source", 0) + 1
            continue
        received_ns = int(row["received_at_ns"])
        if received_ns < floor_ns:
            continue
        valid += 1
        receipts.append(received_ns)
        hosts.add(str(row["collector_host"]))
        symbols.add(str(row.get("symbol") or "").upper())
        delay_ms = (received_ns / 1_000_000) - source_event_ms
        delays_ms.append(delay_ms)
        if delay_ms < float(gates["large_negative_delay_ms"]):
            large_negative += 1

    ratio = valid / eligible if eligible else 0.0
    return {
        "name": name,
        "path": portable(root),
        "eligible_event_time_rows": eligible,
        "arrival_valid_events": valid,
        "arrival_field_ratio": round(ratio, 8),
        "invalid_json_or_io_rows": invalid_json_or_io,
        "missing_or_invalid_fields": missing_fields,
        "hosts": sorted(hosts),
        "symbols": sorted(symbols),
        "first_received_at_ns": min(receipts) if receipts else None,
        "last_received_at_ns": max(receipts) if receipts else None,
        "delivery_delay_ms": {
            "p50": percentile(delays_ms, 0.50),
            "p95": percentile(delays_ms, 0.95),
            "p99": percentile(delays_ms, 0.99),
            "min": round(min(delays_ms), 6) if delays_ms else None,
            "max": round(max(delays_ms), 6) if delays_ms else None,
            "large_negative_count": large_negative,
            "large_negative_ratio": round(large_negative / valid, 8) if valid else 0.0,
        },
    }


def evaluate(config: dict[str, Any], binance: dict[str, Any], bybit: dict[str, Any]) -> tuple[str, list[str]]:
    gates = config["gates"]
    blockers: list[str] = []
    hard_failures: list[str] = []
    sources = [binance, bybit]
    all_hosts = {host for source in sources for host in source["hosts"]}

    for source in sources:
        name = source["name"]
        if source["eligible_event_time_rows"] and source["arrival_field_ratio"] < float(gates["minimum_arrival_field_ratio"]):
            hard_failures.append(f"{name}_arrival_schema_incomplete")
        if source["delivery_delay_ms"]["large_negative_ratio"] > float(gates["maximum_large_negative_delay_ratio"]):
            hard_failures.append(f"{name}_collector_clock_ahead_of_exchange")
        p99 = source["delivery_delay_ms"]["p99"]
        if p99 is not None and p99 > float(gates["maximum_p99_delivery_delay_ms"]):
            hard_failures.append(f"{name}_delivery_delay_p99_exceeds_gate")
        if source["arrival_valid_events"] < int(gates["minimum_events_per_venue"]):
            blockers.append(f"{name}_minimum_events_not_met")

    if gates.get("same_collector_host_required") and len(all_hosts) > 1:
        hard_failures.append("collector_hosts_not_identical")
    if not all_hosts:
        blockers.append("collector_host_not_observed")

    shared_symbols = set(binance["symbols"]) & set(bybit["symbols"])
    if len(shared_symbols) < int(gates["minimum_shared_symbols"]):
        blockers.append("minimum_shared_symbols_not_met")

    starts = [source["first_received_at_ns"] for source in sources if source["first_received_at_ns"] is not None]
    ends = [source["last_received_at_ns"] for source in sources if source["last_received_at_ns"] is not None]
    overlap_seconds = max(0.0, (min(ends) - max(starts)) / 1_000_000_000) if len(starts) == 2 and len(ends) == 2 else 0.0
    if overlap_seconds < float(gates["minimum_overlapping_receipt_span_seconds"]):
        blockers.append("minimum_overlapping_receipt_span_not_met")

    if hard_failures:
        return "liquidation_arrival_time_readiness_hard_fail", sorted(set(hard_failures + blockers))
    if blockers:
        return "liquidation_arrival_time_readiness_collecting", sorted(set(blockers))
    return "liquidation_arrival_time_ready_for_manual_preregistration_review", []


def cross_venue_summary(binance: dict[str, Any], bybit: dict[str, Any]) -> dict[str, Any]:
    shared_symbols = sorted(set(binance["symbols"]) & set(bybit["symbols"]))
    starts = [source["first_received_at_ns"] for source in (binance, bybit) if source["first_received_at_ns"] is not None]
    ends = [source["last_received_at_ns"] for source in (binance, bybit) if source["last_received_at_ns"] is not None]
    overlap_seconds = max(0.0, (min(ends) - max(starts)) / 1_000_000_000) if len(starts) == 2 and len(ends) == 2 else 0.0
    return {
        "collector_hosts": sorted({host for source in (binance, bybit) for host in source["hosts"]}),
        "same_collector_host": bool(binance["hosts"] and bybit["hosts"] and set(binance["hosts"]) == set(bybit["hosts"])),
        "shared_symbols": shared_symbols,
        "shared_symbol_count": len(shared_symbols),
        "overlapping_receipt_span_seconds": round(overlap_seconds, 6),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Cross-Venue Arrival-Time Readiness",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        "- Can trade: `false`",
        f"- Forward floor: `{report['forward_floor_utc']}`",
        f"- Blockers: `{', '.join(report['blockers']) or 'none'}`",
        f"- Same collector host: `{report['cross_venue']['same_collector_host']}`",
        f"- Shared symbols: `{report['cross_venue']['shared_symbol_count']}`",
        f"- Overlapping receipt span: `{report['cross_venue']['overlapping_receipt_span_seconds']}` seconds",
        "",
        "| Venue | Eligible rows | Arrival-valid | Ratio | Hosts | P99 delay ms |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for source in report["sources"]:
        lines.append(
            f"| `{source['name']}` | `{source['eligible_event_time_rows']}` | `{source['arrival_valid_events']}` | "
            f"`{source['arrival_field_ratio']}` | `{', '.join(source['hosts']) or 'none'}` | "
            f"`{source['delivery_delay_ms']['p99']}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This report proves only timestamp/data readiness. It does not prove venue leadership, predictability, or tradability.",
            "- A separate immutable preregistration is required before any lead/lag outcome is inspected.",
            "- Existing liquidation observers and their thresholds are unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check forward-only receipt-time readiness across Binance and Bybit liquidation feeds")
    parser.add_argument("--config", default="configs/LIQUIDATION_CROSS_VENUE_ARRIVAL_TIME_CONTRACT_2026-07-13.json")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_CROSS_VENUE_ARRIVAL_TIME_READINESS_2026-07-13")
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    floor_ns = iso_to_ns(config["forward_floor_utc"])
    required = list(config["required_arrival_fields"])
    gates = config["gates"]
    binance = source_stats("binance", resolve_path(config["sources"]["binance"]), floor_ns, required, gates)
    bybit = source_stats("bybit", resolve_path(config["sources"]["bybit"]), floor_ns, required, gates)
    decision, blockers = evaluate(config, binance, bybit)
    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_cross_venue_arrival_time_readiness.py",
        "contract": portable(config_path),
        "decision": decision,
        "can_trade": False,
        "forward_floor_utc": config["forward_floor_utc"],
        "sources": [binance, bybit],
        "cross_venue": cross_venue_summary(binance, bybit),
        "blockers": blockers,
        "boundary": config["research_boundary"],
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "blockers": blockers, "out": portable(out.with_suffix('.json')), "can_trade": False}, ensure_ascii=False))
    return 2 if decision.endswith("hard_fail") else 0


if __name__ == "__main__":
    raise SystemExit(main())
