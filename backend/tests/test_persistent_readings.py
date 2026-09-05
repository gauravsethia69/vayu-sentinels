import json

from app import database
from app.services import engine

from conftest import receive_websocket_events


def reading(node_id, timestamp, temperature):
    return {
        "node_id": node_id,
        "timestamp": timestamp,
        "received_at": timestamp,
        "sensor_timestamp": timestamp,
        "temperature_c": temperature,
        "pressure_hpa": 944.85,
        "humidity_pct": 70.0,
        "source": "esp32",
        "quality": "raw",
        "raw_sensors": {"ds18b20_temperature_c": temperature},
        "device": {"sequence": int(temperature * 10)},
        "simulation": {"enabled": False},
        "communication_state": "healthy",
    }


def use_temporary_database(monkeypatch, tmp_path):
    path = tmp_path / "persistent-readings.db"
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    database.init_db()
    return path


def test_database_read_helpers_preserve_order_and_raw_trusted_split(monkeypatch, tmp_path):
    use_temporary_database(monkeypatch, tmp_path)
    first = reading("AWS_001", "2026-09-04T10:00:00+00:00", 28.1)
    second = reading("AWS_001", "2026-09-04T10:00:01+00:00", 45.0)
    database.save_reading(first, {**first, "quality": "validated", "provenance": "raw_validated"})
    database.save_reading(second, {
        **second,
        "temperature_c": 28.3,
        "quality": "estimated",
        "provenance": "peer_station_mean",
        "source_nodes": ["AWS_002", "AWS_003"],
        "excluded_node": "AWS_001",
        "corrected_parameters": ["temperature"],
    })

    assert [item["temperature_c"] for item in database.load_reading_history("AWS_001", 10)] == [28.1, 45.0]
    assert [item["temperature_c"] for item in database.load_trusted_history("AWS_001", 10)] == [28.1, 28.3]
    assert database.load_latest_reading("AWS_001")["temperature_c"] == 45.0
    assert database.load_latest_trusted_reading("AWS_001")["temperature_c"] == 28.3
    assert database.load_reading_records("AWS_001", 10)[-1]["raw"]["temperature_c"] == 45.0


def test_rest_history_latest_records_and_trusted_stream_use_sqlite(client, monkeypatch, tmp_path):
    use_temporary_database(monkeypatch, tmp_path)
    first = reading("AWS_002", "2026-09-04T11:00:00+00:00", 27.8)
    second = reading("AWS_002", "2026-09-04T11:00:01+00:00", 27.9)
    database.save_reading(first, {**first, "quality": "validated", "provenance": "raw_validated"})
    database.save_reading(second, {**second, "quality": "validated", "provenance": "raw_validated"})
    engine.hist["AWS_002"].clear()

    assert client.get("/nodes/AWS_002/latest").json()["temperature_c"] == 27.9
    assert [row["temperature_c"] for row in client.get("/nodes/AWS_002/history?limit=10").json()] == [27.8, 27.9]
    records = client.get("/nodes/AWS_002/records?limit=10").json()
    assert records["node_id"] == "AWS_002"
    assert len(records["records"]) == 2
    assert client.get("/trusted-stream?limit=10").json()[-1]["trusted"]["temperature_c"] == 27.9


def test_corrupt_json_row_is_ignored_safely(monkeypatch, tmp_path):
    use_temporary_database(monkeypatch, tmp_path)
    valid = reading("AWS_003", "2026-09-04T12:00:00+00:00", 29.0)
    database.save_reading(valid, {**valid, "quality": "validated", "provenance": "raw_validated"})
    with database.connect() as db:
        db.execute(
            "INSERT INTO sensor_readings(timestamp,node_id,raw_json,trusted_json) VALUES(?,?,?,?)",
            ("2026-09-04T12:00:01+00:00", "AWS_003", "not-json", json.dumps({"node_id": "AWS_003"})),
        )
    assert database.load_latest_reading("AWS_003")["temperature_c"] == 29.0


def test_websocket_additively_emits_trusted_reading(client, monkeypatch, tmp_path):
    use_temporary_database(monkeypatch, tmp_path)
    payload = {
        "node_id": "AWS_003",
        "temperature_c": 28.4,
        "pressure_hpa": 944.9,
        "humidity_pct": 69.5,
        "source": "esp32",
    }
    with client.websocket_connect("/ws/live") as websocket:
        assert websocket.receive_json()["type"] == "system_status"
        response = client.post("/ingest", json=payload)
        assert response.status_code == 200
        messages = receive_websocket_events(websocket, {"sensor_reading", "trusted_reading", "sensor_health"})
    trusted = next(message["data"] for message in messages if message["type"] == "trusted_reading")
    assert trusted["raw"]["node_id"] == "AWS_003"
    assert trusted["trusted"]["quality"] == "validated"
