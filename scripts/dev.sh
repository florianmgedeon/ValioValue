#!/usr/bin/env bash
# Starts mcp-server (8001) and backend (8000). Ctrl+C stops both.
# FAKE_VALIO=1 also starts the offline fake Valio API on 8099.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
PY=.venv/bin/python
trap 'kill 0' EXIT
[ "${FAKE_VALIO:-0}" = "1" ] && $PY mcp-server/dev/fake_valio_api.py &
(cd mcp-server && ../$PY -m valio_mcp.server) &
sleep 2
(cd backend && ../$PY -m buyer_agent.app) &
wait
