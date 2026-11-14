"""SQLite persistence for quotes, orders, approvals and the audit log (Valio Aimo side)."""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
  quote_id TEXT PRIMARY KEY, session TEXT, buyer_id TEXT, data TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY, session TEXT, buyer_id TEXT, quote_id TEXT, status TEXT NOT NULL,
  data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS intents (
  intent_id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mandates (
  buyer_id TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (buyer_id, version));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, session TEXT, order_id TEXT,
  actor TEXT NOT NULL, action TEXT NOT NULL, summary TEXT NOT NULL, reason TEXT, data TEXT);
"""


# Allowed state changes. Anything else is a bug and raises.
INTENT_FLOW = {"proposed": {"confirmed", "superseded", "expired"},
               "confirmed": {"fulfilled", "expired"},   # stays confirmed while some items are still open
               "fulfilled": {"confirmed"},              # only when a human rejects an order, items reopen
               "superseded": set(), "expired": set()}
ORDER_FLOW = {"pending_approval": {"confirmed", "rejected", "quote_expired"},
              "confirmed": set(), "rejected": set(), "quote_expired": set()}


class TransitionError(ValueError):
    pass


def check_transition(flow: dict, old: str, new: str) -> None:
    if new != old and new not in flow.get(old, set()):
        raise TransitionError(f"illegal transition {old} -> {new}")


def now() -> datetime:
    return datetime.now(ZoneInfo("Europe/Helsinki"))


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # ---- quotes
    def save_quote(self, quote_id: str, session: str | None, buyer_id: str, data: dict, expires_at: str) -> None:
        self._exec("INSERT INTO quotes VALUES (?,?,?,?,?,?)",
                   (quote_id, session, buyer_id, json.dumps(data), iso(), expires_at))

    def get_quote(self, quote_id: str) -> dict | None:
        row = self._exec("SELECT * FROM quotes WHERE quote_id=?", (quote_id,)).fetchone()
        return dict(row) | {"data": json.loads(row["data"])} if row else None

    # ---- mandates (append-only, one row per version)
    def latest_mandate(self, buyer_id: str) -> dict | None:
        row = self._exec("SELECT data FROM mandates WHERE buyer_id=? ORDER BY version DESC LIMIT 1",
                         (buyer_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def mandate_version(self, buyer_id: str, version: int) -> dict | None:
        row = self._exec("SELECT data FROM mandates WHERE buyer_id=? AND version=?", (buyer_id, version)).fetchone()
        return json.loads(row["data"]) if row else None

    def mandate_history(self, buyer_id: str) -> list[dict]:
        rows = self._exec("SELECT data FROM mandates WHERE buyer_id=? ORDER BY version DESC", (buyer_id,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_mandate(self, m: dict) -> None:
        self._exec("INSERT INTO mandates VALUES (?,?,?,?)", (m["buyer_id"], m["version"], json.dumps(m), iso()))

    # ---- intents
    def save_intent(self, intent: dict) -> None:
        self._exec("INSERT INTO intents VALUES (?,?,?,?)",
                   (intent["intent_id"], intent["status"], json.dumps(intent), iso()))

    def get_intent(self, intent_id: str) -> dict | None:
        row = self._exec("SELECT data FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def update_intent(self, intent: dict) -> None:
        old = self.get_intent(intent["intent_id"])
        check_transition(INTENT_FLOW, old["status"], intent["status"])
        self._exec("UPDATE intents SET status=?, data=?, updated_at=? WHERE intent_id=?",
                   (intent["status"], json.dumps(intent), iso(), intent["intent_id"]))

    # ---- orders
    def save_order(self, order: dict, session: str | None, buyer_id: str) -> None:
        self._exec("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                   (order["order_id"], session, buyer_id, order["quote_id"], order["status"],
                    json.dumps(order), iso(), iso()))

    def update_order(self, order: dict) -> None:
        old = self.get_order(order["order_id"])
        check_transition(ORDER_FLOW, old["status"], order["status"])
        self._exec("UPDATE orders SET status=?, data=?, updated_at=? WHERE order_id=?",
                   (order["status"], json.dumps(order), iso(), order["order_id"]))

    def get_order(self, order_id: str) -> dict | None:
        row = self._exec("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        return json.loads(row["data"]) | {"_session": row["session"]} if row else None

    def list_orders(self, buyer_id: str | None, limit: int = 20, before: str | None = None) -> list[dict]:
        """Newest first. `before`: an order_id or ISO timestamp; only orders created before it."""
        sql, args = "SELECT * FROM orders WHERE 1=1", []
        if buyer_id:
            sql += " AND buyer_id=?"
            args.append(buyer_id)
        if before:
            ref = self._exec("SELECT created_at, rowid FROM orders WHERE order_id=?", (before,)).fetchone()
            if ref:
                sql += " AND rowid < ?"
                args.append(ref["rowid"])
            else:
                sql += " AND created_at < ?"
                args.append(before)
        sql += " ORDER BY rowid DESC LIMIT ?"
        args.append(int(limit))
        rows = self._exec(sql, tuple(args)).fetchall()
        return [json.loads(r["data"]) | {"_session": r["session"], "_created_at": r["created_at"]} for r in rows]

    # ---- approvals
    def save_approval(self, appr: dict) -> None:
        self._exec("INSERT INTO approvals VALUES (?,?,?,?)",
                   (appr["approval_id"], appr["order_id"], appr["status"], json.dumps(appr)))

    def update_approval(self, appr: dict) -> None:
        self._exec("UPDATE approvals SET status=?, data=? WHERE approval_id=?",
                   (appr["status"], json.dumps(appr), appr["approval_id"]))

    def get_approval(self, approval_id: str) -> dict | None:
        row = self._exec("SELECT data FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_approvals(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._exec("SELECT data FROM approvals WHERE status=? ORDER BY rowid DESC", (status,))
        else:
            rows = self._exec("SELECT data FROM approvals ORDER BY rowid DESC")
        return [json.loads(r["data"]) for r in rows.fetchall()]

    # ---- audit
    def audit(self, actor: str, action: str, summary: str, *, session: str | None = None,
              order_id: str | None = None, reason: str | None = None, data: dict | None = None) -> None:
        self._exec("INSERT INTO audit (ts, session, order_id, actor, action, summary, reason, data) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (iso(), session, order_id, actor, action, summary, reason, json.dumps(data or {})))

    def audit_for_order(self, order_id: str) -> list[dict]:
        """Everything tied to the order, plus the agent's earlier steps in the same session."""
        order = self.get_order(order_id)
        session = order.get("_session") if order else None
        intent_id = (order or {}).get("intent_id")
        rows = self._exec(
            "SELECT * FROM audit WHERE order_id=? OR (? IS NOT NULL AND json_extract(data, '$.intent_id')=?) "
            "OR (session IS NOT NULL AND session=? AND order_id IS NULL "
            "AND id <= (SELECT MAX(id) FROM audit WHERE order_id=?)) ORDER BY id",
            (order_id, intent_id, intent_id, session, order_id)).fetchall()
        return [{"timestamp": r["ts"], "actor": r["actor"], "action": r["action"], "summary": r["summary"],
                 "reason": r["reason"], "data": json.loads(r["data"] or "{}")} for r in rows]


_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(config.DB_PATH)
    return _store


def jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))
