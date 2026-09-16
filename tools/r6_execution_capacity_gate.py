#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
R82_PATH = ROOT / "tools" / "binance_spot_event_time_quote_collector.py"
SPEC = importlib.util.spec_from_file_location("r94_r82", R82_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R82 quote collector")
r82 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = r82
SPEC.loader.exec_module(r82)

SCHEMA = "tradingos.r94_execution_capacity.v1"
BOOK_SCHEMA = "tradingos.r94_synchronized_depth_book.v1"
FILTER_SCHEMA = "tradingos.r94_binance_spot_filters.v1"
CONTRACT_ID = "R94_EXECUTION_CAPACITY_GATE_V1_20260916"
RESEARCH_EQUITY_USDT = Decimal("10000")
MAX_GROSS_MULTIPLE = Decimal("1")
MAX_IMPACT_BPS = Decimal("2")  # inherits frozen R91 slippage-per-side assumption
MAX_CAPTURE_NOTIONAL_USDT = Decimal("20000")
MAX_CAPTURE_LEVELS = 1000
CAPACITY_CLOSE_TIMEOUT_S = 0.1
EXCHANGE_INFO_HOST = "data-api.binance.vision"
EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"
MAX_HTTP_BYTES = 2_000_000


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def dec(value: Any, name: str, *, allow_zero: bool = False) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{name}") from exc
    if not out.is_finite() or out < 0 or (out == 0 and not allow_zero):
        raise ValueError(f"invalid_{name}")
    return out


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "contract_id": CONTRACT_ID, "status": "FAIL_CLOSED",
            "reason": reason, "details": details, "can_trade": False,
            "capital_permission": "DENY"}
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, "redirects forbidden", headers, fp)


def exchange_info_url(symbol: str) -> str:
    symbol = r82.validate_symbol(symbol)
    query = urllib.parse.urlencode({"symbol": symbol})
    return urllib.parse.urlunsplit(("https", EXCHANGE_INFO_HOST, EXCHANGE_INFO_PATH, query, ""))


def _fetch_json(url: str, timeout_s: float = 5.0) -> Any:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != EXCHANGE_INFO_HOST or parsed.port is not None:
        raise ValueError("exchange_info_url_not_allowlisted")
    if parsed.path != EXCHANGE_INFO_PATH or parsed.fragment:
        raise ValueError("exchange_info_path_invalid")
    if not math.isfinite(float(timeout_s)) or timeout_s <= 0 or timeout_s > 30:
        raise ValueError("timeout_invalid")
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "TradingOS-R94/1.0", "Accept": "application/json"})
    with opener.open(req, timeout=timeout_s) as response:  # nosec B310 - fixed HTTPS allowlist
        if getattr(response, "status", None) != 200 or response.geturl() != url:
            raise ValueError("exchange_info_http_invalid")
        body = response.read(MAX_HTTP_BYTES + 1)
    if len(body) > MAX_HTTP_BYTES:
        raise ValueError("exchange_info_too_large")
    return json.loads(body.decode("utf-8"))
