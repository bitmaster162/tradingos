#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MINUTE_MS = 60_000
VENUES = ("binance", "coinbase")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_from_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="minutes")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def pct(part: int, whole: int) -> float:
    return round(part / whole * 100.0, 6) if whole else 0.0


def coverage_status(value: float, required: float) -> str:
    if value >= required:
        return "pass"
    if value >= required - 1.0:
        return "near_miss"
    return "fail"


def compact_runs(minutes: list[int]) -> list[dict[str, Any]]:
    if not minutes:
        return []
    output: list[dict[str, Any]] = []
    start = previous = minutes[0]
    for minute in minutes[1:]:
        if minute == previous + MINUTE_MS:
            previous = minute
            continue
        output.append({
            "start": iso_from_ms(start),
            "end": iso_from_ms(previous),
            "minutes": int((previous - start) // MINUTE_MS + 1),
        })
        start = previous = minute
    output.append({
        "start": iso_from_ms(start),
        "end": iso_from_ms(previous),
        "minutes": int((previous - start) // MINUTE_MS + 1),
    })
    return output


def minute_sets(conn: sqlite3.Connection, start_ms: int, end_ms: int) -> dict[str, dict[str, set[int]]]:
    sets: dict[str, dict[str, set[int]]] = {}
    for venue in VENUES:
        rows = conn.execute(
            """
            SELECT minute_ms,trades,book_snapshots
            FROM minute_features
            WHERE venue=? AND minute_ms>=? AND minute_ms<=?
            """,
            (venue, start_ms, end_ms),
        ).fetchall()
        trade_minutes: set[int] = set()
        book_minutes: set[int] = set()
        feature_minutes: set[int] = set()
        for minute_ms, trades, book_snapshots in rows:
            minute = int(minute_ms)
            feature_minutes.add(minute)
            if int(trades or 0) > 0:
                trade_minutes.add(minute)
            if int(book_snapshots or 0) > 0:
                book_minutes.add(minute)
        sets[venue] = {
            "features": feature_minutes,
            "trades": trade_minutes,
            "books": book_minutes,
        }
    return sets


def window_summary(minutes: list[int], sets: dict[str, dict[str, set[int]]]) -> dict[str, Any]:
    dual_trade = {
        minute for minute in minutes
        if all(minute in sets[venue]["trades"] for venue in VENUES)
    }
    dual_book = {
        minute for minute in minutes
        if all(minute in sets[venue]["books"] for venue in VENUES)
    }
    missing_dual_book = [minute for minute in minutes if minute not in dual_book]
    neither_book = {
        minute for minute in minutes
        if all(minute not in sets[venue]["books"] for venue in VENUES)
    }
    one_sided_book = [
        minute for minute in minutes
        if minute not in neither_book and minute not in dual_book
    ]
    return {
        "minutes": len(minutes),
        "dual_trade_minutes": len(dual_trade),
        "dual_trade_coverage_pct": pct(len(dual_trade), len(minutes)),
        "dual_book_minutes": len(dual_book),
        "dual_book_coverage_pct": pct(len(dual_book), len(minutes)),
        "missing_dual_book_minutes": len(missing_dual_book),
        "neither_venue_book_minutes": len(neither_book),
        "one_sided_book_minutes": len(one_sided_book),
    }


def recent_windows(all_minutes: list[int], sets: dict[str, dict[str, set[int]]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for hours in (1, 6, 12, 24, 48, 72, 168):
        count = min(len(all_minutes), hours * 60)
        minutes = all_minutes[-count:]
        output[f"{hours}h"] = window_summary(minutes, sets)
    return output


def perfect_future_eta(
    all_minutes: list[int],
    missing_dual_book: list[int],
    required_pct: float,
) -> dict[str, Any]:
    if not all_minutes:
        return {"status": "no_data"}
    window_minutes = len(all_minutes)
    target_covered = math.ceil(window_minutes * required_pct / 100.0)
    target_missing = max(0, window_minutes - target_covered)
    current_missing = len(missing_dual_book)
    if current_missing <= target_missing:
        return {
            "status": "already_above_required",
            "target_missing_minutes": target_missing,
            "current_missing_minutes": current_missing,
            "eta_utc": iso_from_ms(all_minutes[-1]),
            "minutes_from_latest": 0,
        }
    need_to_drop = current_missing - target_missing
    if need_to_drop > len(missing_dual_book):
        return {
            "status": "not_estimable",
            "target_missing_minutes": target_missing,
            "current_missing_minutes": current_missing,
        }
    threshold_missing_minute = missing_dual_book[need_to_drop - 1]
    eta_ms = threshold_missing_minute + window_minutes * MINUTE_MS
    return {
        "status": "perfect_future_no_new_gaps",
        "assumption": "Every future minute has both Binance and Coinbase book snapshots.",
        "target_missing_minutes": target_missing,
        "current_missing_minutes": current_missing,
        "missing_minutes_to_roll_out": need_to_drop,
        "threshold_missing_minute_utc": iso_from_ms(threshold_missing_minute),
        "eta_utc": iso_from_ms(eta_ms),
        "minutes_from_latest": int(max(0, (eta_ms - all_minutes[-1]) // MINUTE_MS)),
    }


def classify_coverage(
    current: dict[str, Any],
    recent: dict[str, Any],
    missing_dual_book: list[int],
    latest_ms: int,
    required_pct: float,
    recovery_confirmation_minutes: int = 30,
) -> tuple[str, str, dict[str, Any]]:
    """Separate an active polling fault from a recovered gap rolling out of history."""
    confirmation_minutes = max(0, int(recovery_confirmation_minutes))
    last_missing_ms = max(missing_dual_book) if missing_dual_book else None
    minutes_since_last_missing = (
        max(0, int((latest_ms - last_missing_ms) // MINUTE_MS))
        if last_missing_ms is not None
        else None
    )
    recovery = {
        "last_missing_minute_utc": iso_from_ms(last_missing_ms),
        "minutes_since_last_missing": minutes_since_last_missing,
        "confirmation_required_minutes": confirmation_minutes,
        "confirmed_since_last_gap": bool(
            minutes_since_last_missing is not None
            and minutes_since_last_missing >= confirmation_minutes
        ),
    }

    recent_1h = float(recent.get("1h", {}).get("dual_book_coverage_pct") or 0.0)
    recent_6h = float(recent.get("6h", {}).get("dual_book_coverage_pct") or 0.0)
    current_coverage = float(current.get("dual_book_coverage_pct") or 0.0)
    if current_coverage >= required_pct:
        return (
            "microstructure_book_coverage_pass",
            "Rerun snapshot gate after SLA replay is stable.",
            recovery,
        )
    if recent_1h >= required_pct and recent_6h >= required_pct:
        return (
            "microstructure_book_coverage_wait_for_old_gaps_to_roll_out",
            "Keep collector running; if no new gaps appear, wait for the rolling-window ETA before sealing.",
            recovery,
        )
    if recent_6h >= required_pct and recovery["confirmed_since_last_gap"]:
        return (
            "microstructure_book_coverage_recovered_waiting_recent_gap_rollout",
            "Collector recovery is confirmed; keep it running and wait for recent and historical gaps to roll out before sealing.",
            recovery,
        )
    if recent_1h >= required_pct:
        return (
            "microstructure_book_coverage_partial_recent_recovery",
            "Keep collector running and inspect sub-hour book polling jitter; 6h coverage is still below threshold.",
            recovery,
        )
    return (
        "microstructure_book_coverage_current_polling_degraded",
        "Inspect collector scheduling/network/book fetchers before waiting for old gaps to roll out.",
        recovery,
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    db_path = resolve_path(args.db)
    if not db_path.is_file():
        return {
            "generated_at": now_iso(),
            "decision": "microstructure_book_coverage_diagnostic_missing_db",
            "db_path": str(db_path),
            "runtime_boundary": runtime_boundary(),
            "can_trade": False,
        }

    conn = sqlite3.connect(db_path)
    latest_row = conn.execute("SELECT MAX(minute_ms) FROM minute_features").fetchone()
    latest_ms = int(latest_row[0]) if latest_row and latest_row[0] is not None else None
    if latest_ms is None:
        return {
            "generated_at": now_iso(),
            "decision": "microstructure_book_coverage_diagnostic_no_minute_features",
            "db_path": str(db_path),
            "runtime_boundary": runtime_boundary(),
            "can_trade": False,
        }

    window_minutes = max(1, int(args.window_hours * 60))
    start_ms = latest_ms - (window_minutes - 1) * MINUTE_MS
    minutes = list(range(start_ms, latest_ms + MINUTE_MS, MINUTE_MS))
    sets = minute_sets(conn, start_ms, latest_ms)
    current = window_summary(minutes, sets)
    recent = recent_windows(minutes, sets)
    dual_book_set = {
        minute for minute in minutes
        if all(minute in sets[venue]["books"] for venue in VENUES)
    }
    missing_dual_book = [minute for minute in minutes if minute not in dual_book_set]
    gap_runs = compact_runs(missing_dual_book)
    largest_gaps = sorted(gap_runs, key=lambda item: int(item["minutes"]), reverse=True)[:10]
    missing_by_venue = {
        venue: len([minute for minute in minutes if minute not in sets[venue]["books"]])
        for venue in VENUES
    }
    book_minutes_by_venue = {
        venue: len([minute for minute in minutes if minute in sets[venue]["books"]])
        for venue in VENUES
    }
    trade_minutes_by_venue = {
        venue: len([minute for minute in minutes if minute in sets[venue]["trades"]])
        for venue in VENUES
    }
    eta = perfect_future_eta(minutes, missing_dual_book, args.required_book_coverage_pct)

    decision, next_action, recovery = classify_coverage(
        current,
        recent,
        missing_dual_book,
        latest_ms,
        args.required_book_coverage_pct,
        getattr(args, "recovery_confirmation_minutes", 30),
    )

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "db_path": str(db_path),
        "window": {
            "hours": args.window_hours,
            "minutes": window_minutes,
            "first_minute_utc": iso_from_ms(start_ms),
            "last_minute_utc": iso_from_ms(latest_ms),
            "required_book_coverage_pct": args.required_book_coverage_pct,
        },
        "coverage": current,
        "venue_breakdown": {
            "book_minutes_by_venue": book_minutes_by_venue,
            "trade_minutes_by_venue": trade_minutes_by_venue,
            "missing_book_minutes_by_venue": missing_by_venue,
        },
        "recent_windows": recent,
        "recovery": recovery,
        "missing_dual_book": {
            "gap_count": len(gap_runs),
            "first_gaps": gap_runs[:10],
            "last_gaps": gap_runs[-10:],
            "largest_gaps": largest_gaps,
        },
        "eta": eta,
        "next_action": next_action,
        "runtime_boundary": runtime_boundary(),
        "can_trade": False,
    }


def runtime_boundary() -> dict[str, bool]:
    return {
        "observability_only": True,
        "signals_allowed": False,
        "paper_entries_allowed": False,
        "orders_allowed": False,
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    window = report.get("window") if isinstance(report.get("window"), dict) else {}
    recent = report.get("recent_windows") if isinstance(report.get("recent_windows"), dict) else {}
    venue = report.get("venue_breakdown") if isinstance(report.get("venue_breakdown"), dict) else {}
    eta = report.get("eta") if isinstance(report.get("eta"), dict) else {}
    gaps = report.get("missing_dual_book") if isinstance(report.get("missing_dual_book"), dict) else {}
    recovery = report.get("recovery") if isinstance(report.get("recovery"), dict) else {}

    lines = [
        "# Cross-Venue Microstructure Book Coverage Diagnostic",
        "",
        f"- Generated: `{report.get('generated_at')}`.",
        f"- Decision: `{report.get('decision')}`.",
        f"- Window: `{window.get('first_minute_utc')}` to `{window.get('last_minute_utc')}` (`{window.get('minutes')}` minutes).",
        f"- Required dual-book coverage: `{window.get('required_book_coverage_pct')}%`.",
        f"- Current dual-book coverage: `{coverage.get('dual_book_coverage_pct')}%`.",
        f"- Current dual-trade coverage: `{coverage.get('dual_trade_coverage_pct')}%`.",
        f"- Missing dual-book minutes: `{coverage.get('missing_dual_book_minutes')}`.",
        f"- Neither-venue book missing minutes: `{coverage.get('neither_venue_book_minutes')}`.",
        f"- One-sided book missing minutes: `{coverage.get('one_sided_book_minutes')}`.",
        "",
        "## Venue Breakdown",
        f"- Book minutes by venue: `{venue.get('book_minutes_by_venue')}`.",
        f"- Missing book minutes by venue: `{venue.get('missing_book_minutes_by_venue')}`.",
        f"- Trade minutes by venue: `{venue.get('trade_minutes_by_venue')}`.",
        "",
        "## Recent Dual-Book Coverage",
    ]
    for label in ("1h", "6h", "12h", "24h", "48h", "72h", "168h"):
        item = recent.get(label) if isinstance(recent.get(label), dict) else {}
        lines.append(f"- `{label}`: `{item.get('dual_book_coverage_pct')}%`, missing `{item.get('missing_dual_book_minutes')}` / `{item.get('minutes')}`.")

    lines.extend([
        "",
        "## Gap Shape",
        f"- Gap runs: `{gaps.get('gap_count')}`.",
        f"- Largest gaps: `{gaps.get('largest_gaps')}`.",
        f"- Last gaps: `{gaps.get('last_gaps')}`.",
        "",
        "## Recovery Classification",
        f"- Last missing minute: `{recovery.get('last_missing_minute_utc')}`.",
        f"- Stored minutes since last gap: `{recovery.get('minutes_since_last_missing')}`.",
        f"- Confirmation requirement: `{recovery.get('confirmation_required_minutes')}` minutes.",
        f"- Recovery confirmed: `{recovery.get('confirmed_since_last_gap')}`.",
        "",
        "## Perfect-Future ETA",
        f"- Status: `{eta.get('status')}`.",
        f"- ETA UTC: `{eta.get('eta_utc')}`.",
        f"- Minutes from latest stored minute: `{eta.get('minutes_from_latest')}`.",
        f"- Assumption: `{eta.get('assumption')}`.",
        "",
        "## Next Action",
        f"- {report.get('next_action')}",
        "",
        "## Runtime Boundary",
        "- Observability only.",
        "- No signals, no paper entries, no orders.",
        "- `can_trade=false`.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose dual-venue top-of-book minute coverage for microstructure sealing.")
    parser.add_argument("--db", default="data/cross_venue_microstructure/microstructure.sqlite3")
    parser.add_argument("--window-hours", type=int, default=168)
    parser.add_argument("--required-book-coverage-pct", type=float, default=95.0)
    parser.add_argument("--recovery-confirmation-minutes", type=int, default=30)
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_COVERAGE_DIAGNOSTIC_2026-07-03")
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    write_json(json_path, report)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")

    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    eta = report.get("eta") if isinstance(report.get("eta"), dict) else {}
    print(json.dumps({
        "decision": report.get("decision"),
        "dual_book_coverage_pct": coverage.get("dual_book_coverage_pct"),
        "missing_dual_book_minutes": coverage.get("missing_dual_book_minutes"),
        "eta_utc": eta.get("eta_utc"),
        "out": str(json_path.relative_to(ROOT)) if json_path.is_relative_to(ROOT) else str(json_path),
        "can_trade": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
