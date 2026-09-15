import { api } from "./endpoints";
import type { HealthResponse } from "./types";

export type BackendStatus = "checking" | "online" | "offline";

export const HEALTH_ONLINE_MS = 30_000;
export const HEALTH_OFFLINE_MS = 4_000;

type HealthReporter = (
  status: BackendStatus,
  health?: HealthResponse | null,
  error?: string | null
) => void;

export class HealthMonitor {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;
  private controller: AbortController | null = null;

  constructor(private readonly report: HealthReporter) {}

  start() {
    this.stopped = false;
    void this.probe();
  }

  stop() {
    this.stopped = true;

    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }

    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }

  async probe() {
    if (this.stopped) return;

    this.controller?.abort();
    this.controller = new AbortController();

    let status: BackendStatus = "offline";
    let health: HealthResponse | null = null;
    let error: string | null = null;

    try {
      // Fast backend-online check.
      await api.getPing(this.controller.signal);
      status = "online";

      // Optional detailed health. Failure here must not mark backend offline.
      try {
        health = await api.getHealth(this.controller.signal);
      } catch {
        health = null;
      }
    } catch (cause) {
      status = "offline";
      error = cause instanceof Error ? cause.message : "Backend unavailable";
    }

    this.report(status, health, error);

    if (!this.stopped) {
      this.timer = setTimeout(
        () => void this.probe(),
        status === "online" ? HEALTH_ONLINE_MS : HEALTH_OFFLINE_MS
      );
    }
  }
}
