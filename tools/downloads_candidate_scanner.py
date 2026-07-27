#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOADS = Path.home() / "Downloads"
PROCESSED_REGISTRY_PATHS = (
    ROOT / "docs" / "DOCUMENT_PROCESSING_REGISTRY_2026-06-04.json",
    ROOT / "docs" / "STRATEGY_DISCOVERY_REGISTRY_2026-06-08.json",
)

SENSITIVE_KEYWORDS = {
    "client_secret",
    "secret",
    "private",
    "mnemonic",
    "seed",
    "backup-codes",
    "backup_codes",
    "password",
    "token",
    "api_key",
}

HIGH_KEYWORDS = {
    "btc",
    "bitcoin",
    "btcusdt",
    "crypto",
    "крипто",
    "trading",
    "трейдинг",
    "strategy",
    "strategies",
    "стратег",
    "futures",
    "фьючерс",
    "derivative",
    "дериватив",
    "microstructure",
    "микроструктур",
    "order flow",
    "ордер",
    "binance",
    "bybit",
    "hyperliquid",
    "hft",
}

MEDIUM_KEYWORDS = {
    "bot",
    "бот",
    "agent",
    "агент",
    "data",
    "данн",
    "research",
    "исслед",
    "risk",
    "риск",
    "liquidity",
    "ликвид",
    "market",
    "рын",
    "dashboard",
    "monitor",
    "delist",
}

