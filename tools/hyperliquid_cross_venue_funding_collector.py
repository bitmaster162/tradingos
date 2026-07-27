#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "configs" / "CEX_DEX_FUNDING_LEAD_LAG_PREREG_2026-07-13.json"
DEFAULT_JOURNAL = ROOT / "data" / "forward" / "cex_dex_funding_lead_lag" / "funding_snapshots.jsonl"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CEX_DEX_FUNDING_LEAD_LAG_DATA_QUALITY_2026-07-13"


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


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
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_contract(contract: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    boundary = contract.get("runtime_boundary") if isinstance(contract.get("runtime_boundary"), dict) else {}
    collection = contract.get("collection") if isinstance(contract.get("collection"), dict) else {}
    if contract.get("status") != "fixed_forward_data_collection_contract":
        failures.append("status")
    if contract.get("can_trade") is not False:
        failures.append("can_trade")
    if boundary.get("collector_only") is not True:
        failures.append("collector_only")
    if boundary.get("directional_signal") is not False:
        failures.append("directional_signal")
    if boundary.get("paper_entries_allowed") is not False:
        failures.append("paper_entries_allowed")
    if boundary.get("orders_allowed") is not False:
        failures.append("orders_allowed")
    if collection.get("credentials_allowed") is not False:
        failures.append("credentials_allowed")
    if not collection.get("symbols") or not collection.get("venue_ids"):
        failures.append("collection_scope")
    return failures


def fetch_info(url: str, request_type: str, timeout_seconds: int) -> Any:
    body = json.dumps({"type": request_type}, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "TradingOS-CrossVenueFunding-Collector/1.0",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed public endpoint from locked contract.
        return json.loads(response.read().decode("utf-8"))


def parse_predicted_fundings(payload: Any) -> tuple[dict[str, dict[str, dict[str, Any]]], int]:
    parsed: dict[str, dict[str, dict[str, Any]]] = {}
    rows = payload if isinstance(payload, list) else []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            continue
        coin = str(row[0] or "").upper()
        venues = row[1] if isinstance(row[1], list) else []
        venue_map: dict[str, dict[str, Any]] = {}
        for venue_row in venues:
            if not isinstance(venue_row, list) or len(venue_row) != 2 or not isinstance(venue_row[1], dict):
                continue
            venue_map[str(venue_row[0] or "")] = venue_row[1]
        if coin:
            parsed[coin] = venue_map
    return parsed, len(rows)


def parse_hyperliquid_contexts(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) != 2:
        return {}
    metadata = payload[0] if isinstance(payload[0], dict) else {}
    universe = metadata.get("universe") if isinstance(metadata.get("universe"), list) else []
    contexts = payload[1] if isinstance(payload[1], list) else []
    parsed: dict[str, dict[str, Any]] = {}
    for market, context in zip(universe, contexts):
        if not isinstance(market, dict) or not isinstance(context, dict):
            continue
        coin = str(market.get("name") or "").upper()
        if coin:
            parsed[coin] = context
    return parsed


def derive_snapshot(payload: Any, observed_at_ms: int, contract: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        predicted_payload = payload.get("predictedFundings")
        context_payload = payload.get("metaAndAssetCtxs")
    else:
        predicted_payload = payload
        context_payload = None
    parsed, payload_rows = parse_predicted_fundings(predicted_payload)
    direct_hl = parse_hyperliquid_contexts(context_payload)
    collection = contract["collection"]
    gate = contract["quality_gate"]
    symbols = [str(item).upper() for item in collection["symbols"]]
    venues = [str(item) for item in collection["venue_ids"]]
    minimum_interval = float(gate["minimum_interval_hours_exclusive"])
    maximum_interval = float(gate["maximum_interval_hours"])
    minimum_next_ms = observed_at_ms - int(float(gate["maximum_next_funding_past_seconds"]) * 1000)
    maximum_next_ms = observed_at_ms + int(float(gate["maximum_next_funding_future_hours"]) * 3_600_000)
    direct_hl_required = bool(collection.get("direct_hyperliquid_context_required"))
    hl_interval = float(collection.get("hyperliquid_funding_interval_hours") or 1.0)
    points: dict[str, dict[str, Any]] = {}
    missing_points: list[str] = []
    invalid_points: list[str] = []

    for symbol in symbols:
        symbol_points: dict[str, Any] = {}
        source_venues = parsed.get(symbol, {})
        for venue in venues:
            rate_source = "predictedFundings"
            source = source_venues.get(venue)
            if venue == "HlPerp" and direct_hl_required:
                context = direct_hl.get(symbol)
                source = (
                    {
                        "fundingRate": context.get("funding"),
                        "nextFundingTime": ((observed_at_ms // 3_600_000) + 1) * 3_600_000,
                        "fundingIntervalHours": hl_interval,
                    }
                    if isinstance(context, dict)
                    else None
                )
                rate_source = "metaAndAssetCtxs"
            point_id = f"{symbol}:{venue}"
            if not isinstance(source, dict):
                missing_points.append(point_id)
                continue
            rate = finite(source.get("fundingRate"))
            interval = finite(source.get("fundingIntervalHours"))
            try:
                next_funding_ms = int(source.get("nextFundingTime"))
            except (TypeError, ValueError):
                next_funding_ms = 0
            valid = (
                rate is not None
                and interval is not None
                and minimum_interval < interval <= maximum_interval
                and minimum_next_ms <= next_funding_ms <= maximum_next_ms
            )
            if not valid:
                invalid_points.append(point_id)
                continue
            hourly = float(rate) / float(interval)
            symbol_points[venue] = {
                "rate_source": rate_source,
                "funding_rate_raw": round(float(rate), 12),
                "funding_interval_hours": round(float(interval), 8),
                "funding_rate_per_hour": round(hourly, 12),
                "funding_rate_annualized_simple_pct": round(hourly * 24.0 * 365.0 * 100.0, 8),
                "next_funding_time_ms": next_funding_ms,
                "next_funding_time": iso_from_ms(next_funding_ms),
            }
        points[symbol] = symbol_points

    required_points = len(symbols) * len(venues)
    valid_points = sum(len(item) for item in points.values())
    checks = {
        "payload_rows": payload_rows >= int(gate["minimum_payload_rows"]),
        "direct_hyperliquid_contexts": not direct_hl_required or all(symbol in direct_hl for symbol in symbols),
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
        "source": "hyperliquid_predictedFundings_plus_metaAndAssetCtxs",
        "source_payload_sha256": canonical_sha256(payload),
        "symbols": points,
        "quality": {
            "payload_rows": payload_rows,
            "required_points": required_points,
            "valid_points": valid_points,
            "missing_points": missing_points,
            "invalid_points": invalid_points,
            "checks": checks,
            "quality_pass": all(checks.values()),
        },
        "metric_semantics": "predicted_funding_snapshot_mechanically_normalized_per_hour_not_realized_return",
        "directional_signal": None,
        "paper_entry": None,
        "orders_allowed": False,
        "can_trade": False,
    }


def read_journal(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    bad_lines = 0
    if not path.is_file():
        return rows, bad_lines
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if isinstance(payload, dict):
                rows.append(payload)
            else:
                bad_lines += 1
    return rows, bad_lines


def append_snapshot(path: Path, snapshot: dict[str, Any]) -> tuple[bool, str]:
    existing, _ = read_journal(path)
    current_bucket = int(snapshot["minute_bucket_ms"])
    previous_bucket = int(existing[-1].get("minute_bucket_ms") or 0) if existing else 0
    if previous_bucket >= current_bucket:
        return False, "duplicate_or_nonmonotonic_minute_bucket"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True, "appended"


def journal_metrics(path: Path, required_points: int) -> dict[str, Any]:
    rows, bad_lines = read_journal(path)
    buckets = [int(row.get("minute_bucket_ms") or 0) for row in rows if int(row.get("minute_bucket_ms") or 0) > 0]
    unique_buckets = sorted(set(buckets))
    quality_rows = [row for row in rows if bool((row.get("quality") or {}).get("quality_pass"))]
    coverage = (
        sum(int((row.get("quality") or {}).get("valid_points") or 0) for row in rows)
        / (len(rows) * required_points)
        if rows and required_points > 0
        else 0.0
    )
    span_minutes = (unique_buckets[-1] - unique_buckets[0]) / 60_000.0 if len(unique_buckets) >= 2 else 0.0
    unique_days = {iso_from_ms(bucket)[:10] for bucket in unique_buckets}
    return {
        "rows": len(rows),
        "bad_lines": bad_lines,
        "unique_minute_buckets": len(unique_buckets),
        "duplicate_minute_buckets": len(buckets) - len(unique_buckets),
        "quality_pass_rows": len(quality_rows),
        "required_point_coverage": round(coverage, 8),
        "first_minute_bucket": iso_from_ms(unique_buckets[0]) if unique_buckets else None,
        "last_minute_bucket": iso_from_ms(unique_buckets[-1]) if unique_buckets else None,
        "span_minutes": round(span_minutes, 3),
        "independent_utc_days": len(unique_days),
    }


def write_report(report: dict[str, Any], out_prefix: Path) -> None:
    write_json(out_prefix.with_suffix(".json"), report)
    sample = report.get("sample") or {}
    quality = report.get("snapshot_quality") or {}
    lines = [
        "# CEX-DEX Predicted Funding Lead-Lag Data Quality",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Lock: `{report.get('lock_id')}`",
        f"- Journal: `{report.get('journal')}`",
        f"- Snapshot appended: `{report.get('snapshot_appended')}`",
        f"- Snapshot quality pass: `{quality.get('quality_pass')}`",
        f"- Valid points: `{quality.get('valid_points')}` / `{quality.get('required_points')}`",
        f"- Unique minute snapshots: `{sample.get('unique_minute_buckets')}`",
        f"- Forward span: `{sample.get('span_minutes')}` minutes",
        f"- Independent UTC days: `{sample.get('independent_utc_days')}`",
        f"- Required-point coverage: `{sample.get('required_point_coverage')}`",
        "",
        "## Boundary",
        "",
        "- Public market-data endpoint only; no credentials.",
        "- This collector emits no direction, paper entry or order.",
        "- CEX values are redistributed by Hyperliquid; direct-source replication is mandatory before paper review.",
        "- `can_trade=false`.",
        "",
    ]
    out_prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


def run_once(
    contract_path: Path,
    journal_path: Path,
    out_prefix: Path,
    *,
    payload: Any | None = None,
    observed_at_ms: int | None = None,
) -> tuple[int, dict[str, Any]]:
    contract = read_json(contract_path)
    failures = validate_contract(contract)
    if failures:
        report = {
            "generated_at": iso_from_ms(now_ms()),
            "decision": "cex_dex_funding_collector_blocked_contract",
            "contract_failures": failures,
            "can_trade": False,
        }
        write_report(report, out_prefix)
        return 2, report
    observed = int(observed_at_ms if observed_at_ms is not None else now_ms())
    if payload is None:
        try:
            source = contract["source"]
            timeout = int(contract["collection"]["request_timeout_seconds"])
            payload = {
                "predictedFundings": fetch_info(str(source["url"]), "predictedFundings", timeout),
                "metaAndAssetCtxs": fetch_info(str(source["url"]), "metaAndAssetCtxs", timeout),
            }
        except Exception as exc:  # network boundary
            report = {
                "generated_at": iso_from_ms(observed),
                "decision": "cex_dex_funding_fetch_failed",
                "error": f"{type(exc).__name__}:{exc}",
                "can_trade": False,
            }
            write_report(report, out_prefix)
            return 1, report
    snapshot = derive_snapshot(payload, observed, contract)
    appended, append_reason = append_snapshot(journal_path, snapshot)
    quality = snapshot["quality"]
    if not appended:
        decision = "cex_dex_funding_duplicate_minute_skipped"
    elif quality["quality_pass"]:
        decision = "cex_dex_funding_snapshot_healthy_appended"
    else:
        decision = "cex_dex_funding_snapshot_degraded_appended"
    sample = journal_metrics(journal_path, int(quality["required_points"]))
    report = {
        "schema_version": 1,
        "generated_at": iso_from_ms(observed),
        "tool": "tools/hyperliquid_cross_venue_funding_collector.py",
        "decision": decision,
        "lock_id": contract.get("lock_id"),
        "contract": portable_path(contract_path),
        "journal": portable_path(journal_path),
        "snapshot_appended": appended,
        "append_reason": append_reason,
        "snapshot_quality": quality,
        "sample": sample,
        "research_gate": contract.get("future_research_lock"),
        "runtime_boundary": contract.get("runtime_boundary"),
        "can_trade": False,
    }
    write_report(report, out_prefix)
    return 0, report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Collect synchronized public CEX/Hyperliquid predicted-funding snapshots")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--input-json", help="Optional saved API payload for deterministic smoke tests")
    parser.add_argument("--observed-at-ms", type=int, help="Override collection time for deterministic smoke tests")
    args = parser.parse_args()
    payload = None
    if args.input_json:
        payload = json.loads(resolve_path(args.input_json).read_text(encoding="utf-8-sig"))
    code, report = run_once(
        resolve_path(args.contract),
        resolve_path(args.journal),
        resolve_path(args.out_prefix),
        payload=payload,
        observed_at_ms=args.observed_at_ms,
    )
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "snapshot_appended": report.get("snapshot_appended"),
                "sample": report.get("sample"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
