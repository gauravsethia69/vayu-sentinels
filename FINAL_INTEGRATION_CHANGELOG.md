# SkyGuard AI — Final Live Hardware Changelog

## Backend

- Removed runtime generated-AWS simulation routes, state, background loop, hardware-trigger endpoint, and simulator WebSocket messages.
- Kept a small legacy input guard so `source=simulator` or `simulation.enabled=true` is rejected rather than contaminating live state.
- Removed heuristic freeze generation completely.
- Final freeze policy:
  - temperature freeze -> Hybrid RF v6 only
  - pressure freeze -> unavailable for now
  - humidity freeze -> unavailable for now
- Kept 20 s minimum same-value persistence + confirmation debounce for ML freeze; strong live freeze threshold is 0.70.
- Kept heuristic non-freeze safety checks for spike/drift/range/cross-sensor/degradation evidence.
- Converted missing DS18B20 to a sensor-level quality-gate data-loss event while station communication remains healthy.
- Allows raw `-127.0 C` DS18B20 telemetry to reach the quality gate; it is preserved as raw evidence and classified as data corruption.
- Quality-gate events are authoritative for the affected channel and prevent contradictory duplicate heuristic events.
- Preserved adaptive communication timing, adaptive peer freshness, recovery hysteresis, peer consensus, failover, trusted values, provenance, maintenance, vision, field/community intelligence, MQTT and `/ingest`.
- `packet_jitter` remains removed.

## Frontend

- Removed the Simulations navigation page, runtime simulator status/control APIs, simulation WebSocket state, and simulation status pill.
- Homepage now describes physical AWS-001 hardware fault testing instead of fake generated AWS simulation.
- Overview station cards no longer show the red active-anomaly strip/border/sparkline styling that caused visual jitter.
- Hardware-test copy now states that only the DS18B20 telemetry channel is injected while DHT22/BMP280 remain live.
- Removed obsolete simulator-only CSS from the active frontend source.

## AWS-001 firmware

- Removed `HTTPClient` and `/simulation/hardware-trigger` calls.
- Added a non-blocking, debounced one-fault-at-a-time local telemetry fault state machine.
- Spike GPIO32: DS-only +6/+4/+2 C pulse.
- Freeze GPIO33: DS-only held value for 90 s.
- Drift GPIO14: DS-only +2 C ramp over 20 s.
- Data Loss GPIO13: DS field omitted for 12 s; packets and all other real channels continue.
- Corruption GPIO15: DS telemetry `-127.0 C` for 8 s; other channels remain real.
- Button release does not cancel a test; one press starts a timed test and it auto-recovers.
- No fault label is sent to MQTT/backend.
- OLED shows real local environment and local test status; Serial prints real vs transmitted DS values.
- MQTT/Wi-Fi reconnect and 2 s publish cadence remain non-blocking.

## Final pre-deployment UI add-ons
- Replaced the manual/simulated Vision Intelligence controls with a real browser webcam prototype for camera blockage and large foreground obstruction detection.
- Camera observations are contextual only and do not alter sensor measurements or the Hybrid RF v6 pipeline.
- Added an offline Admin Sensor Datasheet Library with the uploaded DS18B20, DHT22, BMP280, ESP32-WROOM-32, SW-420, and KY-038 PDFs.
- SW-420 and KY-038 are labelled as additional hardware references, not installed SkyGuard AWS sensors.