def fetch_symbol_rules(symbol: str) -> dict[str, Any]:
    symbol = r82.validate_symbol(symbol)
    raw = _fetch_json(exchange_info_url(symbol))
    symbols = raw.get("symbols") if isinstance(raw, dict) else None
    if not isinstance(symbols, list) or len(symbols) != 1 or not isinstance(symbols[0], dict):
        raise ValueError("exchange_info_symbol_shape_invalid")
    item = symbols[0]
    if item.get("symbol") != symbol or item.get("status") != "TRADING" or item.get("quoteAsset") != "USDT":
        raise ValueError("symbol_not_trading_usdt")
    if item.get("isSpotTradingAllowed") is False:
        raise ValueError("spot_trading_not_allowed")
    filters = item.get("filters")
    if not isinstance(filters, list):
        raise ValueError("filters_missing")
    by_type = {f.get("filterType"): f for f in filters if isinstance(f, dict) and isinstance(f.get("filterType"), str)}
    price = by_type.get("PRICE_FILTER")
    lot = by_type.get("LOT_SIZE")
    market = by_type.get("MARKET_LOT_SIZE")
    if not all(isinstance(x, dict) for x in (price, lot)):
        raise ValueError("mandatory_filters_missing")
    tick = dec(price.get("tickSize"), "tickSize")
    lot_step = dec(lot.get("stepSize"), "lot_step")
    market_step = dec(market.get("stepSize"), "market_step", allow_zero=True) if isinstance(market, dict) else Decimal("0")
    base_precision = item.get("baseAssetPrecision")
    if type(base_precision) is not int or base_precision < 0 or base_precision > 18:
        raise ValueError("base_asset_precision_invalid")
    precision_quantum = Decimal(1).scaleb(-base_precision)
    qty_step = market_step if market_step > 0 else max(lot_step, precision_quantum)
    lot_min = dec(lot.get("minQty"), "lot_min_qty", allow_zero=True)
    lot_max = dec(lot.get("maxQty"), "lot_max_qty")
    market_min = dec(market.get("minQty"), "market_min_qty", allow_zero=True) if isinstance(market, dict) else Decimal("0")
    market_max = dec(market.get("maxQty"), "market_max_qty", allow_zero=True) if isinstance(market, dict) else Decimal("0")
    min_qty = max(lot_min, market_min)
    max_qty = min(lot_max, market_max) if market_max > 0 else lot_max
    min_notional = Decimal("0")
    max_notional: Decimal | None = None
    nf = by_type.get("NOTIONAL")
    mn = by_type.get("MIN_NOTIONAL")
    if isinstance(nf, dict):
        if nf.get("applyMinToMarket") is True:
            min_notional = dec(nf.get("minNotional"), "min_notional", allow_zero=True)
        if nf.get("applyMaxToMarket") is True:
            max_notional = dec(nf.get("maxNotional"), "max_notional")
    elif isinstance(mn, dict) and mn.get("applyToMarket") is True:
        min_notional = dec(mn.get("minNotional"), "min_notional", allow_zero=True)
    out = {
        "schema": FILTER_SCHEMA, "symbol": symbol, "status": "TRADING",
        "tick_size": str(tick), "qty_step": str(qty_step), "min_qty": str(min_qty),
        "max_qty": str(max_qty), "min_notional": str(min_notional),
        "max_notional": None if max_notional is None else str(max_notional),
        "base_asset_precision": base_precision, "source": "binance_spot_exchangeInfo",
        "received_at_ms": int(time.time() * 1000), "raw_sha256": sha256_json(raw),
    }
    out["provenance_sha256"] = sha256_json(out)
    return out
def _bounded_levels(book: dict[Decimal, Decimal], *, reverse: bool) -> tuple[list[list[str]], Decimal]:
    levels: list[list[str]] = []
    cumulative = Decimal("0")
    for price in sorted(book, reverse=reverse):
        qty = book[price]
        if qty <= 0:
            continue
        levels.append([str(price), str(qty)])
        cumulative += price * qty
        if len(levels) >= MAX_CAPTURE_LEVELS or cumulative >= MAX_CAPTURE_NOTIONAL_USDT:
            break
    if not levels:
        raise ValueError("capacity_book_empty")
    return levels, cumulative


def _capacity_book(book: Any, quote: dict[str, Any]) -> dict[str, Any]:
    bids, bid_notional = _bounded_levels(book.bids, reverse=True)
    asks, ask_notional = _bounded_levels(book.asks, reverse=False)
    out = {
        "schema": BOOK_SCHEMA, "symbol": quote["symbol"],
        "event_time_ms": quote["event_time_ms"], "decision_time_ms": quote["decision_time_ms"],
        "snapshot_update_id": quote["snapshot_update_id"], "final_update_id": quote["final_update_id"],
        "quote_provenance_sha256": quote["provenance_sha256"],
        "bid_levels": bids, "ask_levels": asks,
        "captured_bid_notional": str(bid_notional), "captured_ask_notional": str(ask_notional),
        "max_capture_notional_usdt": str(MAX_CAPTURE_NOTIONAL_USDT),
        "can_trade": False, "capital_permission": "DENY",
    }
    out["provenance_sha256"] = sha256_json(out)
    return out
