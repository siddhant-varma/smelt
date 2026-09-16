# smelt P2 — HTTP Server Execution Plan

**Goal:** Add `--transport sse` mode so smelt runs as an SSE MCP server over HTTP.  
Mac Mini Claude registers it via URL in `mcp.json`. MacBook hosts during UAT; Acer is final prod.

**Exit criteria:**
- `python server.py --transport sse` starts on MacBook, binds Tailscale IP, port 8765
- Mac Mini `mcp.json` points at `http://100.102.102.100:8765/sse`
- Mac Mini Claude calls `smelt_file` on a MacBook-local path and gets a result
- UAT test: drop a file from Mac Mini Claude → smelt converts on MacBook → chunk returned
- `launchd` plist keeps smelt running on MacBook across reboots (until Acer is ready)

---

## Dependency Graph

```
T1 (CLI args) → T2 (SSE mode) → T3 (launchd) → T4 (Mini mcp.json) → T5 (UAT)
                              → T3a (firewall)
```

---

## Session 1 — MacBook HTTP mode (~2h)

### Tasks

| ID  | Task | Time | Files | Deps | Exit Criteria |
|-----|------|------|-------|------|---------------|
| T1  | Add `--transport` CLI arg (`stdio` default, `sse` for HTTP) | 15m | `server.py` | — | `python server.py --transport sse` starts without error |
| T2  | Wire SSE transport: `FastMCP(host=..., port=8765)` + `mcp.run(transport="sse")` | 20m | `server.py` | T1 | Server starts, uvicorn listening on 0.0.0.0:8765 |
| T3a | macOS firewall: allow port 8765 inbound on Tailscale interface | 10m | — (shell) | T2 | `nc -zv 100.102.102.100 8765` from Mac Mini succeeds |
| T3  | `launchd` plist: `com.smelt.server.plist` in `~/Library/LaunchAgents/` | 30m | `deploy/com.smelt.server.plist` | T2 | `launchctl list | grep smelt` shows running; survives logout |
| T4  | Mac Mini `~/.claude/mcp.json`: add smelt SSE entry pointing at MacBook Tailscale IP | 10m | Mini's `mcp.json` | T3a | Claude Code on Mini shows smelt tools in `/mcp` |
| T5  | UAT: call `smelt_file` from Mac Mini Claude on a shared/path-accessible file | 20m | — | T4 | Response JSON with `file_id`, `tier`, `content` or chunks |

**Session 1 checkpoint:** Mac Mini Claude → smelt → MacBook SQLite → result back. End-to-end verified.

---

## Session 2 — Hardening + Acer prep (~1.5h, after Acer is flashed)

| ID  | Task | Time | Files | Deps | Exit Criteria |
|-----|------|------|-------|------|---------------|
| T6  | Acer: install Ubuntu Server, Python 3.13, git, tesseract, create `~/smelt-store/` | 45m | — (Acer shell) | Acer flashed | `python3.13 --version` on Acer |
| T7  | Acer: `git clone` smelt, `python3.13 -m venv .venv`, `pip install -r requirements.txt` | 20m | — | T6 | `smelt_status()` returns `{"files":[],"count":0}` on Acer |
| T8  | Acer: `systemd` service for smelt SSE (replace `launchd` plist as reference) | 20m | `deploy/smelt.service` | T7 | `systemctl status smelt` → active |
| T9  | Both machines: update `mcp.json` to point at Acer IP instead of MacBook | 10m | MacBook+Mini `mcp.json` | T8 | Both Claudes see smelt tools from Acer |
| T10 | MacBook: remove launchd plist, stop local smelt SSE | 5m | — | T9 | MacBook no longer runs smelt |

**Session 2 checkpoint:** Both Claudes hitting Acer. MacBook stdio entry removed. Single source of truth.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SSE transport DNS rebinding protection blocks Tailscale IP | Medium | High | Pass `transport_security=None` or set `host="0.0.0.0"` in FastMCP init — FastMCP only auto-enables rebinding protection for localhost |
| Mac Mini can't resolve MacBook Tailscale IP | Low | High | Test `ping 100.102.102.100` from Mini before T4; use IP not hostname |
| Shared file paths don't exist on MacBook when called from Mini | High | Medium | During UAT, use files already on MacBook (symlinks or shared NAS path); long-term Acer has its own local store |
| marker-pdf model download (~2GB) on first `quality="accurate"` call hangs | Medium | Low | Models already cached from P1 fix session; Acer will need first-run warmup |
| launchd plist working-dir wrong → SQLite at wrong path | Low | High | Hardcode `STORE_DIR` in plist env or assert path on startup |
| Acer Ubuntu flash timeline unknown | High (dependency) | Medium | Session 1 fully unblocked — UAT on MacBook↔Mini first, Acer is Session 2 |

---

## Integration Checkpoints

| After | Check |
|-------|-------|
| T2 | `curl http://localhost:8765/sse` returns SSE stream headers on MacBook |
| T3a | `nc -zv 100.102.102.100 8765` from Mac Mini succeeds |
| T4 | `/mcp` in Mac Mini Claude Code lists smelt + 4 tools |
| T5 | Full round-trip: Mini → smelt → result. **Session 1 gate.** |
| T9 | Both machines hit Acer, SQLite on Acer, Mini+MacBook verified. **Session 2 gate.** |

---

## Key Technical Notes

- **FastMCP 1.29.0** natively supports SSE — no FastAPI wrapper needed. Transport selected via `mcp.run(transport="sse")`. Host/port set in `FastMCP(host=..., port=8765)`.
- **`--transport` CLI arg** — set `host="0.0.0.0"` for SSE (binds all interfaces including Tailscale), `host="127.0.0.1"` for stdio (irrelevant but clean).
- **Shared SQLite** — file-based, local to the host machine. Mac Mini calls smelt but SQLite lives on MacBook (UAT) then Acer (prod). Paths in results will be host-local paths — that's fine.
- **Mac Mini `mcp.json` entry for SSE:**
  ```json
  "smelt": {
    "url": "http://100.102.102.100:8765/sse"
  }
  ```
- **`deploy/` directory** — add `com.smelt.server.plist` (macOS) and `smelt.service` (Linux systemd) here so Acer setup is one-step.
