#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
DOWNLOADS_ROOT = Path.home() / "Downloads"
DEFAULT_REGISTRY = ROOT / "docs" / "STRATEGY_DISCOVERY_REGISTRY_2026-06-08.json"
DEFAULT_OUT_PREFIX = ROOT / "docs" / "STRATEGY_DISCOVERY_PIPELINE_2026-06-08"
DEFAULT_BACKLOG = ROOT / "docs" / "STRATEGY_DISCOVERY_BACKLOG_2026-06-08.md"
DEFAULT_PROCESSED_DIR = WORKSPACE_ROOT / "processed_docs"

SUPPORTED_TEXT_EXTENSIONS = {".docx", ".md", ".txt", ".json", ".py", ".csv"}
SUPPORTED_ARCHIVE_EXTENSIONS = {".zip"}
SUPPORTED_EXTENSIONS = SUPPORTED_TEXT_EXTENSIONS | SUPPORTED_ARCHIVE_EXTENSIONS
HEAVY_TEXT_LIMIT_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 160_000

EXCLUDED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "data",
    "logs",
    "processed_docs",
    "_processed_archive",
    "MAX_BitEvo_ALL_IN_ONE_UNIFIED_20260323",
}

SENSITIVE_KEYWORDS = {
    "client_secret",
    "secret",
    "private_key",
    "mnemonic",
    "seed",
    "backup-codes",
    "backup_codes",
    "password",
    "passwd",
    "token",
    "api_key",
    "apikey",
    "credential",
}

STRATEGY_KEYWORDS = {
    "strategy",
    "strategies",
    "setup",
    "signal",
    "entry",
    "exit",
    "stop",
    "take profit",
    "tp",
    "sl",
    "rr",
    "risk reward",
    "winrate",
    "expectancy",
    "backtest",
    "walk-forward",
    "holdout",
    "oos",
    "edge",
    "trading",
    "бот",
    "стратег",
    "сетап",
    "сигнал",
    "вход",
    "выход",
    "стоп",
    "тейк",
    "винрейт",
    "математическое ожидание",
    "прибыль",
    "доходность",
    "бэктест",
}

MARKET_FEATURE_KEYWORDS = {
    "btc",
    "btcusdt",
    "bitcoin",
    "funding",
    "open interest",
    "oi",
    "liquidation",
    "liquidity",
    "cvd",
    "delta",
    "order flow",
    "book ticker",
    "depth",
    "spot",
    "perp",
    "basis",
    "volume",
    "vwap",
    "atr",
    "ema",
    "rsi",
    "donchian",
    "breakout",
    "mean reversion",
    "range",
    "sweep",
    "fvg",
    "btc.d",
    "eth/btc",
    "фандинг",
    "открытый интерес",
    "ликвидац",
    "ликвидн",
    "дельта",
    "объем",
    "пробой",
    "диапазон",
    "свип",
}

RUNTIME_KEYWORDS = {
    "binance",
    "bybit",
    "hyperliquid",
    "api",
    "websocket",
    "executor",
    "collector",
    "database",
    "python",
    "docker",
    "testnet",
    "paper",
    "order",
}

ENTRY_PATTERNS = [
    r"\bif\b.+?\b(enter|entry|long|short|buy|sell)\b",
    r"\bwhen\b.+?\b(enter|entry|long|short|buy|sell)\b",
    r"\bentry\b.+",
    r"\bsetup\b.+",
    r"\bstrategy\b.+",
    r"\bвход\b.+",
    r"\bесли\b.+?\b(лонг|шорт|покуп|продаж|вход)\b",
    r"\bсетап\b.+",
    r"\bстратегия\b.+",
]


STRATEGY_KEYWORDS |= {
    "бот",
    "стратег",
    "сетап",
    "сигнал",
    "вход",
    "выход",
    "стоп",
    "тейк",
    "винрейт",
    "математическое ожидание",
    "прибыль",
    "доходность",
    "бэктест",
    "риск ревард",
    "риск-ревард",
}

MARKET_FEATURE_KEYWORDS |= {
    "фандинг",
    "открытый интерес",
    "ликвидац",
    "ликвидн",
    "дельта",
    "объем",
    "объём",
    "пробой",
    "диапазон",
    "свип",
    "доминация",
}

ENTRY_PATTERNS.extend(
    [
        r"\bвход\b.+",
        r"\bесли\b.+?\b(лонг|шорт|покуп|продаж|вход)\b",
        r"\bсетап\b.+",
        r"\bстратегия\b.+",
    ]
)

