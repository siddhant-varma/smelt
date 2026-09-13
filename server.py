"""
smelt — universal file ingestion MCP server
Converts any file to Markdown via markitdown, tiers by size, saves tokens.

Tiers:
  direct  — <20k chars  → return content inline
  cache   — 20k–100k   → return content + cache_recommended=true
  chunk   — >100k chars → split into ~40k char chunks, fetch via smelt_chunk()
  native  — raw images  → pass directly to Claude (markitdown adds no value)
"""

import hashlib
import json
import os

from mcp.server.fastmcp import FastMCP
from markitdown import MarkItDown

mcp = FastMCP("smelt")
_md = MarkItDown()

# ponytail: in-memory chunk store, resets on restart; replaced with SQLite in P1
_chunks: dict[str, list[str]] = {}

CHUNK_SIZE = 40_000       # ~10k tokens per chunk
CACHE_THRESHOLD = 20_000  # chars above which caching pays off
CHUNK_THRESHOLD = 100_000 # chars above which chunking is required

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _tier(char_count: int) -> str:
    if char_count >= CHUNK_THRESHOLD:
        return "chunk"
    if char_count >= CACHE_THRESHOLD:
        return "cache"
    return "direct"


def _fid(path: str) -> str:
    return hashlib.md5(os.path.abspath(path).encode()).hexdigest()[:10]


@mcp.tool()
def smelt_file(path: str) -> str:
    """
    Convert a file (PDF, DOCX, PPTX, XLSX, audio, YouTube URL, HTML, CSV, …)
    to Markdown using markitdown, then apply the appropriate tier:

    - direct : small file  → content returned inline
    - cache  : medium file → content returned + cache_recommended=true
    - chunk  : large file  → chunked; use smelt_chunk() to fetch pieces
    - native : raw image   → pass the file directly to Claude

    Args:
        path: local file path or URL (YouTube, web page, etc.)
    """
    ext = os.path.splitext(path)[1].lower()

    if ext in IMAGE_EXTS:
        return json.dumps({
            "tier": "native",
            "path": path,
            "message": f"Image type ({ext}). Upload directly to Claude — markitdown adds no value here.",
        })

    try:
        text = _md.convert(path).markdown
    except Exception as e:
        return json.dumps({"error": str(e)})

    tier = _tier(len(text))

    if tier == "chunk":
        fid = _fid(path)
        chunks = [text[i : i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
        _chunks[fid] = chunks
        return json.dumps({
            "tier": "chunk",
            "file_id": fid,
            "total_chunks": len(chunks),
            "char_count": len(text),
            "message": (
                f"File too large ({len(text):,} chars). "
                f"Chunked into {len(chunks)} parts. "
                f"Call smelt_chunk(file_id='{fid}', chunk_index=N) for N in 0..{len(chunks)-1}."
            ),
        })

    return json.dumps({
        "tier": tier,
        "char_count": len(text),
        "cache_recommended": tier == "cache",
        "content": text,
    })


@mcp.tool()
def smelt_chunk(file_id: str, chunk_index: int) -> str:
    """
    Fetch one chunk from a previously smelted large file.

    Args:
        file_id:     the file_id returned by smelt_file()
        chunk_index: 0-based index (see total_chunks in smelt_file response)
    """
    if file_id not in _chunks:
        return json.dumps({
            "error": f"No chunks for file_id '{file_id}'. Re-run smelt_file() first."
        })

    chunks = _chunks[file_id]
    if not 0 <= chunk_index < len(chunks):
        return json.dumps({
            "error": f"chunk_index {chunk_index} out of range. Valid: 0–{len(chunks) - 1}."
        })

    return json.dumps({
        "file_id": file_id,
        "chunk_index": chunk_index,
        "total_chunks": len(chunks),
        "content": chunks[chunk_index],
    })


@mcp.tool()
def smelt_status() -> str:
    """List all files currently held in the smelt store."""
    return json.dumps({
        fid: {"total_chunks": len(c), "total_chars": sum(len(x) for x in c)}
        for fid, c in _chunks.items()
    })


if __name__ == "__main__":
    mcp.run()
