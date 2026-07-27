#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml"}
DEFAULT_ROOTS = [
    "README.md",
    "docs",
    "knowledge",
    "smartmoney",
    "configs",
    "bitevo",
    "v7",
    "ops/control_panel",
]


CANON_CORE = {
    "README.md",
    "docs/RUN_ALL_LOCAL_SAFE_2026-06-01.md",
    "docs/CONTROL_PANEL_DASHBOARD_2026-06-01.md",
    "ops/control_panel/README.md",
    "docs/BOUNDED_RUNTIME_PROOF_PACK_2026-04-01.md",
    "docs/LOCAL_RUNTIME_PROOF_2026-04-26.md",
    "docs/MAX_RUNTIME_AUDIT_AND_PROFIT_PLAN_2026-06-01.md",
    "docs/MAX_CORE_LITE_DATA_CACHE_2026-06-02.md",
    "docs/MAX_CORE_LITE_CANDIDATE_HARDENING_2026-06-02.md",
    "docs/MAX_CORE_LITE_V11_WEAK_BID_VALIDATION_2026-06-02.md",
    "docs/MAX_CORE_LITE_V12_REGIME_ISOLATION_2026-06-02.md",
    "docs/MAX_CORE_LITE_V13_STRUCTURAL_CANDIDATE_2026-06-02.md",
    "docs/MAX_CORE_LITE_V14_LONG_EXPANSION_2026-06-02.md",
    "docs/MAX_CORE_LITE_V15_STATE_FILTERS_2026-06-02.md",
    "docs/MAX_CORE_LITE_V16_EVENT_FIRST_MINER_2026-06-02.md",
    "docs/MAX_CORE_LITE_V17_SHORT_CONTINUATION_HARDENING_2026-06-02.md",
    "docs/MAX_CORE_LITE_V18_ALERT_ONLY_INTEGRATION_2026-06-02.md",
    "docs/MAX_CORE_LITE_V19_ALERT_OBSERVABILITY_2026-06-02.md",
    "docs/MAX_CORE_LITE_V20_FORWARD_EVIDENCE_2026-06-02.md",
    "docs/ACTIVE_REFERENCE_RUNTIME_EXTRACTION_2026-06-02.md",
    "docs/BITEVO_CONTRACT_CHECK_2026-06-02.md",
    "docs/BITEVO_REGISTRY_VALIDATION_2026-06-02.md",
    "docs/DETECTOR_GAP_MAP_2026-06-02.md",
    "docs/DELIVERY_SNAPSHOT_POLICY_2026-06-03.md",
    "docs/LIQUIDITY_SWEEP_DETECTOR_SMOKE_2026-06-03.md",
    "docs/LIQUIDITY_SWEEP_FORWARD_EVAL_2026-06-03.md",
    "docs/LIQUIDITY_SWEEP_CONFLUENCE_EVAL_2026-06-03.md",
    "docs/LIQUIDITY_SWEEP_HARDENING_2026-06-03.md",
    "docs/LIQUIDITY_SWEEP_CONFLUENCE_EXTENDED_2026-06-03.md",
    "docs/LIQUIDITY_SWEEP_HARDENING_EXTENDED_2026-06-03.md",
    "docs/LIQUIDITY_SWEEP_RESEARCH_DECISION_2026-06-03.md",
    "docs/SPOT_PERP_DIVERGENCE_HARDENING_2026-06-03.md",
    "docs/SPOT_PERP_DIVERGENCE_RESEARCH_DECISION_2026-06-03.md",
    "docs/FUNDING_OI_REGIME_HARDENING_2026-06-03.md",
    "docs/FUNDING_OI_REGIME_RESEARCH_DECISION_2026-06-03.md",
    "docs/COMBINED_REGIME_HARDENING_2026-06-03.md",
    "docs/COMBINED_REGIME_RESEARCH_DECISION_2026-06-03.md",
    "docs/RESEARCH_GATE_AUDIT_COMBINED_REGIME_2026-06-03.md",
    "docs/COMBINED_REGIME_WALKFORWARD_2026-06-03.md",
    "docs/COMBINED_REGIME_FAILURE_DIAGNOSTICS_2026-06-04.md",
    "docs/STRATEGY_POLYGON_PARALLEL_2026-06-04.md",
    "docs/STRATEGY_POLYGON_100_PARALLEL_2026-06-04.md",
    "docs/EVENT_FEATURE_FACTORY_2026-06-04.md",
    "docs/EVENT_FEATURE_FACTORY_EXTENDED_2026-06-04.md",
    "docs/EVENT_FEATURE_HOLDOUT_VALIDATION_2026-06-04.md",
    "docs/RISK_REWARD_GATE_2026-06-04.md",
    "docs/PRETRADE_GUARDIAN_SMOKE_2026-06-04.md",
    "docs/DOCUMENT_PROCESSING_agents_2026-06-04.md",
}

