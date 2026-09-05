from datetime import datetime, timedelta, timezone
import time

import pytest

from conftest import run_async
from app.services import engine, ml_service


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
BASE_MONOTONIC = 1000.0


def run(coroutine):
    return run_async(coroutine)


def live_reading(
    elapsed,
    *,
    node_id="AWS_001",
    temperature=28.4,
    reference_temperature=28.3,
    humidity=70.0,
    pressure=1007.0,
    sequence=None,
):
    timestamp = (BASE_TIME + timedelta(seconds=elapsed)).isoformat()
    sequence = int(elapsed) if sequence is None else sequence
    return {
        "node_id": node_id,
        "timestamp": timestamp,
        "sensor_timestamp": timestamp,
        "_received_at": timestamp,
        "_received_monotonic": BASE_MONOTONIC + elapsed,
        "temperature_c": temperature,
        "primary_temperature_c": temperature,
        "reference_temperature_c": reference_temperature,
        "temperature_consensus_c": reference_temperature,
        "humidity_pct": humidity,
        "pressure_hpa": pressure,
        "raw_sensors": {
            "ds18b20_temperature_c": temperature,
            "dht22_temperature_c": reference_temperature,
            "dht22_humidity_pct": humidity,
            "bmp280_temperature_c": reference_temperature + 0.02,
            "bmp280_pressure_hpa": pressure,
        },
        "device": {"sequence": sequence, "wifi_rssi": -52},
        "source": "esp32",
    }


def normal_ml_assessment():
    return {
        "enabled": True,
        "loaded": True,
        "ready": True,
        "prediction": "normal",
        "confidence": 0.99,
        "probabilities": {"normal": 0.99},
        "feature_summary": {"ds_same_value_duration_s": 0.0},
        "model_version": "skyguard_final_hybrid_rf_v6",
        "detector_mode": "hybrid_rf_v6",
    }


def freeze_ml_assessment(duration):
    return {
        "enabled": True,
        "loaded": True,
        "ready": True,
        "prediction": "temperature_freeze",
        "confidence": 0.95,
        "probabilities": {"normal": 0.03, "temperature_freeze": 0.95},
        "feature_summary": {
            "ds_same_value_duration_s": float(duration),
            "ds_range_30s": 0.0,
            "ds_slope_c_per_min_30s": 0.0,
        },
        "window_span_seconds": float(duration),
        "model_version": "skyguard_final_hybrid_rf_v6",
        "detector_mode": "hybrid_rf_v6",
    }


@pytest.mark.parametrize(("interval", "expected_warning"), [(2.0, 6.0), (5.0, 12.5)])
def test_regular_real_packet_cadence_remains_healthy(interval, expected_warning):
    for index in range(7):
        run(engine.process(live_reading(index * interval, sequence=index)))
        assert engine.communication_state["AWS_001"] == "healthy"

    expected, warning, failure = engine.communication_timing("AWS_001")
    assert expected == pytest.approx(interval)
    assert warning == pytest.approx(expected_warning)
    assert failure >= warning + expected
    assert all(event["anomaly_type"] != "packet_jitter" for event in engine.events)


def test_five_second_station_tolerates_six_to_eight_second_variation():
    elapsed = 0.0
    for index, interval in enumerate((0, 5, 5, 5, 5, 5, 8, 6)):
        elapsed += interval
        run(engine.process(live_reading(elapsed, sequence=index)))
        assert engine.communication_state["AWS_001"] == "healthy"

    expected, warning, _ = engine.communication_timing("AWS_001")
    assert expected == pytest.approx(5.0)
    assert warning == pytest.approx(12.5)


def test_watchdog_delayed_offline_recovering_then_healthy(monkeypatch):
    monkeypatch.setattr("app.services.time.monotonic", lambda: engine.last_seen.get("AWS_001", BASE_MONOTONIC))
    for index in range(6):
        run(engine.process(live_reading(index * 5, sequence=index)))

    last_seen = engine.last_seen["AWS_001"]
    _, warning, failure = engine.communication_timing("AWS_001")

    assert run(engine._check_communication_unlocked(last_seen + warning - 0.1)) == []
    assert engine.communication_state["AWS_001"] == "healthy"

    delayed = run(engine._check_communication_unlocked(last_seen + warning + 0.1))
    assert any(item["state"] == "delayed" for item in delayed)
    assert engine.communication_state["AWS_001"] == "delayed"

    offline = run(engine._check_communication_unlocked(last_seen + failure + 0.1))
    assert any(item["anomaly_type"] == "communication_failure" for item in offline)
    assert engine.communication_state["AWS_001"] == "communication_failure"

    reconnect_elapsed = 25 + failure + 0.2
    run(engine.process(live_reading(reconnect_elapsed, sequence=6)))
    assert engine.communication_state["AWS_001"] == "recovering"

    for index in range(1, 4):
        run(engine.process(live_reading(reconnect_elapsed + index * 5, sequence=6 + index)))
        assert engine.communication_state["AWS_001"] == "recovering"

    run(engine.process(live_reading(reconnect_elapsed + 20, sequence=10)))
    assert engine.communication_state["AWS_001"] == "healthy"