MOJIBAKE_MARKERS = ("Ð", "Ñ", "Â", "â", "�")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return slug[:110] or "item"


def is_sensitive(path: Path) -> bool:
    lower = path.name.lower()
    return any(keyword in lower for keyword in SENSITIVE_KEYWORDS)


def is_excluded_workspace_path(path: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(WORKSPACE_ROOT.resolve()).parts
    except ValueError:
        return False
    return bool(set(rel_parts) & EXCLUDED_DIR_NAMES)


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1251", "cp1252"):
        try:
            return raw.decode(encoding)[:MAX_TEXT_CHARS]
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]


def read_docx(path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            if name not in archive.namelist():
                continue
            root = ET.fromstring(archive.read(name))
            for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                texts = [
                    node.text or ""
                    for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
                ]
                line = "".join(texts).strip()
                if line:
                    parts.append(line)
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def read_csv_head(path: Path, max_rows: int = 80) -> str:
    lines: list[str] = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        for index, row in enumerate(reader):
            if index >= max_rows:
                break
            lines.append(", ".join(row[:20]))
    return "\n".join(lines)[:MAX_TEXT_CHARS]


def read_zip_listing(path: Path, max_items: int = 300) -> str:
    with zipfile.ZipFile(path) as archive:
        names = [item.filename for item in archive.infolist() if not item.is_dir()]
    return "\n".join(names[:max_items])


def read_candidate_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".json", ".py"}:
        if path.stat().st_size > HEAVY_TEXT_LIMIT_BYTES:
            return "", "skipped_heavy_text"
        return read_text_file(path), "full_text"
    if suffix == ".csv":
        if path.stat().st_size > HEAVY_TEXT_LIMIT_BYTES:
            return read_csv_head(path), "csv_head_heavy_file"
        return read_csv_head(path), "csv_head"
    if suffix == ".docx":
        return read_docx(path), "docx_text"
    if suffix == ".zip":
        return read_zip_listing(path), "zip_listing_only"
    return "", "unsupported"


