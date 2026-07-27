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

from tools.crowd_fade_positioning_diagnostic import (  # noqa: E402
    RATIO_FIELDS,
    build_signals,
    parse_csv_float_by_time,
    resolve_path,
)
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402
from tools.liquidity_sweep_forward_eval import compute_atr  # noqa: E402


DEFAULT_DIAGNOSTIC = ROOT / "docs" / "CROWD_FADE_POSITIONING_DIAGNOSTIC_2026-06-19.json"
DEFAULT_CANDIDATE_LOCK = ROOT / "configs" / "CROWD_FADE_FORWARD_LOCK.json"
DEFAULT_CACHE_DIR = ROOT / "data" / "cache" / "binance_spot_perp_extended"
DEFAULT_JOURNAL = ROOT / "logs" / "forward_paper_feed" / "crowd_fade_positioning_shadow_observer.jsonl"
DEFAULT_STATE = ROOT / "logs" / "forward_paper_feed" / "crowd_fade_positioning_shadow_observer_state.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CROWD_FADE_POSITIONING_SHADOW_OBSERVER_2026-06-19"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_locked_candidate(path: Path) -> tuple[dict[str, Any] | None, str | None, bool]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        return None, None, False
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        return None, str(payload.get("version") or "unknown"), False
    return candidate, str(payload.get("version") or "unknown"), payload.get("enabled") is True


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest", {})
    lines = [
        "# Crowd-Fade Positioning Shadow Observer",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Status: `{latest.get('status')}`",
        f"- Strategy: `{report.get('strategy_id')}`",
        f"- Can trade: `{report.get('can_trade')}`",
        "",
        "## Latest",
        "",
        f"- Signal found: `{latest.get('signal_found')}`",
        f"- Signal time: `{latest.get('signal_time')}`",
        f"- Side: `{latest.get('side_hint')}`",
        f"- Ratio: `{latest.get('ratio')}`",
        f"- Ratio z-score: `{latest.get('ratio_z')}`",
        f"- Funding: `{latest.get('funding')}`",
        f"- OI delta: `{latest.get('oi_delta')}`",
        "",
        "## Boundary",
        "",
        "- Observer-only. No paper entry intent and no orders.",
        "- This watches a low-history crowd-fade candidate until real forward outcomes accumulate.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Observer-only latest-bar check for the crowd-fade positioning candidate.")
    parser.add_argument("--diagnostic", default=str(DEFAULT_DIAGNOSTIC))
    parser.add_argument("--candidate-lock", default=str(DEFAULT_CANDIDATE_LOCK))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--journal-path", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--lookback-bars", type=int, default=3)
    args = parser.parse_args()

    diagnostic_path = resolve_path(args.diagnostic)
    candidate_lock_path = resolve_path(args.candidate_lock)
    cache_dir = resolve_path(args.cache_dir)
    journal_path = resolve_path(args.journal_path)
    state_path = resolve_path(args.state_path)
    out_prefix = resolve_path(args.out_prefix)

    candidate, candidate_lock_version, candidate_enabled = load_locked_candidate(candidate_lock_path)
    if not isinstance(candidate, dict):
        report = {
            "generated_at": now_iso(),
            "status": "missing_candidate_lock",
            "diagnostic": str(diagnostic_path),
            "candidate_lock": str(candidate_lock_path),
            "can_trade": False,
        }
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown({"generated_at": report["generated_at"], "latest": report, "can_trade": False}), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if not candidate_enabled:
        report = {
            "generated_at": now_iso(),
            "engine": "CROWD_FADE_POSITIONING_SHADOW_OBSERVER",
            "engine_version": "1.1.0",
            "status": "candidate_paused_by_lock",
            "diagnostic": str(diagnostic_path),
            "candidate_source": "locked_config",
            "candidate_lock": str(candidate_lock_path),
            "candidate_lock_version": candidate_lock_version,
            "strategy_id": candidate.get("strategy_id"),
            "latest": {"status": "candidate_paused_by_lock", "signal_found": False},
            "can_trade": False,
        }
        write_json(out_prefix.with_suffix(".json"), report)
        out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps({"strategy_id": report["strategy_id"], "status": report["status"], "can_trade": False}, ensure_ascii=False, indent=2))
        return 0

    interval = str(candidate["interval"])
    ratio_field = str(candidate["ratio_field"])
    if ratio_field not in RATIO_FIELDS:
        raise ValueError(f"unsupported ratio_field in candidate: {ratio_field}")

    futures_path = cache_dir / "futures" / args.symbol.upper() / f"{interval}_klines.csv"
    derivatives_path = cache_dir / "futures" / args.symbol.upper() / f"{interval}_oi_aligned.csv"
    crowd_path = cache_dir / "futures" / args.symbol.upper() / f"{interval}_crowd_positioning.csv"
    bars = load_ohlcv(futures_path)
    atr_values = compute_atr(bars, 14)
    crowd_by_time = parse_csv_float_by_time(crowd_path, RATIO_FIELDS)
    derivatives_by_time = parse_csv_float_by_time(derivatives_path, ["open_interest", "funding"])

    signals = build_signals(
        bars=bars,
        crowd_by_time=crowd_by_time,
        derivatives_by_time=derivatives_by_time,
        ratio_field=ratio_field,
        z_window=int(candidate["z_window"]),
        z_threshold=float(candidate["z_threshold"]),
        side_mode=str(candidate["side_mode"]),
        oi_lookback=6 if interval == "1h" else 16 if interval == "15m" else 3,
        require_oi_expansion=bool(candidate.get("require_oi_expansion")),
        require_funding_alignment=bool(candidate.get("require_funding_alignment")),
        atr_values=atr_values,
    )
    recent_start = max(0, len(bars) - max(1, args.lookback_bars))
    recent_signals = [item for item in signals if int(item["bar_index"]) >= recent_start]
    latest_signal = recent_signals[-1] if recent_signals else None

    state = read_json(state_path)
    if not isinstance(state, dict):
        state = {}
    event: dict[str, Any] | None = None
    if latest_signal is not None:
        bar = bars[int(latest_signal["bar_index"])]
        signal_key = f"{candidate['strategy_id']}|{bar.ts}|{latest_signal['side_hint']}"
        event = {
            "ts": now_iso(),
            "signal_key": signal_key,
            "strategy_id": candidate["strategy_id"],
            "interval": interval,
            "signal_time": bar.ts,
            "side_hint": latest_signal["side_hint"],
            "ratio_field": ratio_field,
            "ratio": round(float(latest_signal["ratio"]), 6),
            "ratio_z": round(float(latest_signal["ratio_z"]), 6),
            "atr": round(float(latest_signal["atr"]), 8),
            "funding": latest_signal.get("funding"),
            "oi_delta": latest_signal.get("oi_delta"),
            "stop_atr": candidate.get("stop_atr"),
            "take_atr": candidate.get("take_atr"),
            "rr": f"{candidate.get('stop_atr')}:{candidate.get('take_atr')}",
            "hold": candidate.get("hold"),
            "source": "crowd_fade_positioning_shadow_observer",
            "candidate_lock_version": candidate_lock_version,
            "can_trade": False,
        }
        if state.get("last_signal_key") != signal_key:
            append_jsonl(journal_path, event)
            state["last_signal_key"] = signal_key
            state["last_signal_at"] = now_iso()
            write_json(state_path, state)

    latest = {
        "status": "observer_signal" if latest_signal is not None else "no_recent_signal",
        "signal_found": latest_signal is not None,
        "signal_time": event.get("signal_time") if event else None,
        "signal_key": event.get("signal_key") if event else None,
        "side_hint": event.get("side_hint") if event else None,
        "ratio": event.get("ratio") if event else None,
        "ratio_z": event.get("ratio_z") if event else None,
        "funding": event.get("funding") if event else None,
        "oi_delta": event.get("oi_delta") if event else None,
    }
    report = {
        "generated_at": now_iso(),
        "engine": "CROWD_FADE_POSITIONING_SHADOW_OBSERVER",
        "engine_version": "1.0.0",
        "diagnostic": str(diagnostic_path),
        "candidate_source": "locked_config",
        "candidate_lock": str(candidate_lock_path),
        "candidate_lock_version": candidate_lock_version,
        "strategy_id": candidate["strategy_id"],
        "candidate_classification": candidate.get("classification"),
        "latest": latest,
        "journal_path": str(journal_path),
        "state_path": str(state_path),
        "can_trade": False,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"strategy_id": report["strategy_id"], "latest": latest, "can_trade": False}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
