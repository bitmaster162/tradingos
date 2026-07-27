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

from tools.event_feature_factory import (  # noqa: E402
    FeatureConfig,
    build_features,
    generate_signals,
    load_csv_by_time,
)
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402


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


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def volume_regime(volume_z: Any) -> str:
    value = safe_float(volume_z)
    if value is None:
        return "volume_missing"
    if value < -0.5:
        return "volume_quiet"
    if value <= 0.5:
        return "volume_normal"
    if value <= 1.5:
        return "volume_active"
    return "volume_extreme"


def guard_profiles() -> dict[str, dict[str, Any]]:
    return {
        "volume_active": {
            "hypothesis_id": "DOC_RULE_SPOT_CONFIRM_1H_VOLUME_ACTIVE_RR1X3_V1",
            "setup": "1h LONG spot-confirmed breakout + volume_active",
            "guard_text": "volume_regime=volume_active",
        },
        "volume_z_oi_delta": {
            "hypothesis_id": "DOC_RULE_SPOT_CONFIRM_1H_VOLZ05_OI1_RR1X3_V1",
            "setup": "1h LONG spot-confirmed breakout + volume_z>=0.5 + oi_delta_pct>=1.0",
            "guard_text": "volume_z>=0.5 & oi_delta_pct>=1.0",
        },
    }


def profile_match(profile: str, *, latest_signal: dict[str, Any] | None, latest_regime: str, feature: dict[str, Any]) -> tuple[bool, dict[str, bool]]:
    volume_z = safe_float(feature.get("volume_z"))
    oi_delta = safe_float(feature.get("oi_delta_pct"))
    checks = {
        "spot_confirmed_breakout_long": latest_signal is not None,
        "volume_regime_active": latest_regime == "volume_active",
        "volume_z_ge_0_5": volume_z is not None and volume_z >= 0.5,
        "oi_delta_pct_ge_1_0": oi_delta is not None and oi_delta >= 1.0,
    }
    if profile == "volume_z_oi_delta":
        return bool(checks["spot_confirmed_breakout_long"] and checks["volume_z_ge_0_5"] and checks["oi_delta_pct_ge_1_0"]), checks
    return bool(checks["spot_confirmed_breakout_long"] and checks["volume_regime_active"]), checks


def signal_key(strategy_id: str, bar_ts: str) -> str:
    return f"{strategy_id}|{bar_ts}"


