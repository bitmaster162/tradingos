from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
PACKAGE_NAME = PACKAGE_ROOT.name


KEEP_TOP_LEVEL = {
    PACKAGE_NAME,
    f"{PACKAGE_NAME}.zip",
    "_processed_archive",
    "agents.md",
}

SOURCE_ARCHIVE_NAMES = {
    "MAX_BitEvo_ALL_IN_ONE_20251019 (1).zip",
    "MAX_BitEvo_ALL_IN_ONE_20251019.zip",
    "robert_trade_system_FULL_v7.zip",
    "Trade.zip",
    "Адаптивная BTC-стратегия «Trend-Flex».docx",
    "Аналитический отчёт_ Прогнозирование цены биткоина (краткосрочный и среднесрочный периоды).docx",
    "Вин Рейт и Риск-Ревард в Трейдинге.docx",
    "Исполнительное резюме.docx",
}

RAW_CORPUS_NAMES = {
    "concordance_windows.csv",
    "entities_mentions.csv",
    "motif_concordance.csv",
    "numeric_params.csv",
    "rules_raw.csv",
    "snippets_index.csv",
}

ROOT_KNOWLEDGE_NAMES = {
    "build_v7.py",
    "canon_rules.md",
    "deep_summary.json",
    "discipline_check.py",
    "evaluation.md",
    "extraction_summary.json",
    "FINAL_CODEX_VERDICT.md",
    "findings.md",
    "governance.md",
    "levels_map.md",
    "not_a_git_repo.txt",
    "numeric_rollup.md",
    "order_templates.md",
    "README.md",
    "README_UNIFIED.md",
    "repo_tree.txt",
    "risk_and_sizing.md",
    "runtime_baseline.md",
    "strategies.md",
    "TRADING_OS_AGENT_FEED_DELTA_v1.json",
    "TRADING_OS_PROJECT_PACK_DELTA_v1.md",
    "TRADING_OS_PROOF_PACK_v1.json",
    "TRADING_OS_REMEDIATION_PLAN_2026-04-05.md",
}

ROOT_DIR_ARCHIVE_NAMES = {
    "screenshots",
    "smoke_tests",
    "TRADING_OS_EVIDENCE_PACK_PREFILLED",
    "v7",
}

GENERATED_DELETE_NAMES = {"__pycache__"}
GENERATED_DELETE_SUFFIXES = {".pyc", ".pyo"}


@dataclass
class Action:
    path: Path
    rel: str
    kind: str
    size: int | None
    sha256: str | None
    status: str
    reason: str
    destination: str | None = None
    applied: bool = False


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel_posix(path: Path, root: Path = WORKSPACE_ROOT) -> str:
    return path.relative_to(root).as_posix()


def is_within(path: Path, root: Path) -> bool:
    path_resolved = path.resolve()
    root_resolved = root.resolve()
    return path_resolved == root_resolved or root_resolved in path_resolved.parents


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_size(path: Path) -> int | None:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return None


def classify(path: Path) -> tuple[str, str, str]:
    rel = rel_posix(path)
    top = path.relative_to(WORKSPACE_ROOT).parts[0]
    name = path.name

    if top == "_processed_archive":
        return "keep_active", "archive ledger/history directory", "active"
    if top == PACKAGE_NAME:
        if name in GENERATED_DELETE_NAMES or path.suffix.lower() in GENERATED_DELETE_SUFFIXES:
            return "delete_generated", "generated Python cache inside active package", "generated"
        return "keep_active", "active unified package source/runtime", "active"
    if top == f"{PACKAGE_NAME}.zip":
        return "keep_active", "current deliverable archive", "deliverable"
    if top.lower() == "agents.md":
        return "keep_active", "workspace agent instructions", "governance"
    if top in SOURCE_ARCHIVE_NAMES:
        return "archive_processed", "source artifact already integrated or superseded by unified package", "source"
    if top in RAW_CORPUS_NAMES:
        return "archive_processed", "heavy raw corpus kept outside curated package", "raw_corpus"
    if top in ROOT_KNOWLEDGE_NAMES:
        return "archive_processed", "curated into unified knowledge/docs or superseded by runtime proof docs", "knowledge_source"
    if top in ROOT_DIR_ARCHIVE_NAMES:
        return "archive_processed", "standalone source directory superseded by unified package copy or docs", "source_dir"
    return "review_later", "not matched by curation policy", "unknown"


def unique_destination(base: Path, rel: str) -> Path:
    candidate = base / rel
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    idx = 2
    while True:
        alt = parent / f"{stem}__dup{idx}{suffix}"
        if not alt.exists():
            return alt
        idx += 1


def top_level_items() -> list[Path]:
    items = [item for item in WORKSPACE_ROOT.iterdir() if item.name not in {".", ".."}]
    return sorted(items, key=lambda item: item.name.lower())


def generated_items() -> list[Path]:
    items: list[Path] = []
    for path in PACKAGE_ROOT.rglob("*"):
        if path.name in GENERATED_DELETE_NAMES:
            items.append(path)
        elif path.is_file() and path.suffix.lower() in GENERATED_DELETE_SUFFIXES:
            items.append(path)
    return sorted(items, key=lambda item: rel_posix(item).lower())


