#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "docs" / "STRATEGY_DISCOVERY_REGISTRY_2026-06-08.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "TARGETED_STRATEGY_RULE_EXTRACTOR_2026-06-18"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def normalize_text(text: str) -> str:
    text = str(text or "").replace("\u00a0", " ")
    if any(marker in text for marker in ("Ð", "Ñ", "â€")):
        # Some earlier context packs contain UTF-8 decoded as cp1252. Keep the
        # original if repair is not possible.
        try:
            fixed = text.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
            if fixed.count("�") <= text.count("�"):
                text = fixed
        except UnicodeError:
            pass
    return re.sub(r"\s+", " ", text).strip()


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        out: list[Any] = []
        for key, values in value.items():
            for item in as_list(values):
                out.append(f"{key}: {item}")
        return out
    return [value]


def stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{prefix}_{digest}"


HIGH_VALUE_SOURCE_TERMS = [
    "btc",
    "bitcoin",
    "btcusdt",
    "futures",
    "perpetual",
    "перп",
    "фьючерс",
    "microstructure",
    "микрострукт",
    "trading",
    "strategy",
    "стратег",
    "signal",
    "сетап",
    "cti",
    "eth/btc",
    "btc.d",
]

ENTRY_TERMS = [
    "entry",
    "enter",
    "buy",
    "sell",
    "long",
    "short",
    "trigger",
    "signal",
    "setup",
    "breakout",
    "reclaim",
    "sweep",
    "вход",
    "лонг",
    "шорт",
    "сигнал",
    "сетап",
    "пробой",
    "свип",
    "отбой",
    "возврат",
]

EXIT_RISK_TERMS = [
    "stop",
    "sl",
    "tp",
    "take profit",
    "rr",
    "risk",
    "atr",
    "invalidation",
    "exit",
    "time stop",
    "стоп",
    "тейк",
    "риск",
    "инвалида",
    "выход",
]

DATA_TERMS = [
    "oi",
    "open interest",
    "funding",
    "spot",
    "perp",
    "cvd",
    "delta",
    "volume",
    "liquidation",
    "heatmap",
    "depth",
    "order flow",
    "btc.d",
    "eth/btc",
    "stable",
    "открытый интерес",
    "фандинг",
    "ликвидац",
    "ликвидн",
    "дельта",
    "объем",
    "объём",
]

JUNK_SNIPPET_TERMS = [
    "дата последнего обращения",
    "источники ",
    "sources ",
    "https://",
    "http://",
    "canva",
    "flourish",
    "reddit",
    "mobalytics",
    "leaderboard",
    "common ninja",
    "canvasjs",
    "privacy + cyber",
    "snyk",
    "support for other post",
    "кнопка:",
    "интерактивный элемент",
    "architectural sovereignty",
    "desktop-first",
    "physics of privacy",
    "sqlite pattern",
    "cognitive memory",
    "tauri footguns",
]

LOW_SIGNAL_SOURCE_TERMS = [
    "dashboard redesign",
    "ai companion",
    "gtm-",
    "sovereign arena",
    "crypto product strategy",
]


@dataclass(frozen=True)
class FamilySpec:
    family: str
    keywords: tuple[str, ...]
    required_data: tuple[str, ...]
    missing_if_needed: tuple[str, ...]
    suggested_test_tool: str
    default_status: str


