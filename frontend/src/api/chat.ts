import { apiRequest, apiUrl } from "./client";
import type { ChatRequest, ChatResponse, ChatStreamEvent } from "./types";

export function startChat(request: ChatRequest): Promise<ChatResponse> {
  return apiRequest<ChatResponse>("/chat", { method: "POST", json: request });
}

const chatEventTypes: ChatStreamEvent["type"][] = [
  "user_message",
  "reasoning",
  "message",
  "intent_proposed",
  "intent_confirmed",
  "order_rejected",
  "tool_call",
  "tool_result",
  "offers",
  "approval_required",
  "order_confirmed",
  "error",
  "done",
  "autonomy_progress",
];

export interface StreamHandlers {
  onEvent: (event: ChatStreamEvent) => void;
  onError?: (event: Event) => void;
}

/**
 * Subscribe to the replayable chat stream. The backend currently does not send
 * SSE event IDs, so consumers should make their reducer tolerant of replayed
 * events after a reconnect.
 */
export function subscribeToChatStream(
  sessionId: string,
  handlers: StreamHandlers,
): () => void {
  const source = new EventSource(
    apiUrl(`/chat/${encodeURIComponent(sessionId)}/stream`),
  );

  for (const type of chatEventTypes) {
    source.addEventListener(type, (event) => {
      try {
        handlers.onEvent(JSON.parse((event as MessageEvent<string>).data) as ChatStreamEvent);
      } catch (error) {
        console.error(`Could not parse ${type} event from chat stream`, error);
      }
    });
  }

  source.onerror = (event) => handlers.onError?.(event);
  return () => source.close();
}