ACTIVE_REFERENCE = {
    "docs/BTC_TREND_FLEX_SYSTEM.md",
    "configs/BTC_TREND_FLEX_SYSTEM.json",
    "smartmoney/BTC_TrendFlex_Checklist.md",
    "knowledge/strategies.md",
    "knowledge/risk_and_sizing.md",
    "knowledge/README.md",
    "knowledge/agents.md",
    "knowledge/evaluation.md",
    "knowledge/order_templates.md",
    "smartmoney/SmartMoney_Checklist.md",
    "smartmoney/SmartMoney_Alerts_Config.json",
    "docs/BITEVO_README.md",
    "configs/BitEvo_composite_config.json",
    "bitevo/openapi.yaml",
    "bitevo/schemas/alert.schema.json",
    "bitevo/schemas/kpi.schema.json",
    "bitevo/schemas/quality_gate.schema.json",
    "bitevo/schemas/trade_log.schema.json",
    "v7/README.md",
    "v7/alerts_rules.json",
    "v7/live_checklist_printable.md",
}

OPTIONAL_MODULE_HINTS = (
    "DEX_RANGE",
    "FUTURES",
    "DELIST",
    "BINANCE_BTC_SPOT",
    "BTCUSDT_BINANCE_FUTURES",
)

ARCHIVE_HINTS = (
    "ARCHIVE",
    "FORECAST",
    "SNAPSHOT",
    "market_snapshots",
    "WINRATE",
    "CFR_",
    "TREND_FLEX_BACKTEST",
    "FINAL_CANON",
    "EXTERNAL_SMOKE",
    "MINIMAL_BRIEF",
    "ENTRYPOINT_CURRENT",
    "S_CORE",
)

GENERATED_HINTS = (
    "WORKSPACE_CURATION",
    "WORKSPACE_FULL_FILE_INVENTORY",
    "ACTIVE_REFERENCE_RUNTIME_EXTRACTION",
    "BITEVO_CONTRACT_CHECK",
    "BITEVO_REGISTRY_VALIDATION",
    "DETECTOR_GAP_MAP",
    "LIQUIDITY_SWEEP_DETECTOR_SMOKE",
    "LIQUIDITY_SWEEP_FORWARD_EVAL",
    "LIQUIDITY_SWEEP_CONFLUENCE_EVAL",
    "LIQUIDITY_SWEEP_HARDENING",
    "LIQUIDITY_SWEEP_CONFLUENCE_EXTENDED",
    "LIQUIDITY_SWEEP_HARDENING_EXTENDED",
    "SPOT_PERP_DIVERGENCE_HARDENING",
    "FUNDING_OI_REGIME_HARDENING",
    "COMBINED_REGIME_HARDENING",
    "RESEARCH_GATE_AUDIT",
    "COMBINED_REGIME_WALKFORWARD",
    "COMBINED_REGIME_FAILURE_DIAGNOSTICS",
    "STRATEGY_POLYGON_PARALLEL",
    "STRATEGY_POLYGON_100_PARALLEL",
    "EVENT_FEATURE_FACTORY",
    "EVENT_FEATURE_HOLDOUT_VALIDATION",
    "RISK_REWARD_GATE",
    "PRETRADE_GUARDIAN",
    "DOCUMENT_PROCESSING",
)

