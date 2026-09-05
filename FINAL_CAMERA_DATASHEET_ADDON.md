# Final pre-deployment add-ons

## 1. Vision Intelligence: live browser obstruction prototype

The existing Admin > Vision Intelligence page now has a real webcam workflow using the browser `getUserMedia()` API.

Behavior:
- Start Camera requests webcam permission.
- The first ~3 seconds calibrate the normal scene.
- A lightweight browser detector downsamples the central camera area and checks frame change, darkness, and image-detail/texture loss.
- Three consecutive detections are required before the status becomes `OBSTACLE DETECTED` or `CAMERA BLOCKED`.
- The detector posts contextual observations through the existing `/vision/observations` endpoint with source `camera`.
- Weather sensor readings, Hybrid RF v6, trusted-value logic, and peer failover are not modified by camera observations.
- This is intentionally labelled a prototype scene-change/occlusion detector, not trained object recognition.

For the demo, open the frontend through `http://localhost:5173` (or HTTPS). Browsers normally block webcam access on non-secure LAN HTTP origins.

## 2. Offline sensor datasheet library

Admin > Sensor Maintenance now contains an offline datasheet library. The PDFs are bundled under `frontend/public/datasheets/`.

Installed SkyGuard prototype references:
- DS18B20
- DHT22 / AM2302
- BMP280
- ESP32-WROOM-32

Additional uploaded hardware references, clearly labelled as such:
- SW-420 vibration sensor
- KY-038 sound sensor

Each card provides View PDF and Download actions. Existing manufacturer specification metadata from the backend remains unchanged.

## Validation

- TypeScript: `tsc --noEmit` PASSED using the dependency tree from the previously uploaded frontend.
- Vite production build: not executable in this Linux environment because the uploaded dependency tree contains incompatible/missing Rolldown native bindings. This is the same platform-specific dependency limitation seen previously; run `npm install` on the Windows deployment machine and then `npm run build` or `npm run dev`.
- All six uploaded PDFs passed PDF metadata parsing (`pdfinfo`) and were copied without editing.
- Backend source was not modified for these two add-ons.
- Hybrid RF v6, MQTT, communication monitoring, peer failover, trusted values, and AWS fault-button firmware are unchanged.
