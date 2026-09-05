from datetime import datetime, timedelta, timezone

from conftest import run_async
from app.ml_detector import MODEL_VERSION, ml_service
from app.normalization import normalize_reading
from app.services import engine


def canonical(index: int):
    timestamp = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index * 5)).isoformat()
    reading = normalize_reading({
        "node_id": "AWS_001",
        "timestamp": timestamp,
        "sensors": {
            "ds18b20_temperature_c": 28.4 + index * 0.01,
            "dht22_temperature_c": 28.35 + index * 0.01,
            "dht22_humidity_pct": 70.0,
            "bmp280_temperature_c": 28.32 + index * 0.01,
            "bmp280_pressure_hpa": 945.1,
        },
        "device": {"sequence": index, "wifi_rssi": -55},
        "source": "esp32",
    })
    reading["sensor_timestamp"] = timestamp
    reading["_received_at"] = timestamp
    reading["_received_monotonic"] = 1000.0 + index * 5
    return reading


def test_ml_artifact_loaded(client):
    response = client.get("/ml/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["loaded"] is True
    assert payload["model_version"] == MODEL_VERSION
    assert payload["feature_count"] == 30


def test_health_advertises_final_hybrid_runtime(client):
    payload = client.get("/health").json()
    assert payload["detector_mode"] == "hybrid_rf_v6+heuristic_nonfreeze_safety"
    assert payload["ml"]["loaded"] is True


def test_live_reading_contains_real_ml_assessment():
    result = None
    for index in range(6):
        result = run_async(engine.process(canonical(index)))
    assessment = result["raw"].get("ml_assessment")
    assert assessment is not None
    assert assessment["loaded"] is True
    assert assessment["model_version"] == MODEL_VERSION
    assert assessment["source"] == "random_forest"
    assert assessment["prediction"] in {"normal", "temperature_spike", "temperature_drift", "temperature_freeze"}


def test_environmental_model_classes_are_unchanged():
    assert set(map(str, ml_service.model.classes_)) == {
        "normal",
        "temperature_spike",
        "temperature_drift",
        "temperature_freeze",
    }


def test_runtime_simulation_routes_removed(client):
    for path in (
        "/simulation/status",
        "/simulation/scenarios",
        "/simulation/start",
        "/simulation/pause",
        "/simulation/reset",
        "/simulation/speed",
        "/simulation/inject",
        "/simulation/clear",
        "/simulation/hardware-trigger",
    ):
        response = client.get(path) if path.endswith(("status", "scenarios")) else client.post(path, json={})
        assert response.status_code in (404, 405), (path, response.status_code, response.text)
