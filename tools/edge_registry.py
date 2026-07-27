#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_REPORT_PREFIXES = (
    "EDGE_REGISTRY_",
    "EDGE_FORWARD_",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def as_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def r6(value: Any) -> float | None:
    parsed = as_float(value)
    return None if parsed is None else round(parsed, 6)


def candidate_id(item: dict[str, Any], fallback: str) -> str:
    for key in ("strategy_id", "id", "candidate_id", "base_strategy_id", "variant_id", "name"):
        value = item.get(key)
        if value:
            return str(value)
    return fallback


def summary_from(item: dict[str, Any], *paths: str) -> dict[str, Any]:
    for raw_path in paths:
        cursor: Any = item
        for part in raw_path.split("."):
            if isinstance(cursor, dict):
                cursor = cursor.get(part)
            else:
                cursor = None
                break
        if isinstance(cursor, dict):
            return cursor
    return {}


def stable_folds(item: dict[str, Any]) -> int:
    if "stable_folds" in item:
        return as_int(item.get("stable_folds"))
    for path in ("gate.stable_folds", "full.stable_folds", "walk_forward.stable_windows"):
        value = summary_from({"x": item}, f"x.{path}")
        if value:
            return as_int(value)
    folds = item.get("folds")
    if not isinstance(folds, list):
        folds = item.get("full", {}).get("folds") if isinstance(item.get("full"), dict) else None
    if isinstance(folds, list):
        return sum(1 for row in folds if isinstance(row, dict) and row.get("stable"))
    return 0


def segment_positive_ratio(item: dict[str, Any]) -> float | None:
    explicit = as_float(item.get("segment_positive_ratio"))
    if explicit is not None:
        return explicit
    segments = item.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    positives = 0
    total = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        summary = segment.get("summary") if isinstance(segment.get("summary"), dict) else segment
        exp = as_float(summary.get("expectancy_r"))
        if exp is None:
            continue
        total += 1
        if exp > 0:
            positives += 1
    return round(positives / total, 6) if total else None


def cost_stress_10_expectancy(item: dict[str, Any]) -> float | None:
    rows = item.get("cost_stress")
    if not isinstance(rows, list):
        return None
    best_match: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        bps = as_float(row.get("extra_bps_per_side"))
        if bps == 10.0:
            best_match = row
            break
    if best_match is None:
        return None
    summary = best_match.get("summary") if isinstance(best_match.get("summary"), dict) else best_match
    return r6(summary.get("expectancy_r"))


def explicit_verdict(item: dict[str, Any]) -> str | None:
    for key in ("verdict", "source_verdict", "decision", "promotion_decision", "holdout_verdict", "effective_holdout_verdict"):
        value = item.get(key)
        if value:
            return str(value)
    gate = item.get("gate") if isinstance(item.get("gate"), dict) else {}
    if gate.get("verdict"):
        return str(gate.get("verdict"))
    research_gate = item.get("research_gate") if isinstance(item.get("research_gate"), dict) else {}
    if research_gate.get("decision"):
        return str(research_gate.get("decision"))
    return None


def normalize_candidate(path: Path, item: dict[str, Any], index: int, container: str) -> dict[str, Any] | None:
    cid = candidate_id(item, f"{path.stem}:{container}:{index}")
    full = summary_from(item, "full.summary", "summary", "all_summary", "walk_forward.all_summary")
    holdout = summary_from(item, "holdout.summary", "test_summary", "walk_forward.selected_test_summary")
    if not full and not holdout:
        return None

    full_trades = as_int(full.get("trades"))
    full_exp = r6(full.get("expectancy_r"))
    full_win = r6(full.get("winrate_pct"))
    full_dd = r6(full.get("max_drawdown_r"))
    full_loss_streak = as_int(full.get("max_losing_streak"))
    holdout_trades = as_int(holdout.get("trades"))
    holdout_exp = r6(holdout.get("expectancy_r"))
    holdout_win = r6(holdout.get("winrate_pct"))
    stable = stable_folds(item)
    seg_pos = segment_positive_ratio(item)
    cost10 = cost_stress_10_expectancy(item)

    if full_trades == 0 and holdout_trades == 0:
        return None

    score, blocks, strengths = evidence_score(
        full_trades=full_trades,
        full_exp=full_exp,
        full_win=full_win,
        holdout_trades=holdout_trades,
        holdout_exp=holdout_exp,
        holdout_win=holdout_win,
        stable=stable,
        seg_pos=seg_pos,
        cost10=cost10,
        verdict=explicit_verdict(item),
    )
    return {
        "candidate_id": cid,
        "source": path.relative_to(ROOT).as_posix(),
        "container": container,
        "index": index,
        "family": item.get("family"),
        "interval": item.get("interval"),
        "side": item.get("side"),
        "trigger": item.get("trigger"),
        "rr": item.get("rr"),
        "signals": item.get("signals"),
        "explicit_verdict": explicit_verdict(item),
        "metrics": {
            "full_trades": full_trades,
            "full_winrate_pct": full_win,
            "full_expectancy_r": full_exp,
            "full_max_drawdown_r": full_dd,
            "full_max_losing_streak": full_loss_streak,
            "holdout_trades": holdout_trades,
            "holdout_winrate_pct": holdout_win,
            "holdout_expectancy_r": holdout_exp,
            "stable_folds": stable,
            "segment_positive_ratio": seg_pos,
            "cost10_expectancy_r": cost10,
        },
        "evidence_score": score,
        "edge_classification": classify_edge(score, blocks, full_trades, holdout_trades, holdout_exp, stable),
        "strengths": strengths,
        "blocks": blocks,
        "next_action": next_action(score, blocks, holdout_trades, stable),
    }


