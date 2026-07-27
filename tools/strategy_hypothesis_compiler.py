#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "docs" / "STRATEGY_DISCOVERY_REGISTRY_2026-06-08.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "STRATEGY_HYPOTHESIS_QUEUE_2026-06-08"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


HYPOTHESIS_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "H1_OI_FUNDING_PRESSURE",
        "title": "OI + funding pressure/reload",
        "family": "derivatives_event",
        "type": "test_existing",
        "must_any": [["oi", "open interest", "открытый интерес"], ["funding", "фандинг"]],
        "nice_any": [["liquidation", "ликвидац"], ["trend", "breakout", "пробой"], ["squeeze"]],
        "rule_seed": "Use OI expansion/flush and funding skew/compression as event triggers or abstention filters; test both continuation and squeeze-reversal outcomes.",
        "data_needed": ["futures klines", "aligned open interest", "funding", "HTF regime"],
        "existing_test_path": "tools/max_v16_event_first_miner.py on data/cache/binance_spot_perp_extended",
        "command": "python tools/max_v16_event_first_miner.py --use-cache --cache-dir data/cache/binance_spot_perp_extended --interval 1h --htf-interval 4h --out-prefix docs/DOC_H1_OI_FUNDING_PRESSURE_2026-06-08",
        "reject_gate": "Reject if <100 trades, expectancy <=0R, bootstrap P>0 below 0.60, or stable folds <50%.",
    },
    {
        "id": "H2_SPOT_PERP_CONFIRMATION",
        "title": "Spot vs perp confirmation/divergence",
        "family": "spot_perp_filter",
        "type": "test_existing",
        "must_any": [["spot"], ["perp", "basis"], ["divergence", "delta", "cvd"]],
        "nice_any": [["breakout", "range", "sweep"], ["volume", "объем", "объём"]],
        "rule_seed": "Allow continuation only when spot confirms perp move; treat perp-only move as weak or fade candidate.",
        "data_needed": ["spot klines", "futures klines", "spot/perp return divergence", "volume"],
        "existing_test_path": "tools/event_feature_factory.py and v05_spot_* strategies",
        "command": "python tools/event_feature_factory.py --cache-dir data/cache/binance_spot_perp_extended --max-strategies 72 --workers 8 --out-prefix docs/DOC_H2_SPOT_PERP_CONFIRMATION_2026-06-08",
        "reject_gate": "Reject if holdout pass is false or edge disappears after spot/perp lag and fees.",
    },
    {
        "id": "H3_VOLUME_BREAKOUT_ATR",
        "title": "Volume-confirmed breakout with ATR stop",
        "family": "price_volume_breakout",
        "type": "test_existing",
        "must_any": [["breakout", "пробой"], ["volume", "объем", "объём"], ["atr", "stop", "стоп"]],
        "nice_any": [["ema", "vwap"], ["trend"], ["walk-forward"]],
        "rule_seed": "Trade only completed-bar breakout when relative volume confirms; stop by ATR and reject mid-range/chasing entries.",
        "data_needed": ["OHLCV", "relative volume", "ATR", "HTF bias"],
        "existing_test_path": "tools/max_backtest.py v04_trend/v05_spot_trend and event_feature_factory",
        "command": "python tools/event_feature_factory.py --cache-dir data/cache/binance_spot_perp_extended --max-strategies 72 --workers 8 --out-prefix docs/DOC_H3_VOLUME_BREAKOUT_ATR_2026-06-08",
        "reject_gate": "Reject if net expectancy <=0.10R, winrate below breakeven after fees/slippage, or fold instability is high.",
    },
    {
        "id": "H4_RANGE_SWEEP_RECLAIM",
        "title": "Range edge + liquidity sweep/reclaim",
        "family": "range_reversal",
        "type": "test_existing",
        "must_any": [["range", "диапазон"], ["sweep", "свип", "liquidity", "ликвидн"]],
        "nice_any": [["rsi"], ["false breakout"], ["fvg"], ["funding"]],
        "rule_seed": "Fade range extremes only after sweep/reclaim; stop outside wick/ATR and avoid true breakout conditions.",
        "data_needed": ["OHLCV", "Donchian/range bounds", "RSI", "ATR", "funding optional"],
        "existing_test_path": "tools/max_backtest.py v04_sweep/v04_range/v05_spot_sweep/v05_spot_range",
        "command": "python tools/event_feature_factory.py --cache-dir data/cache/binance_spot_perp_extended --max-strategies 72 --workers 8 --out-prefix docs/DOC_H4_RANGE_SWEEP_RECLAIM_2026-06-08",
        "reject_gate": "Reject if event frequency is too low, range filter fails holdout, or breakout-loss tail dominates.",
    },
    {
        "id": "H5_CTI_ROTATION_OVERLAY",
        "title": "CTI rotation overlay: BTC -> ETH/alts or defensive",
        "family": "portfolio_overlay",
        "type": "overlay_only",
        "must_any": [["cti", "btc.d", "dominance", "доминация", "eth/btc"]],
        "nice_any": [["stable", "funding"], ["oi"], ["dual timeframe"]],
        "rule_seed": "Use ETHBTC slope, inverted BTC.D slope, OI mix, funding compression and stablecoin inflow as portfolio rotation context, not BTC entry permission.",
        "data_needed": ["ETHBTC", "BTC dominance proxy", "stablecoin inflow proxy", "OI/funding optional"],
        "existing_test_path": "tools/overlay_signal_evaluator.py cti",
        "command": "python tools/overlay_signal_evaluator.py cti --ethbtc-trend 0.7 --btcd -0.6 --oi-mix 0.2 --funding-compression 0.3 --stablecoin-inflow 0.5 --confirm-h4 --confirm-d1 --out _dl/control_panel/ARBITER_CTI_DEMO.json",
        "reject_gate": "Do not promote to BTC trade entry; only validate as exposure/risk overlay.",
    },
    {
        "id": "H6_CONFLUENCE_FAIL_CLOSED_GATE",
        "title": "Fail-closed confluence gate",
        "family": "risk_gate",
        "type": "consumer_gate",
        "must_any": [["confluence", "regime", "risk"], ["entry", "signal", "setup"]],
        "nice_any": [["paper"], ["executor"], ["audit"]],
        "rule_seed": "A trade is considered only when regime, setup, directional score, risk kernel and anti-self veto all allow it.",
        "data_needed": ["candidate signal", "regime state", "risk state", "pretrade card"],
        "existing_test_path": "tools/pretrade_guardian.py and tools/research_candidate_trade_card_builder.py",
        "command": "python tools/pretrade_guardian.py --demo --out-prefix docs/DOC_H6_CONFLUENCE_FAIL_CLOSED_GATE_2026-06-08",
        "reject_gate": "This is not alpha. Keep as veto/gate unless it improves live-review error rate.",
    },
    {
        "id": "H7_MICROSTRUCTURE_TOXICITY_GUARD",
        "title": "Order-flow/liquidation toxicity guard",
        "family": "microstructure_guard",
        "type": "guard_only",
        "must_any": [["order flow", "delta", "depth", "liquidation", "ликвидац"]],
        "nice_any": [["funding"], ["oi"], ["volume"]],
        "rule_seed": "Use delta/depth/liquidation clusters to abstain, reduce size or avoid adverse selection; not a standalone entry.",
        "data_needed": ["aggTrade", "bookTicker/local depth", "liquidation/crowding proxy"],
        "existing_test_path": "tools/flow_toxicity_feature_report.py and tools/futures_public_capture_session.py",
        "command": "python tools/flow_toxicity_feature_report.py --demo --out-prefix docs/DOC_H7_MICROSTRUCTURE_TOXICITY_GUARD_2026-06-08",
        "reject_gate": "Reject standalone trading use; keep only if it reduces drawdown or bad-fill clusters.",
    },
    {
        "id": "H8_RESEARCH_OS_QUALITY_GATE",
        "title": "Research OS quality gate",
        "family": "research_process",
        "type": "process_gate",
        "must_any": [["walk-forward", "holdout", "oos", "backtest"], ["expectancy", "winrate", "rr"]],
        "nice_any": [["paper"], ["executor"], ["audit"]],
        "rule_seed": "Every numeric claim must pass deterministic backtest, OOS/holdout, stable folds, fees/slippage and trade-level export before promotion.",
        "data_needed": ["backtest reports", "trade-level exports", "promotion gate"],
        "existing_test_path": "tools/candidate_promotion_gate.py and tools/risk_reward_gate.py",
        "command": "python tools/candidate_promotion_gate.py --out-prefix docs/DOC_H8_RESEARCH_OS_QUALITY_GATE_2026-06-08",
        "reject_gate": "Block any strategy with unverified claims, no trade-level data, small sample or negative holdout.",
    },
]


