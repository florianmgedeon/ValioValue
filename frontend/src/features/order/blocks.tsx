import { useState, type ReactNode } from "react";
import { decideApproval } from "../../api/approvals";
import { ApiError, apiUrl } from "../../api/client";
import { confirmIntent } from "../../api/intents";
import { acceptKitchenSuggestions, rejectKitchenSuggestion } from "../../api/kitchen";
import type { Approval, OrderIntent, Schema } from "../../api/types";
import { SIGNER_NAME } from "../../app/queries";
import { navigate } from "../../app/route";
import { useSignedChange } from "../../app/signing";
import { Button, Card, CardFooter, CardHeader, Chip, Cond, Icon, KindTag, Typing, eur, useToast, type Kind } from "../../ui";
import { fmtDay, unitLabel, useKitchenContext } from "./data";
import { lineDeviations, mergeFunnel, type Block, type ItemIssue, type LocalAction, type UsualExcluded } from "./orderReducer";

type Check = ReturnType<typeof lineDeviations>[number];
type BlockOf<K extends Block["kind"]> = Extract<Block, { kind: K }>;

export interface BlockCtx {
  local: (a: LocalAction) => void;
  retry: () => void;
  /** Put text into the composer for the buyer to finish. */
  prefill: (text: string) => void;
  /** Send a message on the buyer's behalf (a one-click answer to a question the agent asked). */
  send: (text: string) => void;
  /** item_id -> why nothing could be ordered for it */
  issues: Record<string, ItemIssue>;
  /** The read-back of this order, for names and queries in later cards. */
  intent: OrderIntent | null;
  usualExcluded: UsualExcluded[];
  mandateId: string | null;
}

function Agent({ children }: { children: ReactNode }) {
  return (
    <div className="agent">
      <div className="agent-dot"><Icon name="spark" /></div>
      <div className="agent-body">{children}</div>
    </div>
  );
}

