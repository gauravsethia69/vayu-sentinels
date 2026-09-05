from app.services import engine
from app.database import connect
from conftest import run_async
from conftest import receive_websocket_events
from datetime import datetime, timedelta, timezone
import pytest


@pytest.fixture(autouse=True)
def remove_portal_test_reports():
    with connect() as db:
        before = {row[0] for row in db.execute("SELECT id FROM field_reports")}
        db.execute("DELETE FROM field_reports WHERE source='public_user' AND (observation LIKE 'Dark clouds are approaching rapidly%' OR observation LIKE 'A temperature spike appears%')")
    yield
    with connect() as db:
        current = {row[0] for row in db.execute("SELECT id FROM field_reports")}
        for report_id in current - before:
            db.execute("DELETE FROM public_report_actions WHERE report_id=?", (report_id,))
            db.execute("DELETE FROM field_reports WHERE id=?", (report_id,))


def login(client):
    response = client.post("/auth/login", json={"name": "Gaurav Sethia", "password": "23"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def report_payload(**changes):
    payload = {
        "category": "clouds_approaching",
        "observation": "Dark clouds are approaching rapidly from the west.",
        "station_scope": ["AWS_001", "AWS_002"],
        "direction": "west",
        "severity": "moderate",
        "reporter_confidence": "high",
        "location_label": "Near AWS-001",
        "radius_km": 10,
    }
    payload.update(changes)
    return payload


def test_demo_admin_login_session_and_logout(client):
    assert client.post("/auth/login", json={"name": "Gaurav Sethia", "password": "wrong"}).status_code == 401
    headers = login(client)
    assert client.get("/auth/me", headers=headers).json()["role"] == "admin"
    assert client.post("/auth/logout", headers=headers).json() == {"authenticated": False}
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_admin_reports_require_authentication(client):
    assert client.get("/admin/public-reports").status_code == 401
    assert client.get("/admin/public-reports", headers=login(client)).status_code == 200


def test_public_report_creation_is_anonymous_and_listed(client):
    response = client.post("/public/reports", json=report_payload())
    assert response.status_code == 201
    report = response.json()
    assert report["reporter_type"] == "community_user"
    assert report["reporter_name"] is None
    assert {node["node_id"] for node in report["nearby_stations"]} == {"AWS_001", "AWS_002", "AWS_003"}
    assert report["id"] in {item["id"] for item in client.get("/public/reports").json()}


def test_public_report_validation(client):
    assert client.post("/public/reports", json=report_payload(observation="short")).status_code == 422
    assert client.post("/public/reports", json=report_payload(category="invented_weather")).status_code == 422
    assert client.post("/public/reports", json=report_payload(station_scope=["AWS_999"])).status_code == 422


def test_admin_can_verify_and_resolve_public_report(client):
    report = client.post("/public/reports", json=report_payload()).json()
    headers = login(client)
    verified = client.post(f"/admin/public-reports/{report['id']}/verify", headers=headers, json={}).json()
    assert verified["verification_state"] == "manager_verified"
    resolved = client.post(f"/admin/public-reports/{report['id']}/resolve", headers=headers, json={}).json()
    assert resolved["status"] == "resolved"
    assert resolved["verification_state"] == "resolved"


def test_public_report_does_not_create_detector_event(client):
    engine._reset_all_state_for_testing()
    before = len(engine.events)
    response = client.post("/public/reports", json=report_payload(category="custom_observation", observation="A temperature spike appears to be happening nearby."))
    assert response.status_code == 201
    assert len(engine.events) == before
    start = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    for index in range(5):
        for node_id in ("AWS_001", "AWS_002"):
            client.post("/ingest", json={"node_id": node_id, "timestamp": (start + timedelta(seconds=index)).isoformat(), "temperature_c": 29.0 + index * 0.03, "humidity_pct": 70.0 + index * 0.05, "pressure_hpa": 1007.0 - index * 0.02, "source": "esp32"})
    client.post("/ingest", json={"node_id": "AWS_001", "timestamp": (start + timedelta(seconds=5)).isoformat(), "temperature_c": 48.0, "humidity_pct": 70.2, "pressure_hpa": 1006.9, "source": "esp32"})
    assert any(event["anomaly_type"] == "spike" for event in engine.events)


def test_public_report_websocket_alias(client):
    with client.websocket_connect("/ws/live") as websocket:
        assert websocket.receive_json()["type"] == "system_status"
        response = client.post("/public/reports", json=report_payload())
        assert response.status_code == 201
        messages = receive_websocket_events(websocket, {"public_report_created"})
        event = next(item for item in messages if item["type"] == "public_report_created")
        assert event["data"]["reporter_type"] == "community_user"
