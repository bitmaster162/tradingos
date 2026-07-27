#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.derivatives_event_edge_miner import (  # noqa: E402
    EventConfig,
    build_features,
    join_rows,
    read_csv,
    safe_float,
    signal_matches,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"emitted_signal_keys": []}
    try:
        payload = read_json(path)
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("emitted_signal_keys", [])
    return payload


def selected_config(report: dict[str, Any]) -> EventConfig | None:
    selected = report.get("selected")
    if not isinstance(selected, dict):
        return None
    config = selected.get("config")
    if not isinstance(config, dict):
        return None
    return EventConfig(
        strategy_id=str(config["strategy_id"]),
        family=str(config["family"]),
        side=str(config["side"]),
        interval=str(config["interval"]),
        lookback=int(config["lookback"]),
        price_atr=float(config["price_atr"]),
        oi_pct=float(config["oi_pct"]),
        funding_abs=float(config["funding_abs"]),
        volume_z=float(config["volume_z"]),
        close_location=float(config["close_location"]),
        regime_filter=str(config["regime_filter"]),
        stop_atr=float(config["stop_atr"]),
        take_atr=float(config["take_atr"]),
        max_hold_bars=int(config["max_hold_bars"]),
    )


def data_paths(report: dict[str, Any], config: EventConfig) -> tuple[Path, Path] | None:
    for row in report.get("data", []):
        if not isinstance(row, dict) or row.get("interval") != config.interval:
            continue
        klines = row.get("klines_path")
        derivatives = row.get("derivatives_path")
        if isinstance(klines, str) and isinstance(derivatives, str):
            return resolve_path(klines), resolve_path(derivatives)
    return None


def forward_feature(rows: list[dict[str, Any]], config: EventConfig) -> tuple[int | None, dict[str, float] | None]:
    if not rows:
        return None, None
    latest_index = len(rows) - 1
    # The miner omits the last row because backtest simulation needs a next open.
    # Forward observation wants the latest closed bar, so append a dummy row to expose that feature.
    extended = rows + [dict(rows[-1])]
    features = build_features(extended, lookbacks=(config.lookback,))
    feature = features.get(latest_index, {}).get(config.lookback)
    return latest_index, feature


def round_float(value: Any, digits: int = 6) -> float | None:
    parsed = safe_float(value)
    return round(float(parsed), digits) if parsed is not None else None


def signal_key(config: EventConfig, bar_ts: str) -> str:
    return f"{config.strategy_id}|{bar_ts}"


def render_card(report: dict[str, Any]) -> str:
    latest = report.get("latest_observation") if isinstance(report.get("latest_observation"), dict) else {}
    config = report.get("selected_config") if isinstance(report.get("selected_config"), dict) else {}
    return "\n".join(
        [
            "# Derivatives Event Forward Observer",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Observer-only latest-bar check.",
            "- No paper entry intents, no orders, no private credentials.",
            "- Any signal is evidence collection only.",
            "",
            "## Candidate",
            "",
            f"- Strategy: `{config.get('strategy_id')}`.",
            f"- Family / side / TF: `{config.get('family')}` / `{config.get('side')}` / `{config.get('interval')}`.",
            f"- Regime: `{config.get('regime_filter')}`.",
            f"- RR / hold: `1:{config.get('take_atr')}` / `{config.get('max_hold_bars')}` bars.",
            "",
            "## Latest Observation",
            "",
            f"- Status: `{latest.get('status')}`.",
            f"- Bar: `{latest.get('bar_ts')}` close `{latest.get('close')}`.",
            f"- Signal: `{latest.get('signal')}`.",
            f"- Price move ATR: `{latest.get('price_move_atr')}`.",
            f"- OI delta pct: `{latest.get('oi_delta_pct')}`.",
            f"- Funding: `{latest.get('funding')}`.",
            f"- Close location: `{latest.get('close_location')}`.",
            "",
            "## Decision",
            "",
            f"- `{report.get('decision')}`.",
            f"- Next: `{report.get('next_action')}`.",
            "",
        ]
    )


