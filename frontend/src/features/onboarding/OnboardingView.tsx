import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { apiUrl } from "../../api/client";
import { acceptKitchenSuggestions, ingestKitchenDocuments, rejectKitchenSuggestion, uploadFile } from "../../api/kitchen";
import type { IngestResult, Suggestion, UploadMeta } from "../../api/types";
import { usePhoneCamera } from "../../app/PhoneCamera";
import { keys, queryClient, SIGNER_NAME } from "../../app/queries";
import { navigate } from "../../app/route";
import { useSignedChange } from "../../app/signing";
import { Button, Icon, Mark, Paper, useToast } from "../../ui";
import { EXAMPLE_DOCS, exampleFile } from "./fixtures";
import { LearnCard, suggestionKind } from "./LearnCard";

type DocStatus = "uploading" | "ready" | "reading" | "read" | "error";
type Preview = { kind: "image"; src: string } | { kind: "pdf"; name: string } | { kind: "text"; text: string };

interface Doc {
  key: string;
  title: string;
  sub: string;
  preview: Preview;
  status: DocStatus;
  upload?: UploadMeta;
  text?: string;
  note?: string;
}

interface Learned {
  s: Suggestion;
  docKey: string;
  from: string;
}

/** Signed suggestion kinds in the backend: accepting any of them writes a new mandate version (W4). */
const MANDATE_KINDS = new Set(["must", "prefer", "autonomy"]);
const REVEAL_MS = 380;
const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));
let seq = 0;
const newKey = () => `d${++seq}`;

function previewOf(upload: UploadMeta, localSrc?: string): Preview {
  if (upload.media_type === "application/pdf") return { kind: "pdf", name: upload.filename ?? "document.pdf" };
  return { kind: "image", src: localSrc ?? apiUrl(upload.url ?? `/uploads/${upload.upload_id}`) };
}

function subOf(upload: UploadMeta): string {
  const what = upload.media_type === "application/pdf" ? "PDF" : "photo";
  if (upload.source === "phone") return `${what} · from your phone`;
  return `${what} · ${upload.filename ?? "upload"}`;
}

