#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import INTERVAL_MS, fetch_binance_klines, write_ohlcv_csv  # noqa: E402
from tools.strategy_mix_combo_tester import generate_signals, load_interval_data  # noqa: E402
from tools.strategy_mix_deep_validator import signal_config  # noqa: E402
from tools.strategy_mix_holdout_validator import ReplayConfig, result_to_config  # noqa: E402
from tools.strategy_mix_paper_replay import build_position, expected_interval_seconds, journal_event, select_candidates  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_package_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def render_signal_card(card: dict[str, Any]) -> str:
    status = card.get("status")
    latest_event = card.get("latest_event") if isinstance(card.get("latest_event"), dict) else {}
    lines = [
        "# Forward Paper Signal Card",
        "",
        f"Generated: `{card.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Paper-only forward observation.",
        "- Public market data only.",
        "- No private credentials and no exchange orders.",
        "",
        "## Current State",
        "",
        f"- Status: `{status}`.",
        f"- Strategy: `{card.get('strategy_id')}`.",
        f"- Symbol / TF: `{card.get('symbol')}` / `{card.get('interval')}`.",
        f"- Latest closed bar: `{card.get('latest_closed_bar_ts')}` close `{card.get('latest_closed_close')}`.",
        f"- Signals on latest bar: `{card.get('signals_on_latest_bar')}`.",
        f"- Conditions: `{', '.join(card.get('conditions') or [])}`.",
        "",
    ]
    if status == "paper_entry_intent":
        lines.extend(
            [
                "## Paper Entry Plan",
                "",
                f"- Side: `{latest_event.get('side')}`.",
                f"- Signal bar: `{latest_event.get('signal_bar_ts')}`.",
                f"- Entry bar: `{latest_event.get('entry_bar_ts')}`.",
                f"- Entry: `{latest_event.get('entry')}`.",
                f"- Stop: `{latest_event.get('stop')}`.",
                f"- Take: `{latest_event.get('take')}`.",
                f"- ATR: `{latest_event.get('atr')}`.",
                f"- Max hold bars: `{latest_event.get('max_hold_bars')}`.",
                f"- Sends orders: `{latest_event.get('sends_orders')}`.",
                "",
                "## Operator Rule",
                "",
                "- Treat this as a paper card only. Do not manually copy it into live execution without a separate execution review.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Operator Rule",
                "",
                "- No paper entry is active from the latest checked closed candle.",
                "- Wait for the next scheduled 4H closed-bar check.",
                "",
            ]
        )
    return "\n".join(lines)


def write_latest_signal_card(args: argparse.Namespace, card: dict[str, Any]) -> dict[str, str]:
    json_path = resolve_package_path(args.signal_card_json_path)
    md_path = resolve_package_path(args.signal_card_md_path)
    write_json(json_path, card)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_signal_card(card), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def closed_rows(rows: list[dict[str, str]], interval: str) -> tuple[list[dict[str, str]], dict[str, str] | None]:
    interval_ms = INTERVAL_MS.get(interval)
    if not interval_ms:
        raise ValueError(f"unsupported_interval:{interval}")
    current_ms = now_ms()
    closed: list[dict[str, str]] = []
    current_open: dict[str, str] | None = None
    for row in rows:
        open_ms = int(float(row.get("time_ms") or 0))
        if open_ms + interval_ms <= current_ms:
            closed.append(row)
        else:
            current_open = row
    return closed, current_open


