#!/usr/bin/env python3
"""A+100 reading memory store and cross-source recall CLI.

Uses only Python's standard library for the memory store. PDF extraction
optionally uses local `pdftotext`, PyMuPDF, or pypdf when available.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET


SCHEMA_VERSION = "0.3"
MEMORY_TYPES = ("LEX", "GRM", "BKG", "INF", "QST", "CON", "OPI", "SOL", "CAS")
STATUSES = ("open", "partial", "resolved")
RELATIONS = ("repeats", "supports", "revises", "resolves", "contrasts")
FEEDBACK_SIGNALS = (
    "not_understood", "too_abstract", "too_detailed", "understood",
    "wants_application", "wants_context", "grammar_block", "vocabulary_block",
    "ready_to_continue",
)
EXPLANATION_STRATEGIES = (
    "minimal-context", "sentence-skeleton", "chunk-paraphrase",
    "reference-substitution", "logic-bridge", "minimal-background",
    "domain-example", "old-example-contrast", "application-case",
)
FEEDBACK_OUTCOMES = ("success", "partial", "failed", "unknown")
REQUIRED_DIRS = (
    "00-state", "01-sources", "02-events", "03-memory", "04-sessions", "05-reuse"
)
TOKEN_RE = re.compile(r"[\w]+(?:['’-][\w]+)?", re.UNICODE)
DEFAULT_PROJECT_ROOT = Path.home() / ".elvin-reading" / "projects"

SIGNAL_LABELS = {
    "not_understood": "仍未理解",
    "too_abstract": "解释太抽象",
    "too_detailed": "解释过长",
    "understood": "已经理解",
    "wants_application": "希望连接应用",
    "wants_context": "希望补足上下文",
    "grammar_block": "语法结构障碍",
    "vocabulary_block": "词汇障碍",
    "ready_to_continue": "准备继续阅读",
}

STRATEGY_LABELS = {
    "minimal-context": "最短语境解释",
    "sentence-skeleton": "句子主干",
    "chunk-paraphrase": "意群拆分",
    "reference-substitution": "指代替换",
    "logic-bridge": "论证桥梁",
    "minimal-background": "最小背景",
    "domain-example": "同领域例子",
    "old-example-contrast": "新旧例对照",
    "application-case": "应用案例",
}


class ElvinReadingError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def emit(data: object, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ElvinReadingError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ElvinReadingError(f"JSON 无法解析：{path}: {exc}") from exc


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def append_jsonl(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ElvinReadingError(f"JSONL 无法解析：{path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ElvinReadingError(f"JSONL 记录不是对象：{path}:{line_number}")
            records.append(value)
    return records


def project_paths(project: Path) -> dict[str, Path]:
    return {
        "project": project / "00-state" / "project.json",
        "sources": project / "01-sources",
        "events": project / "02-events" / "reading-events.jsonl",
        "index_json": project / "03-memory" / "memory-index.json",
        "index_md": project / "03-memory" / "memory-index.md",
        "open_questions": project / "03-memory" / "open-questions.md",
        "notes_home": project / "03-memory" / "00-学习笔记.md",
        "vocabulary_notes": project / "03-memory" / "01-词汇笔记.md",
        "grammar_notes": project / "03-memory" / "02-语法笔记.md",
        "reading_notes": project / "03-memory" / "03-问题与理解.md",
        "reuse": project / "05-reuse" / "reuse-log.jsonl",
        "checkpoints": project / "04-sessions" / "checkpoints.jsonl",
        "feedback": project / "04-sessions" / "feedback.jsonl",
        "reading_state": project / "00-阅读状态.md",
    }


def empty_reader_model() -> dict:
    return {
        "feedback_count": 0,
        "signal_counts": {},
        "strategy_stats": {},
        "preferred_strategy": None,
        "avoid_strategy": None,
        "pending_strategy": None,
        "last_feedback": None,
    }


def ensure_project_layout(project: Path, paths: dict[str, Path], state: dict) -> bool:
    changed = False
    for directory in REQUIRED_DIRS:
        path = project / directory
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            changed = True
    for path in (paths["events"], paths["reuse"], paths["checkpoints"], paths["feedback"]):
        if not path.exists():
            path.touch()
            changed = True
    if state.get("schema_version") != SCHEMA_VERSION:
        state["schema_version"] = SCHEMA_VERSION
        changed = True
    if not isinstance(state.get("reader_model"), dict):
        state["reader_model"] = empty_reader_model()
        changed = True
    else:
        defaults = empty_reader_model()
        for key, value in defaults.items():
            if key not in state["reader_model"]:
                state["reader_model"][key] = value
                changed = True
    if "next_step" not in state:
        state["next_step"] = None
        changed = True
    if changed:
        state["updated_at"] = utc_now()
        write_json(paths["project"], state)
    return changed


def ensure_project(project_arg: str | Path) -> tuple[Path, dict[str, Path], dict]:
    project = Path(project_arg).expanduser().resolve()
    paths = project_paths(project)
    if not paths["project"].exists():
        raise ElvinReadingError(f"这不是已初始化的 A+100 项目：{project}")
    state = read_json(paths["project"])
    changed = ensure_project_layout(project, paths, state)
    expected_reader_model = build_reader_model(read_jsonl(paths["feedback"]))
    if state.get("reader_model") != expected_reader_model:
        state["reader_model"] = expected_reader_model
        state["updated_at"] = utc_now()
        write_json(paths["project"], state)
        changed = True
    human_note_keys = ("notes_home", "vocabulary_notes", "grammar_notes", "reading_notes")
    if changed or any(not paths[key].exists() for key in human_note_keys):
        rebuild_project(project)
    if changed or not paths["reading_state"].exists():
        update_reading_state(project, state)
    return project, paths, state


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    tokens = TOKEN_RE.findall(value)
    return " ".join(tokens)


def light_stem(token: str) -> str:
    if not token.isascii() or len(token) < 4:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ing") and len(token) > 6:
        base = token[:-3]
        if len(base) > 2 and base[-1] == base[-2]:
            base = base[:-1]
        return base
    if token.endswith("ed") and len(token) > 5:
        return token[:-2]
    if token.endswith("es") and len(token) > 5:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 4:
        return token[:-1]
    return token


def token_set(value: str) -> set[str]:
    return {light_stem(token) for token in normalize_text(value).split() if token}


def split_aliases(values: list[str] | None) -> list[str]:
    if not values:
        return []
    aliases: list[str] = []
    for value in values:
        for item in re.split(r"[,，]", value):
            clean = item.strip()
            if clean and clean not in aliases:
                aliases.append(clean)
    return aliases


def memory_id(memory_type: str, normalized_key: str) -> str:
    digest = hashlib.sha1(f"{memory_type}:{normalized_key}".encode("utf-8")).hexdigest()[:12]
    return f"MEM-{digest.upper()}"


def event_id(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8].upper()}"


def source_sort_key(source_id: str) -> tuple[int, str]:
    match = re.fullmatch(r"SRC-(\d+)", source_id or "")
    return (int(match.group(1)), source_id) if match else (10**9, source_id or "")


def update_global_index(project: Path) -> None:
    root = project.parent.resolve()
    if root != DEFAULT_PROJECT_ROOT.expanduser().resolve():
        return
    entries: list[dict] = []
    for state_path in root.glob("*/00-state/project.json"):
        try:
            item = read_json(state_path)
        except ElvinReadingError:
            continue
        entries.append({
            "name": item.get("name") or state_path.parent.parent.name,
            "domain": item.get("domain") or "",
            "updated_at": item.get("updated_at") or item.get("created_at") or "",
            "next_step": item.get("next_step") or "继续当前材料",
            "project": str(state_path.parent.parent),
        })
    entries.sort(key=lambda item: item["updated_at"], reverse=True)
    lines = [
        "# Elvin-reading 阅读项目",
        "",
        "| 项目 | 领域 | 最近更新 | 下一步 |",
        "|---|---|---|---|",
    ]
    for item in entries:
        values = [
            item["name"], item["domain"], item["updated_at"], item["next_step"]
        ]
        clean = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
        lines.append(f"| {' | '.join(clean)} |")
    index_path = root.parent / "INDEX.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def update_reading_state(project: Path, state: dict) -> None:
    paths = project_paths(project)
    current = state.get("current_source")
    current_title = "尚未导入"
    original_file = ""
    if current:
        metadata_path = paths["sources"] / current / "metadata.json"
        if metadata_path.exists():
            metadata = read_json(metadata_path)
            current_title = metadata.get("title") or current
            stored = metadata.get("stored_original")
            if stored:
                original_file = str(paths["sources"] / current / stored)
        else:
            current_title = current
    current_position = (state.get("reading_positions") or {}).get(current, {})
    last_location = current_position.get("location") or state.get("last_location") or "尚未记录"
    reader = state.get("reader_model") or empty_reader_model()
    preferred = reader.get("preferred_strategy")
    avoid = reader.get("avoid_strategy")
    pending = reader.get("pending_strategy")
    last_feedback = reader.get("last_feedback") or {}
    index = read_json(paths["index_json"]) if paths["index_json"].exists() else {"items": []}
    open_questions = sum(
        1 for item in index.get("items", [])
        if item.get("type") == "QST" and item.get("status") in {"open", "partial"}
    )
    source_line = current_title
    if original_file:
        source_line = f"[{current_title}]({original_file})"
    text = f"""# {state['name']}｜阅读状态

