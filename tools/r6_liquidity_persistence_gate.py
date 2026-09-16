from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("r95_r94_cap", ROOT / "tools" / "r6_execution_capacity_gate.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R94 capacity gate")
cap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cap
SPEC.loader.exec_module(cap)

SCHEMA = "tradingos.r95_liquidity_persistence.v1"
SEQUENCE_SCHEMA = "tradingos.r95_liquidity_persistence_sequence.v1"
CONTRACT_ID = "R95_LIQUIDITY_PERSISTENCE_GATE_V1_20260916"
REQUIRED_STATES = 3
MIN_EVENT_SPAN_MS = 200
MAX_EVENT_GAP_MS = 500
MAX_EVENT_SPAN_MS = 1000
MAX_AGE_MS = 2000
MAX_IMPACT_BPS = cap.MAX_IMPACT_BPS


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fail(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "FAIL_CLOSED",
        "reason": reason,
        "details": details,
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }


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


def _state_payload(book: Any, quote: dict[str, Any], index: int) -> dict[str, Any]:
    capacity = cap._capacity_book(book, quote)
    return {
        "index": index,
        "quote": quote,
        "book": capacity,
    }


async def _capture_once(symbol: str, timeout_s: float = 10.0) -> dict[str, Any]:
    symbol = cap.r82.validate_symbol(symbol)
    stream = f"{symbol.lower()}@depth@100ms"
    ws_url = cap.r82.WS_URL_TEMPLATE.format(stream=stream)
    states: list[dict[str, Any]] = []
    async with cap.r82.websockets.connect(
        ws_url,
        ping_interval=None,
        open_timeout=timeout_s,
        close_timeout=cap.CAPACITY_CLOSE_TIMEOUT_S,
        max_size=cap.r82.MAX_RAW_FRAME_BYTES,
        max_queue=4096,
    ) as websocket:
        snapshot = await asyncio.to_thread(
            cap.r82.default_fetch_json,
            cap.r82.snapshot_url(symbol, 1000),
            timeout_s=timeout_s,
        )
        book = cap.r82.SpotDepthBook(symbol=symbol, snapshot=snapshot)
        first_event_time: int | None = None
        last_event_time: int | None = None
        last_update_id: int | None = None
        for _ in range(800):
            raw = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
            event = cap.r82.parse_depth_event(
                raw,
                symbol=symbol,
                received_at_ms=cap.r82.now_ms(),
                max_age_ms=MAX_AGE_MS,
            )
            quote = book.apply(event)
            if quote is None:
                continue
            event_time = int(quote["event_time_ms"])
            final_update = int(quote["final_update_id"])
            if last_event_time is not None and event_time <= last_event_time:
                continue
            if last_update_id is not None and final_update <= last_update_id:
                continue
            if last_event_time is not None and event_time - last_event_time > MAX_EVENT_GAP_MS:
                states = []
                first_event_time = None
            decision = cap.r82.now_ms()
            quote["decision_time_ms"] = decision
            quote["decision_age_ms"] = decision - event_time
            quote["events_applied"] = book.events_applied
            quote["can_trade"] = False
            quote["capital_permission"] = "DENY"
            quote["provenance_sha256"] = cap.r82.stable_sha256(
                {k: v for k, v in quote.items() if k != "provenance_sha256"}
            )
            cap.r82.validate_quote_at_decision(
                quote,
                decision_time_ms=decision,
                max_age_ms=MAX_AGE_MS,
            )
            states.append(_state_payload(book, dict(quote), len(states)))
            first_event_time = event_time if first_event_time is None else first_event_time
            last_event_time = event_time
            last_update_id = final_update
            if len(states) >= REQUIRED_STATES:
                span = event_time - first_event_time
                if span >= MIN_EVENT_SPAN_MS:
                    break
        if len(states) < REQUIRED_STATES:
            raise cap.r82.SpotQuoteReject("persistence_state_count_not_met")
        if first_event_time is None or last_event_time is None:
            raise cap.r82.SpotQuoteReject("persistence_event_time_missing")
        span = last_event_time - first_event_time
        if span < MIN_EVENT_SPAN_MS:
            raise cap.r82.SpotQuoteReject("persistence_span_too_short")
        if span > MAX_EVENT_SPAN_MS:
            raise cap.r82.SpotQuoteReject("persistence_span_too_long")
        return {"symbol": symbol, "states": states, "event_span_ms": span}


