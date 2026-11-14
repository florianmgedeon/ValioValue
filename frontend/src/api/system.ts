import { apiRequest } from "./client";

export interface BackendHealth {
  status: string;
  llm_provider?: string;
  mcp_server?: string;
}

export interface BackendTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export function getBackendHealth(): Promise<BackendHealth> {
  return apiRequest<BackendHealth>("/health");
}

export function getAgentTools(): Promise<BackendTool[]> {
  return apiRequest<BackendTool[]>("/tools");
}
