from datetime import datetime, timedelta, timezone

import pytest

from app.config import NODES
from app.database import connect
from app.services import engine, ml_service
from conftest import receive_websocket_events, run_async


@pytest.fixture(autouse=True)
def remove_v3_records():
    with connect() as db:
        vision_before = {row[0] for row in db.execute("SELECT id FROM vision_events")}
        exposure_before = {row[0] for row in db.execute("SELECT id FROM sensor_exposure_events")}
    yield
    with connect() as db:
        for table, before in (("vision_events", vision_before), ("sensor_exposure_events", exposure_before)):
            current = {row[0] for row in db.execute(f"SELECT id FROM {table}")}
            for row_id in current - before:
                db.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))


def expanded_payload(node_id, temperature, *, index=0, bmp280=True):
    sensors = {
        "ds18b20_temperature_c": temperature,
        "dht22_temperature_c": temperature + 0.1,
        "dht22_humidity_pct": 70 + index * 0.05,
    }
    prefix = "bmp280" if bmp280 else "bmp180"
    sensors[f"{prefix}_temperature_c"] = temperature - 0.1
    sensors[f"{prefix}_pressure_hpa"] = 1007 - index * 0.02
    return {
        "node_id": node_id,
        "timestamp": (datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc) + timedelta(seconds=index)).isoformat(),
        "sensors": sensors,
        "device": {"wifi_rssi": -60, "uptime_ms": index * 1000, "sequence": index},
        "source": "esp32",
    }


def warm_nodes(client, values):
    for index in range(6):
        for node_id, value in values.items():
            response = client.post("/ingest", json=expanded_payload(node_id, value + index * 0.01, index=index))
            assert response.status_code == 200


def test_aws003_ingest_history_health_and_summary(client):
    response = client.post("/ingest", json=expanded_payload("AWS_003", 29.4))
    assert response.status_code == 200
    assert response.json()["raw"]["sensor_models"]["barometric"] == "BMP280"
    assert client.get("/nodes/AWS_003/history").json()[-1]["node_id"] == "AWS_003"
    assert client.get("/sensor-health/AWS_003").status_code == 200
    assert {item["node_id"] for item in client.get("/dashboard/summary").json()["nodes"]} == set(NODES)


def test_bmp180_payload_remains_compatible(client):
    result = client.post("/ingest", json=expanded_payload("AWS_001", 29.4, bmp280=False)).json()["raw"]
    assert result["pressure_hpa"] == 1007
    assert result["sensor_models"]["barometric"] == "BMP180_legacy"


@pytest.mark.parametrize(
    "target,peers",
    [("AWS_001", {"AWS_002": 29.2, "AWS_003": 29.6}), ("AWS_002", {"AWS_001": 29.1, "AWS_003": 29.5}), ("AWS_003", {"AWS_001": 29.0, "AWS_002": 29.4})],
)
def test_two_healthy_peer_mean_reconstructs_only_faulty_temperature(client, target, peers):
    values = {target: sum(peers.values()) / 2, **peers}
    warm_nodes(client, values)
    for peer_id, peer_value in peers.items():
        client.post("/ingest", json=expanded_payload(peer_id, peer_value, index=6))
    payload = expanded_payload(target, 41.0, index=7)
    payload["sensors"]["dht22_temperature_c"] = values[target] + 0.1
    payload["sensors"]["bmp280_temperature_c"] = values[target] - 0.1
    result = client.post("/ingest", json=payload).json()
    assert result["raw"]["temperature_c"] == 41.0
    assert result["trusted"]["temperature_c"] == pytest.approx(sum(peers.values()) / 2, abs=0.02)
    assert result["trusted"]["provenance"] == "peer_station_mean"
    assert result["trusted"]["source_nodes"] == list(peers)
    assert result["trusted"]["humidity_pct"] == result["raw"]["humidity_pct"]
    assert result["trusted"]["pressure_hpa"] == result["raw"]["pressure_hpa"]


def test_single_peer_estimate_and_no_peer_fallback(client):
    warm_nodes(client, {"AWS_001": 29.4, "AWS_002": 29.2, "AWS_003": 29.6})
    engine.sensor_state["AWS_003"]["temperature"] = "anomalous"
    result = client.post("/ingest", json=expanded_payload("AWS_001", 41.0, index=7)).json()
    assert result["trusted"]["provenance"] == "single_peer_estimate"
    assert result["trusted"]["source_nodes"] == ["AWS_002"]
    engine._reset_all_state_for_testing()
    # Rebuild fresh peer history after the production-state reset, then mark
    # both peers ineligible to verify that no peer reconstruction is invented.
    warm_nodes(client, {"AWS_001": 29.4, "AWS_002": 29.2, "AWS_003": 29.6})
    engine.sensor_state["AWS_002"]["temperature"] = "anomalous"
    engine.sensor_state["AWS_003"]["temperature"] = "anomalous"
    for index in range(6):
        client.post("/ingest", json=expanded_payload("AWS_001", 29.4 + index * 0.01, index=20 + index))
    result = client.post("/ingest", json=expanded_payload("AWS_001", 41.0, index=7)).json()
    assert result["trusted"]["provenance"] != "peer_station_mean"
    assert "source_nodes" not in result["trusted"]


