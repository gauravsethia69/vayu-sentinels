import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = Path(os.getenv("SKYGUARD_DATABASE_PATH", str(BASE_DIR / "skyguard.db"))).expanduser()
NODES = ("AWS_001", "AWS_002", "AWS_003")
MQTT_HOST = os.getenv("SKYGUARD_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("SKYGUARD_MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("SKYGUARD_MQTT_TOPIC", "skyguard/aws/+/telemetry")
MQTT_USERNAME = os.getenv("SKYGUARD_MQTT_USERNAME", "").strip()
MQTT_PASSWORD = os.getenv("SKYGUARD_MQTT_PASSWORD", "")
MQTT_TLS = os.getenv("SKYGUARD_MQTT_TLS", "false").strip().lower() in ("1", "true", "yes", "on")

# Hybrid Random-Forest anomaly layer. The backend remains fail-safe: if the
# artifact or sklearn dependency is unavailable, heuristic-v2 continues alone.
ML_ENABLED = os.getenv("SKYGUARD_ML_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
ML_MODEL_PATH = os.getenv(
    "SKYGUARD_ML_MODEL_PATH",
    str(BASE_DIR / "app" / "models" / "skyguard_final_hybrid_rf_v6.joblib"),
)
ML_CONFIDENCE_THRESHOLD = float(os.getenv("SKYGUARD_ML_CONFIDENCE_THRESHOLD", "0.70"))
ML_MIN_SAMPLES = max(3, int(os.getenv("SKYGUARD_ML_MIN_SAMPLES", "4")))

_DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
CORS_ALLOWED_ORIGINS = tuple(
    origin.strip()
    for origin in os.getenv("SKYGUARD_CORS_ALLOWED_ORIGINS", ",".join(_DEFAULT_CORS_ORIGINS)).split(",")
    if origin.strip()
)

# Hackathon-only demo credentials. Override both values outside local demos.
DEMO_ADMIN_NAME = os.getenv("SKYGUARD_DEMO_ADMIN_NAME", "Gaurav Sethia")
DEMO_ADMIN_PASSWORD = os.getenv("SKYGUARD_DEMO_ADMIN_PASSWORD", "23")
DEMO_ADMIN_SESSION_MINUTES = int(os.getenv("SKYGUARD_DEMO_ADMIN_SESSION_MINUTES", "480"))

PROTOTYPE_CLUSTER_ID = "prototype_cluster_01"
STATION_LOCATIONS = {
    "AWS_001": {
        "latitude": 22.7196,
        "longitude": 75.8577,
        "cluster_id": PROTOTYPE_CLUSTER_ID,
        "location_label": "North Ridge Station",
    },
    "AWS_002": {
        "latitude": 22.7700,
        "longitude": 75.9200,
        "cluster_id": PROTOTYPE_CLUSTER_ID,
        "location_label": "East Valley Station",
    },
    "AWS_003": {
        "latitude": 22.7445,
        "longitude": 75.8950,
        "cluster_id": PROTOTYPE_CLUSTER_ID,
        "location_label": "Central Plain Station",
    },
}

PEER_MIN_SENSOR_HEALTH = 70.0
VISION_MODE = os.getenv("SKYGUARD_VISION_MODE", "manual_observation")
VISION_CATEGORIES = (
    "clear_sky", "sun_visible", "bright_direct_sun", "partly_cloudy", "cloudy",
    "dark_clouds", "possible_rain_clouds", "fog_or_haze", "low_visibility",
    "bird_detected", "animal_detected", "person_near_station", "foreign_object",
    "station_obstruction", "camera_obstruction", "vegetation_interference",
    "possible_station_tilt", "possible_station_damage", "strong_wind_visual",
    "heavy_rain_visual",
)
VISION_EVENT_SEVERITY = {
    "camera_obstruction": "high", "possible_station_damage": "high",
    "station_obstruction": "warning", "foreign_object": "warning",
    "bird_detected": "warning", "animal_detected": "warning",
    "bright_direct_sun": "info", "heavy_rain_visual": "warning",
}
MAINTENANCE_INSPECTION_DAYS = 90
MAINTENANCE_CALIBRATION_DAYS = 180

FIELD_REPORT_WEATHER_CATEGORIES = (
    "clouds_approaching",
    "light_rain",
    "heavy_rain",
    "thunderstorm",
    "strong_wind",
    "fog",
    "low_visibility",
    "hail",
    "dust_storm",
    "rapid_weather_change",
    "flooding",
    "other_weather",
)
FIELD_REPORT_STATION_CATEGORIES = (
    "sensor_damage",
    "sensor_obstruction",
    "sensor_contamination",
    "power_issue",
    "communication_issue",
    "suspected_sensor_fault",
    "station_access_issue",
    "other_station_issue",
)
FIELD_REPORT_CATEGORIES = FIELD_REPORT_WEATHER_CATEGORIES + FIELD_REPORT_STATION_CATEGORIES + (
    "custom_observation",
)
FIELD_REPORT_SEVERITIES = ("information", "low", "moderate", "high", "critical")
FIELD_REPORT_CONFIDENCES = ("low", "medium", "high")
FIELD_REPORT_DEFAULT_EXPIRY_MINUTES = {
    "clouds_approaching": 60,
    "light_rain": 60,
    "heavy_rain": 60,
    "thunderstorm": 60,
    "strong_wind": 30,
    "fog": 60,
    "low_visibility": 60,
    "hail": 60,
    "dust_storm": 60,
    "rapid_weather_change": 60,
    "flooding": 120,
    "other_weather": 60,
    "custom_observation": 60,
}
FIELD_REPORT_BASELINE_SAMPLE_INTERVAL_SECONDS = 5
FIELD_REPORT_HEIGHTENED_SAMPLE_INTERVAL_SECONDS = 2
FIELD_REPORT_PRIORITY_SAMPLE_INTERVAL_SECONDS = 1

PHYSICAL_RANGES = {
    "temperature": (-20.0, 70.0),
    "pressure": (850.0, 1100.0),
    "humidity": (0.0, 100.0),
}
SPIKE_TEMP_THRESHOLD_C = 5.0
SPIKE_THRESHOLDS = {"temperature": SPIKE_TEMP_THRESHOLD_C, "humidity": 18.0, "pressure": 8.0}
PEER_AGREEMENT_TOLERANCES = {"temperature": 2.0, "humidity": 5.0, "pressure": 2.0}
NODE_MISMATCH_THRESHOLDS = {"temperature": 8.0, "humidity": 25.0, "pressure": 10.0}
DRIFT_ACCUMULATION_THRESHOLDS = {"temperature": 2.5, "humidity": 6.0, "pressure": 3.0}
FREEZE_MIN_DURATION_SECONDS = float(os.getenv("SKYGUARD_FREEZE_MIN_DURATION_SECONDS", "20"))
FREEZE_CONFIRMATION_EVALUATIONS = max(
    1, int(os.getenv("SKYGUARD_FREEZE_CONFIRMATION_EVALUATIONS", "2"))
)
FREEZE_ML_STRONG_CONFIDENCE = float(
    os.getenv("SKYGUARD_FREEZE_ML_STRONG_CONFIDENCE", "0.70")
)
DRIFT_MIN_SAMPLES = 7
DRIFT_SLOPE_THRESHOLDS = {"temperature": 0.12, "humidity": 0.3, "pressure": 0.12}

# Backward-compatible drift window alias retained for older imports.
DRIFT_WINDOW_SAMPLES = DRIFT_MIN_SAMPLES

TEMPERATURE_DISAGREEMENT_WARNING_C = 2.0
TEMPERATURE_DISAGREEMENT_STRONG_C = 4.0
REFERENCE_SENSOR_AGREEMENT_C = 1.2
PEER_FRESHNESS_SECONDS = float(os.getenv("SKYGUARD_PEER_FRESHNESS_MIN_SECONDS", "10"))
PEER_FRESHNESS_MULTIPLIER = float(os.getenv("SKYGUARD_PEER_FRESHNESS_MULTIPLIER", "3"))

EXPECTED_SAMPLE_INTERVAL_SECONDS = float(
    os.getenv("SKYGUARD_EXPECTED_SAMPLE_INTERVAL_SECONDS", "3")
)
COMMUNICATION_INTERVAL_WINDOW_SAMPLES = max(
    5, int(os.getenv("SKYGUARD_COMMUNICATION_INTERVAL_WINDOW_SAMPLES", "20"))
)
COMMUNICATION_EXPECTED_MIN_SECONDS = float(
    os.getenv("SKYGUARD_COMMUNICATION_EXPECTED_MIN_SECONDS", "1")
)
COMMUNICATION_EXPECTED_MAX_SECONDS = float(
    os.getenv("SKYGUARD_COMMUNICATION_EXPECTED_MAX_SECONDS", "8")
)
COMMUNICATION_WARNING_SECONDS = float(
    os.getenv("SKYGUARD_COMMUNICATION_WARNING_MIN_SECONDS", "6")
)
COMMUNICATION_FAILURE_SECONDS = float(
    os.getenv("SKYGUARD_COMMUNICATION_FAILURE_MIN_SECONDS", "10")
)
COMMUNICATION_WARNING_MULTIPLIER = float(
    os.getenv("SKYGUARD_COMMUNICATION_WARNING_MULTIPLIER", "2.5")
)
COMMUNICATION_FAILURE_MULTIPLIER = float(
    os.getenv("SKYGUARD_COMMUNICATION_FAILURE_MULTIPLIER", "4")
)
NETWORK_DELAY_WARNING_SECONDS = 2.5
RECOVERY_HEALTHY_SAMPLES = 5
PEER_FAILOVER_RECOVERY_SAMPLES = RECOVERY_HEALTHY_SAMPLES

SENSOR_HISTORY_MAXLEN = 100
EVENT_HISTORY_MAXLEN = 500
TRUSTED_HISTORY_MAXLEN = 500

AVAILABLE_PARAMETERS = ("temperature", "humidity", "pressure")
