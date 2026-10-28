import type { AnalyzeRequest, AnalyzeResponse } from "./types";

export const API_URL = (
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://127.0.0.1:8787"
).replace(/\/+$/, "");

/** An error with a message that's safe to show to the user. */
export class ApiError extends Error {}

/** FastAPI returns `detail` as a string, or as a list of validation errors. */
export function formatDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((err) => {
      const where = Array.isArray(err?.loc) ? err.loc.filter((p: unknown) => p !== "body") : [];
      return where.length ? `${where.join(" → ")}: ${err?.msg}` : String(err?.msg);
    });
    return messages.join("\n");
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError(`Can't reach the BlastRadius API at ${API_URL}. Is it running?`);
  }
  if (!response.ok) {
    let message = `The API returned ${response.status} ${response.statusText}`.trim();
    try {
      message = formatDetail((await response.json()).detail) ?? message;
    } catch {
      // Not JSON: keep the status message.
    }
    throw new ApiError(message);
  }
  return (await response.json()) as T;
}

export function getDemo(): Promise<AnalyzeRequest> {
  return request<AnalyzeRequest>("/demo");
}

export function analyze(body: AnalyzeRequest): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>("/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