/** Inline markdown: **bold** and `code`. */
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*|`[^`]+`)/).map((p, i) =>
        p.startsWith("**") && p.endsWith("**") ? <b key={i}>{p.slice(2, -2)}</b>
        : p.startsWith("`") && p.endsWith("`") && p.length > 1 ? <span key={i} className="font-mono text-[0.92em]">{p.slice(1, -1)}</span>
        : p,
      )}
    </>
  );
}

/** Agent text: paragraphs, "- " lists, bold, code. Enough for what the agent writes; no full markdown. */
function Rich({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) out.push(<ul key={out.length} className="say-list">{list.map((l, i) => <li key={i}><Inline text={l} /></li>)}</ul>);
    list = [];
  };
  for (const line of text.split("\n")) {
    const item = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
    if (item) list.push(item[1]!);
    else {
      flush();
      if (line.trim()) out.push(<p key={out.length}><Inline text={line} /></p>);
    }
  }
  flush();
  return <div className="say">{out}</div>;
}

/** The agent's recap after a receipt repeats the cards. Show its first sentence, the rest on request. */
function Recap({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const first = /^[^\n]*?[.!?](?=\s|$)/.exec(text.replace(/\*\*/g, ""))?.[0] ?? text.split("\n")[0]!;
  if (open || first.length >= text.replace(/\*\*/g, "").trim().length) return <Rich text={text} />;
  return (
    <div className="say">
      <p className="text-ink2">{first} <button className="link text-[13px]" onClick={() => setOpen(true)}>Show my full answer</button></p>
    </div>
  );
}

function errText(e: unknown) {
  if (e instanceof ApiError && typeof e.detail === "object" && e.detail && "error" in e.detail) return String((e.detail as { error: unknown }).error);
  return (e as Error).message;
}

const queryOf = (intent: OrderIntent | null, itemId: string | null | undefined) =>
  intent?.items.find((i) => i.item_id === itemId)?.product?.query ?? itemId ?? "this line";

// ---------------------------------------------------------------- me, photo, say, typing

function Me({ b }: { b: BlockOf<"me"> }) {
  return <div className="msg-me rise"><div className="bubble">{b.text}</div></div>;
}

function Photo({ b }: { b: BlockOf<"photo"> }) {
  return (
    <div className="msg-me rise">
      {b.attachments.map((a, i) => (
        <div key={a.upload_id ?? i} className="photo-msg">
          {a.media_type === "application/pdf" ? (
            <div className="pdf"><Icon name="doc" />{a.filename ?? "PDF"}</div>
          ) : (
            <img src={apiUrl(a.url ?? `/uploads/${a.upload_id}`)} alt={a.filename ?? "Your photo"} />
          )}
        </div>
      ))}
      {b.text && <div className="bubble">{b.text}</div>}
    </div>
  );
}

export function TypingBlock() {
  return <Agent><Typing /></Agent>;
}

// ---------------------------------------------------------------- read-back

function Readback({ b, ctx }: { b: BlockOf<"readback">; ctx: BlockCtx }) {
  const toast = useToast();
  const k = useKitchenContext();
  const [busy, setBusy] = useState(false);
  const it = b.intent;
  const mandateRules = it.hard_rules.filter((r) => r.source === "mandate" || /^m\d/.test(r.id ?? ""));
  const requestRules = it.hard_rules.filter((r) => !mandateRules.includes(r));
  const anyUsual = it.items.some((i) => i.usual?.name);
  const anyNew = it.items.some((i) => !i.usual?.name);
  const questions = it.questions ?? [];
  // Said once for the whole list, not on every line.
  const slacks = it.items.map((i) => i.quantity?.max_over_pct ?? 0);
  const sameSlack = slacks.every((x) => x === slacks[0]) ? slacks[0] ?? 0 : null;
  const hint = [
    anyNew ? (anyUsual ? "New items: I search the catalog after you confirm." : "I search the catalog after you confirm.") : "",
    sameSlack ? `Rounding up to whole packs, at most +${sameSlack} %.` : "",
  ].filter(Boolean).join(" ");

  const sign = async () => {
    if (!it.intent_hash) return;
    setBusy(true);
    try {
      await confirmIntent(it.intent_id, { intent_hash: it.intent_hash, confirmed_by: SIGNER_NAME });
      ctx.local({ type: "local/signed", intentId: it.intent_id });
    } catch (e) {
      toast(`Not sent: ${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };

  /**
   * Open questions block a plain confirm (Valio answers 409). "Confirm as is" is the buyer's answer: revise the
   * intent unchanged (that drops the agent's own questions, Valio's own checks stay), then sign the new hash.
   * If Valio still has a question, the new read-back shows it and nothing is signed.
   */
  const confirmAsIs = async () => {
    if (!it.intent_hash) return;
    setBusy(true);
    try {
      const next = await confirmIntent(it.intent_id, { intent_hash: it.intent_hash, edits: { preferences: {} } });
      if ((next.questions ?? []).length || !next.intent_hash) return;
      await confirmIntent(next.intent_id, { intent_hash: next.intent_hash, confirmed_by: SIGNER_NAME });
      ctx.local({ type: "local/signed", intentId: it.intent_id });
      ctx.local({ type: "local/signed", intentId: next.intent_id });
    } catch (e) {
      toast(`Not sent: ${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
    <Card className="rise">
      <CardHeader title="Here's what I understood">
        <span className="text-[13px] text-ink3">from your {it.items.some((i) => i.note === "from your photo") ? "photo" : "message"}</span>
      </CardHeader>
      {it.items.map((i) => {
        const subs = i.substitutes ?? [];
        const lockedSubs = i.product?.allow_other_substitutes === false;
        return (
          <div key={i.item_id} className="order-item">
            <div className="min-w-0">
              <div className="ask">
                <span className="q">{i.quantity?.amount} {unitLabel(i.quantity?.unit)}</span>
                <span className="n">{i.product?.query}</span>
              </div>
              {i.usual?.name && <div className="nm">Your usual: {i.usual.name}</div>}
              {subs.length > 0 && (
                <div className="nm">
                  If out of stock, {lockedSubs ? "only" : "first"}: {subs.map((s, idx) => `#${idx + 1} ${s.name ?? s.product_id}`).join(" → ")}
                </div>
              )}
              {subs.length === 0 && lockedSubs && <div className="nm">No substitutes allowed if out of stock</div>}
              {sameSlack === null && i.quantity?.max_over_pct ? <div className="nm">Rounding up to whole packs, at most +{i.quantity.max_over_pct} %</div> : null}
            </div>
            {((i.hard_rules ?? []).length > 0 || lockedSubs) && (
              <div className="chips">
                {subs.length > 0 && lockedSubs && <Chip kind="usual" icon="lock">Only approved substitutes ({subs.length})</Chip>}
                {subs.length === 0 && lockedSubs && <Chip kind="must" icon="lock">No substitutes allowed</Chip>}
                {(i.hard_rules ?? []).map((r, n) => <Chip key={n} kind="must" icon="check">{r.label ?? `${r.attr} ${r.op} ${String(r.value)}`}</Chip>)}
              </div>
            )}
          </div>
        );
      })}
      {hint && <div className="items-hint">{hint}</div>}
      <div className="paramchips">
        <span className="pchip">Deliver by <b>{fmtDay(it.deliver_by)}</b></span>
        {it.preferences?.optimise && <span className="pchip">Then <b>{it.preferences.optimise === "price" ? "cheapest" : it.preferences.optimise === "co2" ? "lowest CO₂" : "most reliable"}</b></span>}
        {requestRules.map((r, n) => <span key={n} className="pchip">Only <b>{r.label ?? r.attr}</b></span>)}
      </div>
      {(mandateRules.length > 0 || k.prefers.length > 0 || anyUsual) && (
        <div className="applied">
          Applied from your kitchen:
          {mandateRules.map((r) => <Chip key={r.id ?? r.label} kind="must" icon="lock">{r.label ?? r.id}</Chip>)}
          {k.prefers.map((p) => <Chip key={p.id ?? p.label} kind="prefer">{p.label ?? p.id}</Chip>)}
          {anyUsual && <Chip kind="usual">Your usual products</Chip>}
        </div>
      )}
      {(it.notes ?? []).length > 0 && <div className="applied">{it.notes!.join(" · ")}</div>}
      <CardFooter>
        <div className="fore">
          {questions.length ? (
            <>Answer my question in the chat, or confirm as it is.</>
          ) : (
            <>Valio Aimo checks every line against your rules. Anything outside them comes back to you.</>
          )}
        </div>
        {b.signed ? (
          <Chip kind="prefer" icon="check">Sent</Chip>
        ) : (
          <Button variant="primary" icon="lock" disabled={busy || !it.intent_hash} onClick={() => void (questions.length ? confirmAsIs() : sign())}>
            {busy ? "Sending…" : "Confirm & send to Valio Aimo"}
          </Button>
        )}
      </CardFooter>
    </Card>
    {/* The question itself follows as the agent's chat message, so it reads as "answer me in the chat". */}
    </>
  );
}

