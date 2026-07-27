from __future__ import annotations

import zipfile

from tools.docx_research_intake import extract_docx, render_markdown


def synthetic_docx(path) -> None:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Strategy Overview</w:t></w:r></w:p>
  <w:p><w:r><w:t>Read </w:t></w:r><w:hyperlink r:id="rId1"><w:r><w:t>source</w:t></w:r></w:hyperlink></w:p>
  <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Status</w:t></w:r></w:p></w:tc></w:tr>
  <w:tr><w:tc><w:p><w:r><w:t>Carry</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Test</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
 </w:body>
</w:document>"""
    styles = """<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/></w:style>
</w:styles>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com" TargetMode="External"/>
</Relationships>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", relationships)
        archive.writestr("word/media/image1.png", b"png")


def test_extract_docx_preserves_structure_links_and_source_hash(tmp_path) -> None:
    path = tmp_path / "sample.docx"
    synthetic_docx(path)

    report = extract_docx(path)

    assert report["source"]["unchanged"] is True
    assert report["summary"] == {
        "paragraphs": 2,
        "nonempty_paragraphs": 2,
        "tables": 1,
        "table_rows": 2,
        "external_links": 1,
        "media_files": 1,
        "word_count_approx": 4,
    }
    assert report["external_links"] == ["https://example.com"]
    assert report["blocks"][0]["style_name"] == "Heading 1"
    assert report["blocks"][2]["rows"][1] == ["Carry", "Test"]
    assert report["visual_qa"]["performed"] is False


def test_render_markdown_uses_heading_and_table_structure(tmp_path) -> None:
    path = tmp_path / "sample.docx"
    synthetic_docx(path)

    markdown = render_markdown(extract_docx(path))

    assert "## Strategy Overview" in markdown
    assert "| Name | Status |" in markdown
    assert "https://example.com" in markdown


def test_extract_docx_preserves_cyrillic_utf8(tmp_path) -> None:
    path = tmp_path / "russian.docx"
    document = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:body><w:p><w:r><w:t>Торговая стратегия</w:t></w:r></w:p></w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)

    report = extract_docx(path)

    assert report["blocks"][0]["text"] == "Торговая стратегия"
    assert "Торговая стратегия" in render_markdown(report)