def load_state(path: Path) -> dict[str, Any]:
    state = read_json(path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("emitted_signal_keys", [])
    return state


def selected_config(profile: str) -> FeatureConfig:
    hypothesis_id = guard_profiles()[profile]["hypothesis_id"]
    return FeatureConfig(
        strategy_id=hypothesis_id,
        family="spot_confirmed_breakout",
        interval="1h",
        params={"lookback": 20, "min_body_pct": 0.30, "min_spot_div_abs": 0.03},
    )


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest_observation") if isinstance(report.get("latest_observation"), dict) else {}
    card = report.get("latest_signal_card") if isinstance(report.get("latest_signal_card"), dict) else {}
    lines = [
        "# Document Rule Forward Observer",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "## Boundary",
        "",
        "- Watch-only forward observer.",
        "- No private keys, no orders, no paper/live permission.",
        "- Signals are evidence collection only.",
        "",
        "## Hypothesis",
        "",
        f"- ID: `{report.get('hypothesis_id')}`",
        f"- Setup: `{report.get('setup')}`",
        f"- Guard: `{report.get('guard_text')}`",
        f"- RR model: `1:3`, max hold `24` bars",
        "",
        "## Latest Observation",
        "",
        f"- Status: `{latest.get('status')}`",
        f"- Latest bar: `{latest.get('bar_ts')}`",
        f"- Close: `{latest.get('close')}`",
        f"- Volume regime: `{latest.get('volume_regime')}`",
        f"- Volume z: `{latest.get('volume_z')}`",
        f"- Spot/perp divergence pct: `{latest.get('spot_perp_divergence_pct')}`",
        f"- ATR: `{latest.get('atr')}`",
        f"- Signal: `{latest.get('signal')}`",
        "",
        "## Latest Card",
        "",
        f"- Signal key: `{card.get('signal_key')}`",
        f"- Status: `{card.get('status')}`",
        f"- Side: `{card.get('side')}`",
        f"- Planned entry policy: `{card.get('planned_entry_policy')}`",
        "",
        "## Decision",
        "",
        f"- `{report.get('decision')}`",
        f"- Next: `{report.get('next_action')}`",
        "",
    ]
    return "\n".join(lines)


def blocked_report(reason: str, args: argparse.Namespace, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = guard_profiles()[args.guard_profile]
    report = {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_forward_observer.py",
        "hypothesis_id": profile["hypothesis_id"],
        "setup": profile["setup"],
        "guard_text": profile["guard_text"],
        "decision": reason,
        "runtime_boundary": {
            "classification": "watch_only_forward_observer",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "latest_observation": {"status": reason, "signal": False},
        "latest_signal_card": {"status": reason},
        "journal_path": portable(resolve_path(args.journal_path)),
        "latest_card_path": portable(resolve_path(args.latest_card_path)),
        "state_path": portable(resolve_path(args.state_path)),
        "next_action": "fix observer inputs before forward evidence collection",
        "can_trade": False,
    }
    if extra:
        report.update(extra)
    return report


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    profile = guard_profiles()[args.guard_profile]
    cfg = selected_config(args.guard_profile)
    cache_dir = resolve_path(args.cache_dir)
    futures_path = cache_dir / "futures" / "BTCUSDT" / "1h_klines.csv"
    spot_path = cache_dir / "spot" / "BTCUSDT" / "1h_klines.csv"
    derivatives_path = cache_dir / "futures" / "BTCUSDT" / "1h_oi_aligned.csv"
    missing = [portable(path) for path in (futures_path, spot_path, derivatives_path) if not path.exists()]
    if missing:
        return blocked_report("blocked_missing_data", args, {"missing": missing})

    bars = load_ohlcv(futures_path)
    spot_bars = load_ohlcv(spot_path)
    if len(bars) < 100 or len(spot_bars) < 100:
        return blocked_report("blocked_insufficient_bars", args, {"bars": len(bars), "spot_bars": len(spot_bars)})
    features = build_features(
        bars=bars,
        spot_by_time={bar.ts: bar for bar in spot_bars},
        derivatives_by_time=load_csv_by_time(derivatives_path),
        oi_lag=args.oi_lag,
        spot_perp_lookback=args.spot_perp_lookback,
        volume_window=20,
        atr_window=20,
    )
    signals = generate_signals(cfg, bars, features)
    latest_index = len(bars) - 1
    latest_bar = bars[latest_index]
    latest_feature = features[latest_index]
    latest_dt = parse_ts(latest_bar.ts)
    age_hours = None
    if latest_dt is not None:
        age_hours = (datetime.now(timezone.utc) - latest_dt).total_seconds() / 3600.0
    latest_regime = volume_regime(latest_feature.get("volume_z"))
    latest_signal = None
    for signal in signals:
        if int(signal.get("bar_index", -1)) == latest_index and str(signal.get("side_hint")).upper() == "LONG":
            latest_signal = signal
            break
    matched, guard_checks = profile_match(args.guard_profile, latest_signal=latest_signal, latest_regime=latest_regime, feature=latest_feature)
    status = "watch_signal" if matched else "no_signal"
    if age_hours is not None and age_hours > args.max_bar_age_hours:
        status = "blocked_stale_data"
        matched = False

    observation = {
        "status": status,
        "bar_ts": latest_bar.ts,
        "bar_age_hours": round(age_hours, 3) if age_hours is not None else None,
        "open": round(latest_bar.open, 8),
        "high": round(latest_bar.high, 8),
        "low": round(latest_bar.low, 8),
        "close": round(latest_bar.close, 8),
        "volume": round(latest_bar.volume, 8),
        "signal": matched,
        "raw_signal_on_bar": latest_signal is not None,
        "volume_regime": latest_regime,
        "volume_z": round(float(latest_feature.get("volume_z")), 6) if latest_feature.get("volume_z") is not None else None,
        "spot_perp_divergence_pct": round(float(latest_feature.get("spot_perp_divergence_pct")), 6)
        if latest_feature.get("spot_perp_divergence_pct") is not None
        else None,
        "oi_delta_pct": round(float(latest_feature.get("oi_delta_pct")), 6) if latest_feature.get("oi_delta_pct") is not None else None,
        "funding": round(float(latest_feature.get("funding")), 8) if latest_feature.get("funding") is not None else None,
        "atr": round(float(latest_feature.get("atr")), 8) if latest_feature.get("atr") is not None else None,
        "body_pct": round(float(latest_feature.get("body_pct")), 6) if latest_feature.get("body_pct") is not None else None,
        "close_location": round(float(latest_feature.get("close_location")), 6) if latest_feature.get("close_location") is not None else None,
        "reason": latest_signal.get("reason") if latest_signal else None,
    }
    atr = float(latest_feature.get("atr") or 0.0)
    ref_entry = latest_bar.close
    latest_key = signal_key(cfg.strategy_id, latest_bar.ts)
    card = {
        "generated_at": now_iso(),
        "status": status,
        "hypothesis_id": cfg.strategy_id,
        "strategy_id": "doc_rule_ad70abbc50_spot_confirm_1h",
        "symbol": "BTCUSDT",
        "interval": "1h",
        "side": "LONG",
        "signal_key": latest_key,
        "signal_bar_ts": latest_bar.ts,
        "planned_entry_policy": "watch_only_next_1h_open_after_signal_close",
        "reference_entry": round(ref_entry, 8),
        "reference_stop": round(ref_entry - atr * args.stop_atr, 8) if atr > 0 else None,
        "reference_take": round(ref_entry + atr * args.take_atr, 8) if atr > 0 else None,
        "stop_atr": args.stop_atr,
        "take_atr": args.take_atr,
        "max_hold_bars": args.max_hold_bars,
        "conditions": {
            "spot_confirmed_breakout_long": latest_signal is not None,
            "guard_profile": args.guard_profile,
            "guard_text": profile["guard_text"],
            "guard_checks": guard_checks,
            "volume_regime": latest_regime,
            "volume_active_required": latest_regime == "volume_active",
            "volume_z_oi_delta_required": args.guard_profile == "volume_z_oi_delta",
            "data_not_stale": status != "blocked_stale_data",
        },
        "observation": observation,
        "boundary": {
            "watch_only": True,
            "paper_allowed": False,
            "live_allowed": False,
            "orders_allowed": False,
            "can_trade": False,
        },
    }

    state_path = resolve_path(args.state_path)
    state = load_state(state_path)
    emitted = set(str(item) for item in state.get("emitted_signal_keys", []))
    journal_rows: list[dict[str, Any]] = []
    if matched and latest_key not in emitted:
        journal_rows.append(card)
        emitted.add(latest_key)
    state.update(
        {
            "updated_at": now_iso(),
            "last_status": status,
            "last_signal_key": latest_key if matched else None,
            "emitted_signal_keys": sorted(emitted)[-500:],
        }
    )
    write_json(state_path, state)
    write_json(resolve_path(args.latest_card_path), card)
    if journal_rows:
        append_jsonl(resolve_path(args.journal_path), journal_rows)

    decision = "watch_signal_emitted" if journal_rows else ("watch_signal_duplicate" if matched else status)
    next_action = (
        "notify watch-only channel and wait for outcome resolution"
        if matched
        else "keep observing fresh 1h bars; no action"
    )
    return {
        "generated_at": now_iso(),
        "tool": "tools/document_rule_forward_observer.py",
        "hypothesis_id": cfg.strategy_id,
        "setup": profile["setup"],
        "guard_text": profile["guard_text"],
        "decision": decision,
        "runtime_boundary": {
            "classification": "watch_only_forward_observer",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "data": {
            "cache_dir": portable(cache_dir),
            "futures_path": portable(futures_path),
            "spot_path": portable(spot_path),
            "derivatives_path": portable(derivatives_path),
            "bars": len(bars),
            "spot_bars": len(spot_bars),
            "signals_total": len(signals),
        },
        "latest_observation": observation,
        "latest_signal_card": card,
        "journal_path": portable(resolve_path(args.journal_path)),
        "latest_card_path": portable(resolve_path(args.latest_card_path)),
        "state_path": portable(state_path),
        "journal_rows_written": len(journal_rows),
        "next_action": next_action,
        "can_trade": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch-only forward observer for document-derived spot-confirm rule profiles")
    parser.add_argument("--guard-profile", choices=sorted(guard_profiles()), default="volume_active")
    parser.add_argument("--cache-dir", default="data/cache/binance_spot_perp_extended")
    parser.add_argument("--oi-lag", type=int, default=4)
    parser.add_argument("--spot-perp-lookback", type=int, default=12)
    parser.add_argument("--stop-atr", type=float, default=1.0)
    parser.add_argument("--take-atr", type=float, default=3.0)
    parser.add_argument("--max-hold-bars", type=int, default=24)
    parser.add_argument("--max-bar-age-hours", type=float, default=4.0)
    parser.add_argument("--journal-path", default="logs/document_rule_forward_observer/signals.jsonl")
    parser.add_argument("--latest-card-path", default="logs/document_rule_forward_observer/latest_signal_card.json")
    parser.add_argument("--state-path", default="logs/document_rule_forward_observer/state.json")
    parser.add_argument("--out-prefix", default="docs/DOCUMENT_RULE_FORWARD_OBSERVER_VOLUME_ACTIVE_RR1X3_2026-06-30")
    args = parser.parse_args()

    report = run_once(args)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "status": report.get("latest_observation", {}).get("status"),
                "signal": report.get("latest_observation", {}).get("signal"),
                "bar_ts": report.get("latest_observation", {}).get("bar_ts"),
                "journal_rows_written": report.get("journal_rows_written", 0),
                "json": portable(json_path),
                "md": portable(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