/** First screen for a kitchen without a profile: show me your documents, I suggest what to remember. */
export function OnboardingView() {
  const toast = useToast();
  const sign = useSignedChange();
  const camera = usePhoneCamera();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [learned, setLearned] = useState<Learned[]>([]);
  const [decided, setDecided] = useState<Record<string, boolean>>({});
  const [stage, setStage] = useState<"drop" | "reading" | "review">("drop");
  const [tell, setTell] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [adopting, setAdopting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const learnedRef = useRef<HTMLElement>(null);
  const docsRef = useRef(docs);
  docsRef.current = docs;
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const queue = useRef<Promise<void>>(Promise.resolve());

  const patchDoc = useCallback((key: string, patch: Partial<Doc>) => {
    setDocs((ds) => ds.map((d) => (d.key === key ? { ...d, ...patch } : d)));
  }, []);

  // Local object URLs for dropped images: free them when leaving.
  useEffect(() => () => {
    for (const d of docsRef.current) if (d.preview.kind === "image" && d.preview.src.startsWith("blob:")) URL.revokeObjectURL(d.preview.src);
  }, []);

  /** Read one document. Runs one after the other (one ingest at a time, the cards scan in order). */
  const readDoc = useCallback((key: string) => {
    queue.current = queue.current.then(async () => {
      const doc = docsRef.current.find((d) => d.key === key);
      if (!doc || (doc.status !== "ready" && doc.status !== "error")) return;
      patchDoc(key, { status: "reading", note: undefined });
      try {
        const out = await ingestKitchenDocuments(
          doc.upload?.upload_id ? { upload_ids: [doc.upload.upload_id], by: SIGNER_NAME } : { text: doc.text, by: SIGNER_NAME },
        );
        const result: IngestResult = out.results?.[0] ?? {};
        const title = doc.preview.kind === "text" ? doc.title : result.source?.title || doc.title;
        const fresh = (result.suggestions ?? []).filter((s) => s.status === "proposed");
        for (const s of fresh) {
          setLearned((ls) => (ls.some((l) => l.s.id === s.id) ? ls : [...ls, { s, docKey: key, from: title }]));
          setFocus(s.id ?? null);
          await sleep(REVEAL_MS);
        }
        setFocus(null);
        const note = result.hint || result.note || (fresh.length ? undefined : "Nothing new to remember in this one.");
        patchDoc(key, { status: "read", title, note });
      } catch (e) {
        patchDoc(key, { status: "error", note: (e as Error).message });
      }
    });
    return queue.current;
  }, [patchDoc]);

  const readAll = useCallback(async () => {
    setStage("reading");
    for (const d of docsRef.current) if (d.status === "ready" || (d.status === "error" && (d.upload || d.text))) void readDoc(d.key);
    await queue.current;
    setStage("review");
    window.setTimeout(() => learnedRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  }, [readDoc]);

  /** Add an uploaded document. Once reading has started, new documents are read right away. */
  const addUpload = useCallback((upload: UploadMeta, extra?: { title?: string; sub?: string; localSrc?: string; key?: string }) => {
    const key = extra?.key ?? newKey();
    const doc: Doc = {
      key,
      title: extra?.title ?? upload.filename ?? (upload.source === "phone" ? "Photo from your phone" : "Document"),
      sub: extra?.sub ?? subOf(upload),
      preview: previewOf(upload, extra?.localSrc),
      status: "ready",
      upload,
    };
    setDocs((ds) => (ds.some((d) => d.key === key) ? ds.map((d) => (d.key === key ? doc : d)) : [...ds, doc]));
    docsRef.current = docsRef.current.some((d) => d.key === key) ? docsRef.current.map((d) => (d.key === key ? doc : d)) : [...docsRef.current, doc];
    if (stageRef.current !== "drop") void readDoc(key).then(() => setStage("review"));
  }, [readDoc]);

  const uploadFiles = useCallback(async (files: File[]) => {
    for (const f of files) {
      const key = newKey();
      const localSrc = f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined;
      const placeholder: Doc = {
        key,
        title: f.name,
        sub: "uploading…",
        preview: localSrc ? { kind: "image", src: localSrc } : { kind: "pdf", name: f.name },
        status: "uploading",
      };
      setDocs((ds) => [...ds, placeholder]);
      docsRef.current = [...docsRef.current, placeholder];
      try {
        const meta = await uploadFile(f, { source: "upload" });
        addUpload(meta, { key, localSrc });
      } catch (e) {
        patchDoc(key, { status: "error", sub: "upload failed", note: (e as Error).message });
      }
    }
  }, [addUpload, patchDoc]);

  // Photos from the phone modal (or the composer-less file picker) land here.
  useEffect(() => camera.onPhoto("onboard", (u) => addUpload(u)), [camera, addUpload]);

  /** The text field: before reading it starts reading everything, afterwards it adds one more thing to read. */
  const readMyKitchen = async () => {
    const t = tell.trim();
    let key: string | undefined;
    if (t) {
      key = newKey();
      const doc: Doc = { key, title: "What you told me", sub: "typed", preview: { kind: "text", text: t }, status: "ready", text: t };
      setDocs((ds) => [...ds, doc]);
      docsRef.current = [...docsRef.current, doc];
      setTell("");
    }
    if (stageRef.current === "drop") return readAll();
    if (key) await readDoc(key).then(() => setStage("review"));
  };

  const loadExample = async () => {
    setStage("reading");
    try {
      for (const ex of EXAMPLE_DOCS) {
        const meta = await uploadFile(await exampleFile(ex), { source: "upload" });
        addUpload(meta, { title: ex.title, sub: ex.sub, localSrc: ex.type.startsWith("image/") ? ex.url : undefined });
      }
      await readAll();
    } catch (e) {
      toast(`Could not load the example kitchen: ${(e as Error).message}`);
      setStage("drop");
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/") || f.type === "application/pdf");
    if (files.length) void uploadFiles(files);
    else toast("Drop photos or PDFs.");
  };

  const keep = learned.filter((l) => decided[l.s.id ?? ""] !== false);
  const drop = learned.filter((l) => decided[l.s.id ?? ""] === false);
  const signedCount = keep.filter((l) => l.s.kind === "must" || l.s.kind === "autonomy").length;
  const needsMandate = keep.some((l) => MANDATE_KINDS.has(l.s.kind ?? ""));
  const busyReading = docs.some((d) => d.status === "reading" || d.status === "uploading");
  const canRead = stage === "drop" ? docs.some((d) => d.status === "ready") || !!tell.trim() : !!tell.trim();

  const adopt = async () => {
    setAdopting(true);
    try {
      const ids = keep.map((l) => l.s.id ?? "");
      if (ids.length) {
        const done = await sign({
          title: "Your kitchen, first version",
          call: (mandate_hash) => acceptKitchenSuggestions({ ids, by: SIGNER_NAME, ...(mandate_hash ? { mandate_hash } : {}) }),
        });
        if (done === null) return; // cancelled in the sign modal: nothing applied
      }
      for (const l of drop) await rejectKitchenSuggestion(l.s.id ?? "", { by: SIGNER_NAME }).catch(() => undefined);
      await queryClient.invalidateQueries({ queryKey: keys.kitchen });
      if (!needsMandate && keep.length) toast(`Saved. I remember ${keep.length} thing${keep.length === 1 ? "" : "s"} about your kitchen.`);
      navigate("order");
    } catch (e) {
      toast(`Could not save: ${(e as Error).message}`);
    } finally {
      setAdopting(false);
    }
  };

  const focused = learned.find((l) => l.s.id === focus);

  return (
    <main className="mx-auto max-w-[1280px] px-4 pt-12 pb-36">
      <div className="mx-auto grid max-w-[760px] gap-3.5 text-center">
        <div className="text-[13px] font-bold text-accent">Your purchasing agent for Valio Aimo</div>
        <h1 className="text-[clamp(34px,5vw,56px)] leading-[1.04] font-extrabold">Let me get to know your kitchen.</h1>
        <p className="mx-auto max-w-[600px] text-[17px] text-ink2">
          Don't type rules. Show me what you already have: menus, notes on the fridge, old invoices. I'll read them and suggest what to
          remember. Nothing here places an order. You decide what applies.
        </p>
      </div>

      <div className="mt-10 grid grid-cols-1 items-start gap-[18px] min-[560px]:grid-cols-2 min-[1000px]:grid-cols-[repeat(3,minmax(0,1fr))_minmax(0,0.7fr)]">
        {docs.map((d) => (
          <DocCard key={d.key} doc={d} highlight={focused?.docKey === d.key ? focused : undefined} />
        ))}
        <div
          className={`grid aspect-[4/5] max-w-full place-content-center gap-3 rounded-[18px] border-2 border-dashed p-4 text-center text-ink3 transition-colors ${dragging ? "border-accent bg-accent-soft" : "border-line"}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <Icon name="doc" size={28} className="mx-auto" />
          <p className="text-[13px]">Drop photos or PDFs here</p>
          <div className="flex flex-wrap justify-center gap-2">
            <Button small icon="phone" onClick={() => camera.open("onboard")}>Use your phone</Button>
            <Button small icon="clip" onClick={() => fileInput.current?.click()}>Choose files</Button>
          </div>
          <input
            ref={fileInput}
            type="file"
            accept="image/*,application/pdf"
            multiple
            hidden
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (files.length) void uploadFiles(files);
            }}
          />
        </div>
      </div>

      {/* Typing stays possible while documents upload or are read, and after. */}
      <form
        className="mx-auto mt-[22px] flex max-w-[640px] items-center gap-2 rounded-full bg-surface py-1.5 pr-1.5 pl-[18px]"
        onSubmit={(e) => {
          e.preventDefault();
          if (canRead) void readMyKitchen();
        }}
      >
        <input
          className="h-9 min-w-0 flex-1 bg-transparent outline-none"
          placeholder={stage === "drop" ? "Or tell me in one sentence: We're a school canteen in Espoo, nut-free…" : "Anything else? e.g. We cook for 120 kids, no pork…"}
          aria-label="Describe your kitchen"
          value={tell}
          onChange={(e) => setTell(e.target.value)}
        />
        <Button small variant="primary" type="submit" disabled={!canRead}>{stage === "drop" ? "Read my kitchen" : "Tell me"}</Button>
      </form>
      {stage === "drop" && (
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Button onClick={() => void loadExample()}>Skip, use example kitchen</Button>
        </div>
      )}

      {stage !== "drop" && !busyReading && docs.some((d) => d.status === "error" && (d.upload || d.text)) && (
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Button icon="spark" onClick={() => void readAll()}>Try reading again</Button>
        </div>
      )}

      {learned.length > 0 && (
        <section ref={learnedRef} className="mt-14 grid scroll-mt-24 gap-[18px]">
          <div className="flex flex-wrap items-end gap-4">
            <h2 className="text-3xl font-extrabold">{stage === "review" && !busyReading ? "Here's what I learned" : "Learning…"}</h2>
            <span className="text-ink3">{learned.length} found · hover a card to see where I read it</span>
          </div>
          <div className="grid grid-cols-1 gap-3 min-[760px]:grid-cols-2">
            {learned.map((l) => (
              <LearnCard
                key={l.s.id}
                s={l.s}
                from={l.from}
                decision={decided[l.s.id ?? ""]}
                focused={focus === l.s.id}
                onDecide={(v) => setDecided((d) => ({ ...d, [l.s.id ?? ""]: v }))}
                onHover={(on) => setFocus(on ? l.s.id ?? null : null)}
              />
            ))}
          </div>
        </section>
      )}

      {stage === "review" && !busyReading && (
        <div className="rise mt-10 flex flex-wrap items-center gap-4 rounded-[20px] bg-ink p-5 text-bg">
          <p className="min-w-0 flex-1">
            {learned.length === 0 ? (
              <>
                <b>Nothing to remember yet.</b> Add another photo or a sentence above, or start ordering and teach me as we go.
              </>
            ) : (
              <>
                <b>{keep.length} thing{keep.length === 1 ? "" : "s"} to remember.</b>{" "}
                {signedCount > 0
                  ? `${signedCount} of them ${signedCount === 1 ? "is a signed rule" : "are signed rules"}: Valio Aimo enforces ${signedCount === 1 ? "it" : "them"} on every order, I can't change ${signedCount === 1 ? "it" : "them"}.`
                  : needsMandate
                    ? "Your preferences go into the signed mandate."
                    : "Nothing here needs a signature."}
              </>
            )}
          </p>
          <Button variant="inverse" iconAfter="arrow" disabled={adopting} onClick={() => void adopt()}>
            {adopting ? "Saving…" : learned.length === 0 ? "Start ordering" : needsMandate ? "Sign & start ordering" : "Save & start ordering"}
          </Button>
        </div>
      )}
    </main>
  );
}