def evidence_score(
    *,
    full_trades: int,
    full_exp: float | None,
    full_win: float | None,
    holdout_trades: int,
    holdout_exp: float | None,
    holdout_win: float | None,
    stable: int,
    seg_pos: float | None,
    cost10: float | None,
    verdict: str | None,
) -> tuple[int, list[str], list[str]]:
    score = 0
    blocks: list[str] = []
    strengths: list[str] = []

    if full_trades >= 100:
        score += 18
        strengths.append("sample_100_plus")
    elif full_trades >= 50:
        score += 10
        strengths.append("sample_50_plus")
    elif full_trades >= 30:
        score += 5
    else:
        blocks.append("full_sample_under_30")

    if full_exp is not None and full_exp > 0:
        gain = min(20, max(5, int(full_exp * 30)))
        score += gain
        strengths.append("positive_full_expectancy")
    else:
        blocks.append("full_expectancy_not_positive")

    if full_win is not None and full_win >= 50:
        score += 5
        strengths.append("full_winrate_ge_50")

    if holdout_trades >= 20:
        score += 15
        strengths.append("holdout_20_plus")
    elif holdout_trades >= 10:
        score += 7
        blocks.append("holdout_sample_under_20")
    else:
        blocks.append("missing_or_small_holdout")

    if holdout_exp is not None and holdout_exp > 0:
        score += min(20, max(8, int(holdout_exp * 30)))
        strengths.append("positive_holdout_expectancy")
    else:
        blocks.append("holdout_expectancy_not_positive")

    if holdout_win is not None and holdout_win >= 50:
        score += 5
        strengths.append("holdout_winrate_ge_50")

    if stable >= 5:
        score += 15
        strengths.append("stable_folds_5_plus")
    elif stable >= 3:
        score += 8
        strengths.append("stable_folds_3_plus")
    else:
        blocks.append("stable_folds_under_3")

    if seg_pos is not None:
        if seg_pos >= 0.6:
            score += 8
            strengths.append("segments_positive_60pct_plus")
        elif seg_pos < 0.5:
            blocks.append("segments_not_consistently_positive")

    if cost10 is not None:
        if cost10 > 0:
            score += 8
            strengths.append("cost10_positive")
        else:
            blocks.append("cost10_not_positive")

    if verdict and verdict in {"do_not_trade", "holdout_fail_do_not_trade", "research_only_rejected"}:
        score -= 30
        blocks.append(f"explicit_block:{verdict}")
    elif verdict and "blocked" in verdict:
        score -= 10
        blocks.append(f"explicit_block:{verdict}")

    return max(0, min(100, score)), blocks, strengths


def classify_edge(score: int, blocks: list[str], full_trades: int, holdout_trades: int, holdout_exp: float | None, stable: int) -> str:
    hard_blocks = {"full_expectancy_not_positive", "holdout_expectancy_not_positive", "missing_or_small_holdout"}
    if (
        score >= 75
        and full_trades >= 50
        and holdout_trades >= 20
        and (holdout_exp or -999) > 0
        and stable >= 3
        and "cost10_not_positive" not in blocks
    ):
        return "edge_candidate_forward_proof_required"
    if score >= 55 and not hard_blocks.intersection(blocks):
        return "research_watchlist_needs_forward_evidence"
    if score >= 45 and "full_expectancy_not_positive" not in blocks:
        return "weak_edge_needs_more_data_or_filters"
    return "blocked_no_reliable_edge"


def next_action(score: int, blocks: list[str], holdout_trades: int, stable: int) -> str:
    if "cost10_not_positive" in blocks:
        return "stress with fees/slippage; reject if cost sensitivity remains negative"
    if score >= 75 and holdout_trades >= 20 and stable >= 3:
        return "send_to_forward_observer_only; no paper/live until forward outcomes accumulate"
    if "missing_or_small_holdout" in blocks:
        return "run holdout/walk-forward validation before any forward observer"
    if "stable_folds_under_3" in blocks:
        return "improve regime filter or reject unstable candidate"
    return "keep in research queue; gather more evidence"