FAMILIES: tuple[FamilySpec, ...] = (
    FamilySpec(
        "derivatives_event",
        ("oi", "open interest", "funding", "squeeze", "фандинг", "открытый интерес"),
        ("OHLCV", "open_interest", "funding"),
        (),
        "tools/max_v16_event_first_miner.py",
        "codable_now_existing_data",
    ),
    FamilySpec(
        "spot_perp_confirmation",
        ("spot", "perp", "basis", "cvd", "delta", "диверген", "дериватив"),
        ("spot_ohlcv", "perp_ohlcv", "volume_delta_proxy"),
        ("true_spot_cvd",),
        "tools/event_feature_factory.py",
        "codable_as_proxy_now",
    ),
    FamilySpec(
        "liquidity_sweep_reclaim",
        ("sweep", "liquidity", "false breakout", "reclaim", "свип", "ликвидн"),
        ("OHLCV", "range_bounds", "ATR"),
        ("true_liquidation_heatmap",),
        "tools/range_family_validator.py / tools/range_watchlist_refiner.py",
        "codable_now_existing_data",
    ),
    FamilySpec(
        "range_mean_reversion",
        ("range", "mean reversion", "support", "resistance", "диапазон", "флет", "отбой"),
        ("OHLCV", "range_bounds", "ATR", "RSI_optional"),
        (),
        "tools/range_family_validator.py",
        "codable_now_existing_data",
    ),
    FamilySpec(
        "breakout_continuation",
        ("breakout", "compression", "volatility", "trend", "пробой", "сжат"),
        ("OHLCV", "ATR", "relative_volume", "HTF_regime"),
        (),
        "tools/event_feature_factory.py",
        "codable_now_existing_data",
    ),
    FamilySpec(
        "regime_gate",
        ("regime first", "permission", "allowed actions", "allowed-side", "trend_up", "trend_down", "range otherwise", "hysteresis", "режим"),
        ("OHLCV", "ATR", "ADX_or_trend_score", "range_state"),
        (),
        "tools/pretrade_guardian.py / tools/event_feature_factory.py",
        "codable_as_guard_only",
    ),
    FamilySpec(
        "cti_rotation_overlay",
        ("cti", "btc.d", "dominance", "eth/btc", "stablecoin", "стейбл"),
        ("ETHBTC", "BTC_dominance", "stablecoin_flow", "OI_optional", "funding_optional"),
        ("stablecoin_flow_feed", "BTC_dominance_feed"),
        "tools/overlay_signal_evaluator.py",
        "codable_as_guard_only",
    ),
    FamilySpec(
        "microstructure_guard",
        ("order flow", "depth", "book", "aggressive", "cvd", "delta", "микрострукт", "дельта"),
        ("aggTrades", "bookTicker_or_depth", "volume_delta"),
        ("reliable_depth_history", "true_CVD_history"),
        "tools/flow_toxicity_feature_report.py",
        "codable_as_guard_only",
    ),
    FamilySpec(
        "research_quality_gate",
        ("walk-forward", "holdout", "oos", "expectancy", "profit factor", "bootstrap"),
        ("backtest_report", "trade_export", "promotion_gate"),
        (),
        "tools/candidate_promotion_gate.py / tools/risk_reward_gate.py",
        "process_gate_not_alpha",
    ),
)


def lower(text: str) -> str:
    return normalize_text(text).lower()