async def capture_persistence_sequence(
    symbol: str,
    *,
    timeout_s: float = 10.0,
    max_restarts: int = 2,
) -> dict[str, Any]:
    if type(max_restarts) is not int or max_restarts < 0 or max_restarts > 5:
        raise ValueError("max_restarts_invalid")
    last: Exception | None = None
    for attempt in range(max_restarts + 1):
        try:
            payload = await _capture_once(symbol, timeout_s=timeout_s)
            normalized: list[dict[str, Any]] = []
            for item in payload["states"]:
                q = dict(item["quote"])
                q["restart_count"] = attempt
                q["provenance_sha256"] = cap.r82.stable_sha256(
                    {k: v for k, v in q.items() if k != "provenance_sha256"}
                )
                b = dict(item["book"])
                b["quote_provenance_sha256"] = q["provenance_sha256"]
                b["restart_count"] = attempt
                b["provenance_sha256"] = cap.sha256_json(
                    {k: v for k, v in b.items() if k != "provenance_sha256"}
                )
                normalized.append({"index": item["index"], "quote": q, "book": b})
            out = {
                "schema": SEQUENCE_SCHEMA,
                "contract_id": CONTRACT_ID,
                "symbol": payload["symbol"],
                "required_states": REQUIRED_STATES,
                "event_span_ms": payload["event_span_ms"],
                "states": normalized,
                "restart_count": attempt,
                "can_trade": False,
                "capital_permission": "DENY",
            }
            out["provenance_sha256"] = cap.sha256_json(out)
            return out
        except (asyncio.TimeoutError, OSError, cap.r82.SpotQuoteReject) as exc:
            last = exc
            if attempt >= max_restarts:
                raise
    raise RuntimeError(f"persistence_restart_budget_exhausted:{last}")


def _sequence_basis(sequence: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in sequence.items() if k != "provenance_sha256"}


def validate_sequence(sequence: dict[str, Any]) -> None:
    if not isinstance(sequence, dict) or sequence.get("schema") != SEQUENCE_SCHEMA:
        raise ValueError("persistence_sequence_schema_invalid")
    if sequence.get("provenance_sha256") != cap.sha256_json(_sequence_basis(sequence)):
        raise ValueError("persistence_sequence_provenance_mismatch")
    states = sequence.get("states")
    if not isinstance(states, list) or len(states) < REQUIRED_STATES:
        raise ValueError("persistence_state_count_invalid")
    times: list[int] = []
    update_ids: list[int] = []
    symbol = sequence.get("symbol")
    for index, state in enumerate(states):
        if not isinstance(state, dict) or state.get("index") != index:
            raise ValueError("persistence_state_index_invalid")
        quote = state.get("quote")
        book = state.get("book")
        if not isinstance(quote, dict) or not isinstance(book, dict):
            raise ValueError("persistence_state_payload_invalid")
        if quote.get("symbol") != symbol:
            raise ValueError("persistence_symbol_mismatch")
        cap.r82.validate_quote_at_decision(
            quote,
            decision_time_ms=quote.get("decision_time_ms"),
            max_age_ms=MAX_AGE_MS,
        )
        cap.validate_capacity_book(book, quote)
        times.append(int(quote["event_time_ms"]))
        update_ids.append(int(quote["final_update_id"]))
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("persistence_event_time_not_strict")
    if any(b <= a for a, b in zip(update_ids, update_ids[1:])):
        raise ValueError("persistence_update_id_not_strict")
    gaps = [b - a for a, b in zip(times, times[1:])]
    if any(gap > MAX_EVENT_GAP_MS for gap in gaps):
        raise ValueError("persistence_event_gap_too_large")
    span = times[-1] - times[0]
    if span < MIN_EVENT_SPAN_MS:
        raise ValueError("persistence_span_too_short")
    if span > MAX_EVENT_SPAN_MS:
        raise ValueError("persistence_span_too_long")
    if sequence.get("event_span_ms") != span:
        raise ValueError("persistence_span_binding_mismatch")


def _candidate_quantity(capacity_result: dict[str, Any]) -> Decimal:
    if not isinstance(capacity_result, dict) or capacity_result.get("status") != "PASS":
        raise ValueError("r94_capacity_not_pass")
    quantity = cap.dec(capacity_result.get("quantity"), "quantity")
    if quantity <= 0:
        raise ValueError("capacity_quantity_invalid")
    return quantity


