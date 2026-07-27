#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W_NS, "r": R_NS, "rel": REL_NS}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def qn(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def xml_entry(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        payload = archive.read(name)
    except KeyError:
        return None
    try:
        return ET.fromstring(payload)
    except ET.ParseError:
        return None


def visible_text(element: ET.Element) -> str:
    chunks: list[str] = []
    for node in element.iter():
        if node.tag == qn(W_NS, "t"):
            chunks.append(node.text or "")
        elif node.tag == qn(W_NS, "tab"):
            chunks.append("\t")
        elif node.tag in {qn(W_NS, "br"), qn(W_NS, "cr")}:
            chunks.append("\n")
    return "".join(chunks).strip()


def style_map(root: ET.Element | None) -> dict[str, str]:
    if root is None:
        return {}
    styles: dict[str, str] = {}
    for style in root.findall("w:style", NS):
        style_id = style.get(qn(W_NS, "styleId"))
        name = style.find("w:name", NS)
        if style_id:
            styles[style_id] = str(name.get(qn(W_NS, "val")) if name is not None else style_id)
    return styles


def relationship_map(root: ET.Element | None) -> dict[str, dict[str, str]]:
    if root is None:
        return {}
    relationships: dict[str, dict[str, str]] = {}
    for item in root.findall("rel:Relationship", NS):
        relation_id = str(item.get("Id") or "")
        if relation_id:
            relationships[relation_id] = {
                "target": str(item.get("Target") or ""),
                "type": str(item.get("Type") or ""),
                "target_mode": str(item.get("TargetMode") or ""),
            }
    return relationships


def paragraph_record(
    paragraph: ET.Element,
    styles: dict[str, str],
    relationships: dict[str, dict[str, str]],
    index: int,
) -> dict[str, Any]:
    properties = paragraph.find("w:pPr", NS)
    style_id = None
    num_id = None
    level = None
    if properties is not None:
        style_node = properties.find("w:pStyle", NS)
        style_id = style_node.get(qn(W_NS, "val")) if style_node is not None else None
        numbering = properties.find("w:numPr", NS)
        if numbering is not None:
            num_node = numbering.find("w:numId", NS)
            level_node = numbering.find("w:ilvl", NS)
            num_id = num_node.get(qn(W_NS, "val")) if num_node is not None else None
            level = int(level_node.get(qn(W_NS, "val")) or 0) if level_node is not None else 0
    links: list[dict[str, str]] = []
    for hyperlink in paragraph.findall(".//w:hyperlink", NS):
        relation_id = hyperlink.get(qn(R_NS, "id"))
        relation = relationships.get(str(relation_id or ""), {})
        links.append(
            {
                "text": visible_text(hyperlink),
                "relationship_id": str(relation_id or ""),
                "target": str(relation.get("target") or ""),
                "target_mode": str(relation.get("target_mode") or ""),
            }
        )
    return {
        "type": "paragraph",
        "index": index,
        "style_id": style_id,
        "style_name": styles.get(str(style_id), str(style_id or "Normal")),
        "numbering_id": num_id,
        "numbering_level": level,
        "text": visible_text(paragraph),
        "hyperlinks": links,
    }


def table_record(table: ET.Element, index: int) -> dict[str, Any]:
    rows: list[list[str]] = []
    for row in table.findall("w:tr", NS):
        cells: list[str] = []
        for cell in row.findall("w:tc", NS):
            paragraphs = [visible_text(item) for item in cell.findall("w:p", NS)]
            cells.append("\n".join(item for item in paragraphs if item))
        rows.append(cells)
    return {"type": "table", "index": index, "rows": rows}


