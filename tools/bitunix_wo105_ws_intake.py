#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TOOL_PATH = "tools/bitunix_wo105_ws_intake.py"
ACCEPTED_DECISION = "bitunix_wo104_public_contract_confirmed_shadow_hold"
CAPTURE_SCHEMA = "bitunix-public-capture-v4"
REQUIRED_FILES = ("RAW_FRAMES.jsonl", "RAW_FRAME_INDEX.jsonl", "TRADES.jsonl")
DEFAULT_CVD_WINDOW_MS = 20 * 60 * 1000
DEFAULT_CVD_MIN_PRINTS = 50


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def parse_iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def jsonl_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def finite_positive(value: Any, *, allow_zero: bool = False) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and (number >= 0 if allow_zero else number > 0)


def make_record(
    *,
    source_id: str,
    observed_at: int,
    received_at: int,
    schema_version: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "observed_at": observed_at,
        "received_at": received_at,
        "source_hash": canonical_sha256(payload),
        "schema_version": schema_version,
        "payload": payload,
    }


def verify_capture_receipts(
    run_dir: Path,
    acceptance: dict[str, Any],
    manifest: dict[str, Any],
    *,
    expected_parser_sha256: str,
) -> list[str]:
    failures: list[str] = []
    if acceptance.get("decision") != ACCEPTED_DECISION or acceptance.get("failures") not in ([], None):
        failures.append("independent_acceptance_not_pass")
    if acceptance.get("can_trade") is not False:
        failures.append("acceptance_can_trade_not_false")
    manifest_path = run_dir / "PUBLIC_CAPTURE_MANIFEST.json"
    if acceptance.get("manifest_sha256") != sha256_file(manifest_path):
        failures.append("acceptance_manifest_hash_mismatch")
    if manifest.get("schema") != CAPTURE_SCHEMA:
        failures.append("capture_manifest_schema_invalid")
    if manifest.get("symbols") != ["BTCUSDT"]:
        failures.append("capture_symbol_scope_invalid")
    if set(manifest.get("channels") or []) != {"depth_book15", "trade"}:
        failures.append("capture_channel_scope_invalid")
    if manifest.get("hold") is not False or manifest.get("terminal_hold") is not False:
        failures.append("capture_manifest_hold")
    if manifest.get("credentials_used") != 0 or manifest.get("private_calls") != 0 or manifest.get("order_calls") != 0:
        failures.append("capture_not_public_read_only")
    errors = manifest.get("error_taxonomy") if isinstance(manifest.get("error_taxonomy"), dict) else {}
    if any(int(errors.get(kind, 0)) != 0 for kind in ("NETWORK", "PARSER", "LOCAL", "STORAGE")):
        failures.append("capture_error_taxonomy_nonzero")
    code = ((manifest.get("receipts") or {}).get("code_sha256") or {})
    if code.get("public_ws_venue.py") != expected_parser_sha256:
        failures.append("canonical_parser_hash_mismatch")
    acceptance_files = acceptance.get("file_receipts") if isinstance(acceptance.get("file_receipts"), dict) else {}
    manifest_files = ((manifest.get("receipts") or {}).get("streaming_output_sha256") or {})
    for name in REQUIRED_FILES:
        path = run_dir / name
        if not path.is_file():
            failures.append(f"capture_file_missing:{name}")
            continue
        actual = sha256_file(path)
        receipt = acceptance_files.get(name) if isinstance(acceptance_files.get(name), dict) else {}
        if actual != receipt.get("actual_sha256") or actual != receipt.get("declared_sha256"):
            failures.append(f"acceptance_file_hash_mismatch:{name}")
        if receipt.get("close_ok") is not True or receipt.get("fsync_ok") is not True:
            failures.append(f"capture_file_not_fsync_closed:{name}")
        if actual != manifest_files.get(name):
            failures.append(f"manifest_file_hash_mismatch:{name}")
    return sorted(set(failures))


def _monotonic_causal_ms(venue_ms: int, local_receive_ms: int, previous: int) -> int:
    # A venue clock can lead the local clock. Delaying admission is conservative;
    # moving an event backwards to local receive time would create look-ahead.
    return max(venue_ms, local_receive_ms, previous + 1)


