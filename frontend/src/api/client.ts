import { getApiBaseUrl } from "./config";
export const AUTH_STORAGE_KEY = "skyguard_admin_session";

// Keep every REST request bounded. On weak networks a request that never settles
// must not hold the dashboard refresh lock forever.
const REQUEST_TIMEOUT_MS = 7_000;

export function getAdminToken() {
  try { return JSON.parse(sessionStorage.getItem(AUTH_STORAGE_KEY) || "null")?.token as string | undefined; }
  catch { return undefined; }
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number, public kind: "http" | "network" | "timeout" | "aborted" | "json" = "http") {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAdminToken();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort("request-timeout"), REQUEST_TIMEOUT_MS);

  const externalSignal = init.signal;
  const forwardAbort = () => controller.abort(externalSignal?.reason);
  if (externalSignal) {
    if (externalSignal.aborted) forwardAbort();
    else externalSignal.addEventListener("abort", forwardAbort, { once: true });
  }

  try {
    const API_BASE_URL = getApiBaseUrl();
    const response = await fetch(`${API_BASE_URL}/${path.replace(/^\/+/, "")}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });

    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const body = (await response.json()) as { detail?: string | Array<{ msg?: string }> };
        if (typeof body.detail === "string") message = body.detail;
        if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).filter(Boolean).join(", ");
      } catch {
        // The status text is the safest fallback for a non-JSON response.
      }
      throw new ApiError(message, response.status);
    }

    if (response.status === 204) return undefined as T;
    try {
      return (await response.json()) as T;
    } catch (cause) {
      if (controller.signal.aborted) throw cause;
      throw new ApiError("Backend returned invalid JSON.", response.status, "json");
    }
  } catch (cause) {
    if (controller.signal.aborted && !externalSignal?.aborted) {
      throw new ApiError("Request timed out. Check backend reachability.", 0, "timeout");
    }
    if (externalSignal?.aborted) throw new ApiError("Request cancelled.", 0, "aborted");
    if (cause instanceof ApiError) throw cause;
    throw new ApiError(`Backend request failed: ${cause instanceof Error ? cause.message : "network failure"}`, 0, "network");
  } finally {
    window.clearTimeout(timeout);
    externalSignal?.removeEventListener("abort", forwardAbort);
  }
}

export function postJson<T>(path: string, payload?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

export function patchJson<T>(path: string, payload: unknown): Promise<T> {
  return request<T>(path, { method: "PATCH", body: JSON.stringify(payload) });
}
