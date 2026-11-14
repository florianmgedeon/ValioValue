import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { startChat, subscribeToChatStream } from "../../api/chat";
import { keys, queryClient } from "../../app/queries";
import { useToast } from "../../ui";
import { initialState, reduce, type LocalAction, type OrderEvent } from "./orderReducer";

const SESSION_KEY = "order.session";
const TOASTED_KEY = "order.toasted";

function read(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string | null) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* private mode: the order just does not survive a reload */
  }
}

/**
 * One order = one chat session. The session id lives in sessionStorage, so a reload mid-order subscribes again and
 * the replayed stream rebuilds the same blocks. "New order" forgets it.
 */
export function useOrderSession() {
  const toast = useToast();
  const [sessionId, setSessionId] = useState<string | null>(() => read(SESSION_KEY));
  const [state, dispatch] = useReducer(reduce, initialState);
  const [sending, setSending] = useState(false);
  const toasted = useRef(new Set<string>((read(TOASTED_KEY) ?? "").split(",").filter(Boolean)));

  useEffect(() => {
    if (!sessionId) return;
    let opened = false;
    const close = subscribeToChatStream(sessionId, {
      onEvent: (raw) => {
        opened = true;
        const evt = raw as OrderEvent;
        dispatch(evt);
        if (evt.type === "order_confirmed" || evt.type === "order_rejected" || evt.type === "approval_required") {
          void queryClient.invalidateQueries({ queryKey: keys.orders });
        }
        if (evt.type === "autonomy_progress") {
          void queryClient.invalidateQueries({ queryKey: keys.autonomy });
          // Toast once per order, not again on a replay.
          const id = evt.order_id ?? evt.timestamp;
          if (!toasted.current.has(id)) {
            toasted.current.add(id);
            write(TOASTED_KEY, [...toasted.current].slice(-20).join(","));
            if (evt.clean && (evt.level ?? 0) < 2) toast(`Clean order. ${evt.clean_orders} of ${evt.needed} towards level ${(evt.level ?? 0) + 1}.`);
            else if (evt.clean) toast("Clean order.");
            else if (evt.reason) toast(evt.reason);
          }
        }
      },
      onError: (e) => {
        // The backend forgot the session (restart): start over instead of retrying forever.
        if (!opened && (e.target as EventSource | null)?.readyState === EventSource.CLOSED) {
          write(SESSION_KEY, null);
          setSessionId(null);
          dispatch({ type: "local/reset" });
        }
      },
    });
    return close;
  }, [sessionId, toast]);

  /** Send a message (and photos). Starts a session if there is none. */
  const send = useCallback(
    async (message: string, attachments: { upload_id: string }[] = []) => {
      setSending(true);
      try {
        const res = await startChat({ message, attachments: attachments.length ? attachments : undefined, session_id: sessionId });
        if (res.session_id !== sessionId) {
          write(SESSION_KEY, res.session_id);
          dispatch({ type: "local/reset" });
          setSessionId(res.session_id);
        }
      } catch (e) {
        toast(`Could not send: ${(e as Error).message}`);
      } finally {
        setSending(false);
      }
    },
    [sessionId, toast],
  );

  const newOrder = useCallback(() => {
    write(SESSION_KEY, null);
    setSessionId(null);
    dispatch({ type: "local/reset" });
  }, []);

  const local = useCallback((a: LocalAction) => dispatch(a), []);

  return { sessionId, state, send, sending, newOrder, local };
}
