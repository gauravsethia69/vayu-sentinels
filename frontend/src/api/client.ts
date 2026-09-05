const defaultApiBase = "http://127.0.0.1:8000";
const defaultWebSocketBase = "ws://127.0.0.1:8000";

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || defaultApiBase).replace(/\/$/, "");
const legacyWebSocketBase = (import.meta.env.VITE_WS_BASE_URL || defaultWebSocketBase).replace(/\/$/, "");
export const WS_URL = (import.meta.env.VITE_WS_URL || `${legacyWebSocketBase}/ws/live`).replace(/\/$/, "");
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

  constructor(message: string, status: number) {
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
    const response = await fetch(`${API_BASE_URL}${path}`, {
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

    return (await response.json()) as T;
  } catch (cause) {
    if (controller.signal.aborted && !externalSignal?.aborted) {
      throw new ApiError("Request timed out. Retrying over the live connection.", 0);
    }
    throw cause;
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
