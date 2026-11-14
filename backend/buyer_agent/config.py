"""Buyer-side settings. Read from .env at the repo root."""
from __future__ import annotations

import os
import socket
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

# 0.0.0.0 so a phone on the same Wi-Fi can open the pairing page. Set 127.0.0.1 to keep it laptop-only.
BACKEND_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")
MCP_BASE_URL = MCP_SERVER_URL.rsplit("/mcp", 1)[0]
APPROVER_TOKEN = os.getenv("APPROVER_TOKEN", "dev-approver-token")
DEFAULT_BUYER_ID = os.getenv("DEFAULT_BUYER_ID", "juvenes")
DATA_DIR = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
KEYS_DIR = Path(os.getenv("KEYS_DIR", DATA_DIR / "keys"))

# anthropic | openai | gemini | openai_compatible | scripted
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MAX_STEPS = int(os.getenv("LLM_MAX_STEPS", "40"))  # recipes need 2 steps per ingredient
# Anthropic only: show the model's (summarized) reasoning. Effort: low | medium | high
LLM_THINKING = os.getenv("LLM_THINKING", "on").lower() not in ("0", "off", "false", "no")
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")
TIMEZONE = os.getenv("BUYER_TIMEZONE", "Europe/Helsinki")


def _lan_ip() -> str:
    """This machine's address on the local network (no packet is sent). Falls back to localhost."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "localhost"


# Phone pairing: the phone opens {PUBLIC_BASE_URL}/phone?pair=... "localhost" would point at the phone itself,
# so the default is the laptop's LAN address. Override for a tunnel or a fixed host, e.g. http://192.168.1.20:8000
# On Render, RENDER_EXTERNAL_URL is set automatically to the service's https URL.
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
                   or f"http://{_lan_ip()}:{BACKEND_PORT}").rstrip("/")
# Built React UI (npm run build in frontend/). Served at / when present.
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", REPO_ROOT / "frontend" / "dist"))
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", DATA_DIR / "uploads"))
KITCHEN_DIR = Path(os.getenv("KITCHEN_DIR", DATA_DIR / "kitchen"))
FIXTURES_DIR = REPO_ROOT / "contracts" / "fixtures"
# Clean orders needed before the next autonomy level is offered
AUTONOMY_CLEAN_ORDERS = int(os.getenv("AUTONOMY_CLEAN_ORDERS", "10"))
