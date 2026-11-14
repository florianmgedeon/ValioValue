import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { uploadFile } from "../api/kitchen";
import type { UploadMeta } from "../api/types";
import { ingestSummary, ingestUpload } from "../features/camera/ingestPhoto";
import { PhoneCameraModal } from "../features/camera/PhoneCameraModal";
import { useToast } from "../ui";

/** Where a photo goes (decision W2): an order photo starts a chat, kitchen/onboard photos are only learned from. */
export type CameraTarget = "order" | "kitchen" | "onboard";
/** Photos arrive together: all photos taken in one phone session, or the one uploaded file. */
export type PhotoHandler = (uploads: UploadMeta[]) => void;

interface PhoneCameraApi {
  /** Open the phone camera flow: QR pairing on a desktop, the device camera on a phone. */
  open: (where: CameraTarget) => void;
  /** Pick a file on this device and upload it. */
  upload: (where: CameraTarget) => void;
  /** A feature says what happens with a photo for `where`. Returns unregister. */
  onPhoto: (where: CameraTarget, handler: PhotoHandler) => () => void;
  /** Called by the camera modal / upload when photos arrived. */
  deliver: (where: CameraTarget, uploads: UploadMeta[]) => void;
  /** Replace how `open` works (tests, demos). `null` restores the phone modal. */
  setOpener: (opener: ((where: CameraTarget) => void) | null) => void;
}

const Ctx = createContext<PhoneCameraApi | null>(null);

/** A phone (touch, narrow) takes the photo itself; a desktop pairs a phone via QR. */
function isPhone() {
  return window.matchMedia("(pointer: coarse)").matches && window.innerWidth < 900;
}

export function PhoneCameraProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const handlers = useRef(new Map<CameraTarget, PhotoHandler>());
  const fileInput = useRef<HTMLInputElement>(null);
  const pending = useRef<CameraTarget>("order");
  const opener = useRef<((where: CameraTarget) => void) | null>(null);
  const [modal, setModal] = useState<CameraTarget | null>(null);

  const deliver = useCallback((where: CameraTarget, ups: UploadMeta[]) => {
    if (!ups.length) return;
    const h = handlers.current.get(where);
    if (h) return h(ups);
    if (where === "order") return toast("Photo received, but the Order tab is not open.");
    // Kitchen and onboarding photos are learned from (W2), also when no screen handles them itself.
    toast(ups.length > 1 ? `Reading your ${ups.length} documents…` : "Reading your document…");
    for (const u of ups) {
      ingestUpload(u)
        .then((out) => toast(ingestSummary(out)))
        .catch((e: Error) => toast(`Could not read it: ${e.message}`));
    }
  }, [toast]);

  const upload = useCallback((where: CameraTarget) => {
    pending.current = where;
    fileInput.current?.click();
  }, []);

  const onPhoto = useCallback((where: CameraTarget, handler: PhotoHandler) => {
    handlers.current.set(where, handler);
    return () => {
      if (handlers.current.get(where) === handler) handlers.current.delete(where);
    };
  }, []);

  const setOpener = useCallback((o: ((where: CameraTarget) => void) | null) => {
    opener.current = o;
  }, []);

  const open = useCallback((where: CameraTarget) => {
    if (opener.current) return opener.current(where);
    if (isPhone()) return upload(where);
    setModal(where);
  }, [upload]);

  const closeModal = useCallback(() => setModal(null), []);

  const api = useMemo<PhoneCameraApi>(() => ({ open, upload, onPhoto, deliver, setOpener }), [open, upload, onPhoto, deliver, setOpener]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <PhoneCameraModal where={modal} onClose={closeModal} onPhoto={deliver} onUpload={upload} />
      <input
        ref={fileInput}
        type="file"
        accept="image/*,application/pdf"
        capture="environment"
        hidden
        onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          try {
            deliver(pending.current, [await uploadFile(f, { source: "upload" })]);
          } catch (err) {
            toast(`Upload failed: ${(err as Error).message}`);
          }
        }}
      />
    </Ctx.Provider>
  );
}

export function usePhoneCamera() {
  const api = useContext(Ctx);
  if (!api) throw new Error("PhoneCameraProvider missing");
  return api;
}
