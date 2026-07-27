#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hyperliquid_cross_venue_funding_collector import (
    append_snapshot,
    canonical_sha256,
    finite,
    iso_from_ms,
    journal_metrics,
    now_ms,
    portable_path,
    read_json,
    resolve_path,
    validate_contract,
    write_json,
)

DEFAULT_CONTRACT = ROOT / "configs" / "CEX_FUNDING_DIRECT_REPLICATION_PREREG_2026-07-13.json"
DEFAULT_JOURNAL = ROOT / "data" / "forward" / "cex_dex_funding_lead_lag" / "direct_cex_funding_snapshots.jsonl"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CEX_FUNDING_DIRECT_REPLICATION_DATA_QUALITY_2026-07-13"


def fetch_json(url: str, timeout_seconds: int) -> Any:
    request = Request(url, headers={"User-Agent": "TradingOS-DirectFunding-Replication/1.0"})
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - URLs are fixed by the locked contract.
        return json.loads(response.read().decode("utf-8"))


def source_time_valid(source_time_ms: int, observed_at_ms: int, gate: dict[str, Any]) -> bool:
    minimum = observed_at_ms - int(float(gate["maximum_source_age_seconds"]) * 1000)
    maximum = observed_at_ms + int(float(gate["maximum_source_clock_lead_seconds"]) * 1000)
    return minimum <= source_time_ms <= maximum


def funding_time_valid(next_funding_ms: int, observed_at_ms: int, gate: dict[str, Any]) -> bool:
    minimum = observed_at_ms - int(float(gate["maximum_next_funding_past_seconds"]) * 1000)
    maximum = observed_at_ms + int(float(gate["maximum_next_funding_future_hours"]) * 3_600_000)
    return minimum <= next_funding_ms <= maximum