def text_quality(path: Path, text: str) -> dict[str, Any]:
    sample = f"{path.name}\n{text[:20_000]}"
    marker_count = sum(sample.count(marker) for marker in MOJIBAKE_MARKERS)
    replacement_chars = sample.count("�")
    cyrillic_count = sum(1 for char in sample if "а" <= char.lower() <= "я" or char == "ё")
    total_chars = max(1, len(sample))
    likely_mojibake = marker_count >= 8 and cyrillic_count < marker_count
    return {
        "likely_mojibake": likely_mojibake,
        "mojibake_marker_count": marker_count,
        "replacement_chars": replacement_chars,
        "cyrillic_ratio": round(cyrillic_count / total_chars, 6),
        "note": "review encoding before coding hypotheses" if likely_mojibake else "ok",
    }


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"generated_at": now_iso(), "processed": []}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def processed_receipt_hashes(receipts_dir: Path) -> set[str]:
    hashes: set[str] = set()
    if not receipts_dir.is_dir():
        return hashes
    for path in receipts_dir.glob("*INTAKE*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("processing_status") or "")
        decision = str(payload.get("decision") or "")
        if not (status.startswith("processed") or decision.startswith("processed")):
            continue
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        digest = str(source.get("sha256") or source.get("sha256_before") or "").strip().lower()
        if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
            hashes.add(digest)
    return hashes


def processed_hashes(registry: dict[str, Any], receipts_dir: Path | None = None) -> set[str]:
    hashes = {
        str(item.get("sha256")).strip().lower()
        for item in registry.get("processed", [])
        if item.get("sha256")
    }
    hashes.update(processed_receipt_hashes(receipts_dir or (ROOT / "docs")))
    return hashes


def source_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return f"external/{path.name}"


def discover_files(
    roots: list[Path],
    registry: dict[str, Any],
    receipts_dir: Path | None = None,
) -> list[Path]:
    seen = processed_hashes(registry, receipts_dir)
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        iterator = root.rglob("*") if root != DOWNLOADS_ROOT else root.iterdir()
        for path in iterator:
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if is_sensitive(path):
                continue
            if root == WORKSPACE_ROOT and is_excluded_workspace_path(path):
                continue
            try:
                digest = sha256(path)
            except OSError:
                continue
            if digest in seen:
                continue
            candidates.append(path)
    return sorted(candidates, key=lambda item: (rank_filename(item), item.stat().st_mtime), reverse=True)


def keyword_hits(text: str, words: set[str]) -> list[str]:
    lower = text.lower()
    return sorted(word for word in words if word in lower)


def rank_filename(path: Path) -> int:
    lower = path.name.lower()
    score = 0
    score += 5 * sum(1 for word in STRATEGY_KEYWORDS if word in lower)
    score += 3 * sum(1 for word in MARKET_FEATURE_KEYWORDS if word in lower)
    score += 2 * sum(1 for word in RUNTIME_KEYWORDS if word in lower)
    if path.suffix.lower() in {".docx", ".md", ".py", ".zip"}:
        score += 2
    return score


def split_sentences(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []
    return [item.strip() for item in re.split(r"(?<=[.!?。])\s+|(?<=\.)\s+|(?<=;)\s+", compact) if item.strip()]


def extract_rule_snippets(text: str, limit: int = 12) -> list[str]:
    snippets: list[str] = []
    sentences = split_sentences(text)
    for sentence in sentences:
        lower = sentence.lower()
        if any(re.search(pattern, lower) for pattern in ENTRY_PATTERNS):
            snippets.append(sentence[:500])
        elif any(word in lower for word in ("funding", "open interest", "oi", "cvd", "liquidation", "expectancy", "winrate", "вход", "фандинг", "ликвидац")):
            if any(word in lower for word in ("если", "if", "when", "entry", "вход", "signal", "setup", "стратег")):
                snippets.append(sentence[:500])
        if len(snippets) >= limit:
            break
    return snippets


def extract_numeric_claims(text: str) -> dict[str, list[str]]:
    patterns = {
        "winrate": r"(?i)(?:winrate|win rate|винрейт|прибыльн\w*\s+сдел\w*)[^.\n]{0,80}?(\d+(?:[.,]\d+)?)\s*%",
        "expectancy": r"(?i)(?:expectancy|ожидани\w*|expectancy_r)[^.\n]{0,80}?([+-]?\d+(?:[.,]\d+)?)\s*R?",
        "risk_reward": r"(?i)(?:rr|risk.?reward|риск.?ревард|risk/reward)[^.\n]{0,80}?(\d+(?:[.,]\d+)?)\s*[:/x]\s*(\d+(?:[.,]\d+)?)",
        "trades": r"(?i)(?:trades|сдел\w*|выборк\w*)[^.\n]{0,60}?(\d{2,6})",
        "hold": r"(?i)(?:hold|holding|max hold|удерж\w*)[^.\n]{0,60}?(\d+(?:[.,]\d+)?)\s*(?:h|hour|час|дн|day)",
    }
    claims: dict[str, list[str]] = {}
    for key, pattern in patterns.items():
        matches = []
        for match in re.finditer(pattern, text):
            matches.append(match.group(0)[:220])
            if len(matches) >= 12:
                break
        if matches:
            claims[key] = matches
    return claims


def classify_candidate(path: Path, text: str, read_mode: str) -> dict[str, Any]:
    combined = f"{path.name}\n{text}"
    strategy_hits = keyword_hits(combined, STRATEGY_KEYWORDS)
    market_hits = keyword_hits(combined, MARKET_FEATURE_KEYWORDS)
    runtime_hits = keyword_hits(combined, RUNTIME_KEYWORDS)
    snippets = extract_rule_snippets(text)
    numeric_claims = extract_numeric_claims(text)
    quality = text_quality(path, text)
    score = 0
    score += min(len(strategy_hits), 12) * 4
    score += min(len(market_hits), 16) * 3
    score += min(len(runtime_hits), 10) * 2
    score += len(snippets) * 5
    score += sum(len(value) for value in numeric_claims.values()) * 2
    if read_mode == "zip_listing_only":
        score += 5 if any(name in text.lower() for name in ("backtest", "strategy", "bot", "README".lower())) else 0
    if read_mode.startswith("skipped"):
        score -= 10
    if quality["likely_mojibake"]:
        score -= 8

    actions: list[str] = []
    if snippets and ("backtest" in strategy_hits or "expectancy" in strategy_hits or numeric_claims):
        actions.append("extract_backtest_hypothesis")
    if market_hits:
        actions.append("extract_feature_filter")
    if runtime_hits and path.suffix.lower() in {".zip", ".py", ".json"}:
        actions.append("inspect_runtime_candidate")
    if numeric_claims:
        actions.append("verify_numeric_claims")
    if quality["likely_mojibake"]:
        actions.append("review_text_encoding")
    if not actions:
        actions.append("archive_reference_only")

    if score >= 80:
        priority = "high"
    elif score >= 45:
        priority = "medium"
    elif score >= 20:
        priority = "low"
    else:
        priority = "reference"

    return {
        "score": score,
        "priority": priority,
        "read_mode": read_mode,
        "actions": actions,
        "keyword_hits": {
            "strategy": strategy_hits[:20],
            "market_features": market_hits[:24],
            "runtime": runtime_hits[:16],
        },
        "rule_snippets": snippets,
        "numeric_claims": numeric_claims,
        "text_quality": quality,
        "can_trade": False,
    }


def copy_to_processed(path: Path, processed_dir: Path) -> Path:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        rel = path.resolve().relative_to(WORKSPACE_ROOT.resolve())
        target = processed_dir / date / "strategy_discovery" / rel
    except ValueError:
        target = processed_dir / date / "strategy_discovery" / "external" / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = target.with_name(f"{target.stem}_{sha256(path)[:8]}{target.suffix}")
    resolved = target.resolve()
    if processed_dir.resolve() not in resolved.parents:
        raise RuntimeError(f"unsafe processed target: {target}")
    shutil.copy2(path, target)
    return target


def make_item(path: Path, digest: str, text: str, read_mode: str, processed_copy: Path | None) -> dict[str, Any]:
    classification = classify_candidate(path, text, read_mode)
    return {
        "processed_at": now_iso(),
        "source": str(path),
        "source_rel": source_rel(path),
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "extension": path.suffix.lower(),
        "processed_copy": str(processed_copy) if processed_copy else None,
        "processed_copy_rel": processed_copy.relative_to(WORKSPACE_ROOT).as_posix() if processed_copy else None,
        "classification": classification,
        "can_trade": False,
    }


def render_item_markdown(item: dict[str, Any]) -> str:
    c = item["classification"]
    lines = [
        "# Strategy Discovery Item",
        "",
        f"Processed: `{item['processed_at']}`",
        f"Source: `{item['source_rel']}`",
        f"SHA256: `{item['sha256']}`",
        "",
        "## Decision",
        "",
        f"- Priority: `{c['priority']}`.",
        f"- Score: `{c['score']}`.",
        f"- Actions: `{', '.join(c['actions'])}`.",
        f"- Read mode: `{c['read_mode']}`.",
        f"- Text quality: `{c['text_quality']['note']}`.",
        f"- Can trade: `false`.",
        "",
        "## Numeric Claims",
        "",
    ]
    if not c["numeric_claims"]:
        lines.append("- None extracted.")
    else:
        for key, values in c["numeric_claims"].items():
            lines.append(f"- `{key}`:")
            for value in values[:8]:
                lines.append(f"  - {value}")
    lines.extend(["", "## Rule Snippets", ""])
    if not c["rule_snippets"]:
        lines.append("- No deterministic rule snippets extracted.")
    else:
        for snippet in c["rule_snippets"][:10]:
            lines.append(f"- {snippet}")
    lines.extend(
        [
            "",
            "## Next Step",
            "",
            "- Convert only deterministic snippets into a research-only evaluator.",
            "- Numeric claims must be verified by local backtest/OOS before promotion.",
            "- This item does not create live or paper trade permission.",
            "",
        ]
    )
    return "\n".join(lines)


def render_backlog(items: list[dict[str, Any]]) -> str:
    lines = [
        "# Strategy Discovery Backlog",
        "",
        f"Generated: `{now_iso()}`",
        "",
        "## Boundary",
        "",
        "- Backlog for finding potentially plus-EV strategy hypotheses.",
        "- No item here is trade permission.",
        "- Promotion path remains: deterministic rule -> backtest -> OOS/holdout -> risk/reward gate -> paper/live-review.",
        "",
        "## Top Items",
        "",
        "| Priority | Score | Source | Actions | Main Claims |",
        "|---|---:|---|---|---|",
    ]
    for item in sorted(items, key=lambda value: value["classification"]["score"], reverse=True)[:40]:
        c = item["classification"]
        claims = []
        for key, values in c["numeric_claims"].items():
            if values:
                claims.append(f"{key}: {values[0][:80]}")
        claim_text = "<br>".join(claims[:3]) or "-"
        lines.append(
            f"| `{c['priority']}` | `{c['score']}` | `{item['source_rel']}` | `{', '.join(c['actions'])}` | {claim_text} |"
        )
    lines.extend(
        [
            "",
            "## Next Coding Queue",
            "",
            "1. Prefer `extract_backtest_hypothesis` items with concrete entry/exit/stop rules.",
            "2. Turn each into a small evaluator with fees/slippage and no overlap.",
            "3. Reject anything with small sample, unstable folds or holdout failure.",
            "4. Use market microstructure features first as filters/abstention, not entry permission.",
            "",
        ]
    )
    return "\n".join(lines)


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        "# Strategy Discovery Pipeline",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Scanned candidates: `{report['scanned_candidates']}`.",
        f"- Processed now: `{report['processed_now']}`.",
        f"- Remaining candidates: `{report['remaining_candidates']}`.",
        f"- High priority processed: `{report['priority_counts'].get('high', 0)}`.",
        f"- Medium priority processed: `{report['priority_counts'].get('medium', 0)}`.",
        f"- Can trade: `false`.",
        "",
        "## Processed Items",
        "",
    ]
    for item in report["processed"]:
        c = item["classification"]
        quality = c.get("text_quality", {}).get("note", "unknown")
        lines.append(f"- `{c['priority']}` score `{c['score']}`: `{item['source_rel']}` -> `{', '.join(c['actions'])}`; text `{quality}`")
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "- Original files are not deleted.",
            "- External Downloads files are copied to `processed_docs` for traceability.",
            "- Sensitive filenames are excluded before read/import.",
            "- Output is research backlog only, not runtime trading.",
            "",
        ]
    )
    return "\n".join(lines)


