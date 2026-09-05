# SkyGuard AI — Final Validation Report

## Release scope

This package is the final **live-hardware** runtime build. The generated AWS runtime simulator has been removed. AWS_001/002/003 use real telemetry; AWS_001's five buttons locally alter only the outgoing DS18B20 test copy so the backend must infer the fault from telemetry.

## Backend verification

- `pytest -q`: **66 passed / 66 total** in 5.47 s on the clean release copy.
- `python -m compileall -q app`: **PASS**.
- Hybrid RF artifact loads successfully.
- Model status:
  - `loaded = true`
  - `model_version = skyguard_final_hybrid_rf_v6`
  - `detector_mode = hybrid_rf_v6+heuristic_nonfreeze_safety`
  - `feature_count = 30`
  - live RF confidence threshold = `0.70`
- The shipped `.joblib` SHA-256 is identical to the model in the input NoPacketJitter package; the trained model was **not retrained or modified**.
- Runtime `/simulation/*` API routes are removed; explicit simulator payloads are rejected from live ingest.
- `packet_jitter` anomaly generation remains removed.
- Heuristic freeze generation is removed.
- Final freeze policy:
  - temperature freeze: **Hybrid RF v6 only**, plus >=20 s same-value persistence and confirmation debounce.
  - pressure freeze: intentionally unavailable for now.
  - humidity freeze: intentionally unavailable for now.
- Missing DS18B20 is accepted as sensor-level data loss while live station communication remains healthy.
- Raw DS18B20 `-127.0` reaches the deterministic quality gate, is preserved as raw evidence, and is classified as data corruption instead of being rejected by request validation.
- Adaptive communication timing, recovery hysteresis, adaptive peer freshness, peer consensus/failover and trusted-value provenance remain covered by the passing backend suite.

### Test warning note

Pytest emitted Python `ResourceWarning` messages about SQLite connections during some tests. They did **not** cause test failures; all 66 tests passed. This report does not present those warnings as functional failures.

## Frontend verification

- `./node_modules/.bin/tsc --noEmit`: **PASS** against the final frontend source before packaging.
- Active frontend source contains no runtime simulation UI/state handling.
- The Simulations page/status controls are removed.
- Main AWS overview cards no longer render the red active-anomaly strip/border/sparkline behavior.
- Homepage language now describes **Hardware Fault Testing**, not generated fake AWS simulation.
- `node_modules` and the previous `dist` directory are intentionally excluded from this release.

### Production-build environment note

`npm run build` could not complete inside the Linux validation container because the uploaded dependency tree contained the Windows-native Rolldown package and did not contain `@rolldown/binding-linux-x64-gnu`. TypeScript itself passed. On the target Windows machine run:

```powershell
cd frontend
npm install
npm run build
```

or for the demo:

```powershell
npm run dev
```

This is a platform-specific dependency issue, not a TypeScript source error.

## AWS_001 firmware verification

- Final sketch: `firmware/AWS_1_FINAL.ino`.
- `g++ -std=c++17 -fsyntax-only` with minimal Arduino API stubs: **PASS**.
- `arduino-cli` / the ESP32 board package and physical Arduino libraries were not installed in this environment, so a genuine ESP32 board compile was **not** claimed.
- Existing wiring and 2 s telemetry cadence are preserved.
- Physical buttons are debounced and non-blocking; one press starts one timed test.
- No button/fault label is sent to FastAPI or MQTT.
- DHT22 and BMP280 stay real during every test.

| Button | GPIO | Outgoing DS18B20 test | Duration | Expected detection path |
|---|---:|---|---:|---|
| Spike | 32 | +6 C -> +4 C -> +2 C | 12 s | RF temperature spike |
| Freeze | 33 | Hold captured DS value exactly | 90 s | **RF temperature freeze only** |
| Drift | 14 | Smooth +2 C ramp | 20 s | RF temperature drift |
| Data Loss | 13 | Omit DS field; packets/other sensors continue | 12 s | deterministic sensor missing/data loss |
| Corruption | 15 | DS = -127.0 C | 8 s | deterministic data corruption |

GPIO15 remains an ESP32 strapping pin: do not hold the corruption button LOW while powering on/resetting AWS_001.

## Model provenance / accuracy wording

For presentations, use:

> **98.84% offline test accuracy on realistic synthetic fault injections generated over real three-station AWS baseline data.**

Do not present 98.84% as field-validated real-world accuracy.
