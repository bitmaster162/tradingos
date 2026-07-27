#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def tail_status(cache_dir: Path, symbols: list[str], interval: str, observed_at: datetime, max_age_minutes: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        path = cache_dir / "futures" / symbol / f"{interval}_klines.csv"
        last_ms = None
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for item in csv.DictReader(handle):
                        try:
                            last_ms = max(last_ms or 0, int(float(str(item.get("time_ms") or 0))))
                        except (TypeError, ValueError):
                            continue
            except OSError:
                last_ms = None
        last_open = datetime.fromtimestamp(last_ms / 1000.0, tz=timezone.utc) if last_ms else None
        age = (observed_at - last_open).total_seconds() / 60.0 if last_open else None
        rows.append(
            {
                "symbol": symbol,
                "path": portable(path),
                "exists": path.is_file(),
                "last_open_time": last_open.isoformat(timespec="seconds").replace("+00:00", "Z") if last_open else None,
                "age_minutes": round(age, 3) if age is not None else None,
                "fresh": age is not None and -5.0 <= age <= max_age_minutes,
            }
        )
    return rows


def run_filler(command: list[str], timeout_s: int) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "exit_code": None,
            "timed_out": True,
            "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else "",
        }
    return {
        "command": command,
        "exit_code": result.returncode,
        "timed_out": False,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    observed_at = now_utc()
    lock_path = resolve_path(args.prereg_lock)
    lock = read_json(lock_path)
    fixed = lock.get("fixed_study") if isinstance(lock.get("fixed_study"), dict) else {}
    symbols = [str(item).upper() for item in fixed.get("symbols", []) if str(item).strip()] if isinstance(fixed.get("symbols"), list) else []
    interval = str(fixed.get("interval") or "1h")
    cache_dir = resolve_path(args.cache_dir)
    state_path = resolve_path(args.state_path)
    filler_prefix = resolve_path(args.filler_out_prefix)
    state = read_json(state_path)
    last_success = parse_ts(state.get("last_success_at"))
    minutes_since_success = (observed_at - last_success).total_seconds() / 60.0 if last_success else None
    should_run = bool(args.force or minutes_since_success is None or minutes_since_success >= args.min_run_interval_minutes)
    run = None
    if should_run and symbols:
        command = [
            sys.executable,
            str(ROOT / "tools" / "binance_rest_kline_tail_gap_filler.py"),
            "--market",
            "futures",
            "--symbols",
            ",".join(symbols),
            "--interval",
            interval,
            "--cache-dir",
            portable(cache_dir),
            "--no-backup",
            "--out-prefix",
            portable(filler_prefix),
        ]
        run = run_filler(command, args.timeout_seconds)
    filler_report = read_json(filler_prefix.with_suffix(".json"))
    tails = tail_status(cache_dir, symbols, interval, now_utc(), args.max_tail_age_minutes)
    stale = [item["symbol"] for item in tails if not item["fresh"]]
    filler_errors = [
        f"{item.get('symbol')}:{error}"
        for item in filler_report.get("results", [])
        if isinstance(item, dict)
        for error in item.get("errors", [])
    ]
    run_failed = run is not None and run.get("exit_code") != 0
    if not symbols:
        decision = "force_order_major_cache_refresh_blocked_lock_symbols"
    elif run_failed or filler_errors or stale:
        decision = "force_order_major_cache_refresh_blocked"
    elif run is not None:
        decision = "force_order_major_cache_refreshed"
    else:
        decision = "force_order_major_cache_refresh_throttled_healthy"
    success = decision in {"force_order_major_cache_refreshed", "force_order_major_cache_refresh_throttled_healthy"}
    state.update(
        {
            "last_checked_at": now_iso(),
            "last_attempt_at": now_iso() if run is not None else state.get("last_attempt_at"),
            "last_success_at": now_iso() if success and run is not None else state.get("last_success_at"),
            "last_decision": decision,
            "symbols": symbols,
            "can_trade": False,
        }
    )
    write_json(state_path, state)
    return {
        "generated_at": now_iso(),
        "tool": "tools/liquidation_force_order_major_cache_refresh.py",
        "decision": decision,
        "can_trade": False,
        "orders_allowed": False,
        "inputs": {
            "symbols": symbols,
            "interval": interval,
            "cache_dir": portable(cache_dir),
            "min_run_interval_minutes": args.min_run_interval_minutes,
            "max_tail_age_minutes": args.max_tail_age_minutes,
            "backup_enabled": False,
        },
        "run_attempted": run is not None,
        "run": run,
        "filler_report": portable(filler_prefix.with_suffix(".json")),
        "filler_decision": filler_report.get("decision"),
        "filler_errors": filler_errors,
        "tails": tails,
        "stale_symbols": stale,
        "state_path": portable(state_path),
        "state": state,
        "boundary": {
            "public_market_data_only": True,
            "refreshes_cache_only": True,
            "uses_private_credentials": False,
            "account_endpoints": False,
            "sends_orders": False,
            "can_trade": False,
        },
        "next_action": "continue throttled cache refresh" if success else "repair public kline refresh before matched-bar research",
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ForceOrder Major Futures Cache Refresh",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Run attempted: `{str(report['run_attempted']).lower()}`",
        f"- Filler decision: `{report['filler_decision']}`",
        f"- Stale symbols: `{report['stale_symbols']}`",
        "- Public market data only; `can_trade=false`.",
        "",
        "| Symbol | Last open | Age min | Fresh |",
        "|---|---|---:|---:|",
    ]
    for item in report["tails"]:
        lines.append(f"| `{item['symbol']}` | `{item['last_open_time']}` | `{item['age_minutes']}` | `{item['fresh']}` |")
    lines.extend(["", "## Next Action", "", f"- {report['next_action']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Throttled public 1h futures-cache refresh for locked forceOrder research symbols")
    parser.add_argument("--prereg-lock", default="configs/BINANCE_FORCE_ORDER_EVENT_STUDY_PREREG_2026-07-12.json")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--state-path", default="logs/liquidation_force_order/major_cache_refresh_state.json")
    parser.add_argument("--filler-out-prefix", default="docs/BINANCE_REST_KLINE_TAIL_GAP_FILLER_FORCE_ORDER_2026-07-12")
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_FORCE_ORDER_MAJOR_CACHE_REFRESH_2026-07-12")
    parser.add_argument("--min-run-interval-minutes", type=float, default=30.0)
    parser.add_argument("--max-tail-age-minutes", type=float, default=130.0)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    report = build_report(args)
    out = resolve_path(args.out_prefix)
    write_json(out.with_suffix(".json"), report)
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "run_attempted": report["run_attempted"],
                "stale_symbols": report["stale_symbols"],
                "tails": {item["symbol"]: item["last_open_time"] for item in report["tails"]},
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not report["stale_symbols"] and not report["filler_errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
