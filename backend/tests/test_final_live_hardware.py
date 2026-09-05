
def base_payload(sequence=1):
    return {
        "node_id": "AWS_001",
        "sensors": {
            "ds18b20_temperature_c": 26.4,
            "dht22_temperature_c": 26.5,
            "dht22_humidity_pct": 72.4,
            "bmp280_temperature_c": 26.4,
            "bmp280_pressure_hpa": 945.3,
        },
        "device": {"wifi_rssi": -60, "uptime_ms": 10000 + sequence * 2000, "sequence": sequence},
        "source": "esp32",
    }


def test_ds_channel_loss_is_sensor_event_not_communication_failure(client):
    payload = base_payload()
    payload["sensors"].pop("ds18b20_temperature_c")
    response = client.post("/ingest", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["raw"]["communication_state"] == "healthy"
    assert "ds18b20_temperature_c" in result["raw"]["missing_sensors"]
    assert any(
        event["anomaly_type"] == "data_loss"
        and event["parameter"] == "temperature"
        and event["ml_prediction"] == "sensor_missing"
        and event["ml_source"] == "quality_gate"
        for event in result["events"]
    )
    assert not any(event["anomaly_type"] == "communication_failure" for event in result["events"])


def test_minus_127_reaches_quality_gate_and_raw_is_preserved(client):
    payload = base_payload()
    payload["sensors"]["ds18b20_temperature_c"] = -127.0
    response = client.post("/ingest", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["raw"]["raw_sensors"]["ds18b20_temperature_c"] == -127.0
    assert result["raw"]["ml_assessment"]["prediction"] == "data_corruption"
    event = next(event for event in result["events"] if event["anomaly_type"] == "data_corruption")
    assert event["ml_source"] == "quality_gate"
    assert event["observed_value"] == -127.0
    assert result["trusted"]["temperature_c"] != -127.0
    assert "temperature" in result["trusted"]["corrected_parameters"]


def test_legacy_simulation_false_metadata_does_not_create_simulator(client):
    payload = base_payload()
    payload["simulation"] = {"enabled": False}
    response = client.post("/ingest", json=payload)
    assert response.status_code == 200
    assert response.json()["raw"]["source"] == "esp32"


def test_runtime_simulation_enabled_is_rejected(client):
    payload = base_payload()
    payload["simulation"] = {"enabled": True}
    response = client.post("/ingest", json=payload)
    assert response.status_code == 422


def test_packet_jitter_is_not_generated(client):
    for sequence in range(8):
        response = client.post("/ingest", json=base_payload(sequence))
        assert response.status_code == 200
        assert all(event["anomaly_type"] != "packet_jitter" for event in response.json()["events"])
    events = client.get("/events?limit=100").json()
    assert all(event["anomaly_type"] != "packet_jitter" for event in events)
