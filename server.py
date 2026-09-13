"""
smelt — universal file ingestion MCP server (P1)

Conversion backends (in priority order for quality="accurate"):
  1. marker-pdf   — layout-aware, table-preserving (optional, install separately)
  2. markitdown   — pdfminer-based, good for most text PDFs
  3. tesseract    — OCR fallback for image-only PDFs (requires pytesseract + pymupdf)

Tiers:
  direct  — <20k chars  → content inline
  cache   — 20k–100k   → content inline + cache_recommended=true
  chunk   — >100k chars → 40k-char pieces, fetch via smelt_chunk()
  native  — raw images  → pass directly to Claude

Store: ~/smelt-store/chunks.db (SQLite), 6-month TTL, clearance dir on expiry.
Dedup: SHA-256 on file bytes — same file never re-processed.
"""

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from markitdown import MarkItDown

mcp = FastMCP("smelt")
_md = MarkItDown()

# ── store ───────────────────────────────────────────────────────────────────
STORE_DIR    = Path.home() / "smelt-store"
CLEARANCE    = STORE_DIR / "clearance"
DB_PATH      = STORE_DIR / "chunks.db"
TTL          = 6 * 30 * 24 * 3600  # 6 months in seconds

# ── thresholds ───────────────────────────────────────────────────────────────
CHUNK_SIZE        = 40_000
CACHE_THRESHOLD   = 20_000
CHUNK_THRESHOLD   = 100_000
IMAGE_EXTS        = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


# ── db ───────────────────────────────────────────────────────────────────────
def _init_db() -> sqlite3.Connection:
    STORE_DIR.mkdir(exist_ok=True)
    CLEARANCE.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute("""
        CREATE TABLE IF NOT EXISTS files (
            file_id       TEXT PRIMARY KEY,
            path          TEXT,
            sha256        TEXT UNIQUE,
            doc_type      TEXT,
            tier          TEXT,
            total_chunks  INTEGER,
            total_chars   INTEGER,
            backend       TEXT,
            quality       TEXT,
            created_at    REAL,
            last_accessed REAL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            file_id     TEXT,
            chunk_index INTEGER,
            content     TEXT,
            PRIMARY KEY (file_id, chunk_index)
        )
    """)
    con.commit()
    return con


_db: Optional[sqlite3.Connection] = None

def _db_get() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = _init_db()
    return _db


# ── helpers ──────────────────────────────────────────────────────────────────
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _tier(n: int) -> str:
    if n >= CHUNK_THRESHOLD: return "chunk"
    if n >= CACHE_THRESHOLD: return "cache"
    return "direct"


def _is_image_pdf(path: str) -> bool:
    """True when PDF has no extractable text layer (needs OCR)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        for page in doc:
            if page.get_text().strip():
                return False
        return True
    except ImportError:
        return False


def _ocr_pdf(path: str) -> str:
    """Tesseract OCR — page-by-page, 300 dpi."""
    import fitz
    import pytesseract
    from PIL import Image as PILImage
    doc = fitz.open(path)
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=300)
        img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
        pages.append(pytesseract.image_to_string(img))
    return "\n\n".join(pages)


def _marker_convert(path: str) -> str:
    """marker-pdf high-fidelity conversion (optional dep)."""
    # ponytail: lazy import + global model cache; marker loads ~2GB of models on first call
    global _marker_models
    from marker.converters.pdf import PdfConverter  # type: ignore
    from marker.models import create_model_dict     # type: ignore
    if "_marker_models" not in globals() or _marker_models is None:
        _marker_models = create_model_dict()
    converter = PdfConverter(artifact_dict=_marker_models)
    rendered = converter(path)
    return rendered.markdown


def _convert(path: str, quality: str) -> tuple[str, str]:
    """
    Returns (markdown, backend_name).
    quality: "fast" | "accurate"
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".pdf":
        if _is_image_pdf(path):
            try:
                return _ocr_pdf(path), "tesseract"
            except Exception as e:
                return f"[smelt OCR error: {e}]", "tesseract_error"

        if quality == "accurate":
            try:
                return _marker_convert(path), "marker"
            except (ImportError, Exception):
                pass  # fall through to markitdown

    return _md.convert(path).markdown, "markitdown"


