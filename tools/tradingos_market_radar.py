#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WATCHTOWER_SCHEMA = "tradingos.watchtower.v1"
WATCHTOWER_VERSION = "1.1.0"
LIQUIDITY_SCHEMA = "tradingos.liquidity_lens.v1"
LIQUIDITY_VERSION = "1.1.0"
SCHEMA = "tradingos.market_radar.v1"
VERSION = "1.1.0"
MAX_CAPTURE_SKEW_SECONDS = 120
EXPECTED_WATCHTOWER_PRODUCER_SHA256 = "4bef17bf4d308833847aba7d3f8c9d9cc21563cc9f7bf8a097b307bd0263095c"

_TFS = ("1h", "4h", "1d")
_ALLOWED_BIASES = {"WATCH_LONG", "WATCH_SHORT", "NO_ACTION"}
_ALLOWED_TF_STATES = {"LONG", "SHORT", "NEUTRAL"}
_ALLOWED_CONFLICTS = {None, "HTF_LTF_CONFLICT", "LTF_SPLIT"}
_ALLOWED_LIQ_QUALITIES = {"PASS", "PARTIAL"}
_ALLOWED_LIQ_STATES = {"BID_HEAVY", "ASK_HEAVY", "BALANCED", "INSUFFICIENT_DEPTH_COVERAGE"}
_ALLOWED_LIQ_FLAGS = {
    "EXTREME_DEPTH_IMBALANCE",
    "INSUFFICIENT_DEPTH_COVERAGE",
    "NEAR_BID_WALL",
    "NEAR_ASK_WALL",
    "WIDE_SPREAD",
}
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

WATCHTOWER_SAFETY = {
    "read_only": True,
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
}
LIQUIDITY_SAFETY = {
    "visible_book_only": True,
    "liquidation_map": False,
    "hidden_liquidity_inferred": False,
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
}
RADAR_SAFETY = {
    "read_only": True,
    "network_fetch": False,
    "signals_allowed": False,
    "orders_allowed": False,
    "can_trade": False,
    "capital_permission": "DENY",
}


def finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number: {field}") from exc
    if not math.isfinite(number):
        raise ValueError(f"non-finite number: {field}")
    return number


def bounded(value: Any, field: str, low: float, high: float) -> float:
    number = finite(value, field)
    if number < low or number > high:
        raise ValueError(f"out-of-range number: {field}")
    return number


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: timestamp must be non-empty string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field}: invalid timestamp") from exc
    if dt.tzinfo is None:
        raise ValueError(f"{field}: timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def stable_sha256(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("report is not canonically serializable") from exc
    return hashlib.sha256(raw).hexdigest()


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field}: invalid sha256")
    return value


