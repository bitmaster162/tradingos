#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SAFE_CLASSIFICATIONS = {
    "cross_venue_microstructure_forward_collecting",
    "cross_venue_microstructure_ready_for_preregistered_research",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(value: Any, now: datetime | None = None) -> float | None:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    current = now or now_utc()
    return round(max(0.0, (current - parsed).total_seconds() / 60.0), 6)


def nested(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def feature_retention_drop_allowance(
    previous_generated_at: Any,
    current_generated_at: Any,
    *,
    venue_rows_per_minute: int = 2,
) -> int | None:
    previous_ts = parse_utc(previous_generated_at)
    current_ts = parse_utc(current_generated_at)
    if previous_ts is None or current_ts is None or current_ts < previous_ts:
        return None
    elapsed_minutes = max(1, ceil((current_ts - previous_ts).total_seconds() / 60.0))
    return elapsed_minutes * max(1, venue_rows_per_minute)


def build_sla_report(
    data_quality: dict[str, Any],
    previous: dict[str, Any] | None,
    book_coverage_diagnostic: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    max_report_age_minutes: float = 5.0,
    min_inserted_trades: int = 1,
    min_inserted_books: int = 2,
    min_trade_coverage_pct: float = 95.0,
    min_book_coverage_pct: float = 95.0,
    max_coverage_regression_pct: float = 1.0,
) -> dict[str, Any]:
    previous = previous if isinstance(previous, dict) else {}
    book_coverage_diagnostic = book_coverage_diagnostic if isinstance(book_coverage_diagnostic, dict) else {}
    current_cycle = nested(data_quality, "current_cycle")
    archive = nested(data_quality, "archive")
    coverage = nested(data_quality, "coverage")
    integrity = nested(data_quality, "trade_id_integrity")
    runtime_boundary = nested(data_quality, "runtime_boundary")

    generated_at = data_quality.get("generated_at")
    report_age = age_minutes(generated_at, now)
    classification = str(data_quality.get("classification") or "")
    inserted_trades = safe_int(current_cycle.get("inserted_trades"))
    inserted_books = safe_int(current_cycle.get("inserted_books"))
    new_rows = safe_int(current_cycle.get("new_rows"))
    archive_trades = safe_int(archive.get("trades"))
    archive_books = safe_int(archive.get("book_snapshots"))
    archive_features = safe_int(archive.get("minute_feature_rows"))
    retention_hours = safe_float(archive.get("retention_hours"))
    span_hours = safe_float(coverage.get("span_hours"))
    trade_coverage = safe_float(coverage.get("both_trade_coverage_pct"))
    book_coverage = safe_float(coverage.get("both_book_coverage_pct"))
    binance_missing_ids = safe_int(nested(integrity, "binance").get("missing_ids"))
    coinbase_missing_ids = safe_int(nested(integrity, "coinbase").get("missing_ids"))
    recent_windows = nested(book_coverage_diagnostic, "recent_windows")
    recent_6h_book_coverage = safe_float(nested(recent_windows, "6h").get("dual_book_coverage_pct"))
    recent_24h_book_coverage = safe_float(nested(recent_windows, "24h").get("dual_book_coverage_pct"))
    recent_book_coverage_verified = (
        recent_6h_book_coverage is not None
        and recent_24h_book_coverage is not None
        and recent_6h_book_coverage >= min_book_coverage_pct
        and recent_24h_book_coverage >= min_book_coverage_pct
    )
    rolling_retention_enabled = retention_hours is not None and retention_hours > 0

    previous_archive_trades = safe_int(previous.get("archive_trades"))
    previous_archive_books = safe_int(previous.get("archive_books"))
    previous_archive_features = safe_int(previous.get("archive_features"))
    previous_trade_coverage = safe_float(previous.get("trade_coverage_pct"))
    previous_book_coverage = safe_float(previous.get("book_coverage_pct"))
    archive_trades_delta = None if archive_trades is None or previous_archive_trades is None else archive_trades - previous_archive_trades
    archive_books_delta = None if archive_books is None or previous_archive_books is None else archive_books - previous_archive_books
    archive_features_delta = None if archive_features is None or previous_archive_features is None else archive_features - previous_archive_features
    trade_coverage_delta = None if trade_coverage is None or previous_trade_coverage is None else round(trade_coverage - previous_trade_coverage, 6)
    book_coverage_delta = None if book_coverage is None or previous_book_coverage is None else round(book_coverage - previous_book_coverage, 6)
    feature_drop_rows = max(0, -archive_features_delta) if archive_features_delta is not None else None
    feature_drop_allowance_rows = feature_retention_drop_allowance(
        previous.get("data_generated_at"),
        generated_at,
    )
    feature_retention_drop_bounded = bool(
        feature_drop_rows is not None
        and feature_drop_allowance_rows is not None
        and feature_drop_rows <= feature_drop_allowance_rows
    )

    observed_checks = {
        "data_quality_present": bool(data_quality),
        "classification_safe": classification in SAFE_CLASSIFICATIONS,
        "report_fresh": report_age is not None and report_age <= max_report_age_minutes,
        "cycle_inserted_trades": inserted_trades is not None and inserted_trades >= min_inserted_trades,
        "cycle_inserted_books": inserted_books is not None and inserted_books >= min_inserted_books,
        "archive_trade_rows_not_regressed": archive_trades_delta is None or archive_trades_delta >= 0,
        "archive_book_rows_not_regressed": archive_books_delta is None or archive_books_delta >= 0,
        "archive_feature_rows_not_regressed": archive_features_delta is None or archive_features_delta >= 0,
        "trade_coverage_above_sla": trade_coverage is not None and trade_coverage >= min_trade_coverage_pct,
        "book_coverage_above_sla": book_coverage is not None and book_coverage >= min_book_coverage_pct,
        "coverage_not_sharply_regressed": (
            (trade_coverage_delta is None or trade_coverage_delta >= -max_coverage_regression_pct)
            and (book_coverage_delta is None or book_coverage_delta >= -max_coverage_regression_pct)
        ),
        "trade_id_gaps_zero": binance_missing_ids == 0 and coinbase_missing_ids == 0,
        "runtime_public_data_only": runtime_boundary.get("public_data_only") is True,
        "can_trade_false": data_quality.get("can_trade") is False and runtime_boundary.get("can_trade") is False,
    }
    checks = dict(observed_checks)
    checks.update(
        {
            "archive_trade_rows_retention_safe": observed_checks["archive_trade_rows_not_regressed"]
            or (rolling_retention_enabled and observed_checks["cycle_inserted_trades"]),
            "archive_book_rows_retention_safe": observed_checks["archive_book_rows_not_regressed"]
            or (rolling_retention_enabled and recent_book_coverage_verified),
            "archive_feature_rows_retention_safe": observed_checks["archive_feature_rows_not_regressed"]
            or (
                rolling_retention_enabled
                and feature_retention_drop_bounded
                and (observed_checks["book_coverage_above_sla"] or recent_book_coverage_verified)
                and observed_checks["coverage_not_sharply_regressed"]
                and observed_checks["cycle_inserted_books"]
                and recent_6h_book_coverage is not None
                and recent_6h_book_coverage >= min_book_coverage_pct
            ),
            "recent_6h_book_coverage_above_sla": recent_6h_book_coverage is not None
            and recent_6h_book_coverage >= min_book_coverage_pct,
            "recent_24h_book_coverage_above_sla": recent_24h_book_coverage is not None
            and recent_24h_book_coverage >= min_book_coverage_pct,
            "collector_book_coverage_above_sla": observed_checks["book_coverage_above_sla"]
            or recent_book_coverage_verified,
        }
    )
    hard_check_names = (
        "data_quality_present",
        "classification_safe",
        "report_fresh",
        "cycle_inserted_trades",
        "cycle_inserted_books",
        "archive_trade_rows_retention_safe",
        "archive_book_rows_retention_safe",
        "archive_feature_rows_retention_safe",
        "trade_coverage_above_sla",
        "collector_book_coverage_above_sla",
        "coverage_not_sharply_regressed",
        "trade_id_gaps_zero",
        "runtime_public_data_only",
        "can_trade_false",
    )
    hard_checks = {name: checks[name] for name in hard_check_names}
    failed_checks = [name for name, passed in hard_checks.items() if not passed]
    readiness_checks = {
        "rolling_168h_book_coverage_above_sla": observed_checks["book_coverage_above_sla"],
    }
    readiness_blockers = [name for name, passed in readiness_checks.items() if not passed]
    legacy_gap_recent_coverage_verified = bool(
        rolling_retention_enabled
        and not observed_checks["book_coverage_above_sla"]
        and recent_book_coverage_verified
    )

    if not hard_checks["data_quality_present"]:
        decision = "collector_sla_degraded_missing_data_quality"
        next_action = "run_microstructure_collector_before_sla_guard"
    elif not hard_checks["classification_safe"]:
        decision = "collector_sla_degraded_classification"
        next_action = "inspect_microstructure_collector_report_classification"
    elif not hard_checks["report_fresh"]:
        decision = "collector_sla_degraded_report_stale"
        next_action = "restart_or_repair_microstructure_collector_loop"
    elif not hard_checks["cycle_inserted_trades"]:
        decision = "collector_sla_degraded_no_trade_inserts"
        next_action = "inspect_trade_fetchers_and_exchange_connectivity"
    elif not hard_checks["cycle_inserted_books"]:
        decision = "collector_sla_degraded_no_book_inserts"
        next_action = "inspect_order_book_fetchers_and_exchange_connectivity"
    elif not hard_checks["archive_trade_rows_retention_safe"] or not hard_checks["archive_book_rows_retention_safe"] or not hard_checks["archive_feature_rows_retention_safe"]:
        decision = "collector_sla_degraded_archive_regressed"
        next_action = "inspect_sqlite_retention_migration_or_clock"
    elif not hard_checks["trade_coverage_above_sla"] or not hard_checks["collector_book_coverage_above_sla"]:
        decision = "collector_sla_degraded_coverage_below_sla"
        next_action = "continue_or_repair_dual_venue_collection"
    elif not hard_checks["coverage_not_sharply_regressed"]:
        decision = "collector_sla_degraded_coverage_regressed"
        next_action = "inspect_recent_collection_gaps"
    elif not hard_checks["trade_id_gaps_zero"]:
        decision = "collector_sla_degraded_trade_id_gaps"
        next_action = "run_gap_backfill_until_zero"
    elif not hard_checks["runtime_public_data_only"] or not hard_checks["can_trade_false"]:
        decision = "collector_sla_degraded_runtime_boundary"
        next_action = "restore_public_data_only_no_trade_boundary"
    elif legacy_gap_recent_coverage_verified:
        decision = "collector_sla_healthy_legacy_gap_rolling_out"
        next_action = "continue_collection_until_rolling_readiness_window_clears"
    elif previous_archive_trades is None:
        decision = "collector_sla_baseline_recorded"
        next_action = "continue_collection_and_compare_next_cycle"
    else:
        decision = "collector_sla_healthy"
        next_action = "continue_microstructure_collection"

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision": decision,
        "data_generated_at": generated_at,
        "report_age_minutes": report_age,
        "classification": classification or None,
        "new_rows": new_rows,
        "inserted_trades": inserted_trades,
        "inserted_books": inserted_books,
        "archive_trades": archive_trades,
        "archive_books": archive_books,
        "archive_features": archive_features,
        "retention_hours": retention_hours,
        "rolling_retention_enabled": rolling_retention_enabled,
        "archive_trades_delta": archive_trades_delta,
        "archive_books_delta": archive_books_delta,
        "archive_features_delta": archive_features_delta,
        "feature_retention_drop_rows": feature_drop_rows,
        "feature_retention_drop_allowance_rows": feature_drop_allowance_rows,
        "feature_retention_drop_bounded": feature_retention_drop_bounded,
        "span_hours": span_hours,
        "trade_coverage_pct": trade_coverage,
        "book_coverage_pct": book_coverage,
        "recent_6h_book_coverage_pct": recent_6h_book_coverage,
        "recent_24h_book_coverage_pct": recent_24h_book_coverage,
        "legacy_gap_recent_coverage_verified": legacy_gap_recent_coverage_verified,
        "trade_coverage_delta_pct": trade_coverage_delta,
        "book_coverage_delta_pct": book_coverage_delta,
        "binance_missing_ids": binance_missing_ids,
        "coinbase_missing_ids": coinbase_missing_ids,
        "checks": checks,
        "hard_checks": hard_checks,
        "failed_checks": failed_checks,
        "readiness_checks": readiness_checks,
        "readiness_blockers": readiness_blockers,
        "superseded_legacy_failure_checks": [
            "archive_trade_rows_not_regressed",
            "archive_book_rows_not_regressed",
            "archive_feature_rows_not_regressed",
            "book_coverage_above_sla",
        ]
        if legacy_gap_recent_coverage_verified
        else [],
        "next_action": next_action,
        "runtime_boundary": {
            "sla_guard_only": True,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Cross-Venue Microstructure Collector SLA Guard",
            "",
            f"- Generated: `{report['generated_at']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Data report: `{report.get('data_generated_at')}`, age `{report.get('report_age_minutes')}` minutes.",
            f"- Cycle inserts trades/books: `{report.get('inserted_trades')}` / `{report.get('inserted_books')}`.",
            f"- Archive deltas trades/books/features: `{report.get('archive_trades_delta')}` / `{report.get('archive_books_delta')}` / `{report.get('archive_features_delta')}`.",
            f"- Coverage trade/book: `{report.get('trade_coverage_pct')}` / `{report.get('book_coverage_pct')}`.",
            f"- Recent book coverage 6h/24h: `{report.get('recent_6h_book_coverage_pct')}` / `{report.get('recent_24h_book_coverage_pct')}`.",
            f"- Readiness blockers: `{', '.join(report.get('readiness_blockers') or []) or 'none'}`.",
            f"- Missing IDs B/C: `{report.get('binance_missing_ids')}` / `{report.get('coinbase_missing_ids')}`.",
            f"- Failed checks: `{', '.join(report.get('failed_checks') or []) or 'none'}`.",
            f"- Next action: `{report.get('next_action')}`.",
            "- SLA guard only; no signals and no orders.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Early SLA/degradation guard for cross-venue microstructure collector cycles")
    parser.add_argument("--data-quality", default="docs/CROSS_VENUE_MICROSTRUCTURE_DATA_QUALITY_2026-06-24.json")
    parser.add_argument(
        "--book-coverage-diagnostic",
        default="docs/CROSS_VENUE_MICROSTRUCTURE_BOOK_COVERAGE_DIAGNOSTIC_2026-07-03.json",
    )
    parser.add_argument("--state", default="logs/cross_venue_microstructure/collector_sla_guard_state.json")
    parser.add_argument("--history", default="logs/cross_venue_microstructure/collector_sla_guard_history.jsonl")
    parser.add_argument("--out-prefix", default="docs/CROSS_VENUE_MICROSTRUCTURE_COLLECTOR_SLA_GUARD_2026-06-25")
    parser.add_argument("--max-report-age-minutes", type=float, default=5.0)
    parser.add_argument("--min-inserted-trades", type=int, default=1)
    parser.add_argument("--min-inserted-books", type=int, default=2)
    args = parser.parse_args()

    state_path = resolve_path(args.state)
    report = build_sla_report(
        read_json(resolve_path(args.data_quality)),
        read_json(state_path),
        read_json(resolve_path(args.book_coverage_diagnostic)),
        max_report_age_minutes=max(0.0, args.max_report_age_minutes),
        min_inserted_trades=max(0, args.min_inserted_trades),
        min_inserted_books=max(0, args.min_inserted_books),
    )
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    write_json(
        state_path,
        {
            "updated_at": report["generated_at"],
            "decision": report["decision"],
            "data_generated_at": report.get("data_generated_at"),
            "archive_trades": report.get("archive_trades"),
            "archive_books": report.get("archive_books"),
            "archive_features": report.get("archive_features"),
            "trade_coverage_pct": report.get("trade_coverage_pct"),
            "book_coverage_pct": report.get("book_coverage_pct"),
            "recent_6h_book_coverage_pct": report.get("recent_6h_book_coverage_pct"),
            "recent_24h_book_coverage_pct": report.get("recent_24h_book_coverage_pct"),
            "retention_hours": report.get("retention_hours"),
            "can_trade": False,
        },
    )
    append_jsonl(resolve_path(args.history), report)
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "inserted_trades": report.get("inserted_trades"),
                "inserted_books": report.get("inserted_books"),
                "failed_checks": report.get("failed_checks"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"] in {
        "collector_sla_baseline_recorded",
        "collector_sla_healthy",
        "collector_sla_healthy_legacy_gap_rolling_out",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
