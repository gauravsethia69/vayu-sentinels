import { api } from "./endpoints";
import type { HealthResponse } from "./types";

export type BackendStatus = "checking" | "online" | "offline";
export const HEALTH_ONLINE_MS = 20_000;
export const HEALTH_OFFLINE_MS = 4_000;

/** One serial health loop. Dashboard queries and socket traffic cannot set it. */
export class HealthMonitor {
  private stopped = true;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private pending: Promise<HealthResponse | null> | undefined;
  private controller: AbortController | undefined;

  constructor(
    private report: (status: BackendStatus, health: HealthResponse | null, error: string | null) => void,
    private check = api.getHealth,
  ) {}

  start() { this.stopped = false; return this.probe(); }
  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
    this.controller?.abort();
  }

  probe(): Promise<HealthResponse | null> {
    if (this.stopped) return Promise.resolve(null);
    if (this.pending) return this.pending;
    clearTimeout(this.timer);
    this.controller = new AbortController();
    this.pending = (async () => {
      let health: HealthResponse | null = null;
      let error: string | null = null;
      try {
        health = await this.check(this.controller!.signal);
        if (health?.status !== "ok") throw new Error("Health response did not report status ok.");
      } catch (cause) {
        health = null;
        error = cause instanceof Error ? cause.message : "Backend health unavailable";
      }
      if (!this.stopped) {
        if (import.meta.env.DEV) console.debug(`[SkyGuard API] health ${health ? "success" : "failed"}`, error ?? "");
        this.report(health ? "online" : "offline", health, error);
        this.timer = setTimeout(() => void this.probe(), health ? HEALTH_ONLINE_MS : HEALTH_OFFLINE_MS);
      }
      return health;
    })().finally(() => { this.pending = undefined; });
    return this.pending;
  }
}
