#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pretrade_guardian as guardian


DEFAULT_SCHEMA = Path("configs/BTCUSDT_FUTURES_TRADE_CARD_SCHEMA_v1.json")
DEFAULT_GUARDIAN_CONFIG = Path("configs/PRETRADE_GUARDIAN_v1.json")
DEMO_CARDS = [
    Path("smoke_tests/btcusdt_futures_trade_card.valid.json"),
    Path("smoke_tests/btcusdt_futures_trade_card.blocked.json"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def alias_value(card: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in card:
            return card[name]
    return None


def has_nonempty(card: dict[str, Any], *names: str) -> bool:
    value = alias_value(card, *names)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, list) and not value:
        return False
    return True


def validate_card_schema(card: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    instrument = schema.get("instrument", {})
    allowed_markets = set(instrument.get("allowed_markets", []))
    allowed_sides = set(instrument.get("allowed_sides", []))
    allowed_margin_modes = set(instrument.get("allowed_margin_modes", []))

    errors: list[str] = []
    warnings: list[str] = []

    required_checks = {
        "symbol": ("symbol",),
        "market": ("market", "instrument_type"),
        "tf": ("tf",),
        "side": ("side",),
        "entry": ("entry",),
        "stop": ("stop", "sl"),
        "tp": ("tp", "take_profit", "targets"),
        "risk_pct": ("risk_pct",),
        "leverage": ("leverage",),
        "margin_mode": ("margin_mode",),
        "mark_price": ("mark_price",),
        "liquidation_price": ("liquidation_price", "liq_price"),
        "fees_slippage_included": ("fees_slippage_included",),
        "confirmations": ("confirmations", "signals"),
        "stop_method": ("stop_method",),
    }
    for logical_name, names in required_checks.items():
        if not has_nonempty(card, *names):
            errors.append(f"missing_{logical_name}")

    symbol = str(card.get("symbol") or "").upper()
    if symbol and symbol != str(instrument.get("symbol", "BTCUSDT")).upper():
        errors.append("symbol_must_be_BTCUSDT")

    market = str(card.get("market") or card.get("instrument_type") or "").lower()
    if market and market not in allowed_markets:
        errors.append("market_must_be_futures_or_perp")

    side = str(card.get("side") or "").upper()
    if side and side not in allowed_sides:
        errors.append("side_must_be_LONG_or_SHORT")

    margin_mode = str(card.get("margin_mode") or "").lower()
    if margin_mode and margin_mode not in allowed_margin_modes:
        errors.append("margin_mode_must_be_isolated_or_cross")

    for field_name, aliases in {
        "entry": ("entry",),
        "stop": ("stop", "sl"),
        "risk_pct": ("risk_pct",),
        "leverage": ("leverage",),
        "mark_price": ("mark_price",),
        "liquidation_price": ("liquidation_price", "liq_price"),
    }.items():
        value = alias_value(card, *aliases)
        if value is not None and as_float(value) is None:
            errors.append(f"{field_name}_must_be_numeric")

    targets = alias_value(card, "tp", "take_profit", "targets")
    if targets is not None:
        if not isinstance(targets, list):
            targets = [targets]
        if not targets or any(as_float(item) is None for item in targets):
            errors.append("tp_must_be_numeric_list")

    fees_flag = card.get("fees_slippage_included")
    if fees_flag is not None and not isinstance(fees_flag, bool):
        warnings.append("fees_slippage_included_should_be_boolean")

    for optional_name in schema.get("optional_but_recommended_fields", []):
        if not has_nonempty(card, optional_name):
            warnings.append(f"missing_recommended_{optional_name}")

    return {
        "schema_valid": not errors,
        "schema_errors": sorted(set(errors)),
        "schema_warnings": sorted(set(warnings)),
    }


def load_cards(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.demo:
        return [read_json(path) for path in DEMO_CARDS]
    if not args.input:
        raise SystemExit("provide --demo or --input")
    payload = read_json(Path(args.input))
    return payload if isinstance(payload, list) else [payload]


def evaluate_cards(cards: list[dict[str, Any]], schema: dict[str, Any], guardian_config: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, card in enumerate(cards, start=1):
        schema_result = validate_card_schema(card, schema)
        guardian_result = guardian.evaluate_trade(card, guardian_config)
        operational_blocks = operational_policy_blocks(card)
        final_decision = guardian_result["decision"]
        if not schema_result["schema_valid"]:
            final_decision = "blocked_schema_or_guardian"
            guardian_result["can_trade"] = False
        if operational_blocks:
            final_decision = "blocked_operational_policy"
            guardian_result["can_trade"] = False
        results.append(
            {
                "index": index,
                "source_setup_id": card.get("setup_id"),
                "schema": schema_result,
                "guardian": guardian_result,
                "operational_blocks": operational_blocks,
                "final_decision": final_decision,
                "can_trade": False,
            }
        )
    return results


def operational_policy_blocks(card: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    if card.get("promotion_gate_pass") is False:
        blocks.append("source_candidate_failed_promotion_gate")
    if card.get("source_research_gate_pass") is False:
        blocks.append("source_candidate_failed_research_gate")
    generated_mode = str(card.get("generated_mode") or "")
    if generated_mode == "research_replay_only":
        blocks.append("research_replay_card_not_live_signal")
    liquidation_mode = str(card.get("liquidation_price_mode") or "")
    if "synthetic" in liquidation_mode:
        blocks.append("synthetic_liquidation_price_requires_replacement")
    return sorted(set(blocks))


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BTCUSDT Futures Trade Card Check",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Local input-contract and pre-trade policy check.",
        "- No orders, no private credentials, no live permission.",
        "- A clean card means manual-review eligible only.",
        "",
        "## Summary",
        "",
        f"- Cards: `{report['card_count']}`",
        f"- Schema valid: `{report['schema_valid_count']}`",
        f"- Guardian pass/manual-review: `{report['guardian_pass_count']}`",
        f"- Final blocked: `{report['final_blocked_count']}`",
        "",
        "| # | Setup | Schema | Guardian Decision | RR | Liq Buffer | Final | Blocks / Errors |",
        "|---:|---|---|---|---:|---:|---|---|",
    ]
    for item in report["results"]:
        guardian_result = item["guardian"]
        computed = guardian_result["computed"]
        schema_result = item["schema"]
        blocks = ", ".join(schema_result["schema_errors"] + guardian_result["hard_blocks"] + item.get("operational_blocks", [])) or "-"
        setup_id = item.get("source_setup_id") or "-"
        lines.append(
            f"| `{item['index']}` | `{setup_id}` | `{schema_result['schema_valid']}` | "
            f"`{guardian_result['decision']}` | `{computed['rr']}` | "
            f"`{computed['liquidation_buffer_pct']}` | `{item['final_decision']}` | `{blocks}` |"
        )
    lines.extend(
        [
            "",
            "## Required Futures Fields",
            "",
            "`symbol`, `market`, `tf`, `side`, `entry`, `stop`, `tp`, `risk_pct`, `leverage`, "
            "`margin_mode`, `mark_price`, `liquidation_price`, `fees_slippage_included`, "
            "`confirmations`, `stop_method`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Validate BTCUSDT futures trade cards and run Pretrade Guardian")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    parser.add_argument("--guardian-config", default=str(DEFAULT_GUARDIAN_CONFIG))
    parser.add_argument("--input")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--out-prefix", default="docs/BTCUSDT_FUTURES_TRADE_CARD_SMOKE_2026-06-04")
    args = parser.parse_args()

    schema = read_json(Path(args.schema))
    guardian_config = read_json(Path(args.guardian_config))
    cards = load_cards(args)
    results = evaluate_cards(cards, schema, guardian_config)
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "input_contract_and_pretrade_gate",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "schema": str(Path(args.schema)),
        "guardian_config": str(Path(args.guardian_config)),
        "card_count": len(results),
        "schema_valid_count": sum(1 for item in results if item["schema"]["schema_valid"]),
        "guardian_pass_count": sum(1 for item in results if item["guardian"]["decision"] == "pass_manual_review_only"),
        "final_blocked_count": sum(1 for item in results if item["final_decision"] != "pass_manual_review_only"),
        "results": results,
    }

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "card_count": report["card_count"],
                "schema_valid_count": report["schema_valid_count"],
                "guardian_pass_count": report["guardian_pass_count"],
                "final_blocked_count": report["final_blocked_count"],
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
