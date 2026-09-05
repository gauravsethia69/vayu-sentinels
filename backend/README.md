# SkyGuard AI Backend — Final SIH Runtime

FastAPI + MQTT backend for three real Automatic Weather Stations (`AWS_001`, `AWS_002`, `AWS_003`).

## Runtime pipeline

`ESP32 -> Mosquitto -> MQTT subscriber -> ReadingIn validation -> normalization -> quality gates -> Hybrid RF v6 + non-freeze safety checks -> peer/trusted correction -> SQLite -> WebSocket/frontend`

MQTT and `POST /ingest` reuse the same processing pipeline; MQTT does not write SQLite directly.

## ML contract

Model artifact: `app/models/skyguard_final_hybrid_rf_v6.joblib`

Random Forest environmental classes:

- `normal`
- `temperature_spike`
- `temperature_drift`
- `temperature_freeze`

The model uses 30 causal 30 s / 60 s temporal and cross-sensor features.

### Final freeze policy

- Temperature freeze: **Random Forest only**.
- Pressure freeze: disabled/unavailable until a dedicated larger dataset/model is trained.
- Humidity freeze: disabled/unavailable until a dedicated larger dataset/model is trained.
- If the RF is unavailable, the backend does not invent/fallback to heuristic temperature freeze.

The live ML freeze gate additionally requires sustained DS18B20 same-value duration and confirmation debounce.

## Deterministic quality/communication responsibilities

- `sensor_missing` -> sensor-level data-loss event.
- `data_corruption` -> quality-gate corruption event; raw evidence is preserved.
- Complete packet absence -> adaptive communication watchdog.
- Communication states: healthy / delayed / communication_failure / recovering.
- Peer freshness adapts to observed normal packet intervals.
- `packet_jitter` is not a standalone anomaly in this release.

## Runtime simulator

The old generated AWS runtime simulator is removed. `/simulation/*` control routes do not exist.

For backward compatibility only, a payload may include `simulation.enabled=false`. `source=simulator` or `simulation.enabled=true` is rejected and cannot enter live state.

## Start

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Useful endpoints:

```text
GET  /health
GET  /ml/status
POST /ingest
GET  /nodes
GET  /nodes/{node_id}/latest
GET  /nodes/{node_id}/history
GET  /events
GET  /trusted-stream
GET  /peer-consensus/{node_id}
GET  /sensor-health/{node_id}
GET  /dashboard/summary
WS   /ws/live
```

MQTT topic wildcard:

```text
skyguard/aws/+/telemetry
```

## Validation

Final backend test suite: **66 passed**.
