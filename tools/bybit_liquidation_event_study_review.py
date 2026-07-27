#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DIRECTIONAL_CONTEXTS = ("long_liquidation_flush", "short_liquidation_squeeze")
DIRECTIONS = ("continuation", "reversal")


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
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_float(value: Any, default: float = -999999.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def candidate_rows(study: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_horizon = study.get("by_horizon") if isinstance(study.get("by_horizon"), dict) else {}
    for context in DIRECTIONAL_CONTEXTS:
        for direction in DIRECTIONS:
            points: list[dict[str, Any]] = []
            for horizon, contexts in sorted(by_horizon.items(), key=lambda item: int(item[0])):
                ctx = contexts.get(context) if isinstance(contexts, dict) else None
                if not isinstance(ctx, dict) or not ctx.get("sample_ready"):
                    continue
                summary = ctx.get(direction) if isinstance(ctx.get(direction), dict) else {}
                n = safe_int(summary.get("n"))
                mean_bps = safe_float(summary.get("mean_bps"))
                winrate = safe_float(summary.get("winrate_positive_pct"), 0.0)
                median_bps = safe_float(summary.get("median_bps"))
                qualified = n >= args.min_n and mean_bps >= args.min_mean_bps and winrate >= args.min_winrate_pct
                points.append(
                    {
                        "horizon": int(horizon),
                        "n": n,
                        "mean_bps": mean_bps,
                        "median_bps": median_bps,
                        "winrate_positive_pct": winrate,
                        "qualified": qualified,
                    }
                )
            qualified_points = [item for item in points if item["qualified"]]
            if not points:
                continue
            mean_of_means = sum(item["mean_bps"] for item in points) / len(points)
            qualified_mean = sum(item["mean_bps"] for item in qualified_points) / len(qualified_points) if qualified_points else None
            rows.append(
                {
                    "context": context,
                    "direction": direction,
                    "qualified_horizons": len(qualified_points),
                    "total_ready_horizons": len(points),
                    "qualified_mean_bps": round(qualified_mean, 6) if qualified_mean is not None else None,
                    "mean_of_ready_means_bps": round(mean_of_means, 6),
                    "min_ready_winrate_pct": round(min(item["winrate_positive_pct"] for item in points), 3),
                    "max_ready_horizon": max(item["horizon"] for item in points),
                    "points": points,
                }
            )
    rows.sort(
        key=lambda item: (
            item["qualified_horizons"],
            safe_float(item["qualified_mean_bps"]),
            item["total_ready_horizons"],
            item["mean_of_ready_means_bps"],
        ),
        reverse=True,
    )
    return rows


def build_lock_draft(best: dict[str, Any], study: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    context = best["context"]
    direction = best["direction"]
    if context == "short_liquidation_squeeze" and direction == "continuation":
        side = "long_bias_watch_only"
        plain = "After a Bybit short-liquidation squeeze bar, monitor whether continuation upside persists on untouched forward events."
    elif context == "long_liquidation_flush" and direction == "continuation":
        side = "short_bias_watch_only"
        plain = "After a Bybit long-liquidation flush bar, monitor whether continuation downside persists on untouched forward events."
    elif context == "long_liquidation_flush" and direction == "reversal":
        side = "long_reversal_watch_only"
        plain = "After a Bybit long-liquidation flush bar, monitor whether rebound/reversal upside persists on untouched forward events."
    else:
        side = "short_reversal_watch_only"
        plain = "After a Bybit short-liquidation squeeze bar, monitor whether reversal downside persists on untouched forward events."
    horizons = [item["horizon"] for item in best["points"] if item["qualified"]]
    return {
        "lock_id": f"bybit_{context}_{direction}_forward_review_2026_07_02",
        "status": "draft_forward_observer_lock_not_runtime",
        "created_at": now_iso(),
        "can_trade": False,
        "orders_allowed": False,
        "source_study": study.get("inputs", {}).get("context_csv"),
        "discovery_report": portable(resolve_path(args.event_study)),
        "hypothesis": {
            "plain_english": plain,
            "context": context,
            "direction": direction,
            "side": side,
            "symbols": study.get("inputs", {}).get("symbols"),
            "interval": study.get("inputs", {}).get("interval"),
            "candidate_horizons_bars": horizons,
        },
        "forward_gate_required": {
            "minimum_new_events": 30,
            "minimum_new_events_per_context": 15,
            "minimum_positive_horizons": max(1, min(2, len(horizons))),
            "minimum_mean_bps_after_cost_buffer": 0.0,
            "no_parameter_changes": True,
            "manual_review_before_any_paper": True,
        },
        "boundary": {
            "discovery_sample_not_validation": True,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Bybit Liquidation Event Study Review",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `false`",
        f"- Event study: `{report['event_study_path']}`",
        "",
        "## Boundary",
        "",
        "- Review only. This is not validation, not a signal engine and not paper/live permission.",
        "- The current event-study sample is discovery data; any selected idea must go to untouched forward observation.",
        "- No parameters may be changed after the forward lock is accepted.",
        "",
        "## Candidate Ranking",
        "",
        "| Rank | Context | Direction | Qualified horizons | Mean bps | Min winrate | Notes |",
        "|---:|---|---|---:|---:|---:|---|",
    ]
    for index, row in enumerate(report["candidate_rows"], start=1):
        qualified = [str(item["horizon"]) for item in row["points"] if item["qualified"]]
        lines.append(
            f"| {index} | `{row['context']}` | `{row['direction']}` | `{','.join(qualified) or 'none'}` | "
            f"`{row['qualified_mean_bps']}` | `{row['min_ready_winrate_pct']}` | ready `{row['total_ready_horizons']}` horizons |"
        )
    best = report.get("selected_forward_candidate")
    lines.extend(["", "## Selected Forward Candidate", ""])
    if best:
        lines.extend(
            [
                f"- Context: `{best['context']}`",
                f"- Direction: `{best['direction']}`",
                f"- Qualified horizons: `{','.join(str(item['horizon']) for item in best['points'] if item['qualified'])}`",
                f"- Lock draft: `{report.get('lock_draft_path')}`",
            ]
        )
    else:
        lines.append("- `none`")
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Review Bybit liquidation event-study results and draft one forward-only lock.")
    parser.add_argument("--event-study", default="docs/BYBIT_ALL_LIQUIDATION_EVENT_STUDY_2026-07-02_AFTER_PRICE_GAP_FILL.json")
    parser.add_argument("--min-n", type=int, default=10)
    parser.add_argument("--min-mean-bps", type=float, default=15.0)
    parser.add_argument("--min-winrate-pct", type=float, default=55.0)
    parser.add_argument("--min-qualified-horizons", type=int, default=2)
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_EVENT_STUDY_REVIEW_2026-07-02")
    parser.add_argument("--lock-draft", default="configs/BYBIT_LIQUIDATION_FORWARD_LOCK_DRAFT_2026-07-02.json")
    args = parser.parse_args()
    study_path = resolve_path(args.event_study)
    study = read_json(study_path)
    rows = candidate_rows(study, args)
    best = rows[0] if rows and rows[0]["qualified_horizons"] >= args.min_qualified_horizons else None
    decision = "bybit_liquidation_review_forward_candidate_selected" if best else "bybit_liquidation_review_no_forward_candidate"
    lock_path = resolve_path(args.lock_draft)
    lock_draft = build_lock_draft(best, study, args) if best else None
    if lock_draft:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(lock_draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "generated_at": now_iso(),
        "tool": "tools/bybit_liquidation_event_study_review.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "event_study_path": portable(study_path),
        "review_thresholds": {
            "min_n": args.min_n,
            "min_mean_bps": args.min_mean_bps,
            "min_winrate_pct": args.min_winrate_pct,
            "min_qualified_horizons": args.min_qualified_horizons,
        },
        "candidate_rows": rows,
        "selected_forward_candidate": best,
        "lock_draft_path": portable(lock_path) if lock_draft else None,
        "next_action": (
            "accept or reject the forward lock draft; if accepted, build observer-only forward scorer with no retune"
            if best
            else "keep collecting events; do not create a forward observer from this sample"
        ),
    }
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "selected": {
                    "context": best.get("context"),
                    "direction": best.get("direction"),
                    "qualified_horizons": best.get("qualified_horizons"),
                }
                if best
                else None,
                "lock_draft": portable(lock_path) if lock_draft else None,
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