function DocCard({ doc, highlight }: { doc: Doc; highlight?: Learned }) {
  const reading = doc.status === "reading" || doc.status === "uploading";
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2.5">
      <div
        className={`relative aspect-[4/5] min-h-0 max-w-full overflow-hidden rounded-[18px] bg-surface p-[18px] shadow-card transition-shadow ${highlight ? "ring-3 ring-ink3" : ""}`}
      >
        {doc.preview.kind === "image" ? (
          <img src={doc.preview.src} alt={doc.title} className="absolute inset-[18px] size-[calc(100%-36px)] rounded-md object-cover shadow-card" />
        ) : doc.preview.kind === "pdf" ? (
          <Paper variant="inv">
            <div className="grid h-full place-content-center gap-3 text-center">
              <Icon name="doc" size={40} className="mx-auto" />
              <b className="text-[13px] break-all">{doc.preview.name}</b>
              <span className="opacity-70">PDF</span>
            </div>
          </Paper>
        ) : (
          <Paper variant="hand" tilt="r">
            <div>{doc.preview.text}</div>
          </Paper>
        )}
        {highlight?.s.quote && (
          <div className="rise absolute inset-x-3 bottom-3 rounded-lg bg-paper px-3 py-2 text-[13px] text-paper-ink shadow-float">
            <Mark kind={suggestionKind(highlight.s)} focus>
              {highlight.s.quote}
            </Mark>
          </div>
        )}
        {reading && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 h-[18%]"
            style={{
              background: "linear-gradient(180deg, transparent, color-mix(in srgb, var(--accent) 22%, transparent), transparent)",
              animation: "scan 1.5s linear infinite",
            }}
          />
        )}
      </div>
      <div className="flex items-center gap-2.5">
        <div className="min-w-0">
          <b className="block truncate text-sm">{doc.title}</b>
          <div className="truncate text-xs text-ink3">{doc.sub}</div>
        </div>
        <StatusPill status={doc.status} />
      </div>
      {doc.note && <p className={`text-xs ${doc.status === "error" ? "text-must" : "text-ink3"}`}>{doc.note}</p>}
    </div>
  );
}

function StatusPill({ status }: { status: DocStatus }) {
  const base = "ml-auto whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold";
  switch (status) {
    case "read":
      return <span className={`${base} bg-prefer-soft text-prefer`}>Read</span>;
    case "reading":
      return <span className={`${base} animate-pulse-soft bg-accent-soft text-accent`}>Reading…</span>;
    case "uploading":
      return <span className={`${base} animate-pulse-soft bg-surface text-ink3`}>Uploading…</span>;
    case "error":
      return <span className={`${base} bg-must-soft text-must`}>Failed</span>;
    default:
      return <span className={`${base} bg-surface text-ink3`}>Ready</span>;
  }
}
