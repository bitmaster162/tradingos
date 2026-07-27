#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bybit_all_liquidation_context_intake as base
from tools.liquidity_sweep_detector import OhlcvBar, load_ohlcv


TOOL_PATH = "tools/bybit_liquidation_canonical_input_quality.py"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def last_fully_closed_bar_open(now: datetime, interval: str) -> datetime:
    current_open = base.floor_time(now.astimezone(timezone.utc), interval)
    return current_open - base.parse_interval(interval)


def filter_fully_closed_bars(
    bars: list[OhlcvBar],
    *,
    now: datetime,
    interval: str,
) -> tuple[list[OhlcvBar], int]:
    cutoff = last_fully_closed_bar_open(now, interval)
    closed: list[OhlcvBar] = []
    excluded = 0
    for bar in bars:
        parsed = base.parse_ts(bar.ts)
        if parsed is not None and parsed <= cutoff:
            closed.append(bar)
        else:
            excluded += 1
    return closed, excluded


def event_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("symbol") or "").upper(),
        int(row.get("liquidation_time_ms") or 0),
        str(row.get("side") or "").upper(),
        str(row.get("price")),
        str(row.get("quantity")),
    )


def scan_events(data_dir: Path, symbols: list[str], forward_floor_at: str) -> dict[str, Any]:
    symbol_set = {item.upper() for item in symbols}
    floor = base.parse_ts(forward_floor_at)
    if floor is None:
        raise ValueError("invalid forward floor")
    files = sorted(data_dir.rglob("*.jsonl")) if data_dir.exists() else []
    seen: set[tuple[Any, ...]] = set()
    duplicate_rows = 0
    parse_errors = 0
    schema_errors = 0
    partition_mismatches = 0
    negative_receipt_lags = 0
    nonmonotonic_files = 0
    post_floor_duplicate_rows = 0
    post_floor_parse_errors = 0
    post_floor_schema_errors = 0
    post_floor_partition_mismatches = 0
    post_floor_negative_receipt_lags = 0
    post_floor_nonmonotonic_files = 0
    rows = 0
    schema_valid_rows = 0
    post_floor_rows = 0
    by_symbol: Counter[str] = Counter()
    by_side: Counter[str] = Counter()
    event_lags_ms: list[float] = []
    receipt_lags_ms: list[float] = []
    error_sample: list[dict[str, Any]] = []
    first_liquidation_ms: int | None = None
    last_liquidation_ms: int | None = None

    for path in files:
        previous_ms: int | None = None
        file_nonmonotonic = False
        file_post_floor_nonmonotonic = False
        try:
            partition_date = datetime.strptime(path.stem, "%Y%m%d").date()
        except ValueError:
            partition_date = None
        partition_may_contain_post_floor = partition_date is None or partition_date >= floor.date()
        try:
            handle = path.open("r", encoding="utf-8-sig")
        except OSError as exc:
            parse_errors += 1
            post_floor_parse_errors += int(partition_may_contain_post_floor)
            if len(error_sample) < 25:
                error_sample.append({"path": base.portable(path), "line": None, "error": repr(exc)})
            continue
        with handle:
            for line_no, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    parse_errors += 1
                    post_floor_parse_errors += int(partition_may_contain_post_floor)
                    if len(error_sample) < 25:
                        error_sample.append({"path": base.portable(path), "line": line_no, "error": f"json:{exc}"})
                    continue
                if not isinstance(payload, dict):
                    parse_errors += 1
                    post_floor_parse_errors += int(partition_may_contain_post_floor)
                    continue
                symbol = str(payload.get("symbol") or "").upper()
                if symbol not in symbol_set:
                    continue
                rows += 1
                liquidation_dt = base.ms_to_dt(payload.get("liquidation_time_ms"))
                is_post_floor = liquidation_dt is not None and liquidation_dt >= floor
                post_floor_rows += int(is_post_floor)
                errors = base.validate_event_payload(payload)
                if payload.get("venue") != "bybit":
                    errors.append("venue")
                if int(payload.get("ingest_schema_version") or 0) != 2:
                    errors.append("ingest_schema_version")
                if not str(payload.get("collector_host") or ""):
                    errors.append("collector_host")
                try:
                    received_at_ns = int(payload.get("received_at_ns") or 0)
                except (TypeError, ValueError):
                    received_at_ns = 0
                if received_at_ns <= 0:
                    errors.append("received_at_ns")
                if errors:
                    schema_errors += 1
                    post_floor_schema_errors += int(is_post_floor)
                    if len(error_sample) < 25:
                        error_sample.append({"path": base.portable(path), "line": line_no, "error": ";".join(sorted(set(errors)))})
                else:
                    schema_valid_rows += 1
                if liquidation_dt is None:
                    continue

                liquidation_ms = int(payload["liquidation_time_ms"])
                expected_date = liquidation_dt.strftime("%Y%m%d")
                if path.parent.name.upper() != symbol or path.stem != expected_date:
                    partition_mismatches += 1
                    post_floor_partition_mismatches += int(is_post_floor)
                if previous_ms is not None and liquidation_ms < previous_ms:
                    file_nonmonotonic = True
                    file_post_floor_nonmonotonic = file_post_floor_nonmonotonic or is_post_floor
                previous_ms = liquidation_ms

                identity = event_identity(payload)
                if identity in seen:
                    duplicate_rows += 1
                    post_floor_duplicate_rows += int(is_post_floor)
                else:
                    seen.add(identity)
                if received_at_ns > 0:
                    receipt_lag = (received_at_ns / 1_000_000.0) - liquidation_ms
                    if receipt_lag < 0:
                        negative_receipt_lags += 1
                        post_floor_negative_receipt_lags += int(is_post_floor)
                    receipt_lags_ms.append(receipt_lag)
                try:
                    event_ms = int(payload.get("event_time_ms"))
                except (TypeError, ValueError):
                    event_ms = None
                if event_ms is not None:
                    event_lags_ms.append(float(event_ms - liquidation_ms))
                by_symbol[symbol] += 1
                by_side[str(payload.get("side") or "").upper()] += 1
                first_liquidation_ms = liquidation_ms if first_liquidation_ms is None else min(first_liquidation_ms, liquidation_ms)
                last_liquidation_ms = liquidation_ms if last_liquidation_ms is None else max(last_liquidation_ms, liquidation_ms)
        nonmonotonic_files += int(file_nonmonotonic)
        post_floor_nonmonotonic_files += int(file_post_floor_nonmonotonic)

    def iso(value: int | None) -> str | None:
        return (
            datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if value is not None
            else None
        )

    return {
        "files": len(files),
        "events": rows,
        "schema_valid_events": schema_valid_rows,
        "post_floor_events": post_floor_rows,
        "unique_event_identities": len(seen),
        "duplicate_event_identities": duplicate_rows,
        "json_parse_errors": parse_errors,
        "schema_errors": schema_errors,
        "partition_mismatches": partition_mismatches,
        "nonmonotonic_files": nonmonotonic_files,
        "negative_receipt_lags": negative_receipt_lags,
        "post_floor_json_parse_errors": post_floor_parse_errors,
        "post_floor_schema_errors": post_floor_schema_errors,
        "post_floor_duplicate_event_identities": post_floor_duplicate_rows,
        "post_floor_partition_mismatches": post_floor_partition_mismatches,
        "post_floor_nonmonotonic_files": post_floor_nonmonotonic_files,
        "post_floor_negative_receipt_lags": post_floor_negative_receipt_lags,
        "by_symbol": dict(sorted(by_symbol.items())),
        "by_side": dict(sorted(by_side.items())),
        "first_liquidation_time": iso(first_liquidation_ms),
        "last_liquidation_time": iso(last_liquidation_ms),
        "event_lag_ms": {
            "min": round(min(event_lags_ms), 3) if event_lags_ms else None,
            "p50": round(statistics.median(event_lags_ms), 3) if event_lags_ms else None,
            "p95": round(percentile(event_lags_ms, 0.95), 3) if event_lags_ms else None,
            "max": round(max(event_lags_ms), 3) if event_lags_ms else None,
        },
        "receipt_lag_ms": {
            "min": round(min(receipt_lags_ms), 3) if receipt_lags_ms else None,
            "p50": round(statistics.median(receipt_lags_ms), 3) if receipt_lags_ms else None,
            "p95": round(percentile(receipt_lags_ms, 0.95), 3) if receipt_lags_ms else None,
            "max": round(max(receipt_lags_ms), 3) if receipt_lags_ms else None,
        },
        "error_sample": error_sample,
        "gate_scope": {
            "event_checks": "liquidation_time_gte_forward_floor",
            "unassignable_json_errors": "partition_date_gte_forward_floor_date",
            "historical_metrics_are_diagnostic_only": True,
        },
    }