SUPPORTED_EXTENSIONS = {".docx", ".md", ".txt", ".csv", ".zip", ".py", ".json", ".xlsx"}
RESEARCH_EXTENSIONS = {".docx", ".md", ".txt", ".csv", ".xlsx"}
RUNTIME_EXTENSIONS = {".zip", ".py", ".json"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_out_prefix() -> Path:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Path(f"docs/DOWNLOADS_TRADING_CANDIDATE_SCAN_{date}")


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def hits(name: str, keywords: set[str]) -> list[str]:
    lower = name.lower()
    return sorted(word for word in keywords if word in lower)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def add_processed_record(
    *,
    item: dict[str, Any],
    evidence_path: Path,
    hashes: dict[str, set[str]],
    names: dict[str, set[str]],
) -> None:
    digest = item.get("sha256") or item.get("source_sha256")
    source = item.get("source") or item.get("path")
    evidence = evidence_path.relative_to(ROOT).as_posix()
    if isinstance(digest, str) and len(digest) == 64:
        hashes.setdefault(digest.lower(), set()).add(evidence)
    if isinstance(source, str) and source:
        names.setdefault(Path(source).name.casefold(), set()).add(evidence)


def load_processed_index() -> dict[str, dict[str, set[str]]]:
    hashes: dict[str, set[str]] = {}
    names: dict[str, set[str]] = {}

    for path in PROCESSED_REGISTRY_PATHS:
        report = load_json(path)
        if not report:
            continue
        for item in report.get("processed", []):
            if isinstance(item, dict):
                add_processed_record(item=item, evidence_path=path, hashes=hashes, names=names)

    intake_paths = set((ROOT / "docs").glob("*_DOCX_INTAKE_*.json"))
    intake_paths.update((ROOT / "docs").glob("DOCUMENT_PROCESSING_*.json"))
    for path in sorted(intake_paths):
        if "DRY_RUN" in path.name or "REGISTRY" in path.name:
            continue
        report = load_json(path)
        if not report:
            continue
        source = report.get("source")
        if isinstance(source, dict):
            add_processed_record(item=source, evidence_path=path, hashes=hashes, names=names)
        else:
            add_processed_record(item=report, evidence_path=path, hashes=hashes, names=names)

    return {"hashes": hashes, "names": names}


def classify(path: Path, processed_index: dict[str, dict[str, set[str]]] | None = None) -> dict[str, Any]:
    name = path.name
    sensitive_hits = hits(name, SENSITIVE_KEYWORDS)
    high_hits = hits(name, HIGH_KEYWORDS)
    medium_hits = hits(name, MEDIUM_KEYWORDS)
    extension = path.suffix.lower()
    score = len(high_hits) * 3 + len(medium_hits)
    if extension in RESEARCH_EXTENSIONS:
        score += 1
    if extension in RUNTIME_EXTENSIONS:
        score += 1

    if sensitive_hits:
        relevance = "excluded_sensitive"
        action = "do_not_read_or_import"
    elif extension not in SUPPORTED_EXTENSIONS:
        relevance = "ignored_extension"
        action = "ignore"
    elif score >= 6:
        relevance = "high"
        action = "process_next_if_not_duplicate"
    elif score >= 3:
        relevance = "medium"
        action = "review_filename_then_process_if_useful"
    elif score >= 1:
        relevance = "low"
        action = "archive_reference_or_ignore"
    else:
        relevance = "none"
        action = "ignore"

    digest: str | None = None
    processing_status = "not_checked_unsupported_or_sensitive"
    processing_evidence: list[str] = []
    processed_index = processed_index or {"hashes": {}, "names": {}}
    if not sensitive_hits and extension in SUPPORTED_EXTENSIONS:
        digest = sha256(path)
        hash_evidence = processed_index["hashes"].get(digest.lower(), set())
        name_evidence = processed_index["names"].get(name.casefold(), set())
        if hash_evidence:
            processing_status = "processed_exact_hash"
            processing_evidence = sorted(hash_evidence)
            action = "already_processed_no_repeat"
        elif name_evidence:
            processing_status = "name_seen_hash_changed"
            processing_evidence = sorted(name_evidence)
        else:
            processing_status = "unprocessed"

    return {
        "name": name,
        "path": str(path),
        "extension": extension,
        "size_bytes": path.stat().st_size,
        "last_write_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "relevance": relevance,
        "score": score,
        "keyword_hits": {
            "high": high_hits,
            "medium": medium_hits,
            "sensitive": sensitive_hits,
        },
        "recommended_action": action,
        "sha256": digest,
        "processing_status": processing_status,
        "processing_evidence": processing_evidence,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Downloads Trading Candidate Scan",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Downloads dir: `{report['downloads_dir']}`",
        "",
        "## Boundary",
        "",
        "- Filename/type scan plus SHA-256 matching for supported non-sensitive files.",
        "- Does not read secrets, API keys, backup codes or private material.",
        "- Does not move or import files.",
        "- High relevance means 'candidate for controlled processing', not 'already useful code'.",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Top Candidates",
            "",
        "| Relevance | Score | File | Size | Processing | Action | Hits |",
        "|---|---:|---|---:|---|---|---|",
        ]
    )
    for item in report["top_candidates"]:
        keyword_hits = item["keyword_hits"]
        hits_joined = ", ".join(keyword_hits.get("high", []) + keyword_hits.get("medium", [])) or "-"
        lines.append(
            f"| `{item['relevance']}` | `{item['score']}` | `{item['name']}` | `{item['size_bytes']}` | "
            f"`{item['processing_status']}` | "
            f"`{item['recommended_action']}` | `{hits_joined}` |"
        )
    lines.extend(
        [
            "",
            "## Excluded Sensitive Filenames",
            "",
        ]
    )
    sensitive = [item for item in report["items"] if item["relevance"] == "excluded_sensitive"]
    if not sensitive:
        lines.append("- None detected.")
    else:
        for item in sensitive[:20]:
            lines.append(f"- `{item['name']}`: excluded from reading/import.")
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            "- Process only high-relevance items marked `unprocessed` or `name_seen_hash_changed`.",
            "- Route DOCX/MD one at a time through a bounded intake; exact-hash matches must not be repeated.",
            "- Convert only deterministic, testable rules into code.",
            "- Keep HFT/low-latency architecture docs as reference unless the needed data/execution layer exists.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Safe filename-based scan of Downloads for trading-system candidates")
    parser.add_argument("--downloads-dir", default=str(DEFAULT_DOWNLOADS))
    parser.add_argument("--out-prefix", default=str(default_out_prefix()))
    parser.add_argument("--top-n", type=int, default=50)
    args = parser.parse_args()

    downloads = Path(args.downloads_dir)
    processed_index = load_processed_index()
    items: list[dict[str, Any]] = []
    if downloads.exists():
        for path in downloads.iterdir():
            if path.is_file():
                try:
                    items.append(classify(path, processed_index))
                except OSError:
                    continue
    items.sort(key=lambda item: (item["score"], item["last_write_utc"]), reverse=True)
    counts: dict[str, int] = {}
    processing_counts: dict[str, int] = {}
    for item in items:
        counts[item["relevance"]] = counts.get(item["relevance"], 0) + 1
        status = item["processing_status"]
        processing_counts[status] = processing_counts.get(status, 0) + 1
    top = [
        item
        for item in items
        if item["relevance"] in {"high", "medium"} and item["processing_status"] != "processed_exact_hash"
    ][: args.top_n]
    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "safe_downloads_filename_scan_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "downloads_dir": str(downloads),
        "counts": counts,
        "processing_counts": processing_counts,
        "top_candidates": top,
        "items": items,
        "can_trade": False,
    }

    out_prefix = resolve_path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = out_prefix.with_suffix(".json")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "scanned": len(items),
                "counts": counts,
                "processing_counts": processing_counts,
                "top_count": len(top),
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
