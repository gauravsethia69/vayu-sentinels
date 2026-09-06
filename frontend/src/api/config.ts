export interface RuntimeEnvironment {
  VITE_API_BASE_URL?: string;
  VITE_WS_BASE_URL?: string;
  VITE_WS_URL?: string;
}

function baseUrl(value: string, protocols: string[]) {
  const url = new URL(value.trim());
  if (!protocols.includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error(`Invalid SkyGuard connection URL: ${value}`);
  }
  return url.toString().replace(/\/+$/, "");
}

export function getApiBaseUrl(env: RuntimeEnvironment = import.meta.env) {
  return baseUrl(env.VITE_API_BASE_URL?.trim() || "http://127.0.0.1:8000", ["http:", "https:"]);
}

export function resolveRuntime(env: RuntimeEnvironment) {
  const apiBase = getApiBaseUrl(env);
  const derived = apiBase.replace(/^http/, "ws");
  const wsBase = baseUrl(env.VITE_WS_BASE_URL?.trim() || derived, ["ws:", "wss:"]);
  // Keep the previous full-URL variable compatible with deployed builds.
  const wsUrl = env.VITE_WS_BASE_URL?.trim()
    ? `${wsBase}/ws/live`
    : env.VITE_WS_URL?.trim()
      ? baseUrl(env.VITE_WS_URL, ["ws:", "wss:"])
      : `${wsBase}/ws/live`;
  return { apiBase, wsUrl };
}

export function getWebSocketUrl() {
  return resolveRuntime(import.meta.env).wsUrl;
}