def _state_impacts(state: dict[str, Any], quantity: Decimal) -> tuple[Decimal, Decimal]:
    quote = state["quote"]
    book = state["book"]
    best_bid = cap.dec(quote.get("bid"), "best_bid")
    best_ask = cap.dec(quote.get("ask"), "best_ask")
    buy_vwap, _ = cap._vwap(book.get("ask_levels"), quantity)
    sell_vwap, _ = cap._vwap(book.get("bid_levels"), quantity)
    return cap._impact_bps("BUY", best_ask, buy_vwap), cap._impact_bps("SELL", best_bid, sell_vwap)


def evaluate_persistence(
    candidate: dict[str, Any],
    capacity_result: dict[str, Any],
    sequence: dict[str, Any],
) -> dict[str, Any]:
    try:
        if candidate.get("registration_candidate") is not True:
            return fail("REGISTRATION_CANDIDATE_INVALID")
        if candidate.get("ledger_write_authority") is not False:
            return fail("LEDGER_WRITE_AUTHORITY_INVALID")
        validate_sequence(sequence)
        quantity = _candidate_quantity(capacity_result)
        direction = _direction(candidate)
        row = candidate.get("row_values")
        if not isinstance(row, dict):
            return fail("REGISTRATION_ROW_MISSING")
        symbol = str(row.get("Symbol")) + "USDT"
        if sequence.get("symbol") != symbol:
            return fail("PERSISTENCE_SYMBOL_MISMATCH")
        trigger = json.loads(row.get("CURRENT_Trigger_Spec", ""))
        final_state = sequence["states"][-1]
        final_quote = final_state["quote"]
        final_book = final_state["book"]
        if trigger.get("quote_provenance_sha256") != final_quote.get("provenance_sha256"):
            return fail("FINAL_QUOTE_BINDING_MISMATCH")
        if capacity_result.get("quote_provenance_sha256") != final_quote.get("provenance_sha256"):
            return fail("R94_FINAL_QUOTE_BINDING_MISMATCH")
        if capacity_result.get("capacity_book_provenance_sha256") != final_book.get("provenance_sha256"):
            return fail("R94_FINAL_BOOK_BINDING_MISMATCH")
        impacts: list[dict[str, str]] = []
        max_entry = Decimal("0")
        max_exit = Decimal("0")
        for state in sequence["states"]:
            buy_impact, sell_impact = _state_impacts(state, quantity)
            entry_impact = buy_impact if direction == "LONG" else sell_impact
            exit_impact = sell_impact if direction == "LONG" else buy_impact
            if entry_impact > MAX_IMPACT_BPS:
                return fail(
                    "PERSISTENT_ENTRY_IMPACT_EXCEEDS_MODEL",
                    index=state["index"],
                    impact_bps=str(entry_impact),
                    limit_bps=str(MAX_IMPACT_BPS),
                )
            if exit_impact > MAX_IMPACT_BPS:
                return fail(
                    "PERSISTENT_EXIT_IMPACT_EXCEEDS_MODEL",
                    index=state["index"],
                    impact_bps=str(exit_impact),
                    limit_bps=str(MAX_IMPACT_BPS),
                )
            max_entry = max(max_entry, entry_impact)
            max_exit = max(max_exit, exit_impact)
            impacts.append({
                "index": str(state["index"]),
                "entry_impact_bps": str(entry_impact),
                "exit_impact_bps": str(exit_impact),
            })
    except Exception as exc:
        return fail("PERSISTENCE_INPUT_INVALID", error=str(exc))
    out = {
        "schema": SCHEMA,
        "contract_id": CONTRACT_ID,
        "status": "PASS",
        "reason": "LIQUIDITY_PERSISTENCE_PASS",
        "symbol": sequence["symbol"],
        "direction": direction,
        "quantity": str(quantity),
        "state_count": len(sequence["states"]),
        "event_span_ms": sequence["event_span_ms"],
        "max_entry_impact_bps": str(max_entry),
        "max_exit_impact_bps": str(max_exit),
        "impact_limit_bps": str(MAX_IMPACT_BPS),
        "state_impacts": impacts,
        "sequence_provenance_sha256": sequence["provenance_sha256"],
        "final_quote_provenance_sha256": sequence["states"][-1]["quote"]["provenance_sha256"],
        "final_book_provenance_sha256": sequence["states"][-1]["book"]["provenance_sha256"],
        "ledger_write_authority": False,
        "can_trade": False,
        "capital_permission": "DENY",
    }
    out["provenance_sha256"] = cap.sha256_json(out)
    return out