def validate_symbol(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SYMBOL_RE.fullmatch(value):
        raise ValueError(f"{field}: invalid symbol")
    return value


def _index(rows: Any, field: str) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{field}: must be non-empty list")
    order: list[str] = []
    out: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{field}[{index}]: invalid row")
        symbol = validate_symbol(row.get("symbol"), f"{field}[{index}].symbol")
        if symbol in out:
            raise ValueError(f"{field}: duplicate symbol {symbol}")
        order.append(symbol)
        out[symbol] = row
    return order, out


def _validate_watchtower(report: Any) -> tuple[datetime, list[str], dict[str, dict[str, Any]]]:
    if not isinstance(report, dict):
        raise ValueError("watchtower must be object")
    if report.get("schema") != WATCHTOWER_SCHEMA or report.get("version") != WATCHTOWER_VERSION:
        raise ValueError("unsupported watchtower contract")
    if report.get("safety") != WATCHTOWER_SAFETY:
        raise ValueError("watchtower: unsafe permissions")
    captured = parse_time(report.get("captured_at"), "watchtower.captured_at")
    order, rows = _index(report.get("matrix"), "watchtower.matrix")
    symbols = report.get("symbols")
    if symbols != order:
        raise ValueError("watchtower symbols must exactly match matrix order")

    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("watchtower provenance missing")
    if provenance.get("producer") != "tools/tradingos_watchtower.py":
        raise ValueError("watchtower producer mismatch")
    producer_sha256 = require_sha256(provenance.get("producer_sha256"), "watchtower.producer_sha256")
    if producer_sha256 != EXPECTED_WATCHTOWER_PRODUCER_SHA256:
        raise ValueError("watchtower producer sha256 mismatch")
    require_sha256(provenance.get("capture_sha256"), "watchtower.capture_sha256")

    for symbol, row in rows.items():
        bias = row.get("bias")
        if bias not in _ALLOWED_BIASES:
            raise ValueError(f"{symbol}: invalid watchtower bias")
        conflict = row.get("conflict")
        if conflict not in _ALLOWED_CONFLICTS:
            raise ValueError(f"{symbol}: invalid watchtower conflict")
        attention = bounded(row.get("attention_score"), f"{symbol}.watchtower_attention", 0.0, 100.0)
        _ = attention
        confluence = finite(row.get("weighted_confluence"), f"{symbol}.weighted_confluence")
        if confluence < -6 or confluence > 6 or not float(confluence).is_integer():
            raise ValueError(f"{symbol}: invalid weighted confluence")
        if row.get("can_trade") is not False:
            raise ValueError(f"{symbol}: watchtower row can_trade drift")
        tfs = row.get("timeframes")
        if not isinstance(tfs, dict) or set(tfs) != set(_TFS):
            raise ValueError(f"{symbol}: invalid timeframe map")
        for tf in _TFS:
            tf_row = tfs[tf]
            if not isinstance(tf_row, dict) or tf_row.get("state") not in _ALLOWED_TF_STATES:
                raise ValueError(f"{symbol}.{tf}: invalid timeframe state")
            finite(tf_row.get("score"), f"{symbol}.{tf}.score")
        if bias == "WATCH_LONG" and (confluence < 4 or conflict is not None):
            raise ValueError(f"{symbol}: WATCH_LONG inconsistent with confluence/conflict")
        if bias == "WATCH_SHORT" and (confluence > -4 or conflict is not None):
            raise ValueError(f"{symbol}: WATCH_SHORT inconsistent with confluence/conflict")
        if bias == "NO_ACTION" and conflict is None and abs(confluence) >= 4:
            raise ValueError(f"{symbol}: NO_ACTION inconsistent with clear confluence")

    cross = report.get("cross_asset")
    if not isinstance(cross, dict) or cross.get("top_attention") != order[0]:
        raise ValueError("watchtower top_attention mismatch")
    return captured, order, rows


def _validate_wall(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{field}: wall must be object or null")
    if validate_symbol(value.get("symbol", field.split(".")[0]), f"{field}.symbol") is None:
        raise ValueError(f"{field}: invalid wall symbol")
    bounded(value.get("distance_bps"), f"{field}.distance_bps", 0.0, 1_000_000.0)
    bounded(value.get("notional"), f"{field}.notional", 0.0, 1e30)


def _validate_liquidity(report: Any) -> tuple[datetime, list[str], dict[str, dict[str, Any]]]:
    if not isinstance(report, dict):
        raise ValueError("liquidity must be object")
    if report.get("schema") != LIQUIDITY_SCHEMA or report.get("version") != LIQUIDITY_VERSION:
        raise ValueError("unsupported liquidity contract")
    if report.get("safety") != LIQUIDITY_SAFETY:
        raise ValueError("liquidity: unsafe permissions")
    captured = parse_time(report.get("captured_at"), "liquidity.captured_at")
    order, rows = _index(report.get("matrix"), "liquidity.matrix")
    if report.get("top_attention") != order[0]:
        raise ValueError("liquidity top_attention mismatch")

    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("liquidity provenance missing")
    require_sha256(provenance.get("capture_sha256"), "liquidity.capture_sha256")
    if provenance.get("books_exactly_bound_to_symbols") is not True or provenance.get("timestamp_timezone_required") is not True:
        raise ValueError("liquidity provenance binding missing")

    for symbol, row in rows.items():
        quality = row.get("quality")
        state = row.get("state")
        if quality not in _ALLOWED_LIQ_QUALITIES or state not in _ALLOWED_LIQ_STATES:
            raise ValueError(f"{symbol}: invalid liquidity state/quality")
        if quality == "PASS" and state == "INSUFFICIENT_DEPTH_COVERAGE":
            raise ValueError(f"{symbol}: PASS cannot be insufficient coverage")
        if quality == "PARTIAL" and state != "INSUFFICIENT_DEPTH_COVERAGE":
            raise ValueError(f"{symbol}: PARTIAL must fail closed")
        bounded(row.get("attention_score"), f"{symbol}.liquidity_attention", 0.0, 100.0)
        bounded(row.get("spread_bps"), f"{symbol}.spread_bps", 0.0, 1_000_000.0)
        flags = row.get("flags")
        if not isinstance(flags, list) or any(flag not in _ALLOWED_LIQ_FLAGS for flag in flags) or len(flags) != len(set(flags)):
            raise ValueError(f"{symbol}: invalid liquidity flags")
        _validate_wall(row.get("nearest_bid_wall"), f"{symbol}.nearest_bid_wall")
        _validate_wall(row.get("nearest_ask_wall"), f"{symbol}.nearest_ask_wall")
        if row.get("can_trade") is not False:
            raise ValueError(f"{symbol}: liquidity row can_trade drift")
    return captured, order, rows


def _row(watch: dict[str, Any], liquidity: dict[str, Any]) -> dict[str, Any]:
    symbol = watch["symbol"]
    bias = watch["bias"]
    quality = liquidity["quality"]
    state = liquidity["state"]
    flags = set(liquidity["flags"])
    base_attention = bounded(watch["attention_score"], f"{symbol}.watchtower_attention", 0.0, 100.0)
    liquidity_attention = bounded(liquidity["attention_score"], f"{symbol}.liquidity_attention", 0.0, 100.0)

    vetoes: list[str] = []
    notes: list[str] = []
    if quality != "PASS":
        notes.append("LIQUIDITY_CONTEXT_PARTIAL")
    else:
        if bias == "WATCH_LONG" and state == "ASK_HEAVY":
            vetoes.append("MICROSTRUCTURE_OPPOSES_LONG")
        if bias == "WATCH_SHORT" and state == "BID_HEAVY":
            vetoes.append("MICROSTRUCTURE_OPPOSES_SHORT")
        if bias == "WATCH_LONG" and "NEAR_ASK_WALL" in flags:
            vetoes.append("NEAR_ASK_WALL_FRICTION")
        if bias == "WATCH_SHORT" and "NEAR_BID_WALL" in flags:
            vetoes.append("NEAR_BID_WALL_FRICTION")

    if quality == "PASS":
        priority = min(100.0, base_attention * 0.75 + liquidity_attention * 0.25 + (10.0 if vetoes else 0.0))
    else:
        priority = base_attention

    if bias == "NO_ACTION":
        decision_quality = "BLOCKED_BY_CONFLUENCE" if watch.get("conflict") else "NO_ACTION"
    elif vetoes:
        decision_quality = "CAUTION"
    elif quality == "PASS":
        decision_quality = "CLEAR"
    else:
        decision_quality = "CONTEXT_PARTIAL"

    return {
        "symbol": symbol,
        "bias": bias,
        "decision_quality": decision_quality,
        "priority_score": round(priority, 2),
        "timeframes": {tf: watch["timeframes"][tf]["state"] for tf in _TFS},
        "confluence": watch["weighted_confluence"],
        "watchtower_conflict": watch.get("conflict"),
        "liquidity": {
            "quality": quality,
            "state": state,
            "spread_bps": liquidity["spread_bps"],
            "nearest_bid_wall": liquidity.get("nearest_bid_wall"),
            "nearest_ask_wall": liquidity.get("nearest_ask_wall"),
        },
        "vetoes": sorted(set(vetoes)),
        "notes": sorted(set(notes)),
        "can_trade": False,
    }


def build_radar(watchtower: dict[str, Any], liquidity: dict[str, Any]) -> dict[str, Any]:
    watch_time, watch_order, watch_rows = _validate_watchtower(watchtower)
    liq_time, liq_order, liq_rows = _validate_liquidity(liquidity)
    if set(watch_order) != set(liq_order):
        raise ValueError("watchtower/liquidity symbol sets must exactly match")
    skew = abs((watch_time - liq_time).total_seconds())
    if skew > MAX_CAPTURE_SKEW_SECONDS:
        raise ValueError("watchtower/liquidity capture skew exceeds limit")

    rows = [_row(watch_rows[symbol], liq_rows[symbol]) for symbol in watch_order]
    rows.sort(key=lambda row: (-row["priority_score"], row["symbol"]))
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "watchtower_captured_at": watchtower["captured_at"],
        "liquidity_captured_at": liquidity["captured_at"],
        "capture_skew_seconds": round(skew, 3),
        "symbols": [row["symbol"] for row in rows],
        "matrix": rows,
        "top_priority": rows[0]["symbol"],
        "provenance": {
            "watchtower_schema": WATCHTOWER_SCHEMA,
            "watchtower_version": WATCHTOWER_VERSION,
            "watchtower_report_sha256": stable_sha256(watchtower),
            "watchtower_capture_sha256": watchtower["provenance"]["capture_sha256"],
            "watchtower_producer_sha256": watchtower["provenance"]["producer_sha256"],
            "liquidity_schema": LIQUIDITY_SCHEMA,
            "liquidity_version": LIQUIDITY_VERSION,
            "liquidity_report_sha256": stable_sha256(liquidity),
            "liquidity_capture_sha256": liquidity["provenance"]["capture_sha256"],
            "symbol_sets_exactly_bound": True,
            "max_capture_skew_seconds": MAX_CAPTURE_SKEW_SECONDS,
        },
        "contract": {
            "watchtower_bias_is_authoritative": True,
            "liquidity_can_create_directional_bias": False,
            "liquidity_role": "attention + friction/veto context only when full-depth quality PASS",
            "partial_liquidity_priority_effect": "none",
            "partial_liquidity_veto_effect": "none",
            "priority_formula": "75% watchtower attention + 25% liquidity attention + 10 veto bump only when liquidity quality PASS; otherwise watchtower attention only",
        },
        "safety": dict(RADAR_SAFETY),
    }


def esc(value: Any) -> str:
    return html.escape(str(value))


def validate_report_for_render(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("schema") != SCHEMA or report.get("version") != VERSION:
        raise ValueError("unsupported market radar report")
    if report.get("safety") != RADAR_SAFETY:
        raise ValueError("unsafe market radar report")
    matrix = report.get("matrix")
    if not isinstance(matrix, list) or not matrix:
        raise ValueError("market radar matrix missing")
    symbols = report.get("symbols")
    if symbols != [row.get("symbol") for row in matrix if isinstance(row, dict)]:
        raise ValueError("market radar symbol/matrix identity mismatch")
    if report.get("top_priority") != matrix[0].get("symbol"):
        raise ValueError("market radar top_priority mismatch")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("market radar provenance missing")
    for key in (
        "watchtower_report_sha256",
        "watchtower_capture_sha256",
        "watchtower_producer_sha256",
        "liquidity_report_sha256",
        "liquidity_capture_sha256",
    ):
        require_sha256(provenance.get(key), f"market_radar.{key}")
    for index, row in enumerate(matrix):
        if not isinstance(row, dict):
            raise ValueError(f"market radar row {index} invalid")
        validate_symbol(row.get("symbol"), f"market_radar.matrix[{index}].symbol")
        if row.get("bias") not in _ALLOWED_BIASES:
            raise ValueError("market radar bias invalid")
        bounded(row.get("priority_score"), "market_radar.priority_score", 0.0, 100.0)
        if row.get("can_trade") is not False:
            raise ValueError("market radar row can_trade drift")
    return report


def render_html(report: dict[str, Any]) -> str:
    report = validate_report_for_render(report)
    cards: list[str] = []
    for row in report["matrix"]:
        tf_html = "".join(
            f'<span class="{row["timeframes"][tf].lower()}"><small>{tf}</small><b>{row["timeframes"][tf]}</b></span>'
            for tf in _TFS
        )
        context = " · ".join(row["vetoes"] or row["notes"] or ["no friction veto"])
        liq = row["liquidity"]
        cards.append(
            f'<article><header><div><h2>{esc(row["symbol"])}</h2><strong>{esc(row["bias"])}</strong></div><em>{row["priority_score"]:.0f}</em></header>'
            f'<div class="tfs">{tf_html}</div><div class="line"><span>QUALITY</span><b>{esc(row["decision_quality"])}</b></div>'
            f'<div class="line"><span>LIQUIDITY</span><b>{esc(liq["state"])} · {esc(liq["quality"])}</b></div><footer>{esc(context)}</footer></article>'
        )
    css = '*{box-sizing:border-box}body{margin:0;background:#061019;color:#f5f8fb;font:14px system-ui}main{max-width:1100px;margin:auto;padding:26px}.k{color:#69d9ff;font-size:11px;letter-spacing:.16em;font-weight:800}h1{font-size:52px;margin:4px 0;letter-spacing:-3px}.sub,small,footer,.line span{color:#8da4b7}article{background:#0d1823;border:1px solid #263746;border-radius:18px;padding:18px;margin:14px 0}header{display:flex;justify-content:space-between}h2{margin:0;font-size:28px}strong{color:#dfeaf2}em{font-style:normal;font-size:32px;font-weight:800}.tfs{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}.tfs span{padding:10px;background:#09141e;border:1px solid #263746;border-radius:10px}.tfs small,.tfs b{display:block}.long{color:#80f28b}.short{color:#ff7c7c}.neutral{color:#ffc96b}.line{display:flex;justify-content:space-between;border-top:1px solid #1c2b38;padding:10px 0}footer{margin-top:6px}@media(max-width:600px){h1{font-size:38px}}'
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>TradingOS Market Radar</title><style>{css}</style></head><body><main><div class="k">TRADINGOS · MARKET RADAR</div>'
        '<h1>Scan → Context → Attention</h1><div class="sub">Liquidity may add friction/veto context but cannot create a directional bias.</div>'
        f'<div class="sub">dependency skew ≤ {MAX_CAPTURE_SKEW_SECONDS}s · partial liquidity cannot change priority or vetoes</div>'
        f'{"".join(cards)}<div class="sub">offline=true · network_fetch=false · signals=false · orders=false · can_trade=false · capital_permission=DENY</div></main></body></html>'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Join canonical TradingOS Watchtower and Liquidity Lens reports into an offline Market Radar")
    parser.add_argument("--watchtower", required=True, type=Path)
    parser.add_argument("--liquidity", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        watchtower = json.loads(args.watchtower.read_text(encoding="utf-8-sig"))
        liquidity = json.loads(args.liquidity.read_text(encoding="utf-8-sig"))
        report = build_radar(watchtower, liquidity)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.out_dir / "market_radar.json"
        html_path = args.out_dir / "market_radar.html"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        html_path.write_text(render_html(report), encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "ERROR", "error": str(exc), "can_trade": False}, indent=2))
        return 2
    print(json.dumps({"result": "PASS", "top_priority": report["top_priority"], "outputs": {"json": str(json_path), "html": str(html_path)}, "can_trade": False, "capital_permission": "DENY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
