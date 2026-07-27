#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import btc_futures_trade_card


DEFAULT_SOURCE = Path("_dl/control_panel/MAX_CORE_LITE_V13_STRUCTURAL_CANDIDATE.json")
DEFAULT_OUT_PREFIX = Path("docs/RESEARCH_CANDIDATE_TRADE_CARD_SMOKE_2026-06-04")
DEFAULT_PROMOTION_REPORT = Path("docs/CANDIDATE_PROMOTION_GATE_2026-06-04.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def candidate_id(candidate: dict[str, Any]) -> str:
    return str(candidate.get("id") or candidate.get("strategy_id") or candidate.get("candidate_id") or "unknown_candidate")


def find_candidate(report: dict[str, Any], requested_id: str | None) -> dict[str, Any] | None:
    pools: list[Any] = []
    for key in ("candidates", "top_results", "results", "top_items"):
        value = report.get(key)
        if isinstance(value, list):
            pools.extend(value)
    best = report.get("best_candidate")
    if isinstance(best, dict):
        pools.insert(0, best)
    if requested_id:
        for item in pools:
            if isinstance(item, dict) and candidate_id(item) == requested_id:
                return item
        return None
    return best if isinstance(best, dict) else next((item for item in pools if isinstance(item, dict)), None)


def research_gate_pass(candidate: dict[str, Any]) -> bool:
    gate = candidate.get("research_gate") or candidate.get("gate") or {}
    if isinstance(gate, dict):
        if "pass" in gate:
            return bool(gate.get("pass"))
        if gate.get("decision"):
            return str(gate.get("decision")).lower() in {"pass", "rr_pass", "research_pass"}
    return False


def promotion_status(report_path: Path | None, cid: str) -> dict[str, Any]:
    if report_path is None or not report_path.exists():
        return {
            "promotion_gate_pass": False,
            "promotion_decision": "missing_promotion_report",
            "promotion_blocks": ["missing_promotion_report"],
        }
    payload = read_json(report_path)
    for item in payload.get("candidates", []):
        if isinstance(item, dict) and item.get("candidate_id") == cid:
            return {
                "promotion_gate_pass": item.get("promotion_decision") == "promoted_to_live_review_candidate",
                "promotion_decision": item.get("promotion_decision"),
                "promotion_blocks": item.get("hard_blocks", []),
                "promotion_report": str(report_path),
            }
    return {
        "promotion_gate_pass": False,
        "promotion_decision": "candidate_not_found_in_promotion_report",
        "promotion_blocks": ["candidate_not_found_in_promotion_report"],
        "promotion_report": str(report_path),
    }


def select_trade(candidate: dict[str, Any], mode: str) -> dict[str, Any] | None:
    trades = candidate.get("trades")
    if not isinstance(trades, list) or not trades:
        return None
    valid = [trade for trade in trades if isinstance(trade, dict) and trade.get("entry") and trade.get("stop")]
    if not valid:
        return None
    if mode == "best_net_r":
        return max(valid, key=lambda item: float(item.get("net_r") or item.get("gross_r") or -999999))
    return max(valid, key=lambda item: str(item.get("entry_time") or item.get("signal_time") or ""))


def synthetic_liquidation_price(side: str, entry: float, buffer_pct: float) -> float:
    if side.upper() == "SHORT":
        return round(entry * (1.0 + buffer_pct / 100.0), 8)
    return round(entry * (1.0 - buffer_pct / 100.0), 8)


def infer_tf(report: dict[str, Any], fallback: str) -> str:
    data = report.get("data") or {}
    params = report.get("params") or {}
    for source in (data, params, report):
        if isinstance(source, dict):
            for key in ("interval", "tf", "timeframe"):
                if source.get(key):
                    return str(source[key])
    return fallback


def build_card(
    *,
    report: dict[str, Any],
    source_path: Path,
    candidate: dict[str, Any],
    trade: dict[str, Any],
    promotion: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    side = str(trade.get("side") or candidate.get("side") or "SHORT").upper()
    entry = float(trade["entry"])
    stop = float(trade["stop"])
    take_profit = trade.get("take_profit") or trade.get("tp")
    if isinstance(take_profit, list):
        targets = take_profit
    else:
        targets = [take_profit] if take_profit is not None else []
    requirements = candidate.get("requires") if isinstance(candidate.get("requires"), list) else []
    confirmations = max(2, min(5, len(requirements) or 2))
    return {
        "symbol": args.symbol,
        "market": "futures",
        "tf": infer_tf(report, args.tf),
        "setup_id": candidate_id(candidate),
        "scenario": "research_candidate_replay_card",
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp": targets,
        "risk_pct": args.risk_pct,
        "leverage": args.leverage,
        "margin_mode": args.margin_mode,
        "mark_price": entry,
        "liquidation_price": synthetic_liquidation_price(side, entry, args.synthetic_liq_buffer_pct),
        "liquidation_price_mode": "synthetic_replay_only_replace_with_exchange_value_before_live",
        "fees_slippage_included": "net_r" in trade,
        "confirmations": confirmations,
        "stop_method": "atr" if trade.get("atr14") else "structure",
        "funding": None,
        "m15_bias": side,
        "bitmaster_bias": side,
        "oi_context": "not_available_in_research_candidate",
        "spot_confirmation": trade.get("spot_perp_divergence_12_sign") or "not_available",
        "liquidity_context": "not_available_in_research_candidate",
        "invalidation": f"Historical replay only. Replace entry/stop/tp with live plan before review. Source trade index: {trade.get('index')}.",
        "source_report": str(source_path),
        "source_candidate_id": candidate_id(candidate),
        "source_trade_index": trade.get("index"),
        "source_entry_time": trade.get("entry_time"),
        "source_exit_time": trade.get("exit_time"),
        "source_exit_reason": trade.get("exit_reason"),
        "source_net_r": trade.get("net_r"),
        "source_research_gate_pass": research_gate_pass(candidate),
        "promotion_gate_pass": promotion.get("promotion_gate_pass"),
        "promotion_gate_decision": promotion.get("promotion_decision"),
        "promotion_gate_blocks": promotion.get("promotion_blocks", []),
        "promotion_gate_report": promotion.get("promotion_report"),
        "generated_mode": "research_replay_only",
        "live_permission": false_bool(),
        "notes": "Generated from historical research candidate. This is not a live signal and never grants order permission.",
    }


def false_bool() -> bool:
    return False


def builder_blocks(candidate: dict[str, Any], trade: dict[str, Any] | None, promotion: dict[str, Any]) -> list[str]:
    blocks: list[str] = ["research_replay_only_not_live_signal"]
    if not research_gate_pass(candidate):
        blocks.append("source_candidate_failed_research_gate")
    if promotion.get("promotion_gate_pass") is not True:
        blocks.append("source_candidate_failed_promotion_gate")
    if trade is None:
        blocks.append("source_candidate_has_no_trade_level_entry_stop_tp")
    return sorted(set(blocks))


def render_markdown(report: dict[str, Any]) -> str:
    card = report.get("generated_card") or {}
    validation = report.get("card_validation") or {}
    guardian_result = validation.get("results", [{}])[0].get("guardian", {}) if validation.get("results") else {}
    computed = guardian_result.get("computed", {})
    lines = [
        "# Research Candidate To BTCUSDT Futures Trade Card",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Converts a historical research candidate into a reviewable trade-card draft.",
        "- Does not create a live signal.",
        "- Does not send orders.",
        "- Final decision is blocked while the source remains research-only or replay-only.",
        "",
        "## Result",
        "",
        f"- Source report: `{report['source_report']}`",
        f"- Candidate: `{report.get('source_candidate_id')}`",
        f"- Trade selected: `{report.get('source_trade_index')}`",
        f"- Builder decision: `{report['builder_decision']}`",
        f"- Blocks: `{', '.join(report['builder_blocks']) or '-'}`",
        f"- Card output: `{report.get('card_path')}`",
        "",
        "## Card Preview",
        "",
        f"- Side: `{card.get('side')}`",
        f"- Entry: `{card.get('entry')}`",
        f"- Stop: `{card.get('stop')}`",
        f"- TP: `{card.get('tp')}`",
        f"- RR: `{computed.get('rr')}`",
        f"- Synthetic liquidation buffer: `{computed.get('liquidation_buffer_pct')}`",
        f"- Guardian decision: `{guardian_result.get('decision')}`",
        "",
        "## Next Use",
        "",
        "For a real review, copy the generated card, replace replay prices with the live plan, replace synthetic liquidation price with the exchange value, then run:",
        "",
        "```powershell",
        "python tools/btc_futures_trade_card.py --input <card.json> --out-prefix docs/MY_LIVE_REVIEW_CARD",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build a BTCUSDT futures trade-card draft from a research candidate")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--candidate-id")
    parser.add_argument("--trade-select", choices=["latest", "best_net_r"], default="latest")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--tf", default="1h")
    parser.add_argument("--risk-pct", type=float, default=0.5)
    parser.add_argument("--leverage", type=float, default=3.0)
    parser.add_argument("--margin-mode", default="isolated")
    parser.add_argument("--synthetic-liq-buffer-pct", type=float, default=15.0)
    parser.add_argument("--promotion-report", default=str(DEFAULT_PROMOTION_REPORT))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    source_path = Path(args.source)
    report_payload = read_json(source_path)
    candidate = find_candidate(report_payload, args.candidate_id)
    if candidate is None:
        raise SystemExit(f"candidate not found in {source_path}")
    selected_trade = select_trade(candidate, args.trade_select)
    promo_path = Path(args.promotion_report) if args.promotion_report else None
    if promo_path is not None and not promo_path.is_absolute():
        promo_path = Path(__file__).resolve().parents[1] / promo_path
    promotion = promotion_status(promo_path, candidate_id(candidate))
    card = (
        build_card(
            report=report_payload,
            source_path=source_path,
            candidate=candidate,
            trade=selected_trade,
            promotion=promotion,
            args=args,
        )
        if selected_trade
        else {}
    )

    validation_results: dict[str, Any] | None = None
    if card:
        schema = read_json(btc_futures_trade_card.DEFAULT_SCHEMA)
        guardian_config = read_json(btc_futures_trade_card.DEFAULT_GUARDIAN_CONFIG)
        results = btc_futures_trade_card.evaluate_cards([card], schema, guardian_config)
        validation_results = {
            "card_count": 1,
            "schema_valid_count": sum(1 for item in results if item["schema"]["schema_valid"]),
            "guardian_pass_count": sum(1 for item in results if item["guardian"]["decision"] == "pass_manual_review_only"),
            "final_blocked_count": 1,
            "results": results,
        }

    blocks = builder_blocks(candidate, selected_trade, promotion)
    builder_decision = "blocked_research_replay_only"
    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    card_path = out_prefix.with_suffix(".card.json")
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    if card:
        write_json(card_path, card)
    result_report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "research_candidate_to_trade_card_draft",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "source_report": str(source_path),
        "source_candidate_id": candidate_id(candidate),
        "source_trade_index": selected_trade.get("index") if selected_trade else None,
        "promotion": promotion,
        "builder_decision": builder_decision,
        "builder_blocks": blocks,
        "generated_card": card,
        "card_path": str(card_path) if card else None,
        "card_validation": validation_results,
        "can_trade": False,
    }
    write_json(json_path, result_report)
    md_path.write_text(render_markdown(result_report), encoding="utf-8")
    print(
        json.dumps(
            {
                "builder_decision": builder_decision,
                "builder_blocks": blocks,
                "card_path": str(card_path) if card else None,
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
