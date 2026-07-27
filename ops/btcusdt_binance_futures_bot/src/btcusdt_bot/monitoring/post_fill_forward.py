from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from btcusdt_bot.authoritative.archive import AuthoritativeArchive, USER_TRADES_DATASET, iter_buckets
from btcusdt_bot.monitoring.post_fill_markout import (
    BOOK_MID,
    PostFillMarkoutConfig,
    PostFillMarkoutReport,
    analyze_post_fill_markout,
)


_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class PostFillForwardLock:
    lock_id: str
    forward_start_ms: int
    symbol: str
    archive_root: Path
    market_root: Path
    primary_horizon_seconds: int
    secondary_horizons_seconds: tuple[int, ...]
    max_pre_fill_age_ms: int
    max_post_horizon_delay_ms: int
    max_capture_event_age_ms: int
    minimum_evaluated_fills_per_horizon: int
    minimum_distinct_utc_days: int
    minimum_evaluation_coverage_ratio: Decimal

    @property
    def horizons_seconds(self) -> tuple[int, ...]:
        return (self.primary_horizon_seconds, *self.secondary_horizons_seconds)


def _resolve_path(project_root: Path, value: object, *, field_name: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"missing_{field_name}")
    path = Path(raw)
    return path if path.is_absolute() else project_root / path


def _positive_int(value: object, *, field_name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{field_name}") from exc
    if result <= 0:
        raise ValueError(f"invalid_{field_name}")
    return result


def load_post_fill_forward_lock(path: Path, *, project_root: Path) -> PostFillForwardLock:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_post_fill_forward_lock")
    if payload.get("status") != "forward_execution_quality_preregistered":
        raise ValueError("invalid_post_fill_forward_lock_status")
    if payload.get("can_trade") is not False:
        raise ValueError("post_fill_forward_lock_must_disable_trading")

    runtime_boundary = payload.get("runtime_boundary")
    if not isinstance(runtime_boundary, dict):
        raise ValueError("missing_runtime_boundary")
    forbidden_permissions = (
        "signals_allowed",
        "paper_entries_allowed",
        "orders_allowed",
        "automatic_promotion_allowed",
        "can_trade",
    )
    if any(runtime_boundary.get(key) is not False for key in forbidden_permissions):
        raise ValueError("post_fill_forward_runtime_boundary_not_fail_closed")

    data_contract = payload.get("data_contract")
    timing = payload.get("timing")
    evidence_floor = payload.get("evidence_floor")
    if not isinstance(data_contract, dict) or not isinstance(timing, dict) or not isinstance(evidence_floor, dict):
        raise ValueError("post_fill_forward_lock_sections_missing")
    if data_contract.get("reference_source") != BOOK_MID:
        raise ValueError("post_fill_forward_requires_book_mid")

    primary = _positive_int(timing.get("primary_horizon_seconds"), field_name="primary_horizon_seconds")
    secondary_raw = timing.get("secondary_horizons_seconds")
    if not isinstance(secondary_raw, list):
        raise ValueError("invalid_secondary_horizons_seconds")
    secondary = tuple(
        _positive_int(value, field_name="secondary_horizon_seconds") for value in secondary_raw
    )
    if primary in secondary or len(set(secondary)) != len(secondary):
        raise ValueError("duplicate_post_fill_horizon")

    coverage_ratio = Decimal(str(evidence_floor.get("minimum_evaluation_coverage_ratio", "")))
    if coverage_ratio <= 0 or coverage_ratio > 1:
        raise ValueError("invalid_minimum_evaluation_coverage_ratio")

    return PostFillForwardLock(
        lock_id=str(payload.get("lock_id", "")).strip(),
        forward_start_ms=_positive_int(payload.get("forward_start_ms"), field_name="forward_start_ms"),
        symbol=str(data_contract.get("symbol", "")).upper(),
        archive_root=_resolve_path(project_root, data_contract.get("archive_root"), field_name="archive_root"),
        market_root=_resolve_path(project_root, data_contract.get("market_root"), field_name="market_root"),
        primary_horizon_seconds=primary,
        secondary_horizons_seconds=secondary,
        max_pre_fill_age_ms=_positive_int(
            timing.get("max_pre_fill_age_ms"),
            field_name="max_pre_fill_age_ms",
        ),
        max_post_horizon_delay_ms=_positive_int(
            timing.get("max_post_horizon_delay_ms"),
            field_name="max_post_horizon_delay_ms",
        ),
        max_capture_event_age_ms=_positive_int(
            timing.get("max_capture_event_age_ms"),
            field_name="max_capture_event_age_ms",
        ),
        minimum_evaluated_fills_per_horizon=_positive_int(
            evidence_floor.get("minimum_evaluated_fills_per_horizon"),
            field_name="minimum_evaluated_fills_per_horizon",
        ),
        minimum_distinct_utc_days=_positive_int(
            evidence_floor.get("minimum_distinct_utc_days"),
            field_name="minimum_distinct_utc_days",
        ),
        minimum_evaluation_coverage_ratio=coverage_ratio,
    )


def _read_first_line(path: Path) -> str | None:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                return line
    return None


def _read_last_line(path: Path) -> str | None:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        pending = b""
        while position > 0:
            chunk_size = min(8192, position)
            position -= chunk_size
            handle.seek(position)
            pending = handle.read(chunk_size) + pending
            lines = pending.splitlines()
            if position == 0:
                candidates = lines
            elif len(lines) > 1:
                candidates = lines[1:]
            else:
                continue
            for raw in reversed(candidates):
                if raw.strip():
                    return raw.decode("utf-8")
        return None


def _book_event_time_ms(line: str | None, *, symbol: str) -> int | None:
    if line is None:
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict):
        return None
    payload = row.get("payload")
    if not isinstance(payload, dict):
        payload = row
    if str(payload.get("s", payload.get("symbol", symbol))).upper() != symbol.upper():
        return None
    try:
        event_time_ms = int(payload.get("E", payload.get("T", row.get("event_time_ms", 0))) or 0)
    except (TypeError, ValueError):
        return None
    return event_time_ms if event_time_ms > 0 else None