def derive_cvd_record(
    trades: list[dict[str, Any]],
    *,
    capture_manifest_sha256: str,
    minimum_window_ms: int = DEFAULT_CVD_WINDOW_MS,
    minimum_prints: int = DEFAULT_CVD_MIN_PRINTS,
) -> tuple[dict[str, Any] | None, list[str]]:
    if len(trades) < minimum_prints:
        return None, [f"cvd_insufficient_trade_prints:{len(trades)}<{minimum_prints}"]
    received = [int(row["received_at"]) for row in trades]
    coverage_ms = max(received) - min(received)
    if coverage_ms < minimum_window_ms:
        return None, [f"cvd_insufficient_window_ms:{coverage_ms}<{minimum_window_ms}"]
    buy_volume = sum(float(row["payload"]["size"]) for row in trades if row["payload"]["side"] == "buy")
    sell_volume = sum(float(row["payload"]["size"]) for row in trades if row["payload"]["side"] == "sell")
    total = buy_volume + sell_volume
    if total <= 0:
        return None, ["cvd_total_volume_nonpositive"]
    value = (buy_volume - sell_volume) / total
    payload = {
        "kind": "cvd_norm",
        "value": value,
        "unit": "signed_volume_share",
        "method": "sum(buy_size-sell_size)/sum(size)",
        "window_basis": "accepted_bitunix_public_trade_prints",
        "coverage_ms": coverage_ms,
        "trade_prints": len(trades),
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "capture_manifest_sha256": capture_manifest_sha256,
        "first_trade_source_hash": trades[0]["source_hash"],
        "last_trade_source_hash": trades[-1]["source_hash"],
    }
    observed_at = int(trades[-1]["observed_at"])
    received_at = int(trades[-1]["received_at"])
    return (
        make_record(
            source_id=f"bitunix:wo105:cvd_norm:{capture_manifest_sha256}:{received_at}",
            observed_at=observed_at,
            received_at=received_at,
            schema_version="crowd-point-v1",
            payload=payload,
        ),
        [],
    )


