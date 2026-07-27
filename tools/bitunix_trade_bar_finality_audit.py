#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_wo105_public_rest_collector as rest  # noqa: E402


TOOL_PATH = "tools/bitunix_trade_bar_finality_audit.py"
INTERVAL_MS = 300_000
ACCEPTED_DECISION = "bitunix_wo104_public_contract_confirmed_shadow_hold"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_ms(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"object_expected:{path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_jsonl:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"object_expected:{line_number}")
        rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def build_trade_bars(
    rows: list[dict[str, Any]],
    *,
    capture_start_ms: int,
    capture_end_ms: int,
    symbol: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    first_bucket = ((capture_start_ms + INTERVAL_MS - 1) // INTERVAL_MS) * INTERVAL_MS
    final_boundary = (capture_end_ms // INTERVAL_MS) * INTERVAL_MS
    expected_buckets = list(range(first_bucket, final_boundary, INTERVAL_MS))
    grouped: dict[int, list[tuple[int, int, Decimal, Decimal]]] = {}
    failures: list[str] = []
    for index, row in enumerate(rows):
        if row.get("symbol") != symbol:
            continue
        venue_ts = rest.integer(row.get("venue_ts"))
        recv_ns = rest.integer(row.get("recv_ns"))
        price = decimal(row.get("p"))
        size = decimal(row.get("v"))
        if venue_ts is None or recv_ns is None or price is None or size is None or price <= 0 or size <= 0:
            failures.append(f"trade_row_invalid:{index}")
            continue
        bucket = venue_ts - (venue_ts % INTERVAL_MS)
        if bucket in expected_buckets:
            grouped.setdefault(bucket, []).append((venue_ts, recv_ns, price, size))
    bars: list[dict[str, Any]] = []
    for bucket in expected_buckets:
        trades = sorted(grouped.get(bucket, []), key=lambda item: (item[0], item[1]))
        if not trades:
            failures.append(f"full_bucket_has_no_trades:{bucket}")
            continue
        prices = [item[2] for item in trades]
        coin_volume = sum((item[3] for item in trades), Decimal("0"))
        quote_volume = sum((item[2] * item[3] for item in trades), Decimal("0"))
        bars.append(
            {
                "bucket_start_ms": bucket,
                "close_ms": bucket + INTERVAL_MS,
                "open": str(prices[0]),
                "high": str(max(prices)),
                "low": str(min(prices)),
                "close": str(prices[-1]),
                "coin_volume": str(coin_volume),
                "quote_volume": str(quote_volume),
                "trade_count": len(trades),
                "first_trade_venue_ts": trades[0][0],
                "last_trade_venue_ts": trades[-1][0],
                "last_trade_recv_ms": trades[-1][1] // 1_000_000,
            }
        )
    return bars, sorted(set(failures))


def compare_bars(bars: list[dict[str, Any]], rest_items: list[dict[str, Any]]) -> dict[str, Any]:
    rest_by_bucket = {
        bucket: item
        for item in rest_items
        if isinstance(item, dict) and (bucket := rest.integer(item.get("time"))) is not None
    }
    comparisons: list[dict[str, Any]] = []
    blockers: list[str] = []
    for bar in bars:
        bucket = int(bar["bucket_start_ms"])
        item = rest_by_bucket.get(bucket)
        if item is None:
            blockers.append(f"rest_bucket_missing:{bucket}")
            continue
        field_equal = {
            "open": decimal(bar["open"]) == decimal(item.get("open")),
            "high": decimal(bar["high"]) == decimal(item.get("high")),
            "low": decimal(bar["low"]) == decimal(item.get("low")),
            "close": decimal(bar["close"]) == decimal(item.get("close")),
            "coin_volume": decimal(bar["coin_volume"]) == decimal(item.get("quoteVol")),
            "quote_volume": decimal(bar["quote_volume"]) == decimal(item.get("baseVol")),
        }
        if not all(field_equal.values()):
            blockers.append(f"trade_bar_mismatch:{bucket}")
        comparisons.append(
            {
                "bucket_start_ms": bucket,
                "field_equal": field_equal,
                "all_fields_equal": all(field_equal.values()),
                "trade_count": bar["trade_count"],
                "trade_bar": {key: bar[key] for key in ("open", "high", "low", "close", "coin_volume", "quote_volume")},
                "rest_bar": {
                    "open": str(item.get("open")),
                    "high": str(item.get("high")),
                    "low": str(item.get("low")),
                    "close": str(item.get("close")),
                    "coin_volume": str(item.get("quoteVol")),
                    "quote_volume": str(item.get("baseVol")),
                },
            }
        )
    return {
        "bar_count": len(bars),
        "comparison_count": len(comparisons),
        "matching_bars": sum(1 for item in comparisons if item["all_fields_equal"]),
        "comparisons": comparisons,
        "blockers": sorted(set(blockers)),
        "all_full_bars_match": bool(comparisons) and len(comparisons) == len(bars) and not blockers,
    }


def latest_accepted_run(root: Path) -> Path:
    candidates: list[Path] = []
    for path in root.glob("run_*"):
        acceptance_path = path / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json"
        if not acceptance_path.is_file():
            continue
        try:
            acceptance = read_json(acceptance_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if acceptance.get("decision") == ACCEPTED_DECISION and not acceptance.get("failures"):
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError("no_accepted_completed_ws_capture")
    return sorted(candidates, key=lambda path: path.name)[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit accepted Bitunix public trades as finalized 5m OHLCV bars")
    parser.add_argument("--capture-root", default="data/forward/bitunix_wo105_v3r4_ws")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--out-root", default="data/forward/bitunix_trade_bar_finality")
    args = parser.parse_args()
    capture_root = Path(args.capture_root)
    out_root = Path(args.out_root)
    if not capture_root.is_absolute():
        capture_root = ROOT / capture_root
    if not out_root.is_absolute():
        out_root = ROOT / out_root
    run_dir = Path(args.run_dir) if args.run_dir else latest_accepted_run(capture_root)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir

    manifest = read_json(run_dir / "PUBLIC_CAPTURE_MANIFEST.json")
    acceptance = read_json(run_dir / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json")
    trades_path = run_dir / "TRADES.jsonl"
    failures: list[str] = []
    expected_hash = (((manifest.get("receipts") or {}).get("streaming_output_sha256") or {}).get("TRADES.jsonl"))
    if expected_hash != sha256_file(trades_path):
        failures.append("trades_hash_mismatch")
    if acceptance.get("decision") != ACCEPTED_DECISION or acceptance.get("failures"):
        failures.append("capture_not_independently_accepted")
    if manifest.get("hold") is not False or manifest.get("terminal_hold") is not False:
        failures.append("capture_manifest_hold")
    if any(int(value or 0) for value in (manifest.get("error_taxonomy") or {}).values()):
        failures.append("capture_error_taxonomy_nonzero")
    started = parse_iso_ms(manifest.get("started_utc"))
    ended = parse_iso_ms(manifest.get("ended_utc"))
    if started is None or ended is None or ended <= started:
        failures.append("capture_time_boundary_invalid")
        started, ended = 0, 0
    rows = read_jsonl(trades_path)
    bars, build_failures = build_trade_bars(
        rows,
        capture_start_ms=started,
        capture_end_ms=ended,
        symbol=args.symbol,
    )
    failures.extend(build_failures)

    rest_items: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {}
    if bars:
        try:
            envelope, receipt = rest.public_get(
                "kline",
                {
                    "symbol": args.symbol,
                    "interval": "5m",
                    "startTime": int(bars[0]["bucket_start_ms"]),
                    "endTime": int(bars[-1]["close_ms"]),
                    "limit": min(200, len(bars) + 4),
                    "type": "LAST_PRICE",
                },
            )
            rest_items = rest.data_items(envelope)
        except (RuntimeError, ValueError) as exc:
            failures.append(f"rest_verification_failed:{type(exc).__name__}:{exc}")
    comparison = compare_bars(bars, rest_items)
    failures.extend(comparison["blockers"])
    decision = "bitunix_trade_bars_match_final_rest"
    if failures or not comparison["all_full_bars_match"]:
        decision = "bitunix_trade_bars_not_proven_final"
    generated_at = now_iso()
    report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "tool": TOOL_PATH,
        "decision": decision,
        "capture_run": str(run_dir),
        "capture_manifest_sha256": sha256_file(run_dir / "PUBLIC_CAPTURE_MANIFEST.json"),
        "trades_sha256": sha256_file(trades_path),
        "capture_quality": {
            "independent_acceptance_decision": acceptance.get("decision"),
            "duration_actual_s": manifest.get("duration_actual_s"),
            "max_recv_silence_ms": manifest.get("max_recv_silence_ms"),
            "reconnects": manifest.get("reconnects"),
            "error_taxonomy": manifest.get("error_taxonomy"),
        },
        "rest_receipt": receipt,
        "comparison": comparison,
        "failures": sorted(set(failures)),
        "runtime_boundary": {
            "public_data_only": True,
            "post_capture_data_quality_audit": True,
            "strategy_evaluation": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    stamp = generated_at.replace("-", "").replace(":", "")
    output = out_root / f"TRADE_BAR_FINALITY_{stamp}.json"
    atomic_json(output, report)
    atomic_json(out_root / "LATEST_TRADE_BAR_FINALITY.json", report)
    print(
        json.dumps(
            {
                "decision": decision,
                "bars": comparison["bar_count"],
                "matching": comparison["matching_bars"],
                "failures": report["failures"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if decision == "bitunix_trade_bars_match_final_rest" else 2


if __name__ == "__main__":
    raise SystemExit(main())
