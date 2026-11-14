import { ingestKitchenDocuments } from "../../api/kitchen";
import type { IngestResponse, UploadMeta } from "../../api/types";
import { keys, queryClient, SIGNER_NAME } from "../../app/queries";

/** Learn from one photo or PDF (decision W2: kitchen photos are read, never ordered from). Refreshes the profile. */
export async function ingestUpload(upload: UploadMeta): Promise<IngestResponse> {
  const out = await ingestKitchenDocuments({ upload_ids: [upload.upload_id ?? ""], by: SIGNER_NAME });
  await queryClient.invalidateQueries({ queryKey: keys.kitchen });
  return out;
}

/** One line for a toast: what the agent took from the document. */
export function ingestSummary(out: IngestResponse): string {
  const r = out.results ?? [];
  const n = r.reduce((sum, x) => sum + (x.suggestions?.length ?? 0), 0);
  const hint = r.find((x) => x.hint)?.hint;
  const note = r.find((x) => x.note)?.note;
  if (n) return `I found ${n} thing${n === 1 ? "" : "s"} to remember. Check them under Suggestions.`;
  return hint || note || "I read it, but found nothing new to remember.";
}
