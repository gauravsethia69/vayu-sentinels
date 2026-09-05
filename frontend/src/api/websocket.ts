import { WS_URL } from "./client";
import type { ConnectionStatus, WebSocketEnvelope } from "./types";

interface SocketCallbacks {
  onMessage: (message: WebSocketEnvelope) => void;
  onStatus: (status: ConnectionStatus) => void;
}

const HEARTBEAT_MS = 30_000;
const STALE_CONNECTION_MS = 70_000;
const CONNECT_TIMEOUT_MS = 10_000;
const MAX_RECONNECT_MS = 30_000;

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
    if (this.stopped || this.socket?.readyState === WebSocket.OPEN || this.socket?.readyState === WebSocket.CONNECTING) {
      return;
    }

    this.callbacks.onStatus("connecting");
    const socket = new WebSocket(WS_URL);
    this.socket = socket;
    this.lastInboundAt = Date.now();
    this.startConnectTimeout(socket);

    socket.onopen = () => {
      this.clearConnectTimeout();
      this.reconnectAttempt = 0;
      this.lastInboundAt = Date.now();
      this.callbacks.onStatus("connected");
      this.startHeartbeat(socket);
    };

    socket.onmessage = (event) => {
      this.lastInboundAt = Date.now();
      try {
        const message = JSON.parse(event.data as string) as WebSocketEnvelope;
        if (!message || typeof message.type !== "string") return;

        // Pong is transport health, not dashboard state.
        if (message.type === "pong") return;

        if ("data" in message) this.callbacks.onMessage(message);
      } catch {
        // Ignore malformed frames while keeping the valid stream alive.
      }
    };

    socket.onerror = () => {
      this.callbacks.onStatus("reconnecting");
    };

    socket.onclose = () => {
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
    const jitter = 0.8 + Math.random() * 0.4;
    const delay = Math.round(base * jitter);
    this.reconnectAttempt += 1;

    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}
