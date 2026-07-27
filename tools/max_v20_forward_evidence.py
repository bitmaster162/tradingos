#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "2.0.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pct(value: float, ndigits: int = 3) -> float:
    return round(value * 100.0, ndigits)


def summarise_returns(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "resolved": 0,
            "hit_count": 0,
            "hit_rate_pct": None,
            "avg_directional_return_pct": None,
            "median_directional_return_pct": None,
            "min_directional_return_pct": None,
            "max_directional_return_pct": None,
        }
    hits = [value for value in values if value > 0]
    return {
        "resolved": len(values),
        "hit_count": len(hits),
        "hit_rate_pct": pct(len(hits) / len(values)),
        "avg_directional_return_pct": round(statistics.fmean(values), 6),
        "median_directional_return_pct": round(statistics.median(values), 6),
        "min_directional_return_pct": round(min(values), 6),
        "max_directional_return_pct": round(max(values), 6),
    }


def classify_evidence(
    summary: dict[str, Any],
    *,
    pending: int,
    min_resolved: int,
    min_hit_rate_pct: float,
    min_avg_return_pct: float,
) -> str:
    resolved = int(summary.get("resolved") or 0)
    hit_rate = summary.get("hit_rate_pct")
    avg_return = summary.get("avg_directional_return_pct")
    if resolved == 0 and pending == 0:
        return "no_observations"
    if resolved == 0:
        return "pending_only"
    positive = (
        hit_rate is not None
        and avg_return is not None
        and hit_rate >= min_hit_rate_pct
        and avg_return > min_avg_return_pct
    )
    if resolved < min_resolved:
        return "watchlist_positive_insufficient_sample" if positive else "insufficient_sample"
    if positive:
        return "candidate_for_hardening"
    return "negative_or_mixed"


def group_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("alert_id") or "unknown_alert"),
        str(row.get("tf") or "unknown_tf"),
        str(row.get("pair") or "unknown_pair"),
        str(row.get("side_context") or "unknown_side"),
    )


