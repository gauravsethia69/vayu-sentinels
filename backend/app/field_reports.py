from __future__ import annotations

import asyncio
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from .config import (
    FIELD_REPORT_BASELINE_SAMPLE_INTERVAL_SECONDS,
    FIELD_REPORT_DEFAULT_EXPIRY_MINUTES,
    FIELD_REPORT_HEIGHTENED_SAMPLE_INTERVAL_SECONDS,
    FIELD_REPORT_PRIORITY_SAMPLE_INTERVAL_SECONDS,
    FIELD_REPORT_STATION_CATEGORIES,
    FIELD_REPORT_WEATHER_CATEGORIES,
    NODES,
    PROTOTYPE_CLUSTER_ID,
    STATION_LOCATIONS,
)
from .database import load_field_report, load_field_reports, save_field_report, save_public_report_action


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def haversine_km(latitude_one, longitude_one, latitude_two, longitude_two):
    radius = 6371.0088
    lat_one, lat_two = math.radians(latitude_one), math.radians(latitude_two)
    delta_lat = lat_two - lat_one
    delta_lon = math.radians(longitude_two - longitude_one)
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat_one) * math.cos(lat_two) * math.sin(delta_lon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


class FieldReportService:
    """Contextual field evidence kept deliberately separate from anomaly detection."""

    def __init__(self, engine):
        self.engine = engine
        self._lock = asyncio.Lock()

    def _next_id(self, now):
        prefix = f"FR-{now.year}-"
        numbers = []
        for report in load_field_reports():
            if report["id"].startswith(prefix):
                try:
                    numbers.append(int(report["id"].removeprefix(prefix)))
                except ValueError:
                    pass
        return f"{prefix}{max(numbers, default=0) + 1:05d}"

    def _public_report(self, report):
        result = deepcopy(report)
        result["nearby_stations"] = self.matching_nodes(report)
        result["reporter_confidence"] = report["reporter_confidence"]
        return result

    def public_view(self, report):
        result = self._public_report(report)
        result["reporter_name"] = None
        result["reporter_type"] = "community_user"
        return result

    async def _publish_public_alias(self, event_type, report):
        if report.get("source") == "public_user":
            await self.engine.publish(event_type, self.public_view(report))

    def matching_nodes(self, report, radius_override=None):
        matches = []
        radius = min(float(report["radius_km"]), float(radius_override)) if radius_override else float(report["radius_km"])
        for node in NODES:
            station = STATION_LOCATIONS[node]
            distance = None
            relevant = False
            if report.get("latitude") is not None and report.get("longitude") is not None:
                distance = haversine_km(
                    report["latitude"], report["longitude"], station["latitude"], station["longitude"]
                )
                relevant = distance <= radius
            elif report.get("station_id"):
                relevant = report["station_id"] == node
            else:
                cluster_id = report.get("cluster_id") or PROTOTYPE_CLUSTER_ID
                relevant = station["cluster_id"] == cluster_id
            if relevant:
                matches.append(
                    {
                        "node_id": node,
                        "distance_km": round(distance, 2) if distance is not None else None,
                        "location_label": station["location_label"],
                    }
                )
        return matches

    async def create(self, payload):
        async with self._lock:
            now = utc_now()
            until_resolved = payload.pop("until_resolved", False)
            expiry_minutes = payload.pop("expires_in_minutes", None)
            explicit_expiry = payload.pop("expires_at", None)
            if explicit_expiry and explicit_expiry.tzinfo is None:
                explicit_expiry = explicit_expiry.replace(tzinfo=timezone.utc)
            default_minutes = FIELD_REPORT_DEFAULT_EXPIRY_MINUTES.get(payload["category"])
            expires_at = None if until_resolved else explicit_expiry or (
                now + timedelta(minutes=expiry_minutes or default_minutes) if expiry_minutes or default_minutes else None
            )
            report = {
                **payload,
                "id": self._next_id(now),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "active",
                "expires_at": expires_at.isoformat() if expires_at else None,
                "verification_state": "pending_sensor_confirmation",
                "corroboration_confidence": 0,
                "verified_by_nodes": [],
                "evidence": [],
                "contradicting_evidence": [],
                "message": "Field report received. Sensor confirmation has not yet been detected.",
                "resolved_at": None,
                "moderation_state": None,
            }
            save_field_report(report)
            report = self._evaluate_and_save(report)
        public = self._public_report(report)
        await self.engine.publish("field_report_created", public)
        await self._publish_public_alias("public_report_created", public)
        await self._publish_contexts(public)
        return public

    async def update(self, report_id, changes):
        async with self._lock:
            report = load_field_report(report_id)
            if report is None:
                return None
            report.update({key: value for key, value in changes.items() if value is not None})
            report["updated_at"] = utc_now().isoformat()
            if report.get("status") == "resolved":
                report["resolved_at"] = report["updated_at"]
            save_field_report(report)
            report = self._evaluate_and_save(report)
        public = self._public_report(report)
        event_type = "field_report_resolved" if report["status"] == "resolved" else "field_report_updated"
        await self.engine.publish(event_type, public)
        await self._publish_public_alias(
            "public_report_resolved" if report["status"] == "resolved" else "public_report_updated", public
        )
        await self._publish_contexts(public)
        return public

    async def resolve(self, report_id, notes=None):
        return await self.update(report_id, {"status": "resolved", "notes": notes})

    async def refresh_expiry(self):
        expired = []
        async with self._lock:
            now = utc_now()
            for report in load_field_reports():
                expires_at = parse_time(report.get("expires_at"))
                if report["status"] == "active" and expires_at and expires_at <= now:
                    report["status"] = "expired"
                    report["verification_state"] = "expired"
                    report["updated_at"] = now.isoformat()
                    report["message"] = "Field report expired and is no longer active monitoring context."
                    save_field_report(report)
                    expired.append(self._public_report(report))
        for report in expired:
            await self.engine.publish("field_report_expired", report)
            await self._publish_public_alias("public_report_updated", report)
            await self._publish_contexts(report)
        return expired

    async def get(self, report_id):
        await self.refresh_expiry()
        report = load_field_report(report_id)
        return self._public_report(report) if report else None

    async def list(self, status=None, category=None, station_id=None, severity=None, since=None):
        await self.refresh_expiry()
        reports = []
        since_time = parse_time(since) if since else None
        for report in load_field_reports():
            if status and report["status"] != status:
                continue
            if category and report["category"] != category:
                continue
            if severity and report["severity"] != severity:
                continue
            if since_time and parse_time(report["created_at"]) < since_time:
                continue
            public = self._public_report(report)
            if station_id and station_id not in {item["node_id"] for item in public["nearby_stations"]}:
                continue
            reports.append(public)
        return reports

    async def active(self):
        return await self.list(status="active")

    async def public_reports(self, status=None):
        reports = await self.list(status=status)
        return [self.public_view(report) for report in reports if report.get("source") == "public_user"]

    async def moderate(self, report_id, action, admin_name, notes=None):
        async with self._lock:
            report = load_field_report(report_id)
            if report is None or report.get("source") != "public_user":
                return None
            now = utc_now().isoformat()
            if action == "verify":
                report["moderation_state"] = "verified"
                report["verification_state"] = "manager_verified"
                report["message"] = "A SkyGuard administrator verified this community observation."
            elif action == "monitor":
                report["moderation_state"] = None
                report["verification_state"] = "pending_sensor_confirmation"
                report["message"] = "The report remains under monitoring while sensor evidence develops."
            elif action == "reject":
                report["moderation_state"] = "rejected"
                report["verification_state"] = "not_supported"
                report["message"] = "The observation is not currently supported by available evidence."
            elif action == "resolve":
                report["moderation_state"] = "resolved"
                report["status"] = "resolved"
                report["verification_state"] = "resolved"
                report["resolved_at"] = now
                report["message"] = "The community observation has been resolved."
            else:
                raise ValueError("Unsupported moderation action")
            report["updated_at"] = now
            if notes is not None:
                report["notes"] = notes
            save_field_report(report)
            save_public_report_action(report_id, now, admin_name, action, notes)
            public = self._public_report(report)
        regular_event = "field_report_resolved" if action == "resolve" else "field_report_updated"
        alias_event = {"verify": "public_report_verified", "resolve": "public_report_resolved"}.get(action, "public_report_updated")
        await self.engine.publish(regular_event, public)
        await self._publish_public_alias(alias_event, public)
        await self._publish_contexts(public)
        return public

    async def nearby(self, node_id, radius_override=None):
        reports = await self.active()
        return [
            report for report in reports
            if any(item["node_id"] == node_id for item in self.matching_nodes(report, radius_override))
        ]

    def _trend_evidence(self, report):
        category = report["category"]
        if category not in FIELD_REPORT_WEATHER_CATEGORIES:
            return "pending_sensor_confirmation", 0, [], [], [], "Station issue reported; physical inspection or device evidence is required."

        evidence = []
        contradicting = []
        supporting_nodes = []
        enough_history = False
        node_signals = {}
        for match in self.matching_nodes(report):
            node = match["node_id"]
            history = list(self.engine.hist[node])[-6:]
            if len(history) < 4:
                continue
            enough_history = True
            first, last = history[0], history[-1]
            humidity_delta = (last.get("humidity_pct") or 0) - (first.get("humidity_pct") or 0)
            pressure_delta = (last.get("pressure_hpa") or 0) - (first.get("pressure_hpa") or 0)
            temperature_delta = (last.get("temperature_c") or 0) - (first.get("temperature_c") or 0)
            signals = []
            if humidity_delta >= 0.8:
                signals.append(f"Humidity increased by {humidity_delta:.1f}% across {node.replace('_', '-')}")
            if pressure_delta <= -0.15:
                signals.append(f"Pressure decreased by {abs(pressure_delta):.2f} hPa across {node.replace('_', '-')}")
            if abs(temperature_delta) >= 0.35:
                signals.append(f"Temperature changed gradually by {temperature_delta:+.1f} C across {node.replace('_', '-')}")
            if category in ("fog", "low_visibility") and (last.get("humidity_pct") or 0) >= 85:
                signals.append(f"Humidity is high at {last['humidity_pct']:.1f}% on {node.replace('_', '-')}")
            if signals:
                supporting_nodes.append(node)
                evidence.extend(signals)
            node_signals[node] = (humidity_delta, pressure_delta, temperature_delta)

        if len(node_signals) >= 2:
            values = list(node_signals.values())
            if all(item[0] > 0 for item in values) and all(item[1] < 0 for item in values):
                evidence.append("Nearby AWS nodes show coherent humidity and pressure trends")

        direct_limit_categories = {"strong_wind", "hail", "flooding", "dust_storm", "low_visibility"}
        if not enough_history:
            return "pending_sensor_confirmation", 15, [], [], [], "Field report received. More nearby sensor samples are needed for indirect corroboration."
        if not evidence:
            contradicting.append("Nearby temperature, humidity, and pressure trends do not yet support the observation")
            return "not_supported", 18, [], [], contradicting, "Field report received; supporting sensor trends have not been detected."
        if category in direct_limit_categories:
            return "partially_supported", 55, supporting_nodes, evidence, [], "Indirect environmental trends are consistent with the report; no direct sensor is installed for this condition."
        if len(set(supporting_nodes)) >= 2 and len(evidence) >= 3:
            return "corroborated", 82, supporting_nodes, evidence, [], "Sensor trends across nearby stations are consistent with the controller observation."
        return "partially_supported", 64, supporting_nodes, evidence, [], "Some nearby sensor trends are consistent with the controller observation."

    def _evaluate_and_save(self, report):
        if report["status"] != "active":
            return report
        if report.get("moderation_state") in ("verified", "rejected"):
            return report
        state, confidence, nodes, evidence, contradicting, message = self._trend_evidence(report)
        report.update(
            verification_state=state,
            corroboration_confidence=confidence,
            verified_by_nodes=sorted(set(nodes)),
            evidence=evidence,
            contradicting_evidence=contradicting,
            message=message,
        )
        save_field_report(report)
        return report

    async def on_sensor_update(self, node_id):
        await self.refresh_expiry()
        changed = []
        async with self._lock:
            for report in load_field_reports():
                if report["status"] != "active" or node_id not in {item["node_id"] for item in self.matching_nodes(report)}:
                    continue
                before = (report["verification_state"], report["corroboration_confidence"], tuple(report["verified_by_nodes"]))
                report = self._evaluate_and_save(report)
                after = (report["verification_state"], report["corroboration_confidence"], tuple(report["verified_by_nodes"]))
                if before != after:
                    changed.append(self._public_report(report))
        for report in changed:
            await self.engine.publish("field_report_corroboration", report)
            await self._publish_public_alias("public_report_corroboration", report)
        if changed:
            await self.engine.publish("node_monitoring_context", self.context(node_id))

    def context(self, node_id):
        relevant = []
        for report in load_field_reports():
            if report["status"] != "active":
                continue
            match = next((item for item in self.matching_nodes(report) if item["node_id"] == node_id), None)
            if match:
                relevant.append(
                    {
                        "report_id": report["id"],
                        "type": report["category"],
                        "severity": report["severity"],
                        "distance_km": match["distance_km"],
                        "verification_state": report["verification_state"],
                    }
                )
        if any(item["type"] in FIELD_REPORT_STATION_CATEGORIES for item in relevant):
            mode = "inspection_required"
            interval = FIELD_REPORT_HEIGHTENED_SAMPLE_INTERVAL_SECONDS
        elif any(item["severity"] in ("high", "critical") for item in relevant):
            mode = "priority"
            interval = FIELD_REPORT_PRIORITY_SAMPLE_INTERVAL_SECONDS
        elif relevant:
            mode = "heightened"
            interval = FIELD_REPORT_HEIGHTENED_SAMPLE_INTERVAL_SECONDS
        else:
            mode = "normal"
            interval = FIELD_REPORT_BASELINE_SAMPLE_INTERVAL_SECONDS
        return {
            "node_id": node_id,
            "active_context": relevant,
            "monitoring_mode": mode,
            "recommended_sample_interval_seconds": interval,
            "baseline_sample_interval_seconds": FIELD_REPORT_BASELINE_SAMPLE_INTERVAL_SECONDS,
        }

    def contexts(self):
        return [self.context(node) for node in NODES]

    async def _publish_contexts(self, report):
        for match in self.matching_nodes(report):
            await self.engine.publish("node_monitoring_context", self.context(match["node_id"]))
