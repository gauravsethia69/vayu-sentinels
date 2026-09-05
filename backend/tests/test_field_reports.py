from datetime import datetime, timedelta, timezone

import pytest

from app.database import connect, load_field_report
from app.services import engine

from conftest import receive_websocket_events


@pytest.fixture(autouse=True)
def remove_test_reports():
    with connect() as db:
        db.execute("DELETE FROM field_reports WHERE source='pytest'")
    yield
    with connect() as db:
        db.execute("DELETE FROM field_reports WHERE source='pytest'")


def report_payload(**overrides):
    payload = {
        "reporter_type": "controller",
        "reporter_name": "Test Controller",
        "cluster_id": "prototype_cluster_01",
        "location_label": "North Ridge Sector",
        "category": "clouds_approaching",
        "observation": "Dark cloud formation approaching from the west.",
        "severity": "moderate",
        "reporter_confidence": "high",
        "direction": "west",
        "radius_km": 10,
        "expires_in_minutes": 60,
        "source": "pytest",
    }
    payload.update(overrides)
    return payload


def ingest(client, node_id, index, *, coherent=True):
    humidity = 60 + index * 1.4 if coherent else 60 + (index % 2) * 0.05
    pressure = 1008 - index * 0.35 if coherent else 1008 + (index % 2) * 0.02
    temperature = 29.5 - index * 0.12 if coherent else 29.5 + (index % 2) * 0.03
    return client.post(
        "/ingest",
        json={
            "node_id": node_id,
            "timestamp": (datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc) + timedelta(seconds=index)).isoformat(),
            "temperature_c": temperature,
            "humidity_pct": humidity,
            "pressure_hpa": pressure,
            "source": "esp32",
        },
    )


def test_create_read_and_persist_field_report(client):
    created = client.post("/field-reports", json=report_payload())
    assert created.status_code == 201
    body = created.json()
    assert body["id"].startswith("FR-")
    assert body["status"] == "active"
    assert body["reporter_confidence"] == "high"
    assert {station["node_id"] for station in body["nearby_stations"]} == {"AWS_001", "AWS_002", "AWS_003"}
    assert client.get(f"/field-reports/{body['id']}").json()["observation"] == body["observation"]
    assert load_field_report(body["id"])["category"] == "clouds_approaching"


def test_filters_update_and_resolve_keep_history(client):
    report_id = client.post("/field-reports", json=report_payload(category="sensor_damage", station_id="AWS_001", cluster_id=None, until_resolved=True)).json()["id"]
    updated = client.patch(f"/field-reports/{report_id}", json={"severity": "high", "notes": "Housing is cracked."})
    assert updated.status_code == 200
    assert updated.json()["updated_at"] >= updated.json()["created_at"]
    filtered = client.get("/field-reports", params={"status": "active", "station_id": "AWS_001", "severity": "high"}).json()
    assert report_id in {report["id"] for report in filtered}
    resolved = client.post(f"/field-reports/{report_id}/resolve", json={"notes": "Housing replaced."})
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolved_at"]
    assert client.get(f"/field-reports/{report_id}").status_code == 200
    assert report_id not in {report["id"] for report in client.get("/field-reports/active").json()}


@pytest.mark.parametrize(
    "change",
    [
        {"category": "unsupported_weather"},
        {"severity": "emergency"},
        {"radius_km": 0},
        {"station_id": "AWS_999"},
        {"latitude": 22.7, "longitude": None},
    ],
)
def test_field_report_validation(client, change):
    assert client.post("/field-reports", json=report_payload(**change)).status_code == 422


def test_geographic_and_cluster_nearby_matching(client):
    geographic = client.post(
        "/field-reports",
        json=report_payload(cluster_id=None, latitude=22.7196, longitude=75.8577, radius_km=1),
    ).json()
    assert [station["node_id"] for station in geographic["nearby_stations"]] == ["AWS_001"]
    assert client.get("/field-reports/nearby/AWS_001").status_code == 200
    assert geographic["id"] not in {report["id"] for report in client.get("/field-reports/nearby/AWS_002").json()}


def test_report_expiry_removes_active_context(client):
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    report_id = client.post(
        "/field-reports",
        json=report_payload(expires_at=expired_at, expires_in_minutes=None),
    ).json()["id"]
    report = client.get(f"/field-reports/{report_id}").json()
    assert report["status"] == "expired"
    assert report["verification_state"] == "expired"
    assert report_id not in {item["report_id"] for item in client.get("/monitoring-context/AWS_001").json()["active_context"]}


