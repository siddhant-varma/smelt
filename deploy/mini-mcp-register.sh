#!/usr/bin/env bash
# Run this ON Mac Mini to register smelt SSE endpoint.
# MacBook Tailscale IP: 100.102.102.100
# For Acer prod: replace IP with Acer's Tailscale IP.

set -euo pipefail

MCP_JSON="$HOME/.claude/mcp.json"
SMELT_URL="http://sid-air.local:8765/sse"  # mDNS; fallback: http://192.168.1.50:8765/sse

# Create if missing
if [ ! -f "$MCP_JSON" ]; then
  echo '{"mcpServers":{}}' > "$MCP_JSON"
fi

# Add smelt entry (requires python3 or jq)
python3 - <<EOF
import json, sys

path = "$MCP_JSON"
with open(path) as f:
    cfg = json.load(f)

cfg.setdefault("mcpServers", {})["smelt"] = {"url": "$SMELT_URL"}

with open(path, "w") as f:
    json.dump(cfg, f, indent=2)

print("smelt registered in", path)
print("URL:", "$SMELT_URL")
print("Restart Claude Code on Mini to pick up.")
EOF
