const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | null;
  json?: unknown;
};

export async function apiRequest<TResponse>(
  path: string,
  options: RequestOptions = {},
): Promise<TResponse> {
  const { json, body, headers, ...init } = options;
  const requestHeaders = new Headers(headers);
  let requestBody = body;

  if (json !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
    requestBody = JSON.stringify(json);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    body: requestBody,
    headers: requestHeaders,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? payload.detail
        : payload;
    const message =
      typeof detail === "string"
        ? detail
        : `Request failed (${response.status} ${response.statusText})`;
    throw new ApiError(response.status, message, detail);
  }

  return payload as TResponse;
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}
