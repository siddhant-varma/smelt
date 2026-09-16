#!/usr/bin/env bash
# smelt Acer (ASR) setup — run once after Ubuntu Server flash
# Assumes: Ubuntu 24.04, user=sid, Python 3.13 available

set -euo pipefail

echo "=== smelt Acer setup ==="

# 1. System deps
sudo apt-get update -q
sudo apt-get install -y python3.13 python3.13-venv python3-pip tesseract-ocr git

# 2. Clone
cd ~
git clone https://github.com/siddhant-varma/smelt.git
cd smelt

# 3. Venv + deps
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Store dir
mkdir -p ~/smelt-store/clearance

# 5. Smoke test
.venv/bin/python -c "from mcp.server.fastmcp import FastMCP; print('MCP OK')"

# 6. systemd service
sudo cp deploy/smelt.service /etc/systemd/system/smelt.service
sudo systemctl daemon-reload
sudo systemctl enable smelt
sudo systemctl start smelt

echo "=== done — smelt running on :8765 ==="
echo "Update mcp.json on MacBook+Mini to point at $(hostname -I | awk '{print $1}'):8765"