def prepare_feed_cache(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = Path(args.feed_cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    interval = args.interval
    symbol = args.symbol.upper()
    fetched_futures = fetch_binance_klines(symbol, interval, args.limit, "futures", pages=1)
    fetched_spot = fetch_binance_klines(symbol, interval, args.limit, "spot", pages=1) if args.with_spot else []
    futures_closed, futures_current = closed_rows(fetched_futures, interval)
    spot_closed, spot_current = closed_rows(fetched_spot, interval) if fetched_spot else ([], None)
    if len(futures_closed) < args.min_closed_bars:
        raise ValueError(f"insufficient_closed_futures_bars:{len(futures_closed)}")
    futures_path = cache_dir / "futures" / symbol / f"{interval}_klines.csv"
    spot_path = cache_dir / "spot" / symbol / f"{interval}_klines.csv"
    write_ohlcv_csv(futures_path, futures_closed)
    if spot_closed:
        write_ohlcv_csv(spot_path, spot_closed)

    # Copy cached derivatives when available because the current locked 4H
    # guarded-breakout candidate uses OI/funding guard conditions.
    source_derivatives = ROOT / args.derivatives_cache_dir / "futures" / symbol / f"{interval}_oi_aligned.csv"
    target_derivatives = cache_dir / "futures" / symbol / f"{interval}_oi_aligned.csv"
    if source_derivatives.exists() and args.copy_cached_derivatives:
        target_derivatives.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_derivatives, target_derivatives)
    return {
        "cache_dir": str(cache_dir),
        "futures_path": str(futures_path),
        "spot_path": str(spot_path) if spot_closed else None,
        "closed_futures_rows": len(futures_closed),
        "closed_spot_rows": len(spot_closed),
        "latest_closed": futures_closed[-1],
        "current_open": futures_current,
        "spot_current_open": spot_current,
        "derivatives_copied": target_derivatives.exists(),
    }


def candidate_from_source(path: Path, verdicts: set[str], top: int) -> ReplayConfig:
    source = json.loads(path.read_text(encoding="utf-8-sig"))
    candidates = select_candidates(source, verdicts, top)
    if not candidates:
        raise ValueError("no_forward_candidate_found")
    return result_to_config(candidates[0])


def load_state(path: Path) -> dict[str, Any]:
    state = read_json(path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("emitted_signal_keys", [])
    state.setdefault("last_run_at", None)
    return state


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    source_path = Path(args.source_report)
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    verdicts = {item.strip() for item in args.candidate_verdicts.split(",") if item.strip()}
    config = candidate_from_source(source_path, verdicts, args.top)
    if config.interval != args.interval:
        raise ValueError(f"candidate_interval_mismatch:{config.interval}!={args.interval}")
    feed = prepare_feed_cache(args)
    bars, features, matrix = load_interval_data(Path(feed["cache_dir"]), config.interval, oi_lag=args.oi_lag, spot_perp_lookback=args.spot_perp_lookback)
    signals = generate_signals(signal_config(config), bars, features, matrix)
    latest_index = len(bars) - 1
    latest_bar = bars[latest_index]
    latest_signals = [signal for signal in signals if int(signal["bar_index"]) == latest_index]
    state_path = Path(args.state_path)
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    journal_path = Path(args.journal_path)
    if not journal_path.is_absolute():
        journal_path = ROOT / journal_path
    state = load_state(state_path)
    emitted_keys = set(str(item) for item in state.get("emitted_signal_keys", []))
    events: list[dict[str, Any]] = []
    result_status = "no_signal"
    current_open = feed.get("current_open")
    for signal in latest_signals:
        signal_key = f"{config.strategy_id}|{latest_bar.ts}"
        events.append(
            journal_event(
                "forward_signal",
                strategy_id=config.strategy_id,
                symbol=args.symbol.upper(),
                interval=config.interval,
                bar_ts=latest_bar.ts,
                bar_index=latest_index,
                side=config.side,
                conditions=list(config.conditions),
                close=round(float(latest_bar.close), 8),
                atr=round(float(signal["atr"]), 8),
                feature_snapshot=signal.get("feature_snapshot", {}),
                signal_key=signal_key,
            )
        )
        if signal_key in emitted_keys:
            result_status = "duplicate_signal"
            events.append(journal_event("forward_signal_skipped", strategy_id=config.strategy_id, bar_ts=latest_bar.ts, reason="duplicate_signal", signal_key=signal_key))
            continue
        if current_open is None:
            result_status = "signal_entry_pending_next_bar"
            events.append(
                journal_event(
                    "forward_entry_pending",
                    strategy_id=config.strategy_id,
                    bar_ts=latest_bar.ts,
                    reason="no_current_open_bar_from_rest_yet",
                    signal_key=signal_key,
                )
            )
        else:
            position = build_position(config, [*bars, type("Bar", (), {
                "index": len(bars),
                "ts": current_open.get("time"),
                "open": float(current_open.get("open")),
                "high": float(current_open.get("high")),
                "low": float(current_open.get("low")),
                "close": float(current_open.get("close")),
                "volume": float(current_open.get("volume")),
            })()], signal)
            result_status = "paper_entry_intent"
            events.append(
                journal_event(
                    "forward_paper_entry_intent",
                    strategy_id=config.strategy_id,
                    signal_key=signal_key,
                    signal_bar_ts=latest_bar.ts,
                    entry_bar_ts=current_open.get("time"),
                    side=config.side,
                    entry=round(position.entry, 8) if position else None,
                    stop=round(position.stop, 8) if position else None,
                    take=round(position.take, 8) if position else None,
                    atr=round(position.atr, 8) if position else None,
                    stop_atr=config.stop_atr,
                    take_atr=config.take_atr,
                    max_hold_bars=config.max_hold_bars,
                    sends_orders=False,
                )
            )
        emitted_keys.add(signal_key)
    if not latest_signals:
        events.append(
            journal_event(
                "forward_no_signal",
                strategy_id=config.strategy_id,
                symbol=args.symbol.upper(),
                interval=config.interval,
                latest_closed_bar_ts=latest_bar.ts,
                latest_closed_close=round(float(latest_bar.close), 8),
                checked_conditions=list(config.conditions),
            )
        )
    state["emitted_signal_keys"] = sorted(emitted_keys)[-args.max_state_keys :]
    state["last_run_at"] = now_iso()
    state["last_status"] = result_status
    state["last_closed_bar_ts"] = latest_bar.ts
    state["strategy_id"] = config.strategy_id
    write_json(state_path, state)
    append_jsonl(journal_path, events)
    latest_event = next((event for event in reversed(events) if event.get("event_type") == "forward_paper_entry_intent"), None)
    if latest_event is None:
        latest_event = events[-1] if events else {}
    signal_card = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "forward_paper_card_public_data_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "status": result_status,
        "strategy_id": config.strategy_id,
        "symbol": args.symbol.upper(),
        "interval": config.interval,
        "side": config.side,
        "conditions": list(config.conditions),
        "latest_closed_bar_ts": latest_bar.ts,
        "latest_closed_close": round(float(latest_bar.close), 8),
        "signals_on_latest_bar": len(latest_signals),
        "latest_event": latest_event,
        "state_path": str(state_path),
        "journal_path": str(journal_path),
        "can_trade": False,
        "decision": "forward_paper_only_no_orders",
    }
    signal_card_paths = write_latest_signal_card(args, signal_card)
    return {
        "status": result_status,
        "events_written": len(events),
        "strategy_id": config.strategy_id,
        "symbol": args.symbol.upper(),
        "interval": config.interval,
        "latest_closed_bar_ts": latest_bar.ts,
        "latest_closed_close": round(float(latest_bar.close), 8),
        "signals_on_latest_bar": len(latest_signals),
        "feed": feed,
        "state_path": str(state_path),
        "journal_path": str(journal_path),
        "signal_card_paths": signal_card_paths,
        "events": events,
    }


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest_result", {})
    lines = [
        "# Strategy Mix Forward Paper Feed",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Public Binance market data only.",
        "- No private credentials, no exchange account, no orders.",
        "- This writes forward paper signals/intents to JSONL.",
        "",
        "## Latest Run",
        "",
        f"- Status: `{latest.get('status')}`.",
        f"- Strategy: `{latest.get('strategy_id')}`.",
        f"- Symbol / TF: `{latest.get('symbol')}` / `{latest.get('interval')}`.",
        f"- Latest closed bar: `{latest.get('latest_closed_bar_ts')}` close `{latest.get('latest_closed_close')}`.",
        f"- Signals on latest bar: `{latest.get('signals_on_latest_bar')}`.",
        f"- Journal: `{report['journal_path']}`.",
        f"- State: `{report['state_path']}`.",
        f"- Latest card JSON: `{latest.get('signal_card_paths', {}).get('json')}`.",
        f"- Latest card MD: `{latest.get('signal_card_paths', {}).get('md')}`.",
        "",
        "## Meaning",
        "",
        "- `forward_no_signal`: rules did not match on the latest closed 4H candle.",
        "- `forward_signal`: rules matched on the latest closed 4H candle.",
        "- `forward_paper_entry_intent`: a paper-only next-bar entry plan was written.",
        "- `forward_signal_skipped`: duplicate or blocked forward signal.",
        "",
        "## Source",
        "",
        "- Binance USD-M Futures klines are fetched from the official public `GET /fapi/v1/klines` endpoint.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Forward paper feed for locked strategy mix candidate")
    parser.add_argument("--source-report", default="docs/STRATEGY_MIX_FORWARD_LOCKED_CANDIDATE_2026-06-29_4H_GUARDED_SHORT.json")
    parser.add_argument("--candidate-verdicts", default="paper_replay_candidate_locked")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--limit", type=int, default=320)
    parser.add_argument("--min-closed-bars", type=int, default=260)
    parser.add_argument("--top", type=int, default=1)
    parser.add_argument("--feed-cache-dir", default="_dl/forward_paper_feed/cache")
    parser.add_argument("--derivatives-cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--copy-cached-derivatives", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--with-spot", action="store_true")
    parser.add_argument("--oi-lag", type=int, default=12)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/strategy_mix_forward_paper_feed.jsonl")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/strategy_mix_forward_paper_feed_state.json")
    parser.add_argument("--signal-card-json-path", default="logs/forward_paper_feed/latest_signal_card.json")
    parser.add_argument("--signal-card-md-path", default="logs/forward_paper_feed/latest_signal_card.md")
    parser.add_argument("--max-state-keys", type=int, default=500)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--sleep-seconds", type=int, default=300)
    parser.add_argument("--max-cycles", type=int, default=1)
    parser.add_argument("--out-prefix", default="docs/STRATEGY_MIX_FORWARD_PAPER_FEED_2026-06-08")
    args = parser.parse_args()

    results = []
    cycles = args.max_cycles if args.loop else 1
    for cycle in range(max(1, cycles)):
        results.append(run_once(args))
        if args.loop and cycle < cycles - 1:
            time.sleep(max(1, args.sleep_seconds))

    out_prefix = Path(args.out_prefix)
    if not out_prefix.is_absolute():
        out_prefix = ROOT / out_prefix
    journal_path = Path(args.journal_path)
    if not journal_path.is_absolute():
        journal_path = ROOT / journal_path
    state_path = Path(args.state_path)
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "forward_paper_feed_public_data_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "exchange_connection": "public_market_data_only",
        },
        "source_report": args.source_report,
        "cycles": len(results),
        "latest_result": {key: value for key, value in results[-1].items() if key != "events"},
        "results": [{key: value for key, value in item.items() if key != "events"} for item in results],
        "journal_path": str(journal_path),
        "state_path": str(state_path),
        "decision": "forward_paper_only_no_orders",
        "next_action": "review_forward_journal_then_add_scheduler_or_alert_surface",
        "can_trade": False,
    }
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "journal": str(journal_path),
                "state": str(state_path),
                "signal_card": report["latest_result"].get("signal_card_paths"),
                "latest_status": report["latest_result"].get("status"),
                "signals_on_latest_bar": report["latest_result"].get("signals_on_latest_bar"),
                "decision": report["decision"],
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
