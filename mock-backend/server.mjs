// Mock of the buyer backend for frontend development.
// Implements contracts/backend-api.yaml by replaying fixtures.
// No dependencies. Run: node mock-backend/server.mjs  (Node 18+)
import http from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const PORT = Number(process.env.PORT || 8000);
const DELAY_MS = Number(process.env.DELAY_MS || 700);
const fx = join(dirname(fileURLToPath(import.meta.url)), "..", "contracts", "fixtures");
const load = (f) => JSON.parse(readFileSync(join(fx, f), "utf8"));

const approvals = new Map(load("approvals.json").map((a) => [a.approval_id, a]));
const sessions = new Map(); // session_id -> { queue: [], clients: Set<res> }
let counter = 0;

function getSession(id) {
  if (!sessions.has(id)) sessions.set(id, { queue: [], clients: new Set() });
  return sessions.get(id);
}

function enqueue(sessionId, events) {
  const s = getSession(sessionId);
  events.forEach((e, i) => {
    setTimeout(() => {
      const evt = { ...e, session_id: sessionId, timestamp: new Date().toISOString() };
      s.queue.push(evt);
      for (const res of s.clients) send(res, evt);
    }, (i + 1) * DELAY_MS);
  });
}

function send(res, evt) {
  res.write(`event: ${evt.type}\ndata: ${JSON.stringify(evt)}\n\n`);
}

function json(res, code, body) {
  res.writeHead(code, { "Content-Type": "application/json", ...cors });
  res.end(JSON.stringify(body));
}

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
};

async function body(req) {
  let data = "";
  for await (const chunk of req) data += chunk;
  return data ? JSON.parse(data) : {};
}

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, `http://localhost:${PORT}`);
    const p = url.pathname;
    if (req.method === "OPTIONS") return res.writeHead(204, cors).end();

    if (p === "/health") return json(res, 200, { status: "ok", mock: true });

    if (p === "/chat" && req.method === "POST") {
      const b = await body(req);
      const sessionId = b.session_id || `sess_${++counter}`;
      enqueue(sessionId, load("scenario-milk-approval.json"));
      return json(res, 200, { session_id: sessionId });
    }

    let m = p.match(/^\/chat\/([^/]+)\/stream$/);
    if (m && req.method === "GET") {
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
        ...cors,
      });
      const s = getSession(m[1]);
      s.queue.forEach((e) => send(res, e)); // replay what already happened
      s.clients.add(res);
      req.on("close", () => s.clients.delete(res));
      return;
    }

    if (p === "/approvals" && req.method === "GET") {
      const status = url.searchParams.get("status");
      return json(res, 200, [...approvals.values()].filter((a) => !status || a.status === status));
    }

    m = p.match(/^\/approvals\/([^/]+)$/);
    if (m && req.method === "POST") {
      const a = approvals.get(m[1]);
      if (!a) return json(res, 404, { error: "not found" });
      const b = await body(req);
      Object.assign(a, {
        status: b.decision === "approve" ? "approved" : "rejected",
        decided_by: b.decided_by || "demo.user",
        decided_at: new Date().toISOString(),
        comment: b.comment || null,
      });
      // push follow-up events into every session that is listening
      if (a.status === "approved") {
        for (const id of sessions.keys()) enqueue(id, load("scenario-milk-after-approval.json"));
      }
      return json(res, 200, a);
    }

    m = p.match(/^\/orders\/([^/]+)\/audit$/);
    if (m && req.method === "GET") return json(res, 200, load("audit-ord_01.json"));

    json(res, 404, { error: "not found" });
  })
  .listen(PORT, () => console.log(`Mock backend on http://localhost:${PORT}`));
