"""Buyer-side settings. Read from .env at the repo root."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

BACKEND_HOST = os.getenv("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")
MCP_BASE_URL = MCP_SERVER_URL.rsplit("/mcp", 1)[0]
APPROVER_TOKEN = os.getenv("APPROVER_TOKEN", "dev-approver-token")
DEFAULT_BUYER_ID = os.getenv("DEFAULT_BUYER_ID", "hotel-chain-helsinki")

# anthropic | openai | gemini | openai_compatible | scripted
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MAX_STEPS = int(os.getenv("LLM_MAX_STEPS", "8"))
# Anthropic only: show the model's (summarized) reasoning. Effort: low | medium | high
LLM_THINKING = os.getenv("LLM_THINKING", "on").lower() not in ("0", "off", "false", "no")
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")
TIMEZONE = os.getenv("BUYER_TIMEZONE", "Europe/Helsinki")
