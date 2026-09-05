# SkyGuard Render + HiveMQ deployment

Backend MQTT now supports username/password + TLS through environment variables.
The frontend sends a 30-second WebSocket heartbeat while the dashboard is open so a Render Free web service receives inbound WebSocket traffic during the live demo.

HiveMQ host configured in render.yaml:
`2a39147c05e34fca81922447ecc81a83.s1.eu.hivemq.cloud:8883`

Secrets are NOT bundled. Enter `SKYGUARD_MQTT_USERNAME` and `SKYGUARD_MQTT_PASSWORD` in Render.
After Vercel deployment, set `SKYGUARD_CORS_ALLOWED_ORIGINS` to the exact Vercel production URL and redeploy.