def contains_term(blob: str, term: str) -> bool:
    term = term.lower()
    if re.fullmatch(r"[a-z0-9]{1,3}", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", blob) is not None
    return term in blob


def has_any(blob: str, terms: list[str] | tuple[str, ...]) -> bool:
    return any(contains_term(blob, term) for term in terms)


def source_rel(item: dict[str, Any]) -> str:
    return str(item.get("source_rel") or item.get("source") or "")


def item_blob(item: dict[str, Any]) -> str:
    c = item.get("classification", {})
    parts: list[str] = [source_rel(item)]
    for value in as_list(c.get("keyword_hits")):
        parts.append(str(value))
    for value in as_list(c.get("rule_snippets")):
        parts.append(str(value))
    for value in as_list(c.get("numeric_claims")):
        parts.append(str(value))
    return lower("\n".join(parts))


def is_high_value_item(item: dict[str, Any], min_score: int) -> bool:
    c = item.get("classification", {})
    score = int(c.get("score") or 0)
    if score < min_score:
        return False
    blob = item_blob(item)
    if not has_any(blob, HIGH_VALUE_SOURCE_TERMS):
        return False
    if "continuityos" in blob and not has_any(blob, ("btc", "trading", "strategy", "signal")):
        return False
    src = lower(source_rel(item))
    if has_any(src, tuple(LOW_SIGNAL_SOURCE_TERMS)):
        return False
    return True


def classify_family(text: str) -> FamilySpec:
    blob = lower(text)
    scored: list[tuple[int, FamilySpec]] = []
    for spec in FAMILIES:
        score = sum(1 for term in spec.keywords if contains_term(blob, term))
        scored.append((score, spec))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return FAMILIES[-1]


def infer_side(text: str) -> str:
    blob = lower(text)
    has_long = has_any(blob, ("long", "buy", "лонг", "покуп"))
    has_short = has_any(blob, ("short", "sell", "шорт", "продаж"))
    if has_long and has_short:
        return "both"
    if has_long:
        return "long"
    if has_short:
        return "short"
    return "unknown"


def score_snippet(text: str, source_score: int) -> int:
    blob = lower(text)
    score = min(source_score // 8, 25)
    score += 16 if has_any(blob, ENTRY_TERMS) else 0
    score += 12 if has_any(blob, EXIT_RISK_TERMS) else 0
    score += 12 if has_any(blob, DATA_TERMS) else 0
    score += 8 if has_any(blob, ("backtest", "walk-forward", "holdout", "oos", "бэктест")) else 0
    score += 6 if any(ch.isdigit() for ch in blob) else 0
    if has_any(blob, ("guarantee", "гарант", "100%", "90%", "x100")):
        score -= 10
    return max(score, 0)


def is_junk_snippet(text: str) -> bool:
    blob = lower(text)
    if has_any(blob, tuple(JUNK_SNIPPET_TERMS)):
        return True
    if blob.count("http") >= 2:
        return True
    if len(re.findall(r"https?://", blob)) >= 1 and not has_any(blob, ("entry", "exit", "вход", "выход", "stop", "стоп")):
        return True
    if len(blob) > 280 and sum(1 for term in ENTRY_TERMS + EXIT_RISK_TERMS + DATA_TERMS if contains_term(blob, term)) < 2:
        return True
    return False


def codable_status(spec: FamilySpec, text: str) -> str:
    blob = lower(text)
    if spec.default_status in {"codable_as_guard_only", "process_gate_not_alpha", "codable_as_proxy_now"}:
        return spec.default_status
    if has_any(blob, ("heatmap", "order book", "depth", "cvd", "stablecoin", "btc.d", "eth/btc")):
        if spec.family not in {"liquidity_sweep_reclaim", "range_mean_reversion"}:
            return "needs_external_data"
    return spec.default_status


def build_rule_cards(items: list[dict[str, Any]], *, min_score: int, max_cards: int) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not is_high_value_item(item, min_score):
            continue
        c = item.get("classification", {})
        source_score = int(c.get("score") or 0)
        snippets = [normalize_text(str(value)) for value in as_list(c.get("rule_snippets"))]
        numeric_claims = [normalize_text(str(value)) for value in as_list(c.get("numeric_claims"))]
        for snippet in snippets:
            if len(snippet) < 80:
                continue
            if is_junk_snippet(snippet):
                continue
            snippet_score = score_snippet(snippet, source_score)
            if snippet_score < 25:
                continue
            key = lower(snippet)[:260]
            if key in seen:
                continue
            seen.add(key)
            spec = classify_family(snippet)
            status = codable_status(spec, snippet)
            snippet_blob = lower(snippet)
            risk_terms_present = has_any(snippet_blob, EXIT_RISK_TERMS)
            entry_terms_present = has_any(snippet_blob, ENTRY_TERMS)
            data_terms_present = has_any(snippet_blob, DATA_TERMS)
            card = {
                "rule_id": stable_id("rule", source_rel(item) + snippet),
                "source_rel": source_rel(item),
                "source_priority": c.get("priority"),
                "source_score": source_score,
                "family": spec.family,
                "trade_side": infer_side(snippet),
                "rule_score": snippet_score,
                "codable_status": status,
                "setup_text": snippet,
                "required_data": list(spec.required_data),
                "missing_data": list(spec.missing_if_needed) if status in {"needs_external_data", "codable_as_guard_only"} else [],
                "suggested_test_tool": spec.suggested_test_tool,
                "quality_flags": {
                    "has_entry_or_trigger_terms": entry_terms_present,
                    "has_exit_or_risk_terms": risk_terms_present,
                    "has_market_data_terms": data_terms_present,
                    "numeric_claims_in_source": numeric_claims[:8],
                },
                "can_trade": False,
            }
            cards.append(card)
    cards.sort(key=lambda card: (card["codable_status"] != "codable_now_existing_data", -card["rule_score"], -card["source_score"]))
    return cards[:max_cards]


def summarize(cards: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for card in cards:
        by_status[card["codable_status"]] = by_status.get(card["codable_status"], 0) + 1
        by_family[card["family"]] = by_family.get(card["family"], 0) + 1
    return {
        "rule_cards": len(cards),
        "by_status": dict(sorted(by_status.items())),
        "by_family": dict(sorted(by_family.items())),
        "top_codable_now": [
            {
                "rule_id": card["rule_id"],
                "family": card["family"],
                "source_rel": card["source_rel"],
                "score": card["rule_score"],
                "suggested_test_tool": card["suggested_test_tool"],
            }
            for card in cards
            if card["codable_status"] == "codable_now_existing_data"
        ][:10],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    cards = report["rule_cards"]
    lines = [
        "# Targeted Strategy Rule Extractor",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- This is a research extraction report, not a live trading permission.",
        "- `can_trade=false` for every extracted rule.",
        "- `codable_now_existing_data` means the rule can be tested against the current historical/cache layer; it does not mean the rule has edge.",
        "- External-data rules are kept as research backlog until stable feeds exist.",
        "",
        "## Summary",
        "",
        f"- Sources scanned: `{report['sources_scanned']}`",
        f"- High-value sources selected: `{report['high_value_sources']}`",
        f"- Rule cards extracted: `{summary['rule_cards']}`",
        "",
        "### By Status",
        "",
    ]
    for status, count in summary["by_status"].items():
        lines.append(f"- `{status}`: {count}")
    lines.extend(["", "### By Family", ""])
    for family, count in summary["by_family"].items():
        lines.append(f"- `{family}`: {count}")
    lines.extend(
        [
            "",
            "## Top Codable-Now Cards",
            "",
            "| Rule | Family | Source | Score | Test Tool |",
            "|---|---|---|---:|---|",
        ]
    )
    for card in [card for card in cards if card["codable_status"] == "codable_now_existing_data"][:12]:
        lines.append(
            "| `{rule_id}` | `{family}` | {source} | {score} | `{tool}` |".format(
                rule_id=card["rule_id"],
                family=card["family"],
                source=card["source_rel"].replace("|", "\\|"),
                score=card["rule_score"],
                tool=card["suggested_test_tool"],
            )
        )
    lines.extend(["", "## Rule Cards", ""])
    for idx, card in enumerate(cards[:35], start=1):
        flags = card["quality_flags"]
        lines.extend(
            [
                f"### {idx}. `{card['rule_id']}`",
                "",
                f"- Source: `{card['source_rel']}`",
                f"- Family: `{card['family']}`",
                f"- Side: `{card['trade_side']}`",
                f"- Status: `{card['codable_status']}`",
                f"- Rule score: `{card['rule_score']}`",
                f"- Required data: `{', '.join(card['required_data'])}`",
                f"- Missing data: `{', '.join(card['missing_data']) if card['missing_data'] else 'none for first test'}`",
                f"- Suggested test: `{card['suggested_test_tool']}`",
                f"- Quality flags: entry={flags['has_entry_or_trigger_terms']}, risk={flags['has_exit_or_risk_terms']}, data={flags['has_market_data_terms']}",
                "",
                "> " + card["setup_text"][:900],
                "",
            ]
        )
    lines.extend(
        [
            "## Next Action",
            "",
            "1. Test only one `codable_now_existing_data` card at a time.",
            "2. Prefer cards that reuse the current cache and already have a holdout validator.",
            "3. Reject any candidate that fails holdout, has too few trades, or relies on uncollected data.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Extract codable trading rule cards from strategy discovery docs.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="Strategy discovery registry JSON.")
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX), help="Output path prefix without extension.")
    parser.add_argument("--min-source-score", type=int, default=85, help="Minimum source score to consider.")
    parser.add_argument("--max-cards", type=int, default=80, help="Maximum rule cards to emit.")
    args = parser.parse_args()

    registry_path = Path(args.registry)
    out_prefix = Path(args.out_prefix)
    if not registry_path.exists():
        print(f"registry_not_found: {registry_path}", file=sys.stderr)
        return 2

    registry = read_json(registry_path)
    items = list(registry.get("processed", []))
    high_value_items = [item for item in items if is_high_value_item(item, args.min_source_score)]
    cards = build_rule_cards(items, min_score=args.min_source_score, max_cards=args.max_cards)
    report = {
        "generated_at": now_iso(),
        "registry": str(registry_path),
        "sources_scanned": len(items),
        "high_value_sources": len(high_value_items),
        "can_trade": False,
        "summary": summarize(cards),
        "rule_cards": cards,
    }
    write_json(out_prefix.with_suffix(".json"), report)
    write_text(out_prefix.with_suffix(".md"), render_markdown(report))
    print(json.dumps({"status": "ok", **report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