def normalise_markdown_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.suffix:
        return path
    return path.with_suffix(".md")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Scan workspace/Downloads for testable plus-EV strategy candidates")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--roots", default="workspace,downloads", help="Comma list: workspace,downloads or absolute paths")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--processed-dir", default=str(DEFAULT_PROCESSED_DIR))
    parser.add_argument("--out-prefix", default=str(DEFAULT_OUT_PREFIX))
    parser.add_argument("--backlog", default=str(DEFAULT_BACKLOG))
    parser.add_argument("--no-copy", action="store_true")
    args = parser.parse_args()

    root_values = []
    for raw in args.roots.split(","):
        value = raw.strip()
        if not value:
            continue
        if value == "workspace":
            root_values.append(WORKSPACE_ROOT)
        elif value == "downloads":
            root_values.append(DOWNLOADS_ROOT)
        else:
            root_values.append(Path(value))

    registry_path = Path(args.registry)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry = load_registry(registry_path)
    candidates = discover_files(root_values, registry)
    processed_dir = Path(args.processed_dir)
    backlog_path = normalise_markdown_path(args.backlog)
    processed: list[dict[str, Any]] = []

    for path in candidates[: max(0, args.limit)]:
        digest = sha256(path)
        text, read_mode = read_candidate_text(path)
        processed_copy = None if args.no_copy else copy_to_processed(path, processed_dir)
        item = make_item(path, digest, text, read_mode, processed_copy)
        processed.append(item)
        stem = safe_slug(Path(item["source_rel"]).stem)
        item_prefix = ROOT / "docs" / f"STRATEGY_DISCOVERY_{stem}_{digest[:10]}_2026-06-08"
        item_prefix.with_suffix(".json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        item_prefix.with_suffix(".md").write_text(render_item_markdown(item), encoding="utf-8")
        registry.setdefault("processed", []).append(item)

    registry["generated_at"] = now_iso()
    registry["processed_count"] = len(registry.get("processed", []))
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    priority_counts: dict[str, int] = {}
    for item in processed:
        priority = item["classification"]["priority"]
        priority_counts[priority] = priority_counts.get(priority, 0) + 1

    report = {
        "generated_at": now_iso(),
        "runtime_boundary": {
            "classification": "strategy_discovery_research_only",
            "can_trade": False,
            "sends_orders": False,
            "uses_private_credentials": False,
        },
        "roots": [str(root) for root in root_values],
        "scanned_candidates": len(candidates),
        "processed_now": len(processed),
        "remaining_candidates": max(0, len(candidates) - len(processed)),
        "priority_counts": priority_counts,
        "processed": processed,
        "registry": str(registry_path),
        "backlog": str(backlog_path),
        "can_trade": False,
    }

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_summary(report), encoding="utf-8")

    all_items = list(registry.get("processed", []))
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    backlog_path.write_text(render_backlog(all_items), encoding="utf-8")

    print(
        json.dumps(
            {
                "processed_now": len(processed),
                "remaining_candidates": report["remaining_candidates"],
                "priority_counts": priority_counts,
                "json": str(out_prefix.with_suffix(".json")),
                "md": str(out_prefix.with_suffix(".md")),
                "backlog": str(backlog_path),
                "registry": str(registry_path),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