def build_report(
    *,
    tracker_path: Path,
    log_path: Path,
    min_resolved: int,
    min_hit_rate_pct: float,
    min_avg_return_pct: float,
) -> dict[str, Any]:
    tracker_rows = read_jsonl(tracker_path)
    log_rows = read_jsonl(log_path)
    resolved_rows = [row for row in tracker_rows if row.get("status") == "resolved"]
    pending_rows = [row for row in tracker_rows if row.get("status") == "pending"]

    overall_returns = [
        value
        for value in (safe_float(row.get("directional_return_pct")) for row in resolved_rows)
        if value is not None
    ]
    overall = summarise_returns(overall_returns)
    overall["pending"] = len(pending_rows)
    overall["total_tracker_rows"] = len(tracker_rows)
    overall["classification"] = classify_evidence(
        overall,
        pending=len(pending_rows),
        min_resolved=min_resolved,
        min_hit_rate_pct=min_hit_rate_pct,
        min_avg_return_pct=min_avg_return_pct,
    )

    grouped: dict[tuple[str, str, str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"resolved": [], "pending": []})
    for row in resolved_rows:
        grouped[group_key(row)]["resolved"].append(row)
    for row in pending_rows:
        grouped[group_key(row)]["pending"].append(row)

    group_summaries: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        returns = [
            value
            for value in (safe_float(row.get("directional_return_pct")) for row in rows["resolved"])
            if value is not None
        ]
        summary = summarise_returns(returns)
        pending = len(rows["pending"])
        summary["pending"] = pending
        summary["classification"] = classify_evidence(
            summary,
            pending=pending,
            min_resolved=min_resolved,
            min_hit_rate_pct=min_hit_rate_pct,
            min_avg_return_pct=min_avg_return_pct,
        )
        alert_id, tf, pair, side_context = key
        group_summaries.append(
            {
                "alert_id": alert_id,
                "tf": tf,
                "pair": pair,
                "side_context": side_context,
                **summary,
            }
        )

    group_summaries.sort(
        key=lambda item: (
            int(item.get("resolved") or 0),
            float(item.get("avg_directional_return_pct") or -999999.0),
        ),
        reverse=True,
    )

    active_log_events = [
        row for row in log_rows if row.get("event_type") == "market_state_alert_active"
    ]
    snapshot_events = [
        row for row in log_rows if row.get("event_type") == "market_state_alert_snapshot"
    ]

    return {
        "engine": "MAX_CORE_LITE_FORWARD_EVIDENCE",
        "version": VERSION,
        "generated_at": now_iso(),
        "inputs": {
            "tracker": str(tracker_path),
            "log": str(log_path),
            "min_resolved": min_resolved,
            "min_hit_rate_pct": min_hit_rate_pct,
            "min_avg_return_pct": min_avg_return_pct,
        },
        "policy": {
            "trade_permission": False,
            "entry_permission": "evidence_only",
            "live_orders": False,
            "risk_multiplier": 0.0,
            "promotion_rule": "Only candidate_for_hardening may be researched further; none may trade directly.",
        },
        "log_summary": {
            "total_events": len(log_rows),
            "snapshot_events": len(snapshot_events),
            "active_events": len(active_log_events),
            "last_event": log_rows[-1] if log_rows else None,
        },
        "overall": overall,
        "groups": group_summaries,
        "pending_sample": pending_rows[-10:],
        "resolved_sample": resolved_rows[-10:],
        "next_actions": [
            "Keep running v1.9 on fresh composite reports until there are at least 30 resolved observations per alert/tf group.",
            "Do not trade from alert evidence. Promote only to a separate hardening test when sample size and outcomes pass the gate.",
            "If evidence stays negative_or_mixed, keep the alert as context or remove it from the dashboard.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    lines = [
        "# MAX Core Lite v2.0 Forward Evidence",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Tracker: `{report['inputs']['tracker']}`",
        f"- Log: `{report['inputs']['log']}`",
        f"- Trade permission: `{report['policy']['trade_permission']}`",
        f"- Overall classification: **{overall['classification']}**",
        f"- Resolved / pending: `{overall['resolved']}` / `{overall['pending']}`",
        f"- Hit-rate: `{overall['hit_rate_pct']}`",
        f"- Avg directional return pct: `{overall['avg_directional_return_pct']}`",
        "",
        "## Groups",
        "",
    ]
    groups = report.get("groups") or []
    if groups:
        lines.append("| Alert | TF | Pair | Side | Resolved | Pending | Hit-rate | Avg dir. return | Classification |")
        lines.append("|---|---:|---|---|---:|---:|---:|---:|---|")
        for item in groups:
            lines.append(
                "| "
                f"`{item['alert_id']}` | `{item['tf']}` | `{item['pair']}` | `{item['side_context']}` | "
                f"`{item['resolved']}` | `{item['pending']}` | `{item['hit_rate_pct']}` | "
                f"`{item['avg_directional_return_pct']}` | `{item['classification']}` |"
            )
    else:
        lines.append("- No tracker rows yet.")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This is evidence scoring only. It does not create orders, does not unlock entries, and does not change risk.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX Core Lite v2.0 forward-evidence scoreboard")
    parser.add_argument("--tracker", default="logs/market_state_alerts/forward_tracker.jsonl")
    parser.add_argument("--log", default="logs/market_state_alerts/market_state_alerts.jsonl")
    parser.add_argument("--out-prefix", default="_dl/control_panel/MAX_CORE_LITE_V20_FORWARD_EVIDENCE")
    parser.add_argument("--min-resolved", type=int, default=30)
    parser.add_argument("--min-hit-rate-pct", type=float, default=55.0)
    parser.add_argument("--min-avg-return-pct", type=float, default=0.0)
    args = parser.parse_args()

    report = build_report(
        tracker_path=Path(args.tracker),
        log_path=Path(args.log),
        min_resolved=max(1, args.min_resolved),
        min_hit_rate_pct=args.min_hit_rate_pct,
        min_avg_return_pct=args.min_avg_return_pct,
    )
    out_prefix = Path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
