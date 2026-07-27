#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://cryptoguidessite.vercel.app"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "CRYPTO_GUIDES_WEB_INGEST_2026-06-19"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def fetch_text(url: str, timeout: int) -> tuple[int | None, str, str | None]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TradingOSResearchIngest/1.0 (+observer-only; no trading)",
            "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset()
            encodings = ["utf-8", "utf-8-sig"]
            if charset and charset.lower() not in encodings:
                encodings.append(charset)
            for encoding in encodings:
                try:
                    return response.status, raw.decode(encoding), None
                except UnicodeDecodeError:
                    continue
            return response.status, raw.decode(charset or "utf-8", errors="replace"), None
    except (urllib.error.URLError, TimeoutError, UnicodeError) as exc:
        return None, "", f"{type(exc).__name__}: {exc}"


def strip_html(raw: str) -> str:
    cleaned = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", raw)
    cleaned = re.sub(r"(?is)<nav\b.*?</nav>|<footer\b.*?</footer>|<header\b.*?</header>", " ", cleaned)
    cleaned = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", cleaned)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line)


def compact_text(text: str, limit: int = 14000) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def extract_title(raw_html: str, text: str, fallback: str) -> str:
    for pattern in (r"<h1[^>]*>(.*?)</h1>", r"<title[^>]*>(.*?)</title>"):
        match = re.search(pattern, raw_html, flags=re.I | re.S)
        if match:
            title = strip_html(match.group(1))
            title = re.sub(r"\s*·\s*Crypto Guides\s*$", "", title).strip()
            if title:
                return title
    first = text.splitlines()[0] if text.splitlines() else fallback
    return first[:180]


def extract_published(text: str) -> dict[str, str | None]:
    match = re.search(r"Published:\s*(\d{4}-\d{2}-\d{2})\s*·\s*([A-Za-zА-Яа-я-]+)", text)
    return {"date": match.group(1), "category": match.group(2)} if match else {"date": None, "category": None}


def discover_guide_links(base_url: str, homepage_html: str, llms_text: str) -> list[str]:
    links = set(re.findall(r'href=["\'](/guides/[^"\']+)["\']', homepage_html))
    links.update(re.findall(r"Slug:\s*(/guides/[A-Za-z0-9_./-]+)", llms_text))
    return sorted(links)