async def _capture_once(symbol: str, max_age_ms: int = 2000, timeout_s: float = 10.0) -> tuple[dict[str, Any], dict[str, Any]]:
    symbol = r82.validate_symbol(symbol)
    stream = f"{symbol.lower()}@depth@100ms"
    ws_url = r82.WS_URL_TEMPLATE.format(stream=stream)
    async with r82.websockets.connect(
        ws_url, ping_interval=None, open_timeout=timeout_s, close_timeout=CAPACITY_CLOSE_TIMEOUT_S,
        max_size=r82.MAX_RAW_FRAME_BYTES, max_queue=4096,
    ) as websocket:
        snapshot = await asyncio.to_thread(
            r82.default_fetch_json, r82.snapshot_url(symbol, 1000), timeout_s=timeout_s,
        )
        book = r82.SpotDepthBook(symbol=symbol, snapshot=snapshot)
        for _ in range(500):
            raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            event = r82.parse_depth_event(
                raw_message, symbol=symbol, received_at_ms=r82.now_ms(), max_age_ms=max_age_ms,
            )
            quote = book.apply(event)
            if quote is None:
                continue
            decision_time_ms = r82.now_ms()
            quote["decision_time_ms"] = decision_time_ms
            quote["decision_age_ms"] = decision_time_ms - quote["event_time_ms"]
            quote["events_applied"] = book.events_applied
            quote["can_trade"] = False
            quote["capital_permission"] = "DENY"
            quote["provenance_sha256"] = r82.stable_sha256(
                {k: v for k, v in quote.items() if k != "provenance_sha256"}
            )
            r82.validate_quote_at_decision(quote, decision_time_ms=decision_time_ms, max_age_ms=max_age_ms)
            capacity = _capacity_book(book, quote)
            return quote, capacity
    raise r82.SpotQuoteReject("no_synchronized_capacity_book_within_event_budget")


