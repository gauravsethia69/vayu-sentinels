import { WS_URL } from "./client";
import type { ConnectionStatus, WebSocketEnvelope } from "./types";

interface SocketCallbacks {
  onMessage: (message: WebSocketEnvelope) => void;
  onStatus: (status: ConnectionStatus) => void;
}

export class SkyGuardSocket {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private stopped = false;
  private reconnectAttempt = 0;
  private heartbeatTimer: number | null = null;
  private readonly callbacks: SocketCallbacks;

  constructor(callbacks: SocketCallbacks) {
    this.callbacks = callbacks;
  }

  connect() {
    if (this.stopped || this.socket?.readyState === WebSocket.OPEN || this.socket?.readyState === WebSocket.CONNECTING) {
      return;
    }

    this.callbacks.onStatus("connecting");
    const socket = new WebSocket(WS_URL);
    this.socket = socket;

    socket.onopen = () => {
      this.reconnectAttempt = 0;
      this.callbacks.onStatus("connected");
      this.startHeartbeat(socket);
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data as string) as WebSocketEnvelope;
        if (message && typeof message.type === "string" && "data" in message) {
          this.callbacks.onMessage(message);
        }
      } catch {
        // Ignore malformed frames while keeping the valid stream alive.
      }
    };
    socket.onerror = () => {
      this.callbacks.onStatus("reconnecting");
    };
    socket.onclose = () => {
      this.stopHeartbeat();
      if (this.socket === socket) this.socket = null;
      if (this.stopped) return;
      this.callbacks.onStatus("reconnecting");
      this.scheduleReconnect();
    };
  }
stop() {
  this.stopped = true;

  if (this.reconnectTimer !== null) {
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }
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


  private startHeartbeat(socket: WebSocket) {
    this.stopHeartbeat();
    this.heartbeatTimer = window.setInterval(() => {
      if (this.socket === socket && socket.readyState === WebSocket.OPEN) {
        try {
          socket.send(JSON.stringify({ type: "ping", ts: Date.now() }));
        } catch {
          // Reconnect handling will recover if the socket has gone away.
        }
      }
    }, 30_000);
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer !== null) {
      window.clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer !== null || this.stopped) return;
    const delay = Math.min(1_000 * 2 ** this.reconnectAttempt, 15_000);
    this.reconnectAttempt += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}
