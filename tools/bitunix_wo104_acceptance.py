#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(resolve(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with resolve(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def validate_cohort(prereg_path: str | Path, policy: dict[str, Any]) -> dict[str, Any]:
    path = resolve(prereg_path)
    failures: list[str] = []
    try:
        doc = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "decision": "bitunix_wo104_cohort_binding_blocked",
            "failures": [f"prereg_read_failed:{type(exc).__name__}"],
            "can_trade": False,
        }

    expected = policy["proposal"]
    actual_file_hash = sha256_file(path)
    if actual_file_hash != expected["prereg_sha256"]:
        failures.append("prereg_file_hash_mismatch")
    if doc.get("schema") != "bitunix-setup-a-prereg-v3":
        failures.append("prereg_schema_mismatch")
    if doc.get("cohort_id") != expected["cohort_id"]:
        failures.append("cohort_id_mismatch")
    if doc.get("parameter_cohort_sha256") != expected["parameter_cohort_sha256"]:
        failures.append("declared_parameter_hash_mismatch")
    params = doc.get("params")
    if not isinstance(params, dict) or canonical_sha256(params) != expected["parameter_cohort_sha256"]:
        failures.append("computed_parameter_hash_mismatch")
    required_scope = expected["required_scope"]
    scope = doc.get("scope")
    if scope != required_scope:
        failures.append("full_scope_mismatch")

    scope_hash = canonical_sha256(scope) if isinstance(scope, dict) else None
    binding_payload = {
        "cohort_id": doc.get("cohort_id"),
        "prereg_file_sha256": actual_file_hash,
        "parameter_cohort_sha256": doc.get("parameter_cohort_sha256"),
        "scope_sha256": scope_hash,
    }
    decision = "bitunix_wo104_cohort_scope_bound" if not failures else "bitunix_wo104_cohort_binding_blocked"
    return {
        "generated_at": now_iso(),
        "decision": decision,
        "prereg": portable(path),
        "cohort_id": doc.get("cohort_id"),
        "prereg_file_sha256": actual_file_hash,
        "parameter_cohort_sha256": doc.get("parameter_cohort_sha256"),
        "scope_sha256": scope_hash,
        "cohort_binding_sha256": canonical_sha256(binding_payload),
        "failures": failures,
        "scope": scope,
        "params": params if isinstance(params, dict) else {},
        "can_trade": False,
    }


def validate_source_record(
    record: Any,
    *,
    now_ms: int,
    maximum_age_ms: int,
    expected_source_id: str | None = None,
    extra_required: tuple[str, ...] = (),
) -> list[str]:
    failures: list[str] = []
    if not isinstance(record, dict):
        return ["record_not_object"]
    required = {"source_id", "observed_at", "received_at", "source_hash", *extra_required}
    missing = sorted(required - set(record))
    failures.extend(f"missing:{item}" for item in missing)
    if missing:
        return failures
    if expected_source_id is not None and record.get("source_id") != expected_source_id:
        failures.append("source_id_mismatch")
    source_hash = str(record.get("source_hash") or "").lower()
    if HEX64.fullmatch(source_hash) is None:
        failures.append("source_hash_invalid")
    observed = record.get("observed_at")
    received = record.get("received_at")
    if not isinstance(observed, int) or isinstance(observed, bool) or observed <= 0:
        failures.append("observed_at_invalid")
    if not isinstance(received, int) or isinstance(received, bool) or received <= 0:
        failures.append("received_at_invalid")
    if failures:
        return failures
    if observed > received:
        failures.append("observed_after_received")
    if received > now_ms:
        failures.append("received_in_future")
    if now_ms - observed > maximum_age_ms:
        failures.append("source_stale")
    return failures


