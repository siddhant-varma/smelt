# smelt 🔥

Universal file ingestion MCP server for the shadowlabs Claude ecosystem.

Raw files go in. Lean Markdown comes out.

## What it does

Any Claude surface (Claude Code, Claude Chat, AI-on-UI, autonomous agents) routes large files through **smelt** before hitting the Claude API. Smelt converts them to Markdown, tiers by size, and returns a reference — not the raw content.

Saves tokens. Handles image PDFs via OCR. Lives on ASR (Acer Aspire V3-575).

## Tiers

| Tier | Size | Behaviour |
|------|------|-----------|
| `direct` | < 20k chars | content returned inline |
| `cache` | 20k–100k chars | content returned + `cache_recommended: true` |
| `chunk` | > 100k chars | split into 40k-char pieces; fetch via `smelt_chunk()` |
| `native` | images | pass raw to Claude (markitdown adds no value) |

## Tools

- `smelt_file(path)` — ingest any file or URL
- `smelt_chunk(file_id, chunk_index)` — fetch one chunk of a large file
- `smelt_status()` — list all files currently in store

## Supported formats

PDF, DOCX, PPTX, XLSX, HTML, CSV, ZIP, audio, YouTube URLs — via [markitdown](https://github.com/microsoft/markitdown).  
Image-based PDFs → auto-detected via PyMuPDF → Tesseract OCR (P1).

## Roadmap

| Phase | Status | What |
|-------|--------|------|
| P0 | ✅ Done | stdio, markitdown, 3-tier, MacBook local |
| P1 | 🔜 Next | Tesseract OCR + auto-detect, SQLite store, 6-month TTL |
| P2 | 🔒 Blocked | HTTP server on ASR, Tailscale mesh, all-device access |
| P3 | 📋 Planned | Files API upload, prompt cache hints, SHA-256 dedup |
| P4 | 📋 Planned | Silent auto-intercept hook on all Claude surfaces |
| P5 | 📋 Planned | Semantic chunking, section index, two-model pipeline |
| P6 | 📋 Planned | Cost tracker, audit log, TG alerts, clearance cron |

P2 blocked on: flash Ubuntu Server on ASR.

## Setup

```bash
pip install -r requirements.txt
```

Register in `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "smelt": {
      "command": "/opt/homebrew/bin/python3",
      "args": ["/Users/siddhantvarma/code/smelt/server.py"]
    }
  }
}
```

## Production host

**Acer Aspire V3-575 (ASR)** — i7-6500U, Ubuntu Server (pending flash).  
All devices reach it via Tailscale mesh once P2 ships.