def derive_snapshot(payloads: dict[str, Any], observed_at_ms: int, contract: dict[str, Any]) -> dict[str, Any]:
    collection = contract["collection"]
    gate = contract["quality_gate"]
    symbols = [str(item).upper() for item in collection["symbols"]]
    quote_asset = str(collection.get("quote_asset") or "USDT").upper()
    default_binance_interval = float(collection["default_binance_funding_interval_hours"])

    premium_rows = payloads.get("binance_premium_index") if isinstance(payloads.get("binance_premium_index"), list) else []
    funding_info_rows = payloads.get("binance_funding_info") if isinstance(payloads.get("binance_funding_info"), list) else []
    bybit_payload = payloads.get("bybit_tickers") if isinstance(payloads.get("bybit_tickers"), dict) else {}
    bybit_result = bybit_payload.get("result") if isinstance(bybit_payload.get("result"), dict) else {}
    bybit_rows = bybit_result.get("list") if isinstance(bybit_result.get("list"), list) else []

    binance_by_symbol = {str(row.get("symbol") or "").upper(): row for row in premium_rows if isinstance(row, dict)}
    interval_overrides = {
        str(row.get("symbol") or "").upper(): finite(row.get("fundingIntervalHours"))
        for row in funding_info_rows
        if isinstance(row, dict)
    }
    bybit_by_symbol = {str(row.get("symbol") or "").upper(): row for row in bybit_rows if isinstance(row, dict)}
    try:
        bybit_source_time = int(bybit_payload.get("time"))
    except (TypeError, ValueError):
        bybit_source_time = 0

    points: dict[str, dict[str, Any]] = {}
    missing_points: list[str] = []
    invalid_points: list[str] = []
    minimum_interval = float(gate["minimum_interval_hours_exclusive"])
    maximum_interval = float(gate["maximum_interval_hours"])

    for symbol in symbols:
        market = f"{symbol}{quote_asset}"
        symbol_points: dict[str, Any] = {}
        binance = binance_by_symbol.get(market)
        if not isinstance(binance, dict):
            missing_points.append(f"{symbol}:BinanceDirect")
        else:
            rate = finite(binance.get("lastFundingRate"))
            interval = interval_overrides.get(market) or default_binance_interval
            try:
                next_funding_ms = int(binance.get("nextFundingTime"))
                source_time_ms = int(binance.get("time"))
            except (TypeError, ValueError):
                next_funding_ms = 0
                source_time_ms = 0
            valid = (
                rate is not None
                and minimum_interval < interval <= maximum_interval
                and source_time_valid(source_time_ms, observed_at_ms, gate)
                and funding_time_valid(next_funding_ms, observed_at_ms, gate)
            )
            if not valid:
                invalid_points.append(f"{symbol}:BinanceDirect")
            else:
                hourly = float(rate) / float(interval)
                symbol_points["BinanceDirect"] = {
                    "rate_semantics": "latest_funding_rate",
                    "funding_rate_raw": round(float(rate), 12),
                    "funding_interval_hours": round(float(interval), 8),
                    "funding_interval_source": "fundingInfo_override" if market in interval_overrides else "locked_default",
                    "funding_rate_per_hour": round(hourly, 12),
                    "funding_rate_annualized_simple_pct": round(hourly * 24.0 * 365.0 * 100.0, 8),
                    "next_funding_time_ms": next_funding_ms,
                    "next_funding_time": iso_from_ms(next_funding_ms),
                    "source_time_ms": source_time_ms,
                    "source_time": iso_from_ms(source_time_ms),
                }

        bybit = bybit_by_symbol.get(market)
        if not isinstance(bybit, dict):
            missing_points.append(f"{symbol}:BybitDirect")
        else:
            rate = finite(bybit.get("fundingRate"))
            interval = finite(bybit.get("fundingIntervalHour"))
            try:
                next_funding_ms = int(bybit.get("nextFundingTime"))
            except (TypeError, ValueError):
                next_funding_ms = 0
            valid = (
                rate is not None
                and interval is not None
                and minimum_interval < interval <= maximum_interval
                and source_time_valid(bybit_source_time, observed_at_ms, gate)
                and funding_time_valid(next_funding_ms, observed_at_ms, gate)
            )
            if not valid:
                invalid_points.append(f"{symbol}:BybitDirect")
            else:
                hourly = float(rate) / float(interval)
                symbol_points["BybitDirect"] = {
                    "rate_semantics": "ticker_funding_rate",
                    "funding_rate_raw": round(float(rate), 12),
                    "funding_interval_hours": round(float(interval), 8),
                    "funding_interval_source": "ticker",
                    "funding_rate_per_hour": round(hourly, 12),
                    "funding_rate_annualized_simple_pct": round(hourly * 24.0 * 365.0 * 100.0, 8),
                    "next_funding_time_ms": next_funding_ms,
                    "next_funding_time": iso_from_ms(next_funding_ms),
                    "source_time_ms": bybit_source_time,
                    "source_time": iso_from_ms(bybit_source_time),
                }
        points[symbol] = symbol_points

    required_points = len(symbols) * 2
    valid_points = sum(len(item) for item in points.values())
    checks = {
        "binance_payload_present": bool(premium_rows),
        "bybit_response_ok": bybit_payload.get("retCode") == 0,
        "all_required_points": valid_points == required_points,
        "no_missing_points": not missing_points,
        "no_invalid_points": not invalid_points,
    }
    bucket_ms = observed_at_ms - observed_at_ms % (int(collection["bucket_seconds"]) * 1000)
    return {
        "schema_version": 1,
        "lock_id": contract.get("lock_id"),
        "observed_at": iso_from_ms(observed_at_ms),
        "observed_at_ms": observed_at_ms,
        "minute_bucket": iso_from_ms(bucket_ms),
        "minute_bucket_ms": bucket_ms,
        "source": "binance_and_bybit_direct_public_rest",
        "source_payload_sha256": canonical_sha256(payloads),
        "symbols": points,
        "quality": {
            "required_points": required_points,
            "valid_points": valid_points,
            "missing_points": missing_points,
            "invalid_points": invalid_points,
            "checks": checks,
            "quality_pass": all(checks.values()),
        },
        "metric_semantics": "direct_venue_raw_funding_fields_mechanically_normalized_not_assumed_equal_to_predictedFundings",
        "directional_signal": None,
        "paper_entry": None,
        "orders_allowed": False,
        "can_trade": False,
    }


