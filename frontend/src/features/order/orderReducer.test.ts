import { describe, expect, it } from "vitest";
import { fold, initialState, mergeFunnel, reduce, type Block, type OrderEvent, type OrderState } from "./orderReducer";

/** Recorded streams, one JSON event per line (contracts/fixtures/streams/README.md). */
const RAW = import.meta.glob<string>("../../../../contracts/fixtures/streams/*.jsonl", { query: "?raw", import: "default", eager: true });
const STREAMS = Object.fromEntries(Object.entries(RAW).map(([path, text]) => [path.split("/").pop()!, text]));
const load = (name: string): OrderEvent[] =>
  (STREAMS[name] ?? "").split("\n").filter(Boolean).map((l) => JSON.parse(l) as OrderEvent);

const kinds = (s: OrderState) => s.blocks.map((b) => b.kind);
const one = <K extends Block["kind"]>(s: OrderState, kind: K) => {
  const found = s.blocks.filter((b): b is Extract<Block, { kind: K }> => b.kind === kind);
  expect(found).toHaveLength(1);
  return found[0]!;
};

describe("every recorded stream", () => {
  const files = Object.keys(STREAMS);

  it("has the four scenarios", () => {
    expect(files.sort()).toEqual(["jailbreak-blocked.jsonl", "milk-auto.jsonl", "over-limit.jsonl", "usual-granola-blocked.jsonl"]);
  });

  for (const f of files) {
    it(`${f}: a replay after reconnect gives the same state`, () => {
      const events = load(f);
      const once = fold(events);
      expect(fold(events, once)).toEqual(once);
      // Reconnect mid-way: the stream starts again from the first event.
      const half = fold(events.slice(0, Math.floor(events.length / 2)));
      expect(fold(events, half)).toEqual(once);
    });

    it(`${f}: block keys are unique`, () => {
      const keys = fold(load(f)).blocks.map((b) => b.key);
      expect(new Set(keys).size).toBe(keys.length);
    });
  }
});

describe("milk only: ordered alone", () => {
  const s = fold(load("milk-auto.jsonl"));

  it("asks, reads back, searches, confirms", () => {
    expect(kinds(s)).toEqual(expect.arrayContaining(["me", "readback", "funnel", "receipt"]));
    expect(kinds(s)).not.toContain("decision");
    expect(kinds(s)).not.toContain("approve");
    expect(kinds(s)).not.toContain("block");
  });

  it("signs the read-back and ends done", () => {
    expect(one(s, "readback").signed).toBe(true);
    expect(s.live.step).toBe("done");
    expect(s.live.now).toBe("done");
    expect(s.live.note).toBe("ordered alone");
    expect(s.live.typing).toBe(false);
  });

  it("receipt carries tier and the grant that allowed it", () => {
    const r = one(s, "receipt");
    expect(r.tier).toBe("auto");
    expect(r.receipt?.grants_used).toEqual(["g1"]);
    expect(r.total).toBeGreaterThan(0);
  });

  it("the funnel stops checking once Valio answered", () => {
    expect(one(s, "funnel").checking).toBe(false);
  });

  it("before the signature, the panel waits for the buyer", () => {
    const events = load("milk-auto.jsonl");
    const upto = events.findIndex((e) => e.type === "intent_confirmed");
    const before = fold(events.slice(0, upto));
    expect(before.live.step).toBe("understood");
    expect(before.live.now).toBe("wait");
    expect(one(before, "readback").signed).toBe(false);
  });
});

describe("usual order, granola blocked", () => {
  const events = load("usual-granola-blocked.jsonl");
  const s = fold(events);

  it("merges every item into one funnel and keeps the blocked usual product", () => {
    const f = one(s, "funnel");
    expect(Object.keys(f.items)).toHaveLength(6);
    expect(f.usualExcluded).toEqual([expect.objectContaining({ item_id: "i5", name: "Start Naturell granola 750g", rule_id: "m2" })]);
    const rows = mergeFunnel(f.items);
    expect(rows[0]).toMatchObject({ label: "Looked at in the catalog", why: "6 lines" });
    expect(rows.filter((r) => r.kind === "must").map((r) => r.label)).toEqual([
      "Your must: no peanuts incl. 'may contain'",
      "Your must: no tree nuts incl. 'may contain'",
    ]);
    const catalog = Object.values(f.items).reduce((n, steps) => n + (steps.find((x) => x.step === "catalog")?.count ?? 0), 0);
    expect(rows[0]!.count).toBe(catalog);
  });

  it("shows a decision card for the changed line, then a receipt", () => {
    const d = one(s, "decision");
    expect(d.checks.map((c) => c.item_id)).toContain("i5");
    expect(d.decided).toBe("approve");
    expect(one(s, "receipt").tier).toBe("ask");
  });

  it("while undecided, the panel says a decision waits", () => {
    const upto = events.findIndex((e) => e.type === "order_confirmed");
    const before = fold(events.slice(0, upto));
    expect(before.live.now).toBe("wait");
    expect(before.live.waiting).toBe(1);
    expect(before.live.note).toBe("1 decision waits for you");
  });

  it("a local decision clears the wait before the stream confirms", () => {
    const upto = events.findIndex((e) => e.type === "order_confirmed");
    const before = fold(events.slice(0, upto));
    const d = one(before, "decision");
    const after = reduce(before, { type: "local/decided", approvalId: d.approval.approval_id, decision: "approve" });
    expect(after.live.waiting).toBe(0);
    expect(after.live.now).toBeNull();
  });
});

