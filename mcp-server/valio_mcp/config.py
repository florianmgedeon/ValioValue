"""Settings for the Valio Aimo side. Everything comes from the environment (.env at repo root)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))

VALIO_API_BASE = os.getenv("VALIO_API_BASE", "https://junction26.valioaimo.fi").rstrip("/")
VALIO_API_KEY = os.getenv("VALIO_API_KEY", "")
VALIO_CUSTOMER = os.getenv("VALIO_CUSTOMER", "123")

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8001"))

# Shared secret for the human approval endpoints. Held by the buyer backend,
# never handed to the LLM, never exposed as an MCP tool.
APPROVER_TOKEN = os.getenv("APPROVER_TOKEN", "dev-approver-token")

DB_PATH = Path(os.getenv("MCP_DB_PATH", DATA_DIR / "valio_mcp.db"))
POLICY_FILE = Path(os.getenv("POLICY_FILE", REPO_ROOT / "mcp-server" / "policies.json"))

# How long a quote binds the price if the order cut-off is further away.
QUOTE_TTL_MINUTES = int(os.getenv("QUOTE_TTL_MINUTES", "30"))


def products_file() -> Path:
    """Real catalog if present, else the small dev sample."""
    explicit = os.getenv("PRODUCTS_FILE")
    if explicit:
        return Path(explicit)
    real = DATA_DIR / "raw" / "products.json"
    return real if real.exists() else DATA_DIR / "products.sample.json"