def extract_docx(path: Path) -> dict[str, Any]:
    before_hash = sha256_file(path)
    with zipfile.ZipFile(path, "r") as archive:
        names = sorted(archive.namelist())
        document = xml_entry(archive, "word/document.xml")
        styles = style_map(xml_entry(archive, "word/styles.xml"))
        relationships = relationship_map(xml_entry(archive, "word/_rels/document.xml.rels"))
        if document is None:
            raise ValueError("DOCX is missing a readable word/document.xml")
        body = document.find("w:body", NS)
        if body is None:
            raise ValueError("DOCX document.xml is missing w:body")
        blocks: list[dict[str, Any]] = []
        paragraph_index = 0
        table_index = 0
        for child in list(body):
            if child.tag == qn(W_NS, "p"):
                paragraph_index += 1
                blocks.append(paragraph_record(child, styles, relationships, paragraph_index))
            elif child.tag == qn(W_NS, "tbl"):
                table_index += 1
                blocks.append(table_record(child, table_index))
        media = [
            {"path": name, "size": archive.getinfo(name).file_size}
            for name in names
            if name.startswith("word/media/") and not name.endswith("/")
        ]
    paragraphs = [item for item in blocks if item["type"] == "paragraph"]
    tables = [item for item in blocks if item["type"] == "table"]
    external_links = sorted(
        {
            link["target"]
            for item in paragraphs
            for link in item.get("hyperlinks", [])
            if link.get("target") and link.get("target_mode").lower() == "external"
        }
    )
    text = "\n".join(str(item.get("text") or "") for item in paragraphs)
    after_hash = sha256_file(path)
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "tool": "tools/docx_research_intake.py",
        "source": {
            "path": str(path.resolve()),
            "size": path.stat().st_size,
            "sha256_before": before_hash,
            "sha256_after": after_hash,
            "unchanged": before_hash == after_hash,
        },
        "summary": {
            "paragraphs": len(paragraphs),
            "nonempty_paragraphs": sum(bool(item.get("text")) for item in paragraphs),
            "tables": len(tables),
            "table_rows": sum(len(item.get("rows") or []) for item in tables),
            "external_links": len(external_links),
            "media_files": len(media),
            "word_count_approx": len(re.findall(r"\S+", text)),
        },
        "blocks": blocks,
        "external_links": external_links,
        "media": media,
        "styles": styles,
        "visual_qa": {
            "performed": False,
            "reason": "LibreOffice and Microsoft Word renderer unavailable in the current environment",
        },
        "boundary": {
            "read_only_extraction": True,
            "claims_verified": False,
            "runtime_feature": False,
            "can_trade": False,
        },
        "can_trade": False,
    }


def heading_level(style_name: str) -> int | None:
    normalized = style_name.strip().lower()
    if normalized in {"title", "document title", "название", "заголовок документа"}:
        return 1
    match = re.search(r"(?:heading|заголовок)\s*([1-6])", normalized)
    return int(match.group(1)) + 1 if match else None


def escape_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def render_markdown(report: dict[str, Any]) -> str:
    source = report["source"]
    lines = [
        "# DOCX Research Intake",
        "",
        f"- Source: `{source['path']}`",
        f"- SHA256: `{source['sha256_before']}`",
        f"- Source unchanged: `{source['unchanged']}`",
        f"- Visual QA performed: `false` ({report['visual_qa']['reason']})",
        f"- Claims verified: `false`",
        "",
        "## Extracted Content",
        "",
    ]
    for block in report["blocks"]:
        if block["type"] == "paragraph":
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            level = heading_level(str(block.get("style_name") or ""))
            if level:
                lines.extend([f"{'#' * min(6, level)} {text}", ""])
            elif block.get("numbering_id") is not None:
                indent = "  " * int(block.get("numbering_level") or 0)
                lines.append(f"{indent}- {text}")
            else:
                lines.extend([text, ""])
        elif block["type"] == "table":
            rows = block.get("rows") or []
            if not rows:
                continue
            width = max(len(row) for row in rows)
            normalized = [row + [""] * (width - len(row)) for row in rows]
            lines.append("| " + " | ".join(escape_cell(item) for item in normalized[0]) + " |")
            lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
            for row in normalized[1:]:
                lines.append("| " + " | ".join(escape_cell(item) for item in row) + " |")
            lines.append("")
    if report["external_links"]:
        lines.extend(["## External Links", ""])
        lines.extend(f"- {item}" for item in report["external_links"])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Stdlib-only read-only DOCX research intake extractor")
    parser.add_argument("docx")
    parser.add_argument("--out-prefix", default="tmp/DOCX_RESEARCH_INTAKE")
    args = parser.parse_args()
    source = resolve_path(args.docx)
    report = extract_docx(source)
    out = resolve_path(args.out_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "source": report["source"]["path"],
                "sha256": report["source"]["sha256_before"],
                "summary": report["summary"],
                "source_unchanged": report["source"]["unchanged"],
                "out": portable(out.with_suffix(".json")),
                "can_trade": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