def inspect_book_capture(lock: PostFillForwardLock, *, generated_at_ms: int) -> dict[str, object]:
    files: list[str] = []
    edge_parse_failures = 0
    first_event_ms: int | None = None
    latest_event_ms: int | None = None
    for bucket in iter_buckets(lock.forward_start_ms, generated_at_ms):
        path = lock.market_root / "public" / bucket / f"{lock.symbol.lower()}_bookTicker.jsonl"
        if not path.exists() or path.stat().st_size <= 0:
            continue
        files.append(str(path))
        first = _book_event_time_ms(_read_first_line(path), symbol=lock.symbol)
        last = _book_event_time_ms(_read_last_line(path), symbol=lock.symbol)
        edge_parse_failures += int(first is None) + int(last is None)
        if first is not None and (first_event_ms is None or first < first_event_ms):
            first_event_ms = first
        if last is not None and (latest_event_ms is None or last > latest_event_ms):
            latest_event_ms = last

    raw_age_ms = generated_at_ms - latest_event_ms if latest_event_ms is not None else None
    return {
        "files": files,
        "file_count": len(files),
        "edge_parse_failures": edge_parse_failures,
        "first_event_time_ms": first_event_ms,
        "latest_event_time_ms": latest_event_ms,
        "latest_event_age_ms": max(0, raw_age_ms) if raw_age_ms is not None else None,
        "latest_event_clock_lead_ms": max(0, -raw_age_ms) if raw_age_ms is not None else None,
        "capture_started_by_forward_floor": bool(
            first_event_ms is not None and first_event_ms <= lock.forward_start_ms
        ),
        "capture_fresh": bool(
            raw_age_ms is not None and raw_age_ms <= lock.max_capture_event_age_ms
        ),
    }