def scan_bars(
    bars_root: Path,
    symbols: list[str],
    interval: str,
    *,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, list[OhlcvBar]]]:
    interval_delta = base.parse_interval(interval)
    cutoff = last_fully_closed_bar_open(now, interval)
    by_symbol: dict[str, Any] = {}
    closed_by_symbol: dict[str, list[OhlcvBar]] = {}
    for symbol in symbols:
        path = bars_root / symbol / f"{interval}_klines.csv"
        errors: list[str] = []
        bars: list[OhlcvBar] = []
        if path.exists():
            try:
                bars = load_ohlcv(path)
            except (OSError, ValueError) as exc:
                errors.append(repr(exc))
        else:
            errors.append("missing_bar_file")
        closed, excluded = filter_fully_closed_bars(bars, now=now, interval=interval)
        closed_by_symbol[symbol] = closed
        last_closed = base.parse_ts(closed[-1].ts) if closed else None
        lag_intervals = (
            int((cutoff - last_closed).total_seconds() // interval_delta.total_seconds())
            if last_closed is not None
            else None
        )
        by_symbol[symbol] = {
            "path": base.portable(path),
            "rows": len(bars),
            "closed_rows": len(closed),
            "open_or_invalid_rows_excluded": excluded,
            "last_closed_bar_open": (
                last_closed.isoformat(timespec="milliseconds").replace("+00:00", "Z") if last_closed else None
            ),
            "closed_bar_lag_intervals": lag_intervals,
            "errors": errors,
        }
    return {
        "closed_cutoff_bar_open": cutoff.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "symbols": by_symbol,
        "bar_symbols_present": sum(bool(item["closed_rows"]) and not item["errors"] for item in by_symbol.values()),
        "maximum_closed_bar_lag_intervals": max(
            (int(item["closed_bar_lag_intervals"]) for item in by_symbol.values() if item["closed_bar_lag_intervals"] is not None),
            default=None,
        ),
        "current_interval_rows_excluded": sum(int(item["open_or_invalid_rows_excluded"]) for item in by_symbol.values()),
    }, closed_by_symbol


def build_quality(contract: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate = contract["candidate"]
    sources = contract["sources"]
    gate = contract["input_quality_gate"]
    symbols = [str(item).upper() for item in candidate["symbols"]]
    events = scan_events(base.resolve_path(sources["liquidations"]), symbols, contract["forward_start_at"])
    bars, _ = scan_bars(
        base.resolve_path(sources["bars_root"]),
        symbols,
        str(candidate["interval"]),
        now=observed_at,
    )
    checks = {
        "post_floor_json_parse_errors": events["post_floor_json_parse_errors"] <= int(gate["maximum_json_parse_errors"]),
        "post_floor_schema_errors": events["post_floor_schema_errors"] <= int(gate["maximum_schema_errors"]),
        "post_floor_duplicate_event_identities": events["post_floor_duplicate_event_identities"] <= int(gate["maximum_duplicate_event_identities"]),
        "post_floor_partition_mismatches": events["post_floor_partition_mismatches"] <= int(gate["maximum_partition_mismatches"]),
        "post_floor_nonmonotonic_files": events["post_floor_nonmonotonic_files"] <= int(gate["maximum_nonmonotonic_files"]),
        "post_floor_negative_receipt_lags": events["post_floor_negative_receipt_lags"] <= int(gate["maximum_negative_receipt_lags"]),
        "required_bar_symbols": bars["bar_symbols_present"] >= int(gate["required_bar_symbols"]),
        "closed_bar_freshness": (
            bars["maximum_closed_bar_lag_intervals"] is not None
            and int(bars["maximum_closed_bar_lag_intervals"]) <= int(gate["maximum_closed_bar_lag_intervals"])
        ),
    }
    hard_failures = [name for name, passed in checks.items() if not passed]
    return {
        "generated_at": observed_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "tool": TOOL_PATH,
        "decision": (
            "bybit_canonical_input_quality_pass"
            if not hard_failures
            else "bybit_canonical_input_quality_blocked"
        ),
        "can_trade": False,
        "orders_allowed": False,
        "forward_floor_at": contract["forward_start_at"],
        "events": events,
        "bars": bars,
        "checks": checks,
        "hard_failures": hard_failures,
        "boundary": {
            "input_quality_only": True,
            "outcome_fields_computed": False,
            "return_metrics_visible": False,
            "uses_public_market_data_only": True,
            "uses_private_credentials": False,
            "signals_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Bybit Canonical Input Quality",
            "",
            f"- Generated: `{report['generated_at']}`",
            f"- Decision: `{report['decision']}`",
            f"- Events: `{report['events']['events']}`",
            f"- Schema-valid events: `{report['events']['schema_valid_events']}`",
            f"- Post-floor events: `{report['events']['post_floor_events']}`",
            f"- Historical schema errors (diagnostic): `{report['events']['schema_errors']}`",
            f"- Post-floor schema errors (hard gate): `{report['events']['post_floor_schema_errors']}`",
            f"- Post-floor duplicate identities (hard gate): `{report['events']['post_floor_duplicate_event_identities']}`",
            f"- Closed-bar symbols: `{report['bars']['bar_symbols_present']}`",
            f"- Current interval rows excluded: `{report['bars']['current_interval_rows_excluded']}`",
            f"- Hard failures: `{', '.join(report['hard_failures']) or 'none'}`",
            "- Outcome fields computed: `false`",
            "- Can trade: `false`",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Outcome-blind input-quality gate for canonical Bybit liquidation research")
    parser.add_argument("--prereg", default="configs/BYBIT_LIQUIDATION_CANONICAL_FORWARD_PREREG_V3_2026-07-13.json")
    parser.add_argument("--out-prefix", default="docs/BYBIT_LIQUIDATION_CANONICAL_INPUT_QUALITY_V3_2026-07-13")
    args = parser.parse_args()
    prereg_path = base.resolve_path(args.prereg)
    contract = json.loads(prereg_path.read_text(encoding="utf-8-sig"))
    contract["forward_start_at"] = contract["forward_floor_at"]
    report = build_quality(contract)
    out = base.resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "hard_failures": report["hard_failures"], "can_trade": False}, indent=2))
    return 1 if report["hard_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