## 阅读目标

- 领域：{state['domain']}
- 成果目标：{state['goal']}
- 材料数量：{state.get('source_count', 0)}

## 当前进度

- 当前材料：{source_line}
- 上次位置：{last_location}
- 下一步：{state.get('next_step') or '从上次位置继续阅读'}
- 开放问题：{open_questions}
- 最近更新：{state.get('updated_at') or state.get('created_at')}

## 当前读者状态

- 已记录真实反馈：{reader.get('feedback_count', 0)}
- 当前有效策略：{STRATEGY_LABELS.get(preferred, preferred) if preferred else '尚无足够证据'}
- 下次谨慎使用：{STRATEGY_LABELS.get(avoid, avoid) if avoid else '尚无'}
- 下一次解释策略：{STRATEGY_LABELS.get(pending, pending) if pending else '按当前问题判断'}
- 最近反馈：{last_feedback.get('text') or '尚无'}
- 最近结果：{last_feedback.get('outcome') or '尚无'}

## 学习笔记

[打开人类可读的学习笔记]({paths['notes_home']})

## 使用方式

在任意 Agent 中输入 `$elvin-reading` 开始或继续。阅读时直接提交划线原文和自然问题；状态、反馈与记忆由 Agent 在后台更新。

> 本页由程序生成。逐条阅读记忆保存在 `02-events/` 与 `03-memory/`，请勿在本页手工维护。
"""
    paths["reading_state"].write_text(text, encoding="utf-8")
    update_global_index(project)


def cmd_init(args: argparse.Namespace) -> dict:
    project = Path(args.project).expanduser().resolve()
    paths = project_paths(project)
    if paths["project"].exists():
        raise ElvinReadingError(f"项目已经初始化：{project}")
    if project.exists() and any(project.iterdir()):
        raise ElvinReadingError(f"目标目录不是空目录，且不是现有 A+100 项目：{project}")

    project.mkdir(parents=True, exist_ok=True)
    for directory in REQUIRED_DIRS:
        (project / directory).mkdir(parents=True, exist_ok=True)
    for path in (paths["events"], paths["reuse"], paths["checkpoints"], paths["feedback"]):
        path.touch()
    state = {
        "schema_version": SCHEMA_VERSION,
        "name": args.name.strip(),
        "domain": args.domain.strip(),
        "goal": args.goal.strip(),
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "current_source": None,
        "source_count": 0,
        "reading_positions": {},
        "last_location": None,
        "last_checkpoint_at": None,
        "next_step": "导入第一份材料并开始阅读",
        "reader_model": empty_reader_model(),
    }
    if not all((state["name"], state["domain"], state["goal"])):
        raise ElvinReadingError("项目名、窄领域和排他性目标不能为空")
    write_json(paths["project"], state)
    rebuild_project(project)
    update_reading_state(project, state)
    return {"ok": True, "project": str(project), "state": state}


def cmd_projects(args: argparse.Namespace) -> dict:
    root = Path(args.root).expanduser().resolve()
    projects: list[dict] = []
    if root.exists():
        for state_path in root.glob("*/00-state/project.json"):
            try:
                state = read_json(state_path)
            except ElvinReadingError:
                continue
            project = state_path.parent.parent
            current_source = state.get("current_source")
            position = (state.get("reading_positions") or {}).get(current_source, {})
            projects.append({
                "project": str(project),
                "name": state.get("name"),
                "domain": state.get("domain"),
                "goal": state.get("goal"),
                "current_source": current_source,
                "last_location": position.get("location") or state.get("last_location"),
                "source_count": state.get("source_count", 0),
                "next_step": state.get("next_step"),
                "preferred_strategy": (state.get("reader_model") or {}).get("preferred_strategy"),
                "updated_at": state.get("updated_at") or state.get("created_at") or "",
            })
    projects.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {
        "ok": True,
        "root": str(root),
        "count": len(projects),
        "projects": projects[: args.limit],
    }


class TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "p", "div", "section", "article", "header", "footer", "aside", "nav",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "br",
        "tr", "table", "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        elif not self.skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return clean_extracted_text("".join(self.parts))


def clean_extracted_text(value: str) -> str:
    value = html.unescape(value).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def decode_text_bytes(data: bytes, path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "latin-1"):
        try:
            return clean_extracted_text(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise ElvinReadingError(f"无法识别文本编码：{path}")


def html_to_text(data: bytes | str) -> str:
    if isinstance(data, bytes):
        raw = decode_text_bytes(data, Path("HTML"))
    else:
        raw = data
    parser = TextExtractor()
    parser.feed(raw)
    parser.close()
    return parser.text()


def extract_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ElvinReadingError(f"DOCX 无法解析：{path}: {exc}") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.rsplit("}", 1)[-1] != "p":
            continue
        parts: list[str] = []
        for node in paragraph.iter():
            local = node.tag.rsplit("}", 1)[-1]
            if local == "t" and node.text:
                parts.append(node.text)
            elif local in {"tab"}:
                parts.append("\t")
            elif local in {"br", "cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return clean_extracted_text("\n\n".join(paragraphs))


def archive_member(base: str, href: str) -> str:
    href = unquote(href.split("#", 1)[0])
    candidate = PurePosixPath(base).parent.joinpath(href)
    parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def extract_epub(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(
                (node.attrib.get("full-path") for node in container.iter()
                 if node.tag.rsplit("}", 1)[-1] == "rootfile" and node.attrib.get("full-path")),
                None,
            )
            if not rootfile:
                raise ElvinReadingError("EPUB 缺少 OPF rootfile")
            opf = ET.fromstring(archive.read(rootfile))
            manifest: dict[str, tuple[str, str]] = {}
            spine: list[str] = []
            for node in opf.iter():
                local = node.tag.rsplit("}", 1)[-1]
                if local == "item" and node.attrib.get("id") and node.attrib.get("href"):
                    manifest[node.attrib["id"]] = (
                        node.attrib["href"], node.attrib.get("media-type", "")
                    )
                elif local == "itemref" and node.attrib.get("idref"):
                    spine.append(node.attrib["idref"])
            chapters: list[str] = []
            for position, item_id in enumerate(spine, start=1):
                item = manifest.get(item_id)
                if not item:
                    continue
                href, media_type = item
                if "html" not in media_type and not href.lower().endswith((".xhtml", ".html", ".htm")):
                    continue
                member = archive_member(rootfile, href)
                text = html_to_text(archive.read(member))
                if text:
                    chapters.append(f"## EPUB section {position}\n\n{text}")
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ElvinReadingError(f"EPUB 无法解析：{path}: {exc}") from exc
    return clean_extracted_text("\n\n".join(chapters))


def format_pdf_pages(pages: list[str]) -> str:
    cleaned = [clean_extracted_text(page) for page in pages]
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    if not any(cleaned):
        return ""
    return clean_extracted_text(
        "\n\n".join(
            f"## PDF page {index}\n\n{text}"
            for index, text in enumerate(cleaned, start=1)
        )
    )


def decode_pdf_pages(data: bytes, path: Path) -> list[str]:
    for encoding in ("utf-8-sig", "utf-16", "gb18030", "latin-1"):
        try:
            raw = data.decode(encoding)
            return raw.replace("\r\n", "\n").replace("\r", "\n").split("\f")
        except UnicodeDecodeError:
            continue
    raise ElvinReadingError(f"无法识别 PDF 提取文本编码：{path}")


def extract_pdf(path: Path) -> str:
    parsed_without_text = False
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        result = subprocess.run(
            [pdftotext, "-layout", str(path), "-"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            parsed_without_text = True
            text = format_pdf_pages(decode_pdf_pages(result.stdout, path))
            if text:
                return text
    try:
        import fitz  # type: ignore

        with fitz.open(str(path)) as document:
            pages = [page.get_text("text") or "" for page in document]
        parsed_without_text = True
        text = format_pdf_pages(pages)
        if text:
            return text
    except ImportError:
        pass
    except Exception as exc:
        raise ElvinReadingError(f"PDF 无法解析：{path}: {exc}") from exc
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        if parsed_without_text:
            raise ElvinReadingError(
                "PDF 没有可提取文本，可能是扫描版，请先 OCR"
            ) from exc
        raise ElvinReadingError(
            "PDF 文本提取失败。请安装 pdftotext、PyMuPDF 或 pypdf；扫描版 PDF 请先 OCR。"
        ) from exc
    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ElvinReadingError(f"PDF 无法解析：{path}: {exc}") from exc
    text = format_pdf_pages(pages)
    if not text:
        raise ElvinReadingError("PDF 没有可提取文本，可能是扫描版，请先 OCR")
    return text


def extract_doc(path: Path) -> str:
    textutil = shutil.which("textutil")
    if not textutil:
        raise ElvinReadingError("旧版 .doc 需要 macOS textutil 或先转换为 .docx")
    result = subprocess.run(
        [textutil, "-convert", "txt", "-stdout", str(path)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ElvinReadingError(f"DOC 无法解析：{message or path}")
    return decode_text_bytes(result.stdout, path)


def extract_material(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".rst"}:
        return decode_text_bytes(path.read_bytes(), path), "plain-text"
    if suffix in {".html", ".htm"}:
        return html_to_text(path.read_bytes()), "html"
    if suffix == ".docx":
        return extract_docx(path), "docx"
    if suffix == ".doc":
        return extract_doc(path), "doc"
    if suffix == ".epub":
        return extract_epub(path), "epub"
    if suffix == ".pdf":
        return extract_pdf(path), "pdf"
    raise ElvinReadingError(f"暂不支持此文件类型：{suffix or '[无扩展名]'}")


def cmd_ingest(args: argparse.Namespace) -> dict:
    project, paths, state = ensure_project(args.project)
    material = Path(args.file).expanduser().resolve()
    if not material.is_file():
        raise ElvinReadingError(f"材料不存在或不是文件：{material}")
    text, extractor = extract_material(material)
    if not text.strip():
        raise ElvinReadingError("没有提取到文本；未创建 source")

    number = int(state.get("source_count", 0)) + 1
    source_id = f"SRC-{number:04d}"
    source_dir = paths["sources"] / source_id
    if source_dir.exists():
        raise ElvinReadingError(f"source 目录已存在，项目计数可能损坏：{source_dir}")
    source_dir.mkdir(parents=True)
    original_name = f"original{material.suffix.lower()}" if material.suffix else "original"
    shutil.copy2(material, source_dir / original_name)
    title = (args.title or material.stem).strip()
    extracted = f"# {title}\n\n<!-- source_id: {source_id}; extractor: {extractor} -->\n\n{text}\n"
    (source_dir / "extracted.md").write_text(extracted, encoding="utf-8")
    digest = hashlib.sha256(material.read_bytes()).hexdigest()
    metadata = {
        "source_id": source_id,
        "title": title,
        "original_name": material.name,
        "stored_original": original_name,
        "original_path": str(material),
        "sha256": digest,
        "extractor": extractor,
        "character_count": len(text),
        "imported_at": utc_now(),
    }
    write_json(source_dir / "metadata.json", metadata)
    state["source_count"] = number
    state["current_source"] = source_id
    state["next_step"] = f"从《{title}》开头开始阅读"
    state["updated_at"] = utc_now()
    write_json(paths["project"], state)
    update_reading_state(project, state)
    return {
        "ok": True,
        "source_id": source_id,
        "source_label": source_label(source_id),
        "title": title,
        "characters": len(text),
        "original_file": str(source_dir / original_name),
        "extracted_text": str(source_dir / "extracted.md"),
        "metadata": str(source_dir / "metadata.json"),
    }


def source_exists(paths: dict[str, Path], source_id: str) -> bool:
    return (paths["sources"] / source_id / "metadata.json").is_file()


def source_label(source_id: str) -> str:
    """Turn SRC-0001, SRC-0002... into reader-facing 材料 A, 材料 B... labels."""
    match = re.fullmatch(r"SRC-(\d+)", source_id or "")
    if not match:
        return "材料"
    number = int(match.group(1))
    if number < 1:
        return "材料"
    letters: list[str] = []
    while number:
        number, remainder = divmod(number - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return f"材料 {''.join(reversed(letters))}"


def source_information(paths: dict[str, Path], source_id: str) -> dict:
    if not source_exists(paths, source_id):
        raise ElvinReadingError(f"source 不存在：{source_id}")
    source_dir = paths["sources"] / source_id
    metadata = read_json(source_dir / "metadata.json")
    stored_original = metadata.get("stored_original")
    if not stored_original:
        raise ElvinReadingError(f"{source_id} metadata 缺少 stored_original")
    original_file = source_dir / stored_original
    if not original_file.is_file():
        raise ElvinReadingError(f"{source_id} 缺少存档原文：{original_file}")
    return {
        "source_id": source_id,
        "source_label": source_label(source_id),
        "title": metadata.get("title"),
        "original_file": str(original_file),
        "extracted_text": str(source_dir / "extracted.md"),
        "metadata": str(source_dir / "metadata.json"),
        "extractor": metadata.get("extractor"),
        "character_count": metadata.get("character_count"),
        "imported_at": metadata.get("imported_at"),
    }


def cmd_source_info(args: argparse.Namespace) -> dict:
    _, paths, state = ensure_project(args.project)
    source_id = args.source or state.get("current_source")
    if not source_id:
        raise ElvinReadingError("项目还没有当前材料")
    position = (state.get("reading_positions") or {}).get(source_id, {})
    return {
        "ok": True,
        **source_information(paths, source_id),
        "last_location": position.get("location"),
        "last_checkpoint_at": position.get("created_at"),
    }


def searchable_text_with_map(value: str) -> tuple[str, list[int]]:
    searchable: list[str] = []
    source_indexes: list[int] = []
    previous_was_space = False
    for source_index, character in enumerate(unicodedata.normalize("NFKC", value)):
        folded = character.casefold()
        for folded_character in folded:
            if folded_character.isspace():
                if searchable and not previous_was_space:
                    searchable.append(" ")
                    source_indexes.append(source_index)
                previous_was_space = True
            else:
                searchable.append(folded_character)
                source_indexes.append(source_index)
                previous_was_space = False
    if searchable and searchable[-1] == " ":
        searchable.pop()
        source_indexes.pop()
    return "".join(searchable), source_indexes


def location_for_index(content: str, source_index: int) -> tuple[str, int]:
    line_number = content.count("\n", 0, source_index) + 1
    lines = content.splitlines()
    heading = ""
    for index in range(min(line_number - 1, len(lines) - 1), -1, -1):
        candidate = lines[index].strip()
        if candidate.startswith("## PDF page ") or candidate.startswith("## EPUB section "):
            heading = candidate.removeprefix("## ")
            break
        if candidate.startswith("# ") and not heading:
            heading = candidate.removeprefix("# ")
    return (heading or f"line {line_number}"), line_number


def cmd_locate(args: argparse.Namespace) -> dict:
    _, paths, state = ensure_project(args.project)
    source_id = args.source or state.get("current_source")
    if not source_id:
        raise ElvinReadingError("项目还没有当前材料")
    info = source_information(paths, source_id)
    extracted_path = Path(info["extracted_text"])
    content = extracted_path.read_text(encoding="utf-8")
    quote = args.quote.strip()
    if not quote:
        raise ElvinReadingError("quote 不能为空")

    searchable_content, index_map = searchable_text_with_map(content)
    searchable_quote, _ = searchable_text_with_map(quote)
    matches: list[dict] = []
    if searchable_quote:
        start = 0
        while len(matches) < args.limit:
            found = searchable_content.find(searchable_quote, start)
            if found < 0:
                break
            source_index = index_map[found]
            end_source_index = index_map[min(found + len(searchable_quote) - 1, len(index_map) - 1)] + 1
            location, line_number = location_for_index(content, source_index)
            matches.append({
                "match_type": "exact",
                "score": 1.0,
                "location": location,
                "line": line_number,
                "matched_text": clean_extracted_text(content[source_index:end_source_index]),
            })
            start = found + max(1, len(searchable_quote))

    if not matches:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]
        quote_tokens = token_set(quote)
        fuzzy: list[tuple[float, str, int]] = []
        search_from = 0
        for block in blocks:
            block_index = content.find(block, search_from)
            if block_index < 0:
                block_index = content.find(block)
            search_from = max(search_from, block_index + len(block))
            block_searchable, _ = searchable_text_with_map(block)
            sequence_score = SequenceMatcher(None, searchable_quote, block_searchable).ratio()
            block_tokens = token_set(block)
            token_score = (
                len(quote_tokens & block_tokens) / len(quote_tokens)
                if quote_tokens else 0.0
            )
            score = max(sequence_score, token_score * 0.9)
            if score >= args.min_score:
                fuzzy.append((score, block, max(0, block_index)))
        fuzzy.sort(key=lambda item: item[0], reverse=True)
        for score, block, source_index in fuzzy[: args.limit]:
            location, line_number = location_for_index(content, source_index)
            matches.append({
                "match_type": "fuzzy",
                "score": round(score, 3),
                "location": location,
                "line": line_number,
                "matched_text": short(block, 500),
            })

    return {
        "ok": True,
        "source_id": source_id,
        "title": info.get("title"),
        "query": quote,
        "count": len(matches),
        "matches": matches,
    }


def cmd_record(args: argparse.Namespace) -> dict:
    project, paths, state = ensure_project(args.project)
    source_id = args.source or state.get("current_source")
    if not source_id:
        raise ElvinReadingError("项目还没有当前材料，请先 ingest 或传入 --source")
    if not source_exists(paths, source_id):
        raise ElvinReadingError(f"source 不存在：{source_id}")

    key = args.key.strip()
    location = args.location.strip()
    quote = args.quote.strip()
    if not key or not location or not quote:
        raise ElvinReadingError("key、location 和 quote 是长期记忆必需的证据字段")
    normalized_key = normalize_text(key)
    if not normalized_key:
        raise ElvinReadingError("key 规范化后为空")
    if args.status == "resolved" and not (args.answer or "").strip():
        raise ElvinReadingError("resolved 记录必须提供非空 answer")
    if bool(args.related_to) != bool(args.relation):
        raise ElvinReadingError("--related-to 与 --relation 必须同时提供")

    feedback_record = None
    if args.feedback_id:
        feedback_record = next(
            (
                item for item in read_jsonl(paths["feedback"])
                if item.get("feedback_id") == args.feedback_id
            ),
            None,
        )
        if not feedback_record:
            raise ElvinReadingError(f"feedback 不存在：{args.feedback_id}")
        if feedback_record.get("source_id") and feedback_record.get("source_id") != source_id:
            raise ElvinReadingError("feedback 与阅读事件不属于同一份材料")
    if (args.understanding or "").strip() and not feedback_record:
        raise ElvinReadingError("记录使用者理解必须提供 --feedback-id 作为真实反馈证据")
    if args.type in {"LEX", "GRM"} and args.status == "resolved":
        if not feedback_record or feedback_record.get("outcome") != "success":
            raise ElvinReadingError(
                "词汇或语法只有关联用户明确理解的 success feedback 后才能标记 resolved；"
                "首次解释请使用 partial"
            )

    existing = read_jsonl(paths["events"])
    existing_ids = {event.get("event_id") for event in existing}
    if args.related_to and args.related_to not in existing_ids:
        raise ElvinReadingError(f"related event 不存在：{args.related_to}")

    event = {
        "event_id": event_id("EVT"),
        "created_at": utc_now(),
        "source_id": source_id,
        "type": args.type,
        "key": key,
        "normalized_key": normalized_key,
        "location": location,
        "quote": quote,
        "question": (args.question or "").strip(),
        "answer": (args.answer or "").strip(),
        "understanding": (args.understanding or "").strip(),
        "feedback_id": args.feedback_id,
        "status": args.status,
        "aliases": split_aliases(args.alias),
        "related_to": args.related_to,
        "relation": args.relation,
    }
    append_jsonl(paths["events"], event)
    index = rebuild_project(project)
    state["updated_at"] = utc_now()
    write_json(paths["project"], state)
    update_reading_state(project, state)
    return {
        "ok": True,
        "event_id": event["event_id"],
        "memory_id": memory_id(args.type, normalized_key),
        "source_id": source_id,
        "memory_count": index["memory_count"],
        "notify_learning_notes": index["event_count"] == 1,
        "learning_notes_file": str(paths["notes_home"]),
    }


def effective_feedback_records(records: list[dict]) -> list[dict]:
    effective: list[dict] = []
    for original in records:
        current = dict(original)
        if effective:
            previous = effective[-1]
            same_user_message = all(
                previous.get(key) == current.get(key)
                for key in ("created_at", "source_id", "location", "text")
            )
            old_result_then_new_unknown = (
                previous.get("outcome") in {"failed", "partial"}
                and current.get("outcome") == "unknown"
                and previous.get("strategy") != current.get("strategy")
            )
            if same_user_message and old_result_then_new_unknown:
                previous["next_strategy"] = current.get("strategy")
                previous["merged_feedback_ids"] = [
                    previous.get("feedback_id"), current.get("feedback_id")
                ]
                continue
        effective.append(current)
    return effective


def build_reader_model(records: list[dict]) -> dict:
    records = effective_feedback_records(records)
    model = empty_reader_model()
    model["feedback_count"] = len(records)
    for record in records:
        signal = record.get("signal")
        strategy = record.get("strategy")
        outcome = record.get("outcome")
        if signal:
            counts = model["signal_counts"]
            counts[signal] = counts.get(signal, 0) + 1
        if strategy:
            stats = model["strategy_stats"].setdefault(
                strategy, {key: 0 for key in FEEDBACK_OUTCOMES}
            )
            if outcome in FEEDBACK_OUTCOMES:
                stats[outcome] += 1
    if records:
        latest = records[-1]
        model["last_feedback"] = {
            "feedback_id": latest.get("feedback_id"),
            "created_at": latest.get("created_at"),
            "text": latest.get("text"),
            "signal": latest.get("signal"),
            "strategy": latest.get("strategy"),
            "next_strategy": latest.get("next_strategy"),
            "outcome": latest.get("outcome"),
            "location": latest.get("location"),
        }
        if latest.get("outcome") == "unknown":
            model["pending_strategy"] = latest.get("strategy")
        else:
            model["pending_strategy"] = latest.get("next_strategy")
    scored: list[tuple[int, int, str]] = []
    failed: list[tuple[int, str]] = []
    for strategy, stats in model["strategy_stats"].items():
        score = stats.get("success", 0) * 3 + stats.get("partial", 0) - stats.get("failed", 0) * 2
        evidence = stats.get("success", 0) + stats.get("partial", 0) + stats.get("failed", 0)
        if evidence:
            scored.append((score, evidence, strategy))
        if stats.get("failed", 0):
            failed.append((stats["failed"], strategy))
    if scored:
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        if scored[0][0] > 0:
            model["preferred_strategy"] = scored[0][2]
    if failed:
        failed.sort(key=lambda item: (-item[0], item[1]))
        model["avoid_strategy"] = failed[0][1]
    return model


def cmd_feedback(args: argparse.Namespace) -> dict:
    project, paths, state = ensure_project(args.project)
    text = args.text.strip()
    if not text:
        raise ElvinReadingError("反馈原话不能为空")
    source_id = args.source or state.get("current_source")
    if source_id and not source_exists(paths, source_id):
        raise ElvinReadingError(f"source 不存在：{source_id}")
    if args.outcome == "failed" and not args.next_strategy:
        raise ElvinReadingError("解释失败时必须用 --next-strategy 选择不同的下一种策略")
    if args.next_strategy and args.next_strategy == args.strategy:
        raise ElvinReadingError("next strategy 必须不同于刚刚失败或不足的策略")
    position = (state.get("reading_positions") or {}).get(source_id, {})
    location = (args.location or position.get("location") or "当前对话").strip()
    record = {
        "feedback_id": event_id("FDB"),
        "created_at": utc_now(),
        "source_id": source_id,
        "location": location,
        "text": text,
        "signal": args.signal,
        "strategy": args.strategy,
        "next_strategy": args.next_strategy,
        "outcome": args.outcome,
        "obstacle_type": args.obstacle_type,
        "note": (args.note or "").strip(),
    }
    append_jsonl(paths["feedback"], record)
    records = read_jsonl(paths["feedback"])
    state["reader_model"] = build_reader_model(records)
    if args.outcome == "failed":
        next_label = STRATEGY_LABELS.get(args.next_strategy, args.next_strategy)
        state["next_step"] = f"改用{next_label}重新处理 {location}"
    elif args.outcome == "success" or args.signal == "ready_to_continue":
        state["next_step"] = f"从 {location} 继续阅读"
    state["updated_at"] = utc_now()
    write_json(paths["project"], state)
    update_reading_state(project, state)
    return {
        "ok": True,
        **record,
        "reader_model": state["reader_model"],
        "next_step": state.get("next_step"),
    }


def load_reuse_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in read_jsonl(path):
        item = record.get("memory_id")
        if item:
            counts[item] = counts.get(item, 0) + 1
    return counts


def rebuild_project(project: Path) -> dict:
    paths = project_paths(project)
    events = read_jsonl(paths["events"])
    reuse_counts = load_reuse_counts(paths["reuse"])
    grouped: dict[tuple[str, str], list[dict]] = {}
    for event in events:
        memory_type = event.get("type", "")
        normalized_key = event.get("normalized_key") or normalize_text(event.get("key", ""))
        if not memory_type or not normalized_key:
            continue
        grouped.setdefault((memory_type, normalized_key), []).append(event)

    items: list[dict] = []
    for (memory_type, normalized_key), occurrences in grouped.items():
        aliases: list[str] = []
        for occurrence in occurrences:
            for alias in occurrence.get("aliases") or []:
                if alias not in aliases:
                    aliases.append(alias)
        item_id = memory_id(memory_type, normalized_key)
        latest = occurrences[-1]
        item = {
            "memory_id": item_id,
            "type": memory_type,
            "key": latest.get("key") or occurrences[0].get("key"),
            "normalized_key": normalized_key,
            "aliases": aliases,
            "status": latest.get("status", "open"),
            "first_seen_source": occurrences[0].get("source_id"),
            "latest_seen_source": latest.get("source_id"),
            "occurrence_count": len(occurrences),
            "reuse_count": reuse_counts.get(item_id, 0),
            "occurrences": occurrences,
        }
        items.append(item)
    items.sort(key=lambda item: (MEMORY_TYPES.index(item["type"]), item["normalized_key"]))

    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "memory_count": len(items),
        "event_count": len(events),
        "items": items,
    }
    write_json(paths["index_json"], index)
    write_human_indexes(paths, index)
    return index


def markdown_cell(value: object) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "\\|")
    return text


def short(value: object, length: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= length else text[: length - 1] + "…"


HUMAN_TYPE_LABELS = {
    "LEX": "词汇",
    "GRM": "语法",
    "BKG": "背景",
    "INF": "推理",
    "QST": "问题",
    "CON": "概念",
    "OPI": "观点",
    "SOL": "方法",
    "CAS": "案例",
}


def source_title_map(paths: dict[str, Path]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for metadata_path in paths["sources"].glob("SRC-*/metadata.json"):
        try:
            metadata = read_json(metadata_path)
        except ElvinReadingError:
            continue
        source_id = metadata.get("source_id") or metadata_path.parent.name
        titles[source_id] = metadata.get("title") or source_id
    return titles


def quote_block(value: object) -> list[str]:
    lines = str(value or "").strip().splitlines() or [""]
    return [f"> {line}" for line in lines]


def human_learning_state(occurrence: dict, feedback_by_id: dict[str, dict]) -> str:
    memory_type = occurrence.get("type")
    status = occurrence.get("status")
    if memory_type == "QST":
        if status == "open":
            return "待后续材料继续回答"
        if status == "partial":
            return "已有部分答案，仍需继续追踪"
        return "已有可用答案"
    if memory_type in {"LEX", "GRM"}:
        feedback = feedback_by_id.get(occurrence.get("feedback_id"))
        if feedback:
            outcome = feedback.get("outcome")
            if outcome == "success":
                return "你已明确确认理解"
            if outcome == "partial":
                return "你已有部分理解，仍需继续"
            if outcome == "failed":
                return "当前解释未解决问题"
            return "已给出解释，等待你的反馈"
        if status == "open":
            return "尚未解决"
        return "已给出解释，但你尚未确认理解"
    if status == "open":
        return "尚未解决"
    if status == "partial":
        return "已有部分记录，仍需继续追踪"
    return "已有可用记录"


def render_human_notes(
    title: str,
    items: list[dict],
    source_titles: dict[str, str],
    feedback_by_id: dict[str, dict],
    answer_heading: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        "> 本页由 Elvin-reading 自动整理，面向阅读者使用。内容来自有原文证据的阅读记录，请勿手工维护。",
        "",
    ]
    if not items:
        lines.append("目前还没有可整理的内容。")
        return "\n".join(lines).rstrip() + "\n"

    for item in items:
        latest = item["occurrences"][-1]
        source_id = latest.get("source_id")
        source_title = source_titles.get(source_id, source_id or "未知材料")
        label = HUMAN_TYPE_LABELS.get(item.get("type"), item.get("type", "笔记"))
        memory_type = item.get("type")
        key = item.get("key") or "未命名记录"
        question = short(latest.get("question"), 100)
        if memory_type == "LEX":
            heading = key
        else:
            heading = f"{label}｜{question or key}"
        lines.extend([
            f"## {heading}",
            "",
            f"**当前状态：** {human_learning_state(latest, feedback_by_id)}",
            "",
            f"**来源：**《{source_title}》· {latest.get('location') or '位置未记录'}",
            "",
        ])
        if memory_type == "GRM" and question and key != question:
            lines.extend([f"**语法主题：** {key}", ""])
        if latest.get("question"):
            lines.extend(["### 你当时的问题", "", latest["question"].strip(), ""])
        if latest.get("answer"):
            lines.extend([f"### {answer_heading}", "", latest["answer"].strip(), ""])
        # “你的理解”只能来自已绑定的真实反馈；历史 Agent 推断不在此冒充用户确认。
        if latest.get("understanding") and feedback_by_id.get(latest.get("feedback_id")):
            lines.extend(["### 你的理解", "", latest["understanding"].strip(), ""])
        lines.extend(["### 原文证据", "", *quote_block(latest.get("quote")), ""])
        if item.get("aliases"):
            lines.extend([f"**相关表达：** {', '.join(item['aliases'])}", ""])
        if len(item["occurrences"]) > 1:
            lines.extend(["### 理解变化", ""])
            for occurrence in item["occurrences"]:
                old_source = source_titles.get(
                    occurrence.get("source_id"), occurrence.get("source_id") or "未知材料"
                )
                summary = occurrence.get("answer") or occurrence.get("question") or "新增证据"
                lines.append(
                    f"- 《{old_source}》· {occurrence.get('location') or '位置未记录'}："
                    f"{short(summary, 180)}（{human_learning_state(occurrence, feedback_by_id)}）"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_human_indexes(paths: dict[str, Path], index: dict) -> None:
    lines = ["# 阅读记忆索引", "", f"生成时间：{index['generated_at']}", ""]
    if not index["items"]:
        lines.append("尚无记忆。")
    for item in index["items"]:
        lines.extend([
            f"## {item['type']} · {item['key']}", "",
            f"- memory_id: `{item['memory_id']}`",
            f"- 状态：{item['status']}",
            f"- 出现次数：{item['occurrence_count']}",
            f"- 成功复用：{item['reuse_count']}",
        ])
        if item["aliases"]:
            lines.append(f"- 别名：{', '.join(item['aliases'])}")
        lines.append("")
        for occurrence in item["occurrences"]:
            lines.extend([
                f"### {occurrence.get('source_id')} · {occurrence.get('location')}", "",
                f"> {short(occurrence.get('quote'), 360)}", "",
            ])
            if occurrence.get("question"):
                lines.append(f"- 问题：{short(occurrence['question'], 240)}")
            if occurrence.get("answer"):
                lines.append(f"- 解释：{short(occurrence['answer'], 360)}")
            lines.append(f"- 状态：{occurrence.get('status')}")
            if occurrence.get("related_to"):
                lines.append(
                    f"- 关系：{occurrence.get('relation')} → `{occurrence.get('related_to')}`"
                )
            lines.append("")
    paths["index_md"].write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    question_lines = ["# 开放问题", ""]
    open_items = [
        item for item in index["items"]
        if item["type"] == "QST" and item["status"] in {"open", "partial"}
    ]
    if not open_items:
        question_lines.append("当前没有开放问题。")
    else:
        question_lines.extend([
            "| memory_id | 状态 | 问题键 | 最近材料 |",
            "|---|---|---|---|",
        ])
        for item in open_items:
            question_lines.append(
                f"| `{item['memory_id']}` | {item['status']} | {markdown_cell(item['key'])} | {item['latest_seen_source']} |"
            )
    paths["open_questions"].write_text("\n".join(question_lines).rstrip() + "\n", encoding="utf-8")

    source_titles = source_title_map(paths)
    feedback_by_id = {
        item.get("feedback_id"): item
        for item in read_jsonl(paths["feedback"])
        if item.get("feedback_id")
    }
    vocabulary_items = [item for item in index["items"] if item["type"] == "LEX"]
    grammar_items = [item for item in index["items"] if item["type"] == "GRM"]
    reading_items = [item for item in index["items"] if item["type"] not in {"LEX", "GRM"}]
    open_count = sum(
        1 for item in reading_items
        if item["type"] == "QST" and item["status"] in {"open", "partial"}
    )
    home_lines = [
        "# 学习笔记",
        "",
        "> 这里是给你直接阅读的入口。文件由 Elvin-reading 自动整理，内部索引仍保留给 Agent 使用。",
        "",
        f"- [词汇笔记]({paths['vocabulary_notes']})：{len(vocabulary_items)} 条",
        f"- [语法笔记]({paths['grammar_notes']})：{len(grammar_items)} 条",
        f"- [问题与理解]({paths['reading_notes']})：{len(reading_items)} 条，其中 {open_count} 个问题仍待追踪",
        "",
        f"最近整理：{index['generated_at']}",
    ]
    paths["notes_home"].write_text("\n".join(home_lines).rstrip() + "\n", encoding="utf-8")
    paths["vocabulary_notes"].write_text(
        render_human_notes(
            "词汇笔记", vocabulary_items, source_titles, feedback_by_id, "当前语境义"
        ),
        encoding="utf-8",
    )
    paths["grammar_notes"].write_text(
        render_human_notes(
            "语法笔记", grammar_items, source_titles, feedback_by_id, "当前解释"
        ),
        encoding="utf-8",
    )
    paths["reading_notes"].write_text(
        render_human_notes(
            "问题与理解", reading_items, source_titles, feedback_by_id, "当前回答或理解"
        ),
        encoding="utf-8",
    )


def cmd_rebuild(args: argparse.Namespace) -> dict:
    project, _, state = ensure_project(args.project)
    index = rebuild_project(project)
    update_reading_state(project, state)
    return {
        "ok": True,
        "memory_count": index["memory_count"],
        "event_count": index["event_count"],
    }


def recall_score(query: str, item: dict, occurrences: list[dict]) -> float:
    query_norm = normalize_text(query)
    query_tokens = token_set(query)
    candidate_key = item.get("normalized_key", "")
    candidate_tokens = token_set(candidate_key)
    score = 0.0
    if query_norm == candidate_key:
        score += 120
    elif " ".join(light_stem(token) for token in query_norm.split()) == " ".join(
        light_stem(token) for token in candidate_key.split()
    ):
        score += 105
    elif query_norm and (query_norm in candidate_key or candidate_key in query_norm):
        score += 55

    aliases = item.get("aliases") or []
    normalized_aliases = [normalize_text(alias) for alias in aliases]
    if query_norm in normalized_aliases:
        score += 100
    alias_tokens = set().union(*(token_set(alias) for alias in aliases)) if aliases else set()
    key_union = candidate_tokens | alias_tokens
    if query_tokens and key_union:
        score += 55 * len(query_tokens & key_union) / len(query_tokens | key_union)

    searchable = " ".join(
        str(value or "")
        for occurrence in occurrences
        for value in (
            occurrence.get("question"), occurrence.get("answer"), occurrence.get("quote")
        )
    )
    searchable_norm = normalize_text(searchable)
    if query_norm and query_norm in searchable_norm:
        score += 28
    searchable_tokens = token_set(searchable)
    if query_tokens and searchable_tokens:
        coverage = len(query_tokens & searchable_tokens) / len(query_tokens)
        score += 32 * coverage
    return score


def cmd_recall(args: argparse.Namespace) -> dict:
    _, paths, state = ensure_project(args.project)
    if not args.query and not args.key:
        raise ElvinReadingError("recall 需要 --query 或 --key")
    query = args.key or args.query
    index = read_json(paths["index_json"]) if paths["index_json"].exists() else rebuild_project(Path(args.project).expanduser().resolve())
    excluded_source = None if args.include_current else (args.current_source or state.get("current_source"))
    if excluded_source and not source_exists(paths, excluded_source):
        raise ElvinReadingError(f"current source 不存在：{excluded_source}")
    source_titles = source_title_map(paths)

    candidates: list[dict] = []
    for item in index.get("items", []):
        if args.type and item.get("type") != args.type:
            continue
        old_occurrences = [
            occurrence for occurrence in item.get("occurrences", [])
            if not excluded_source or occurrence.get("source_id") != excluded_source
        ]
        if not old_occurrences:
            continue
        old_aliases: list[str] = []
        for occurrence in old_occurrences:
            for alias in occurrence.get("aliases") or []:
                if alias not in old_aliases:
                    old_aliases.append(alias)
        historical_item = {
            **item,
            "key": old_occurrences[-1].get("key") or item.get("key"),
            "normalized_key": old_occurrences[-1].get("normalized_key") or item.get("normalized_key"),
            "aliases": old_aliases,
            "status": old_occurrences[-1].get("status", "open"),
        }
        score = recall_score(query, historical_item, old_occurrences)
        if score < args.min_score:
            continue
        candidates.append({
            "memory_id": historical_item["memory_id"],
            "type": historical_item["type"],
            "key": historical_item["key"],
            "status": historical_item["status"],
            "aliases": historical_item["aliases"],
            "score": round(score, 2),
            "previous_occurrences": [
                {
                    **occurrence,
                    "source_label": source_label(occurrence.get("source_id", "")),
                    "source_title": source_titles.get(
                        occurrence.get("source_id"), occurrence.get("source_id")
                    ),
                }
                for occurrence in list(reversed(old_occurrences))[: args.occurrences]
            ],
        })
    candidates.sort(key=lambda item: (-item["score"], item["memory_id"]))
    results = candidates[: args.limit]
    return {
        "ok": True,
        "query": query,
        "excluded_current_source": excluded_source,
        "count": len(results),
        "results": results,
    }


def format_recall(result: dict) -> str:
    if not result["results"]:
        exclusion = result.get("excluded_current_source") or "无"
        return f"未找到可复用旧记忆（已排除当前材料：{exclusion}）。"
    lines = [
        f"召回 {result['count']} 条旧记忆；已排除当前材料：{result.get('excluded_current_source') or '无'}。"
    ]
    for rank, item in enumerate(result["results"], start=1):
        lines.extend([
            "",
            f"[{rank}] {item['memory_id']} | {item['type']} | {item['key']} | score={item['score']}",
            f"状态：{item['status']}",
        ])
        if item.get("aliases"):
            lines.append(f"别名：{', '.join(item['aliases'])}")
        for occurrence in item["previous_occurrences"]:
            label = occurrence.get("source_label") or source_label(
                occurrence.get("source_id", "")
            )
            title = occurrence.get("source_title") or occurrence.get("source_id")
            material_name = label if title == label else f"{label}《{title}》"
            lines.extend([
                f"- {material_name} · {occurrence.get('location')}",
                f"  原句：{short(occurrence.get('quote'), 360)}",
            ])
            if occurrence.get("question"):
                lines.append(f"  当时问题：{short(occurrence['question'], 240)}")
            if occurrence.get("answer"):
                lines.append(f"  当时解释：{short(occurrence['answer'], 360)}")
    return "\n".join(lines)


def cmd_mark_reuse(args: argparse.Namespace) -> dict:
    project, paths, state = ensure_project(args.project)
    if not source_exists(paths, args.current_source):
        raise ElvinReadingError(f"current source 不存在：{args.current_source}")
    index = read_json(paths["index_json"]) if paths["index_json"].exists() else rebuild_project(project)
    item = next((item for item in index.get("items", []) if item.get("memory_id") == args.memory_id), None)
    if not item:
        raise ElvinReadingError(f"memory 不存在：{args.memory_id}")
    old_sources = {
        occurrence.get("source_id") for occurrence in item.get("occurrences", [])
        if occurrence.get("source_id") != args.current_source
    }
    if not old_sources:
        raise ElvinReadingError("该记忆没有来自旧材料的 occurrence，不能标记为跨材料复用")
    reason = args.reason.strip()
    if not reason:
        raise ElvinReadingError("reason 不能为空")
    record = {
        "reuse_id": event_id("RUS"),
        "created_at": utc_now(),
        "current_source": args.current_source,
        "memory_id": args.memory_id,
        "old_sources": sorted(old_sources, key=source_sort_key),
        "reason": reason,
    }
    append_jsonl(paths["reuse"], record)
    rebuild_project(project)
    state["updated_at"] = utc_now()
    write_json(paths["project"], state)
    update_reading_state(project, state)
    return {"ok": True, **record}


def cmd_checkpoint(args: argparse.Namespace) -> dict:
    project, paths, state = ensure_project(args.project)
    source_id = args.source or state.get("current_source")
    if not source_id:
        raise ElvinReadingError("项目还没有当前材料")
    if not source_exists(paths, source_id):
        raise ElvinReadingError(f"source 不存在：{source_id}")
    location = args.location.strip()
    if not location:
        raise ElvinReadingError("location 不能为空")
    checkpoint = {
        "checkpoint_id": event_id("CHK"),
        "created_at": utc_now(),
        "source_id": source_id,
        "location": location,
        "note": (args.note or "").strip(),
        "next_step": (args.next_step or "").strip(),
    }
    append_jsonl(paths["checkpoints"], checkpoint)
    positions = state.get("reading_positions") or {}
    positions[source_id] = {
        "location": location,
        "note": checkpoint["note"],
        "created_at": checkpoint["created_at"],
    }
    state["reading_positions"] = positions
    state["last_location"] = location
    state["last_checkpoint_at"] = checkpoint["created_at"]
    state["next_step"] = checkpoint["next_step"] or f"从 {location} 继续阅读"
    state["updated_at"] = utc_now()
    write_json(paths["project"], state)
    update_reading_state(project, state)
    return {"ok": True, "project": str(project), **checkpoint}


def validate_project(project: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    paths = project_paths(project)
    for directory in REQUIRED_DIRS:
        if not (project / directory).is_dir():
            errors.append(f"缺少目录：{directory}")
    for key in (
        "project", "events", "reuse", "checkpoints", "feedback", "reading_state",
        "notes_home", "vocabulary_notes", "grammar_notes", "reading_notes",
    ):
        if not paths[key].exists():
            errors.append(f"缺少文件：{paths[key].relative_to(project)}")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    try:
        state = read_json(paths["project"])
        events = read_jsonl(paths["events"])
        reuse = read_jsonl(paths["reuse"])
        checkpoints = read_jsonl(paths["checkpoints"])
        feedback = read_jsonl(paths["feedback"])
    except ElvinReadingError as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": warnings}

    source_ids: set[str] = set()
    metadata_count = 0
    for source_dir in sorted(paths["sources"].glob("SRC-*")):
        if not source_dir.is_dir():
            continue
        source_id = source_dir.name
        source_ids.add(source_id)
        metadata_count += 1
        for filename in ("metadata.json", "extracted.md"):
            if not (source_dir / filename).is_file():
                errors.append(f"{source_id} 缺少 {filename}")
        try:
            metadata = read_json(source_dir / "metadata.json")
            if metadata.get("source_id") != source_id:
                errors.append(f"{source_id} 的 metadata source_id 不一致")
            stored = metadata.get("stored_original")
            if not stored or not (source_dir / stored).is_file():
                errors.append(f"{source_id} 缺少原始材料副本")
        except ElvinReadingError as exc:
            errors.append(str(exc))

    if int(state.get("source_count", -1)) != metadata_count:
        errors.append(
            f"project source_count={state.get('source_count')}，实际 source={metadata_count}"
        )
    if state.get("current_source") and state["current_source"] not in source_ids:
        errors.append(f"current_source 不存在：{state['current_source']}")
    for position, checkpoint in enumerate(checkpoints, start=1):
        checkpoint_id = checkpoint.get("checkpoint_id")
        if not checkpoint_id:
            errors.append(f"checkpoint 第 {position} 条缺 checkpoint_id")
        if checkpoint.get("source_id") not in source_ids:
            errors.append(f"checkpoint {checkpoint_id} 引用不存在 source")
        if not str(checkpoint.get("location", "")).strip():
            errors.append(f"checkpoint {checkpoint_id} location 为空")

    feedback_ids: set[str] = set()
    required_feedback_fields = {
        "feedback_id", "created_at", "source_id", "location", "text",
        "signal", "strategy", "outcome", "obstacle_type", "note",
    }
    for position, record in enumerate(feedback, start=1):
        missing = sorted(required_feedback_fields - set(record))
        if missing:
            errors.append(f"feedback 第 {position} 条缺字段：{', '.join(missing)}")
            continue
        feedback_id = record.get("feedback_id")
        if not feedback_id:
            errors.append(f"feedback 第 {position} 条缺 feedback_id")
        elif feedback_id in feedback_ids:
            errors.append(f"feedback_id 重复：{feedback_id}")
        feedback_ids.add(feedback_id)
        if record.get("source_id") and record.get("source_id") not in source_ids:
            errors.append(f"feedback {feedback_id} 引用不存在 source")
        if not str(record.get("text", "")).strip():
            errors.append(f"feedback {feedback_id} text 为空")
        if record.get("signal") not in FEEDBACK_SIGNALS:
            errors.append(f"feedback {feedback_id} signal 非法：{record.get('signal')}")
        if record.get("strategy") not in EXPLANATION_STRATEGIES:
            errors.append(f"feedback {feedback_id} strategy 非法：{record.get('strategy')}")
        if record.get("next_strategy") and record.get("next_strategy") not in EXPLANATION_STRATEGIES:
            errors.append(
                f"feedback {feedback_id} next_strategy 非法：{record.get('next_strategy')}"
            )
        if record.get("next_strategy") and record.get("next_strategy") == record.get("strategy"):
            errors.append(f"feedback {feedback_id} 没有真正更换解释策略")
        if record.get("outcome") not in FEEDBACK_OUTCOMES:
            errors.append(f"feedback {feedback_id} outcome 非法：{record.get('outcome')}")
        if record.get("obstacle_type") and record.get("obstacle_type") not in MEMORY_TYPES:
            errors.append(
                f"feedback {feedback_id} obstacle_type 非法：{record.get('obstacle_type')}"
            )

    expected_reader_model = build_reader_model(feedback)
    if state.get("reader_model") != expected_reader_model:
        errors.append("project reader_model 与 feedback 日志不一致")
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"project schema_version={state.get('schema_version')}，期望 {SCHEMA_VERSION}"
        )

    event_ids: set[str] = set()
    valid_events: list[dict] = []
    required_event_fields = {
        "event_id", "created_at", "source_id", "type", "key", "normalized_key",
        "location", "quote", "status", "aliases",
    }
    for position, event in enumerate(events, start=1):
        missing = sorted(required_event_fields - set(event))
        if missing:
            errors.append(f"event 第 {position} 条缺字段：{', '.join(missing)}")
            continue
        item_id = event.get("event_id")
        if item_id in event_ids:
            errors.append(f"event_id 重复：{item_id}")
        event_ids.add(item_id)
        if event.get("source_id") not in source_ids:
            errors.append(f"event {item_id} 引用不存在 source：{event.get('source_id')}")
        if event.get("type") not in MEMORY_TYPES:
            errors.append(f"event {item_id} type 非法：{event.get('type')}")
        if event.get("status") not in STATUSES:
            errors.append(f"event {item_id} status 非法：{event.get('status')}")
        if not str(event.get("key", "")).strip() or not str(event.get("location", "")).strip() or not str(event.get("quote", "")).strip():
            errors.append(f"event {item_id} 缺少 EvidenceSpan 或 key")
        if event.get("normalized_key") != normalize_text(event.get("key", "")):
            errors.append(f"event {item_id} normalized_key 与 key 不一致")
        if event.get("status") == "resolved" and not str(event.get("answer", "")).strip():
            errors.append(f"event {item_id} resolved 但 answer 为空")
        if bool(event.get("related_to")) != bool(event.get("relation")):
            errors.append(f"event {item_id} related_to/relation 不成对")
        if event.get("relation") and event.get("relation") not in RELATIONS:
            errors.append(f"event {item_id} relation 非法：{event.get('relation')}")
        linked_feedback = event.get("feedback_id")
        if linked_feedback and linked_feedback not in feedback_ids:
            errors.append(f"event {item_id} 引用不存在 feedback：{linked_feedback}")
        elif linked_feedback:
            feedback_record = next(
                item for item in feedback if item.get("feedback_id") == linked_feedback
            )
            if feedback_record.get("source_id") and feedback_record.get("source_id") != event.get("source_id"):
                errors.append(f"event {item_id} 与 feedback 不属于同一份材料")
            if (
                event.get("type") in {"LEX", "GRM"}
                and event.get("status") == "resolved"
                and feedback_record.get("outcome") != "success"
            ):
                errors.append(f"event {item_id} 未经明确理解反馈就标记 resolved")
        valid_events.append(event)
    for event in valid_events:
        if event.get("related_to") and event["related_to"] not in event_ids:
            errors.append(f"event {event['event_id']} 引用不存在 event：{event['related_to']}")

    expected_memory_ids = {
        memory_id(event.get("type", ""), event.get("normalized_key", ""))
        for event in valid_events
    }
    reuse_ids: set[str] = set()
    for position, record in enumerate(reuse, start=1):
        reuse_id = record.get("reuse_id")
        if not reuse_id:
            errors.append(f"reuse 第 {position} 条缺 reuse_id")
        elif reuse_id in reuse_ids:
            errors.append(f"reuse_id 重复：{reuse_id}")
        reuse_ids.add(reuse_id)
        if record.get("current_source") not in source_ids:
            errors.append(f"reuse {reuse_id} 引用不存在 current source")
        if record.get("memory_id") not in expected_memory_ids:
            errors.append(f"reuse {reuse_id} 引用不存在 memory：{record.get('memory_id')}")
        old_sources = set(record.get("old_sources") or [])
        if not old_sources or not old_sources.issubset(source_ids):
            errors.append(f"reuse {reuse_id} 的 old_sources 无效")
        if record.get("current_source") in old_sources:
            errors.append(f"reuse {reuse_id} 把当前材料列为旧材料")
        if not str(record.get("reason", "")).strip():
            errors.append(f"reuse {reuse_id} reason 为空")

    try:
        rebuilt = rebuild_project(project)
        if rebuilt["event_count"] != len(events):
            errors.append("重建后的 event_count 不一致")
    except ElvinReadingError as exc:
        errors.append(f"索引重建失败：{exc}")

    if not events:
        warnings.append("项目尚无阅读记忆")
    if metadata_count < 2:
        warnings.append("尚未导入第二份材料，无法验证跨材料复用")
    effective_feedback = effective_feedback_records(feedback)
    return {
        "ok": not errors,
        "project": str(project),
        "sources": metadata_count,
        "events": len(events),
        "memories": len(expected_memory_ids),
        "reuse_records": len(reuse),
        "feedback_records": len(effective_feedback),
        "raw_feedback_records": len(feedback),
        "errors": errors,
        "warnings": warnings,
    }


def cmd_validate(args: argparse.Namespace) -> dict:
    project = Path(args.project).expanduser().resolve()
    return validate_project(project)


def cmd_status(args: argparse.Namespace) -> dict:
    project, paths, state = ensure_project(args.project)
    events = read_jsonl(paths["events"])
    reuse = read_jsonl(paths["reuse"])
    feedback = read_jsonl(paths["feedback"])
    effective_feedback = effective_feedback_records(feedback)
    index = read_json(paths["index_json"]) if paths["index_json"].exists() else rebuild_project(project)
    by_type = {memory_type: 0 for memory_type in MEMORY_TYPES}
    for item in index.get("items", []):
        by_type[item["type"]] += 1
    open_questions = sum(
        1 for item in index.get("items", [])
        if item["type"] == "QST" and item["status"] in {"open", "partial"}
    )
    current_source = state.get("current_source")
    current_position = (state.get("reading_positions") or {}).get(current_source, {})
    current_info = source_information(paths, current_source) if current_source else {}
    reader_model = state.get("reader_model") or empty_reader_model()
    return {
        "ok": True,
        "project": str(project),
        "name": state.get("name"),
        "domain": state.get("domain"),
        "goal": state.get("goal"),
        "current_source": current_source,
        "current_material_label": current_info.get("source_label"),
        "current_title": current_info.get("title"),
        "original_file": current_info.get("original_file"),
        "last_location": current_position.get("location") or state.get("last_location"),
        "last_checkpoint_at": current_position.get("created_at") or state.get("last_checkpoint_at"),
        "next_step": state.get("next_step"),
        "sources": state.get("source_count", 0),
        "events": len(events),
        "memories": index.get("memory_count", 0),
        "by_type": {key: value for key, value in by_type.items() if value},
        "open_questions": open_questions,
        "reuse_records": len(reuse),
        "feedback_records": len(effective_feedback),
        "raw_feedback_records": len(feedback),
        "reader_model": reader_model,
        "recent_feedback": effective_feedback[-5:],
        "reading_state_file": str(paths["reading_state"]),
        "learning_notes_file": str(paths["notes_home"]),
    }


def format_status(result: dict) -> str:
    types = "、".join(f"{key}={value}" for key, value in result["by_type"].items()) or "无"
    return "\n".join([
        f"项目：{result['name']}",
        f"领域：{result['domain']}",
        f"目标：{result['goal']}",
        f"当前材料：{result['current_title'] or result['current_source'] or '无'}",
        f"上次位置：{result['last_location'] or '尚未记录'}",
        f"下一步：{result['next_step'] or '从上次位置继续阅读'}",
        f"材料 / 事件 / 记忆：{result['sources']} / {result['events']} / {result['memories']}",
        f"记忆类型：{types}",
        f"开放问题：{result['open_questions']}",
        f"跨材料成功复用：{result['reuse_records']}",
        f"真实反馈：{result['feedback_records']}",
        f"有效解释策略：{STRATEGY_LABELS.get(result['reader_model'].get('preferred_strategy'), '尚无足够证据')}",
        f"下次谨慎使用：{STRATEGY_LABELS.get(result['reader_model'].get('avoid_strategy'), '尚无')}",
        f"阅读状态页：{result['reading_state_file']}",
        f"学习笔记：{result['learning_notes_file']}",
    ])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A+100 本地阅读记忆：导入、记录、跨材料召回、复用与校验"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    projects_parser = subparsers.add_parser("projects", help="列出本地阅读项目，最近更新优先")
    projects_parser.add_argument("--root", default=str(DEFAULT_PROJECT_ROOT))
    projects_parser.add_argument("--limit", type=int, default=20)
    projects_parser.add_argument("--json", action="store_true")
    projects_parser.set_defaults(handler=cmd_projects)

    init_parser = subparsers.add_parser("init", help="初始化阅读项目")
    init_parser.add_argument("project")
    init_parser.add_argument("--name", required=True)
    init_parser.add_argument("--domain", required=True)
    init_parser.add_argument("--goal", required=True)
    init_parser.add_argument("--json", action="store_true")
    init_parser.set_defaults(handler=cmd_init)

    ingest_parser = subparsers.add_parser("ingest", help="导入一份材料并提取文本")
    ingest_parser.add_argument("project")
    ingest_parser.add_argument("file")
    ingest_parser.add_argument("--title")
    ingest_parser.add_argument("--json", action="store_true")
    ingest_parser.set_defaults(handler=cmd_ingest)

    source_parser = subparsers.add_parser("source-info", help="取得当前原文路径和阅读位置")
    source_parser.add_argument("project")
    source_parser.add_argument("--source", help="默认使用当前 source")
    source_parser.add_argument("--json", action="store_true")
    source_parser.set_defaults(handler=cmd_source_info)

    locate_parser = subparsers.add_parser("locate", help="在当前材料中自动定位复制的原文")
    locate_parser.add_argument("project")
    locate_parser.add_argument("--source", help="默认使用当前 source")
    locate_parser.add_argument("--quote", required=True)
    locate_parser.add_argument("--limit", type=int, default=5)
    locate_parser.add_argument("--min-score", type=float, default=0.55)
    locate_parser.add_argument("--json", action="store_true")
    locate_parser.set_defaults(handler=cmd_locate)

    record_parser = subparsers.add_parser("record", help="追加一条带原文证据的阅读事件")
    record_parser.add_argument("project")
    record_parser.add_argument("--source", help="默认使用当前 source")
    record_parser.add_argument("--type", required=True, choices=MEMORY_TYPES)
    record_parser.add_argument("--key", required=True)
    record_parser.add_argument("--location", required=True)
    record_parser.add_argument("--quote", required=True)
    record_parser.add_argument("--question")
    record_parser.add_argument("--answer")
    record_parser.add_argument("--understanding")
    record_parser.add_argument("--feedback-id", help="证明使用者理解状态的真实反馈记录")
    record_parser.add_argument("--status", choices=STATUSES, default="partial")
    record_parser.add_argument("--alias", action="append", help="可重复或使用逗号分隔")
    record_parser.add_argument("--related-to")
    record_parser.add_argument("--relation", choices=RELATIONS)
    record_parser.add_argument("--json", action="store_true")
    record_parser.set_defaults(handler=cmd_record)

    feedback_parser = subparsers.add_parser("feedback", help="记录真实阅读反馈与解释策略结果")
    feedback_parser.add_argument("project")
    feedback_parser.add_argument("--source", help="默认使用当前 source")
    feedback_parser.add_argument("--location", help="默认使用最近阅读位置或当前对话")
    feedback_parser.add_argument("--text", required=True, help="使用者真实原话")
    feedback_parser.add_argument("--signal", required=True, choices=FEEDBACK_SIGNALS)
    feedback_parser.add_argument("--strategy", required=True, choices=EXPLANATION_STRATEGIES)
    feedback_parser.add_argument(
        "--next-strategy", choices=EXPLANATION_STRATEGIES,
        help="本条反馈后准备采用的新策略；failed 时必填且必须与 strategy 不同",
    )
    feedback_parser.add_argument("--outcome", choices=FEEDBACK_OUTCOMES, default="unknown")
    feedback_parser.add_argument("--obstacle-type", choices=MEMORY_TYPES)
    feedback_parser.add_argument("--note")
    feedback_parser.add_argument("--json", action="store_true")
    feedback_parser.set_defaults(handler=cmd_feedback)

    recall_parser = subparsers.add_parser("recall", help="从旧材料召回候选记忆")
    recall_parser.add_argument("project")
    query_group = recall_parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument("--query", help="自然问题、句式或词语")
    query_group.add_argument("--key", help="稳定检索键")
    recall_parser.add_argument("--type", choices=MEMORY_TYPES)
    recall_parser.add_argument("--current-source", help="从候选中排除；默认项目当前 source")
    recall_parser.add_argument("--include-current", action="store_true", help="审计时包含当前材料")
    recall_parser.add_argument("--limit", type=int, default=5)
    recall_parser.add_argument("--occurrences", type=int, default=3, help="每条记忆最多返回几条前序材料记录")
    recall_parser.add_argument("--min-score", type=float, default=16.0)
    recall_parser.add_argument("--json", action="store_true")
    recall_parser.set_defaults(handler=cmd_recall)

    reuse_parser = subparsers.add_parser("mark-reuse", help="记录一次真正采用的旧记忆")
    reuse_parser.add_argument("project")
    reuse_parser.add_argument("--current-source", required=True)
    reuse_parser.add_argument("--memory-id", required=True)
    reuse_parser.add_argument("--reason", required=True)
    reuse_parser.add_argument("--json", action="store_true")
    reuse_parser.set_defaults(handler=cmd_mark_reuse)

    checkpoint_parser = subparsers.add_parser("checkpoint", help="保存当前阅读位置")
    checkpoint_parser.add_argument("project")
    checkpoint_parser.add_argument("--source", help="默认使用当前 source")
    checkpoint_parser.add_argument("--location", required=True)
    checkpoint_parser.add_argument("--note")
    checkpoint_parser.add_argument("--next-step")
    checkpoint_parser.add_argument("--json", action="store_true")
    checkpoint_parser.set_defaults(handler=cmd_checkpoint)

    rebuild_parser = subparsers.add_parser("rebuild", help="从事件和复用日志重建索引")
    rebuild_parser.add_argument("project")
    rebuild_parser.add_argument("--json", action="store_true")
    rebuild_parser.set_defaults(handler=cmd_rebuild)

    status_parser = subparsers.add_parser("status", help="查看项目阅读状态")
    status_parser.add_argument("project")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=cmd_status)

    validate_parser = subparsers.add_parser("validate", help="校验项目结构与所有引用")
    validate_parser.add_argument("project")
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.set_defaults(handler=cmd_validate)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.handler(args)
        if args.command == "recall" and not args.json:
            print(format_recall(result))
        elif args.command == "status" and not args.json:
            print(format_status(result))
        elif args.command == "validate" and not args.json:
            if result["ok"]:
                print(
                    f"校验通过：sources={result['sources']} events={result['events']} "
                    f"memories={result['memories']} feedback={result['feedback_records']} "
                    f"reuse={result['reuse_records']}"
                )
                for warning in result["warnings"]:
                    print(f"警告：{warning}")
            else:
                print("校验失败：", file=sys.stderr)
                for error in result["errors"]:
                    print(f"- {error}", file=sys.stderr)
        else:
            emit(result, args.json)
        return 0 if result.get("ok", True) else 1
    except ElvinReadingError as exc:
        if getattr(args, "json", False):
            emit({"ok": False, "error": str(exc)}, True)
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
