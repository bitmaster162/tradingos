#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import bitunix_public_kline_timing_probe as probe  # noqa: E402
from tools import bitunix_wo105_public_rest_collector as rest  # noqa: E402


TOOL_PATH = "tools/bitunix_kline_receipt_finality_verifier.py"
WS_TO_REST_FIELDS = {"o": "open", "h": "high", "l": "low", "c": "close"}


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
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_jsonl:{path}:{line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"object_expected:{path}:{line_number}")
        rows.append(payload)
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


def decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def verify_final_values(
    records: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    rest_items: list[dict[str, Any]],
) -> dict[str, Any]:
    ws_by_bucket: dict[int, list[dict[str, Any]]] = {}
    for row in records:
        bucket = probe.integer(row.get("bucket_start_ms"))
        if bucket is not None:
            ws_by_bucket.setdefault(bucket, []).append(row)
    rest_by_bucket = {
        bucket: item
        for item in rest_items
        if isinstance(item, dict) and (bucket := probe.integer(item.get("time"))) is not None
    }
    comparisons: list[dict[str, Any]] = []
    blockers: list[str] = []
    for transition in transitions:
        bucket = int(transition["closed_bucket_start_ms"])
        ws_rows = sorted(ws_by_bucket.get(bucket, []), key=lambda row: int(row["recv_ns"]))
        rest_item = rest_by_bucket.get(bucket)
        if not ws_rows:
            blockers.append(f"ws_bucket_missing:{bucket}")
            continue
        if rest_item is None:
            blockers.append(f"rest_bucket_missing:{bucket}")
            continue
        ws_payload = ws_rows[-1].get("payload") if isinstance(ws_rows[-1].get("payload"), dict) else {}
        ws_ohlc = {rest_field: str(ws_payload.get(ws_field)) for ws_field, rest_field in WS_TO_REST_FIELDS.items()}
        rest_ohlc = {rest_field: str(rest_item.get(rest_field)) for rest_field in WS_TO_REST_FIELDS.values()}
        field_equal: dict[str, bool] = {}
        for ws_field, rest_field in WS_TO_REST_FIELDS.items():
            ws_value = decimal(ws_payload.get(ws_field))
            rest_value = decimal(rest_item.get(rest_field))
            field_equal[rest_field] = ws_value is not None and rest_value is not None and ws_value == rest_value
        equal = all(field_equal.values())
        if not equal:
            blockers.append(f"final_ohlc_mismatch:{bucket}")
        comparisons.append(
            {
                "bucket_start_ms": bucket,
                "within_preregistered_cutoff": transition.get("within_cutoff") is True,
                "field_equal": field_equal,
                "final_ohlc_equal": equal,
                "ws_last_snapshot_recv_ms": int(ws_rows[-1]["recv_ms"]),
                "ws_ohlc": ws_ohlc,
                "rest_ohlc": rest_ohlc,
            }
        )
    verified = (
        bool(comparisons)
        and not blockers
        and len(comparisons) == len(transitions)
        and all(item["within_preregistered_cutoff"] and item["final_ohlc_equal"] for item in comparisons)
    )
    return {
        "transition_count": len(transitions),
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
        "blockers": sorted(set(blockers)),
        "timely_final_ohlc_verified": verified,
        "volume_finality_verified": False,
    }


def latest_completed_run(root: Path) -> Path:
    candidates = [path for path in root.glob("run_*") if (path / "KLINE_TIMING_REPORT.json").is_file()]
    if not candidates:
        raise FileNotFoundError("no_completed_kline_timing_run")
    return sorted(candidates, key=lambda path: path.name)[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify timely Bitunix WS kline snapshots against later public REST")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--run-root", default="data/forward/bitunix_kline_timing")
    args = parser.parse_args()
    run_root = Path(args.run_root)
    if not run_root.is_absolute():
        run_root = ROOT / run_root
    run_dir = Path(args.run_dir) if args.run_dir else latest_completed_run(run_root)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir

    timing_report = read_json(run_dir / "KLINE_TIMING_REPORT.json")
    raw_path = run_dir / "KLINE_RECEIPTS.jsonl"
    expected_hash = ((timing_report.get("evidence") or {}).get("raw_receipts_sha256"))
    failures: list[str] = []
    if expected_hash != sha256_file(raw_path):
        failures.append("raw_receipts_hash_mismatch")
    records = read_jsonl(raw_path)
    analysis = timing_report.get("analysis") if isinstance(timing_report.get("analysis"), dict) else {}
    transitions = analysis.get("transitions") if isinstance(analysis.get("transitions"), list) else []
    if not transitions:
        failures.append("no_observed_rollover_transition")

    rest_items: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {}
    if transitions:
        earliest = min(int(item["closed_bucket_start_ms"]) for item in transitions)
        latest = max(int(item["boundary_ms"]) for item in transitions)
        try:
            envelope, receipt = rest.public_get(
                "kline",
                {
                    "symbol": timing_report["symbol"],
                    "interval": "5m" if timing_report["channel"] == "market_kline_5min" else "1m",
                    "startTime": earliest,
                    "endTime": latest,
                    "limit": min(200, len(transitions) + 4),
                    "type": "LAST_PRICE",
                },
            )
            rest_items = rest.data_items(envelope)
        except (RuntimeError, ValueError, KeyError) as exc:
            failures.append(f"rest_verification_failed:{type(exc).__name__}:{exc}")
    verification = verify_final_values(records, transitions, rest_items)
    failures.extend(verification["blockers"])
    decision = "bitunix_kline_receipt_finality_verified"
    if failures or not verification["timely_final_ohlc_verified"]:
        decision = "bitunix_kline_receipt_finality_not_verified"
    generated_at = probe.now_iso()
    report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "tool": TOOL_PATH,
        "decision": decision,
        "run_dir": str(run_dir),
        "timing_report_sha256": sha256_file(run_dir / "KLINE_TIMING_REPORT.json"),
        "rest_receipt": receipt,
        "verification": verification,
        "failures": sorted(set(failures)),
        "runtime_boundary": {
            "public_data_only": True,
            "outcome_values_used_only_for_post_capture_finality_audit": True,
            "strategy_evaluation": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
        "can_trade": False,
    }
    stamp = generated_at.replace("-", "").replace(":", "")
    output = run_dir / f"KLINE_FINALITY_VERIFICATION_{stamp}.json"
    atomic_json(output, report)
    atomic_json(run_root / "LATEST_KLINE_FINALITY_VERIFICATION.json", report)
    print(
        json.dumps(
            {
                "decision": decision,
                "comparisons": verification["comparison_count"],
                "failures": report["failures"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0 if decision == "bitunix_kline_receipt_finality_verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
