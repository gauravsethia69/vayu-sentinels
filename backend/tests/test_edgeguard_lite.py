def base_payload(edge_ai=None):
    payload = {
        "node_id": "AWS_001",
        "sensors": {
            "ds18b20_temperature_c": 26.4,
            "dht22_temperature_c": 26.5,
            "dht22_humidity_pct": 72.4,
            "bmp280_temperature_c": 26.4,
            "bmp280_pressure_hpa": 945.3,
        },
        "device": {"wifi_rssi": -60, "uptime_ms": 12000, "sequence": 1},
        "source": "esp32",
    }
    if edge_ai is not None:
        payload["edge_ai"] = edge_ai
    return payload


def test_edgeguard_payload_is_preserved_in_latest_and_ai_summary(client):
    edge = {
        "enabled": True,
        "version": "EdgeGuard Lite v1",
        "local_decision": "warning",
        "risk_score": 55,
        "reasons": ["DS18B20 and DHT22 mismatch"],
        "local_action": "yellow_led",
    }
    response = client.post("/ingest", json=base_payload(edge))
    assert response.status_code == 200, response.text
    raw = response.json()["raw"]
    assert raw["edge_ai"] == edge
    assert raw["ai_summary"]["edge"] == edge
    assert "rf" in raw["ai_summary"]
    assert "pytorch" in raw["ai_summary"]

    latest = client.get("/nodes/AWS_001/latest")
    assert latest.status_code == 200
    assert latest.json()["edge_ai"] == edge
    assert latest.json()["ai_summary"]["edge"] == edge


def test_missing_edgeguard_payload_returns_waiting_summary(client):
    response = client.post("/ingest", json=base_payload())
    assert response.status_code == 200, response.text
    raw = response.json()["raw"]
    assert "edge_ai" not in raw
    assert raw["ai_summary"]["edge"] == {
        "enabled": False,
        "version": "EdgeGuard Lite v1",
        "local_decision": "waiting",
        "risk_score": None,
        "reasons": ["Waiting for EdgeGuard data from hardware"],
        "local_action": "waiting",
    }


def test_invalid_edgeguard_contract_is_rejected(client):
    edge = {
        "enabled": True,
        "version": "EdgeGuard Lite v1",
        "local_decision": "warning",
        "risk_score": 101,
        "reasons": [],
        "local_action": "yellow_led",
    }
    assert client.post("/ingest", json=base_payload(edge)).status_code == 422


def test_edgeguard_critical_sensor_evidence_is_preserved(client):
    edge = {
        "enabled": True,
        "version": "EdgeGuard Lite v1",
        "local_decision": "critical",
        "risk_score": 90,
        "reasons": ["humidity out of range", "pressure out of expected range"],
        "local_action": "red_led",
    }
    payload = base_payload(edge)
    payload["sensors"]["dht22_humidity_pct"] = 101.0
    payload["sensors"]["bmp280_pressure_hpa"] = 1101.0

    response = client.post("/ingest", json=payload)

    assert response.status_code == 200, response.text
    raw = response.json()["raw"]
    assert raw["raw_sensors"]["dht22_humidity_pct"] == 101.0
    assert raw["raw_sensors"]["bmp280_pressure_hpa"] == 1101.0
    assert raw["ai_summary"]["edge"] == edge