def write_report(report: dict[str, Any], out_prefix: Path) -> None:
    write_json(out_prefix.with_suffix(".json"), report)
    quality = report.get("snapshot_quality") or {}
    sample = report.get("sample") or {}
    lines = [
        "# Direct CEX Funding Replication Data Quality",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Snapshot appended: `{report.get('snapshot_appended')}`",
        f"- Valid points: `{quality.get('valid_points')}` / `{quality.get('required_points')}`",
        f"- Unique minute snapshots: `{sample.get('unique_minute_buckets')}`",
        f"- Forward span: `{sample.get('span_minutes')}` minutes",
        f"- Coverage: `{sample.get('required_point_coverage')}`",
        "",
        "## Boundary",
        "",
        "- Direct public Binance and Bybit endpoints; no credentials.",
        "- Raw rate semantics are preserved and are not assumed equal to Hyperliquid predicted funding estimates.",
        "- No signal, paper entry or order. `can_trade=false`.",
        "",
    ]
    out_prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def run_once(
    contract_path: Path,
    journal_path: Path,
    out_prefix: Path,
    *,
    payloads: dict[str, Any] | None = None,
    observed_at_ms: int | None = None,
) -> tuple[int, dict[str, Any]]:
    contract = read_json(contract_path)
    failures = validate_contract(contract)
    if failures:
        report = {
            "generated_at": iso_from_ms(now_ms()),
            "decision": "direct_cex_funding_replication_blocked_contract",
            "contract_failures": failures,
            "can_trade": False,
        }
        write_report(report, out_prefix)
        return 2, report
    if payloads is None:
        try:
            timeout = int(contract["collection"]["request_timeout_seconds"])
            sources = contract["sources"]
            urls = {
                "binance_premium_index": str(sources["binance"]["premium_index_url"]),
                "binance_funding_info": str(sources["binance"]["funding_info_url"]),
                "bybit_tickers": str(sources["bybit"]["tickers_url"]),
            }
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=len(urls), thread_name_prefix="direct-funding") as executor:
                futures = {name: executor.submit(fetch_json, url, timeout) for name, url in urls.items()}
                payloads = {name: future.result() for name, future in futures.items()}
            fetch_latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        except Exception as exc:  # network boundary
            report = {
                "generated_at": iso_from_ms(now_ms()),
                "decision": "direct_cex_funding_replication_fetch_failed",
                "error": f"{type(exc).__name__}:{exc}",
                "can_trade": False,
            }
            write_report(report, out_prefix)
            return 1, report
    else:
        fetch_latency_ms = 0.0
    observed = int(observed_at_ms if observed_at_ms is not None else now_ms())
    snapshot = derive_snapshot(payloads, observed, contract)
    appended, append_reason = append_snapshot(journal_path, snapshot)
    quality = snapshot["quality"]
    if not appended:
        decision = "direct_cex_funding_replication_duplicate_minute_skipped"
    elif quality["quality_pass"]:
        decision = "direct_cex_funding_replication_snapshot_healthy_appended"
    else:
        decision = "direct_cex_funding_replication_snapshot_degraded_appended"
    sample = journal_metrics(journal_path, int(quality["required_points"]))
    report = {
        "schema_version": 1,
        "generated_at": iso_from_ms(observed),
        "tool": "tools/direct_cex_funding_replication_collector.py",
        "decision": decision,
        "lock_id": contract.get("lock_id"),
        "contract": portable_path(contract_path),
        "journal": portable_path(journal_path),
        "snapshot_appended": appended,
        "append_reason": append_reason,
        "snapshot_quality": quality,
        "fetch_latency_ms": fetch_latency_ms,
        "sample": sample,
        "replication_gate": contract.get("replication_gate"),
        "runtime_boundary": contract.get("runtime_boundary"),
        "can_trade": False,
    }
    write_report(report, out_prefix)
    return 0, report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Collect direct public Binance and Bybit funding snapshots for replication")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()
    code, report = run_once(resolve_path(args.contract), resolve_path(args.journal), resolve_path(args.out_prefix))
    print(json.dumps({"decision": report.get("decision"), "sample": report.get("sample"), "can_trade": False}, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
