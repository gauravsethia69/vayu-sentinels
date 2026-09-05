from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator

from .config import FIELD_REPORT_CATEGORIES, FIELD_REPORT_CONFIDENCES, FIELD_REPORT_SEVERITIES, NODES, VISION_CATEGORIES


class SensorChannels(BaseModel):
    ds18b20_temperature_c: float | None = None
    dht22_temperature_c: float | None = Field(default=None, ge=-80, le=80)
    dht22_humidity_pct: float | None = Field(default=None, ge=0, le=100)
    bmp180_temperature_c: float | None = Field(default=None, ge=-80, le=80)
    bmp180_pressure_hpa: float | None = Field(default=None, ge=300, le=1200)
    bmp280_temperature_c: float | None = Field(default=None, ge=-80, le=85)
    bmp280_pressure_hpa: float | None = Field(default=None, ge=300, le=1100)

    @model_validator(mode="after")
    def require_temperature(self):
        if all(
            value is None
            for value in (
                self.ds18b20_temperature_c,
                self.dht22_temperature_c,
                self.bmp180_temperature_c,
                self.bmp280_temperature_c,
            )
        ):
            raise ValueError("At least one temperature channel is required")
        return self


class DeviceMetadata(BaseModel):
    wifi_rssi: int | None = Field(default=None, ge=-127, le=0)
    uptime_ms: int | None = Field(default=None, ge=0)
    sequence: int | None = Field(default=None, ge=0)


class SimulationMetadata(BaseModel):
    enabled: bool = False
    mode: str | None = Field(default=None, max_length=60)
    trigger: str | None = Field(default=None, max_length=60)

class ReadingIn(BaseModel):
    node_id: str
    timestamp: datetime | None = None
    temperature_c: float | None = Field(default=None, ge=-80, le=80)
    pressure_hpa: float | None = Field(default=None, ge=300, le=1200)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    sensors: SensorChannels | None = None
    device: DeviceMetadata | None = None
    source: str = Field(default="esp32", min_length=1, max_length=40)
    simulation: SimulationMetadata | None = None

    @model_validator(mode="after")
    def require_complete_shape(self):
        if self.sensors is None and any(
            value is None for value in (self.temperature_c, self.pressure_hpa, self.humidity_pct)
        ):
            raise ValueError("Legacy payload requires temperature_c, pressure_hpa, and humidity_pct")
        return self

    @model_validator(mode="after")
    def reject_runtime_simulation(self):
        if self.source.strip().lower() == "simulator" or bool(self.simulation and self.simulation.enabled):
            raise ValueError("Runtime simulation is disabled; submit real AWS telemetry")
        return self

class EventOut(BaseModel):
    id: int | None = None
    node_id: str
    timestamp: str
    anomaly_type: str
    parameter: str | None = None
    suspected_sensor: str | None = None
    event_type: str = "anomaly"
    confidence: int
    severity: str
    message: str
    observed_value: float | None = None
    expected_value: float | None = None
    corrected_value: float | None = None
    reasons: list[str] = Field(default_factory=list)
    factor_contributions: dict[str, int] = Field(default_factory=dict)
    recommended_action: str
    detector_mode: str = "heuristic_nonfreeze_safety"
    model_version: str = "heuristic-v1"
    event_group: str | None = None
    active_anomalies: list[str] | None = None


class FieldReportCreate(BaseModel):
    reporter_type: str = Field(default="controller", min_length=2, max_length=40)
    reporter_name: str | None = Field(default=None, max_length=100)
    station_id: str | None = None
    cluster_id: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    location_label: str | None = Field(default=None, max_length=160)
    category: str
    observation: str = Field(min_length=3, max_length=1200)
    severity: str = "moderate"
    reporter_confidence: str = Field(
        default="medium",
        validation_alias=AliasChoices("reporter_confidence", "confidence"),
    )
    direction: str | None = Field(default=None, max_length=20)
    radius_km: float = Field(default=10, gt=0, le=100)
    expires_at: datetime | None = None
    expires_in_minutes: int | None = Field(default=None, ge=1, le=10080)
    until_resolved: bool = False
    source: str = Field(default="controller_ui", min_length=2, max_length=60)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_report(self):
        if self.category not in FIELD_REPORT_CATEGORIES:
            raise ValueError("Unsupported field report category")
        if self.severity not in FIELD_REPORT_SEVERITIES:
            raise ValueError("Unsupported field report severity")
        if self.reporter_confidence not in FIELD_REPORT_CONFIDENCES:
            raise ValueError("Unsupported reporter confidence")
        if self.station_id is not None and self.station_id not in NODES:
            raise ValueError("Unknown station_id")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        return self


class FieldReportUpdate(BaseModel):
    status: str | None = None
    severity: str | None = None
    observation: str | None = Field(default=None, min_length=3, max_length=1200)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_update(self):
        if self.status is not None and self.status not in ("active", "resolved"):
            raise ValueError("status must be active or resolved")
        if self.severity is not None and self.severity not in FIELD_REPORT_SEVERITIES:
            raise ValueError("Unsupported field report severity")
        return self


class FieldReportResolve(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


class DemoLoginIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=100)


class PublicReportCreate(BaseModel):
    category: str
    observation: str = Field(min_length=10, max_length=1200)
    station_scope: list[str] = Field(default_factory=list, max_length=2)
    direction: str | None = Field(default=None, max_length=20)
    severity: str = "moderate"
    reporter_confidence: str = "medium"
    location_label: str | None = Field(default=None, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float = Field(default=10, gt=0, le=100)
    expires_in_minutes: int | None = Field(default=None, ge=1, le=10080)

    @model_validator(mode="after")
    def validate_public_report(self):
        if self.category not in FIELD_REPORT_CATEGORIES:
            raise ValueError("Unsupported public report category")
        if self.severity not in FIELD_REPORT_SEVERITIES:
            raise ValueError("Unsupported field report severity")
        if self.reporter_confidence not in FIELD_REPORT_CONFIDENCES:
            raise ValueError("Unsupported reporter confidence")
        if any(node not in NODES for node in self.station_scope):
            raise ValueError("Unknown station in station_scope")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        return self


class AdminReportAction(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


class VisionDetectionIn(BaseModel):
    type: str
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_type(self):
        if self.type not in VISION_CATEGORIES:
            raise ValueError("Unsupported vision category")
        return self


class VisionObservationIn(BaseModel):
    node_id: str
    timestamp: datetime | None = None
    source: Literal["camera", "manual_camera", "simulated_camera"] = "manual_camera"
    detections: list[VisionDetectionIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_node(self):
        if self.node_id not in NODES:
            raise ValueError("Unknown node_id")
        return self
