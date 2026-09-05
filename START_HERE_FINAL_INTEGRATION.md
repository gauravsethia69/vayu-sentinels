# SkyGuard AI — FINAL SIH Live-Hardware Build

This is the final runtime package for the three real AWS stations. The old generated AWS runtime simulator has been removed.

## Final runtime architecture

`AWS_001 / AWS_002 / AWS_003 -> MQTT (Mosquitto) -> FastAPI -> normalization -> deterministic quality gates -> Hybrid RF v6 -> peer/trusted correction -> SQLite -> WebSocket -> React dashboard`

### What Hybrid RF v6 currently detects

The trained environmental Random Forest classes are:

- `normal`
- `temperature_spike`
- `temperature_drift`
- `temperature_freeze`

Temperature freeze is **ML-only** in this release. The heuristic detector cannot raise a freeze event.

Pressure freeze and humidity freeze are intentionally **not detected yet**; they are reserved for a later model trained with a larger dedicated dataset.

Deterministic quality gates still handle:

- missing sensor channel -> `data_loss` / `sensor_missing`
- corrupted sensor channel -> `data_corruption`
- complete packet absence -> adaptive communication watchdog (`delayed` / `communication_failure` / recovery)

`packet_jitter` has been removed as a standalone anomaly.

## AWS-001 physical fault buttons

Flash `firmware/AWS_1_FINAL.ino` (also provided at the package root).

The button never tells the backend the answer. It changes only the outgoing DS18B20 telemetry copy while DHT22 and BMP280 remain live.

| Button | GPIO | Local telemetry test | Typical duration | Detection path |
|---|---:|---|---:|---|
| Spike | 32 | DS18B20 +6 -> +4 -> +2 C pulse | 12 s | RF `temperature_spike` + existing safety evidence |
| Freeze | 33 | Hold captured DS18B20 value exactly | 90 s | **RF `temperature_freeze` only** |
| Drift | 14 | Smooth DS18B20 +2 C ramp | 20 s | RF `temperature_drift` + existing safety evidence |
| Data Loss | 13 | Omit DS18B20 field; keep packet/references live | 12 s | deterministic `sensor_missing` -> sensor-level `data_loss` |
| Corruption | 15 | Send DS18B20 `-127.0` only | 8 s | deterministic `data_corruption` |

GPIO15 is an ESP32 strapping pin: **do not hold the corruption button LOW during power-on/reset.**

The Freeze test is deliberately long because the trained RF needs sustained temporal evidence; with representative AWS-001-like data it becomes strongly freeze-like after roughly one minute, then the backend also requires persistence/confirmation before exposing the event.

## 1. Start Mosquitto

Example Administrator PowerShell:

```powershell
cd "C:\Program Files\mosquitto"
.\mosquitto.exe -c "C:\path\to\backend\mosquitto.local.conf" -v
```

Keep the broker window open.

## 2. Start backend

```powershell
cd C:\path\to\backend
py -m venv venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify:

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8000/ml/status
```

Expected ML essentials:

```text
loaded: true
model_version: skyguard_final_hybrid_rf_v6
feature_count: 30
detector_mode: hybrid_rf_v6+heuristic_nonfreeze_safety
```

If the RF artifact cannot load, non-freeze safety checks continue, but **temperature freeze is intentionally unavailable** rather than falling back to heuristic freeze.

## 3. Start frontend

The ZIP intentionally excludes `node_modules` because native packages are OS-specific.

```powershell
cd C:\path\to\frontend
npm install
npm run dev
```

Default endpoints:

- REST: `http://127.0.0.1:8000`
- WebSocket: `ws://127.0.0.1:8000/ws/live`

## 4. Flash AWS-001

Before flashing, update these values in `firmware/AWS_1_FINAL.ino` if your LAN changed:

```cpp
const char* WIFI_SSID = "...";
const char* WIFI_PASSWORD = "...";
const char* MQTT_BROKER = "<laptop IPv4>";
```

Keep topic:

```text
skyguard/aws/AWS_001/telemetry
```

AWS-002/AWS-003 firmware does not need this fault-injection state machine.

## Demo truthfulness

Use this accuracy wording:

> **98.84% offline test accuracy on realistic synthetic fault injections generated over real three-station AWS baseline data.**

Do not call it field-validated real-world accuracy.

## Final validation in this package

- Backend: **66 / 66 pytest tests passed**.
- Hybrid RF v6 artifact: loads successfully; 30 environmental features; classes unchanged.
- Runtime simulation API: removed; simulator payloads are rejected from live ingest.
- Heuristic freeze: removed; pressure/humidity freeze unavailable; temperature freeze ML-only.
- Packet jitter anomaly: removed.
- Frontend: TypeScript `tsc --noEmit` passed after final source cleanup.
- Vite build could not be executed in this Linux container using the uploaded Windows dependency tree because its platform-native Rolldown binding is Windows-specific. The release excludes `node_modules`; run `npm install` on the target Windows laptop before `npm run build`/`npm run dev`.
- AWS-001 firmware: source was syntax-checked with C++/Arduino API stubs. `arduino-cli` and board libraries were not installed here, so a real ESP32 board compile must still be done in Arduino IDE/CLI on the target machine.

## Camera demo note
Use the admin dashboard through `http://localhost:5173` (or an HTTPS deployment) when demonstrating Vision Intelligence. Browser webcam permission is normally unavailable on an insecure `http://<LAN-IP>` origin. Open **Vision Intelligence**, click **Start camera**, keep the normal scene clear during the ~3-second calibration, then place a large object in the central view or cover the camera.

## Datasheet library
Open **Sensor Maintenance** in the admin dashboard. The bundled sensor PDFs are available offline under **Sensor datasheet library** with View PDF and Download actions.
