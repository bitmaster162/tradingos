#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
OBSERVER_PATH = Path(__file__).resolve()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def parse_iso_ns(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1_000_000_000)


def iso_from_ns(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iter_jsonl(root: Path) -> Iterable[tuple[Path, int, dict[str, Any] | None]]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, start=1):
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        yield path, line_number, None
                        continue
                    yield path, line_number, payload if isinstance(payload, dict) else None
        except OSError:
            yield path, 0, None


def validate_prereg(prereg: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if prereg.get("status") != "prospective_preregistration_before_forward_floor":
        failures.append("status")
    if parse_iso_ns(prereg.get("forward_floor_at")) is None:
        failures.append("forward_floor_at")
    sources = prereg.get("sources") if isinstance(prereg.get("sources"), dict) else {}
    if not sources.get("binance") or not sources.get("bybit"):
        failures.append("sources")
    rules = prereg.get("fixed_rules") if isinstance(prereg.get("fixed_rules"), dict) else {}
    windows = rules.get("follow_windows_seconds")
    if not isinstance(windows, list) or windows != sorted(windows) or any(float(item) <= 0 for item in windows):
        failures.append("follow_windows_seconds")
    if float(rules.get("primary_window_seconds") or 0) not in [float(item) for item in windows or []]:
        failures.append("primary_window_seconds")
    boundary = prereg.get("runtime_boundary") if isinstance(prereg.get("runtime_boundary"), dict) else {}
    for key in ("paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary.{key}")
    if prereg.get("can_trade") is not False or prereg.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    return failures


def build_lock(prereg_path: Path, *, created_at: str | None = None) -> dict[str, Any]:
    prereg = read_json(prereg_path)
    failures = validate_prereg(prereg)
    if failures:
        raise ValueError("invalid preregistration: " + ",".join(failures))
    created = created_at or now_iso()
    created_ns = parse_iso_ns(created)
    floor_ns = parse_iso_ns(prereg["forward_floor_at"])
    if created_ns is None or floor_ns is None or created_ns >= floor_ns:
        raise ValueError("lock must be sealed before forward_floor_at")
    return {
        "schema_version": 1,
        "lock_id": prereg["prereg_id"],
        "status": "prospective_forward_lock_before_outcome_review",
        "created_at": created,
        "forward_start_at": prereg["forward_floor_at"],
        "preregistration": {"path": portable(prereg_path), "sha256": sha256_file(prereg_path)},
        "observer": {"path": portable(OBSERVER_PATH), "sha256": sha256_file(OBSERVER_PATH)},
        "sources": prereg["sources"],
        "shared_symbols": prereg["shared_symbols"],
        "fixed_rules": prereg["fixed_rules"],
        "terminal_gate": prereg["terminal_gate"],
        "runtime_boundary": prereg["runtime_boundary"],
        "can_trade": False,
        "orders_allowed": False,
    }


def validate_lock(lock: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if lock.get("status") != "prospective_forward_lock_before_outcome_review":
        failures.append("status")
    if parse_iso_ns(lock.get("forward_start_at")) is None:
        failures.append("forward_start_at")
    if lock.get("can_trade") is not False or lock.get("orders_allowed") is not False:
        failures.append("top_level_runtime_boundary")
    boundary = lock.get("runtime_boundary") if isinstance(lock.get("runtime_boundary"), dict) else {}
    for key in ("paper_entries_allowed", "live_entries_allowed", "orders_allowed", "uses_private_credentials", "can_trade"):
        if boundary.get(key) is not False:
            failures.append(f"runtime_boundary.{key}")
    for section in ("preregistration", "observer"):
        item = lock.get(section) if isinstance(lock.get(section), dict) else {}
        path = resolve_path(str(item.get("path") or ""))
        expected = str(item.get("sha256") or "")
        if not path.is_file() or not expected or sha256_file(path) != expected:
            failures.append(f"{section}_integrity")
    return failures


def source_event_key(venue: str, row: dict[str, Any]) -> tuple[Any, ...]:
    source_ms = row.get("trade_time_ms") if venue == "binance" else row.get("liquidation_time_ms")
    return (
        venue,
        row.get("symbol"),
        row.get("side"),
        source_ms,
        row.get("price"),
        row.get("quantity"),
    )


def load_events(
    venue: str,
    root: Path,
    *,
    floor_ns: int,
    symbols: set[str],
    required_host: str | None,
    required_schema_version: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counters: Counter[str] = Counter()
    events: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for path, line_number, row in iter_jsonl(root):
        if row is None:
            counters["invalid_json_or_io"] += 1
            continue
        try:
            received_ns = int(row.get("received_at_ns"))
        except (TypeError, ValueError):
            counters["missing_received_at_ns"] += 1
            continue
        if received_ns < floor_ns:
            counters["before_forward_floor"] += 1
            continue
        symbol = str(row.get("symbol") or "").upper()
        side = str(row.get("side") or "").upper()
        if symbol not in symbols or side not in {"BUY", "SELL"}:
            counters["outside_fixed_universe_or_side"] += 1
            continue
        if row.get("is_real_liquidation_feed") is not True:
            counters["not_real_liquidation_feed"] += 1
            continue
        if int(row.get("ingest_schema_version") or 0) != required_schema_version:
            counters["wrong_schema_version"] += 1
            continue
        host = str(row.get("collector_host") or "")
        if not host or (required_host and host != required_host):
            counters["wrong_or_missing_collector_host"] += 1
            continue
        key = source_event_key(venue, row)
        if key in seen:
            counters["duplicate_source_event"] += 1
            continue
        seen.add(key)
        events.append(
            {
                "venue": venue,
                "symbol": symbol,
                "side": side,
                "received_at_ns": received_ns,
                "received_at": iso_from_ns(received_ns),
                "notional_usd": max(0.0, float(row.get("notional_usd") or 0.0)),
                "source_path": portable(path),
                "source_line": line_number,
            }
        )
        counters["accepted"] += 1
    events.sort(key=lambda item: (item["received_at_ns"], item["symbol"], item["side"]))
    return events, dict(sorted(counters.items()))


def build_direction_observations(
    leader_events: list[dict[str, Any]],
    follower_events: list[dict[str, Any]],
    *,
    cutoff_ns: int,
    preceding_exclusion_ns: int,
    leader_cooldown_ns: int,
    windows_ns: list[int],
) -> list[dict[str, Any]]:
    followers_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in follower_events:
        followers_by_key[(event["symbol"], event["side"])].append(event)
    follower_times = {
        key: [event["received_at_ns"] for event in values] for key, values in followers_by_key.items()
    }
    used_followers: set[tuple[str, str, int]] = set()
    last_leader: dict[tuple[str, str], int] = {}
    observations: list[dict[str, Any]] = []
    max_window = max(windows_ns)

    for event in leader_events:
        leader_ns = event["received_at_ns"]
        if leader_ns > cutoff_ns:
            continue
        key = (event["symbol"], event["side"])
        if leader_ns - last_leader.get(key, -10**30) < leader_cooldown_ns:
            continue
        candidates = followers_by_key.get(key, [])
        times = follower_times.get(key, [])
        previous_index = bisect.bisect_left(times, leader_ns) - 1
        if previous_index >= 0 and times[previous_index] >= leader_ns - preceding_exclusion_ns:
            continue
        last_leader[key] = leader_ns
        next_index = bisect.bisect_right(times, leader_ns)
        follower: dict[str, Any] | None = None
        while next_index < len(candidates):
            candidate = candidates[next_index]
            delay_ns = candidate["received_at_ns"] - leader_ns
            if delay_ns > max_window:
                break
            follower_key = (candidate["symbol"], candidate["side"], candidate["received_at_ns"])
            if follower_key not in used_followers:
                follower = candidate
                used_followers.add(follower_key)
                break
            next_index += 1
        delay_ns = follower["received_at_ns"] - leader_ns if follower else None
        observations.append(
            {
                "leader_venue": event["venue"],
                "follower_venue": follower_events[0]["venue"] if follower_events else None,
                "symbol": event["symbol"],
                "side": event["side"],
                "leader_received_at_ns": leader_ns,
                "leader_received_at": event["received_at"],
                "leader_notional_usd": event["notional_usd"],
                "follower_received_at_ns": follower["received_at_ns"] if follower else None,
                "delay_ms": round(delay_ns / 1_000_000, 6) if delay_ns is not None else None,
                "followed": {str(window // 1_000_000_000): delay_ns is not None and delay_ns <= window for window in windows_ns},
            }
        )
    return observations


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    probability = successes / total
    denominator = 1.0 + z * z / total
    center = (probability + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(probability * (1.0 - probability) / total + z * z / (4.0 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def summarize_direction(observations: list[dict[str, Any]], windows_seconds: list[int]) -> dict[str, Any]:
    total = len(observations)
    symbols = Counter(row["symbol"] for row in observations)
    dates = {str(row["leader_received_at"])[:10] for row in observations}
    windows: dict[str, Any] = {}
    for seconds in windows_seconds:
        successes = sum(bool(row["followed"][str(seconds)]) for row in observations)
        lower, upper = wilson_interval(successes, total)
        windows[str(seconds)] = {
            "follow_count": successes,
            "follow_rate": round(successes / total, 8) if total else 0.0,
            "wilson_95": {"lower": round(lower, 8), "upper": round(upper, 8)},
        }
    return {
        "clean_leader_events": total,
        "utc_days": len(dates),
        "symbols": dict(sorted(symbols.items())),
        "symbol_count": len(symbols),
        "max_single_symbol_share": round(max(symbols.values()) / total, 8) if total else 0.0,
        "first_leader_at": observations[0]["leader_received_at"] if observations else None,
        "last_leader_at": observations[-1]["leader_received_at"] if observations else None,
        "windows_seconds": windows,
    }


def evaluate_terminal(
    summaries: dict[str, dict[str, Any]], gate: dict[str, Any]
) -> tuple[str, list[str], dict[str, Any]]:
    blockers: list[str] = []
    minimum_events = int(gate["minimum_clean_leaders_per_direction"])
    minimum_days = int(gate["minimum_utc_days_per_direction"])
    minimum_symbols = int(gate["minimum_symbols_per_direction"])
    maximum_share = float(gate["maximum_single_symbol_share"])
    primary = str(int(gate["primary_window_seconds"]))
    for name, summary in summaries.items():
        if summary["clean_leader_events"] < minimum_events:
            blockers.append(f"{name}_minimum_clean_leaders_not_met")
        if summary["utc_days"] < minimum_days:
            blockers.append(f"{name}_minimum_utc_days_not_met")
        if summary["symbol_count"] < minimum_symbols:
            blockers.append(f"{name}_minimum_symbols_not_met")
        if summary["max_single_symbol_share"] > maximum_share:
            blockers.append(f"{name}_single_symbol_concentration_exceeded")
    if blockers:
        return "liquidation_cross_venue_receipt_leadership_collecting_forward_sample", blockers, {}

    rates = {name: summary["windows_seconds"][primary]["follow_rate"] for name, summary in summaries.items()}
    ordered = sorted(rates, key=rates.get, reverse=True)
    candidate, reverse = ordered[0], ordered[1]
    candidate_metrics = summaries[candidate]["windows_seconds"][primary]
    reverse_metrics = summaries[reverse]["windows_seconds"][primary]
    gap = candidate_metrics["follow_rate"] - reverse_metrics["follow_rate"]
    intervals_separate = candidate_metrics["wilson_95"]["lower"] > reverse_metrics["wilson_95"]["upper"]
    evidence = {
        "candidate_direction": candidate,
        "reverse_direction": reverse,
        "primary_window_seconds": int(primary),
        "absolute_follow_rate_gap": round(gap, 8),
        "wilson_intervals_separate": intervals_separate,
    }
    if gap >= float(gate["minimum_absolute_follow_rate_gap"]) and intervals_separate:
        return (
            "liquidation_cross_venue_receipt_leadership_candidate_for_manual_price_impact_preregistration",
            [],
            evidence,
        )
    return "liquidation_cross_venue_receipt_leadership_no_stable_leader_tombstone", [], evidence


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Liquidation Cross-Venue Receipt Leadership Forward Observer",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Forward floor: `{report['lock']['forward_start_at']}`",
        f"- Terminal frozen: `{report['terminal']['frozen']}`",
        "- Can trade: `false`",
        "",
        "| Direction | Clean leaders | UTC days | Symbols | Max symbol share | 1s | 5s | 15s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, summary in report["directions"].items():
        windows = summary["windows_seconds"]
        lines.append(
            f"| `{name}` | `{summary['clean_leader_events']}` | `{summary['utc_days']}` | "
            f"`{summary['symbol_count']}` | `{summary['max_single_symbol_share']}` | "
            f"`{windows.get('1', {}).get('follow_rate')}` | `{windows.get('5', {}).get('follow_rate')}` | "
            f"`{windows.get('15', {}).get('follow_rate')}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This observer measures event-to-event receipt leadership only; it does not inspect subsequent price returns.",
            "- A positive terminal result permits only a new immutable price-impact preregistration.",
            "- No signal, paper order, live order, credential, or automatic promotion is allowed.",
            "",
            "## Blockers",
            "",
            *[f"- `{item}`" for item in report["blockers"]],
            "",
            "## Next Action",
            "",
            f"- {report['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def run_observer(lock_path: Path, out_prefix: Path, terminal_receipt_path: Path) -> dict[str, Any]:
    lock = read_json(lock_path)
    failures = validate_lock(lock)
    if failures:
        raise ValueError("invalid forward lock: " + ",".join(failures))
    existing_terminal = read_json(terminal_receipt_path)
    if existing_terminal.get("lock_id") == lock.get("lock_id") and existing_terminal.get("terminal") is True:
        frozen = dict(existing_terminal["report"])
        frozen["generated_at"] = now_iso()
        frozen["terminal"]["frozen"] = True
        write_json(out_prefix.with_suffix(".json"), frozen)
        out_prefix.with_suffix(".md").write_text(render_markdown(frozen), encoding="utf-8")
        return frozen

    floor_ns = parse_iso_ns(lock["forward_start_at"])
    assert floor_ns is not None
    current_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    rules = lock["fixed_rules"]
    windows_seconds = [int(value) for value in rules["follow_windows_seconds"]]
    windows_ns = [value * 1_000_000_000 for value in windows_seconds]
    sources = lock["sources"]
    symbols = {str(value).upper() for value in lock["shared_symbols"]}
    required_host = str(rules.get("required_collector_host") or "") or None
    schema_version = int(rules["required_ingest_schema_version"])
    binance, binance_counters = load_events(
        "binance", resolve_path(sources["binance"]), floor_ns=floor_ns, symbols=symbols,
        required_host=required_host, required_schema_version=schema_version,
    )
    bybit, bybit_counters = load_events(
        "bybit", resolve_path(sources["bybit"]), floor_ns=floor_ns, symbols=symbols,
        required_host=required_host, required_schema_version=schema_version,
    )
    latest_common = min(
        max((row["received_at_ns"] for row in binance), default=current_ns),
        max((row["received_at_ns"] for row in bybit), default=current_ns),
        current_ns,
    )
    cutoff_ns = latest_common - max(windows_ns)
    kwargs = {
        "cutoff_ns": cutoff_ns,
        "preceding_exclusion_ns": int(rules["preceding_exclusion_seconds"]) * 1_000_000_000,
        "leader_cooldown_ns": int(rules["leader_cooldown_seconds"]) * 1_000_000_000,
        "windows_ns": windows_ns,
    }
    b_to_y = build_direction_observations(binance, bybit, **kwargs)
    y_to_b = build_direction_observations(bybit, binance, **kwargs)
    summaries = {
        "binance_leads_bybit": summarize_direction(b_to_y, windows_seconds),
        "bybit_leads_binance": summarize_direction(y_to_b, windows_seconds),
    }
    if current_ns < floor_ns:
        decision = "liquidation_cross_venue_receipt_leadership_waiting_forward_floor"
        blockers = ["forward_floor_not_reached"]
        evidence: dict[str, Any] = {}
    else:
        decision, blockers, evidence = evaluate_terminal(summaries, lock["terminal_gate"])
    terminal = decision in {
        "liquidation_cross_venue_receipt_leadership_candidate_for_manual_price_impact_preregistration",
        "liquidation_cross_venue_receipt_leadership_no_stable_leader_tombstone",
    }
    next_action = (
        "keep collectors and this observer running without parameter changes"
        if not terminal
        else (
            "manually preregister a separate forward-only price-impact test with a new future floor"
            if decision.endswith("manual_price_impact_preregistration")
            else "tombstone this family; do not reverse, retune, or recycle it"
        )
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_cross_venue_lead_lag_forward_observer.py",
        "decision": decision,
        "can_trade": False,
        "automatic_promotion_allowed": False,
        "lock": {
            "path": portable(lock_path),
            "sha256": sha256_file(lock_path),
            "lock_id": lock["lock_id"],
            "status": lock["status"],
            "forward_start_at": lock["forward_start_at"],
        },
        "source_counters": {"binance": binance_counters, "bybit": bybit_counters},
        "evaluation_cutoff": iso_from_ns(cutoff_ns),
        "directions": summaries,
        "terminal_evidence": evidence,
        "terminal": {"reached": terminal, "frozen": False, "receipt": portable(terminal_receipt_path)},
        "blockers": sorted(set(blockers)),
        "next_action": next_action,
        "runtime_boundary": {
            "observer_only": True,
            "price_outcomes_read": False,
            "signals_allowed": False,
            "paper_entries_allowed": False,
            "live_entries_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    if terminal:
        write_json(
            terminal_receipt_path,
            {"lock_id": lock["lock_id"], "terminal": True, "sealed_at": now_iso(), "report": report},
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Immutable forward-only Binance/Bybit liquidation receipt-leadership observer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seal = subparsers.add_parser("seal-lock")
    seal.add_argument("--prereg", default="configs/LIQUIDATION_CROSS_VENUE_LEAD_LAG_PREREG_2026-07-13.json")
    seal.add_argument("--lock", default="configs/LIQUIDATION_CROSS_VENUE_LEAD_LAG_LOCK_2026-07-13.json")
    seal.add_argument("--acknowledge-forward-only", action="store_true")
    run = subparsers.add_parser("run-once")
    run.add_argument("--lock", default="configs/LIQUIDATION_CROSS_VENUE_LEAD_LAG_LOCK_2026-07-13.json")
    run.add_argument("--out-prefix", default="docs/LIQUIDATION_CROSS_VENUE_LEAD_LAG_FORWARD_OBSERVER_2026-07-13")
    run.add_argument("--terminal-receipt", default="logs/liquidation_cross_venue_lead_lag/terminal_receipt.json")
    args = parser.parse_args()
    try:
        if args.command == "seal-lock":
            if not args.acknowledge_forward_only:
                raise ValueError("--acknowledge-forward-only is required")
            prereg_path = resolve_path(args.prereg)
            lock_path = resolve_path(args.lock)
            lock = build_lock(prereg_path)
            write_json(lock_path, lock)
            print(json.dumps({"decision": "forward_lock_sealed", "lock": portable(lock_path), "can_trade": False}, indent=2))
            return 0
        report = run_observer(resolve_path(args.lock), resolve_path(args.out_prefix), resolve_path(args.terminal_receipt))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"decision": "liquidation_cross_venue_receipt_leadership_observer_error", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"decision": report["decision"], "directions": {key: value["clean_leader_events"] for key, value in report["directions"].items()}, "terminal": report["terminal"]["reached"], "can_trade": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