def validate_crowd_records(records: Any, cohort: dict[str, Any], now_ms: int) -> dict[str, Any]:
    failures: list[str] = []
    accepted: list[str] = []
    if not isinstance(records, list):
        failures.append("crowd_not_list")
        records = []
    sources = ((cohort.get("params") or {}).get("crowd_funding") or {}).get("sources") or {}
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            failures.append(f"record_{index}:record_not_object")
            continue
        source_id = str(record.get("source_id") or "")
        if source_id in seen:
            failures.append(f"record_{index}:duplicate_source_id")
            continue
        seen.add(source_id)
        spec = sources.get(source_id)
        if not isinstance(spec, dict):
            failures.append(f"record_{index}:source_not_in_frozen_cohort")
            continue
        record_failures = validate_source_record(
            record,
            now_ms=now_ms,
            maximum_age_ms=int(spec["freshness_max_age_ms"]),
            expected_source_id=source_id,
            extra_required=("value",),
        )
        if not _finite_number(record.get("value")):
            record_failures.append("value_invalid")
        if record_failures:
            failures.extend(f"record_{index}:{item}" for item in record_failures)
        else:
            accepted.append(source_id)
    quorum = int((((cohort.get("params") or {}).get("crowd_funding") or {}).get("quorum_fresh_inputs_required")) or 0)
    if len(accepted) < quorum:
        failures.append(f"crowd_quorum_not_met:{len(accepted)}<{quorum}")
    return {
        "decision": "bitunix_wo104_crowd_receipts_bound" if not failures else "bitunix_wo104_crowd_receipts_blocked",
        "accepted_sources": accepted,
        "records_sha256": canonical_sha256(records),
        "failures": failures,
        "can_trade": False,
    }


def load_module(path: str | Path, name: str) -> ModuleType:
    module_path = resolve(path)
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def bind_sfp_detection(
    bars: Any,
    *,
    as_of_ts: int,
    cohort: dict[str, Any],
    detector_path: str | Path,
    detector: Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    if not isinstance(bars, list) or not bars:
        failures.append("bars_missing")
        bars = []
    timestamps = [bar.get("ts") for bar in bars if isinstance(bar, dict)]
    if len(timestamps) != len(bars) or any(not isinstance(ts, int) or isinstance(ts, bool) for ts in timestamps):
        failures.append("bar_timestamp_invalid")
    elif timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
        failures.append("bars_non_monotonic")
    elif timestamps and timestamps[-1] > as_of_ts:
        failures.append("bar_after_as_of")

    result: dict[str, Any] = {"detected": False}
    path = resolve(detector_path)
    if not failures:
        if detector is None:
            module = load_module(path, "_bitunix_wo104_setup_gate")
            detector = module.detect_sfp
        result = detector(bars, cohort.get("params") or {})
        if not isinstance(result, dict):
            failures.append("detector_result_not_object")
            result = {"detected": False}
        elif result.get("detected"):
            if result.get("uses_future_bars") is not False:
                failures.append("detector_future_bar_violation")
            if int(result.get("entry_eligible_after_ts") or 0) > as_of_ts:
                failures.append("entry_after_as_of")

    receipt_payload = {
        "detector_sha256": sha256_file(path),
        "bars_sha256": canonical_sha256(bars),
        "input_max_ts": timestamps[-1] if timestamps else None,
        "as_of_ts": as_of_ts,
        "result_sha256": canonical_sha256(result),
    }
    return {
        "decision": "bitunix_wo104_sfp_detector_receipt_bound" if not failures else "bitunix_wo104_sfp_detector_receipt_blocked",
        **receipt_payload,
        "receipt_sha256": canonical_sha256(receipt_payload),
        "result": result,
        "failures": failures,
        "can_trade": False,
    }


def plan_entry_bound(signal_close_ts_ms: int, book_states: Any, latency_ms: int) -> dict[str, Any]:
    failures: list[str] = []
    if not isinstance(book_states, list) or not book_states:
        failures.append("book_states_missing")
        book_states = []
    timestamps: list[int] = []
    for index, state in enumerate(book_states):
        if not isinstance(state, dict):
            failures.append(f"book_{index}:not_object")
            continue
        ts = state.get("ts")
        if not isinstance(ts, int) or isinstance(ts, bool) or ts <= 0:
            failures.append(f"book_{index}:timestamp_invalid")
        else:
            timestamps.append(ts)
        if HEX64.fullmatch(str(state.get("source_hash") or "").lower()) is None:
            failures.append(f"book_{index}:source_hash_invalid")
    if len(timestamps) == len(book_states) and (timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps)):
        failures.append("book_states_non_monotonic")
    target = signal_close_ts_ms + latency_ms
    eligible = next((ts for ts in timestamps if ts >= target), None) if not failures else None
    return {
        "decision": "bitunix_wo104_entry_receipt_bound" if not failures and eligible is not None else "bitunix_wo104_entry_hold",
        "target_ts": target,
        "book_state_ts": eligible,
        "book_states_sha256": canonical_sha256(book_states),
        "failures": failures + ([] if eligible is not None or failures else ["no_eligible_book_state"]),
        "can_trade": False,
    }


