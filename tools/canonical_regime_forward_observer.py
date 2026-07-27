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

from tools.canonical_regime_gate_overlay import classify_regimes  # noqa: E402
from tools.liquidity_sweep_detector import load_ohlcv  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def render_markdown(report: dict[str, Any]) -> str:
    latest = report.get("latest_regime") if isinstance(report.get("latest_regime"), dict) else {}
    card = report.get("forward_card") if isinstance(report.get("forward_card"), dict) else {}
    return "\n".join(
        [
            "# Canonical Regime Forward Observer",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Forward observation only.",
            "- Computes Canonical Bot-Safe regime on the latest closed public 4H cache.",
            "- Does not filter signals, send alerts by itself, send orders or grant paper/live permission.",
            "",
            "## Latest Regime",
            "",
            f"- Regime: `{latest.get('regime')}`.",
            f"- Bar: `{latest.get('ts')}` close `{latest.get('close')}`.",
            f"- Trend score: `{latest.get('trend_strength_score')}`.",
            f"- ADX14: `{latest.get('adx14')}`.",
            f"- Range/ATR: `{latest.get('range_atr')}`.",
            f"- Shock watch: `{report.get('shock_watch')}`.",
            "",
            "## Forward Card Linkage",
            "",
            f"- Card status: `{card.get('status')}`.",
            f"- Card bar: `{card.get('latest_closed_bar_ts')}`.",
            f"- Card signals: `{card.get('signals_on_latest_bar')}`.",
            "",
            "## Decision",
            "",
            f"- `{report.get('decision')}`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward observer for Canonical Bot-Safe regime state")
    parser.add_argument("--ohlcv-csv", default="_dl/forward_paper_feed/cache/futures/BTCUSDT/4h_klines.csv")
    parser.add_argument("--card-json-path", default="logs/forward_paper_feed/latest_signal_card.json")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/canonical_regime_forward_observer.jsonl")
    parser.add_argument("--shock-range-atr", type=float, default=2.5)
    parser.add_argument("--min-adx", type=float, default=18.0)
    parser.add_argument("--trend-threshold", type=float, default=0.5)
    parser.add_argument("--out-prefix", default="docs/CANONICAL_REGIME_FORWARD_OBSERVER_2026-06-09")
    args = parser.parse_args()

    ohlcv_path = resolve_path(args.ohlcv_csv)
    card_path = resolve_path(args.card_json_path)
    bars = load_ohlcv(ohlcv_path)
    regimes = classify_regimes(
        bars,
        shock_range_atr=args.shock_range_atr,
        min_adx=args.min_adx,
        trend_threshold=args.trend_threshold,
    )
    latest = regimes[-1] if regimes else {}
    card = read_json(card_path)
    if not isinstance(card, dict):
        card = {}
    shock_watch = latest.get("regime") == "SHOCK"
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "canonical_regime_forward_observer_public_data_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "ohlcv_path": str(ohlcv_path),
        "card_path": str(card_path) if card_path.exists() else None,
        "settings": {
            "shock_range_atr": args.shock_range_atr,
            "min_adx": args.min_adx,
            "trend_threshold": args.trend_threshold,
        },
        "latest_regime": latest,
        "forward_card": {
            "status": card.get("status"),
            "strategy_id": card.get("strategy_id"),
            "symbol": card.get("symbol"),
            "interval": card.get("interval"),
            "latest_closed_bar_ts": card.get("latest_closed_bar_ts"),
            "signals_on_latest_bar": card.get("signals_on_latest_bar"),
        },
        "shock_watch": shock_watch,
        "decision": "observe_only_no_signal_filtering_no_orders",
        "can_trade": False,
    }
    append_jsonl(resolve_path(args.journal_path), report)
    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "regime": latest.get("regime"),
                "bar": latest.get("ts"),
                "shock_watch": shock_watch,
                "json": str(json_path),
                "md": str(md_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
