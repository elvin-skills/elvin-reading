#!/usr/bin/env python3
"""Run deterministic release tests for Elvin-reading without network access."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


SCRIPT = Path(__file__).with_name("elvin_reading.py")


def process(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )


def run(*args: str) -> dict:
    result = process(*args)
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return json.loads(result.stdout)


def run_error(*args: str, contains: str | None = None) -> None:
    result = process(*args)
    if result.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(args)}")
    output = f"{result.stdout}\n{result.stderr}"
    if contains and contains not in output:
        raise AssertionError(
            f"expected error containing {contains!r}: {' '.join(args)}\n{output}"
        )


def run_failed_json(*args: str) -> dict:
    result = process(*args)
    if result.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(args)}")
    return json.loads(result.stdout)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_minimal_docx(path: Path, text: str) -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body>'
        '</w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            '</Relationships>',
        )
        archive.writestr("word/document.xml", document)


def write_minimal_epub(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<manifest><item id="chapter" href="chapter.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            f'<p>{escape(text)}</p></body></html>',
        )


def write_minimal_pdf(path: Path, text: str = "") -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = (
        f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        if text else b""
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, item in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(item)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(output)


def test_core_flow(root: Path) -> Path:
    project_root = root / "projects"
    project = project_root / "closure-reading"
    first = root / "first.txt"
    second = root / "second.txt"
    first.write_text(
        "A closure retains access to variables from its lexical environment.\n\n"
        "A compiler, which translates source code, may optimize the function.\n",
        encoding="utf-8",
    )
    second.write_text(
        "The closure, which was returned by the outer function, still referenced "
        "its lexical environment.\n",
        encoding="utf-8",
    )

    run(
        "init", str(project), "--name", "Closure 阅读",
        "--domain", "编程语言与闭包", "--goal", "理解闭包并比较两份材料",
    )
    first_ingest = run("ingest", str(project), str(first), "--title", "材料 A")
    assert first_ingest["source_label"] == "材料 A"
    listed = run("projects", "--root", str(project_root))
    assert listed["count"] == 1
    assert listed["projects"][0]["project"] == str(project.resolve())

    located = run(
        "locate", str(project), "--quote",
        "A closure retains access to variables from its lexical environment.",
    )
    assert located["count"] >= 1

    lexical = run(
        "record", str(project), "--type", "LEX", "--key", "closure",
        "--location", "line 3",
        "--quote", "A closure retains access to variables from its lexical environment.",
        "--question", "closure 在这里是什么意思？",
        "--answer", "闭包会保留对词法环境中变量的访问。",
        "--status", "partial",
    )
    assert lexical["notify_learning_notes"] is True
    assert Path(lexical["learning_notes_file"]).is_file()
    grammar = run(
        "record", str(project), "--type", "GRM",
        "--key", "non-restrictive relative clause", "--location", "line 5",
        "--quote", "A compiler, which translates source code, may optimize the function.",
        "--question", "which 从句怎么连接？",
        "--answer", "逗号之间的 which 从句补充说明 compiler。",
        "--status", "partial",
    )
    assert grammar["notify_learning_notes"] is False
    run(
        "record", str(project), "--type", "QST",
        "--key", "why closure keeps variables alive", "--location", "line 3",
        "--quote", "A closure retains access to variables from its lexical environment.",
        "--question", "为什么函数返回后变量还在？", "--status", "open",
    )
    run_error(
        "record", str(project), "--type", "LEX", "--key", "lexical environment",
        "--location", "line 3",
        "--quote", "A closure retains access to variables from its lexical environment.",
        "--answer", "词法环境。", "--status", "resolved",
        contains="success feedback",
    )
    run(
        "feedback", str(project), "--text", "单词都懂，但整句还是连不起来",
        "--signal", "grammar_block", "--strategy", "sentence-skeleton",
        "--outcome", "unknown", "--obstacle-type", "GRM", "--location", "line 5",
    )
    unknown_status = run("status", str(project))
    assert unknown_status["reader_model"]["preferred_strategy"] is None
    run_error(
        "feedback", str(project), "--text", "这样解释还是没懂",
        "--signal", "not_understood", "--strategy", "sentence-skeleton",
        "--outcome", "failed", "--obstacle-type", "GRM", "--location", "line 5",
        contains="next-strategy",
    )
    failed = run(
        "feedback", str(project), "--text", "这样解释还是没懂",
        "--signal", "not_understood", "--strategy", "sentence-skeleton",
        "--next-strategy", "chunk-paraphrase",
        "--outcome", "failed", "--obstacle-type", "GRM", "--location", "line 5",
    )
    assert failed["reader_model"]["pending_strategy"] == "chunk-paraphrase"
    success = run(
        "feedback", str(project), "--text", "按意群拆开后我懂了",
        "--signal", "understood", "--strategy", "chunk-paraphrase",
        "--outcome", "success", "--obstacle-type", "GRM", "--location", "line 5",
    )
    assert success["reader_model"]["pending_strategy"] is None
    run(
        "record", str(project), "--type", "GRM",
        "--key", "non-restrictive relative clause", "--location", "line 5",
        "--quote", "A compiler, which translates source code, may optimize the function.",
        "--question", "which 从句怎么连接？",
        "--answer", "逗号之间的 which 从句补充说明 compiler。",
        "--understanding", "按意群拆开后我懂了",
        "--feedback-id", success["feedback_id"], "--status", "resolved",
        "--related-to", grammar["event_id"], "--relation", "resolves",
    )
    run(
        "checkpoint", str(project), "--location", "材料 A 结尾",
        "--next-step", "开始阅读材料 B",
    )
    source_info = run("source-info", str(project))
    assert source_info["source_id"] == "SRC-0001"
    assert source_info["source_label"] == "材料 A"
    assert source_info["last_location"] == "材料 A 结尾"
    rebuilt = run("rebuild", str(project))
    assert rebuilt["event_count"] == 4

    second_ingest = run("ingest", str(project), str(second), "--title", "材料 B")
    assert second_ingest["source_label"] == "材料 B"
    recalled = run(
        "recall", str(project), "--query", "closure",
        "--type", "LEX", "--current-source", "SRC-0002",
    )
    assert recalled["count"] >= 1
    recalled_old = recalled["results"][0]["previous_occurrences"][0]
    assert recalled_old["source_id"] == "SRC-0001"
    assert recalled_old["source_label"] == "材料 A"
    assert recalled_old["source_title"] == "材料 A"
    assert recalled_old["location"] == "line 3"
    assert recalled_old["quote"] == "A closure retains access to variables from its lexical environment."
    run(
        "mark-reuse", str(project), "--current-source", "SRC-0002",
        "--memory-id", lexical["memory_id"],
        "--reason", "用材料 A 的原句与解释帮助理解材料 B 中的 closure",
    )
    run(
        "record", str(project), "--type", "LEX", "--key", "closure",
        "--location", "line 3",
        "--quote", "The closure, which was returned by the outer function, still referenced its lexical environment.",
        "--question", "这里的 closure 和上次一样吗？",
        "--answer", "核心义相同，本句额外强调外层函数返回后仍保留引用。",
        "--related-to", lexical["event_id"], "--relation", "repeats",
    )

    status = run("status", str(project))
    assert status["current_material_label"] == "材料 B"
    assert status["reader_model"]["preferred_strategy"] == "chunk-paraphrase"
    assert status["reader_model"]["avoid_strategy"] == "sentence-skeleton"
    assert status["feedback_records"] == 3
    assert Path(status["reading_state_file"]).is_file()
    assert Path(status["learning_notes_file"]).is_file()
    notes_root = project / "03-memory"
    vocabulary_notes = (notes_root / "01-词汇笔记.md").read_text(encoding="utf-8")
    grammar_notes = (notes_root / "02-语法笔记.md").read_text(encoding="utf-8")
    reading_notes = (notes_root / "03-问题与理解.md").read_text(encoding="utf-8")
    assert "closure" in vocabulary_notes and "当前语境义" in vocabulary_notes
    assert "语法｜which 从句怎么连接？" in grammar_notes
    assert "non-restrictive relative clause" in grammar_notes
    assert "你已明确确认理解" in grammar_notes
    assert "问题｜为什么函数返回后变量还在？" in reading_notes
    assert "why closure keeps variables alive" not in reading_notes
    assert "memory_id" not in vocabulary_notes + grammar_notes + reading_notes
    checked = run("validate", str(project))
    assert checked["ok"], checked["errors"]
    assert checked["feedback_records"] == 3
    return project


def test_material_formats(root: Path) -> None:
    project = root / "projects" / "format-reading"
    fixtures = root / "formats"
    fixtures.mkdir()
    markers = {
        "sample.txt": "Plain text format marker.",
        "sample.md": "Markdown format marker.",
        "sample.html": "HTML format marker.",
        "sample.docx": "DOCX format marker.",
        "sample.epub": "EPUB format marker.",
        "sample.pdf": "PDF format marker.",
    }
    (fixtures / "sample.txt").write_text(markers["sample.txt"], encoding="utf-8")
    (fixtures / "sample.md").write_text(f"# Test\n\n{markers['sample.md']}", encoding="utf-8")
    (fixtures / "sample.html").write_text(
        f"<html><body><p>{markers['sample.html']}</p>"
        "<script>hidden script marker</script></body></html>",
        encoding="utf-8",
    )
    write_minimal_docx(fixtures / "sample.docx", markers["sample.docx"])
    write_minimal_epub(fixtures / "sample.epub", markers["sample.epub"])
    write_minimal_pdf(fixtures / "sample.pdf", markers["sample.pdf"])

    run(
        "init", str(project), "--name", "格式测试",
        "--domain", "文件导入", "--goal", "验证所有承诺格式",
    )
    expected_extractors = {
        "sample.txt": "plain-text",
        "sample.md": "plain-text",
        "sample.html": "html",
        "sample.docx": "docx",
        "sample.epub": "epub",
        "sample.pdf": "pdf",
    }
    source_ids: dict[str, str] = {}
    for filename, marker in markers.items():
        result = run("ingest", str(project), str(fixtures / filename), "--title", filename)
        source_ids[filename] = result["source_id"]
        metadata = read_json(Path(result["metadata"]))
        extracted = Path(result["extracted_text"]).read_text(encoding="utf-8")
        assert metadata["extractor"] == expected_extractors[filename]
        assert marker in extracted
        if filename == "sample.html":
            assert "hidden script marker" not in extracted

    pdf_location = run(
        "locate", str(project), "--source", source_ids["sample.pdf"],
        "--quote", markers["sample.pdf"],
    )
    assert pdf_location["count"] == 1
    assert pdf_location["matches"][0]["location"] == "PDF page 1"
    epub_location = run(
        "locate", str(project), "--source", source_ids["sample.epub"],
        "--quote", markers["sample.epub"],
    )
    assert epub_location["count"] == 1
    assert epub_location["matches"][0]["location"] == "EPUB section 1"

    source_count = read_json(project / "00-state" / "project.json")["source_count"]
    empty = fixtures / "empty.txt"
    empty.write_text("", encoding="utf-8")
    run_error("ingest", str(project), str(empty), contains="没有提取到文本")
    unsupported = fixtures / "unsupported.bin"
    unsupported.write_bytes(b"not a supported reading material")
    run_error("ingest", str(project), str(unsupported), contains="暂不支持")
    scan = fixtures / "scan.pdf"
    write_minimal_pdf(scan)
    run_error("ingest", str(project), str(scan), contains="OCR")
    assert read_json(project / "00-state" / "project.json")["source_count"] == source_count


def test_corruption_detection(root: Path, source_project: Path) -> None:
    broken_jsonl = root / "projects" / "broken-jsonl"
    shutil.copytree(source_project, broken_jsonl)
    with (broken_jsonl / "02-events" / "reading-events.jsonl").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write("{broken json\n")
    checked = run_failed_json("validate", str(broken_jsonl))
    assert not checked["ok"]
    assert any("JSONL" in error for error in checked["errors"])

    broken_reference = root / "projects" / "broken-reference"
    shutil.copytree(source_project, broken_reference)
    state_path = broken_reference / "00-state" / "project.json"
    state = read_json(state_path)
    state["source_count"] = 999
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checked = run_failed_json("validate", str(broken_reference))
    assert not checked["ok"]
    assert any("source_count" in error for error in checked["errors"])


def test_legacy_feedback_compaction(root: Path, source_project: Path) -> None:
    project = root / "projects" / "legacy-feedback"
    shutil.copytree(source_project, project)
    feedback_path = project / "04-sessions" / "feedback.jsonl"
    legacy_pair = [
        {
            "feedback_id": "FDB-LEGACY-FAILED",
            "created_at": "2026-01-01T00:00:00Z",
            "source_id": "SRC-0002",
            "location": "line 3",
            "text": "还是没懂",
            "signal": "not_understood",
            "strategy": "minimal-context",
            "outcome": "failed",
            "obstacle_type": "GRM",
            "note": "旧写法中的失败记录",
        },
        {
            "feedback_id": "FDB-LEGACY-UNKNOWN",
            "created_at": "2026-01-01T00:00:00Z",
            "source_id": "SRC-0002",
            "location": "line 3",
            "text": "还是没懂",
            "signal": "wants_context",
            "strategy": "domain-example",
            "outcome": "unknown",
            "obstacle_type": "GRM",
            "note": "旧写法中重复的新策略记录",
        },
    ]
    with feedback_path.open("a", encoding="utf-8") as handle:
        for record in legacy_pair:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    status = run("status", str(project))
    assert status["raw_feedback_records"] == 5
    assert status["feedback_records"] == 4
    assert status["reader_model"]["pending_strategy"] == "domain-example"
    checked = run("validate", str(project))
    assert checked["ok"], checked["errors"]
    assert checked["raw_feedback_records"] == 5
    assert checked["feedback_records"] == 4


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="elvin-reading-test-") as temp:
        root = Path(temp)
        core_project = test_core_flow(root)
        test_material_formats(root)
        test_legacy_feedback_compaction(root, core_project)
        test_corruption_detection(root, core_project)

    print("Elvin-reading release tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