def admit_capture_run(
    run_dir: Path,
    *,
    forward_floor_ms: int,
    expected_parser_sha256: str,
    cvd_window_ms: int = DEFAULT_CVD_WINDOW_MS,
    cvd_min_prints: int = DEFAULT_CVD_MIN_PRINTS,
) -> dict[str, Any]:
    failures: list[str] = []
    try:
        acceptance = read_object(run_dir / "TRADINGOS_INDEPENDENT_ACCEPTANCE.json")
        manifest = read_object(run_dir / "PUBLIC_CAPTURE_MANIFEST.json")
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {"run_dir": str(run_dir), "accepted": False, "failures": [f"capture_metadata_invalid:{type(exc).__name__}"]}
    failures.extend(
        verify_capture_receipts(
            run_dir,
            acceptance,
            manifest,
            expected_parser_sha256=expected_parser_sha256,
        )
    )
    started_ms = parse_iso_ms(manifest.get("started_utc"))
    if started_ms is None:
        failures.append("capture_started_at_invalid")
    elif started_ms < forward_floor_ms:
        failures.append("capture_started_before_forward_floor")
    if failures:
        return {"run_dir": str(run_dir), "accepted": False, "failures": sorted(set(failures))}

    raw_lines = jsonl_lines(run_dir / "RAW_FRAMES.jsonl")
    index_lines = jsonl_lines(run_dir / "RAW_FRAME_INDEX.jsonl")
    if len(raw_lines) != len(index_lines):
        return {
            "run_dir": str(run_dir),
            "accepted": False,
            "failures": [f"raw_index_line_count_mismatch:{len(raw_lines)}!={len(index_lines)}"],
        }

    manifest_sha = sha256_file(run_dir / "PUBLIC_CAPTURE_MANIFEST.json")
    books: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    previous_book_ms = -1
    previous_trade_ms = -1
    for line_number, (raw_line, index_line) in enumerate(zip(raw_lines, index_lines), start=1):
        try:
            index = json.loads(index_line)
            frame = json.loads(raw_line)
        except json.JSONDecodeError:
            failures.append(f"frame_decode:{line_number}")
            continue
        if not isinstance(index, dict) or not isinstance(frame, dict):
            failures.append(f"frame_not_object:{line_number}")
            continue
        if index.get("sha256") != sha256_text(raw_line):
            failures.append(f"raw_frame_hash_mismatch:{line_number}")
            continue
        if index.get("dup") is True:
            failures.append(f"duplicate_frame_not_admitted:{line_number}")
            continue
        recv_ns = index.get("recv_ns")
        venue_ms = index.get("venue_ts")
        if not isinstance(recv_ns, int) or isinstance(recv_ns, bool):
            if index.get("parse_kind") not in ("ControlAck", "Heartbeat"):
                failures.append(f"recv_ns_invalid:{line_number}")
            continue
        local_receive_ms = recv_ns // 1_000_000
        kind = index.get("parse_kind")
        if kind == "DepthUpdate":
            data = frame.get("data")
            if (
                frame.get("ch") != "depth_book15"
                or frame.get("symbol") != "BTCUSDT"
                or not isinstance(venue_ms, int)
                or not isinstance(data, dict)
            ):
                failures.append(f"depth_shape_invalid:{line_number}")
                continue
            bids = data.get("b")
            asks = data.get("a")
            if not isinstance(bids, list) or not isinstance(asks, list) or not bids or not asks:
                failures.append(f"depth_levels_missing:{line_number}")
                continue
            parsed_bids: list[list[float]] = []
            parsed_asks: list[list[float]] = []
            for label, levels, target in (("bid", bids, parsed_bids), ("ask", asks, parsed_asks)):
                for level in levels:
                    if (
                        not isinstance(level, list)
                        or len(level) != 2
                        or not finite_positive(level[0])
                        or not finite_positive(level[1])
                    ):
                        failures.append(f"depth_{label}_level_invalid:{line_number}")
                        break
                    target.append([float(level[0]), float(level[1])])
            causal_ms = _monotonic_causal_ms(venue_ms, local_receive_ms, previous_book_ms)
            previous_book_ms = causal_ms
            payload = {
                "bids": parsed_bids,
                "asks": parsed_asks,
                "venue_ts_ms": venue_ms,
                "local_receive_ms": local_receive_ms,
                "causal_available_ms": causal_ms,
                "causal_available_rule": "max(venue_ts_ms,local_receive_ms,previous_causal_ms+1)",
                "capture_manifest_sha256": manifest_sha,
                "raw_frame_sha256": index["sha256"],
            }
            books.append(
                make_record(
                    source_id=f"bitunix:wo105:book:{manifest_sha}:{line_number}",
                    observed_at=causal_ms,
                    received_at=causal_ms,
                    schema_version="public-book-v1",
                    payload=payload,
                )
            )
        elif kind == "TradeBatch":
            data = frame.get("data")
            if (
                frame.get("ch") != "trade"
                or frame.get("symbol") != "BTCUSDT"
                or not isinstance(venue_ms, int)
                or not isinstance(data, list)
                or not data
            ):
                failures.append(f"trade_shape_invalid:{line_number}")
                continue
            for print_index, item in enumerate(data):
                if (
                    not isinstance(item, dict)
                    or not finite_positive(item.get("p"))
                    or not finite_positive(item.get("v"), allow_zero=True)
                    or item.get("s") not in ("buy", "sell")
                    or parse_iso_ms(item.get("t")) is None
                ):
                    failures.append(f"trade_print_invalid:{line_number}:{print_index}")
                    continue
                causal_ms = _monotonic_causal_ms(venue_ms, local_receive_ms, previous_trade_ms)
                previous_trade_ms = causal_ms
                payload = {
                    "price": float(item["p"]),
                    "size": float(item["v"]),
                    "side": item["s"],
                    "trade_ts_iso": item["t"],
                    "venue_ts_ms": venue_ms,
                    "local_receive_ms": local_receive_ms,
                    "causal_available_ms": causal_ms,
                    "causal_available_rule": "max(venue_ts_ms,local_receive_ms,previous_causal_ms+1)",
                    "capture_manifest_sha256": manifest_sha,
                    "raw_frame_sha256": index["sha256"],
                    "batch_print_index": print_index,
                }
                trades.append(
                    make_record(
                        source_id=f"bitunix:wo105:trade:{manifest_sha}:{line_number}:{print_index}",
                        observed_at=causal_ms,
                        received_at=causal_ms,
                        schema_version="public-trade-v1",
                        payload=payload,
                    )
                )
        elif kind not in ("ControlAck", "Heartbeat"):
            failures.append(f"unadmitted_parse_kind:{line_number}:{kind}")

    if failures:
        return {"run_dir": str(run_dir), "accepted": False, "failures": sorted(set(failures))}
    cvd, cvd_failures = derive_cvd_record(
        trades,
        capture_manifest_sha256=manifest_sha,
        minimum_window_ms=cvd_window_ms,
        minimum_prints=cvd_min_prints,
    )
    return {
        "run_dir": str(run_dir),
        "accepted": True,
        "failures": [],
        "capture_manifest_sha256": manifest_sha,
        "books": books,
        "trades": trades,
        "cvd": cvd,
        "cvd_failures": cvd_failures,
        "counts": {"books": len(books), "trades": len(trades), "cvd": 1 if cvd else 0},
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_intake(
    capture_root: Path,
    *,
    forward_floor_ms: int,
    expected_parser_sha256: str,
    out_dir: Path,
) -> dict[str, Any]:
    candidates = sorted((path for path in capture_root.glob("run_*") if path.is_dir()), key=lambda path: path.name)
    runs = [
        admit_capture_run(
            path,
            forward_floor_ms=forward_floor_ms,
            expected_parser_sha256=expected_parser_sha256,
        )
        for path in candidates
    ]
    accepted = [run for run in runs if run.get("accepted")]
    books = [row for run in accepted for row in run.get("books", [])]
    trades = [row for run in accepted for row in run.get("trades", [])]
    crowd = [run["cvd"] for run in accepted if run.get("cvd")]
    books.sort(key=lambda row: (row["received_at"], row["source_id"]))
    trades.sort(key=lambda row: (row["received_at"], row["source_id"]))
    crowd.sort(key=lambda row: (row["received_at"], row["source_id"]))
    write_jsonl(out_dir / "WS_BOOKS.jsonl", books)
    write_jsonl(out_dir / "WS_TRADES.jsonl", trades)
    write_jsonl(out_dir / "CROWD_CVD.jsonl", crowd)
    decision = "bitunix_wo105_ws_intake_ready" if accepted else "bitunix_wo105_ws_intake_hold_no_post_floor_capture"
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": TOOL_PATH,
        "decision": decision,
        "forward_floor_ms": forward_floor_ms,
        "capture_root": str(capture_root.resolve()),
        "candidate_runs": len(candidates),
        "accepted_runs": len(accepted),
        "runs": [{key: value for key, value in run.items() if key not in ("books", "trades", "cvd")} for run in runs],
        "records": {"books": len(books), "trades": len(trades), "cvd": len(crowd)},
        "evaluator_packet_ready": False,
        "missing_for_packet": ["at_least_one_additional_independent_fresh_crowd_receipt_for_3_of_n_quorum"],
        "runtime_boundary": {
            "public_read_only": True,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
        "can_trade": False,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "WS_INTAKE_MANIFEST.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed WO105 adapter for independently accepted Bitunix public WS runs")
    parser.add_argument("--capture-root", default="data/forward/bitunix_wo105_ws")
    parser.add_argument("--forward-floor", default="2026-07-14T12:00:00Z")
    parser.add_argument("--policy", default="configs/BITUNIX_WO104_INDEPENDENT_ACCEPTANCE_POLICY.json")
    parser.add_argument("--out-dir", default="_dl/bitunix_wo105_ws_intake")
    args = parser.parse_args()
    floor = parse_iso_ms(args.forward_floor)
    if floor is None:
        raise SystemExit("invalid --forward-floor; expected timezone-aware ISO-8601")
    policy = read_object(resolve(args.policy))
    expected_parser = str(((policy.get("proposal") or {}).get("parser_sha256") or ""))
    report = build_intake(
        resolve(args.capture_root),
        forward_floor_ms=floor,
        expected_parser_sha256=expected_parser,
        out_dir=resolve(args.out_dir),
    )
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "accepted_runs": report["accepted_runs"],
                "records": report["records"],
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
