import { createContext, useCallback, useContext, useRef, type ReactNode } from "react";
import { uploadFile } from "../api/kitchen";
import type { UploadMeta } from "../api/types";
import { useToast } from "../ui";

/** Where a photo goes (decision W2): an order photo starts a chat, kitchen/onboard photos are only learned from. */
export type CameraTarget = "order" | "kitchen" | "onboard";
export type PhotoHandler = (upload: UploadMeta) => void;

interface PhoneCameraApi {
  /** Open the phone camera flow (QR pairing). F3 replaces the stub modal. */
  open: (where: CameraTarget) => void;
  /** Pick a file on this device and upload it. */
  upload: (where: CameraTarget) => void;
  /** A feature says what happens with a photo for `where`. Returns unregister. */
  onPhoto: (where: CameraTarget, handler: PhotoHandler) => () => void;
  /** Called by the camera modal / upload when a photo arrived. */
  deliver: (where: CameraTarget, upload: UploadMeta) => void;
  /** F3's modal registers how `open` works. Without it, `open` falls back to the file picker. */
  setOpener: (opener: ((where: CameraTarget) => void) | null) => void;
}

const Ctx = createContext<PhoneCameraApi | null>(null);

export function PhoneCameraProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const handlers = useRef(new Map<CameraTarget, PhotoHandler>());
  const fileInput = useRef<HTMLInputElement>(null);
  const pending = useRef<CameraTarget>("order");
  const opener = useRef<((where: CameraTarget) => void) | null>(null);

  const deliver = useCallback((where: CameraTarget, upload: UploadMeta) => {
    const h = handlers.current.get(where);
    if (h) h(upload);
    else toast(`Photo received, but nothing handles "${where}" photos yet.`);
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

  const api: PhoneCameraApi = {
    open: (where) => (opener.current ?? upload)(where),
    upload,
    onPhoto,
    deliver,
    setOpener,
  };

  return (
    <Ctx.Provider value={api}>
      {children}
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
            deliver(pending.current, await uploadFile(f, { source: "upload" }));
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
