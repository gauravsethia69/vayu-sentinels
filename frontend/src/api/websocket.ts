import { getWebSocketUrl } from "./config";
import type { ConnectionStatus, WebSocketEnvelope } from "./types";

interface SocketCallbacks {
  onMessage: (message: WebSocketEnvelope) => void;
  onStatus: (status: ConnectionStatus) => void;
}

const HEARTBEAT_MS = 30_000;
const STALE_CONNECTION_MS = 70_000;
const CONNECT_TIMEOUT_MS = 10_000;
const MAX_RECONNECT_MS = 10_000;

const eventTypes = new Set([
  "system_status", "sensor_reading", "trusted_reading", "anomaly", "sensor_health",
  "communication_status", "sensor_consistency", "recovery", "field_report_created",
  "field_report_updated", "field_report_resolved", "field_report_expired", "field_report_corroboration",
  "node_monitoring_context", "public_report_created", "public_report_updated", "public_report_verified",
  "public_report_resolved", "public_report_corroboration", "vision_event", "vision_status",
  "peer_failover_started", "peer_failover_updated", "peer_failover_ended", "maintenance_status",
  "sensor_exposure", "multi_source_context", "pong",
]);
const nodeIds = new Set(["AWS_001", "AWS_002", "AWS_003"]);
const object = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));

export function validEnvelope(value: unknown): value is WebSocketEnvelope {
  if (!object(value) || typeof value.type !== "string" || !eventTypes.has(value.type) || !object(value.data)) return false;
  const data = value.data;
  if (value.type === "sensor_reading") return nodeIds.has(String(data.node_id)) && typeof data.timestamp === "string";
  if (value.type === "trusted_reading") return object(data.raw) && object(data.trusted) && nodeIds.has(String(data.raw.node_id)) && typeof data.raw.timestamp === "string";
  if (["sensor_health", "communication_status", "node_monitoring_context", "vision_event", "vision_status", "maintenance_status"].includes(value.type) && !nodeIds.has(String(data.node_id))) return false;
  if (value.type.startsWith("field_report_")) return typeof data.id === "string" && typeof data.category === "string" && Array.isArray(data.nearby_stations);
  if (value.type === "maintenance_status") return Array.isArray(data.sensors);
  return true;
}

export class SkyGuardSocket {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private connectTimeout: number | null = null;
  private stopped = false;
  private reconnectAttempt = 0;
  private heartbeatTimer: number | null = null;
  private lastInboundAt = 0;
  private readonly callbacks: SocketCallbacks;

  constructor(callbacks: SocketCallbacks) {
    this.callbacks = callbacks;
    window.addEventListener("online", this.handleOnline);
    window.addEventListener("offline", this.handleOffline);
  }

  connect() {
    if (this.stopped || this.socket !== null) {
      return;
    }

    this.clearReconnect();
    this.callbacks.onStatus(this.reconnectAttempt ? "reconnecting" : "connecting");
    let socket: WebSocket;
    try {
      socket = new WebSocket(getWebSocketUrl());
    } catch (error) {
      if (import.meta.env.DEV) console.warn("[SkyGuard WS] connection failed", error);
      this.callbacks.onStatus("reconnecting");
      this.scheduleReconnect();
      return;
    }
    if (import.meta.env.DEV) console.debug("[SkyGuard WS] connecting");
    this.socket = socket;
    this.lastInboundAt = Date.now();
    this.startConnectTimeout(socket);

    socket.onopen = () => {
      if (this.stopped || this.socket !== socket || socket.readyState !== WebSocket.OPEN) return;
      this.clearConnectTimeout();
      this.reconnectAttempt = 0;
      this.lastInboundAt = Date.now();
      this.callbacks.onStatus("connected");
      this.startHeartbeat(socket);
    };

    socket.onmessage = (event) => {
      if (this.stopped || this.socket !== socket) return;
      this.lastInboundAt = Date.now();
      try {
        const message: unknown = JSON.parse(event.data as string);
        if (!validEnvelope(message)) {
          if (import.meta.env.DEV) console.debug("[SkyGuard WS] ignored unknown or malformed envelope");
          return;
        }

        // Pong is transport health, not dashboard state.
        if (message.type === "pong") return;

        if ("data" in message) this.callbacks.onMessage(message);
      } catch (error) {
        if (import.meta.env.DEV) console.warn("[SkyGuard WS] rejected frame", error);
      }
    };

    socket.onerror = () => {
      if (this.stopped || this.socket !== socket) return;
      this.callbacks.onStatus("reconnecting");
    };

    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.clearConnectTimeout();
      this.stopHeartbeat();
      if (this.socket === socket) this.socket = null;
      if (this.stopped) return;
      this.callbacks.onStatus("reconnecting");
      this.scheduleReconnect();
    };
  }

  stop() {
    this.stopped = true;
    window.removeEventListener("online", this.handleOnline);
    window.removeEventListener("offline", this.handleOffline);

    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.clearConnectTimeout();
    this.stopHeartbeat();

    const socket = this.socket;
    this.socket = null;

    if (!socket) return;

    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;

    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      try {
        socket.close(1000, "dashboard unmounted");
      } catch {
        // Some browser implementations reject close() during the connecting state.
      }
    }
  }

  resume() {
    if (this.stopped) return;
    if (this.socket?.readyState === WebSocket.OPEN && Date.now() - this.lastInboundAt > STALE_CONNECTION_MS) {
      this.callbacks.onStatus("reconnecting");
      this.socket.close(4000, "resume stale connection");
      return;
    }
    this.handleOnline();
  }

  private clearReconnect() {
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private readonly handleOnline = () => {
    if (this.stopped) return;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.connect();
  };

  private readonly handleOffline = () => {
    if (this.stopped) return;
    this.callbacks.onStatus("reconnecting");
    const socket = this.socket;
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      try { socket.close(4001, "network offline"); } catch { /* reconnect loop will recover */ }
    }
  };

  private startConnectTimeout(socket: WebSocket) {
    this.clearConnectTimeout();
    this.connectTimeout = window.setTimeout(() => {
      if (this.socket !== socket || socket.readyState !== WebSocket.CONNECTING) return;
      try { socket.close(); } catch { /* onclose/reconnect handles recovery */ }
    }, CONNECT_TIMEOUT_MS);
  }

  private clearConnectTimeout() {
    if (this.connectTimeout !== null) {
      window.clearTimeout(this.connectTimeout);
      this.connectTimeout = null;
    }
  }

  private startHeartbeat(socket: WebSocket) {
    this.stopHeartbeat();
    this.heartbeatTimer = window.setInterval(() => {
      if (this.socket !== socket || socket.readyState !== WebSocket.OPEN) return;

      // A half-open WebSocket must not remain "connected" forever on unstable Wi-Fi.
      if (Date.now() - this.lastInboundAt > STALE_CONNECTION_MS) {
        try { socket.close(4000, "stale connection"); } catch { /* reconnect handles recovery */ }
        return;
      }

      try {
        socket.send(JSON.stringify({ type: "ping", ts: Date.now() }));
      } catch {
        try { socket.close(); } catch { /* reconnect handles recovery */ }
      }
    }, HEARTBEAT_MS);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer !== null) {
      window.clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer !== null || this.stopped) return;

    // Exponential backoff with jitter prevents a reconnect storm when the
    // network returns after an outage.
    const base = Math.min(1_000 * 2 ** this.reconnectAttempt, MAX_RECONNECT_MS);
    const delay = base;
    if (import.meta.env.DEV) console.debug(`[SkyGuard WS] reconnecting in ${delay}ms`);
    this.reconnectAttempt += 1;

    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}