def test_peer_freshness_tracks_peer_expected_interval():
    state = engine._live_state
    peer_id = "AWS_002"
    state.arrival_intervals[peer_id].extend([5.0] * 10)
    state.hist[peer_id].append(live_reading(0, node_id=peer_id, sequence=1))
    state.communication_state[peer_id] = "healthy"
    now = time.monotonic()
    state.last_seen[peer_id] = now - 5.1

    assert engine.peer_freshness_threshold(peer_id) == pytest.approx(15.0)
    assert [peer["node_id"] for peer in engine.get_healthy_peers("AWS_001")] == [peer_id]

    state.last_seen[peer_id] = now - 15.1
    assert engine.get_healthy_peers("AWS_001") == []


def test_runtime_simulation_payload_is_rejected(client):
    payload = {
        "node_id": "AWS_001",
        "sensors": {
            "ds18b20_temperature_c": 28.4,
            "dht22_temperature_c": 28.3,
            "dht22_humidity_pct": 70.0,
            "bmp280_temperature_c": 28.3,
            "bmp280_pressure_hpa": 1007.0,
        },
        "source": "simulator",
    }
    assert client.post("/ingest", json=payload).status_code == 422


@pytest.mark.parametrize("parameter", ["temperature", "humidity", "pressure"])
def test_short_identical_runs_are_not_frozen(monkeypatch, parameter):
    monkeypatch.setattr(ml_service, "assess", lambda reading, history: normal_ml_assessment())
    for index in range(3):
        result = run(engine.process(live_reading(index * 5, reference_temperature=28.3 + index * 0.08, sequence=index)))
        assert not any(event.get("anomaly_type") == "freeze" and event.get("parameter") == parameter for event in result["events"])


def test_long_stable_temperature_with_ml_normal_never_becomes_freeze(monkeypatch):
    monkeypatch.setattr(ml_service, "assess", lambda reading, history: normal_ml_assessment())
    for index in range(20):
        result = run(engine.process(live_reading(index * 5, temperature=28.4, reference_temperature=28.3 + index * 0.02, sequence=index)))
        assert not any(event.get("anomaly_type") == "freeze" for event in result["events"])
    assert engine.sensor_state["AWS_001"]["temperature"] != "anomalous"


def test_ml_only_temperature_freeze_requires_duration_and_confirmation(monkeypatch):
    def fake_assess(reading, history):
        elapsed = (datetime.fromisoformat(reading["sensor_timestamp"]) - BASE_TIME).total_seconds()
        return freeze_ml_assessment(elapsed)

    monkeypatch.setattr(ml_service, "assess", fake_assess)
    seen = []
    for index in range(7):
        result = run(engine.process(live_reading(index * 5, temperature=28.4, reference_temperature=28.3 + index * 0.05, sequence=index)))
        seen.extend(event for event in result["events"] if event.get("anomaly_type") == "freeze")
        if index <= 4:  # 0..20 s: insufficient duration / first candidate only
            assert not seen

    assert seen
    event = seen[0]
    assert event["parameter"] == "temperature"
    assert event["ml_source"] == "environmental_random_forest"
    assert event["ml_prediction"] == "temperature_freeze"
    assert event["freeze_duration_seconds"] >= 20
    assert event["freeze_confirmation_evaluations"] >= 2


def test_pressure_and_humidity_freeze_are_never_allowed(monkeypatch):
    def fake_heuristic(reading, history, peer):
        return [
            {"node_id": reading["node_id"], "timestamp": reading["timestamp"], "anomaly_type": "freeze", "event_type": "anomaly", "parameter": "pressure", "confidence": 99, "severity": "high", "message": "fake", "reasons": [], "factor_contributions": {}, "recommended_action": "none", "detector_mode": "heuristic_nonfreeze_safety", "model_version": "test"},
            {"node_id": reading["node_id"], "timestamp": reading["timestamp"], "anomaly_type": "freeze", "event_type": "anomaly", "parameter": "humidity", "confidence": 99, "severity": "high", "message": "fake", "reasons": [], "factor_contributions": {}, "recommended_action": "none", "detector_mode": "heuristic_nonfreeze_safety", "model_version": "test"},
        ]

    monkeypatch.setattr(engine.detector, "detect", fake_heuristic)
    monkeypatch.setattr(ml_service, "assess", lambda reading, history: normal_ml_assessment())
    result = run(engine.process(live_reading(0)))
    assert not any(event.get("anomaly_type") == "freeze" for event in result["events"])
