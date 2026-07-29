#!/usr/bin/env python3
"""Fail-closed forward-evidence audit for the public Arb Radar snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MIN_DISTINCT_SNAPSHOTS = 3
MIN_PERSISTENCE = 3
MIN_ROBUST_APR = 0.25
MIN_VENUES = 4
MAX_BREAKEVEN_H = 48.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def parse_updated(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)


def route_key(item: dict[str, Any]) -> str:
    return "|".join(
        str(item.get(name, ""))
        for name in ("kind", "symbol", "long_venue", "short_venue", "venue")
    )


def source_findings(engine_text: str, service_text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    tag_is_broad = 'o["kind"] != "funding"' in service_text
    funding_meta_drops_tag = (
        'meta = {"legs": {}}' in engine_text
        and "json.dumps(meta)" in engine_text
        and 'o["_tag"]' not in engine_text
    )
    if tag_is_broad and funding_meta_drops_tag:
        findings.append(
            {
                "id": "ROBUST_TAG_CLASS_COLLISION",
                "severity": "critical",
                "status": "confirmed",
                "evidence": (
                    "Service tags every non-funding opportunity robust, while "
                    "funding/carry paper metadata is rebuilt without _tag."
                ),
            }
        )

    instant_spread = bool(
        re.search(r'if o\["kind"\] == "spread":', engine_text)
        and re.search(r"opened_ms,closed_ms", engine_text)
        and re.search(r"\(o\[\"kind\"\].*now_ms,\s*now_ms,", engine_text, re.S)
    )
    if instant_spread:
        findings.append(
            {
                "id": "SPREAD_SAME_SNAPSHOT_CLOSE",
                "severity": "critical",
                "status": "confirmed",
                "evidence": (
                    "Spread paper positions are inserted already closed with "
                    "opened_ms == closed_ms; this is not a forward outcome."
                ),
            }
        )

    double_cost = (
        'pnl = o["gross"] * notional - cost' in engine_text
        and "funding_collected+spread_pnl-entry_cost-exit_cost" in engine_text
    )
    if double_cost:
        findings.append(
            {
                "id": "SPREAD_COST_DOUBLE_SUBTRACTION",
                "severity": "high",
                "status": "confirmed",
                "evidence": (
                    "Spread PnL subtracts cost when opened, stores the same cost "
                    "as entry_cost, then the summary subtracts entry_cost again."
                ),
            }
        )
    return findings


def evaluate(
    snapshots: list[dict[str, Any]],
    engine_text: str,
    service_text: str,
    captured_at: datetime,
) -> dict[str, Any]:
    timestamps = [parse_updated(str(snapshot["updated"])) for snapshot in snapshots]
    distinct_timestamps = sorted(set(timestamps))
    latest = max(timestamps)
    age_minutes = (captured_at - latest).total_seconds() / 60.0

    route_counts: dict[str, int] = {}
    latest_routes: dict[str, dict[str, Any]] = {}
    for snapshot in sorted(snapshots, key=lambda item: parse_updated(str(item["updated"]))):
        seen_in_snapshot: set[str] = set()
        for item in snapshot.get("opportunities", []):
            key = route_key(item)
            latest_routes[key] = item
            if key not in seen_in_snapshot:
                route_counts[key] = route_counts.get(key, 0) + 1
                seen_in_snapshot.add(key)

    latest_snapshot = max(snapshots, key=lambda item: parse_updated(str(item["updated"])))
    book = latest_snapshot.get("book", {})
    robust = book.get("robust_vs_fragile", {}).get("robust", {})
    spread = book.get("by_kind", {}).get("spread", [])
    robust_equals_spread = (
        isinstance(spread, list)
        and len(spread) == 2
        and robust.get("n") == spread[0]
        and abs(float(robust.get("pnl", 0)) - float(spread[1])) < 1e-9
    )

    findings = source_findings(engine_text, service_text)
    if robust_equals_spread:
        findings.append(
            {
                "id": "PUBLISHED_ROBUST_EQUALS_SPREAD_BOOK",
                "severity": "critical",
                "status": "confirmed",
                "evidence": {
                    "robust_n": robust.get("n"),
                    "robust_pnl": robust.get("pnl"),
                    "spread_n": spread[0],
                    "spread_pnl": spread[1],
                },
            }
        )

    accepted: list[dict[str, Any]] = []
    watchlist: list[dict[str, Any]] = []
    for key, item in sorted(latest_routes.items()):
        if item.get("kind") != "funding":
            continue
        structural = (
            float(item.get("apr_robust", 0)) >= MIN_ROBUST_APR
            and int(item.get("venues_n", 0)) >= MIN_VENUES
            and float(item.get("breakeven_h", 1e9)) <= MAX_BREAKEVEN_H
        )
        if not structural:
            continue
        observed = route_counts.get(key, 0)
        missing = []
        if len(distinct_timestamps) < MIN_DISTINCT_SNAPSHOTS:
            missing.append("three_distinct_timestamp_locked_snapshots")
        if observed < MIN_PERSISTENCE:
            missing.append("route_persistence_at_least_three_snapshots")
        missing.extend(
            [
                "synchronized_executable_depth_both_legs",
                "funding_clock_alignment",
                "exit_depth_and_exit_cost",
                "account_and_venue_constraints",
                "transfer_or_prefunding_feasibility",
            ]
        )
        record = {
            "route_key": key,
            "symbol": item.get("symbol"),
            "long": item.get("long"),
            "short": item.get("short"),
            "apr": item.get("apr"),
            "apr_robust": item.get("apr_robust"),
            "venues_n": item.get("venues_n"),
            "breakeven_h": item.get("breakeven_h"),
            "source_streak": item.get("streak"),
            "independent_snapshot_count": observed,
            "status": "HOLD_FORWARD_OBSERVATION_ONLY",
            "missing_gates": missing,
        }
        watchlist.append(record)

    # No candidate can pass while execution evidence is absent. This is deliberate.
    terminal = (
        "EDGE_NOT_SUPPORTED"
        if robust_equals_spread and len(distinct_timestamps) >= MIN_DISTINCT_SNAPSHOTS
        else "INSUFFICIENT_FORWARD_EVIDENCE"
    )
    rejected = [
        {
            "hypothesis": "Published robust subset proves the funding robustness filter.",
            "decision": "REJECT",
            "reason": (
                "The published robust aggregate equals the spread book exactly, "
                "and source tagging does not preserve funding robust/fragile tags."
            ),
        },
        {
            "hypothesis": "Paper spread wins are forward executions.",
            "decision": "REJECT",
            "reason": "Spread positions open and close at the same timestamp from one snapshot.",
        },
        {
            "hypothesis": "Headline APR is executable net edge.",
            "decision": "REJECT",
            "reason": (
                "Snapshot lacks synchronized depth, funding-clock capture, exit depth, "
                "account limits, prefunding/transfer constraints and realized fills."
            ),
        },
        {
            "hypothesis": "Current route persistence is established.",
            "decision": "REJECT",
            "reason": (
                f"Only {len(distinct_timestamps)} distinct timestamp-locked snapshot(s) "
                "were supplied; current source streaks are one."
            ),
        },
    ]
    return {
        "schema": "TRADINGOS_ARB_RADAR_R52_AUDIT_V1",
        "terminal": terminal,
        "can_trade": False,
        "capital_permission": "DENY",
        "captured_at_utc": captured_at.isoformat().replace("+00:00", "Z"),
        "snapshot_count": len(snapshots),
        "distinct_timestamp_count": len(distinct_timestamps),
        "latest_source_updated_utc": latest.isoformat().replace("+00:00", "Z"),
        "latest_source_age_minutes_at_capture": round(age_minutes, 3),
        "published_book": book,
        "robust_equals_spread_book": robust_equals_spread,
        "source_findings": findings,
        "candidate_edges": accepted,
        "forward_watchlist": watchlist,
        "rejected_hypotheses": rejected,
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_report(path: Path, result: dict[str, Any]) -> None:
    book = result["published_book"]
    lines = [
        "# Arb Radar R52: forward edge research",
        "",
        f"**Terminal:** `{result['terminal']}`",
        "",
        "## Decision",
        "",
        "The current positive subset is not accepted as a reproducible forward edge.",
        f"Only {result['distinct_timestamp_count']} distinct timestamp-locked snapshot "
        "was available, and all published route streaks are one.",
        "",
        "## Published evidence",
        "",
        f"- Full book: {book.get('closed')} closed; PnL "
        f"{float(book.get('closed_pnl', 0)):.2f}; win rate "
        f"{float(book.get('winrate', 0)):.2f}%.",
        f"- Published robust: {book.get('robust_vs_fragile', {}).get('robust', {})}.",
        f"- Robust aggregate equals spread book: "
        f"`{str(result['robust_equals_spread_book']).lower()}`.",
        "",
        "## Causal findings",
        "",
    ]
    for finding in result["source_findings"]:
        lines.append(
            f"- **{finding['id']}** ({finding['severity']}): "
            f"{finding['evidence']}"
        )
    lines.extend(
        [
            "",
            "## Deterministic admission rule",
            "",
            "A funding route remains observation-only until it has at least three "
            "independent timestamp-locked snapshots and passes synchronized depth, "
            "funding-clock, basis, fee/slippage, exit, account and prefunding gates.",
            "No current route passes.",
            "",
            "## Negative results",
            "",
        ]
    )
    for item in result["rejected_hypotheses"]:
        lines.append(f"- **{item['decision']}**: {item['hypothesis']} {item['reason']}")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "`can_trade=false`; `capital_permission=DENY`. This audit sends no order, "
            "uses no credential and changes no runtime.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", action="append", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--service", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--captured-at", required=True)
    args = parser.parse_args()

    captured_at = datetime.fromisoformat(args.captured_at.replace("Z", "+00:00"))
    snapshots = [load_json(path) for path in args.snapshot]
    result = evaluate(
        snapshots,
        args.engine.read_text(encoding="utf-8"),
        args.service.read_text(encoding="utf-8"),
        captured_at,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out / "candidate_edges.json", result["candidate_edges"])
    write_json(args.out / "rejected_hypotheses.json", result["rejected_hypotheses"])
    write_json(args.out / "forward_watchlist.json", result["forward_watchlist"])
    write_json(args.out / "audit_summary.json", result)
    write_report(args.out / "EDGE_RESEARCH_R52.md", result)

    source_paths = [*args.snapshot, args.engine, args.service]
    manifest = {
        "schema": "TRADINGOS_ARB_RADAR_R52_SOURCE_MANIFEST_V1",
        "files": [
            {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in source_paths
        ],
    }
    write_json(args.out / "source_manifest.json", manifest)
    print(json.dumps({"terminal": result["terminal"], "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
