#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
WORKSPACE_ROOT = ROOT.parent
DEFAULT_PROCESSED_DIR = WORKSPACE_ROOT / "processed_docs"
DEFAULT_REGISTRY = ROOT / "docs" / "DOCUMENT_PROCESSING_REGISTRY_2026-06-04.json"
SUPPORTED_EXTENSIONS = {".md", ".txt", ".json", ".docx"}
EXCLUDE_DIR_NAMES = {
    ".git",
    "__pycache__",
    "_dl",
    "data",
    "logs",
    "processed_docs",
    "MAX_BitEvo_ALL_IN_ONE_UNIFIED_20260323",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


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
    return "\n".join(parts)


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return read_docx(path)
    return read_text_file(path)


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"generated_at": now_iso(), "processed": []}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def registry_keys(registry: dict[str, Any]) -> set[str]:
    return {
        str(item.get("sha256"))
        for item in registry.get("processed", [])
        if item.get("sha256") and item.get("target_rel")
    }


def source_rel_for(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return f"external/{path.name}"


def is_excluded(path: Path, include_archive: bool) -> bool:
    parts = set(path.relative_to(WORKSPACE_ROOT).parts)
    if not include_archive and "_processed_archive" in parts:
        return True
    return bool(parts & EXCLUDE_DIR_NAMES)


def discover_candidates(*, include_archive: bool, registry: dict[str, Any]) -> list[Path]:
    seen_hashes = registry_keys(registry)
    candidates: list[Path] = []
    for path in WORKSPACE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if is_excluded(path, include_archive):
            continue
        try:
            digest = sha256(path)
        except OSError:
            continue
        if digest in seen_hashes:
            continue
        candidates.append(path)
    extension_priority = {".docx": 0, ".md": 1, ".txt": 2, ".json": 3}
    return sorted(candidates, key=lambda item: (extension_priority.get(item.suffix.lower(), 9), item.as_posix().lower()))


def compact_excerpt(text: str, limit: int = 1800) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def find_keywords(text: str) -> dict[str, list[str]]:
    lower = text.lower()
    groups = {
        "market_inputs": ["oi", "open interest", "funding", "liquidation", "spot", "perp", "volume", "delta", "vwap"],
        "trade_rules": ["entry", "exit", "stop", "take", "tp", "sl", "risk", "rr", "winrate", "setup", "signal"],
        "testable": ["backtest", "walk-forward", "oos", "test", "validate", "expectancy", "drawdown", "sample"],
        "runtime": ["api", "bot", "websocket", "binance", "router", "executor", "database", "schema"],
        "governance": ["checklist", "policy", "governance", "kill-switch", "risk management", "discipline"],
    }
    found: dict[str, list[str]] = {}
    for group, words in groups.items():
        hits = [word for word in words if word in lower]
        if hits:
            found[group] = hits[:12]
    return found


def classify_document(text: str) -> dict[str, Any]:
    keywords = find_keywords(text)
    actions: list[str] = []
    if "trade_rules" in keywords and "testable" in keywords:
        actions.append("code_candidate")
        actions.append("backtest_candidate")
    if "market_inputs" in keywords:
        actions.append("feature_candidate")
    if "runtime" in keywords:
        actions.append("runtime_or_adapter_candidate")
    if "governance" in keywords:
        actions.append("knowledge_or_checklist_candidate")
    if not actions:
        actions.append("archive_reference_only")
    confidence = "high" if len(actions) >= 2 else "medium" if actions[0] != "archive_reference_only" else "low"
    return {
        "actions": actions,
        "confidence": confidence,
        "keyword_hits": keywords,
    }


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return slug[:120] or "document"


def report_name_for(prefix: str, item: dict[str, Any]) -> str:
    stem = Path(item["source_rel"]).stem
    digest = str(item.get("sha256") or "")[:10] or "nohash"
    return f"{prefix}_{safe_slug(stem)}_{digest}_2026-06-04"


def make_report(
    path: Path,
    text: str,
    classification: dict[str, Any],
    digest: str,
    target: Path | None,
    size_bytes: int,
) -> dict[str, Any]:
    rel_source = source_rel_for(path)
    return {
        "processed_at": now_iso(),
        "source": str(path),
        "source_rel": rel_source,
        "sha256": digest,
        "size_bytes": size_bytes,
        "extension": path.suffix.lower(),
        "classification": classification,
        "target": str(target) if target else None,
        "target_rel": target.relative_to(WORKSPACE_ROOT).as_posix() if target else None,
        "excerpt": compact_excerpt(text),
        "recommended_next_steps": recommended_steps(classification),
    }


def recommended_steps(classification: dict[str, Any]) -> list[str]:
    actions = set(classification.get("actions", []))
    steps: list[str] = []
    if "feature_candidate" in actions:
        steps.append("Extract concrete feature definitions and add them to a research-only evaluator.")
    if "code_candidate" in actions:
        steps.append("Translate only deterministic rules into code; avoid claims without testable thresholds.")
    if "backtest_candidate" in actions:
        steps.append("Run through the same polygon/OOS gate before any paper/live promotion.")
    if "runtime_or_adapter_candidate" in actions:
        steps.append("Map to a real consumer before treating it as runtime.")
    if "knowledge_or_checklist_candidate" in actions:
        steps.append("Merge durable checklist/governance content into knowledge docs, not runtime configs.")
    if not steps:
        steps.append("Keep as archived reference; no code action.")
    return steps


def render_markdown(item: dict[str, Any]) -> str:
    classification = item["classification"]
    lines = [
        "# Document Processing Note",
        "",
        f"Processed: `{item['processed_at']}`",
        f"Source: `{item['source_rel']}`",
        f"SHA256: `{item['sha256']}`",
        "",
        "## Classification",
        "",
        f"- Confidence: `{classification['confidence']}`.",
        f"- Actions: `{', '.join(classification['actions'])}`.",
        f"- Target after processing: `{item.get('target_rel') or 'not_moved'}`.",
        "",
        "## Keyword Hits",
        "",
    ]
    for group, hits in classification.get("keyword_hits", {}).items():
        lines.append(f"- `{group}`: {', '.join(f'`{hit}`' for hit in hits)}")
    lines.extend(
        [
            "",
            "## Recommended Next Steps",
            "",
        ]
    )
    for step in item["recommended_next_steps"]:
        lines.append(f"- {step}")
    lines.extend(
        [
            "",
            "## Excerpt",
            "",
            item["excerpt"] or "_No readable text extracted._",
            "",
        ]
    )
    return "\n".join(lines)


def move_to_processed(path: Path, processed_dir: Path) -> Path:
    rel = Path(source_rel_for(path))
    target = processed_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        counter = 2
        while target.exists():
            target = target.with_name(f"{stem}_{counter}{suffix}")
            counter += 1
    resolved_target = target.resolve()
    if processed_dir.resolve() not in resolved_target.parents:
        raise ValueError(f"unsafe processed target: {target}")
    shutil.move(str(path), str(target))
    return target


def copy_external_to_processed(path: Path, processed_dir: Path) -> Path:
    target = processed_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "external" / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        counter = 2
        while target.exists():
            target = target.with_name(f"{stem}_{counter}{suffix}")
            counter += 1
    resolved_target = target.resolve()
    if processed_dir.resolve() not in resolved_target.parents:
        raise ValueError(f"unsafe external processed target: {target}")
    shutil.copy2(str(path), str(target))
    return target


def process_target(path: Path, processed_dir: Path, no_move: bool) -> Path | None:
    if no_move:
        return None
    try:
        path.resolve().relative_to(WORKSPACE_ROOT.resolve())
    except ValueError:
        return copy_external_to_processed(path, processed_dir)
    return move_to_processed(path, processed_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Process one workspace document into registry + processed_docs")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--include-archive", action="store_true")
    parser.add_argument("--no-move", action="store_true")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--processed-dir", default=str(DEFAULT_PROCESSED_DIR))
    parser.add_argument("--out-dir", default=str(ROOT / "docs"))
    args = parser.parse_args()

    registry_path = Path(args.registry)
    processed_dir = Path(args.processed_dir)
    out_dir = Path(args.out_dir)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    registry = load_registry(registry_path)
    if args.file:
        candidates = [Path(item) for item in args.file]
        seen_hashes = registry_keys(registry)
        candidates = [path for path in candidates if path.exists() and path.suffix.lower() in SUPPORTED_EXTENSIONS and sha256(path) not in seen_hashes]
    else:
        candidates = discover_candidates(include_archive=args.include_archive, registry=registry)
    processed: list[dict[str, Any]] = []
    for path in candidates[: max(0, args.limit)]:
        digest = sha256(path)
        size_bytes = path.stat().st_size
        text = read_document(path)
        classification = classify_document(text)
        target = process_target(path, processed_dir, args.no_move)
        item = make_report(path, text, classification, digest, target, size_bytes)
        processed.append(item)
        prefix = "DOCUMENT_PROCESSING_DRY_RUN" if args.no_move else "DOCUMENT_PROCESSING"
        report_name = report_name_for(prefix, item)
        (out_dir / f"{report_name}.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / f"{report_name}.md").write_text(render_markdown(item), encoding="utf-8")
        if not args.no_move:
            registry.setdefault("processed", []).append(item)
    if not args.no_move:
        registry["generated_at"] = now_iso()
        registry["processed_count"] = len(registry.get("processed", []))
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "workspace_root": str(WORKSPACE_ROOT),
            "processed_now": len(processed),
            "remaining_candidates": max(0, len(candidates) - len(processed)),
            "processed": [
                {
                    "source_rel": item["source_rel"],
                    "target_rel": item["target_rel"],
                    "actions": item["classification"]["actions"],
                    "confidence": item["classification"]["confidence"],
                }
                for item in processed
            ],
            "registry": str(registry_path),
            "can_trade": False,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
