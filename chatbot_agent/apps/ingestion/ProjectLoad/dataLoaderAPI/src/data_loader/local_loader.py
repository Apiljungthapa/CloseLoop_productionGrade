"""Load a local file, detect its type, and wrap it as a DocumentSchema."""
import csv
import io
import json
import zipfile
from pathlib import Path

from loguru import logger

from data_loader.schema import DocumentSchema, create_metadata_model

# extension -> logical file format used by processors / chunkers
EXTENSION_FORMATS = {
    ".html": "html", ".htm": "html",
    ".xml": "xml",
    ".txt": "txt", ".md": "txt", ".log": "txt",
    ".json": "json",
    ".csv": "csv",
    ".pdf": "pdf",
    ".docx": "docx",
}

# Formats with a TextProcessor + section chunker; everything else is
# converted to plain text here and split by the recursive splitter.
STRUCTURED_FORMATS = ("html", "xml")

LocalMetadata = create_metadata_model(
    {"recordId": "str", "title": "str", "fileFormat": "str"}
)


def detect_file_format(path: Path) -> str:
    """Detect format by extension, falling back to sniffing the content."""
    fmt = EXTENSION_FORMATS.get(path.suffix.lower())
    if fmt:
        return fmt
    head = path.read_bytes()[:2048]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK") and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" in archive.namelist():
                return "docx"
    stripped = head.lstrip().lower()
    if stripped.startswith((b"<?xml", b"<healthrecord")):
        return "xml"
    if stripped.startswith((b"<!doctype html", b"<html")):
        return "html"
    if stripped[:1] in (b"{", b"["):
        return "json"
    return "txt"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("PDF support needs `pip install pypdf`") from error
    return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _read_docx(path: Path) -> str:
    """Paragraphs and tables in document order; headings become markdown headings."""
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as error:
        raise RuntimeError("DOCX support needs `pip install python-docx`") from error

    document = Document(str(path))
    parts = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = paragraph.style.name if paragraph.style is not None else ""
            if style == "Title":
                text = f"# {text}"
            elif style.startswith("Heading ") and style[8:].isdigit():
                text = f"{'#' * min(int(style[8:]) + 1, 6)} {text}"
            elif "List" in style:
                text = f"- {text}"
            parts.append(text)
        elif child.tag.endswith("}tbl"):
            rows = [
                [cell.text.strip().replace("\n", " ") for cell in row.cells]
                for row in Table(child, document).rows
            ]
            if rows:
                parts.append("\n".join(
                    ["| " + " | ".join(rows[0]) + " |", "|---" * len(rows[0]) + "|"]
                    + ["| " + " | ".join(row) + " |" for row in rows[1:]]
                ))
    return "\n\n".join(parts)


def _read_json(path: Path) -> str:
    return json.dumps(json.loads(_read_text(path)), indent=2, ensure_ascii=False)


def _read_csv(path: Path) -> str:
    """Render rows as a markdown table (same style as the XML data grids)."""
    rows = list(csv.reader(io.StringIO(_read_text(path))))
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |", "|---" * len(rows[0]) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


READERS = {
    "html": _read_text, "xml": _read_text, "txt": _read_text,
    "json": _read_json, "csv": _read_csv, "pdf": _read_pdf, "docx": _read_docx,
}


def load_local_document(file_path: str) -> DocumentSchema:
    """Read the file by its detected type and return a DocumentSchema."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    file_format = detect_file_format(path)
    logger.info(f"Detected file format '{file_format}' for {path.name}")
    text = READERS[file_format](path)

    document = DocumentSchema(
        id=path.stem,
        text=text,
        file_path=str(path),
        metadata=LocalMetadata(recordId=path.stem, title=path.stem, fileFormat=file_format),
    )
    if file_format not in STRUCTURED_FORMATS:
        # No TextProcessor for these: the extracted text is already clean
        document.processed_text = text
    return document
