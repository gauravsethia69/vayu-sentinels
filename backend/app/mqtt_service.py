import asyncio
import json
import logging
import ssl
import threading
from datetime import datetime, timezone

try:
    import paho.mqtt.client as mqtt
except ImportError:  # Optional at import time so REST/tests can still run.
    mqtt = None


logger = logging.getLogger(__name__)


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
    ):
        self.processor = processor
        self.host = host
        self.port = port
        self.topic = topic
        self.username = username or ""
        self.password = password or ""
        self.tls = bool(tls)
        self.loop = None
        self.connected = False
        self.last_connected_at = None
        self.last_message_at = None
        self.messages_received = 0
        self.messages_rejected = 0
        self.messages_dropped_overload = 0
        self.pending_processing = 0
        self.max_pending_processing = 24
        self._pending_lock = threading.Lock()
        self.available = mqtt is not None
        self.client = None

        if not self.available:
            logger.warning("paho-mqtt is not installed; MQTT transport is disabled until dependencies are installed")
            return

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="skyguard-backend",
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)

        if self.username:
            self.client.username_pw_set(self.username, self.password)

        if self.tls:
            # Render/Linux system CA store validates HiveMQ Cloud's public certificate.
            tls_context = ssl.create_default_context()
            self.client.tls_set_context(tls_context)

    def start(self, loop):
        self.loop = loop
        if not self.available or self.client is None:
            logger.warning("MQTT start skipped because paho-mqtt is unavailable")
            return

        logger.info("Starting MQTT client → %s:%s (TLS=%s, auth=%s)", self.host, self.port, self.tls, bool(self.username))
        self.client.connect_async(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        if not self.available or self.client is None:
            return
        logger.info("Stopping MQTT client")
        try:
            self.client.disconnect()
        finally:
            self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected = True
            self.last_connected_at = datetime.now(timezone.utc).isoformat()
            client.subscribe(self.topic, qos=1)
            logger.info("MQTT connected. Subscribed to %s", self.topic)
        else:
            self.connected = False
            logger.warning("MQTT connection failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.connected = False
        logger.warning("MQTT disconnected: %s", reason_code)

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

            # Never create an unbounded number of asyncio futures if the small
            # cloud instance becomes temporarily slow.  Three AWS nodes publish
            # continuously, so a stuck browser/socket must not be able to grow
            # memory until Render becomes unresponsive.
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
            "messages_received": self.messages_received,
            "messages_rejected": self.messages_rejected,
            "messages_dropped_overload": self.messages_dropped_overload,
            "pending_processing": self.pending_processing,
            "max_pending_processing": self.max_pending_processing,
            "last_connected_at": self.last_connected_at,
            "last_message_at": self.last_message_at,
            "dependency_error": None if self.available else "paho-mqtt not installed",
        }
