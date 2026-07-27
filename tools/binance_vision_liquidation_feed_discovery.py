#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
S3_LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DATA_ROOT = "https://data.binance.vision"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def request_text(url: str, timeout: float) -> tuple[int, str, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "TradingOS-liquidation-discovery/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            return status, response.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        return int(exc.code), "", str(exc)
    except Exception as exc:  # pragma: no cover - network/environment dependent
        return 0, "", repr(exc)


def list_prefix(prefix: str, timeout: float) -> dict[str, Any]:
    params = urllib.parse.urlencode({"delimiter": "/", "prefix": prefix})
    status, body, error = request_text(f"{S3_LIST_URL}?{params}", timeout)
    result: dict[str, Any] = {"prefix": prefix, "status": status, "error": error, "keys": [], "common_prefixes": []}
    if status != 200 or not body:
        return result
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        result["error"] = f"xml_parse_error: {exc}"
        return result
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"
    result["keys"] = [node.text for node in root.findall(f".//{namespace}Contents/{namespace}Key") if node.text]
    result["common_prefixes"] = [
        node.text for node in root.findall(f".//{namespace}CommonPrefixes/{namespace}Prefix") if node.text
    ]
    return result


def discover_candidate(prefix: str, timeout: float, limit_keys: int) -> dict[str, Any]:
    listing = list_prefix(prefix, timeout)
    keys = [key for key in listing.get("keys", []) if key.endswith(".zip")]
    sample_keys = sorted(keys)[-limit_keys:]
    return {
        "prefix": prefix,
        "status": listing["status"],
        "error": listing["error"],
        "zip_count": len(keys),
        "sample_keys": sample_keys,
        "sample_urls": [f"{DATA_ROOT}/{key}" for key in sample_keys],
        "available": bool(keys),
    }


def month_range(start: str, end: str) -> list[str]:
    start_dt = datetime.strptime(start, "%Y-%m").date().replace(day=1)
    end_dt = datetime.strptime(end, "%Y-%m").date().replace(day=1)
    out: list[str] = []
    cursor = start_dt
    while cursor <= end_dt:
        out.append(cursor.strftime("%Y-%m"))
        year = cursor.year + int(cursor.month == 12)
        month = 1 if cursor.month == 12 else cursor.month + 1
        cursor = date(year, month, 1)
    return out


def day_range(start: str, end: str) -> list[str]:
    start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end, "%Y-%m-%d").date()
    out: list[str] = []
    cursor = start_dt
    while cursor <= end_dt:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def probe_exact_files(symbol: str, market: str, timeout: float, months: list[str], days: list[str]) -> list[dict[str, Any]]:
    symbol = symbol.upper()
    checks: list[tuple[str, str]] = []
    for month in months:
        checks.extend(
            [
                (
                    "monthly_liquidationSnapshot",
                    f"data/futures/{market}/monthly/liquidationSnapshot/{symbol}/{symbol}-liquidationSnapshot-{month}.zip",
                ),
                (
                    "monthly_forceOrder",
                    f"data/futures/{market}/monthly/forceOrder/{symbol}/{symbol}-forceOrder-{month}.zip",
                ),
            ]
        )
    for day in days:
        checks.extend(
            [
                (
                    "daily_liquidationSnapshot",
                    f"data/futures/{market}/daily/liquidationSnapshot/{symbol}/{symbol}-liquidationSnapshot-{day}.zip",
                ),
                (
                    "daily_forceOrder",
                    f"data/futures/{market}/daily/forceOrder/{symbol}/{symbol}-forceOrder-{day}.zip",
                ),
            ]
        )
    results: list[dict[str, Any]] = []
    for label, key in checks:
        url = f"{DATA_ROOT}/{key}.CHECKSUM"
        status, body, error = request_text(url, timeout)
        checksum = None
        if status == 200 and body.strip():
            checksum = body.strip().split()[0]
        results.append(
            {
                "candidate": label,
                "key": key,
                "checksum_url": url,
                "status": status,
                "available": status == 200,
                "checksum": checksum,
                "error": error,
            }
        )
    return results


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Binance Vision Liquidation Real-Feed Discovery",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Decision: `{report['decision']}`",
        f"- Can trade: `{str(report['can_trade']).lower()}`",
        f"- Symbol: `{report['symbol']}`",
        "",
        "## Discovery Results",
        "",
        "| Source | Available | Zip count | Sample keys |",
        "|---|---:|---:|---|",
    ]
    for item in report["prefix_discovery"]:
        samples = "<br>".join(item["sample_keys"][:3]) if item["sample_keys"] else ""
        lines.append(f"| `{item['prefix']}` | `{str(item['available']).lower()}` | `{item['zip_count']}` | {samples} |")
    lines.extend(["", "## Exact File Probes", "", "| Candidate | Status | Available | Key |", "|---|---:|---:|---|"])
    for item in report["exact_file_probes"]:
        lines.append(f"| `{item['candidate']}` | `{item['status']}` | `{str(item['available']).lower()}` | `{item['key']}` |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            report["interpretation"],
            "",
            "## Source Notes",
            "",
            "- Binance USD-M futures websocket force liquidation stream is `<symbol>@forceOrder`.",
            "- Binance Vision exposes public archive prefixes under `data/futures/.../liquidationSnapshot/` when files exist.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover true liquidation/force-order archive availability in Binance Vision")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--market", choices=["um", "cm"], default="um")
    parser.add_argument("--month-start", default="2026-01")
    parser.add_argument("--month-end", default="2026-05")
    parser.add_argument("--day-start", default="2026-06-20")
    parser.add_argument("--day-end", default="2026-06-29")
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument("--limit-keys", type=int, default=5)
    parser.add_argument("--out-prefix", default="docs/LIQUIDATION_REAL_FEED_DISCOVERY_2026-06-30")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    prefixes = [
        f"data/futures/{args.market}/daily/liquidationSnapshot/{symbol}/",
        f"data/futures/{args.market}/monthly/liquidationSnapshot/{symbol}/",
        f"data/futures/{args.market}/daily/forceOrder/{symbol}/",
        f"data/futures/{args.market}/monthly/forceOrder/{symbol}/",
    ]
    prefix_discovery = [discover_candidate(prefix, args.timeout_sec, args.limit_keys) for prefix in prefixes]
    exact_file_probes = probe_exact_files(
        symbol,
        args.market,
        args.timeout_sec,
        month_range(args.month_start, args.month_end),
        day_range(args.day_start, args.day_end),
    )
    found_archive = any(item["available"] for item in prefix_discovery) or any(
        item["available"] for item in exact_file_probes
    )
    decision = "real_liquidation_archive_discovered_schema_backfill_next" if found_archive else "no_archive_discovered_forward_websocket_required"
    interpretation = (
        "At least one archive candidate exists. The next step is a bounded downloader/parser and schema audit before any strategy consumes it."
        if found_archive
        else "No tested Binance Vision archive candidate returned files. Treat historical liquidation research as blocked on a real source; use a forward websocket forceOrder collector or a paid event-level feed."
    )
    report = {
        "generated_at": now_iso(),
        "tool": "tools/binance_vision_liquidation_feed_discovery.py",
        "symbol": symbol,
        "market": args.market,
        "decision": decision,
        "can_trade": False,
        "contract": "configs/LIQUIDATION_REAL_FEED_CONTRACT.json",
        "prefix_discovery": prefix_discovery,
        "exact_file_probes": exact_file_probes,
        "found_archive": found_archive,
        "interpretation": interpretation,
        "source_references": [
            "https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Liquidation-Order-Streams",
            "https://data.binance.vision/?prefix=data%2Ffutures%2Fum%2Fdaily%2FliquidationSnapshot%2F",
        ],
    }

    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"decision": decision, "out": portable_path(out.with_suffix(".json"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
