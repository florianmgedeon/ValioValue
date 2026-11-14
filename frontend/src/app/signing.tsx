import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { previewMandate, saveMandate } from "../api/mandate";
import { Button, CedarBlock, DiffList, HashRow, Modal, useToast, type DiffKind } from "../ui";
import { invalidateMandateViews, SIGNER_NAME } from "./queries";

/** What Valio Aimo previews before a signature. */
export interface SignPreview {
  mandate_hash: string;
  version?: number;
  cedar?: string;
  summary?: string;
  diff?: string[];
}

export type DiffLine = { text: string; kind: DiffKind };

export type SignRequest<T> = {
  /** Short name of the change, e.g. "Autonomy level 2". */
  title: string;
  /** What changes, in the buyer's words. Falls back to the backend's diff lines. */
  lines?: DiffLine[];
} & (
  | {
      /** Endpoints with the 409 round trip (`/kitchen/suggestions/accept`, `/autonomy/level`): called without hash
       *  first; on `409 {needs_signature, preview}` the modal opens; "Sign" calls again with the previewed hash. */
      call: (mandateHash?: string) => Promise<T>;
      mandate?: never;
    }
  | {
      /** A whole new mandate for `PUT /mandate` (batched rule edits): previewed, signed, saved. */
      mandate: Record<string, unknown>;
      call?: never;
    }
);

interface Pending {
  title: string;
  lines: DiffLine[];
  preview: SignPreview;
  confirm: () => Promise<void>;
  cancel: () => void;
}

const SignContext = createContext<(<T>(req: SignRequest<T>) => Promise<T | null>) | null>(null);

function needsSignature(e: unknown): SignPreview | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null;
  const d = e.detail as { needs_signature?: boolean; preview?: SignPreview } | undefined;
  return d?.needs_signature && d.preview?.mandate_hash ? d.preview : null;
}

export function SignProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const settle = useRef<(() => void) | null>(null);

  const sign = useCallback(<T,>(req: SignRequest<T>): Promise<T | null> => {
    return new Promise<T | null>((resolve, reject) => {
      const open = (preview: SignPreview, run: (hash: string) => Promise<T>) => {
        setError(null);
        settle.current = () => resolve(null);
        setPending({
          title: req.title,
          lines: req.lines ?? (preview.diff ?? []).map((text) => ({ text, kind: "chg" as const })),
          preview,
          cancel: () => {
            setPending(null);
            resolve(null);
          },
          confirm: async () => {
            setBusy(true);
            try {
              const out = await run(preview.mandate_hash);
              setPending(null);
              await invalidateMandateViews();
              toast(`Mandate v${preview.version ?? "?"} signed. Valio Aimo enforces it from the next order.`);
              resolve(out);
            } catch (e) {
              const again = needsSignature(e);
              if (again) {
                // Something changed since the preview: show the new one, sign again.
                setPending((p) => (p ? { ...p, preview: again } : p));
                setError("The mandate changed since it was shown. Check it again.");
              } else {
                setError((e as Error).message);
              }
            } finally {
              setBusy(false);
            }
          },
        });
      };

      if (req.mandate) {
        const mandate = req.mandate;
        previewMandate({ mandate })
          .then((pre) => open(pre as SignPreview, (hash) => saveMandate({ mandate, mandate_hash: hash, updated_by: SIGNER_NAME }) as Promise<T>))
          .catch(reject);
        return;
      }
      const call = req.call;
      call()
        .then((out) => {
          // No signature needed (unsigned kitchen-profile change).
          void invalidateMandateViews();
          resolve(out);
        })
        .catch((e) => {
          const preview = needsSignature(e);
          if (preview) open(preview, (hash) => call(hash));
          else reject(e);
        });
    });
  }, [toast]);

  return (
    <SignContext.Provider value={sign}>
      {children}
      <Modal open={!!pending} label="Review and sign" onClose={() => pending?.cancel()}>
        {pending && (
          <>
            <div className="eyebrow">{pending.title}{pending.preview.version ? ` · mandate v${pending.preview.version}` : ""}</div>
            <h2>Review and sign</h2>
            <p className="text-ink3">Valio Aimo enforces this version from the next order on. Orders already placed keep the version they were made with.</p>
            {pending.lines.length > 0 && <DiffList lines={pending.lines} />}
            {pending.preview.cedar && (
              <details>
                <summary className="link cursor-pointer text-sm">Show the generated policy (Cedar)</summary>
                <div className="mt-2.5 max-h-72 overflow-auto rounded-xl">
                  <CedarBlock text={pending.preview.cedar} />
                </div>
              </details>
            )}
            <HashRow label="You sign this hash" value={pending.preview.mandate_hash} />
            {error && <p className="rounded-xl bg-must-soft px-3.5 py-2.5 text-sm text-must">{error}</p>}
            <div className="flex flex-wrap justify-end gap-2.5">
              <Button onClick={pending.cancel} disabled={busy}>Cancel</Button>
              <Button variant="primary" icon="lock" onClick={() => void pending.confirm()} disabled={busy}>
                {busy ? "Signing…" : `Sign as ${SIGNER_NAME}`}
              </Button>
            </div>
          </>
        )}
      </Modal>
    </SignContext.Provider>
  );
}

/**
 * Every change to the signed mandate goes through this. Resolves with the endpoint's result, or null if the buyer
 * cancelled. Unsigned changes (the endpoint answers without 409) resolve right away, no modal.
 *
 *   const sign = useSignedChange();
 *   await sign({ title: "Autonomy level 2", call: (mandate_hash) => setAutonomyLevel({ level: 2, mandate_hash }) });
 *   await sign({ title: "3 rule changes", lines, mandate: draft });
 */
export function useSignedChange() {
  const sign = useContext(SignContext);
  if (!sign) throw new Error("SignProvider missing");
  return sign;
}
