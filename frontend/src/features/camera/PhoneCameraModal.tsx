import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { apiUrl } from "../../api/client";
import { createPairing, endPairing, subscribeToPairingStream } from "../../api/kitchen";
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

type Photo = { upload: UploadMeta; src: string; isPdf: boolean };

type State =
  | { stage: "pairing" }
  | { stage: "waiting"; pairId: string; phoneUrl: string; qr: string; paired: boolean }
  | { stage: "error"; message: string };

/**
 * "Use your phone as the camera": QR code of the pairing URL, live pairing stream, received thumbnails.
 * One pairing takes any number of photos. They collect here and go on together when the user clicks
 * "Use photos"; closing the dialog any other way discards them and ends the pairing.
 * The phone page itself is the backend's `GET /phone` (1a).
 */
export function PhoneCameraModal({ where, onClose, onPhoto, onUpload }: {
  where: CameraTarget | null;
  onClose: () => void;
  onPhoto: (where: CameraTarget, uploads: UploadMeta[]) => void;
  onUpload: (where: CameraTarget) => void;
}) {
  const [state, setState] = useState<State>({ stage: "pairing" });
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [shown, setShown] = useState<string | null>(null);

  useEffect(() => {
    if (!where) return;
    let stop: (() => void) | null = null;
    let pairId = "";
    let cancelled = false;
    setState({ stage: "pairing" });
    setPhotos([]);
    setShown(null);

    (async () => {
      try {
        const pairing = await createPairing(undefined, where === "order" ? "order" : "kitchen");
        if (cancelled) return;
        pairId = pairing.pair_id ?? "";
        const phoneUrl = pairing.phone_url ?? "";
        const qr = await QRCode.toString(phoneUrl, { type: "svg", margin: 1, errorCorrectionLevel: "M" });
        if (cancelled) return;
        setState({ stage: "waiting", pairId, phoneUrl, qr, paired: false });
        stop = subscribeToPairingStream(pairId, (evt) => {
          if (evt.type === "paired") {
            setState((s) => (s.stage === "waiting" ? { ...s, paired: true } : s));
          } else if (evt.type === "photo_received" && evt.upload_id) {
            const id = evt.upload_id;
            const isPdf = evt.media_type === "application/pdf";
            const photo: Photo = {
              upload: { upload_id: id, url: evt.url, media_type: isPdf ? "application/pdf" : "image/jpeg", source: "phone", pair_id: pairId },
              src: apiUrl(evt.url ?? `/uploads/${id}`),
              isPdf,
            };
            // A photo also means the phone is connected (in case `paired` was missed).
            setState((s) => (s.stage === "waiting" ? { ...s, paired: true } : s));
            setPhotos((ps) => (ps.some((p) => p.upload.upload_id === id) ? ps : [...ps, photo]));
            setShown(id);
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
      if (pairId) void endPairing(pairId).catch(() => undefined);
    };
  }, [where]);

  if (!where) return null;
  const n = photos.length;
  const current = photos.find((p) => p.upload.upload_id === shown) ?? photos[n - 1] ?? null;
  const remove = (id: string) => {
    setPhotos((ps) => ps.filter((p) => p.upload.upload_id !== id));
    if (shown === id) setShown(null);
  };
  const use = () => {
    onPhoto(where, photos.map((p) => p.upload));
    onClose();
  };

  return (
    <Modal open label="Use your phone as the camera" width={880} onClose={onClose}>
      <div className="grid items-center gap-7 md:grid-cols-[minmax(0,1fr)_300px]">
        <div className="grid justify-items-start gap-3.5">
          <div className="eyebrow">Use your phone as the camera</div>
          <h2>{HEADLINE[where]}</h2>
          <p className="text-ink3">{BODY[where]} Take as many photos as you need. They appear here right away.</p>
          {state.stage === "waiting" && (
            <>
              <div
                className="size-[168px] rounded-[14px] border border-line bg-white p-2.5 [&>svg]:size-full"
                role="img"
                aria-label="QR code to pair your phone"
                dangerouslySetInnerHTML={{ __html: state.qr }}
              />
              <p className="text-[13px] text-ink3">
                {n ? (
                  <span className="text-prefer">
                    <Icon name="check" size={14} className="mr-1 inline align-[-2px]" />
                    {n === 1 ? "1 photo received." : `${n} photos received.`} Take another on your phone, or use {n === 1 ? "it" : "them"}.
                  </span>
                ) : state.paired ? (
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
          {state.stage === "error" && <p className="rounded-xl bg-must-soft px-3.5 py-2.5 text-sm text-must">{state.message}</p>}
          {n > 0 && (
            <ul className="flex flex-wrap gap-2" aria-label="Received photos">
              {photos.map((p, i) => (
                <li key={p.upload.upload_id} className="relative">
                  <button
                    type="button"
                    onClick={() => setShown(p.upload.upload_id ?? null)}
                    aria-label={`Show photo ${i + 1}`}
                    className={`grid size-16 place-content-center overflow-hidden rounded-[10px] border-2 bg-[#141416] ${p === current ? "border-ink" : "border-line"}`}
                  >
                    {p.isPdf ? <Icon name="doc" size={24} className="text-white" /> : <img src={p.src} alt="" className="size-16 object-cover" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(p.upload.upload_id ?? "")}
                    aria-label={`Remove photo ${i + 1}`}
                    title="Remove"
                    className="absolute -top-1.5 -right-1.5 grid size-5 place-content-center rounded-full bg-ink text-[13px] leading-none text-bg"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap gap-2.5">
            {n > 0 && (
              <Button small variant="primary" icon="check" onClick={use}>
                {n === 1 ? "Use this photo" : `Use ${n} photos`}
              </Button>
            )}
            <Button small icon="clip" onClick={() => { onClose(); onUpload(where); }}>Upload from this computer</Button>
            <Button small variant="link" onClick={onClose}>Cancel</Button>
          </div>
        </div>
        <PhoneFrame state={state} photo={current} />
      </div>
    </Modal>
  );
}

function PhoneFrame({ state, photo }: { state: State; photo: Photo | null }) {
  return (
    <div className="relative hidden h-[560px] w-[280px] max-w-full justify-self-center rounded-[44px] bg-[#0b0b0c] p-3 shadow-float md:block">
      <div className="absolute top-5 left-1/2 z-10 h-[26px] w-[90px] -translate-x-1/2 rounded-[14px] bg-[#0b0b0c]" />
      <div className="relative grid size-full overflow-hidden rounded-[34px] bg-[#141416]">
        {photo ? (
          photo.isPdf ? (
            <div className="grid place-content-center gap-2.5 p-6 text-center text-[13px] text-[#bbb]">
              <Icon name="doc" size={40} className="mx-auto text-white" />
              <b className="text-[15px] text-white">PDF received</b>
            </div>
          ) : (
            <img key={photo.src} src={photo.src} alt="The photo from your phone" className="rise size-full object-cover" />
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