def build_actions(archive_dir: Path) -> list[Action]:
    actions: list[Action] = []
    seen: set[Path] = set()

    for path in top_level_items():
        status, reason, kind = classify(path)
        rel = rel_posix(path)
        destination = None
        if status == "archive_processed":
            destination = str(unique_destination(archive_dir / "workspace_root", rel))
        actions.append(
            Action(
                path=path,
                rel=rel,
                kind=kind,
                size=file_size(path),
                sha256=hash_file(path) if path.is_file() else None,
                status=status,
                reason=reason,
                destination=destination,
            )
        )
        seen.add(path)

    for path in generated_items():
        if path in seen:
            continue
        status, reason, kind = classify(path)
        actions.append(
            Action(
                path=path,
                rel=rel_posix(path),
                kind=kind,
                size=file_size(path),
                sha256=hash_file(path) if path.is_file() else None,
                status=status,
                reason=reason,
            )
        )
    return actions


def action_to_dict(action: Action) -> dict[str, Any]:
    return {
        "path": action.rel,
        "kind": action.kind,
        "size": action.size,
        "sha256": action.sha256,
        "status": action.status,
        "reason": action.reason,
        "destination": action.destination,
        "applied": action.applied,
    }


def apply_actions(actions: list[Action], archive_dir: Path) -> None:
    workspace_resolved = WORKSPACE_ROOT.resolve()
    archive_resolved = archive_dir.resolve()
    if not is_within(archive_resolved, workspace_resolved):
        raise RuntimeError(f"Archive path is outside workspace: {archive_resolved}")
    archive_dir.mkdir(parents=True, exist_ok=True)

    for action in actions:
        source = action.path.resolve()
        if not is_within(source, workspace_resolved):
            raise RuntimeError(f"Refusing to operate outside workspace: {source}")
        if action.status == "archive_processed":
            if not action.destination:
                raise RuntimeError(f"Missing archive destination for {action.rel}")
            destination = Path(action.destination).resolve()
            if not is_within(destination, archive_resolved):
                raise RuntimeError(f"Archive destination escapes archive root: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if action.path.exists():
                shutil.move(str(action.path), str(destination))
                action.applied = True
        elif action.status == "delete_generated":
            if action.path.exists():
                if action.path.is_dir():
                    shutil.rmtree(action.path)
                else:
                    action.path.unlink()
                action.applied = True


def write_outputs(actions: list[Action], archive_dir: Path, out_prefix: Path, apply: bool) -> dict[str, str]:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    records = [action_to_dict(action) for action in actions]
    summary: dict[str, Any] = {
        "generated_at": now_iso(),
        "workspace_root": str(WORKSPACE_ROOT),
        "package_root": str(PACKAGE_ROOT),
        "archive_dir": str(archive_dir),
        "apply": apply,
        "counts": {},
        "records": records,
    }
    for record in records:
        summary["counts"][record["status"]] = summary["counts"].get(record["status"], 0) + 1

    json_path = out_prefix.with_suffix(".json")
    csv_path = out_prefix.with_suffix(".csv")
    md_path = out_prefix.with_suffix(".md")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["path", "kind", "size", "sha256", "status", "reason", "destination", "applied"],
        )
        writer.writeheader()
        writer.writerows(records)

    lines = [
        "# Workspace Curation Ledger",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Workspace: `{summary['workspace_root']}`",
        f"- Package: `{summary['package_root']}`",
        f"- Archive: `{summary['archive_dir']}`",
        f"- Applied: `{apply}`",
        "",
        "## Counts",
        "",
    ]
    for key in sorted(summary["counts"]):
        lines.append(f"- `{key}`: `{summary['counts'][key]}`")
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "- Active unified package and current ZIP are kept in place.",
            "- Workspace `agents.md` is kept in place because it controls local agent instructions.",
            "- Root-level sources already integrated into the unified package are moved to `_processed_archive/`, not permanently deleted.",
            "- Heavy raw corpus CSV files are moved to `_processed_archive/` and excluded from the active package.",
            "- Generated Python caches are deleted.",
            "",
            "## Active Result",
            "",
            "The active working surface is now the unified package plus its current ZIP. Archived sources remain recoverable.",
            "",
            "## Records",
            "",
            "| Status | Kind | Path | Destination | Reason |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for record in records:
        destination = record["destination"] or ""
        lines.append(
            f"| `{record['status']}` | `{record['kind']}` | `{record['path']}` | `{destination}` | {record['reason']} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "md": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Curate the Trade workspace into active package + processed archive")
    parser.add_argument("--apply", action="store_true", help="Move processed source artifacts and delete generated caches")
    parser.add_argument("--stamp", default=now_stamp())
    parser.add_argument("--out-prefix", default=None)
    args = parser.parse_args()

    archive_dir = WORKSPACE_ROOT / "_processed_archive" / f"{args.stamp}_workspace_curation"
    out_prefix = Path(args.out_prefix) if args.out_prefix else PACKAGE_ROOT / "docs" / f"WORKSPACE_CURATION_{args.stamp}"
    if not out_prefix.is_absolute():
        out_prefix = PACKAGE_ROOT / out_prefix

    actions = build_actions(archive_dir)
    if args.apply:
        apply_actions(actions, archive_dir)
    outputs = write_outputs(actions, archive_dir, out_prefix, args.apply)
    print(json.dumps({"outputs": outputs, "archive_dir": str(archive_dir), "counts": {a.status: sum(1 for x in actions if x.status == a.status) for a in actions}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
