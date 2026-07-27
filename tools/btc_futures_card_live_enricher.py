#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

import btc_futures_trade_card


DEFAULT_INPUT = Path("docs/RESEARCH_CANDIDATE_TRADE_CARD_SMOKE_2026-06-04.card.json")
DEFAULT_OUT_PREFIX = Path("docs/BTCUSDT_FUTURES_CARD_LIVE_ENRICHMENT_2026-06-04")
FAPI_BASE = "https://fapi.binance.com"
SUPPORTED_OI_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ms_to_iso(value: int | float | str | None) -> str | None:
    if value in {None, ""}:
        return None
    return datetime.fromtimestamp(int(float(value)) / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def to_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_json(path: str, params: dict[str, Any], timeout: int) -> Any:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{FAPI_BASE}{path}?{query}" if query else f"{FAPI_BASE}{path}"
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed public Binance endpoint.
        return json.loads(response.read().decode("utf-8"))


def fetch_public_snapshot(symbol: str, interval: str, kline_limit: int, timeout: int) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    premium: dict[str, Any] = {}
    oi: dict[str, Any] = {}
    oi_hist: list[dict[str, Any]] = []
    klines: list[list[Any]] = []
    try:
        premium = fetch_json("/fapi/v1/premiumIndex", {"symbol": symbol}, timeout)
    except Exception as exc:  # noqa: BLE001 - report as blocked context, not live signal.
        errors.append(f"premium_index_fetch_failed:{type(exc).__name__}")
    try:
        oi = fetch_json("/fapi/v1/openInterest", {"symbol": symbol}, timeout)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"open_interest_fetch_failed:{type(exc).__name__}")
    try:
        klines = fetch_json(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": max(20, min(kline_limit, 500))},
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"klines_fetch_failed:{type(exc).__name__}")
    oi_period = interval if interval in SUPPORTED_OI_PERIODS else "1h"
    try:
        oi_hist = fetch_json(
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": oi_period, "limit": 30},
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"open_interest_hist_fetch_failed:{type(exc).__name__}")

    return {
        "symbol": symbol,
        "interval": interval,
        "fetched_at": now_iso(),
        "premium_index": premium,
        "open_interest": oi,
        "open_interest_hist_period": oi_period,
        "open_interest_hist": oi_hist if isinstance(oi_hist, list) else [],
        "klines": klines if isinstance(klines, list) else [],
    }, errors


def true_range(row: list[Any], prev_close: float | None) -> float:
    high = float(row[2])
    low = float(row[3])
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr14(klines: list[list[Any]]) -> float | None:
    if len(klines) < 15:
        return None
    trs: list[float] = []
    prev_close: float | None = None
    for row in klines:
        tr = true_range(row, prev_close)
        trs.append(tr)
        prev_close = float(row[4])
    values = trs[-14:]
    if not values:
        return None
    return round(sum(values) / len(values), 8)


def latest_close(klines: list[list[Any]]) -> float | None:
    if not klines:
        return None
    return to_float(klines[-1][4])


def relative_volume(klines: list[list[Any]], lookback: int = 20) -> float | None:
    if len(klines) < lookback + 1:
        return None
    latest = to_float(klines[-1][5])
    history = [to_float(row[5]) for row in klines[-(lookback + 1):-1]]
    history = [item for item in history if item is not None]
    if latest is None or not history:
        return None
    avg = sum(history) / len(history)
    if avg <= 0:
        return None
    return round(latest / avg, 6)


def oi_change_pct(snapshot: dict[str, Any]) -> float | None:
    rows = snapshot.get("open_interest_hist") or []
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    first = to_float(rows[0].get("sumOpenInterest"))
    last = to_float(rows[-1].get("sumOpenInterest"))
    if first is None or last is None or first <= 0:
        return None
    return round(((last - first) / first) * 100.0, 6)