def build_blocked_report(reason: str, args: argparse.Namespace, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "observer_id": "derivatives_event_forward_observer",
        "decision": reason,
        "latest_observation": {"status": reason, "signal": False},
        "runtime_boundary": {
            "observer_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "journal_path": rel_path(resolve_path(args.journal_path)),
        "state_path": rel_path(resolve_path(args.state_path)),
        "next_action": "fix observer input before collecting forward evidence",
        "can_trade": False,
    }
    if extra:
        report.update(extra)
    return report


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    miner_path = resolve_path(args.miner_report)
    journal_path = resolve_path(args.journal_path)
    state_path = resolve_path(args.state_path)

    if not miner_path.exists():
        return build_blocked_report("blocked_missing_miner_report", args, {"miner_report": rel_path(miner_path)})
    miner_report = read_json(miner_path)
    if not isinstance(miner_report, dict):
        return build_blocked_report("blocked_invalid_miner_report", args, {"miner_report": rel_path(miner_path)})
    config = selected_config(miner_report)
    if config is None:
        return build_blocked_report("blocked_no_selected_derivatives_candidate", args, {"miner_report": rel_path(miner_path)})
    paths = data_paths(miner_report, config)
    if paths is None:
        return build_blocked_report(
            "blocked_missing_candidate_data_paths",
            args,
            {"miner_report": rel_path(miner_path), "selected_config": config.__dict__},
        )

    klines_path, derivatives_path = paths
    klines = read_csv(klines_path)
    derivatives = read_csv(derivatives_path)
    rows = join_rows(klines, derivatives)
    latest_index, feature = forward_feature(rows, config)
    if latest_index is None or feature is None:
        return build_blocked_report(
            "blocked_no_latest_forward_feature",
            args,
            {
                "miner_report": rel_path(miner_path),
                "selected_config": config.__dict__,
                "rows": len(rows),
                "klines_path": rel_path(klines_path),
                "derivatives_path": rel_path(derivatives_path),
            },
        )

    latest_row = rows[latest_index]
    bar_ts = str(latest_row.get("time") or "")
    matched = bool(signal_matches(config, feature))
    key = signal_key(config, bar_ts)
    state = load_state(state_path)
    emitted_keys = set(str(item) for item in state.get("emitted_signal_keys", []))
    duplicate = matched and key in emitted_keys
    events_written = 0
    status = "observer_no_signal"

    event_payload = {
        "event_type": "derivatives_event_observer_signal",
        "observer_id": "derivatives_event_forward_observer",
        "ts_emitted": now_iso(),
        "strategy_id": config.strategy_id,
        "family": config.family,
        "symbol": args.symbol.upper(),
        "interval": config.interval,
        "side": config.side,
        "bar_ts": bar_ts,
        "bar_index": latest_index,
        "close": round_float(latest_row.get("close"), 8),
        "price_move_atr": round_float(feature.get("price_move_atr")),
        "oi_delta_pct": round_float(feature.get("oi_delta_pct")),
        "funding": round_float(feature.get("funding"), 8),
        "volume_z": round_float(feature.get("volume_z")),
        "close_location": round_float(feature.get("close_location")),
        "atr": round_float(feature.get("atr"), 8),
        "regime_filter": config.regime_filter,
        "stop_atr": config.stop_atr,
        "take_atr": config.take_atr,
        "max_hold_bars": config.max_hold_bars,
        "signal_key": key,
        "can_trade": False,
        "sends_orders": False,
        "creates_paper_entry_intents": False,
        "uses_private_credentials": False,
    }
    if matched and not duplicate:
        append_jsonl(journal_path, [event_payload])
        emitted_keys.add(key)
        events_written = 1
        status = "observer_signal_written"
    elif duplicate:
        status = "observer_signal_duplicate_suppressed"

    state.update(
        {
            "observer_id": "derivatives_event_forward_observer",
            "strategy_id": config.strategy_id,
            "last_checked_at": now_iso(),
            "last_bar_ts": bar_ts,
            "last_status": status,
            "emitted_signal_keys": sorted(emitted_keys)[-500:],
            "can_trade": False,
        }
    )
    write_json(state_path, state)

    latest_observation = {
        "status": status,
        "signal": matched,
        "duplicate_suppressed": duplicate,
        "events_written": events_written,
        "bar_ts": bar_ts,
        "bar_index": latest_index,
        "close": round_float(latest_row.get("close"), 8),
        "price_move_atr": event_payload["price_move_atr"],
        "oi_delta_pct": event_payload["oi_delta_pct"],
        "funding": event_payload["funding"],
        "volume_z": event_payload["volume_z"],
        "close_location": event_payload["close_location"],
        "atr": event_payload["atr"],
        "thresholds": {
            "price_atr": config.price_atr,
            "oi_pct": config.oi_pct,
            "funding_abs": config.funding_abs,
            "volume_z": config.volume_z,
            "close_location": config.close_location,
            "regime_filter": config.regime_filter,
        },
    }
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "observer_id": "derivatives_event_forward_observer",
        "miner_report": rel_path(miner_path),
        "klines_path": rel_path(klines_path),
        "derivatives_path": rel_path(derivatives_path),
        "selected_config": config.__dict__,
        "latest_observation": latest_observation,
        "journal_path": rel_path(journal_path),
        "state_path": rel_path(state_path),
        "decision": status,
        "next_action": "keep collecting independent forward observations; no paper/live promotion from one signal",
        "runtime_boundary": {
            "observer_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "uses_private_credentials": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Observer-only latest-bar check for derivatives-event candidate")
    parser.add_argument("--miner-report", default="docs/DERIVATIVES_EVENT_EDGE_MINER_REGIME_2026-06-26.json")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/derivatives_event_forward_observer.jsonl")
    parser.add_argument("--state-path", default="logs/forward_paper_feed/derivatives_event_forward_observer_state.json")
    parser.add_argument("--latest-card-json", default="logs/forward_paper_feed/latest_derivatives_event_card.json")
    parser.add_argument("--latest-card-md", default="logs/forward_paper_feed/latest_derivatives_event_card.md")
    parser.add_argument("--out-prefix", default="docs/DERIVATIVES_EVENT_FORWARD_OBSERVER_2026-06-26")
    args = parser.parse_args()

    report = run_once(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_card(report), encoding="utf-8")
    write_json(resolve_path(args.latest_card_json), report)
    resolve_path(args.latest_card_md).write_text(render_card(report), encoding="utf-8")

    latest = report.get("latest_observation") if isinstance(report.get("latest_observation"), dict) else {}
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "signal": latest.get("signal"),
                "events_written": latest.get("events_written", 0),
                "bar_ts": latest.get("bar_ts"),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    decision = str(report.get("decision", ""))
    if decision == "blocked_no_selected_derivatives_candidate":
        return 0
    return 0 if not decision.startswith("blocked_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
