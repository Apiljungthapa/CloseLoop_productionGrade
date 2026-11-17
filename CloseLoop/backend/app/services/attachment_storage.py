"""Attachment file storage — files live on disk under UPLOAD_DIR (a
Docker volume in prod, see docker-compose.yml), one sub-directory per
tenant so nothing can be listed/guessed across tenants. Every stored
file is named with a fresh UUID (never the caller-supplied filename),
so the only thing derived from user input is the extension — no path
traversal surface. The DB `Attachment.storage_path` row is the sole
mapping back to a human-readable filename.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from ..config import settings

UPLOAD_DIR = Path(settings.UPLOAD_DIR)

MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024  # 20 MB

_UNSAFE_FILENAME_CHARS = re.compile(r'[\r\n"\\\x00-\x1f]')


def sanitize_filename(filename: str) -> str:
    """Strip characters that would let a filename break out of an HTTP
    Content-Disposition header (quotes, CR/LF, control chars) before it
    ever reaches the DB or a response header."""
    name = (filename or "file").strip()
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    return name[:300] or "file"

# Deliberately conservative — office docs, spreadsheets, PDFs, images,
# plain text/CSV, and zip archives. No executables/scripts.
ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".csv",
    ".ppt",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".txt",
    ".zip",
    ".msg",
    ".eml",
}


def validate_extension(filename: str) -> str:
    """Return the lower-cased extension, raising ValueError if it's not
    on the allowlist. Only the extension is trusted from `filename` —
    it never becomes part of the on-disk path."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type '{ext or '(none)'}' is not allowed. Allowed types: "
            + ", ".join(sorted(ALLOWED_EXTENSIONS))
        )
    return ext


async def read_upload_capped(
    file: UploadFile, cap: int = MAX_ATTACHMENT_SIZE
) -> bytes:
    """Read an UploadFile into memory in 1 MB chunks, aborting as soon as
    the running total exceeds `cap`. This bounds memory to ~`cap` bytes and
    rejects an oversized upload after ~1 MB instead of buffering the whole
    (possibly multi-GB) body first. Raises ValueError on an empty or
    over-cap upload — routers translate that to HTTP 400."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise ValueError(
                f"File too large (max {cap // (1024 * 1024)} MB)"
            )
        chunks.append(chunk)
    if total == 0:
        raise ValueError("Uploaded file is empty")
    return b"".join(chunks)


def _save_sync(tenant_id: str, original_filename: str, content: bytes) -> str:
    ext = validate_extension(original_filename)
    tenant_dir = UPLOAD_DIR / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4()}{ext}"
    (tenant_dir / stored_name).write_bytes(content)
    return f"{tenant_id}/{stored_name}"


def _read_sync(storage_path: str) -> bytes:
    return (UPLOAD_DIR / storage_path).read_bytes()


def _delete_sync(storage_path: str) -> None:
    try:
        (UPLOAD_DIR / storage_path).unlink(missing_ok=True)
    except OSError:
        pass


async def save(tenant_id: str, original_filename: str, content: bytes) -> str:
    """Persist `content` under UPLOAD_DIR/<tenant_id>/<uuid><ext> and
    return the relative storage_path to store on the Attachment row.
    Blocking disk I/O runs in a worker thread so the event loop stays free."""
    return await run_in_threadpool(
        _save_sync, tenant_id, original_filename, content
    )


async def read(storage_path: str) -> bytes:
    return await run_in_threadpool(_read_sync, storage_path)


async def delete(storage_path: str) -> None:
    await run_in_threadpool(_delete_sync, storage_path)
