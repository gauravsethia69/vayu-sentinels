import asyncio
import json
import logging
import os
import socket
import ssl
import threading
import uuid
from datetime import datetime, timezone

try:
    import paho.mqtt.client as mqtt
except ImportError:  # Optional at import time so REST/tests can still run.
    mqtt = None


logger = logging.getLogger(__name__)


def _safe_client_id(value: str | None = None) -> str:
    """Return a safe unique MQTT client id.

    HiveMQ disconnects clients that reuse the same client id. This matters when
    Render and a local backend are running at the same time.
    """
    if value and value.strip():
        return value.strip()[:120]
    hostname = socket.gethostname().replace(" ", "-")[:32]
    short = uuid.uuid4().hex[:8]
    return f"skyguard-backend-{hostname}-{short}"[:120]


class MQTTService:
    def __init__(
        self,
        processor,
        host="127.0.0.1",
        port=1883,
        topic="skyguard/aws/+/telemetry",
        username="",
        password="",
        tls=False,
        client_id="",
        keepalive=30,
    ):
        self.processor = processor
        self.host = host
        self.port = int(port)
        self.topic = topic
        self.username = username or ""
        self.password = password or ""
        self.tls = bool(tls)
        self.client_id = _safe_client_id(client_id or os.getenv("SKYGUARD_MQTT_CLIENT_ID", ""))
        self.keepalive = int(keepalive or 30)
        self.loop = None
        self.connected = False
        self.last_connected_at = None
        self.last_disconnected_at = None
        self.last_message_at = None
        self.last_disconnect_reason = None
        self.messages_received = 0
        self.messages_rejected = 0
        self.messages_dropped_overload = 0
        self.pending_processing = 0
        self.max_pending_processing = 24
        self._pending_lock = threading.Lock()
        self.available = mqtt is not None
        self.client = None
        self.startup_error = None
        self._started = False

        if not self.available:
            logger.warning("paho-mqtt is not installed; MQTT transport is disabled until dependencies are installed")
            return

        try:
            self._configure_client()
        except Exception as exc:
            self.startup_error = f"{type(exc).__name__}: {exc}"
            self.client = None
            logger.exception("Optional MQTT client configuration failed; REST remains available")

    def _configure_client(self):
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            clean_session=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=2, max_delay=30)

        if self.username:
            self.client.username_pw_set(self.username, self.password)

        if self.tls:
            tls_context = ssl.create_default_context()
            self.client.tls_set_context(tls_context)
            # Required when using HiveMQ Cloud over port 8883.
            try:
                self.client.tls_insecure_set(False)
            except Exception:
                pass

    def start(self, loop):
        self.loop = loop
        if not self.available or self.client is None:
            logger.warning("MQTT start skipped because paho-mqtt is unavailable")
            return

        if self._started:
            return

        logger.info(
            "Starting MQTT client → %s:%s (TLS=%s, auth=%s, client_id=%s)",
            self.host,
            self.port,
            self.tls,
            bool(self.username),
            self.client_id,
        )
        try:
            self.client.connect_async(self.host, self.port, keepalive=self.keepalive)
            self.client.loop_start()
            self._started = True
            self.startup_error = None
        except Exception as exc:
            self.connected = False
            self.startup_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Optional MQTT startup failed; REST remains available")

    def stop(self):
        if not self.available or self.client is None:
            return
        logger.info("Stopping MQTT client")
        try:
            self.client.disconnect()
        finally:
            self.client.loop_stop()
            self.connected = False
            self._started = False

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected = True
            self.last_disconnect_reason = None
            self.last_connected_at = datetime.now(timezone.utc).isoformat()
            client.subscribe(self.topic, qos=1)
            logger.info("MQTT connected. Subscribed to %s", self.topic)
        else:
            self.connected = False
            self.last_disconnect_reason = str(reason_code)
            logger.warning("MQTT connection failed: %s", reason_code)

    def _on_connect_fail(self, client, userdata):
        self.connected = False
        self.last_disconnect_reason = "connect_fail"
        logger.warning("MQTT broker %s:%s unavailable; background retry continues", self.host, self.port)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.connected = False
        self.last_disconnected_at = datetime.now(timezone.utc).isoformat()
        self.last_disconnect_reason = str(reason_code)
        logger.warning("MQTT disconnected: %s; background reconnect continues", reason_code)

    def _on_message(self, client, userdata, message):
        self.messages_received += 1
        self.last_message_at = datetime.now(timezone.utc).isoformat()

        try:
            raw = message.payload.decode("utf-8")
            payload = json.loads(raw)
            parts = message.topic.split("/")
            if len(parts) != 4:
                raise ValueError(f"Unexpected MQTT topic: {message.topic}")

            topic_node = parts[2]
            payload_node = payload.get("node_id")
            if payload_node != topic_node:
                raise ValueError(f"Topic node {topic_node} does not match payload node {payload_node}")

            if self.loop is None:
                raise RuntimeError("FastAPI event loop unavailable")

            with self._pending_lock:
                if self.pending_processing >= self.max_pending_processing:
                    self.messages_dropped_overload += 1
                    logger.warning(
                        "Dropping MQTT telemetry because processing backlog is full (%s pending)",
                        self.pending_processing,
                    )
                    return
                self.pending_processing += 1

            try:
                future = asyncio.run_coroutine_threadsafe(self.processor(payload), self.loop)
            except Exception:
                with self._pending_lock:
                    self.pending_processing = max(0, self.pending_processing - 1)
                raise
            future.add_done_callback(self._processing_done)
        except Exception:
            self.messages_rejected += 1
            logger.exception("Rejected MQTT message from %s", message.topic)

    def _processing_done(self, future):
        try:
            future.result()
        except Exception:
            self.messages_rejected += 1
            logger.exception("MQTT telemetry processing failed")
        finally:
            with self._pending_lock:
                self.pending_processing = max(0, self.pending_processing - 1)

    def status(self):
        return {
            "enabled": self.available,
            "connected": self.connected,
            "broker": f"{self.host}:{self.port}",
            "topic": self.topic,
            "tls": self.tls,
            "authenticated": bool(self.username),
            "client_id": self.client_id,
            "keepalive_seconds": self.keepalive,
            "messages_received": self.messages_received,
            "messages_rejected": self.messages_rejected,
            "messages_dropped_overload": self.messages_dropped_overload,
            "pending_processing": self.pending_processing,
            "max_pending_processing": self.max_pending_processing,
            "last_connected_at": self.last_connected_at,
            "last_disconnected_at": self.last_disconnected_at,
            "last_disconnect_reason": self.last_disconnect_reason,
            "last_message_at": self.last_message_at,
            "dependency_error": None if self.available else "paho-mqtt not installed",
            "startup_error": self.startup_error,
        }