def adjudicate_edge_receipt(receipt: Any, cohort: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if not isinstance(receipt, dict):
        receipt = {}
        failures.append("receipt_not_object")
    required = set(policy["edge_receipt"]["required_identity_fields"])
    failures.extend(f"missing:{field}" for field in sorted(required - set(receipt)))
    for field in ("cohort_binding_sha256", "evaluator_sha256", "cost_model_sha256", "source_manifest_sha256"):
        if field in receipt and HEX64.fullmatch(str(receipt.get(field) or "").lower()) is None:
            failures.append(f"{field}_invalid")
    if receipt.get("cohort_binding_sha256") != cohort.get("cohort_binding_sha256"):
        failures.append("cohort_binding_mismatch")
    for field in ("evaluator_id", "cost_model_id"):
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            failures.append(f"{field}_invalid")
    sample_size = receipt.get("sample_size")
    minimum = int(policy["edge_receipt"]["minimum_forward_sample"])
    if not isinstance(sample_size, int) or isinstance(sample_size, bool) or sample_size < minimum:
        failures.append("sample_size_below_minimum")
    if not _finite_number(receipt.get("net_edge_R")):
        failures.append("net_edge_R_invalid")
    try:
        start = datetime.fromisoformat(str(receipt.get("data_start")).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(receipt.get("data_end")).replace("Z", "+00:00"))
        if start >= end:
            failures.append("data_range_invalid")
    except ValueError:
        failures.append("data_range_invalid")
    return {
        "decision": "bitunix_wo104_edge_receipt_evaluated_not_accepted" if not failures else "bitunix_wo104_edge_receipt_blocked",
        "edge_evaluated": not failures,
        "edge_accepted": False,
        "promotion": "HOLD",
        "receipt_identity_sha256": canonical_sha256(receipt),
        "failures": sorted(set(failures)),
        "can_trade": False,
    }


def _sum_values(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    return sum(int(item or 0) for item in value.values())


def adjudicate_capture_manifest(
    manifest_path: str | Path,
    close_receipts_path: str | Path,
    policy: dict[str, Any],
) -> dict[str, Any]:
    path = resolve(manifest_path)
    close_path = resolve(close_receipts_path)
    failures: list[str] = []
    try:
        manifest = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        manifest = {}
        failures.append(f"manifest_read_failed:{type(exc).__name__}")
    try:
        close_receipts = read_json(close_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        close_receipts = {}
        failures.append(f"close_receipts_read_failed:{type(exc).__name__}")

    cfg = policy["capture"]
    if close_receipts.get("schema") != "tradingos-bitunix-close-fsync-receipts-v1":
        failures.append("close_receipts_schema_mismatch")
    if close_receipts.get("method") != "wrapper_records_successful_return_from_writer_flush_fsync_close":
        failures.append("close_receipts_method_mismatch")
    if close_receipts.get("writer_newline_policy") != "LF":
        failures.append("writer_newline_policy_not_lf")
    if close_receipts.get("policy_sha256") != canonical_sha256(policy):
        failures.append("close_receipts_policy_hash_mismatch")
    if close_receipts.get("acceptance_sha256") != sha256_file(Path(__file__)):
        failures.append("close_receipts_acceptance_hash_mismatch")
    runner_path = ROOT / "tools" / "bitunix_wo104_public_capture_runner.py"
    if close_receipts.get("runner_sha256") != sha256_file(runner_path):
        failures.append("close_receipts_runner_hash_mismatch")
    if close_receipts.get("can_trade") is not False:
        failures.append("close_receipts_can_trade_not_false")
    if manifest.get("schema") != cfg["required_schema"]:
        failures.append("capture_schema_mismatch")
    if manifest.get("can_trade") is not False:
        failures.append("capture_can_trade_not_false")
    if manifest.get("remote_effect_permission") != "PUBLIC_READ_ONLY_CAPTURE_ONLY":
        failures.append("remote_effect_permission_mismatch")
    for field in ("credentials_used", "private_calls", "order_calls"):
        if int(manifest.get(field, -1)) != 0:
            failures.append(f"{field}_nonzero")
    requested = float(manifest.get("duration_requested_s") or 0)
    actual = float(manifest.get("duration_actual_s") or 0)
    if requested < int(cfg["minimum_requested_duration_seconds"]):
        failures.append("requested_duration_below_minimum")
    if actual < requested * float(cfg["minimum_duration_fraction"]):
        failures.append("actual_duration_below_fraction")
    if int(manifest.get("frames_total") or 0) < int(cfg["minimum_frames"]):
        failures.append("frames_below_minimum")
    if sorted(manifest.get("symbols") or []) != sorted(cfg["required_symbols"]):
        failures.append("symbol_scope_mismatch")
    if sorted(manifest.get("channels") or []) != sorted(cfg["required_channels"]):
        failures.append("channel_scope_mismatch")

    subscription = manifest.get("subscription_acceptance") or {}
    if subscription.get("accepted") is not True:
        failures.append("subscription_not_accepted")
    if sorted(subscription.get("covered") or []) != sorted(cfg["required_subscription_coverage"]):
        failures.append("subscription_coverage_mismatch")
    if subscription.get("missing"):
        failures.append("subscription_missing_channels")
    if _sum_values(manifest.get("unknown_schema_ledger")) != 0:
        failures.append("unknown_schema_nonzero")
    if int(manifest.get("future_skew_frames") or 0) != 0:
        failures.append("future_skew_nonzero")
    if int(manifest.get("out_of_order_total") or 0) != 0:
        failures.append("out_of_order_nonzero")
    if _sum_values(manifest.get("stale_events")) != 0:
        failures.append("stale_events_nonzero")
    max_gap = max([float(value) for value in (manifest.get("max_depth_gap_ms") or {}).values()] or [0.0])
    if max_gap > float(cfg["maximum_depth_gap_ms"]):
        failures.append("maximum_depth_gap_exceeded")
    if float(manifest.get("max_recv_silence_ms") or 0) > float(cfg["maximum_receive_silence_ms"]):
        failures.append("maximum_receive_silence_exceeded")
    final_ages = list((manifest.get("final_depth_age_ms") or {}).values()) + list((manifest.get("final_trade_age_ms") or {}).values())
    if any(float(value) > float(cfg["maximum_final_age_ms"]) for value in final_ages):
        failures.append("maximum_final_age_exceeded")
    if float(manifest.get("reconnect_downtime_ms") or 0) > float(cfg["maximum_reconnect_downtime_ms"]):
        failures.append("maximum_reconnect_downtime_exceeded")
    if int(manifest.get("reconnects") or 0) > int(cfg["maximum_reconnects_until_drop_epoch_fix"]):
        failures.append("reconnect_present_while_downtime_epoch_untrusted")
    errors = manifest.get("error_taxonomy") or {}
    if int(errors.get("LOCAL") or 0) != 0 or int(errors.get("STORAGE") or 0) != 0:
        failures.append("local_or_storage_error_nonzero")
    if manifest.get("terminal_hold") is not False or manifest.get("hold") is not False:
        failures.append("proposal_manifest_on_hold")
    code_receipts = ((manifest.get("receipts") or {}).get("code_sha256") or {})
    if code_receipts.get("public_ws_venue.py") != policy["proposal"]["parser_sha256"]:
        failures.append("manifest_parser_hash_mismatch")
    if code_receipts.get("bitunix_public_capture.py") != policy["proposal"]["capture_harness_sha256"]:
        failures.append("manifest_capture_harness_hash_mismatch")

    capture_dir = path.parent
    declared_hashes = ((manifest.get("receipts") or {}).get("streaming_output_sha256") or {})
    close_files = close_receipts.get("files") if isinstance(close_receipts.get("files"), dict) else {}
    file_receipts: dict[str, Any] = {}
    for filename in cfg["required_output_files"]:
        candidate = capture_dir / filename
        item: dict[str, Any] = {"exists": candidate.is_file()}
        if not candidate.is_file():
            failures.append(f"output_missing:{filename}")
        else:
            actual_hash = sha256_file(candidate)
            item["actual_sha256"] = actual_hash
            item["declared_sha256"] = declared_hashes.get(filename)
            if declared_hashes.get(filename) != actual_hash:
                failures.append(f"streaming_hash_mismatch:{filename}")
            close_item = close_files.get(filename) if isinstance(close_files.get(filename), dict) else {}
            item["close_ok"] = close_item.get("close_ok")
            item["fsync_ok"] = close_item.get("fsync_ok")
            item["close_receipt_sha256"] = close_item.get("sha256")
            if close_item.get("close_ok") is not True or close_item.get("fsync_ok") is not True:
                failures.append(f"close_fsync_receipt_missing:{filename}")
            if close_item.get("sha256") != actual_hash:
                failures.append(f"close_receipt_hash_mismatch:{filename}")
        file_receipts[filename] = item

    decision = "bitunix_wo104_public_contract_confirmed_shadow_hold" if not failures else "bitunix_wo104_capture_invalid_hold"
    return {
        "generated_at": now_iso(),
        "decision": decision,
        "proposal_status": "PUBLIC_CONTRACT_CONFIRMED" if not failures else "CAPTURE_INVALID",
        "setup_status": "FROZEN_SHADOW_POLICY_ORACLE",
        "promotion": "HOLD",
        "edge_evaluated": False,
        "manifest": portable(path),
        "manifest_sha256": sha256_file(path) if path.is_file() else None,
        "file_receipts": file_receipts,
        "failures": sorted(set(failures)),
        "can_trade": False,
    }


def replay_public_frames(raw_path: str | Path, parser_path: str | Path) -> dict[str, Any]:
    path = resolve(raw_path)
    parser = load_module(parser_path, "_bitunix_wo104_public_parser")
    kinds: dict[str, int] = {}
    channels: set[str] = set()
    unknown: dict[str, int] = {}
    total = 0
    decode_failures = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            total += 1
            try:
                frame = json.loads(line)
                if isinstance(frame, str):
                    frame = json.loads(frame)
                if not isinstance(frame, dict):
                    raise ValueError("frame_not_object")
            except (json.JSONDecodeError, ValueError):
                decode_failures += 1
                continue
            venue_ts = frame.get("ts")
            now_ms = int(venue_ts) if isinstance(venue_ts, (int, float)) and venue_ts > 0 else None
            parsed = parser.parse_public_frame(frame, now_ms=now_ms)
            name = type(parsed).__name__
            kinds[name] = kinds.get(name, 0) + 1
            channel = getattr(parsed, "channel", None)
            if channel:
                channels.add(str(channel))
            if name == "UnknownSchema":
                reason = str(getattr(parsed, "reason", "unknown"))
                unknown[reason] = unknown.get(reason, 0) + 1
    decision = "bitunix_wo104_historical_schema_replay_pass" if not unknown and not decode_failures else "bitunix_wo104_historical_schema_replay_hold"
    return {
        "generated_at": now_iso(),
        "decision": decision,
        "scope": "historical_schema_replay_only_not_arrival_quality",
        "raw_frames": portable(path),
        "raw_frames_sha256": sha256_file(path),
        "frames_total": total,
        "parse_kinds": kinds,
        "channels": sorted(channels),
        "unknown_schema": unknown,
        "decode_failures": decode_failures,
        "reviewed_123_frame_sample_supplied": False,
        "canonical_replay_status": "REPLAY_PENDING",
        "can_trade": False,
    }


def write_report(report: dict[str, Any], out_path: str | Path) -> None:
    path = resolve(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent TradingOS acceptance layer for Bitunix WO-104")
    parser.add_argument("--policy", default="configs/BITUNIX_WO104_INDEPENDENT_ACCEPTANCE_POLICY.json")
    sub = parser.add_subparsers(dest="command", required=True)

    cohort_cmd = sub.add_parser("cohort")
    cohort_cmd.add_argument("prereg")
    cohort_cmd.add_argument("--out", required=True)

    capture_cmd = sub.add_parser("capture")
    capture_cmd.add_argument("manifest")
    capture_cmd.add_argument("close_receipts")
    capture_cmd.add_argument("--out", required=True)

    replay_cmd = sub.add_parser("replay")
    replay_cmd.add_argument("raw_frames")
    replay_cmd.add_argument("parser")
    replay_cmd.add_argument("--out", required=True)

    args = parser.parse_args()
    policy = read_json(args.policy)
    if args.command == "cohort":
        report = validate_cohort(args.prereg, policy)
    elif args.command == "capture":
        report = adjudicate_capture_manifest(args.manifest, args.close_receipts, policy)
    else:
        report = replay_public_frames(args.raw_frames, args.parser)
    write_report(report, args.out)
    print(json.dumps({"decision": report["decision"], "failures": report.get("failures", []), "can_trade": False}))
    return 0 if not report.get("failures") else 2


if __name__ == "__main__":
    raise SystemExit(main())