@dataclass(frozen=True)
class RouteSpec:
    route_id: str
    title: str
    keywords: tuple[str, ...]
    testability: str
    test_type: str
    suggested_test: str
    data_requirements: tuple[str, ...]
    reject_gate: str


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        "EDGE_VALIDATION_QUALITY_GATE",
        "OOS/bootstrap/expectancy quality gate",
        ("oos", "bootstrap", "in-sample", "walk-forward", "holdout", "expectancy", "ergodicity", "kelly", "winrate", "r-multiple", "paper-trading"),
        "process_gate",
        "quality_gate",
        "python tools/candidate_promotion_gate.py --out-prefix docs/CRYPTO_GUIDES_CANDIDATE_PROMOTION_GATE_2026-06-19 && python tools/risk_reward_gate.py --out-prefix docs/CRYPTO_GUIDES_RISK_REWARD_GATE_2026-06-19",
        ("existing backtest reports", "trade-level exports", "holdout/OOS summaries"),
        "Use as gate only; it is not an entry strategy.",
    ),
    RouteSpec(
        "REGIME_TIMING_AND_MILD_TREND_FILTER",
        "Regime timing and mild-vs-strong reversal filter",
        ("regime", "30-120", "mild", "strong", "trend", "reversal", "timing", "режим", "разворот"),
        "codable_now_existing_data",
        "backtest_filter",
        "python tools/combined_regime_hardening.py --out-prefix docs/CRYPTO_GUIDES_REGIME_TIMING_HARDENING_2026-06-19",
        ("OHLCV", "ATR/trend strength", "completed candle alignment"),
        "Reject if effect disappears after walk-forward or if trade count is too small.",
    ),
    RouteSpec(
        "COMPRESSION_NO_MANS_LAND_GUARD",
        "Compression / no-man's-land abstention guard",
        ("compression", "no-man", "сжатие", "mid-range", "middle", "range", "trap", "капкан"),
        "codable_now_existing_data",
        "guard_overlay",
        "python tools/strategy_polygon_parallel.py --intervals 15m,1h,4h --max-strategies 100 --out-prefix docs/CRYPTO_GUIDES_COMPRESSION_POLYGON_2026-06-19",
        ("OHLCV", "ATR percentile/proxy", "range position 30-70%"),
        "Keep only if it reduces drawdown or false entries without killing all frequency.",
    ),
    RouteSpec(
        "CROWD_FADE_POSITIONING",
        "Crowd-fade long/short positioning",
        ("crowd", "long/short", "positioning", "sentiment", "толпа", "фейд"),
        "external_data_needed",
        "external_data_backlog",
        "No honest run until long/short ratio history is collected and aligned.",
        ("long/short ratio history", "OHLCV", "OI/funding", "spot/perp confirmation"),
        "Do not proxy with price-only data; that would invent the signal.",
    ),
    RouteSpec(
        "FNG_FUNDING_RISK_GATE",
        "FNG + funding + market-risk gate",
        ("fng", "fear", "greed", "funding", "risk", "каскад"),
        "external_data_needed",
        "risk_overlay",
        "python tools/oi_funding_forward_context_observer.py --source cache --out-prefix docs/CRYPTO_GUIDES_FUNDING_RISK_CONTEXT_2026-06-19",
        ("funding", "FNG history if used", "regime state"),
        "Funding can be tested now; FNG part stays blocked until historical FNG is available.",
    ),
    RouteSpec(
        "ETHBTC_RELATIVITY_ROTATION",
        "ETH/BTC relativity and rotation overlay",
        ("eth/btc", "relativity", "macro", "btc.d", "dominance", "stablecoin", "liquidity"),
        "external_data_needed",
        "portfolio_overlay",
        "python tools/overlay_signal_evaluator.py cti --ethbtc-trend 0.7 --btcd -0.6 --oi-mix 0.2 --funding-compression 0.3 --stablecoin-inflow 0.5 --confirm-h4 --confirm-d1 --out _dl/control_panel/CRYPTO_GUIDES_CTI_DEMO.json",
        ("ETHBTC", "BTC dominance", "stablecoin flow", "macro proxies optional"),
        "Overlay only; not BTC entry permission.",
    ),
    RouteSpec(
        "GRID_RANGE_RECENTERING",
        "Grid/range recentering and fee firewall",
        ("grid", "recentering", "range-farm", "fee-firewall", "atr", "stop & rebuild", "сетка"),
        "codable_as_separate_bot",
        "range_bot_design",
        "Route to DEX/CEX range-bot paper module; do not mix with current BTC edge observer.",
        ("range bounds", "fees", "slippage", "inventory state", "recenter rules"),
        "Must be paper-tested as inventory bot, not as single-entry strategy.",
    ),
    RouteSpec(
        "SECTOR_DIVERGENCE_FAKE_CROWN",
        "Sector divergence fake-crown guard",
        ("fake crown", "sector", "divergence", "один говорит", "три молчат", "сектор"),
        "external_data_needed",
        "alt_sector_guard",
        "No run until sector basket OHLCV is cached; then test as alt-signal veto.",
        ("multi-asset sector OHLCV", "sector confirmation metric"),
        "BTC-only data cannot validate this.",
    ),
    RouteSpec(
        "DATA_ALIGNMENT_RUNTIME_GUARD",
        "Completed-candle and data alignment guard",
        ("alignment", "candle", "sync", "незакрыт", "синхронизац", "data"),
        "process_gate",
        "runtime_guard",
        "Keep in scheduler/observer rules: closed bars only, no lookahead, timestamp normalization.",
        ("scheduler state", "bar timestamps", "cache freshness"),
        "Process guard only; no alpha claim.",
    ),
    RouteSpec(
        "MICROSTRUCTURE_DELIST_DATA_QUALITY",
        "Microstructure/delisting data-quality guard",
        ("microstructure", "delisting", "open-interest", "unilateral", "data-integrity", "делист"),
        "guard_overlay",
        "data_quality_guard",
        "python tools/oi_funding_data_quality_collector.py --out-prefix docs/CRYPTO_GUIDES_OI_FUNDING_DATA_QUALITY_2026-06-19",
        ("exchange-specific OI/funding coverage", "symbol lifecycle/delist metadata"),
        "Treat as exclusion filter; not alpha.",
    ),
    RouteSpec(
        "DISCIPLINE_MAE_MFE_JOURNAL",
        "MAE/MFE journal and behavioral guard",
        ("mae", "mfe", "journal", "discipline", "drawdown", "psychology", "anti-self", "дневник"),
        "process_gate",
        "post_trade_analytics",
        "Route to trade journal enrichment after paper/live signals exist.",
        ("trade-level entry/exit", "MAE/MFE path", "journal reasons"),
        "Cannot be tested before trade-level path is populated.",
    ),
    RouteSpec(
        "DEX_MEV_EXECUTION_GUARD",
        "DEX/MEV execution guard",
        ("solana", "mev", "jito", "jupiter", "sandwich", "slippage"),
        "external_data_needed",
        "execution_guard",
        "Route to DEX range-bot BNB/Solana execution backlog; no BTC Binance test.",
        ("DEX quotes", "slippage", "private routing", "gas/tip bounds"),
        "Execution guard only; not a signal.",
    ),
)