def benchmark_full_equity_persistence(
    sequence: dict[str, Any], rules: dict[str, Any],
    *, notional_usdt: Decimal = cap.RESEARCH_EQUITY_USDT,
) -> dict[str, Any]:
    """Stress-only: final-decision 1x quantities must fit every persistence state."""
    try:
        validate_sequence(sequence)
        symbol=sequence.get("symbol"); cap.validate_rules(rules,symbol)
        if notional_usdt<=0 or notional_usdt>cap.RESEARCH_EQUITY_USDT*cap.MAX_GROSS_MULTIPLE:
            return fail("BENCHMARK_NOTIONAL_OUT_OF_RANGE",notional=str(notional_usdt))
        final=sequence["states"][-1]["quote"]
        best_bid=cap.dec(final.get("bid"),"best_bid"); best_ask=cap.dec(final.get("ask"),"best_ask")
        step=cap.dec(rules.get("qty_step"),"qty_step")
        min_qty=cap.dec(rules.get("min_qty"),"min_qty",allow_zero=True)
        max_qty=cap.dec(rules.get("max_qty"),"max_qty")
        min_notional=cap.dec(rules.get("min_notional"),"min_notional",allow_zero=True)
        max_n=rules.get("max_notional"); max_n=None if max_n is None else cap.dec(max_n,"max_notional")
    except Exception as exc:
        return fail("PERSISTENCE_BENCHMARK_INPUT_INVALID",error=str(exc))
    if max_n is not None and notional_usdt>max_n:
        return fail("PERSISTENCE_BENCHMARK_ABOVE_VENUE_MAX_NOTIONAL")
    buy_qty=cap._floor_to_step(min(notional_usdt/best_ask,max_qty),step)
    sell_qty=cap._floor_to_step(min(notional_usdt/best_bid,max_qty),step)
    if buy_qty<=0 or sell_qty<=0 or buy_qty<min_qty or sell_qty<min_qty:
        return fail("PERSISTENCE_BENCHMARK_QUANTITY_BELOW_VENUE_MIN")
    if buy_qty*best_ask<min_notional or sell_qty*best_bid<min_notional:
        return fail("PERSISTENCE_BENCHMARK_NOTIONAL_BELOW_VENUE_MIN")
    rows=[]; max_buy=Decimal("0"); max_sell=Decimal("0")
    try:
        for state in sequence["states"]:
            q=state["quote"]; b=state["book"]
            bid=cap.dec(q.get("bid"),"best_bid"); ask=cap.dec(q.get("ask"),"best_ask")
            buy_vwap,_=cap._vwap(b.get("ask_levels"),buy_qty)
            sell_vwap,_=cap._vwap(b.get("bid_levels"),sell_qty)
            buy_impact=cap._impact_bps("BUY",ask,buy_vwap)
            sell_impact=cap._impact_bps("SELL",bid,sell_vwap)
            if buy_impact>MAX_IMPACT_BPS or sell_impact>MAX_IMPACT_BPS:
                return fail("PERSISTENCE_BENCHMARK_IMPACT_EXCEEDS_MODEL",index=state["index"],
                            buy_impact_bps=str(buy_impact),sell_impact_bps=str(sell_impact),
                            limit_bps=str(MAX_IMPACT_BPS))
            max_buy=max(max_buy,buy_impact); max_sell=max(max_sell,sell_impact)
            rows.append({"index":state["index"],"buy_impact_bps":str(buy_impact),
                         "sell_impact_bps":str(sell_impact)})
    except Exception as exc:
        return fail("PERSISTENCE_BENCHMARK_DEPTH_UNPROVEN",error=str(exc))
    out={"schema":SCHEMA,"contract_id":CONTRACT_ID,"status":"PASS",
         "reason":"FULL_EQUITY_PERSISTENCE_BENCHMARK_PASS","symbol":sequence["symbol"],
         "benchmark_notional_usdt":str(notional_usdt),"buy_quantity":str(buy_qty),
         "sell_quantity":str(sell_qty),"state_count":len(sequence["states"]),
         "event_span_ms":sequence["event_span_ms"],"max_buy_impact_bps":str(max_buy),
         "max_sell_impact_bps":str(max_sell),"state_impacts":rows,
         "sequence_provenance_sha256":sequence["provenance_sha256"],
         "symbol_rules_provenance_sha256":rules["provenance_sha256"],
         "can_trade":False,"capital_permission":"DENY"}
    out["provenance_sha256"]=cap.sha256_json(out); return out