def summarize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    premium = snapshot.get("premium_index") or {}
    oi = snapshot.get("open_interest") or {}
    klines = snapshot.get("klines") or []
    mark_price = to_float(premium.get("markPrice"))
    last_funding = to_float(premium.get("lastFundingRate"))
    index_price = to_float(premium.get("indexPrice"))
    current_oi = to_float(oi.get("openInterest"))
    latest = latest_close(klines)
    atr = atr14(klines)
    return {
        "symbol": snapshot.get("symbol"),
        "interval": snapshot.get("interval"),
        "fetched_at": snapshot.get("fetched_at"),
        "mark_price": mark_price,
        "index_price": index_price,
        "last_funding_rate": last_funding,
        "next_funding_time": ms_to_iso(premium.get("nextFundingTime")),
        "open_interest": current_oi,
        "open_interest_time": ms_to_iso(oi.get("time")),
        "open_interest_change_pct_window": oi_change_pct(snapshot),
        "latest_close": latest,
        "atr14": atr,
        "atr14_pct_of_mark": round((atr / mark_price) * 100.0, 6) if atr and mark_price else None,
        "relative_volume_20": relative_volume(klines),
        "kline_count": len(klines),
        "last_kline_open_time": ms_to_iso(klines[-1][0]) if klines else None,
    }


def side_stop_tp(side: str, entry: float, stop_distance: float, target_rr: float) -> tuple[float, float]:
    if side.upper() == "SHORT":
        return round(entry + stop_distance, 2), round(entry - stop_distance * target_rr, 2)
    return round(entry - stop_distance, 2), round(entry + stop_distance * target_rr, 2)


def liquidation_price(side: str, entry: float, buffer_pct: float) -> float:
    if side.upper() == "SHORT":
        return round(entry * (1.0 + buffer_pct / 100.0), 2)
    return round(entry * (1.0 - buffer_pct / 100.0), 2)


