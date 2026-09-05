from __future__ import annotations

from datetime import datetime, timezone

from .config import MAINTENANCE_CALIBRATION_DAYS, MAINTENANCE_INSPECTION_DAYS, NODES
from .database import ensure_sensor_inventory, load_exposure_events, load_sensor_inventory, save_exposure_event
from .sensor_registry import SENSOR_SPECS


MODEL_PARAMETER = {"DS18B20": "temperature", "DHT22": "humidity", "BMP280": "pressure"}


class MaintenanceService:
    """Transparent heuristic maintenance priority; never an RUL prediction."""

    def __init__(self, engine):
        self.engine = engine
        self._last_exposure = {}
        now = datetime.now(timezone.utc).isoformat()
        ensure_sensor_inventory([
            {"sensor_id": f"{node.replace('_', '')}-{model}-01", "node_id": node, "model": model, "installed_at": now}
            for node in NODES for model in SENSOR_SPECS
        ])

    async def on_reading(self, reading):
        checks = []
        temperature = reading.get("temperature_c")
        humidity = reading.get("humidity_pct")
        if temperature is not None and temperature >= 80:
            checks.append(("extreme_heat", "high", {"temperature_c": temperature}))
        elif temperature is not None and temperature <= -40:
            checks.append(("extreme_cold", "high", {"temperature_c": temperature}))
        if humidity is not None and humidity >= 95:
            checks.append(("prolonged_high_humidity", "warning", {"humidity_pct": humidity}))
        for exposure_type, severity, details in checks:
            signature = (reading["node_id"], exposure_type)
            if self._last_exposure.get(signature) == reading.get("timestamp"):
                continue
            self._last_exposure[signature] = reading.get("timestamp")
            await self.record_exposure(reading["node_id"], exposure_type, severity, "sensor_measurement", details=details)

    async def record_exposure(self, node_id, exposure_type, severity, source_type, source_id=None, details=None):
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "node_id": node_id,
            "type": exposure_type, "severity": severity, "source_type": source_type,
            "source_id": source_id, "details": details or {},
        }
        event["id"] = save_exposure_event(event)
        await self.engine.publish("sensor_exposure", event)
        await self.engine.publish("maintenance_status", self.node_status(node_id))
        return event

    def inventory(self, node_id=None):
        return load_sensor_inventory(node_id)

    def sensor_status(self, record):
        parameter = MODEL_PARAMETER[record["model"]]
        health = round(self.engine.health[record["node_id"]][parameter], 1)
        exposures = [item for item in load_exposure_events(record["node_id"]) if item.get("sensor_id") in (None, record["sensor_id"])]
        risk = max(0, 100 - health)
        reasons = []
        severe = [item for item in exposures if item["severity"] in ("high", "critical")]
        if exposures:
            risk += min(15, len(exposures) * 5)
            reasons.append(f"{len(exposures)} recorded environmental or physical exposure event(s)")
        if severe:
            risk += min(30, len(severe) * 10)
            reasons.append(f"{len(severe)} exposure event(s) were high severity")
        if health < 90:
            reasons.append(f"Current {parameter} health score is {health}%")
        if record.get("last_inspection_at") is None:
            risk += 10
            reasons.append("No inspection date has been recorded")
        if record.get("last_calibration_at") is None:
            risk += 8
            reasons.append("No calibration date has been recorded")
        risk = min(100, round(risk))
        priority = "critical_inspection" if risk >= 75 else "inspection_recommended" if risk >= 55 else "inspect_soon" if risk >= 35 else "observe" if risk >= 18 else "normal"
        return {
            **record, "health_score": health, "maintenance_risk": risk,
            "maintenance_priority": priority, "reasons": reasons or ["No current maintenance risk indicators"],
            "recommended_action": "Inspect enclosure and verify sensor calibration." if priority not in ("normal", "observe") else "Continue scheduled observation.",
            "expected_service_life": SENSOR_SPECS[record["model"]]["expected_service_life"],
            "scoring_mode": "heuristic_datasheet_exposure",
            "inspection_interval_days": MAINTENANCE_INSPECTION_DAYS,
            "calibration_review_days": MAINTENANCE_CALIBRATION_DAYS,
        }

    def statuses(self, node_id=None):
        return [self.sensor_status(record) for record in self.inventory(node_id)]

    def node_status(self, node_id):
        statuses = self.statuses(node_id)
        return {"node_id": node_id, "sensors": statuses, "highest_priority": max(statuses, key=lambda item: item["maintenance_risk"])["maintenance_priority"]}