# ── tools ────────────────────────────────────────────────────────────────────
@mcp.tool()
def smelt_file(path: str, quality: str = "fast", doc_type: str = "") -> str:
    """
    Convert a file to Markdown and return a tiered reference.

    Tiers:
      direct  — small (<20k chars) → content inline
      cache   — medium (20k–100k) → content inline + cache_recommended=true
      chunk   — large (>100k)     → chunked; fetch pieces with smelt_chunk()
      native  — raw image         → pass directly to Claude

    Args:
        path:      local file path or URL (YouTube, HTML, etc.)
        quality:   "fast" (markitdown, default) | "accurate" (marker-pdf, tables-heavy docs)
        doc_type:  optional label e.g. "bank_statement", "cc_statement", "research_paper"
    """
    ext = os.path.splitext(path)[1].lower()

    if ext in IMAGE_EXTS:
        return json.dumps({
            "tier": "native",
            "path": path,
            "message": f"Image ({ext}). Upload directly to Claude — markitdown adds no value.",
        })

    # dedup check
    try:
        sha = _sha256(path)
    except Exception:
        sha = hashlib.md5(os.path.abspath(path).encode()).hexdigest()

    db  = _db_get()
    now = time.time()
    row = db.execute(
        "SELECT file_id, tier, total_chunks, total_chars, backend FROM files WHERE sha256=?",
        (sha,)
    ).fetchone()

    if row:
        fid, tier, total_chunks, total_chars, backend = row
        db.execute("UPDATE files SET last_accessed=? WHERE file_id=?", (now, fid))
        db.commit()
        result = {
            "file_id": fid,
            "tier": tier,
            "total_chars": total_chars,
            "backend": backend,
            "cache_hit": True,
        }
        if tier == "chunk":
            result["total_chunks"] = total_chunks
            result["message"] = (
                f"Cached. {total_chunks} chunks. "
                f"Call smelt_chunk(file_id='{fid}', chunk_index=N) for N in 0..{total_chunks-1}."
            )
        else:
            # re-fetch content from db
            chunk = db.execute(
                "SELECT content FROM chunks WHERE file_id=? AND chunk_index=0", (fid,)
            ).fetchone()
            result["content"] = chunk[0] if chunk else ""
            result["cache_recommended"] = tier == "cache"
        return json.dumps(result)

    # convert
    try:
        text, backend = _convert(path, quality)
    except Exception as e:
        return json.dumps({"error": str(e)})

    tier   = _tier(len(text))
    fid    = sha[:16]
    chunks = [text[i : i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]

    db.execute(
        """INSERT OR REPLACE INTO files
           (file_id, path, sha256, doc_type, tier, total_chunks, total_chars,
            backend, quality, created_at, last_accessed)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (fid, path, sha, doc_type or None, tier, len(chunks), len(text),
         backend, quality, now, now)
    )
    for i, c in enumerate(chunks):
        db.execute(
            "INSERT OR REPLACE INTO chunks (file_id, chunk_index, content) VALUES (?,?,?)",
            (fid, i, c)
        )
    db.commit()

    result: dict = {
        "file_id": fid,
        "tier": tier,
        "total_chars": len(text),
        "backend": backend,
        "cache_hit": False,
    }
    if doc_type:
        result["doc_type"] = doc_type

    if tier == "chunk":
        result["total_chunks"] = len(chunks)
        result["message"] = (
            f"Chunked into {len(chunks)} parts ({len(text):,} chars). "
            f"Call smelt_chunk(file_id='{fid}', chunk_index=N) for N in 0..{len(chunks)-1}."
        )
    else:
        result["content"] = text
        result["cache_recommended"] = tier == "cache"

    return json.dumps(result)


@mcp.tool()
def smelt_chunk(file_id: str, chunk_index: int) -> str:
    """
    Fetch one chunk from a previously smelted large file.

    Args:
        file_id:     returned by smelt_file()
        chunk_index: 0-based (see total_chunks in smelt_file response)
    """
    db = _db_get()
    meta = db.execute(
        "SELECT total_chunks FROM files WHERE file_id=?", (file_id,)
    ).fetchone()

    if not meta:
        return json.dumps({"error": f"No file for file_id '{file_id}'. Run smelt_file() first."})

    total = meta[0]
    if not 0 <= chunk_index < total:
        return json.dumps({"error": f"chunk_index {chunk_index} out of range 0–{total-1}."})

    row = db.execute(
        "SELECT content FROM chunks WHERE file_id=? AND chunk_index=?",
        (file_id, chunk_index)
    ).fetchone()

    db.execute("UPDATE files SET last_accessed=? WHERE file_id=?", (time.time(), file_id))
    db.commit()

    return json.dumps({
        "file_id": file_id,
        "chunk_index": chunk_index,
        "total_chunks": total,
        "content": row[0] if row else "",
    })


@mcp.tool()
def smelt_status() -> str:
    """List all files in the smelt store with TTL info."""
    db  = _db_get()
    now = time.time()
    rows = db.execute(
        "SELECT file_id, path, tier, total_chars, backend, last_accessed FROM files ORDER BY last_accessed DESC"
    ).fetchall()

    files = []
    for fid, path, tier, chars, backend, last in rows:
        age_days   = int((now - last) / 86400)
        ttl_days   = int(TTL / 86400)
        expires_in = ttl_days - age_days
        files.append({
            "file_id":    fid,
            "path":       path,
            "tier":       tier,
            "total_chars": chars,
            "backend":    backend,
            "age_days":   age_days,
            "expires_in_days": max(0, expires_in),
            "expired":    expires_in <= 0,
        })

    return json.dumps({"files": files, "count": len(files)})


@mcp.tool()
def smelt_expire() -> str:
    """
    Move files older than 6 months to ~/smelt-store/clearance/.
    Call periodically or on low-disk events.
    """
    db    = _db_get()
    now   = time.time()
    cutoff = now - TTL
    rows  = db.execute(
        "SELECT file_id, path FROM files WHERE last_accessed < ?", (cutoff,)
    ).fetchall()

    moved = []
    for fid, path in rows:
        db.execute("DELETE FROM chunks WHERE file_id=?", (fid,))
        db.execute("DELETE FROM files WHERE file_id=?", (fid,))
        # write clearance receipt
        receipt = CLEARANCE / f"{fid}.json"
        receipt.write_text(json.dumps({"file_id": fid, "path": path, "expired_at": now}))
        moved.append(fid)

    db.commit()
    return json.dumps({"expired": moved, "count": len(moved)})


if __name__ == "__main__":
    mcp.run()
