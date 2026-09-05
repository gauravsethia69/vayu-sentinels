from __future__ import annotations

from datetime import datetime, timezone

from .config import NODES, VISION_EVENT_SEVERITY, VISION_MODE
from .database import load_vision_events, save_vision_event


PHYSICAL_TYPES = {"bird_detected", "animal_detected", "person_near_station", "foreign_object", "station_obstruction", "camera_obstruction", "vegetation_interference", "possible_station_tilt", "possible_station_damage"}
EXPOSURE_TYPES = {"bright_direct_sun", "strong_wind_visual", "heavy_rain_visual", "station_obstruction", "camera_obstruction", "possible_station_damage"}


class VisionDetector:
    mode = "observation_only"

    def normalize(self, observation):
        raise NotImplementedError


class ManualVisionDetector(VisionDetector):
    mode = "manual_observation"

    def normalize(self, observation):
        return observation


class SimulatedVisionDetector(ManualVisionDetector):
    mode = "simulated"


class VisionService:
    """Stores supplied visual observations; no image classifier is claimed or run."""

    def __init__(self, engine, maintenance):
        self.engine = engine
        self.maintenance = maintenance

    async def observe(self, payload):
        detector = SimulatedVisionDetector() if payload["source"] == "simulated_camera" else ManualVisionDetector()
        timestamp = payload.get("timestamp") or datetime.now(timezone.utc)
        if hasattr(timestamp, "isoformat"):
            timestamp = timestamp.isoformat()
        results = []
        for detection in payload["detections"]:
            detection = detector.normalize(detection)
            event = {
                "node_id": payload["node_id"], "timestamp": timestamp, "source": payload["source"],
                "vision_mode": detector.mode, "type": detection["type"], "confidence": detection["confidence"],
                "severity": VISION_EVENT_SEVERITY.get(detection["type"], "info"),
                "event_group": "physical_risk" if detection["type"] in PHYSICAL_TYPES else "weather_context",
                "message": self._message(detection["type"]),
                "recommended_action": "Inspect the station if sensor readings become inconsistent." if detection["type"] in PHYSICAL_TYPES else "Compare with nearby sensor and community evidence.",
            }
            event["id"] = save_vision_event(event)
            results.append(event)
            await self.engine.publish("vision_event", event)
            if detection["type"] in EXPOSURE_TYPES and detection["confidence"] >= 0.65:
                await self.maintenance.record_exposure(payload["node_id"], detection["type"], event["severity"], "camera_observation", str(event["id"]), {"vision_mode": detector.mode, "confidence": detection["confidence"]})
        await self.engine.publish("vision_status", self.status(payload["node_id"])[0])
        await self.engine.publish("multi_source_context", self.context(payload["node_id"]))
        return results

    def _message(self, detection_type):
        if detection_type in PHYSICAL_TYPES:
            return "Physical presence or interference was visually reported; sensors remain independently evaluated."
        return "Visual weather context recorded; sensor measurements remain unchanged."

    def events(self, node_id=None, limit=100):
        return load_vision_events(limit, node_id)

    def status(self, node_id=None):
        targets = (node_id,) if node_id else NODES
        result = []
        for node in targets:
            events = self.events(node, 20)
            latest = events[0] if events else None
            result.append({
                "node_id": node, "camera_status": "online" if latest else "offline",
                "vision_mode": latest["vision_mode"] if latest else VISION_MODE,
                "latest_analysis": latest, "last_observation_at": latest["timestamp"] if latest else None,
            })
        return result

    def context(self, node_id):
        events = self.events(node_id, 10)
        reading = self.engine.hist[node_id][-1] if self.engine.hist[node_id] else None
        reports = self.engine.context_service.context(node_id)["active_context"] if self.engine.context_service else []
        return {
            "node_id": node_id, "sources": {
                "sensor": {"available": reading is not None, "latest": reading},
                "peer_station": {"healthy_peers": [item["node_id"] for item in self.engine.get_healthy_peers(node_id)]},
                "camera": {"observations": events},
                "public_report": {"active_context": reports},
                "datasheet": {"mode": "verified_specification_registry"},
            },
            "classification": "context_only", "detector_mode": self.engine.detector.mode,
            "message": "Independent evidence sources are correlated without becoming sensor ground truth.",
        }