def test_monitoring_context_is_recommended_not_forced(client):
    report = client.post("/field-reports", json=report_payload()).json()
    context = client.get("/monitoring-context/AWS_001").json()
    assert context["monitoring_mode"] == "heightened"
    assert context["recommended_sample_interval_seconds"] == 2
    assert context["baseline_sample_interval_seconds"] == 5
    assert report["id"] in {item["report_id"] for item in context["active_context"]}
    client.post(f"/field-reports/{report['id']}/resolve", json={})
    assert client.get("/monitoring-context/AWS_001").json()["monitoring_mode"] == "normal"


def test_weather_report_corroborates_only_after_sensor_evidence(client):
    report_id = client.post("/field-reports", json=report_payload()).json()["id"]
    assert client.get(f"/field-reports/{report_id}").json()["verification_state"] == "pending_sensor_confirmation"
    for index in range(6):
        assert ingest(client, "AWS_001", index).status_code == 200
        assert ingest(client, "AWS_002", index).status_code == 200
    report = client.get(f"/field-reports/{report_id}").json()
    assert report["verification_state"] in {"partially_supported", "corroborated"}
    assert report["corroboration_confidence"] >= 60
    assert set(report["verified_by_nodes"]) == {"AWS_001", "AWS_002"}
    assert any("coherent" in item.lower() for item in report["evidence"])


def test_weather_report_without_support_is_not_confirmed(client):
    report_id = client.post("/field-reports", json=report_payload()).json()["id"]
    for index in range(6):
        ingest(client, "AWS_001", index, coherent=False)
        ingest(client, "AWS_002", index, coherent=False)
    report = client.get(f"/field-reports/{report_id}").json()
    assert report["verification_state"] == "not_supported"
    assert report["corroboration_confidence"] < 50


def test_human_report_label_cannot_create_detector_anomaly(client):
    client.post(
        "/field-reports",
        json=report_payload(
            category="suspected_sensor_fault",
            observation="Controller suspects a temperature spike.",
            station_id="AWS_001",
            cluster_id=None,
            until_resolved=True,
        ),
    )
    assert not engine.events
    for index in range(6):
        ingest(client, "AWS_001", index, coherent=False)
        ingest(client, "AWS_002", index, coherent=False)
    assert all(event["anomaly_type"] != "spike" for event in engine.events)
    assert engine.detector.mode == "heuristic_nonfreeze_safety"


def test_dashboard_summary_preserves_contract_and_exposes_node_context(client):
    client.post("/field-reports", json=report_payload())
    summary = client.get("/dashboard/summary").json()
    assert set(summary) == {"system", "nodes", "recent_events", "metrics"}
    assert all("monitoring_context" in node for node in summary["nodes"])


def test_field_report_websocket_events_are_bounded_and_distinct(client):
    with client.websocket_connect("/ws/live") as websocket:
        assert websocket.receive_json()["type"] == "system_status"
        report = client.post("/field-reports", json=report_payload(station_id="AWS_001", cluster_id=None)).json()
        created = receive_websocket_events(
            websocket,
            {"field_report_created", "node_monitoring_context"},
        )
        assert {message["type"] for message in created} >= {"field_report_created", "node_monitoring_context"}
        client.post(f"/field-reports/{report['id']}/resolve", json={})
        resolved = receive_websocket_events(
            websocket,
            {"field_report_resolved", "node_monitoring_context"},
        )
        assert {message["type"] for message in resolved} >= {"field_report_resolved", "node_monitoring_context"}
    assert len(engine.clients) == 0


def test_corroboration_websocket_event_follows_numerical_evidence(client):
    with client.websocket_connect("/ws/live") as websocket:
        assert websocket.receive_json()["type"] == "system_status"
        client.post("/field-reports", json=report_payload())
        receive_websocket_events(websocket, {"field_report_created", "node_monitoring_context"})
        for index in range(6):
            ingest(client, "AWS_001", index)
            ingest(client, "AWS_002", index)
        received = receive_websocket_events(websocket, {"field_report_corroboration"})
        corroboration = next(message["data"] for message in received if message["type"] == "field_report_corroboration")
        assert corroboration["verification_state"] in {"partially_supported", "corroborated"}
        assert corroboration["corroboration_confidence"] >= 60
    assert len(engine.clients) == 0