// ---------------------------------------------------------------- funnel

function Funnel({ b }: { b: BlockOf<"funnel"> }) {
  const rows = mergeFunnel(b.items);
  const top = Math.max(1, rows[0]?.count ?? 1);
  return (
    <Card className="rise">
      <CardHeader title="How I narrowed it down"><span className="text-[13px] text-ink3">Valio Aimo catalog · live price and stock</span></CardHeader>
      <div className="funnel">
        {rows.map((r) => (
          <div key={r.key} className={`frow ${r.kind ? `f-${r.kind}` : ""}`}>
            <div>
              <div>{r.label} {r.why && <span className="why">· {r.why}</span>}</div>
              <div className="bar"><i style={{ width: `${Math.max(2, Math.round((r.count / top) * 100))}%` }} /></div>
            </div>
            <div className="cnt">{r.count.toLocaleString("en-GB")}</div>
          </div>
        ))}
      </div>
      {b.usualExcluded.length > 0 && (
        <div className="applied">
          {b.usualExcluded.map((u) => (
            <Chip key={u.item_id} kind="must" icon="flag">Your usual {u.query ?? ""} broke {u.rule_id ?? "a must"}</Chip>
          ))}
        </div>
      )}
      {b.checking && (
        <CardFooter>
          <div className="fore flex items-center gap-2"><Typing />Valio Aimo is checking every line against your mandate…</div>
        </CardFooter>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- decision and approve

function useDecide(approval: Approval, local: BlockCtx["local"]) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const decide = async (decision: "approve" | "reject") => {
    setBusy(true);
    try {
      await decideApproval(approval.approval_id, { decision, decided_by: SIGNER_NAME, cart_hash: decision === "approve" ? approval.cart_hash : undefined });
      local({ type: "local/decided", approvalId: approval.approval_id, decision });
    } catch (e) {
      toast(`Not sent: ${errText(e)}`);
    } finally {
      setBusy(false);
    }
  };
  return { busy, decide };
}

function checkTitle(c: Check, ctx: BlockCtx): { title: string; text: string; usual?: UsualExcluded } {
  const q = queryOf(ctx.intent, c.item_id);
  const usual = ctx.usualExcluded.find((u) => u.item_id === c.item_id);
  if ((c.field === "usual" || c.field === "product") && usual) {
    return {
      title: `Your usual ${q} is blocked by your rules.`,
      text: `${usual.name ?? "It"}: ${usual.reason ?? "breaks a must"}. Valio Aimo would block it. I picked ${c.cart} instead. The other lines are fine.`,
      usual,
    };
  }
  if (c.field === "usual") return { title: `${cap(q)}: not your usual product.`, text: `Your usual is ${c.intended}. I found ${c.cart}.` };
  return { title: `${cap(q)}: ${c.detail ?? c.field}.`, text: `You asked for ${c.intended ?? "—"}. The cart has ${c.cart ?? "—"}.` };
}
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function Decision({ b, n, ctx }: { b: BlockOf<"decision">; n: number; ctx: BlockCtx }) {
  const { busy, decide } = useDecide(b.approval, ctx.local);
  const [pick, setPick] = useState<"approve" | "reject">("approve");
  const first = b.checks[0]!;
  const head = checkTitle(first, ctx);
  const lines = b.approval.lines.filter((l) => b.checks.some((c) => c.item_id === l.item_id));
  const done = b.decided;
  const choice = done ?? pick;
  const ruleId = head.usual?.rule_id;
  const opts = [
    {
      v: "approve" as const,
      title: lines.length ? lines.map((l) => `${l.name} × ${l.quantity}`).join(", ") : "Take what I found",
      text: b.checks.length > 1 ? `Accept ${b.checks.length} changes and send the whole cart (${eur(b.approval.total_price)}).` : `Send the cart with this line (${eur(b.approval.total_price)} in total).`,
      price: lines.reduce((s, l) => s + l.total_price, 0),
      chips: lines.flatMap((l) => (l.usual ? [] : [["about", "New for you"] as [Kind, string]])),
    },
    { v: "reject" as const, title: "Not this time", text: "I order nothing. Tell me what to change and I start again.", price: null, chips: [] },
  ];
  return (
    <Card variant="decision" className="rise">
      <CardHeader title={<span className="flex items-center gap-2.5"><span className="dnum">{n}</span>A decision for you</span>}>
        <span className="flex-1" />
        {ruleId ? <Chip kind="must" icon="lock">Your rule</Chip> : <Chip kind="auto" icon="lock">Needs your OK</Chip>}
      </CardHeader>
      <div className="grid gap-1.5 px-5 pt-4">
        <b className="text-base">{head.title}</b>
        <p className="text-sm text-ink3">{head.text}</p>
        {b.checks.slice(1).map((c, i) => <p key={i} className="text-sm text-ink3">Also: {checkTitle(c, ctx).title}</p>)}
      </div>
      <div className="opts" role="radiogroup" aria-label="What should I do?">
        {opts.map((o) => (
          <button key={o.v} type="button" className="opt" role="radio" aria-checked={choice === o.v} disabled={!!done} onClick={() => setPick(o.v)}>
            <span className="radio" />
            <span>
              <b>{o.title}</b>
              <span className="text-[13px] text-ink3">{o.text}</span>
              {o.chips.length > 0 && <span className="mt-1.5 flex flex-wrap gap-1.5">{o.chips.map(([k, l], i) => <Chip key={i} kind={k}>{l}</Chip>)}</span>}
            </span>
            <span className="px">{o.price !== null ? eur(o.price) : "—"}</span>
          </button>
        ))}
      </div>
      <CardFooter>
        <div className="fore">Valio Aimo checks the cart again before it goes out.</div>
        {done ? (
          <Chip kind="prefer" icon="check">{done === "approve" ? "Decided" : "Rejected"}</Chip>
        ) : (
          <Button variant="primary" icon="lock" disabled={busy} onClick={() => void decide(pick)}>{busy ? "Sending…" : "Confirm"}</Button>
        )}
      </CardFooter>
    </Card>
  );
}

function Approve({ b, n, ctx }: { b: BlockOf<"approve">; n: number; ctx: BlockCtx }) {
  const { busy, decide } = useDecide(b.approval, ctx.local);
  const k = useKitchenContext();
  const total = b.approval.total_price;
  const over = k.limit !== null ? total - k.limit : null;
  return (
    <Card variant="decision" className="rise">
      <CardHeader title={<span className="flex items-center gap-2.5"><span className="dnum">{n}</span>Above your limit</span>}>
        <span className="flex-1" />
        {k.limit !== null && <Chip kind="auto" icon="lock">Your limit · {eur(k.limit)}</Chip>}
      </CardHeader>
      <div className="grid gap-1.5 px-5 py-4">
        <b className="text-base">This cart is {eur(total)}{over !== null && over > 0 ? `, ${eur(over)} more than I may order alone` : ""}.</b>
        <p className="text-sm text-ink3">Every line passes your rules. Only the total needs your OK. If anything changes, I ask you again.</p>
      </div>
      <CardFooter>
        <div className="fore">Want this to go through alone next time? Raise the limit in your kitchen profile.</div>
        {b.decided ? (
          <Chip kind="prefer" icon="check">{b.decided === "approve" ? "Approved" : "Rejected"}</Chip>
        ) : (
          <>
            <Button small disabled={busy} onClick={() => void decide("reject")}>Reject</Button>
            <Button variant="primary" icon="lock" disabled={busy} onClick={() => void decide("approve")}>{busy ? "Sending…" : `Approve ${eur(total)}`}</Button>
          </>
        )}
      </CardFooter>
    </Card>
  );
}

// ---------------------------------------------------------------- blocked

function Blocked({ b, ctx }: { b: BlockOf<"block">; ctx: BlockCtx }) {
  const c = b.checks[0];
  const ev = c?.evidence;
  const usual = ctx.usualExcluded.find((u) => u.item_id === c?.item_id);

  return (
    <Card variant="block" className="rise">
      <CardHeader title={<span className="flex items-center gap-2"><Icon name="ban" />Blocked by Valio Aimo</span>}>
      </CardHeader>
      <div className="evidence">
        <p className="text-[15px]">I passed your request on, but I can't override this. It's one of your fixed rules, and Valio Aimo checks it on every line, no matter what I'm told in chat.</p>
        <dl>
          {c && <><dt>Product</dt><dd>{usual?.name ?? productOf(b, c.item_id) ?? queryOf(ctx.intent, c.item_id)}</dd></>}
          {c && <><dt>Rule</dt><dd><Chip kind="must" icon="lock">{ev?.rule ?? c.intended ?? "Your rule"}</Chip></dd></>}
          {c && (
            <>
              <dt>Evidence</dt>
              <dd className="flex flex-wrap gap-1.5">
                {(ev?.signals ?? []).filter((s) => s.says !== "safe").map((s, i) => (
                  <span key={i}>{s.source?.toUpperCase()} {s.field}: <Cond>{s.value}</Cond></span>
                ))}
                {!ev?.signals?.length && <span>{c.detail}</span>}
              </dd>
            </>
          )}
          {!c && b.message && <><dt>Why</dt><dd>{b.message}</dd></>}
          <dt>Who decided</dt><dd>Valio Aimo, using your rules</dd>
          <dt>To change it</dt><dd>Change the rule in your kitchen profile. Not in chat.</dd>
        </dl>
      </div>
      <CardFooter>
        <div className="fore">No setting lets me order this anyway. Only changing the rule does.</div>
        <Button small variant="primary" onClick={() => navigate("kitchen")}>Open kitchen profile</Button>
      </CardFooter>
    </Card>
  );
}

function productOf(b: BlockOf<"block">, itemId: string | null | undefined) {
  return b.checks.find((x) => x.item_id === itemId && x.field === "product")?.cart;
}

// ---------------------------------------------------------------- receipt

function Receipt({ b, ctx }: { b: BlockOf<"receipt">; ctx: BlockCtx }) {
  const k = useKitchenContext();
  const deviations = lineDeviations(b.diff);
  const n = b.lines.length;
  const asked = n + b.openItems.length;
  const items = b.openItems.length ? `${n} of ${asked} items` : `${n} item${n === 1 ? "" : "s"}`;
  // What it did, in one line, and why it was allowed to. Per line only what differs from that.
  const why =
    b.tier === "auto"
      ? `I ordered this on my own: every line passed your rules${k.limit ? ` and the total is inside your ${eur(k.limit)} limit` : ""}.`
      : deviations.length
        ? "Ordered after you decided on the changes below."
        : "Ordered after you approved the cart. Every line passed your rules.";
  return (
    <Card variant="receipt" className="rise">
      <div className="receipt-h">
        <span className="check"><Icon name="check" /></span>
        <div className="min-w-0">
          <h3>Ordered · {eur(b.total)}</h3>
          <div className="sub">{items}{b.deliveryDate ? ` · delivery ${fmtDay(b.deliveryDate)}` : ""}</div>
        </div>
      </div>
      <p className="receipt-why">{why}</p>
      {b.lines.map((l, i) => {
        const dev = b.tier === "auto" ? undefined : deviations.find((d) => d.item_id === l.item_id);
        return (
          <div key={l.quote_id ?? `${l.product_id}-${i}`} className="rline">
            <span className="nm">{l.name} × {l.quantity}{l.sales_unit ? ` ${l.sales_unit.toLowerCase()}` : ""}</span>
            <span className="px">{eur(l.total_price)}</span>
            {l.approved_substitute && (
              <span className="why">
                <Chip kind="usual">Fallback #{l.substitute_rank ?? 1}</Chip>{" "}
                {l.substitute_reason ?? "your usual was out of stock, ordered your approved substitute"}
              </span>
            )}
            {dev && <span className="why">You decided: {dev.detail ?? "a change you approved"}</span>}
          </div>
        );
      })}
      <div className="rtotal"><span>Total</span><span className="num">{eur(b.total)}</span></div>
      {b.openItems.length > 0 && <StillOpen ids={b.openItems} ctx={ctx} />}
      <div className="proof">
        <span>Order {b.orderId} · prices locked by Valio Aimo</span>
        <span className="flex-1" />
        <button className="link" onClick={() => navigate("activity", b.orderId)}>Why, line by line</button>
      </div>
    </Card>
  );
}

/** Items of the intent that this order does not contain: one plain question, one button per answer. */
function StillOpen({ ids, ctx }: { ids: string[]; ctx: BlockCtx }) {
  const [asked, setAsked] = useState(false);
  const rows = ids.map((id) => {
    const item = ctx.intent?.items.find((i) => i.item_id === id);
    const issue = ctx.issues[id] ?? { kind: "delivery" as const, reason: "Not ordered yet." };
    const what = item ? `${item.quantity?.amount ?? ""} ${unitLabel(item.quantity?.unit)} ${item.product?.query ?? id}`.trim() : id;
    return { id, query: item?.product?.query ?? id, what, issue };
  });
  const late = rows.filter((r) => r.issue.kind === "delivery");
  const rules = rows.filter((r) => r.issue.kind !== "delivery");
  const answer = (text: string) => {
    setAsked(true);
    ctx.send(text);
  };
  const question =
    rules.length && late.length ? "What should I do?"
    : rules.length ? `Should I look for another ${rules.length === 1 ? rules[0]!.query : "product"} that passes your rules?`
    : `Should I order ${late.length === 1 ? "it" : "them"} for the next possible delivery date?`;
  return (
    <div className="still-open">
      <div className="still-h"><Icon name="flag" size={15} />Not ordered yet</div>
      {rows.map((r) => (
        <div key={r.id} className="open-row">
          <b>{cap(r.what)}</b>
          <span className="text-ink3"> · {cap(r.issue.reason)}</span>
        </div>
      ))}
      <div className="still-q">{question}</div>
      <div className="open-acts">
        {rules.length > 0 && (
          <Button small variant="primary" disabled={asked} onClick={() => answer(`Find another ${rules.map((r) => r.query).join(" and ")} that passes my rules`)}>
            {late.length ? `Find another ${rules.length === 1 ? rules[0]!.query : "product"}` : "Yes, find another"}
          </Button>
        )}
        {late.length > 0 && (
          <Button small variant={rules.length ? undefined : "primary"} disabled={asked} onClick={() => answer(`Order ${late.map((r) => r.what).join(", ")} for the next possible delivery date`)}>
            {rules.length ? `Deliver ${late.length === 1 ? late[0]!.query : `${late.length} items`} later` : "Yes, deliver later"}
          </Button>
        )}
        <Button small disabled={asked} onClick={() => setAsked(true)}>No, leave it</Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- learn (2a)

const KIND_OF: Record<string, Kind> = { about: "about", usual: "usual", must: "must", prefer: "prefer", autonomy: "auto" };

function Learn({ b, ctx }: { b: BlockOf<"learn">; ctx: BlockCtx }) {
  const toast = useToast();
  const sign = useSignedChange();
  const [busy, setBusy] = useState(false);
  const s = b.suggestion as Schema["Suggestion"];
  if (b.done) {
    return (
      <div className="learn done rise">
        <div className="bulb"><Icon name="check" /></div>
        <div><b>{b.done === "remembered" ? `Remembered: ${s.title}` : "Only this time. I won't remember it."}</b></div>
      </div>
    );
  }
  const remember = async () => {
    setBusy(true);
    try {
      const out = await sign({ title: s.title ?? "Remember", call: (mandate_hash) => acceptKitchenSuggestions({ ids: [s.id], by: SIGNER_NAME, mandate_hash }) });
      if (out !== null) ctx.local({ type: "local/learned", suggestionId: s.id!, done: "remembered" });
    } catch (e) {
      toast(errText(e));
    } finally {
      setBusy(false);
    }
  };
  const once = async () => {
    setBusy(true);
    try {
      await rejectKitchenSuggestion(s.id!, { by: SIGNER_NAME });
      ctx.local({ type: "local/learned", suggestionId: s.id!, done: "once" });
    } catch (e) {
      toast(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="learn rise">
      <div className="bulb"><Icon name="bulb" /></div>
      <div className="grid gap-1">
        <span className="flex items-center gap-2"><KindTag kind={KIND_OF[s.kind ?? "about"] ?? "about"} /><b>{s.title}</b></span>
        {s.quote && <p>“{s.quote}”</p>}
      </div>
      <div className="acts">
        <Button small variant="primary" disabled={busy} onClick={() => void remember()}>Remember</Button>
        <Button small disabled={busy} onClick={() => void once()}>Just this time</Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- error

function ErrorCard({ b, ctx }: { b: BlockOf<"error">; ctx: BlockCtx }) {
  return (
    <Card variant="block" className="rise">
      <CardHeader title={<span className="flex items-center gap-2"><Icon name="flag" />Something went wrong</span>} />
      <p className="px-5 py-4 text-sm text-ink2">{b.message}</p>
      {b.recoverable && (
        <CardFooter>
          <div className="fore">Nothing was ordered. You can send your last message again.</div>
          <Button small onClick={ctx.retry}>Try again</Button>
        </CardFooter>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- dispatcher

export function BlockView({ b, n, ctx }: { b: Block; n: number; ctx: BlockCtx }) {
  switch (b.kind) {
    case "me": return <Me b={b} />;
    case "photo": return <Photo b={b} />;
    case "say": return <Agent>{b.recap ? <Recap text={b.text} /> : <Rich text={b.text} />}</Agent>;
    case "readback": return <Agent><Readback b={b} ctx={ctx} /></Agent>;
    case "funnel": return <Agent><Funnel b={b} /></Agent>;
    case "decision": return <Agent><Decision b={b} n={n} ctx={ctx} /></Agent>;
    case "approve": return <Agent><Approve b={b} n={n} ctx={ctx} /></Agent>;
    case "block": return <Agent><Blocked b={b} ctx={ctx} /></Agent>;
    case "receipt": return <Agent><Receipt b={b} ctx={ctx} /></Agent>;
    case "learn": return <Agent><Learn b={b} ctx={ctx} /></Agent>;
    case "error": return <Agent><ErrorCard b={b} ctx={ctx} /></Agent>;
  }
}