describe("items left open", () => {
  const s = fold(load("usual-granola-blocked.jsonl"));

  it("the receipt lists the open items", () => {
    expect(one(s, "receipt").openItems).toEqual(["i3", "i6"]);
  });

  it("knows why each one is open", () => {
    const f = one(s, "funnel");
    expect(f.issues.i3).toMatchObject({ kind: "missing" });
    expect(f.issues.i6).toMatchObject({ kind: "missing" });
    // Granola: the usual one broke a must, another one could be ordered.
    expect(f.issues.i5).toBeUndefined();
    expect(f.issues.i1).toBeUndefined();
  });

  it("an item whose products all arrive late is a delivery issue, one whose products all break a must a rules issue", () => {
    const base = { session_id: "s", timestamp: "2026-11-14T09:00:00+02:00", item_id: "i1", product_query: "milk" };
    const late = fold([{ type: "offers", ...base, offers: [{ can_fulfil: false, fulfil_issues: ["order cut-off for 2026-11-16 has passed"] } as never], funnel: [{ step: "catalog", count: 5 }] }]);
    expect(one(late, "funnel").issues.i1).toEqual({ kind: "delivery", reason: "order cut-off for 2026-11-16 has passed" });
    const banned = fold([{ type: "offers", ...base, offers: [], excluded: [{ name: "Granola", reason: "may contain peanuts" }], funnel: [{ step: "catalog", count: 9 }] }]);
    expect(one(banned, "funnel").issues.i1).toEqual({ kind: "rules", reason: "may contain peanuts" });
  });

  it("the agent's answer after the receipt is marked as a recap, earlier ones are not, also after a replay", () => {
    const events = load("usual-granola-blocked.jsonl");
    const twice = fold(events, s);
    const says = twice.blocks.filter((b): b is Extract<Block, { kind: "say" }> => b.kind === "say");
    expect(says.at(-1)!.recap).toBe(true);
    expect(says[0]!.recap).toBe(false);
  });
});

describe("cart over the limit", () => {
  const s = fold(load("over-limit.jsonl"));

  it("shows the approve card, not a decision", () => {
    expect(kinds(s)).toContain("approve");
    expect(kinds(s)).not.toContain("decision");
    expect(one(s, "approve").approval.total_price).toBeGreaterThan(300);
  });

  it("ends with a receipt signed by the buyer", () => {
    const r = one(s, "receipt");
    expect(r.tier).toBe("ask");
    expect(r.approvedBy).toBe("Anna Virtanen");
  });
});

describe("jailbreak: Valio Aimo blocks", () => {
  const s = fold(load("jailbreak-blocked.jsonl"));

  it("shows the block card with the rule and its evidence", () => {
    const b = one(s, "block");
    expect(b.checks).toHaveLength(1);
    expect(b.checks[0]!.evidence?.id).toBe("m2");
    expect(b.checks[0]!.evidence?.signals?.[0]?.value).toBe("MAY_CONTAIN");
  });

  it("the panel says blocked, no receipt", () => {
    expect(s.live.now).toBe("block");
    expect(kinds(s)).not.toContain("receipt");
  });
});

describe("other events", () => {
  const base = { session_id: "s", timestamp: "2026-11-14T09:00:00+02:00" };

  it("a photo message becomes a photo block", () => {
    const s = fold([{ type: "user_message", ...base, text: "", attachments: [{ upload_id: "up_1", url: "/uploads/up_1", media_type: "image/jpeg" }] }]);
    expect(one(s, "photo").attachments[0]!.upload_id).toBe("up_1");
  });

  it("an error is shown once, even when replayed", () => {
    const e: OrderEvent = { type: "error", ...base, message: "boom", recoverable: true };
    expect(kinds(fold([e, e]))).toEqual(["error"]);
  });

  it("context suggestions become learn cards and keep a local answer through a replay", () => {
    const e: OrderEvent = { type: "context_suggestion", ...base, reason: "after_order", suggestions: [{ id: "sg9", kind: "usual", status: "proposed", title: '"granola" means oat muesli' }] };
    let s = fold([e]);
    s = reduce(s, { type: "local/learned", suggestionId: "sg9", done: "remembered" });
    s = fold([e], s);
    expect(one(s, "learn").done).toBe("remembered");
  });

  it("reset clears everything", () => {
    const s = fold(load("milk-auto.jsonl"));
    expect(reduce(s, { type: "local/reset" })).toEqual(initialState);
  });
});