OUT_OF_SCOPE_TERMS = ("monetization", "forensics", "ai-agent", "trust layer", "audit", "service", "nft")


def route_score(text: str, spec: RouteSpec) -> int:
    lower = text.lower()
    return sum(1 for keyword in spec.keywords if keyword.lower() in lower)


def page_snippets(text: str, keywords: tuple[str, ...], limit: int = 3) -> list[str]:
    lower_keywords = [keyword.lower() for keyword in keywords]
    sentences = re.split(r"(?<=[.!?。])\s+|\n+", text)
    out: list[str] = []
    for sentence in sentences:
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if len(sentence) < 40:
            continue
        lower = sentence.lower()
        if any(keyword in lower for keyword in lower_keywords):
            out.append(sentence[:320])
        if len(out) >= limit:
            break
    return out


def classify_page(page: dict[str, Any]) -> dict[str, Any]:
    blob = " ".join(
        str(page.get(key) or "")
        for key in ("path", "title", "category", "summary", "text_sample")
    ).lower()
    route_hits: list[dict[str, Any]] = []
    for spec in ROUTES:
        score = route_score(blob, spec)
        if score <= 0:
            continue
        route_hits.append(
            {
                "route_id": spec.route_id,
                "title": spec.title,
                "score": score,
                "testability": spec.testability,
                "test_type": spec.test_type,
                "suggested_test": spec.suggested_test,
                "data_requirements": list(spec.data_requirements),
                "reject_gate": spec.reject_gate,
                "snippets": page_snippets(str(page.get("text_sample") or ""), spec.keywords),
            }
        )
    route_hits.sort(key=lambda item: item["score"], reverse=True)
    if route_hits:
        primary = route_hits[0]
        disposition = primary["testability"]
    elif any(term in blob for term in OUT_OF_SCOPE_TERMS):
        primary = None
        disposition = "out_of_scope_for_btc_tests"
    else:
        primary = None
        disposition = "manual_review"
    return {
        "disposition": disposition,
        "primary_route": primary,
        "all_route_hits": route_hits[:4],
    }


