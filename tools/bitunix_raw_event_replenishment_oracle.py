#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = "configs/BITUNIX_RAW_EVENT_REPLENISHMENT_PREREG_2026-07-16.json"
ACCEPTED_CAPTURE_DECISION = "bitunix_wo104_public_contract_confirmed_shadow_hold"
ORACLE_PATH = "tools/bitunix_raw_event_replenishment_oracle.py"


@dataclass(frozen=True)
class Book:
    recv_ms: int
    bid: float
    ask: float
    bid_depth: float
    ask_depth: float
    line_number: int
    source_hash: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class Trade:
    recv_ms: int
    price: float
    quantity: float
    side: str
    line_number: int
    source_hash: str

    @property
    def notional(self) -> float:
        return self.price * self.quantity


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


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


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finite_number(value: Any, *, positive: bool = False, allow_zero: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if positive and (number < 0 if allow_zero else number <= 0):
        return None
    return number


def validate_config(config: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    created = parse_iso_ms(config.get("created_at_utc"))
    floor = parse_iso_ms(config.get("forward_floor_utc"))
    if created is None or floor is None or created >= floor:
        failures.append("prereg_clock_contract_invalid")
    boundary = config.get("runtime_boundary") if isinstance(config.get("runtime_boundary"), dict) else {}
    required_false = (
        "autoload_allowed",
        "network_calls_allowed",
        "telegram_allowed",
        "signals_allowed",
        "paper_entries_allowed",
        "orders_allowed",
        "can_trade",
    )
    for field in required_false:
        if boundary.get(field) is not False:
            failures.append(f"runtime_boundary_not_false:{field}")
    if boundary.get("capital_permission") != "DENY":
        failures.append("capital_permission_not_deny")
    params = config.get("parameters") if isinstance(config.get("parameters"), dict) else {}
    horizons = params.get("horizons_ms") if isinstance(params.get("horizons_ms"), list) else []
    if params.get("primary_horizon_ms") not in horizons:
        failures.append("primary_horizon_not_in_horizons")
    if params.get("bucket_ms") != 5000:
        failures.append("unexpected_bucket_contract")
    return sorted(set(failures))


def validate_lock(lock_path: Path | None, config_path: Path) -> list[str]:
    if lock_path is None:
        return []
    try:
        lock = read_object(lock_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"lock_invalid:{type(exc).__name__}"]
    failures: list[str] = []
    bindings = lock.get("bindings") if isinstance(lock.get("bindings"), dict) else {}
    expected = {
        "prereg": (config_path, bindings.get("prereg_sha256")),
        "oracle": (Path(__file__).resolve(), bindings.get("oracle_sha256")),
    }
    for name, (path, expected_hash) in expected.items():
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            failures.append(f"lock_binding_missing:{name}")
        elif not path.is_file() or sha256_file(path) != expected_hash:
            failures.append(f"lock_binding_hash_mismatch:{name}")
    if lock.get("can_trade") is not False:
        failures.append("lock_can_trade_not_false")
    return sorted(set(failures))


def _receipt_hash(receipts: dict[str, Any], name: str) -> str | None:
    value = receipts.get(name)
    return value if isinstance(value, str) else None


def verify_capture_metadata(
    run_dir: Path,
    config: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str], dict[str, str]]:
    source = config["source_contract"]
    failures: list[str] = []
    hashes: dict[str, str] = {}
    try:
        manifest_path = run_dir / source["manifest_file"]
        acceptance_path = run_dir / source["acceptance_file"]
        manifest = read_object(manifest_path)
        acceptance = read_object(acceptance_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, None, [f"capture_metadata_invalid:{type(exc).__name__}"], hashes

    hashes["manifest_sha256"] = sha256_file(manifest_path)
    hashes["acceptance_sha256"] = sha256_file(acceptance_path)
    if manifest.get("schema") != source["capture_schema"]:
        failures.append("capture_schema_mismatch")
    if manifest.get("symbols") != [source["symbol"]]:
        failures.append("capture_symbol_scope_mismatch")
    if set(manifest.get("channels") or []) != set(source["required_channels"]):
        failures.append("capture_channel_scope_mismatch")
    if manifest.get("hold") is not False or manifest.get("terminal_hold") is not False:
        failures.append("capture_hold_not_false")
    if any(manifest.get(field) != 0 for field in ("credentials_used", "private_calls", "order_calls")):
        failures.append("capture_not_public_read_only")
    errors = manifest.get("error_taxonomy") if isinstance(manifest.get("error_taxonomy"), dict) else {}
    if any(int(errors.get(kind, 0)) != 0 for kind in ("NETWORK", "PARSER", "LOCAL", "STORAGE")):
        failures.append("capture_error_taxonomy_nonzero")
    if acceptance.get("decision") != ACCEPTED_CAPTURE_DECISION or acceptance.get("failures") not in ([], None):
        failures.append("independent_acceptance_not_pass")
    if acceptance.get("can_trade") is not False:
        failures.append("acceptance_can_trade_not_false")
    if acceptance.get("manifest_sha256") != hashes["manifest_sha256"]:
        failures.append("acceptance_manifest_hash_mismatch")

    accepted_receipts = acceptance.get("file_receipts") if isinstance(acceptance.get("file_receipts"), dict) else {}
    manifest_receipts = (
        ((manifest.get("receipts") or {}).get("streaming_output_sha256") or {})
        if isinstance(manifest.get("receipts"), dict)
        else {}
    )
    for key in ("raw_frames_file", "raw_index_file"):
        name = source[key]
        path = run_dir / name
        if not path.is_file():
            failures.append(f"capture_file_missing:{name}")
            continue
        actual = sha256_file(path)
        hashes[f"{name}_sha256"] = actual
        receipt = accepted_receipts.get(name) if isinstance(accepted_receipts.get(name), dict) else {}
        if receipt.get("actual_sha256") != actual or receipt.get("declared_sha256") != actual:
            failures.append(f"acceptance_file_hash_mismatch:{name}")
        if receipt.get("close_ok") is not True or receipt.get("fsync_ok") is not True:
            failures.append(f"capture_file_not_fsync_closed:{name}")
        if _receipt_hash(manifest_receipts, name) != actual:
            failures.append(f"manifest_file_hash_mismatch:{name}")
    return manifest, acceptance, sorted(set(failures)), hashes


def _parse_book(
    data: Any,
    recv_ms: int,
    line_number: int,
    source_hash: str,
    top_levels: int,
) -> Book | None:
    if not isinstance(data, dict):
        return None
    bids = data.get("b")
    asks = data.get("a")
    if not isinstance(bids, list) or not isinstance(asks, list) or len(bids) < top_levels or len(asks) < top_levels:
        return None

    def levels(rows: list[Any]) -> list[tuple[float, float]] | None:
        parsed: list[tuple[float, float]] = []
        for row in rows[:top_levels]:
            if not isinstance(row, list) or len(row) < 2:
                return None
            price = finite_number(row[0], positive=True)
            quantity = finite_number(row[1], positive=True, allow_zero=True)
            if price is None or quantity is None:
                return None
            parsed.append((price, quantity))
        return parsed

    parsed_bids = levels(bids)
    parsed_asks = levels(asks)
    if parsed_bids is None or parsed_asks is None:
        return None
    best_bid = max(price for price, _ in parsed_bids)
    best_ask = min(price for price, _ in parsed_asks)
    if best_bid >= best_ask:
        return None
    return Book(
        recv_ms=recv_ms,
        bid=best_bid,
        ask=best_ask,
        bid_depth=sum(quantity for _, quantity in parsed_bids),
        ask_depth=sum(quantity for _, quantity in parsed_asks),
        line_number=line_number,
        source_hash=source_hash,
    )


def parse_capture(run_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    manifest, _acceptance, failures, input_hashes = verify_capture_metadata(run_dir, config)
    if manifest is None:
        return {
            "run_dir": portable(run_dir),
            "quality_pass": False,
            "edge_eligible": False,
            "failures": failures,
            "books": [],
            "trades": [],
            "input_hashes": input_hashes,
        }

    source = config["source_contract"]
    params = config["parameters"]
    raw_path = run_dir / source["raw_frames_file"]
    index_path = run_dir / source["raw_index_file"]
    books: list[Book] = []
    trades: list[Trade] = []
    parse_counts: Counter[str] = Counter()
    recv_values: list[int] = []
    line_count = 0
    if raw_path.is_file() and index_path.is_file():
        with raw_path.open("r", encoding="utf-8-sig") as raw_handle, index_path.open(
            "r", encoding="utf-8-sig"
        ) as index_handle:
            for line_number, pair in enumerate(zip_longest(raw_handle, index_handle), start=1):
                raw_with_newline, index_with_newline = pair
                if raw_with_newline is None or index_with_newline is None:
                    failures.append("raw_index_line_count_mismatch")
                    break
                raw_line = raw_with_newline.rstrip("\r\n")
                index_line = index_with_newline.rstrip("\r\n")
                if not raw_line or not index_line:
                    failures.append(f"blank_raw_or_index_line:{line_number}")
                    continue
                line_count += 1
                try:
                    frame = json.loads(raw_line)
                    index = json.loads(index_line)
                except json.JSONDecodeError:
                    failures.append(f"frame_decode_failure:{line_number}")
                    continue
                if not isinstance(frame, dict) or not isinstance(index, dict):
                    failures.append(f"frame_not_object:{line_number}")
                    continue
                source_hash = sha256_text(raw_line)
                if index.get("sha256") != source_hash:
                    failures.append(f"raw_frame_hash_mismatch:{line_number}")
                    continue
                if index.get("dup") is True:
                    failures.append(f"duplicate_frame_rejected:{line_number}")
                    continue
                recv_ns = index.get("recv_ns")
                if isinstance(recv_ns, bool) or not isinstance(recv_ns, int) or recv_ns <= 0:
                    failures.append(f"recv_ns_invalid:{line_number}")
                    continue
                recv_ms = recv_ns // 1_000_000
                recv_values.append(recv_ms)
                kind = str(index.get("parse_kind") or "UNKNOWN")
                parse_counts[kind] += 1
                channel = frame.get("ch")
                if kind not in source["required_parse_kinds"]:
                    continue
                if frame.get("symbol") != source["symbol"] or index.get("symbol") != source["symbol"]:
                    failures.append(f"frame_symbol_mismatch:{line_number}")
                    continue
                if channel != index.get("ch"):
                    failures.append(f"frame_index_channel_mismatch:{line_number}")
                    continue
                if kind == "DepthUpdate":
                    if channel != "depth_book15":
                        failures.append(f"depth_channel_mismatch:{line_number}")
                        continue
                    book = _parse_book(
                        frame.get("data"),
                        recv_ms,
                        line_number,
                        source_hash,
                        int(params["top_book_levels"]),
                    )
                    if book is None:
                        failures.append(f"depth_book_invalid:{line_number}")
                    else:
                        books.append(book)
                elif kind == "TradeBatch":
                    if channel != "trade" or not isinstance(frame.get("data"), list):
                        failures.append(f"trade_batch_invalid:{line_number}")
                        continue
                    for row_number, row in enumerate(frame["data"], start=1):
                        if not isinstance(row, dict):
                            failures.append(f"trade_row_invalid:{line_number}:{row_number}")
                            continue
                        price = finite_number(row.get("p"), positive=True)
                        quantity = finite_number(row.get("v"), positive=True)
                        side = str(row.get("s") or "").lower()
                        if price is None or quantity is None or side not in {"buy", "sell"}:
                            failures.append(f"trade_row_invalid:{line_number}:{row_number}")
                            continue
                        trades.append(
                            Trade(
                                recv_ms=recv_ms,
                                price=price,
                                quantity=quantity,
                                side=side,
                                line_number=line_number,
                                source_hash=source_hash,
                            )
                        )

    for kind in source["required_parse_kinds"]:
        if parse_counts[kind] <= 0:
            failures.append(f"required_parse_kind_missing:{kind}")
    books.sort(key=lambda row: (row.recv_ms, row.line_number))
    trades.sort(key=lambda row: (row.recv_ms, row.line_number))
    book_gaps = [right.recv_ms - left.recv_ms for left, right in zip(books, books[1:])]
    maximum_gap = max(book_gaps, default=None)
    if maximum_gap is not None and maximum_gap > int(params["maximum_book_age_ms"]):
        failures.append(f"maximum_book_gap_exceeded:{maximum_gap}")

    floor_ms = parse_iso_ms(config["forward_floor_utc"])
    started_ms = parse_iso_ms(manifest.get("started_utc"))
    edge_eligible = floor_ms is not None and started_ms is not None and started_ms >= floor_ms
    run_id = input_hashes.get("manifest_sha256", sha256_text(str(run_dir.resolve())))[:16]
    quality = {
        "run_id": run_id,
        "run_dir": portable(run_dir),
        "manifest_started_utc": manifest.get("started_utc"),
        "manifest_ended_utc": manifest.get("ended_utc"),
        "quality_pass": not failures,
        "edge_eligible": edge_eligible and not failures,
        "pre_floor_quality_fixture": not edge_eligible,
        "failures": sorted(set(failures)),
        "line_count": line_count,
        "parse_counts": dict(sorted(parse_counts.items())),
        "book_count": len(books),
        "trade_print_count": len(trades),
        "maximum_book_gap_ms": maximum_gap,
        "receive_coverage_ms": max(recv_values) - min(recv_values) if recv_values else 0,
        "input_hashes": input_hashes,
        "books": books,
        "trades": trades,
    }
    return quality


def _book_at_or_before(books: list[Book], times: list[int], target_ms: int, max_age_ms: int) -> Book | None:
    index = bisect.bisect_right(times, target_ms) - 1
    if index < 0 or target_ms - books[index].recv_ms > max_age_ms:
        return None
    return books[index]


def _book_at_or_after(books: list[Book], times: list[int], target_ms: int, max_lag_ms: int) -> Book | None:
    index = bisect.bisect_left(times, target_ms)
    if index >= len(books) or books[index].recv_ms - target_ms > max_lag_ms:
        return None
    return books[index]


def _event_outcomes(
    direction: str,
    entry_book: Book,
    books: list[Book],
    times: list[int],
    config: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    params = config["parameters"]
    execution = params["execution"]
    outcomes: dict[str, Any] = {}
    all_resolved = True
    entry = entry_book.ask if direction == "LONG" else entry_book.bid
    for horizon in params["horizons_ms"]:
        target = entry_book.recv_ms + int(horizon)
        exit_book = _book_at_or_after(
            books,
            times,
            target,
            int(params["maximum_horizon_lookup_lag_ms"]),
        )
        if exit_book is None:
            outcomes[str(horizon)] = {"resolved": False}
            all_resolved = False
            continue
        exit_price = exit_book.bid if direction == "LONG" else exit_book.ask
        gross = ((exit_price / entry) - 1.0) * 10000.0 if direction == "LONG" else ((entry / exit_price) - 1.0) * 10000.0
        outcomes[str(horizon)] = {
            "resolved": True,
            "target_recv_ms": target,
            "exit_recv_ms": exit_book.recv_ms,
            "entry_touch_price": entry,
            "exit_touch_price": exit_price,
            "gross_bps": gross,
            "net_base_bps": gross - float(execution["base_extra_round_trip_cost_bps"]),
            "net_stress_bps": gross - float(execution["stress_extra_round_trip_cost_bps"]),
        }
    return outcomes, all_resolved


def detect_events(capture: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    if not capture.get("edge_eligible"):
        return []
    books: list[Book] = capture["books"]
    trades: list[Trade] = capture["trades"]
    params = config["parameters"]
    bucket_ms = int(params["bucket_ms"])
    buckets: dict[int, dict[str, float]] = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
    for trade in trades:
        start = (trade.recv_ms // bucket_ms) * bucket_ms
        buckets[start][trade.side] += trade.notional
    book_times = [row.recv_ms for row in books]
    events: list[dict[str, Any]] = []
    cooldown_until = -1
    for bucket_start in sorted(buckets):
        if bucket_start < cooldown_until:
            continue
        prior = [
            sum(buckets.get(bucket_start - offset * bucket_ms, {}).values())
            for offset in range(int(params["baseline_buckets"]), 0, -1)
        ]
        active = [value for value in prior if value > 0]
        if len(active) < int(params["minimum_active_baseline_buckets"]):
            continue
        baseline = statistics.median(active)
        buy = buckets[bucket_start]["buy"]
        sell = buckets[bucket_start]["sell"]
        total = buy + sell
        if baseline <= 0 or total < float(params["volume_multiple"]) * baseline:
            continue
        imbalance = (buy - sell) / total
        if abs(imbalance) < float(params["absolute_flow_imbalance_min"]):
            continue
        burst_side = "buy" if imbalance > 0 else "sell"
        direction = "SHORT" if burst_side == "buy" else "LONG"
        bucket_end = bucket_start + bucket_ms - 1
        pre_book = _book_at_or_before(books, book_times, bucket_start, int(params["maximum_book_age_ms"]))
        end_book = _book_at_or_before(books, book_times, bucket_end, int(params["maximum_book_age_ms"]))
        if pre_book is None or end_book is None:
            continue
        impact = (
            ((end_book.mid / pre_book.mid) - 1.0) * 10000.0
            if burst_side == "buy"
            else ((pre_book.mid / end_book.mid) - 1.0) * 10000.0
        )
        if impact < float(params["directional_impact_bps_min"]):
            continue
        first_book = bisect.bisect_left(book_times, bucket_start)
        after_bucket = bisect.bisect_right(book_times, bucket_end)
        burst_books = [pre_book, *books[first_book:after_bucket], end_book]
        depth_field = "ask_depth" if burst_side == "buy" else "bid_depth"
        minimum_depth = min(getattr(row, depth_field) for row in burst_books)
        if minimum_depth <= 0:
            continue
        confirmation: Book | None = None
        replenishment_ratio = 0.0
        confirmation_deadline = bucket_end + int(params["confirmation_window_ms"])
        for book in books[after_bucket:]:
            if book.recv_ms > confirmation_deadline:
                break
            ratio = getattr(book, depth_field) / minimum_depth
            recovered = book.mid <= end_book.mid if burst_side == "buy" else book.mid >= end_book.mid
            if ratio >= float(params["replenishment_ratio_min"]) and recovered:
                confirmation = book
                replenishment_ratio = ratio
                break
        if confirmation is None:
            continue
        outcomes, resolved = _event_outcomes(direction, confirmation, books, book_times, config)
        event_key = f"{capture['run_id']}|{bucket_start}|{burst_side}|{confirmation.recv_ms}"
        events.append(
            {
                "event_id": sha256_text(event_key)[:24],
                "run_id": capture["run_id"],
                "bucket_start_recv_ms": bucket_start,
                "confirmation_recv_ms": confirmation.recv_ms,
                "utc_day": datetime.fromtimestamp(confirmation.recv_ms / 1000, timezone.utc).date().isoformat(),
                "utc_4h_block": confirmation.recv_ms // (4 * 60 * 60 * 1000),
                "burst_side": burst_side,
                "direction": direction,
                "baseline_active_buckets": len(active),
                "baseline_median_notional": baseline,
                "burst_notional": total,
                "volume_multiple": total / baseline,
                "flow_imbalance": imbalance,
                "directional_impact_bps": impact,
                "minimum_same_side_top5_depth": minimum_depth,
                "confirmation_same_side_top5_depth": getattr(confirmation, depth_field),
                "replenishment_ratio": replenishment_ratio,
                "entry_touch_price": confirmation.ask if direction == "LONG" else confirmation.bid,
                "resolved": resolved,
                "outcomes": outcomes,
            }
        )
        cooldown_until = confirmation.recv_ms + int(params["cooldown_ms"])
    return events


def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in event.items()
        if key not in {"outcomes"}
    } | {
        "horizon_status": {
            horizon: bool(payload.get("resolved"))
            for horizon, payload in event["outcomes"].items()
        }
    }


def terminal_gate(events: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    gate = config["terminal_gate"]
    primary = str(config["parameters"]["primary_horizon_ms"])
    resolved = [event for event in events if event["outcomes"].get(primary, {}).get("resolved") is True]
    days = sorted({event["utc_day"] for event in resolved})
    blocks = Counter(event["utc_4h_block"] for event in resolved)
    maximum_share = max(blocks.values(), default=0) / len(resolved) if resolved else 0.0
    checks = {
        "minimum_resolved_events": len(resolved) >= int(gate["minimum_resolved_events"]),
        "minimum_distinct_utc_days": len(days) >= int(gate["minimum_distinct_utc_days"]),
        "minimum_independent_4h_blocks": len(blocks) >= int(gate["minimum_independent_4h_blocks"]),
        "maximum_single_4h_block_event_share": maximum_share <= float(gate["maximum_single_4h_block_event_share"]),
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "progress": {
            "resolved_events": len(resolved),
            "required_resolved_events": int(gate["minimum_resolved_events"]),
            "distinct_utc_days": len(days),
            "required_distinct_utc_days": int(gate["minimum_distinct_utc_days"]),
            "independent_4h_blocks": len(blocks),
            "required_independent_4h_blocks": int(gate["minimum_independent_4h_blocks"]),
            "maximum_single_4h_block_event_share": maximum_share,
            "allowed_maximum_share": float(gate["maximum_single_4h_block_event_share"]),
        },
    }


def _mean(values: Iterable[float]) -> float:
    rows = list(values)
    return statistics.fmean(rows) if rows else 0.0


def terminal_metrics(events: list[dict[str, Any]], config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    primary = str(config["parameters"]["primary_horizon_ms"])
    resolved = [event for event in events if event["outcomes"].get(primary, {}).get("resolved") is True]
    base = [float(event["outcomes"][primary]["net_base_bps"]) for event in resolved]
    stress = [float(event["outcomes"][primary]["net_stress_bps"]) for event in resolved]
    day_values: dict[str, list[float]] = defaultdict(list)
    for event, value in zip(resolved, base):
        day_values[event["utc_day"]].append(value)
    positive_days = sum(1 for values in day_values.values() if _mean(values) > 0)
    metrics = {
        "primary_horizon_ms": int(primary),
        "resolved_events": len(resolved),
        "mean_net_base_bps": _mean(base),
        "win_rate_net_base": sum(value > 0 for value in base) / len(base) if base else 0.0,
        "mean_net_stress_bps": _mean(stress),
        "positive_utc_days": positive_days,
        "utc_days": len(day_values),
    }
    rules = config["terminal_gate"]["pass_rules"]
    pass_checks = {
        "primary_mean_net_base_bps": metrics["mean_net_base_bps"] > float(rules["primary_mean_net_base_bps_gt"]),
        "primary_win_rate": metrics["win_rate_net_base"] > float(rules["primary_win_rate_gt"]),
        "primary_mean_net_stress_bps": metrics["mean_net_stress_bps"] > float(rules["primary_mean_net_stress_bps_gt"]),
        "minimum_positive_utc_days": metrics["positive_utc_days"] >= int(rules["minimum_positive_utc_days"]),
    }
    metrics["pass_checks"] = pass_checks
    decision = "TERMINAL_PASS_REQUIRES_SEPARATE_REVIEW" if all(pass_checks.values()) else "TERMINAL_FAIL_TOMBSTONE"
    return metrics, decision


def build_report(
    config_path: Path,
    run_dirs: list[Path],
    *,
    mode: str,
    lock_path: Path | None = None,
) -> dict[str, Any]:
    config = read_object(config_path)
    config_failures = validate_config(config)
    lock_failures = validate_lock(lock_path, config_path)
    captures = [parse_capture(run_dir, config) for run_dir in run_dirs]
    quality_rows = [
        {key: value for key, value in capture.items() if key not in {"books", "trades"}}
        for capture in captures
    ]
    quality_pass = not config_failures and not lock_failures and all(row["quality_pass"] for row in quality_rows)
    events: list[dict[str, Any]] = []
    if mode == "blind-forward" and quality_pass:
        for capture in captures:
            events.extend(detect_events(capture, config))
    gate = terminal_gate(events, config)
    if mode == "quality-only":
        decision = "SCHEMA_QUALITY_PASS_EDGE_NOT_EVALUATED" if quality_pass else "SCHEMA_QUALITY_FAIL"
        metrics: dict[str, Any] = {"visibility": "NOT_COMPUTED_IN_QUALITY_ONLY_MODE"}
    elif not quality_pass:
        decision = "SOURCE_INTEGRITY_FAIL_NO_EDGE_ROWS"
        metrics = {"visibility": "HIDDEN_SOURCE_INTEGRITY_FAIL"}
    elif gate["ready"]:
        metrics, decision = terminal_metrics(events, config)
    else:
        decision = "BLIND_FORWARD_WAIT_NO_INTERIM_OUTCOME_METRICS"
        metrics = {"visibility": "HIDDEN_UNTIL_TERMINAL_GATE"}
    return {
        "schema": "bitunix-raw-event-replenishment-oracle-report-v1",
        "generated_at_utc": now_iso(),
        "prereg_id": config["prereg_id"],
        "mode": mode,
        "decision": decision,
        "quality_pass": quality_pass,
        "config_failures": config_failures,
        "lock_failures": lock_failures,
        "config": portable(config_path),
        "config_sha256": sha256_file(config_path),
        "lock": portable(lock_path) if lock_path else None,
        "captures": quality_rows,
        "edge_rows_admitted": len(events),
        "events": [_safe_event(event) for event in events],
        "terminal_gate": gate,
        "outcome_metrics": metrics,
        "runtime_boundary": {
            "offline_analysis_only": True,
            "network_calls": 0,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "orders_allowed": False,
            "capital_permission": "DENY",
            "can_trade": False,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    progress = report["terminal_gate"]["progress"]
    return "\n".join(
        [
            "# Bitunix Raw Event Replenishment Oracle",
            "",
            f"- Generated: `{report['generated_at_utc']}`.",
            f"- Preregistration: `{report['prereg_id']}`.",
            f"- Mode: `{report['mode']}`.",
            f"- Decision: `{report['decision']}`.",
            f"- Source quality pass: `{report['quality_pass']}`.",
            f"- Edge rows admitted: `{report['edge_rows_admitted']}`.",
            f"- Resolved events: `{progress['resolved_events']}/{progress['required_resolved_events']}`.",
            f"- UTC days: `{progress['distinct_utc_days']}/{progress['required_distinct_utc_days']}`.",
            f"- Independent 4h blocks: `{progress['independent_4h_blocks']}/{progress['required_independent_4h_blocks']}`.",
            f"- Outcome visibility: `{report['outcome_metrics'].get('visibility', 'TERMINAL_GATE_OPEN')}`.",
            "- Pre-floor captures are quality fixtures only and contribute zero edge rows.",
            "- This tool has no network, signal, paper-entry, order, or capital permission.",
            "- `can_trade=false`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline blind oracle for a preregistered Bitunix raw-event hypothesis")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--lock", default="")
    parser.add_argument("--run-dir", action="append", required=True)
    parser.add_argument("--mode", choices=("quality-only", "blind-forward"), default="quality-only")
    parser.add_argument("--out-prefix", default="_dl/bitunix_raw_event_replenishment_v1/LAST_REPORT")
    args = parser.parse_args()

    config_path = resolve(args.config)
    lock_path = resolve(args.lock) if args.lock else None
    run_dirs = [resolve(value) for value in args.run_dir]
    report = build_report(config_path, run_dirs, mode=args.mode, lock_path=lock_path)
    out_prefix = resolve(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "quality_pass": report["quality_pass"],
                "edge_rows_admitted": report["edge_rows_admitted"],
                "outcome_visibility": report["outcome_metrics"].get("visibility", "TERMINAL_GATE_OPEN"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["quality_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
