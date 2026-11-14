import { apiRequest } from "./client";
import type { Approval, ApprovalDecision, ApprovalStatus } from "./types";

export function listApprovals(status?: ApprovalStatus): Promise<Approval[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiRequest<Approval[]>(`/approvals${query}`);
}

export function decideApproval(
  approvalId: string,
  decision: ApprovalDecision,
): Promise<Approval> {
  return apiRequest<Approval>(`/approvals/${encodeURIComponent(approvalId)}`, {
    method: "POST",
    json: decision,
  });
}