SELF_OUTPUT_HINTS = (
    "DOCS_CANON_AUDIT_2026-06-02",
    "CANON_INDEX_2026-06-02",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def discover_files(root: Path, roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in roots:
        path = root / item
        if not path.exists():
            continue
        if path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS:
            if any(hint in path.name for hint in SELF_OUTPUT_HINTS):
                continue
            files.append(path)
            continue
        if path.is_dir():
            for child in path.rglob("*"):
                if not child.is_file():
                    continue
                if child.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                if any(hint in child.name for hint in SELF_OUTPUT_HINTS):
                    continue
                if any(part in {".git", "__pycache__", ".pytest_cache"} for part in child.parts):
                    continue
                files.append(child)
    return sorted(set(files), key=lambda item: rel(item, root).lower())


def extract_title(path: Path, text: str) -> str:
    if path.suffix.lower() in {".json", ".yaml", ".yml"}:
        return path.name
    for line in text.splitlines()[:80]:
        clean = line.strip()
        if clean.startswith("#"):
            title = clean.lstrip("#").strip()
            if has_mojibake(title):
                return path.stem.replace("_", " ").replace("-", " ")
            return title
    return path.name


def extract_headings(text: str, limit: int = 8) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith("#"):
            headings.append(clean.lstrip("#").strip())
        if len(headings) >= limit:
            break
    return headings


def json_top_keys(path: Path, text: str) -> list[str]:
    if path.suffix.lower() != ".json":
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())[:20]
    return []


def has_mojibake(text: str) -> bool:
    return any(marker in text for marker in ("Ð", "Ñ", "Â", "â€", "�"))


def detect_flags(text: str, path: str) -> list[str]:
    flags: list[str] = []
    lower = text.lower()
    if "do_not_trade" in lower:
        flags.append("contains_do_not_trade")
    if "candidate_for_hardening" in lower:
        flags.append("contains_candidate_for_hardening")
    if "70%+" in text or "target_winrate_pct" in lower:
        flags.append("contains_winrate_target")
    if "live" in lower and ("proof" in lower or "locked" in lower or "forbidden" in lower):
        flags.append("live_boundary_discussed")
    if "historical reference" in lower or "archive" in lower or "dated reference" in lower:
        flags.append("historical_or_archive")
    if "runtime_truth" in lower or "consumer" in lower and "exists" in lower:
        flags.append("consumer_boundary_discussed")
    if has_mojibake(text):
        flags.append("possible_mojibake")
    if path.startswith("configs/") and "runtime_truth" not in lower and "consumer" not in lower:
        flags.append("config_consumer_not_explicit")
    return sorted(set(flags))


def classify(path: str, text: str) -> tuple[str, str]:
    name = Path(path).name
    upper = path.upper()
    lower = text.lower()

    if any(hint in upper for hint in SELF_OUTPUT_HINTS):
        return "generated_inventory", "Current canon-audit output; generated for navigation."
    if path in CANON_CORE:
        return "canon_core", "Primary package/runtime truth."
    if path in ACTIVE_REFERENCE:
        return "active_reference", "Reusable active playbook, contract, checklist or tool reference."
    if any(hint in upper for hint in GENERATED_HINTS):
        return "generated_inventory", "Generated inventory/curation output; useful for traceability, not daily canon."
    if any(hint.upper() in upper for hint in ARCHIVE_HINTS):
        return "archive_reference", "Historical/import/reference note; do not treat as live rule."
    if any(hint in upper for hint in OPTIONAL_MODULE_HINTS):
        return "optional_module", "Optional module documentation/spec; requires separate runtime proof before live use."
    if path.startswith("bitevo/"):
        return "active_reference", "BitEvo contract/example/schema layer."
    if path.startswith("configs/"):
        if "runtime_truth" in lower or "not_a_runtime" in lower:
            return "spec_only", "Config/spec without proven repo-local runtime consumer."
        return "active_reference", "Config/playbook spec; verify consumer before runtime use."
    if path.startswith("knowledge/"):
        if name in {"governance.md", "levels_map.md", "findings.md"}:
            return "supporting_knowledge", "Large knowledge/reference layer; use selectively."
        return "active_reference", "Knowledge layer used by playbooks and governance."
    if path.startswith("smartmoney/"):
        return "active_reference", "Trader-facing checklist/template layer."
    if path.startswith("v7/"):
        return "active_reference", "v7 toolkit reference/config."
    return "supporting_reference", "Supporting document; not primary canon."


def build_audit(root: Path, roots: list[str]) -> dict[str, Any]:
    files = discover_files(root, roots)
    items: list[dict[str, Any]] = []
    hashes: dict[str, list[str]] = defaultdict(list)
    for path in files:
        path_rel = rel(path, root)
        text = read_text(path)
        digest = sha256(path)
        category, rationale = classify(path_rel, text)
        item = {
            "path": path_rel,
            "title": extract_title(path, text),
            "category": category,
            "rationale": rationale,
            "extension": path.suffix.lower(),
            "size": path.stat().st_size,
            "sha256": digest,
            "headings": extract_headings(text),
            "json_top_keys": json_top_keys(path, text),
            "flags": detect_flags(text, path_rel),
        }
        items.append(item)
        hashes[digest].append(path_rel)

    duplicates = [
        {"sha256": digest, "paths": paths}
        for digest, paths in sorted(hashes.items())
        if len(paths) > 1
    ]
    counts = Counter(item["category"] for item in items)
    flag_counts = Counter(flag for item in items for flag in item["flags"])
    return {
        "generated_at": now_iso(),
        "scope": roots,
        "total_files": len(items),
        "category_counts": dict(sorted(counts.items())),
        "flag_counts": dict(sorted(flag_counts.items())),
        "duplicate_groups": duplicates,
        "items": items,
    }


def items_by_category(audit: dict[str, Any], category: str) -> list[dict[str, Any]]:
    return [item for item in audit["items"] if item["category"] == category]


def md_link(path: str) -> str:
    return f"`{path}`"


def render_canon_index(audit: dict[str, Any]) -> str:
    lines = [
        "# Trading OS Canon Index",
        "",
        f"Generated: `{audit['generated_at']}`",
        "",
        "## What This Index Means",
        "",
        "This file separates the documentation corpus into operational canon, active references, optional modules, archives and generated inventories.",
        "",
        "Runtime rule: a document or JSON spec is not a live feature unless there is a runnable consumer and a passing smoke/proof path.",
        "",
        "## Canon Core",
        "",
    ]
    for item in items_by_category(audit, "canon_core"):
        lines.append(f"- {md_link(item['path'])} - {item['title']}")

    sections = [
        ("Active References", "active_reference"),
        ("Optional Modules", "optional_module"),
        ("Spec Only", "spec_only"),
        ("Supporting Knowledge", "supporting_knowledge"),
        ("Archive References", "archive_reference"),
        ("Generated Inventory", "generated_inventory"),
        ("Supporting References", "supporting_reference"),
    ]
    for title, category in sections:
        group = items_by_category(audit, category)
        if not group:
            continue
        lines.extend(["", f"## {title}", ""])
        for item in group:
            flag_text = f" flags=`{','.join(item['flags'])}`" if item["flags"] else ""
            lines.append(f"- {md_link(item['path'])} - {item['title']}{flag_text}")

    lines.extend(
        [
            "",
            "## Current Canon Decision",
            "",
            "- Use `README.md` and the `canon_core` docs as source of truth for current runtime state.",
            "- Use `active_reference` docs for trading-system design and checklists.",
            "- Treat `optional_module` docs as useful modules that still need their own runtime proof before live use.",
            "- Treat `archive_reference` docs as historical context only.",
            "- Treat `generated_inventory` docs as traceability/output, not daily operating instructions.",
            "",
        ]
    )
    return "\n".join(lines)


def render_audit_memo(audit: dict[str, Any]) -> str:
    counts = audit["category_counts"]
    flags = audit["flag_counts"]
    duplicates = audit["duplicate_groups"]
    lines = [
        "# Docs Canon Audit",
        "",
        f"Generated: `{audit['generated_at']}`",
        f"Files audited: `{audit['total_files']}`",
        "",
        "## Findings First",
        "",
        "- The corpus is usable, but it must be read through a canon boundary: current runtime truth lives in `README.md`, runtime proof docs, control-panel docs and MAX Core Lite v1.x/v2.0 docs.",
        "- Strategy/playbook material is valuable, but not all of it is runnable. Config JSON is not a feature unless a real consumer exists.",
        "- Historical reports and dated market snapshots are useful for context only; they must not feed live bias or live levels.",
        "- Current MAX Core Lite remains research/evidence-first. No document currently upgrades `short_continuation_pressure` or any MAX lead into a live strategy.",
        "- The right next process is evidence accumulation, not live escalation.",
        "",
        "## Category Counts",
        "",
    ]
    for category, count in sorted(counts.items()):
        lines.append(f"- `{category}`: `{count}`")

    lines.extend(["", "## Flags", ""])
    if flags:
        for flag, count in sorted(flags.items()):
            lines.append(f"- `{flag}`: `{count}`")
    else:
        lines.append("- No flags detected.")

    lines.extend(
        [
            "",
            "## Canon Rules",
            "",
            "- `canon_core`: primary truth for package state, runnable proofs and MAX Core Lite research status.",
            "- `active_reference`: usable playbooks/contracts/checklists, but still subject to runtime proof.",
            "- `optional_module`: useful module material; keep, but do not treat as live without proof.",
            "- `archive_reference`: dated or imported material; keep for traceability, not live decision logic.",
            "- `generated_inventory`: output/inventory files; keep only while useful for traceability.",
            "",
            "## Duplicates",
            "",
        ]
    )
    if duplicates:
        for duplicate in duplicates:
            lines.append(f"- `{duplicate['sha256'][:12]}`: {', '.join(md_link(path) for path in duplicate['paths'])}")
    else:
        lines.append("- No exact duplicate content groups found in scoped docs.")

    lines.extend(
        [
            "",
            "## Recommended Operating Path",
            "",
            "1. Start with `README.md`.",
            "2. For runtime proof, read `docs/RUN_ALL_LOCAL_SAFE_2026-06-01.md`, `docs/CONTROL_PANEL_DASHBOARD_2026-06-01.md`, and `docs/BOUNDED_RUNTIME_PROOF_PACK_2026-04-01.md`.",
            "3. For MAX research status, read `docs/MAX_RUNTIME_AUDIT_AND_PROFIT_PLAN_2026-06-01.md`, then v1.1 through v2.0 docs.",
            "4. For BTC manual/system design, read `docs/BTC_TREND_FLEX_SYSTEM.md`, `smartmoney/BTC_TrendFlex_Checklist.md`, and `knowledge/risk_and_sizing.md`.",
            "5. For optional modules, read DEX/Futures/Delist docs only after deciding which runtime path to harden.",
            "",
            "## Do Not Do",
            "",
            "- Do not use archived price levels or probabilities as live rules.",
            "- Do not treat a high winrate claim as accepted unless it is tied to current backtest/paper/live proof.",
            "- Do not promote any alert into live trading from documentation alone.",
            "- Do not run live/signed trading until separate testnet/live proof exists.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build docs canon audit and canon index")
    parser.add_argument("--out-prefix", default="docs/DOCS_CANON_AUDIT_2026-06-02")
    parser.add_argument("--index-out", default="docs/CANON_INDEX_2026-06-02.md")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    audit = build_audit(root, DEFAULT_ROOTS)
    out_prefix = root / args.out_prefix
    index_out = root / args.index_out
    out_prefix.with_suffix(".json").parent.mkdir(parents=True, exist_ok=True)
    out_prefix.with_suffix(".json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    out_prefix.with_suffix(".md").write_text(render_audit_memo(audit), encoding="utf-8")
    index_out.parent.mkdir(parents=True, exist_ok=True)
    index_out.write_text(render_canon_index(audit), encoding="utf-8")
    print(
        json.dumps(
            {
                "total_files": audit["total_files"],
                "category_counts": audit["category_counts"],
                "flag_counts": audit["flag_counts"],
                "audit_md": str(out_prefix.with_suffix(".md")),
                "audit_json": str(out_prefix.with_suffix(".json")),
                "index_md": str(index_out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