def collect_from_payload(path: Path, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    containers = [
        ("selected_candidate", payload.get("selected_candidate")),
        ("best_candidate", payload.get("best_candidate")),
        ("best_variant", payload.get("best_variant")),
    ]
    for name, value in containers:
        if isinstance(value, dict):
            candidate = normalize_candidate(path, value, 0, name)
            if candidate:
                rows.append(candidate)
    for name in ("top_results", "results", "all_results", "all_results_ranked", "candidates", "top_items", "validated_candidates"):
        value = payload.get(name)
        if not isinstance(value, list):
            continue
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            candidate = normalize_candidate(path, item, index, name)
            if candidate:
                rows.append(candidate)
    return rows


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["source"], row["candidate_id"])
        existing = best.get(key)
        if existing is None or row["evidence_score"] > existing["evidence_score"]:
            best[key] = row
    return list(best.values())


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Edge Registry",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Research registry only.",
        "- Does not change active strategy.",
        "- Does not create paper-entry intents.",
        "- Does not send orders.",
        "",
        "## Summary",
        "",
        f"- JSON reports scanned: `{report.get('json_reports_scanned')}`.",
        f"- Candidate rows extracted: `{report.get('candidate_rows_extracted')}`.",
        f"- Unique candidates: `{report.get('unique_candidates')}`.",
        f"- Forward-proof candidates: `{report.get('class_counts', {}).get('edge_candidate_forward_proof_required', 0)}`.",
        f"- Research watchlist: `{report.get('class_counts', {}).get('research_watchlist_needs_forward_evidence', 0)}`.",
        f"- Can trade: `{report.get('can_trade')}`.",
        "",
        "## Top Candidates",
        "",
        "| Rank | Score | Classification | Candidate | Source | Trades | Exp R | Holdout | Holdout Exp R | Stable | Blocks | Next |",
        "|---:|---:|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for index, row in enumerate(report.get("top_candidates", [])[:30], start=1):
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        blocks = "+".join(row.get("blocks") or []) or "-"
        lines.append(
            f"| {index} | `{row.get('evidence_score')}` | `{row.get('edge_classification')}` | `{row.get('candidate_id')}` | "
            f"`{row.get('source')}` | `{metrics.get('full_trades')}` | `{metrics.get('full_expectancy_r')}` | "
            f"`{metrics.get('holdout_trades')}` | `{metrics.get('holdout_expectancy_r')}` | `{metrics.get('stable_folds')}` | `{blocks}` | `{row.get('next_action')}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A high winrate alone is not enough. The registry rewards positive expectancy, holdout evidence, stable folds, segment consistency and cost stress.",
            "- `edge_candidate_forward_proof_required` means the candidate can be watched forward, not traded.",
            "- Any candidate without sufficient holdout or stable folds stays research-only.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Aggregate research reports into a ranked edge registry")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--out-prefix", default="docs/EDGE_REGISTRY_2026-06-18")
    parser.add_argument("--max-files", type=int, default=500)
    args = parser.parse_args()

    docs_dir = resolve_path(args.docs_dir)
    rows: list[dict[str, Any]] = []
    source_status: list[dict[str, Any]] = []
    for path in sorted(docs_dir.glob("*.json"))[: args.max_files]:
        if path.name.startswith(EXCLUDED_REPORT_PREFIXES):
            continue
        try:
            payload = read_json(path)
        except Exception as exc:  # noqa: BLE001
            source_status.append({"path": path.relative_to(ROOT).as_posix(), "read_error": type(exc).__name__, "candidates": 0})
            continue
        candidates = collect_from_payload(path, payload)
        source_status.append({"path": path.relative_to(ROOT).as_posix(), "candidates": len(candidates)})
        rows.extend(candidates)

    unique = dedupe(rows)
    ranked = sorted(
        unique,
        key=lambda row: (
            row.get("evidence_score") or 0,
            row.get("metrics", {}).get("holdout_trades") or 0,
            row.get("metrics", {}).get("full_trades") or 0,
            row.get("metrics", {}).get("holdout_expectancy_r") or -999,
        ),
        reverse=True,
    )
    class_counts: dict[str, int] = {}
    for row in ranked:
        classification = str(row.get("edge_classification") or "unknown")
        class_counts[classification] = class_counts.get(classification, 0) + 1
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "edge_registry_research_only",
            "can_trade": False,
            "sends_orders": False,
            "creates_paper_entry_intents": False,
            "changes_active_strategy": False,
        },
        "inputs": {
            "docs_dir": str(docs_dir),
            "max_files": args.max_files,
        },
        "json_reports_scanned": len(source_status),
        "candidate_rows_extracted": len(rows),
        "unique_candidates": len(unique),
        "class_counts": class_counts,
        "top_candidates": ranked[:50],
        "source_status": source_status,
        "decision": "edge_registry_built_research_only",
        "next_action": "Use top edge_candidate_forward_proof_required rows for observer-only forward proof; do not trade from registry alone.",
        "can_trade": False,
    }
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "unique_candidates": report["unique_candidates"],
                "forward_proof_candidates": class_counts.get("edge_candidate_forward_proof_required", 0),
                "research_watchlist": class_counts.get("research_watchlist_needs_forward_evidence", 0),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