def load_registry(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return list(data.get("processed", []))


def item_blob(item: dict[str, Any]) -> str:
    c = item.get("classification", {})
    hits = c.get("keyword_hits", {})
    parts: list[str] = [str(item.get("source_rel", ""))]
    for values in hits.values():
        parts.extend(str(value) for value in values)
    parts.extend(str(value) for value in c.get("rule_snippets", []))
    for values in c.get("numeric_claims", {}).values():
        parts.extend(str(value) for value in values)
    return "\n".join(parts).lower()


def group_matches(blob: str, group: list[str]) -> bool:
    return any(token.lower() in blob for token in group)


def template_matches(item: dict[str, Any], template: dict[str, Any]) -> bool:
    blob = item_blob(item)
    return all(group_matches(blob, group) for group in template["must_any"])


def evidence_snippets(item: dict[str, Any], template: dict[str, Any], limit: int = 3) -> list[str]:
    c = item.get("classification", {})
    tokens = {token.lower() for group in template["must_any"] + template.get("nice_any", []) for token in group}
    found: list[str] = []
    for snippet in c.get("rule_snippets", []):
        lower = str(snippet).lower()
        if any(token in lower for token in tokens):
            found.append(str(snippet)[:360])
        if len(found) >= limit:
            break
    return found


def compile_hypotheses(items: list[dict[str, Any]], *, top_sources: int) -> list[dict[str, Any]]:
    compiled: list[dict[str, Any]] = []
    for template in HYPOTHESIS_TEMPLATES:
        matches = [item for item in items if template_matches(item, template)]
        matches.sort(key=lambda item: item.get("classification", {}).get("score", 0), reverse=True)
        sources = matches[:top_sources]
        score = sum(int(item.get("classification", {}).get("score", 0)) for item in sources)
        priority = "high" if score >= 250 else "medium" if score >= 120 else "low" if score > 0 else "missing"
        compiled.append(
            {
                "id": template["id"],
                "title": template["title"],
                "family": template["family"],
                "type": template["type"],
                "priority": priority,
                "source_count": len(matches),
                "score": score,
                "rule_seed": template["rule_seed"],
                "data_needed": template["data_needed"],
                "existing_test_path": template["existing_test_path"],
                "command": template["command"],
                "reject_gate": template["reject_gate"],
                "sources": [
                    {
                        "source_rel": item.get("source_rel"),
                        "source_score": item.get("classification", {}).get("score"),
                        "priority": item.get("classification", {}).get("priority"),
                        "numeric_claims": item.get("classification", {}).get("numeric_claims", {}),
                        "snippets": evidence_snippets(item, template),
                    }
                    for item in sources
                ],
                "can_trade": False,
            }
        )
    return sorted(compiled, key=lambda item: (item["priority"] != "high", -item["score"], item["id"]))


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Hypothesis Queue",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- This is a research/coding queue, not trade permission.",
        "- A hypothesis becomes useful only after deterministic backtest, OOS/holdout, fold stability, fees/slippage and promotion gates.",
        "- CTI, confluence and microstructure items are overlays/guards unless a separate backtest proves entry edge.",
        "",
        "## Ranked Queue",
        "",
        "| Priority | ID | Type | Sources | Score | First Test |",
        "|---|---|---|---:|---:|---|",
    ]
    for item in report["hypotheses"]:
        first_test = item["command"].replace("|", "\\|")
        lines.append(
            f"| `{item['priority']}` | `{item['id']}` | `{item['type']}` | `{item['source_count']}` | `{item['score']}` | `{first_test}` |"
        )
    lines.extend(["", "## Hypotheses", ""])
    for item in report["hypotheses"]:
        lines.extend(
            [
                f"### {item['id']} - {item['title']}",
                "",
                f"- Priority: `{item['priority']}`.",
                f"- Type: `{item['type']}`.",
                f"- Family: `{item['family']}`.",
                f"- Source count: `{item['source_count']}`.",
                f"- Rule seed: {item['rule_seed']}",
                f"- Data needed: `{', '.join(item['data_needed'])}`.",
                f"- Existing test path: `{item['existing_test_path']}`.",
                f"- Reject gate: {item['reject_gate']}",
                f"- Can trade: `false`.",
                "",
                "Command:",
                "",
                "```powershell",
                item["command"],
                "```",
                "",
            ]
        )
        if item["sources"]:
            lines.extend(["Evidence sources:", ""])
            for source in item["sources"][:5]:
                lines.append(f"- `{source['source_rel']}` score `{source['source_score']}`.")
                for snippet in source.get("snippets", [])[:2]:
                    lines.append(f"- Snippet: {snippet}")
            lines.append("")
        else:
            lines.extend(["Evidence sources:", "", "- None yet.", ""])
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Compile processed strategy documents into a testable hypothesis queue")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--top-sources", type=int, default=6)
    args = parser.parse_args()

    items = load_registry(Path(args.registry))
    hypotheses = compile_hypotheses(items, top_sources=max(1, args.top_sources))
    report = {
        "generated_at": now_iso(),
        "registry": str(Path(args.registry)),
        "processed_items": len(items),
        "runtime_boundary": {
            "classification": "strategy_hypothesis_queue_research_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "hypotheses": hypotheses,
        "can_trade": False,
    }

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")

    print(
        json.dumps(
            {
                "processed_items": len(items),
                "hypotheses": len(hypotheses),
                "high_priority": sum(1 for item in hypotheses if item["priority"] == "high"),
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
