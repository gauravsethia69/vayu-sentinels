# SkyGuard AI Frontend — Final Live Monitoring UI

React + TypeScript dashboard for the final real-hardware SkyGuard runtime.

The old generated-AWS simulation page and controls have been removed. The UI consumes only live backend REST/WebSocket state for normal operation.

## Main admin views

- Overview
- Live Sensors
- Analytics
- Anomalies
- Field Intelligence
- Community Intelligence
- Vision Intelligence
- Sensor Maintenance
- Trusted Data
- System Health

The homepage contains an **illustrative Hardware Fault Testing** section explaining the five physical AWS-001 buttons. Those buttons exist on the ESP32; the web preview does not inject faults into the backend.

## Start

```powershell
npm install
npm run dev
```

Default backend addresses are configured for localhost FastAPI REST/WebSocket.

## Data behavior

- Real station values remain visible during brief network delays; communication state is shown separately.
- Overview AWS cards use stable dimensions and do not render a flashing red anomaly strip beneath the readings.
- Detailed anomaly/event pages still expose actual backend anomalies and ML evidence.
- Hybrid RF assessment/model metadata is displayed when available.
- No frontend component calculates ML classes; backend remains the source of truth.

## Validation

Final TypeScript check:

```text
tsc --noEmit -> passed
```

The uploaded dependency tree contained Windows-native Vite/Rolldown binaries, so a Linux Vite production build could not be run in this container. `node_modules` is excluded from the release; run `npm install` on the target Windows system before `npm run build` or `npm run dev`.
