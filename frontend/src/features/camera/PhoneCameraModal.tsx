import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { apiUrl } from "../../api/client";
import { createPairing, subscribeToPairingStream } from "../../api/kitchen";
import type { UploadMeta } from "../../api/types";
import type { CameraTarget } from "../../app/PhoneCamera";
import { Button, Icon, Modal } from "../../ui";

const HEADLINE: Record<CameraTarget, string> = {
  onboard: "Photograph anything from your kitchen.",
  kitchen: "Teach me with a photo.",
  order: "Photograph your order.",
};

const BODY: Record<CameraTarget, string> = {
  order: "A list, a note, a whiteboard. I read items, amounts and the delivery date.",
  kitchen: "Menus, notes, invoices. I only learn from these, I never order from them.",
  onboard: "Menus, notes, invoices. I only learn from these, I never order from them.",
};

type State =
  | { stage: "pairing" }
  | { stage: "waiting"; pairId: string; phoneUrl: string; qr: string; paired: boolean }
  | { stage: "received"; url: string; isPdf: boolean }
  | { stage: "error"; message: string };

/** The phone's photo is shown for a moment before the modal closes. */
const RECEIVED_MS = 900;

/**
 * "Use your phone as the camera": QR code of the pairing URL, live pairing stream, received thumbnail.
 * The phone page itself is the backend's `GET /phone` (1a).
 */
export function PhoneCameraModal({ where, onClose, onPhoto, onUpload }: {
  where: CameraTarget | null;
  onClose: () => void;
  onPhoto: (where: CameraTarget, upload: UploadMeta) => void;
  onUpload: (where: CameraTarget) => void;
}) {
  const [state, setState] = useState<State>({ stage: "pairing" });

  useEffect(() => {
    if (!where) return;
    let stop: (() => void) | null = null;
    let closeTimer: number | undefined;
    let cancelled = false;
    setState({ stage: "pairing" });

    (async () => {
      try {
        const pairing = await createPairing(undefined, where === "order" ? "order" : "kitchen");
        if (cancelled) return;
        const pairId = pairing.pair_id ?? "";
        const phoneUrl = pairing.phone_url ?? "";
        const qr = await QRCode.toString(phoneUrl, { type: "svg", margin: 1, errorCorrectionLevel: "M" });
        if (cancelled) return;
        setState({ stage: "waiting", pairId, phoneUrl, qr, paired: false });
        stop = subscribeToPairingStream(pairId, (evt) => {
          if (evt.type === "paired") {
            setState((s) => (s.stage === "waiting" ? { ...s, paired: true } : s));
          } else if (evt.type === "photo_received" && evt.upload_id) {
            const upload: UploadMeta = {
              upload_id: evt.upload_id,
              url: evt.url,
              media_type: evt.media_type === "application/pdf" ? "application/pdf" : "image/jpeg",
              source: "phone",
              pair_id: pairId,
            };
            stop?.();
            setState({ stage: "received", url: apiUrl(evt.url ?? `/uploads/${evt.upload_id}`), isPdf: upload.media_type === "application/pdf" });
            onPhoto(where, upload);
            closeTimer = window.setTimeout(onClose, RECEIVED_MS);
          } else if (evt.type === "expired") {
            setState({ stage: "error", message: "The pairing expired. Close this and try again." });
          }
        });
      } catch (e) {
        if (!cancelled) setState({ stage: "error", message: `Could not start pairing: ${(e as Error).message}` });
      }
    })();

    return () => {
      cancelled = true;
      stop?.();
      window.clearTimeout(closeTimer);
    };
    // onPhoto/onClose are stable callbacks from the provider.
  }, [where, onPhoto, onClose]);

  if (!where) return null;
  return (
    <Modal open label="Use your phone as the camera" width={880} onClose={onClose}>
      <div className="grid items-center gap-7 md:grid-cols-[minmax(0,1fr)_300px]">
        <div className="grid justify-items-start gap-3.5">
          <div className="eyebrow">Use your phone as the camera</div>
          <h2>{HEADLINE[where]}</h2>
          <p className="text-ink3">{BODY[where]} Photos appear here right away.</p>
          {state.stage === "waiting" && (
            <>
              <div
                className="size-[168px] rounded-[14px] border border-line bg-white p-2.5 [&>svg]:size-full"
                role="img"
                aria-label="QR code to pair your phone"
                dangerouslySetInnerHTML={{ __html: state.qr }}
              />
              <p className="text-[13px] text-ink3">
                {state.paired ? (
                  <>
                    <Icon name="check" size={14} className="mr-1 inline align-[-2px] text-prefer" />
                    Your phone is connected. Take the photo.
                  </>
                ) : (
                  <span className="animate-pulse-soft">Waiting for your phone…</span>
                )}
              </p>
              <p className="text-xs break-all text-ink3">
                Or open <a className="link" href={state.phoneUrl} target="_blank" rel="noreferrer">{state.phoneUrl}</a>
                {" "}· code <code className="font-mono">{state.pairId.replace(/^pair_/, "")}</code>
              </p>
            </>
          )}
          {state.stage === "pairing" && <p className="text-[13px] text-ink3 animate-pulse-soft">Preparing the QR code…</p>}
          {state.stage === "received" && (
            <p className="text-[13px] text-prefer">
              <Icon name="check" size={14} className="mr-1 inline align-[-2px]" />
              Photo received.
            </p>
          )}
          {state.stage === "error" && <p className="rounded-xl bg-must-soft px-3.5 py-2.5 text-sm text-must">{state.message}</p>}
          <div className="flex flex-wrap gap-2.5">
            <Button small icon="clip" onClick={() => { onClose(); onUpload(where); }}>Upload from this computer</Button>
            <Button small variant="link" onClick={onClose}>Cancel</Button>
          </div>
        </div>
        <PhoneFrame state={state} />
      </div>
    </Modal>
  );
}

function PhoneFrame({ state }: { state: State }) {
  return (
    <div className="relative hidden h-[560px] w-[280px] max-w-full justify-self-center rounded-[44px] bg-[#0b0b0c] p-3 shadow-float md:block">
      <div className="absolute top-5 left-1/2 z-10 h-[26px] w-[90px] -translate-x-1/2 rounded-[14px] bg-[#0b0b0c]" />
      <div className="relative grid size-full overflow-hidden rounded-[34px] bg-[#141416]">
        {state.stage === "received" ? (
          state.isPdf ? (
            <div className="grid place-content-center gap-2.5 p-6 text-center text-[13px] text-[#bbb]">
              <Icon name="doc" size={40} className="mx-auto text-white" />
              <b className="text-[15px] text-white">PDF received</b>
            </div>
          ) : (
            <img src={state.url} alt="The photo from your phone" className="rise size-full object-cover" />
          )
        ) : (
          <div className="grid place-content-center gap-2.5 p-6 text-center text-[13px] text-[#bbb]">
            <b className="text-[15px] text-white">Kitchen agent</b>
            {state.stage === "waiting" && state.paired ? "Camera open. Take the photo." : "Scan the QR code to open the camera."}
          </div>
        )}
      </div>
    </div>
  );
}