def test_stale_peers_are_excluded(client):
    warm_nodes(client, {"AWS_001": 29.4, "AWS_002": 29.2, "AWS_003": 29.6})
    engine.last_seen["AWS_002"] -= 30
    engine.last_seen["AWS_003"] -= 30
    assert engine.get_healthy_peers("AWS_001", "temperature") == []


def test_peer_failover_recovers_to_local_validated_sensor(client, monkeypatch):
    # This test isolates peer-failover recovery; Hybrid RF v6 prediction and
    # loading are covered independently in test_ml_integration.py.
    monkeypatch.setattr(
        ml_service,
        "assess",
        lambda reading, history: {
            "enabled": True,
            "loaded": True,
            "ready": False,
            "prediction": None,
            "confidence": None,
            "probabilities": {},
        },
    )
    warm_nodes(client, {"AWS_001": 29.4, "AWS_002": 29.2, "AWS_003": 29.6})
    anomalous = expanded_payload("AWS_001", 41.0, index=7)
    anomalous["sensors"]["dht22_temperature_c"] = 29.5
    anomalous["sensors"]["bmp280_temperature_c"] = 29.3
    assert client.post("/ingest", json=anomalous).json()["trusted"]["provenance"] == "peer_station_mean"
    latest = None
    healthy_values = [29.40, 29.51, 29.36, 29.47, 29.39, 29.49, 29.37, 29.46, 29.41, 29.45, 29.38, 29.44]
    for index, value in enumerate(healthy_values, start=8):
        client.post("/ingest", json=expanded_payload("AWS_002", value - 0.2, index=index))
        client.post("/ingest", json=expanded_payload("AWS_003", value + 0.2, index=index))
        latest = client.post("/ingest", json=expanded_payload("AWS_001", value, index=index)).json()
    assert engine.sensor_state["AWS_001"]["temperature"] == "healthy"
    assert latest["trusted"]["provenance"] == "raw_validated"
    assert "peer_failover" not in latest["trusted"]


def test_aws003_sensor_reading_is_broadcast(client):
    with client.websocket_connect("/ws/live") as websocket:
        assert websocket.receive_json()["type"] == "system_status"
        assert client.post("/ingest", json=expanded_payload("AWS_003", 29.4)).status_code == 200
        messages = receive_websocket_events(websocket, {"sensor_reading", "sensor_health"})
        reading = next(item["data"] for item in messages if item["type"] == "sensor_reading")
        assert reading["node_id"] == "AWS_003"


def test_vision_observation_is_context_only_and_broadcast(client):
    before_events = len(engine.events)
    before_readings = engine.total
    with client.websocket_connect("/ws/live") as websocket:
        assert websocket.receive_json()["type"] == "system_status"
        response = client.post("/vision/observations", json={"node_id": "AWS_001", "source": "simulated_camera", "detections": [{"type": "bird_detected", "confidence": 0.88}]})
        assert response.status_code == 201
        messages = receive_websocket_events(websocket, {"vision_event", "vision_status", "multi_source_context"})
        event = next(item["data"] for item in messages if item["type"] == "vision_event")
        assert event["vision_mode"] == "simulated"
    assert len(engine.events) == before_events
    assert engine.total == before_readings


@pytest.mark.parametrize("category", ["dark_clouds", "animal_detected", "camera_obstruction", "bright_direct_sun", "strong_wind_visual"])
def test_vision_categories_are_stored(client, category):
    response = client.post("/vision/observations", json={"node_id": "AWS_003", "source": "manual_camera", "detections": [{"type": category, "confidence": 0.8}]})
    assert response.status_code == 201
    assert response.json()[0]["type"] == category


def test_sensor_registry_inventory_and_maintenance_exposure(client):
    specs = client.get("/sensor-specs").json()
    assert {item["model"] for item in specs} == {"DS18B20", "DHT22 / AM2302", "BMP280"}
    assert all(item["expected_service_life"] is None for item in specs)
    inventory = client.get("/sensor-inventory").json()
    assert len(inventory) == 9
    assert len(client.get("/sensor-inventory/AWS_003").json()) == 3
    before = {item["sensor_id"]: item["maintenance_risk"] for item in client.get("/maintenance/AWS_001").json()["sensors"]}
    response = client.post("/vision/observations", json={"node_id": "AWS_001", "source": "simulated_camera", "detections": [{"type": "heavy_rain_visual", "confidence": 0.9}]})
    assert response.status_code == 201
    after = {item["sensor_id"]: item["maintenance_risk"] for item in client.get("/maintenance/AWS_001").json()["sensors"]}
    assert max(after.values()) >= max(before.values())
    statuses = client.get("/maintenance/AWS_001").json()["sensors"]
    assert any("exposure event" in reason for item in statuses for reason in item["reasons"])


def test_three_node_peer_consensus_endpoint(client):
    warm_nodes(client, {"AWS_001": 29.4, "AWS_002": 29.2, "AWS_003": 29.6})
    client.post("/ingest", json=expanded_payload("AWS_002", 29.2, index=6))
    client.post("/ingest", json=expanded_payload("AWS_003", 29.6, index=6))
    result = client.get("/peer-consensus/AWS_001").json()["parameters"]["temperature"]
    assert result["value"] == pytest.approx(29.4, abs=0.02)
    assert result["source_nodes"] == ["AWS_002", "AWS_003"]