def enrich_card(card: dict[str, Any], summary: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    enriched = dict(card)
    blocks: list[str] = ["live_review_only_no_order_permission"]
    mark = summary.get("mark_price") or summary.get("latest_close")
    if mark is None:
        blocks.append("missing_public_mark_price")
        return enriched, blocks

    side = str(enriched.get("side") or "").upper()
    old_entry = to_float(enriched.get("entry"))
    if old_entry and old_entry > 0:
        distance_pct = abs(mark - old_entry) / mark * 100.0
        enriched["entry_distance_to_live_mark_pct"] = round(distance_pct, 6)
        if distance_pct > args.max_entry_distance_pct and not args.recenter_to_mark:
            blocks.append("entry_far_from_live_mark")

    if args.recenter_to_mark:
        atr = to_float(summary.get("atr14"))
        min_stop = mark * (args.min_stop_pct / 100.0)
        stop_distance = max(atr * args.atr_stop_mult if atr else 0.0, min_stop)
        stop, tp = side_stop_tp(side, mark, stop_distance, args.target_rr)
        enriched["entry"] = round(mark, 2)
        enriched["stop"] = stop
        enriched["tp"] = [tp]
        enriched["stop_method"] = "atr"
        enriched["entry_recentered_to_live_mark"] = True
        enriched["recenter_policy"] = {
            "atr_stop_mult": args.atr_stop_mult,
            "min_stop_pct": args.min_stop_pct,
            "target_rr": args.target_rr,
            "stop_distance": round(stop_distance, 8),
        }

    entry = to_float(enriched.get("entry")) or mark
    enriched["mark_price"] = round(mark, 2)
    enriched["liquidation_price"] = liquidation_price(side, entry, args.synthetic_liq_buffer_pct)
    enriched["liquidation_price_mode"] = "synthetic_review_only_replace_with_exchange_value_before_paper_or_live"
    enriched["funding"] = summary.get("last_funding_rate")
    enriched["fees_slippage_included"] = bool(enriched.get("fees_slippage_included", True))
    enriched["live_market_snapshot"] = summary
    enriched["oi_context"] = f"current_oi={summary.get('open_interest')}; oi_change_pct_window={summary.get('open_interest_change_pct_window')}"
    enriched["volatility_context"] = f"atr14={summary.get('atr14')}; atr14_pct_of_mark={summary.get('atr14_pct_of_mark')}; rel_vol20={summary.get('relative_volume_20')}"
    enriched["generated_mode"] = "live_public_enriched_review"
    enriched["live_permission"] = False
    enriched["notes"] = (
        str(enriched.get("notes") or "")
        + " Public-data enriched review only. No order permission. Replace synthetic liquidation with exchange value before paper/live."
    ).strip()

    if enriched.get("source_research_gate_pass") is False:
        blocks.append("source_candidate_failed_research_gate")
    if enriched.get("promotion_gate_pass") is False:
        blocks.append("source_candidate_failed_promotion_gate")
    if str(card.get("generated_mode") or "").startswith("research_replay"):
        blocks.append("source_card_was_research_replay")
    return enriched, sorted(set(blocks))


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("market_summary") or {}
    validation = report.get("card_validation") or {}
    first = validation.get("results", [{}])[0] if validation.get("results") else {}
    guardian_result = first.get("guardian", {})
    computed = guardian_result.get("computed", {})
    card = report.get("enriched_card") or {}
    lines = [
        "# BTCUSDT Futures Card Live Enrichment",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Public Binance market-data enrichment only.",
        "- No API keys, no private data, no orders.",
        "- Output is a review card, not a live trading signal.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Blocks: `{', '.join(report['blocks']) or '-'}`",
        f"- Enriched card: `{report.get('enriched_card_path')}`",
        "",
        "## Market Snapshot",
        "",
        f"- Mark price: `{summary.get('mark_price')}`",
        f"- Funding: `{summary.get('last_funding_rate')}`",
        f"- Open interest: `{summary.get('open_interest')}`",
        f"- OI change window: `{summary.get('open_interest_change_pct_window')}`",
        f"- ATR14: `{summary.get('atr14')}`",
        f"- ATR14 % of mark: `{summary.get('atr14_pct_of_mark')}`",
        f"- Relative volume 20: `{summary.get('relative_volume_20')}`",
        "",
        "## Card Check",
        "",
        f"- Side: `{card.get('side')}`",
        f"- Entry: `{card.get('entry')}`",
        f"- Stop: `{card.get('stop')}`",
        f"- TP: `{card.get('tp')}`",
        f"- RR: `{computed.get('rr')}`",
        f"- Liquidation buffer: `{computed.get('liquidation_buffer_pct')}`",
        f"- Guardian decision: `{guardian_result.get('decision')}`",
        "",
    ]
    if report.get("fetch_errors"):
        lines.extend(["## Fetch Errors", ""])
        for err in report["fetch_errors"]:
            lines.append(f"- `{err}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Enrich a BTCUSDT futures trade card with public live market data")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--kline-limit", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--recenter-to-mark", action="store_true")
    parser.add_argument("--atr-stop-mult", type=float, default=1.0)
    parser.add_argument("--min-stop-pct", type=float, default=0.35)
    parser.add_argument("--target-rr", type=float, default=2.0)
    parser.add_argument("--max-entry-distance-pct", type=float, default=0.75)
    parser.add_argument("--synthetic-liq-buffer-pct", type=float, default=15.0)
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    input_path = Path(args.input)
    card = read_json(input_path)
    snapshot, fetch_errors = fetch_public_snapshot(args.symbol, args.interval, args.kline_limit, args.timeout)
    summary = summarize_snapshot(snapshot)
    enriched, blocks = enrich_card(card, summary, args)
    blocks.extend(fetch_errors)
    blocks = sorted(set(blocks))

    schema = read_json(btc_futures_trade_card.DEFAULT_SCHEMA)
    guardian_config = read_json(btc_futures_trade_card.DEFAULT_GUARDIAN_CONFIG)
    validation_results = btc_futures_trade_card.evaluate_cards([enriched], schema, guardian_config)
    if validation_results[0]["guardian"]["decision"] != "pass_manual_review_only":
        blocks.append("guardian_not_clean_pass")
    if validation_results[0]["schema"]["schema_errors"]:
        blocks.append("schema_errors_present")
    blocks = sorted(set(blocks))

    decision = "blocked_live_review_only" if blocks else "manual_review_candidate_no_trade_permission"
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    card_path = out_prefix.with_suffix(".enriched.card.json")
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    write_json(card_path, enriched)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "public_market_data_card_enrichment",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "input_card": str(input_path),
        "enriched_card_path": str(card_path),
        "decision": decision,
        "blocks": blocks,
        "fetch_errors": fetch_errors,
        "market_summary": summary,
        "enriched_card": enriched,
        "card_validation": {
            "card_count": 1,
            "schema_valid_count": sum(1 for item in validation_results if item["schema"]["schema_valid"]),
            "guardian_pass_count": sum(1 for item in validation_results if item["guardian"]["decision"] == "pass_manual_review_only"),
            "final_blocked_count": 1,
            "results": validation_results,
        },
        "can_trade": False,
    }
    write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": decision,
                "blocks": blocks,
                "enriched_card_path": str(card_path),
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
