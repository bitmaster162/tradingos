#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.max_backtest import (  # noqa: E402
    align_derivatives,
    fetch_funding_history,
    fetch_open_interest_history,
    write_oi_csv,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def pct_delta(rows: list[dict[str, str]], index: int, field: str, lookback: int) -> float | None:
    if index - lookback < 0:
        return None
    current = safe_float(rows[index].get(field))
    previous = safe_float(rows[index - lookback].get(field))
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100


def zscore(rows: list[dict[str, str]], index: int, field: str, window: int) -> float | None:
    if index - window + 1 < 0:
        return None
    values = [safe_float(row.get(field)) for row in rows[index - window + 1 : index + 1]]
    clean = [value for value in values if value is not None]
    if len(clean) < max(20, window // 2):
        return None
    mean = sum(clean) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean) / len(clean)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (clean[-1] - mean) / std


def latest_row_at_or_before(rows: list[dict[str, str]], target_ts: str) -> tuple[int, dict[str, str] | None]:
    target = parse_time(target_ts)
    if target is None:
        return -1, None
    best_index = -1
    best_time: datetime | None = None
    for index, row in enumerate(rows):
        row_time = parse_time(row.get("time") or row.get("timestamp"))
        if row_time is None or row_time > target:
            continue
        if best_time is None or row_time >= best_time:
            best_index = index
            best_time = row_time
    return best_index, rows[best_index] if best_index >= 0 else None


def classify_funding(funding: float | None, compressed_abs: float, hot_abs: float) -> str:
    if funding is None:
        return "unavailable"
    if funding >= hot_abs:
        return "positive_hot"
    if funding <= -hot_abs:
        return "negative_hot"
    if abs(funding) <= compressed_abs:
        return "compressed"
    return "positive_mild" if funding > 0 else "negative_mild"


def classify_oi(delta_pct: float | None, strong_abs_pct: float) -> str:
    if delta_pct is None:
        return "unavailable"
    if delta_pct >= strong_abs_pct:
        return "expansion_strong"
    if delta_pct > 0:
        return "expansion_mild"
    if delta_pct <= -strong_abs_pct:
        return "contraction_strong"
    if delta_pct < 0:
        return "contraction_mild"
    return "flat"


def context_bias(*, price_delta_pct: float | None, oi_delta_pct: float | None, funding_state: str) -> dict[str, Any]:
    if price_delta_pct is None or oi_delta_pct is None or funding_state == "unavailable":
        return {"bias": "unavailable", "notes": ["missing_price_or_derivatives_context"]}
    notes: list[str] = []
    bias = "mixed"
    if price_delta_pct > 0 and oi_delta_pct > 0:
        bias = "trend_confirmation_long"
        notes.append("price_up_oi_up")
    elif price_delta_pct > 0 and oi_delta_pct < 0:
        bias = "short_squeeze_or_position_closing"
        notes.append("price_up_oi_down")
    elif price_delta_pct < 0 and oi_delta_pct > 0:
        bias = "short_build_or_downtrend_confirmation"
        notes.append("price_down_oi_up")
    elif price_delta_pct < 0 and oi_delta_pct < 0:
        bias = "deleveraging_or_capitulation"
        notes.append("price_down_oi_down")
    if funding_state == "positive_hot":
        notes.append("positive_hot_funding_long_crowding_risk")
    elif funding_state == "negative_hot":
        notes.append("negative_hot_funding_short_crowding_risk")
    elif funding_state == "compressed":
        notes.append("funding_compressed_ready_for_impulse")
    return {"bias": bias, "notes": notes}


def oi_guard_candidate_state(*, data_degraded: bool, oi_state: str, context_bias_value: str) -> dict[str, Any]:
    if data_degraded or oi_state == "unavailable":
        state = "unavailable"
        reason = "derivatives_context_missing_or_stale"
        would_keep = False
    elif oi_state == "expansion_strong":
        state = "would_keep"
        reason = "validated_candidate_keep_oi_expansion_strong"
        would_keep = True
    else:
        state = "would_block"
        reason = f"oi_state_not_expansion_strong:{oi_state}"
        would_keep = False
    return {
        "name": "keep_oi_expansion_strong",
        "source_validation": "docs/STRATEGY_MIX_OI_GUARD_VALIDATION_2026-06-15.md",
        "state": state,
        "would_keep_long_signal": would_keep,
        "reason": reason,
        "context_bias": context_bias_value,
        "can_filter_now": False,
        "live_permission": False,
        "paper_permission": False,
        "mode": "forward_observation_only",
    }