async def capture_quote_and_capacity(
    symbol: str, *, max_age_ms: int = 2000, timeout_s: float = 10.0, max_restarts: int = 2
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(max_restarts) is not int or max_restarts < 0 or max_restarts > 5:
        raise ValueError("max_restarts_invalid")
    last: Exception | None = None
    for attempt in range(max_restarts + 1):
        try:
            quote, book = await _capture_once(symbol, max_age_ms=max_age_ms, timeout_s=timeout_s)
            quote = dict(quote)
            quote["restart_count"] = attempt
            quote["provenance_sha256"] = r82.stable_sha256(
                {k: v for k, v in quote.items() if k != "provenance_sha256"}
            )
            book = dict(book)
            book["quote_provenance_sha256"] = quote["provenance_sha256"]
            book["restart_count"] = attempt
            book["provenance_sha256"] = sha256_json(
                {k: v for k, v in book.items() if k != "provenance_sha256"}
            )
            return quote, book
        except (asyncio.TimeoutError, OSError, r82.SpotQuoteReject) as exc:
            last = exc
            if attempt >= max_restarts:
                raise
    raise RuntimeError(f"capacity_restart_budget_exhausted:{last}")
def _validate_bound(value: Any, expected: str, name: str) -> None:
    if value != expected:
        raise ValueError(f"{name}_provenance_mismatch")


def validate_capacity_book(book: dict[str, Any], quote: dict[str, Any]) -> None:
    if not isinstance(book, dict) or book.get("schema") != BOOK_SCHEMA:
        raise ValueError("capacity_book_schema_invalid")
    if book.get("symbol") != quote.get("symbol"):
        raise ValueError("capacity_book_symbol_mismatch")
    for key in ("event_time_ms", "decision_time_ms", "snapshot_update_id", "final_update_id"):
        if book.get(key) != quote.get(key):
            raise ValueError(f"capacity_book_{key}_mismatch")
    _validate_bound(book.get("quote_provenance_sha256"), quote.get("provenance_sha256"), "quote")
    supplied = book.get("provenance_sha256")
    basis = {k: v for k, v in book.items() if k != "provenance_sha256"}
    if supplied != sha256_json(basis):
        raise ValueError("capacity_book_provenance_mismatch")


def validate_rules(rules: dict[str, Any], symbol: str) -> None:
    if not isinstance(rules, dict) or rules.get("schema") != FILTER_SCHEMA or rules.get("symbol") != symbol:
        raise ValueError("symbol_rules_invalid")
    supplied = rules.get("provenance_sha256")
    basis = {k: v for k, v in rules.items() if k != "provenance_sha256"}
    if supplied != sha256_json(basis):
        raise ValueError("symbol_rules_provenance_mismatch")


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("step_invalid")
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _round_price(value: Decimal, tick: Decimal, *, up: bool) -> Decimal:
    if tick <= 0:
        raise ValueError("tick_invalid")
    mode = ROUND_CEILING if up else ROUND_FLOOR
    return (value / tick).to_integral_value(rounding=mode) * tick
def _vwap(levels: Any, qty: Decimal) -> tuple[Decimal, Decimal]:
    if qty <= 0 or not isinstance(levels, list):
        raise ValueError("vwap_input_invalid")
    remaining = qty
    cost = Decimal("0")
    filled = Decimal("0")
    for row in levels:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("depth_level_invalid")
        price = dec(row[0], "depth_price")
        avail = dec(row[1], "depth_qty", allow_zero=True)
        if avail <= 0:
            continue
        take = min(avail, remaining)
        cost += price * take
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0 or filled <= 0:
        raise ValueError("insufficient_captured_depth")
    return cost / filled, filled


def _impact_bps(direction: str, best: Decimal, vwap: Decimal) -> Decimal:
    if direction == "BUY":
        if vwap < best:
            raise ValueError("buy_vwap_better_than_best_invalid")
        return (vwap / best - Decimal("1")) * Decimal("10000")
    if direction == "SELL":
        if vwap > best:
            raise ValueError("sell_vwap_better_than_best_invalid")
        return (Decimal("1") - vwap / best) * Decimal("10000")
    raise ValueError("impact_direction_invalid")


def _risk_state(runtime_state: dict[str, Any], symbol: str) -> dict[str, Any]:
    per = runtime_state.get("risk_state_by_symbol")
    risk = per.get(symbol) if isinstance(per, dict) else None
    if risk is None:
        risk = runtime_state.get("risk_state")
    if not isinstance(risk, dict):
        raise ValueError("risk_state_missing")
    return risk


def _direction(candidate: dict[str, Any]) -> str:
    row = candidate.get("row_values")
    if not isinstance(row, dict):
        raise ValueError("registration_row_missing")
    decision = row.get("CURRENT_Decision")
    if decision == "ENTER_LONG_AT_R85_DECISION_QUOTE":
        return "LONG"
    if decision == "ENTER_SHORT_AT_R92_DECISION_QUOTE":
        return "SHORT"
    raise ValueError("candidate_direction_unknown")
def evaluate_capacity(
    candidate: dict[str, Any], runtime_state: dict[str, Any], quote: dict[str, Any],
    book: dict[str, Any], rules: dict[str, Any],
) -> dict[str, Any]:
    try:
        if candidate.get("registration_candidate") is not True or candidate.get("ledger_write_authority") is not False:
            return fail("REGISTRATION_CANDIDATE_INVALID")
        row = candidate.get("row_values")
        if not isinstance(row, dict):
            return fail("REGISTRATION_ROW_MISSING")
        symbol = str(row.get("Symbol")) + "USDT"
        direction = _direction(candidate)
        if quote.get("symbol") != symbol:
            return fail("QUOTE_SYMBOL_MISMATCH")
        r82.validate_quote_at_decision(quote, decision_time_ms=quote.get("decision_time_ms"), max_age_ms=2000)
        validate_capacity_book(book, quote)
        validate_rules(rules, symbol)
        trigger = json.loads(row.get("CURRENT_Trigger_Spec", ""))
        if trigger.get("quote_provenance_sha256") != quote.get("provenance_sha256"):
            return fail("CANDIDATE_QUOTE_BINDING_MISMATCH")
        risk_state = _risk_state(runtime_state, symbol)
        cost = runtime_state.get("cost_model")
        if not isinstance(cost, dict):
            return fail("COST_MODEL_MISSING")
    except Exception as exc:
        return fail("CAPACITY_INPUT_INVALID", error=str(exc))
    try:
        proposed = dec(risk_state.get("proposed_risk_pct"), "proposed_risk_pct")
        fee_in = dec(cost.get("entry_fee_rate"), "entry_fee_rate", allow_zero=True)
        fee_out = dec(cost.get("exit_fee_rate"), "exit_fee_rate", allow_zero=True)
        slip_in = dec(cost.get("entry_slippage_bps"), "entry_slippage_bps", allow_zero=True)
        slip_out = dec(cost.get("exit_slippage_bps"), "exit_slippage_bps", allow_zero=True)
        if slip_in != MAX_IMPACT_BPS or slip_out != MAX_IMPACT_BPS:
            return fail("R91_SLIPPAGE_CONTRACT_MISMATCH", entry=str(slip_in), exit=str(slip_out))
        entry = dec(row.get("CURRENT_Entry_Price"), "current_entry_price")
        stop = dec(row.get("CURRENT_Stop"), "current_stop")
        target = dec(row.get("CURRENT_Target"), "current_target")
        tick = dec(rules.get("tick_size"), "tick_size")
        step = dec(rules.get("qty_step"), "qty_step")
        min_qty = dec(rules.get("min_qty"), "min_qty", allow_zero=True)
        max_qty = dec(rules.get("max_qty"), "max_qty")
        min_notional = dec(rules.get("min_notional"), "min_notional", allow_zero=True)
        max_notional_raw = rules.get("max_notional")
        max_notional = None if max_notional_raw is None else dec(max_notional_raw, "max_notional")
    except Exception as exc:
        return fail("CAPACITY_NUMERIC_INPUT_INVALID", error=str(exc))

    if proposed <= 0 or proposed > Decimal("1"):
        return fail("PROPOSED_RISK_OUT_OF_RANGE", proposed=str(proposed))
    if direction == "LONG":
        stop_exec = _round_price(stop, tick, up=False)
        target_exec = _round_price(target, tick, up=False)
        stop_eff = stop_exec * (Decimal("1") - slip_out / Decimal("10000"))
        target_eff = target_exec * (Decimal("1") - slip_out / Decimal("10000"))
        unit_risk = entry - stop_eff + fee_in * entry + fee_out * stop_eff
        unit_reward = target_eff - entry - fee_in * entry - fee_out * target_eff
    else:
        stop_exec = _round_price(stop, tick, up=True)
        target_exec = _round_price(target, tick, up=True)
        stop_eff = stop_exec * (Decimal("1") + slip_out / Decimal("10000"))
        target_eff = target_exec * (Decimal("1") + slip_out / Decimal("10000"))
        unit_risk = stop_eff - entry + fee_in * entry + fee_out * stop_eff
        unit_reward = entry - target_eff - fee_in * entry - fee_out * target_eff
    if unit_risk <= 0 or unit_reward <= 0:
        return fail("ROUNDED_NET_GEOMETRY_NONPOSITIVE", risk=str(unit_risk), reward=str(unit_reward))
    risk_budget = RESEARCH_EQUITY_USDT * proposed / Decimal("100")
    q_by_risk = risk_budget / unit_risk
    q_by_gross = RESEARCH_EQUITY_USDT * MAX_GROSS_MULTIPLE / entry
    q_cap = min(q_by_risk, q_by_gross, max_qty)
    if max_notional is not None:
        q_cap = min(q_cap, max_notional / entry)
    q = _floor_to_step(q_cap, step)
    if q <= 0 or q < min_qty:
        return fail("QUANTITY_BELOW_VENUE_MIN", quantity=str(q), min_qty=str(min_qty))
    notional = q * entry
    if notional < min_notional:
        return fail("NOTIONAL_BELOW_VENUE_MIN", notional=str(notional), min_notional=str(min_notional))
    actual_risk = q * unit_risk
    if actual_risk > risk_budget:
        return fail("RISK_BUDGET_EXCEEDED_AFTER_ROUNDING", actual=str(actual_risk), budget=str(risk_budget))

    try:
        best_bid = dec(quote.get("bid"), "best_bid")
        best_ask = dec(quote.get("ask"), "best_ask")
        first_bid = dec(book["bid_levels"][0][0], "book_best_bid")
        first_ask = dec(book["ask_levels"][0][0], "book_best_ask")
        if first_bid != best_bid or first_ask != best_ask:
            return fail("CAPACITY_BOOK_L1_MISMATCH")
        buy_vwap, _ = _vwap(book.get("ask_levels"), q)
        sell_vwap, _ = _vwap(book.get("bid_levels"), q)
        buy_impact = _impact_bps("BUY", best_ask, buy_vwap)
        sell_impact = _impact_bps("SELL", best_bid, sell_vwap)
    except Exception as exc:
        return fail("DEPTH_CAPACITY_UNPROVEN", error=str(exc), quantity=str(q))

    entry_impact = buy_impact if direction == "LONG" else sell_impact
    exit_impact = sell_impact if direction == "LONG" else buy_impact
    if entry_impact > MAX_IMPACT_BPS:
        return fail("ENTRY_DEPTH_IMPACT_EXCEEDS_MODEL", impact_bps=str(entry_impact), limit_bps=str(MAX_IMPACT_BPS))
    if exit_impact > MAX_IMPACT_BPS:
        return fail("EXIT_DEPTH_IMPACT_EXCEEDS_MODEL", impact_bps=str(exit_impact), limit_bps=str(MAX_IMPACT_BPS))
    net_rr = unit_reward / unit_risk
    actual_risk_pct = actual_risk / RESEARCH_EQUITY_USDT * Decimal("100")
    out = {
        "schema": SCHEMA, "contract_id": CONTRACT_ID, "status": "PASS",
        "reason": "EXECUTION_CAPACITY_PASS", "symbol": symbol, "direction": direction,
        "research_equity_usdt": str(RESEARCH_EQUITY_USDT),
        "research_scale_authority": "R5_BOOKKEEPING_EQUITY_10000_SCALE_ONLY_NO_SIGNAL_INHERITANCE",
        "proposed_risk_pct": str(proposed), "risk_budget_usdt": str(risk_budget),
        "quantity": str(q), "gross_notional_usdt": str(notional),
        "actual_planned_risk_usdt": str(actual_risk), "actual_planned_risk_pct": str(actual_risk_pct),
        "gross_equity_multiple": str(notional / RESEARCH_EQUITY_USDT),
        "entry_depth_vwap": str(buy_vwap if direction == "LONG" else sell_vwap),
        "exit_depth_vwap_proxy": str(sell_vwap if direction == "LONG" else buy_vwap),
        "entry_impact_bps": str(entry_impact), "exit_impact_bps": str(exit_impact),
        "impact_limit_bps": str(MAX_IMPACT_BPS),
        "venue_tick_size": str(tick), "venue_qty_step": str(step),
        "venue_min_notional": str(min_notional), "rounded_stop": str(stop_exec),
        "rounded_target": str(target_exec), "net_rr_after_venue_rounding": str(net_rr),
        "capacity_book_provenance_sha256": book["provenance_sha256"],
        "symbol_rules_provenance_sha256": rules["provenance_sha256"],
        "quote_provenance_sha256": quote["provenance_sha256"],
        "short_capacity_semantics": "PAPER_SELL_BUYBACK_ONLY_NO_LIVE_SPOT_SHORT_AUTHORITY" if direction == "SHORT" else None,
        "ledger_write_authority": False, "can_trade": False, "capital_permission": "DENY",
    }
    out["provenance_sha256"] = sha256_json(out)
    return out


def benchmark_full_equity_capacity(
    quote: dict[str, Any], book: dict[str, Any], rules: dict[str, Any],
    *, notional_usdt: Decimal = RESEARCH_EQUITY_USDT,
) -> dict[str, Any]:
    """Prove worst-case <=1x paper gross capacity in both directions."""
    try:
        if notional_usdt <= 0 or notional_usdt > RESEARCH_EQUITY_USDT * MAX_GROSS_MULTIPLE:
            return fail("BENCHMARK_NOTIONAL_OUT_OF_RANGE", notional=str(notional_usdt))
        symbol = quote.get("symbol")
        if not isinstance(symbol, str):
            return fail("BENCHMARK_SYMBOL_INVALID")
        r82.validate_quote_at_decision(
            quote, decision_time_ms=quote.get("decision_time_ms"), max_age_ms=2000,
        )
        validate_capacity_book(book, quote)
        validate_rules(rules, symbol)
        best_bid = dec(quote.get("bid"), "best_bid")
        best_ask = dec(quote.get("ask"), "best_ask")
        step = dec(rules.get("qty_step"), "qty_step")
        min_qty = dec(rules.get("min_qty"), "min_qty", allow_zero=True)
        max_qty = dec(rules.get("max_qty"), "max_qty")
        min_notional = dec(rules.get("min_notional"), "min_notional", allow_zero=True)
    except Exception as exc:
        return fail("BENCHMARK_INPUT_INVALID", error=str(exc))
    max_notional_raw = rules.get("max_notional")
    max_notional = None if max_notional_raw is None else dec(max_notional_raw, "max_notional")
    if max_notional is not None and notional_usdt > max_notional:
        return fail(
            "BENCHMARK_ABOVE_VENUE_MAX_NOTIONAL",
            requested=str(notional_usdt), max_notional=str(max_notional),
        )
    buy_qty = _floor_to_step(min(notional_usdt / best_ask, max_qty), step)
    sell_qty = _floor_to_step(min(notional_usdt / best_bid, max_qty), step)
    if buy_qty < min_qty or sell_qty < min_qty or buy_qty <= 0 or sell_qty <= 0:
        return fail(
            "BENCHMARK_QUANTITY_BELOW_VENUE_MIN",
            buy_qty=str(buy_qty), sell_qty=str(sell_qty), min_qty=str(min_qty),
        )
    buy_notional = buy_qty * best_ask
    sell_notional = sell_qty * best_bid
    if buy_notional < min_notional or sell_notional < min_notional:
        return fail(
            "BENCHMARK_NOTIONAL_BELOW_VENUE_MIN",
            buy_notional=str(buy_notional), sell_notional=str(sell_notional),
            min_notional=str(min_notional),
        )
    try:
        buy_vwap, _ = _vwap(book.get("ask_levels"), buy_qty)
        sell_vwap, _ = _vwap(book.get("bid_levels"), sell_qty)
        buy_impact = _impact_bps("BUY", best_ask, buy_vwap)
        sell_impact = _impact_bps("SELL", best_bid, sell_vwap)
    except Exception as exc:
        return fail("BENCHMARK_DEPTH_UNPROVEN", error=str(exc))
    if buy_impact > MAX_IMPACT_BPS or sell_impact > MAX_IMPACT_BPS:
        return fail(
            "BENCHMARK_IMPACT_EXCEEDS_MODEL",
            buy_impact_bps=str(buy_impact), sell_impact_bps=str(sell_impact),
            limit_bps=str(MAX_IMPACT_BPS),
        )
    out = {
        "schema": SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "PASS",
        "reason": "FULL_EQUITY_CAPACITY_BENCHMARK_PASS",
        "symbol": symbol,
        "benchmark_notional_usdt": str(notional_usdt),
        "buy_quantity": str(buy_qty),
        "sell_quantity": str(sell_qty),
        "buy_notional_usdt": str(buy_notional),
        "sell_notional_usdt": str(sell_notional),
        "buy_vwap": str(buy_vwap),
        "sell_vwap": str(sell_vwap),
        "buy_impact_bps": str(buy_impact),
        "sell_impact_bps": str(sell_impact),
        "impact_limit_bps": str(MAX_IMPACT_BPS),
        "capacity_book_provenance_sha256": book["provenance_sha256"],
        "symbol_rules_provenance_sha256": rules["provenance_sha256"],
        "quote_provenance_sha256": quote["provenance_sha256"],
        "can_trade": False,
        "capital_permission": "DENY",
    }
    out["provenance_sha256"] = sha256_json(out)
    return out