def _horizon_summary(report: PostFillMarkoutReport) -> dict[str, object]:
    observed_days = sorted(
        {
            datetime.fromtimestamp(item.trade_time_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
            for item in report.observations
        }
    )
    return {
        "horizon_seconds": report.horizon_ms // 1000,
        "decision": report.decision,
        "archive_source_mode": report.archive_source_mode,
        "archive_coverage_ratio": report.archive_coverage_ratio,
        "archive_gaps": report.archive_gaps,
        "raw_fill_count": report.raw_fill_count,
        "valid_fill_count": report.valid_fill_count,
        "evaluated_fill_count": report.evaluated_fill_count,
        "evaluation_coverage_ratio": report.evaluation_coverage_ratio,
        "distinct_utc_days": observed_days,
        "distinct_utc_day_count": len(observed_days),
        "missing_pre_reference_count": report.missing_pre_reference_count,
        "stale_pre_reference_count": report.stale_pre_reference_count,
        "missing_post_reference_count": report.missing_post_reference_count,
        "late_post_reference_count": report.late_post_reference_count,
        "favorable_markout_count": report.favorable_markout_count,
        "adverse_markout_count": report.adverse_markout_count,
        "flat_markout_count": report.flat_markout_count,
        "evaluated_quote_qty": report.evaluated_quote_qty,
        "quote_weighted_signed_markout_bps": report.quote_weighted_signed_markout_bps,
        "quote_weighted_effective_spread_bps": report.quote_weighted_effective_spread_bps,
        "quote_weighted_realized_spread_bps": report.quote_weighted_realized_spread_bps,
        "quote_weighted_price_impact_bps": report.quote_weighted_price_impact_bps,
        "maker_quote_weighted_signed_markout_bps": report.maker_quote_weighted_signed_markout_bps,
        "taker_quote_weighted_signed_markout_bps": report.taker_quote_weighted_signed_markout_bps,
        "orders_allowed": False,
        "can_trade": False,
    }


def build_post_fill_forward_report(
    prereg_path: Path,
    *,
    project_root: Path,
    generated_at_ms: int,
    credentials_present: bool,
) -> dict[str, Any]:
    lock = load_post_fill_forward_lock(prereg_path, project_root=project_root)
    if not lock.lock_id or not lock.symbol:
        raise ValueError("post_fill_forward_lock_identity_missing")

    maximum_horizon_ms = max(lock.horizons_seconds) * 1000
    common_window_end_ms = generated_at_ms - maximum_horizon_ms - lock.max_post_horizon_delay_ms
    capture = inspect_book_capture(lock, generated_at_ms=generated_at_ms)
    archive = AuthoritativeArchive(lock.archive_root, symbol=lock.symbol)
    manifest_present = archive.manifest_path().exists()

    horizon_summaries: list[dict[str, object]] = []
    if common_window_end_ms >= lock.forward_start_ms:
        for horizon_seconds in lock.horizons_seconds:
            markout = analyze_post_fill_markout(
                PostFillMarkoutConfig(
                    archive_root=lock.archive_root,
                    market_root=lock.market_root,
                    symbol=lock.symbol,
                    start_ms=lock.forward_start_ms,
                    end_ms=common_window_end_ms,
                    horizon_ms=horizon_seconds * 1000,
                    max_pre_fill_age_ms=lock.max_pre_fill_age_ms,
                    max_post_horizon_delay_ms=lock.max_post_horizon_delay_ms,
                    reference_source=BOOK_MID,
                ),
                generated_at_ms=generated_at_ms,
            )
            horizon_summaries.append(_horizon_summary(markout))

    checks = {
        "forward_floor_reached": generated_at_ms >= lock.forward_start_ms,
        "common_outcome_window_closed": common_window_end_ms >= lock.forward_start_ms,
        "book_capture_present": capture["file_count"] > 0,
        "book_capture_started_by_forward_floor": capture["capture_started_by_forward_floor"],
        "book_capture_fresh": capture["capture_fresh"],
        "authoritative_manifest_present": manifest_present,
        "authoritative_coverage_complete": bool(
            horizon_summaries
            and all(item["archive_coverage_ratio"] == _ONE for item in horizon_summaries)
        ),
        "minimum_evaluated_fills_reached": bool(
            horizon_summaries
            and all(
                int(item["evaluated_fill_count"]) >= lock.minimum_evaluated_fills_per_horizon
                for item in horizon_summaries
            )
        ),
        "minimum_distinct_utc_days_reached": bool(
            horizon_summaries
            and all(
                int(item["distinct_utc_day_count"]) >= lock.minimum_distinct_utc_days
                for item in horizon_summaries
            )
        ),
        "evaluation_coverage_reached": bool(
            horizon_summaries
            and all(
                item["evaluation_coverage_ratio"] >= lock.minimum_evaluation_coverage_ratio
                for item in horizon_summaries
            )
        ),
    }

    blockers: list[str] = []
    if not checks["forward_floor_reached"]:
        blockers.append("waiting_forward_floor")
    elif not checks["common_outcome_window_closed"]:
        blockers.append("waiting_longest_horizon_closure")
    if not checks["book_capture_present"]:
        blockers.append("book_ticker_capture_missing")
    elif not checks["book_capture_started_by_forward_floor"]:
        blockers.append("book_ticker_started_after_forward_floor")
    if checks["book_capture_present"] and not checks["book_capture_fresh"]:
        blockers.append("book_ticker_capture_stale")
    if not manifest_present:
        blockers.append(
            "demo_credentials_missing_for_authoritative_fills"
            if not credentials_present
            else "authoritative_backfill_not_started"
        )
    elif not checks["authoritative_coverage_complete"]:
        blockers.append("authoritative_archive_coverage_incomplete")

    raw_fill_count = max((int(item["raw_fill_count"]) for item in horizon_summaries), default=0)
    if manifest_present and checks["authoritative_coverage_complete"] and raw_fill_count == 0:
        blockers.append("no_authoritative_fills_observed")
    if raw_fill_count > 0 and not checks["evaluation_coverage_reached"]:
        blockers.append("markout_reference_coverage_incomplete")
    if raw_fill_count > 0 and not checks["minimum_evaluated_fills_reached"]:
        blockers.append("minimum_fill_evidence_not_reached")
    if raw_fill_count > 0 and not checks["minimum_distinct_utc_days_reached"]:
        blockers.append("minimum_day_diversity_not_reached")

    review_ready = bool(checks and all(checks.values()))
    if review_ready:
        decision = "markout_distribution_ready_for_manual_review"
    elif "waiting_forward_floor" in blockers:
        decision = "waiting_forward_floor"
    elif "waiting_longest_horizon_closure" in blockers:
        decision = "waiting_longest_horizon_closure"
    elif "book_ticker_capture_missing" in blockers:
        decision = "waiting_book_ticker_capture"
    elif "book_ticker_capture_stale" in blockers:
        decision = "book_ticker_capture_stale"
    elif "demo_credentials_missing_for_authoritative_fills" in blockers:
        decision = "waiting_demo_credentials_for_authoritative_fills"
    elif "authoritative_backfill_not_started" in blockers:
        decision = "waiting_authoritative_backfill"
    elif "authoritative_archive_coverage_incomplete" in blockers:
        decision = "waiting_authoritative_archive_coverage"
    elif "no_authoritative_fills_observed" in blockers:
        decision = "no_authoritative_fills_observed"
    elif "markout_reference_coverage_incomplete" in blockers:
        decision = "markout_reference_coverage_incomplete"
    else:
        decision = "collecting_markout_distribution"

    return {
        "schema_version": 1,
        "generated_at_ms": generated_at_ms,
        "decision": decision,
        "lock_id": lock.lock_id,
        "prereg_path": str(prereg_path),
        "forward_start_ms": lock.forward_start_ms,
        "common_window_end_ms": common_window_end_ms,
        "symbol": lock.symbol,
        "credentials_present": bool(credentials_present),
        "data_contract": {
            "archive_root": str(lock.archive_root),
            "market_root": str(lock.market_root),
            "reference_source": BOOK_MID,
            "causality_mode": "exchange_event_time",
        },
        "locked_horizons_seconds": list(lock.horizons_seconds),
        "primary_horizon_seconds": lock.primary_horizon_seconds,
        "freshness_budgets": {
            "max_pre_fill_age_ms": lock.max_pre_fill_age_ms,
            "max_post_horizon_delay_ms": lock.max_post_horizon_delay_ms,
            "max_capture_event_age_ms": lock.max_capture_event_age_ms,
        },
        "evidence_floor": {
            "minimum_evaluated_fills_per_horizon": lock.minimum_evaluated_fills_per_horizon,
            "minimum_distinct_utc_days": lock.minimum_distinct_utc_days,
            "minimum_evaluation_coverage_ratio": lock.minimum_evaluation_coverage_ratio,
            "meaning": "manual_distribution_review_only",
        },
        "book_capture": capture,
        "authoritative_manifest_path": str(archive.manifest_path()),
        "authoritative_manifest_present": manifest_present,
        "horizons": horizon_summaries,
        "checks": checks,
        "blockers": blockers,
        "next_action": (
            "Provide Binance Futures demo read-only credentials, backfill userTrades into the locked archive root, and keep the aligned public bookTicker collector running."
            if "demo_credentials_missing_for_authoritative_fills" in blockers
            else "Keep collecting the locked forward cohort; do not set economic drift thresholds before manual distribution review."
        ),
        "runtime_boundary": {
            "forward_execution_quality_observer_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "automatic_promotion_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