def read_api_guides(base_url: str, timeout: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status, text, error = fetch_text(urllib.parse.urljoin(base_url, "/api/guides"), timeout)
    if error:
        return [], {"status": status, "error": error}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], {"status": status, "error": f"JSONDecodeError: {exc}"}
    if not isinstance(payload, list):
        return [], {"status": status, "error": "api_payload_not_list"}
    return [item for item in payload if isinstance(item, dict)], {"status": status, "count": len(payload)}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    homepage_status, homepage_html, homepage_error = fetch_text(base_url + "/", args.timeout_s)
    llms_status, llms_text, llms_error = fetch_text(base_url + "/llms.txt", args.timeout_s)
    api_guides, api_status = read_api_guides(base_url, args.timeout_s)
    guide_links = discover_guide_links(base_url, homepage_html, llms_text)

    pages: list[dict[str, Any]] = []
    for path in guide_links[: args.max_pages]:
        url = urllib.parse.urljoin(base_url, path)
        status, raw_html, error = fetch_text(url, args.timeout_s)
        text = strip_html(raw_html) if raw_html else ""
        metadata = extract_published(text)
        sample = compact_text(text, args.max_text_chars)
        page = {
            "url": url,
            "path": path,
            "status": status,
            "error": error,
            "title": extract_title(raw_html, text, path) if raw_html else path,
            "published_date": metadata["date"],
            "category": metadata["category"],
            "text_chars_scanned": len(sample),
            "text_sample": sample,
        }
        page["classification"] = classify_page(page)
        pages.append(page)

    route_buckets: dict[str, dict[str, Any]] = {}
    for page in pages:
        primary = page.get("classification", {}).get("primary_route")
        if not isinstance(primary, dict):
            continue
        route_id = str(primary.get("route_id"))
        bucket = route_buckets.setdefault(
            route_id,
            {
                "route_id": route_id,
                "title": primary.get("title"),
                "testability": primary.get("testability"),
                "test_type": primary.get("test_type"),
                "suggested_test": primary.get("suggested_test"),
                "data_requirements": primary.get("data_requirements"),
                "reject_gate": primary.get("reject_gate"),
                "sources": [],
            },
        )
        bucket["sources"].append(
            {
                "title": page.get("title"),
                "url": page.get("url"),
                "published_date": page.get("published_date"),
                "category": page.get("category"),
                "score": primary.get("score"),
                "snippets": primary.get("snippets"),
            }
        )

    disposition_counts: dict[str, int] = {}
    for page in pages:
        disposition = str(page.get("classification", {}).get("disposition") or "unknown")
        disposition_counts[disposition] = disposition_counts.get(disposition, 0) + 1

    test_queue = sorted(
        route_buckets.values(),
        key=lambda item: (
            item["testability"] not in {"codable_now_existing_data", "guard_overlay"},
            -len(item["sources"]),
            str(item["route_id"]),
        ),
    )
    return {
        "generated_at": now_iso(),
        "source": {
            "base_url": base_url,
            "homepage_status": homepage_status,
            "homepage_error": homepage_error,
            "llms_status": llms_status,
            "llms_error": llms_error,
            "api_guides": api_status,
            "discovered_guide_links": len(guide_links),
            "fetched_pages": len(pages),
        },
        "runtime_boundary": {
            "mode": "research_ingest_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
            "creates_paper_entry_intents": False,
        },
        "summary": {
            "disposition_counts": disposition_counts,
            "route_count": len(test_queue),
            "codable_now_routes": [item["route_id"] for item in test_queue if item["testability"] == "codable_now_existing_data"],
            "guard_overlay_routes": [item["route_id"] for item in test_queue if item["testability"] == "guard_overlay"],
            "external_data_routes": [item["route_id"] for item in test_queue if item["testability"] == "external_data_needed"],
        },
        "api_guides": api_guides,
        "test_queue": test_queue,
        "pages": pages,
        "decision": "crypto_guides_ingested_to_research_test_queue_no_trade_permission",
        "next_action": "run codable_now routes first; collect external data before testing crowd/sector/CTI claims",
        "can_trade": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    source = report["source"]
    summary = report["summary"]
    lines = [
        "# Crypto Guides Web Ingest",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Boundary",
        "",
        "- Research ingest only: no live logic, no paper-entry intents, no orders.",
        "- Website claims are routed into test queues; they are not accepted as proven edge.",
        "- Full raw page bodies are not used as package documentation; only compact rule/test summaries are kept.",
        "",
        "## Source",
        "",
        f"- Base URL: `{source['base_url']}`.",
        f"- Homepage status: `{source.get('homepage_status')}`.",
        f"- LLMs status: `{source.get('llms_status')}`.",
        f"- API guides status/count: `{source.get('api_guides', {}).get('status')}` / `{source.get('api_guides', {}).get('count')}`.",
        f"- Discovered / fetched guide pages: `{source['discovered_guide_links']}` / `{source['fetched_pages']}`.",
        "",
        "## Summary",
        "",
        f"- Disposition counts: `{summary['disposition_counts']}`.",
        f"- Codable now: `{', '.join(summary['codable_now_routes']) or 'none'}`.",
        f"- Guard overlays: `{', '.join(summary['guard_overlay_routes']) or 'none'}`.",
        f"- External-data blocked: `{', '.join(summary['external_data_routes']) or 'none'}`.",
        "",
        "## Test Queue",
        "",
        "| Route | Type | Testability | Sources | Suggested first test |",
        "|---|---|---|---:|---|",
    ]
    for item in report["test_queue"]:
        command = str(item.get("suggested_test") or "").replace("|", "\\|")
        lines.append(
            f"| `{item['route_id']}` | `{item['test_type']}` | `{item['testability']}` | `{len(item.get('sources') or [])}` | `{command}` |"
        )
    lines.extend(["", "## Route Details", ""])
    for item in report["test_queue"]:
        lines.extend(
            [
                f"### {item['route_id']} - {item.get('title')}",
                "",
                f"- Testability: `{item.get('testability')}`.",
                f"- Test type: `{item.get('test_type')}`.",
                f"- Data requirements: `{', '.join(item.get('data_requirements') or [])}`.",
                f"- Reject gate: {item.get('reject_gate')}",
                "",
                "Suggested first test:",
                "",
                "```powershell",
                str(item.get("suggested_test") or "manual review"),
                "```",
                "",
                "Sources:",
                "",
            ]
        )
        for source in item.get("sources", [])[:8]:
            lines.append(f"- [{source.get('title')}]({source.get('url')}) score `{source.get('score')}`.")
        lines.append("")
    lines.extend(["## All Pages", "", "| Disposition | Title | URL |", "|---|---|---|"])
    for page in report["pages"]:
        classification = page.get("classification") if isinstance(page.get("classification"), dict) else {}
        lines.append(f"| `{classification.get('disposition')}` | {page.get('title')} | {page.get('url')} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Ingest Crypto Guides website into research/test queue.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--max-text-chars", type=int, default=14000)
    parser.add_argument("--timeout-s", type=int, default=20)
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    args = parser.parse_args()

    report = build_report(args)
    out_prefix = resolve_path(args.out_prefix)
    write_json(out_prefix.with_suffix(".json"), report)
    write_text(out_prefix.with_suffix(".md"), render_markdown(report))
    print(
        json.dumps(
            {
                "status": "ok",
                "fetched_pages": report["source"]["fetched_pages"],
                "route_count": report["summary"]["route_count"],
                "codable_now_routes": report["summary"]["codable_now_routes"],
                "guard_overlay_routes": report["summary"]["guard_overlay_routes"],
                "external_data_routes": report["summary"]["external_data_routes"],
                "json": rel_path(out_prefix.with_suffix(".json")),
                "md": rel_path(out_prefix.with_suffix(".md")),
                "can_trade": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
