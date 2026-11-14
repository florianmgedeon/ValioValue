#!/usr/bin/env bash
# Container entrypoint: Valio Aimo side (mcp-server) and buyer backend in one container.
# The mcp-server only listens on 127.0.0.1 inside the container; only the backend is public.
#   VALIO_API_KEY set   -> real Valio Aimo API, full catalog downloaded on start
#   VALIO_API_KEY empty -> offline fake Valio API with the sample catalog
set -euo pipefail
cd "$(dirname "$0")/.."

export BACKEND_HOST=0.0.0.0
export BACKEND_PORT="${PORT:-${BACKEND_PORT:-8000}}"   # Render sets PORT
export MCP_HOST=127.0.0.1 MCP_PORT="${MCP_PORT:-8001}"
export MCP_SERVER_URL="http://127.0.0.1:$MCP_PORT/mcp"
export APPROVER_TOKEN="${APPROVER_TOKEN:-$(python -c 'import secrets;print(secrets.token_hex(16))')}"
export LLM_PROVIDER="${LLM_PROVIDER:-scripted}"

trap 'kill 0' EXIT

if [ -z "${VALIO_API_KEY:-}" ]; then
  echo "VALIO_API_KEY not set: using the offline fake Valio API"
  export VALIO_API_BASE=http://127.0.0.1:8099 VALIO_API_KEY=offline
  python mcp-server/dev/fake_valio_api.py &
elif [ ! -f data/raw/products.json ]; then
  echo "Downloading the product catalog"
  python - <<'PY' || echo "Catalog download failed, falling back to the sample catalog"
import os, httpx
base = os.getenv("VALIO_API_BASE", "https://junction26.valioaimo.fi").rstrip("/")
os.makedirs("data/raw", exist_ok=True)
# The API redirects to the file (about 90 MB); stream it to disk instead of holding it in memory.
with httpx.stream("GET", f"{base}/api/products/download", headers={"X-API-Key": os.environ["VALIO_API_KEY"]},
                  follow_redirects=True, timeout=120) as r:
    r.raise_for_status()
    with open("data/raw/products.json.part", "wb") as f:
        for chunk in r.iter_bytes():
            f.write(chunk)
os.replace("data/raw/products.json.part", "data/raw/products.json")
print(os.path.getsize("data/raw/products.json") // 1_000_000, "MB catalog")
PY
fi

(cd mcp-server && python -m valio_mcp.server) &
MCP_PID=$!

python - <<'PY'
import os, socket, time
for _ in range(60):
    try:
        socket.create_connection(("127.0.0.1", int(os.environ["MCP_PORT"])), 1).close(); break
    except OSError:
        time.sleep(0.5)
PY
sleep 1
kill -0 "$MCP_PID" 2>/dev/null || { echo "mcp-server did not start"; exit 1; }

# exec: the backend becomes PID 1 and receives SIGTERM directly (Render redeploys, docker stop).
# The mcp-server stops with the container.
trap - EXIT
cd backend && exec python -m buyer_agent.app