def fetch_live_derivatives(symbol: str, interval: str, rows: list[dict[str, str]], pages: int, limit: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
    oi_records = fetch_open_interest_history(symbol, interval, limit=limit, pages=pages)
    funding_records = fetch_funding_history(symbol, limit=1000, pages=max(1, pages))
    aligned = align_derivatives(rows, interval=interval, oi_records=oi_records, funding_records=funding_records)
    return aligned, {
        "source": "binance_public_live_derivatives",
        "oi_records": len(oi_records),
        "funding_records": len(funding_records),
    }


def render_markdown(report: dict[str, Any]) -> str:
    ctx = report.get("context") if isinstance(report.get("context"), dict) else {}
    freshness = report.get("freshness") if isinstance(report.get("freshness"), dict) else {}
    card = report.get("forward_card") if isinstance(report.get("forward_card"), dict) else {}
    guard = report.get("oi_guard_candidate") if isinstance(report.get("oi_guard_candidate"), dict) else {}
    return "\n".join(
        [
            "# OI/Funding Forward Context Observer",
            "",
            f"Generated: `{report.get('generated_at')}`",
            "",
            "## Boundary",
            "",
            "- Forward observation only.",
            "- Public derivatives context only.",
            "- Does not filter signals, send Telegram alerts by itself, send orders or grant paper/live permission.",
            "",
            "## Latest Context",
            "",
            f"- Forward card status: `{card.get('status')}`.",
            f"- Card bar: `{card.get('latest_closed_bar_ts')}`.",
            f"- Derivatives source: `{report.get('derivatives_source')}`.",
            f"- Data degraded: `{report.get('data_degraded')}`.",
            f"- Staleness hours: `{freshness.get('staleness_hours')}`.",
            f"- Funding: `{ctx.get('funding')}` state `{ctx.get('funding_state')}`.",
            f"- OI: `{ctx.get('open_interest')}`.",
            f"- OI delta 12 bars: `{ctx.get('oi_delta_12_pct')}`.",
            f"- OI zscore 100: `{ctx.get('oi_zscore_100')}`.",
            f"- Price delta 12 bars: `{ctx.get('price_delta_12_pct')}`.",
            f"- Context bias: `{ctx.get('context_bias')}`.",
            f"- Notes: `{', '.join(ctx.get('context_notes') or [])}`.",
            "",
            "## OI Guard Candidate",
            "",
            f"- Candidate: `{guard.get('name')}`.",
            f"- State: `{guard.get('state')}`.",
            f"- Would keep long signal: `{guard.get('would_keep_long_signal')}`.",
            f"- Reason: `{guard.get('reason')}`.",
            f"- Can filter now: `{guard.get('can_filter_now')}`.",
            f"- Live permission: `{guard.get('live_permission')}`.",
            "",
            "## Decision",
            "",
            f"- `{report.get('decision')}`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward observer for OI/funding context")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--ohlcv-csv", default="_dl/forward_paper_feed/cache/futures/BTCUSDT/4h_klines.csv")
    parser.add_argument("--derivatives-csv", default="data/cache/binance_spot_perp_extended/futures/BTCUSDT/4h_oi_aligned.csv")
    parser.add_argument("--aligned-out-csv", default="_dl/forward_paper_feed/cache/futures/BTCUSDT/4h_oi_aligned.csv")
    parser.add_argument("--card-json-path", default="logs/forward_paper_feed/latest_signal_card.json")
    parser.add_argument("--journal-path", default="logs/forward_paper_feed/oi_funding_forward_context_observer.jsonl")
    parser.add_argument("--source", choices=["auto", "live", "cache"], default="auto")
    parser.add_argument("--live-pages", type=int, default=2)
    parser.add_argument("--live-limit", type=int, default=500)
    parser.add_argument("--max-stale-hours", type=float, default=12.0)
    parser.add_argument("--oi-lookback", type=int, default=12)
    parser.add_argument("--oi-z-window", type=int, default=100)
    parser.add_argument("--oi-strong-abs-pct", type=float, default=0.10)
    parser.add_argument("--funding-compressed-abs", type=float, default=0.0002)
    parser.add_argument("--funding-hot-abs", type=float, default=0.0008)
    parser.add_argument("--out-prefix", default="docs/OI_FUNDING_FORWARD_CONTEXT_OBSERVER_2026-06-09")
    args = parser.parse_args()

    ohlcv_path = resolve_path(args.ohlcv_csv)
    derivatives_path = resolve_path(args.derivatives_csv)
    card_path = resolve_path(args.card_json_path)
    aligned_out_path = resolve_path(args.aligned_out_csv)

    ohlcv_rows = read_csv_rows(ohlcv_path)
    if not ohlcv_rows:
        raise ValueError(f"missing_or_empty_ohlcv_csv:{ohlcv_path}")

    card = read_json(card_path)
    if not isinstance(card, dict):
        card = {}
    target_ts = str(card.get("latest_closed_bar_ts") or ohlcv_rows[-1].get("time") or "")

    derivatives_rows: list[dict[str, str]] = []
    source_meta: dict[str, Any] = {"source": "none"}
    live_error: str | None = None
    if args.source in {"auto", "live"}:
        try:
            derivatives_rows, source_meta = fetch_live_derivatives(args.symbol.upper(), args.interval, ohlcv_rows, args.live_pages, args.live_limit)
            write_oi_csv(aligned_out_path, derivatives_rows)
        except Exception as exc:  # noqa: BLE001 - report public-data degradation, do not crash auto mode.
            live_error = f"{type(exc).__name__}:{exc}"
            if args.source == "live":
                raise
    if not derivatives_rows and args.source in {"auto", "cache"}:
        derivatives_rows = read_csv_rows(derivatives_path)
        source_meta = {"source": "cache_aligned_derivatives", "path": str(derivatives_path), "live_error": live_error}

    derivative_index, derivative_row = latest_row_at_or_before(derivatives_rows, target_ts)
    ohlcv_index, ohlcv_row = latest_row_at_or_before(ohlcv_rows, target_ts)
    derivative_ts = derivative_row.get("time") if derivative_row else None
    target_time = parse_time(target_ts)
    derivative_time = parse_time(derivative_ts) if derivative_ts else None
    staleness_hours = None
    if target_time and derivative_time:
        staleness_hours = round((target_time - derivative_time).total_seconds() / 3600, 3)

    funding = safe_float(derivative_row.get("funding")) if derivative_row else None
    open_interest = safe_float(derivative_row.get("open_interest")) if derivative_row else None
    oi_delta_12 = pct_delta(derivatives_rows, derivative_index, "open_interest", args.oi_lookback) if derivative_index >= 0 else None
    oi_delta_3 = pct_delta(derivatives_rows, derivative_index, "open_interest", 3) if derivative_index >= 0 else None
    oi_z = zscore(derivatives_rows, derivative_index, "open_interest", args.oi_z_window) if derivative_index >= 0 else None
    price_delta_12 = pct_delta(ohlcv_rows, ohlcv_index, "close", args.oi_lookback) if ohlcv_index >= 0 else None
    funding_state = classify_funding(funding, args.funding_compressed_abs, args.funding_hot_abs)
    oi_state = classify_oi(oi_delta_12, args.oi_strong_abs_pct)

    stale = staleness_hours is None or staleness_hours > args.max_stale_hours
    missing = funding is None or open_interest is None
    data_degraded = bool(stale or missing)
    bias = {"bias": "degraded_unavailable", "notes": ["derivatives_data_stale_or_missing"]} if data_degraded else context_bias(
        price_delta_pct=price_delta_12,
        oi_delta_pct=oi_delta_12,
        funding_state=funding_state,
    )
    decision = "observe_only_data_degraded_no_orders" if data_degraded else "observe_only_context_available_no_orders"
    guard_candidate = oi_guard_candidate_state(
        data_degraded=data_degraded,
        oi_state=oi_state,
        context_bias_value=str(bias["bias"]),
    )

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "oi_funding_forward_context_public_data_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "symbol": args.symbol.upper(),
        "interval": args.interval,
        "ohlcv_path": str(ohlcv_path),
        "derivatives_source": source_meta,
        "aligned_out_csv": str(aligned_out_path) if aligned_out_path.exists() else None,
        "forward_card": {
            "status": card.get("status"),
            "strategy_id": card.get("strategy_id"),
            "symbol": card.get("symbol"),
            "interval": card.get("interval"),
            "latest_closed_bar_ts": card.get("latest_closed_bar_ts"),
            "signals_on_latest_bar": card.get("signals_on_latest_bar"),
        },
        "freshness": {
            "target_ts": target_ts,
            "derivatives_ts": derivative_ts,
            "staleness_hours": staleness_hours,
            "max_stale_hours": args.max_stale_hours,
            "is_stale": stale,
        },
        "context": {
            "open_interest": None if open_interest is None else round(open_interest, 6),
            "funding": None if funding is None else funding,
            "funding_state": funding_state,
            "oi_delta_3_pct": None if oi_delta_3 is None else round(oi_delta_3, 6),
            "oi_delta_12_pct": None if oi_delta_12 is None else round(oi_delta_12, 6),
            "oi_state": oi_state,
            "oi_zscore_100": None if oi_z is None else round(oi_z, 6),
            "price_delta_12_pct": None if price_delta_12 is None else round(price_delta_12, 6),
            "context_bias": bias["bias"],
            "context_notes": bias["notes"],
        },
        "oi_guard_candidate": guard_candidate,
        "data_degraded": data_degraded,
        "decision": decision,
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
                "decision": decision,
                "data_degraded": data_degraded,
                "context_bias": report["context"]["context_bias"],
                "oi_guard_candidate_state": guard_candidate["state"],
                "oi_guard_would_keep_long_signal": guard_candidate["would_keep_long_signal"],
                "funding_state": funding_state,
                "oi_state": oi_state,
                "staleness_hours": staleness_hours,
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
