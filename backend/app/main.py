import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from .mqtt_service import MQTTService
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import (
    CORS_ALLOWED_ORIGINS,
    FIELD_REPORT_CATEGORIES,
    FIELD_REPORT_SEVERITIES,
    MQTT_HOST,
    MQTT_PORT,
    MQTT_TOPIC,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_TLS,
    NODES,
)
from .auth import auth_service, require_admin
from .database import init_db, load_latest_reading, load_reading_history, load_reading_records
from .field_reports import FieldReportService
from .normalization import normalize_reading
from .maintenance import MaintenanceService
from .ml_detector import ml_service
from .sensor_registry import sensor_specs
from .vision import VisionService
from .schemas import (
    FieldReportCreate,
    FieldReportResolve,
    FieldReportUpdate,
    ReadingIn,
    AdminReportAction,
    DemoLoginIn,
    PublicReportCreate,
    VisionObservationIn,
)
from .services import engine

logger = logging.getLogger("uvicorn.error")


field_reports = FieldReportService(engine)
engine.context_service = field_reports
maintenance = MaintenanceService(engine)
vision = VisionService(engine, maintenance)
engine.maintenance_service = maintenance


async def process_reading(body: ReadingIn):
    """Run one validated reading through SkyGuard's existing ingest pipeline."""
    if body.node_id not in NODES:
        raise ValueError(f"Unknown SkyGuard node: {body.node_id}")

    data = body.model_dump(exclude_none=True)
    if body.timestamp:
        data["timestamp"] = body.timestamp.isoformat()

    canonical = normalize_reading(data)
    return await engine.process(canonical)


async def process_external_reading(payload: dict):
    """MQTT entry point: validate JSON, then reuse the normal ingest pipeline."""
    body = ReadingIn.model_validate(payload)
    return await process_reading(body)


mqtt_service = MQTTService(
    processor=process_external_reading,
    host=MQTT_HOST,
    port=MQTT_PORT,
    topic=MQTT_TOPIC,
    username=MQTT_USERNAME,
    password=MQTT_PASSWORD,
    tls=MQTT_TLS,
)


@asynccontextmanager
async def lifespan(app):
    try:
        init_db()
    except Exception:
        logger.exception("SkyGuard database initialization failed")
        raise
    logger.info("SkyGuard database initialized")
    await engine.start_monitor()
    logger.info("SkyGuard communication monitor started")
    try:
        mqtt_service.start(asyncio.get_running_loop())
        logger.info("SkyGuard startup complete; optional MQTT connects in background")
        yield
    finally:
        try:
            await asyncio.to_thread(mqtt_service.stop)
        finally:
            await engine.shutdown()
            logger.info("SkyGuard shutdown complete")


app = FastAPI(title="SkyGuard AI Backend", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_node(node_id):
    if node_id not in NODES:
        raise HTTPException(404, "Unknown node")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "skyguard-backend",
        "detector_mode": ml_service.combined_mode,
        "ml": ml_service.status(),
        "mqtt": mqtt_service.status(),
        "websocket_clients": len(engine.clients),
    }


@app.get("/ml/status")
def ml_status():
    return ml_service.status()


@app.post("/auth/login")
def auth_login(body: DemoLoginIn):
    session = auth_service.login(body.name, body.password)
    if session is None:
        raise HTTPException(401, "Invalid administrator name or password")
    return session


@app.get("/auth/me")
def auth_me(admin=Depends(require_admin)):
    return {key: value for key, value in admin.items() if key != "token"}


@app.post("/auth/logout")
def auth_logout(admin=Depends(require_admin)):
    auth_service.logout(admin["token"])
    return {"authenticated": False}


@app.get("/nodes")
def nodes():
    return [
        {
            "node_id": node,
            "active": bool(engine.hist[node]),
            "communication_state": engine.communication_state[node],
        }
        for node in NODES
    ]


@app.get("/nodes/{node_id}/latest")
def latest(node_id: str):
    require_node(node_id)
    reading = engine.hist[node_id][-1] if engine.hist[node_id] else load_latest_reading(node_id)
    if reading is None:
        raise HTTPException(404, "No readings yet")
    return reading


@app.get("/nodes/{node_id}/history")
def history(node_id: str, limit: int = Query(default=100, ge=1, le=500)):
    require_node(node_id)
    return load_reading_history(node_id, limit)


@app.get("/nodes/{node_id}/records")
def records(node_id: str, limit: int = Query(default=100, ge=1, le=500)):
    require_node(node_id)
    return {"node_id": node_id, "records": load_reading_records(node_id, limit)}


@app.get("/events")
def events(limit: int = Query(default=100, ge=1, le=500)):
    return list(engine.events)[:limit]


@app.get("/sensor-health")
def all_health():
    return [engine.health_payload(node) for node in NODES]


@app.get("/sensor-health/{node_id}")
def node_health(node_id: str):
    require_node(node_id)
    return engine.health_payload(node_id)


@app.get("/trusted-stream")
def trusted_stream(limit: int = Query(default=100, ge=1, le=500)):
    return load_reading_records(limit=limit)


@app.get("/metrics")
def metrics():
    return engine.metrics()


@app.get("/dashboard/summary")
async def dashboard_summary():
    await field_reports.refresh_expiry()
    summary = engine.dashboard_summary()
    summary["system"]["detector_mode"] = ml_service.combined_mode
    summary["system"]["ml_model"] = ml_service.status()
    contexts = {item["node_id"]: item for item in field_reports.contexts()}
    for node in summary["nodes"]:
        if node["latest"] is None:
            node["latest"] = load_latest_reading(node["node_id"])
        node["monitoring_context"] = contexts[node["node_id"]]
        node["peer_failovers"] = list(engine.peer_failovers[node["node_id"]].values())
    active_reports = await field_reports.active()
    summary["system"].update(
        stations_online=f"{summary['metrics']['active_nodes']} / {len(NODES)}",
        vision_systems_online=sum(item["camera_status"] == "online" for item in vision.status()),
        peer_failovers_active=sum(len(items) for items in engine.peer_failovers.values()),
        maintenance_warnings=sum(item["maintenance_priority"] not in ("normal", "observe") for item in maintenance.statuses()),
        community_reports=len([item for item in active_reports if item.get("source") == "public_user"]),
    )
    return summary


@app.get("/peer-consensus/{node_id}")
def peer_consensus(node_id: str):
    require_node(node_id)
    result = {"node_id": node_id, "parameters": {}}
    for parameter, key in (("temperature", "temperature_c"), ("humidity", "humidity_pct"), ("pressure", "pressure_hpa")):
        peers = engine.get_healthy_peers(node_id, parameter)
        values = [item["reading"][key] for item in peers]
        result["parameters"][parameter] = {
            "value": round(sum(values) / len(values), 3) if values else None,
            "source_nodes": [item["node_id"] for item in peers],
            "provenance": "peer_station_mean" if len(peers) >= 2 else "single_peer_estimate" if peers else None,
        }
    return result


@app.post("/vision/observations", status_code=201)
async def vision_observation(body: VisionObservationIn):
    return await vision.observe(body.model_dump())


@app.get("/vision/status")
def vision_status():
    return vision.status()


@app.get("/vision/events")
def vision_events(node_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
    if node_id:
        require_node(node_id)
    return vision.events(node_id, limit)


@app.get("/multi-source-context/{node_id}")
def multi_source_context(node_id: str):
    require_node(node_id)
    return vision.context(node_id)


@app.get("/sensor-specs")
def get_sensor_specs():
    return sensor_specs()


@app.get("/sensor-inventory")
def sensor_inventory():
    return maintenance.inventory()


@app.get("/sensor-inventory/{node_id}")
def node_sensor_inventory(node_id: str):
    require_node(node_id)
    return maintenance.inventory(node_id)


@app.get("/maintenance")
def maintenance_status():
    return maintenance.statuses()


@app.get("/maintenance/{node_id}")
def node_maintenance_status(node_id: str):
    require_node(node_id)
    return maintenance.node_status(node_id)


@app.post("/field-reports", status_code=201)
async def create_field_report(body: FieldReportCreate):
    return await field_reports.create(body.model_dump())


@app.get("/field-reports/active")
async def active_field_reports():
    return await field_reports.active()


@app.get("/field-reports/nearby/{node_id}")
async def nearby_field_reports(node_id: str, radius_km: float | None = Query(default=None, gt=0, le=100)):
    require_node(node_id)
    return await field_reports.nearby(node_id, radius_km)


@app.get("/field-reports")
async def list_field_reports(
    status: str | None = None,
    category: str | None = None,
    station_id: str | None = None,
    severity: str | None = None,
    since: datetime | None = None,
):
    if status and status not in ("active", "resolved", "expired"):
        raise HTTPException(422, "Unsupported field report status")
    if category and category not in FIELD_REPORT_CATEGORIES:
        raise HTTPException(422, "Unsupported field report category")
    if severity and severity not in FIELD_REPORT_SEVERITIES:
        raise HTTPException(422, "Unsupported field report severity")
    if station_id:
        require_node(station_id)
    return await field_reports.list(status, category, station_id, severity, since.isoformat() if since else None)


@app.get("/field-reports/{report_id}")
async def get_field_report(report_id: str):
    report = await field_reports.get(report_id)
    if report is None:
        raise HTTPException(404, "Unknown field report")
    return report


@app.patch("/field-reports/{report_id}")
async def update_field_report(report_id: str, body: FieldReportUpdate):
    report = await field_reports.update(report_id, body.model_dump(exclude_none=True))
    if report is None:
        raise HTTPException(404, "Unknown field report")
    return report


@app.post("/field-reports/{report_id}/resolve")
async def resolve_field_report(report_id: str, body: FieldReportResolve | None = None):
    report = await field_reports.resolve(report_id, body.notes if body else None)
    if report is None:
        raise HTTPException(404, "Unknown field report")
    return report


@app.get("/monitoring-context")
async def monitoring_contexts():
    await field_reports.refresh_expiry()
    return field_reports.contexts()


@app.get("/monitoring-context/{node_id}")
async def monitoring_context(node_id: str):
    require_node(node_id)
    await field_reports.refresh_expiry()
    return field_reports.context(node_id)


@app.post("/public/reports", status_code=201)
async def create_public_report(body: PublicReportCreate):
    values = body.model_dump()
    station_scope = values.pop("station_scope")
    station_id = station_scope[0] if len(station_scope) == 1 else None
    payload = {
        **values,
        "reporter_type": "public_user",
        "reporter_name": None,
        "station_id": station_id,
        "cluster_id": None if station_id else "prototype_cluster_01",
        "source": "public_user",
        "until_resolved": body.category in ("sensor_damage", "sensor_obstruction", "power_issue", "communication_issue"),
        "notes": None,
    }
    return field_reports.public_view(await field_reports.create(payload))


@app.get("/public/reports")
async def list_public_reports(status: str | None = None):
    if status and status not in ("active", "resolved", "expired"):
        raise HTTPException(422, "Unsupported report status")
    return await field_reports.public_reports(status)


@app.get("/public/reports/active")
async def active_public_reports():
    return await field_reports.public_reports("active")


@app.get("/public/reports/{report_id}")
async def get_public_report(report_id: str):
    report = await field_reports.get(report_id)
    if report is None or report.get("source") != "public_user":
        raise HTTPException(404, "Unknown public report")
    return field_reports.public_view(report)


@app.get("/admin/public-reports")
async def admin_public_reports(admin=Depends(require_admin)):
    return await field_reports.public_reports()


async def moderate_public_report(report_id, action, body, admin):
    try:
        report = await field_reports.moderate(report_id, action, admin["name"], body.notes if body else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if report is None:
        raise HTTPException(404, "Unknown public report")
    return report


@app.post("/admin/public-reports/{report_id}/verify")
async def verify_public_report(report_id: str, body: AdminReportAction | None = None, admin=Depends(require_admin)):
    return await moderate_public_report(report_id, "verify", body, admin)


@app.post("/admin/public-reports/{report_id}/monitor")
async def monitor_public_report(report_id: str, body: AdminReportAction | None = None, admin=Depends(require_admin)):
    return await moderate_public_report(report_id, "monitor", body, admin)


@app.post("/admin/public-reports/{report_id}/reject")
async def reject_public_report(report_id: str, body: AdminReportAction | None = None, admin=Depends(require_admin)):
    return await moderate_public_report(report_id, "reject", body, admin)


@app.post("/admin/public-reports/{report_id}/resolve")
async def resolve_public_report_admin(report_id: str, body: AdminReportAction | None = None, admin=Depends(require_admin)):
    return await moderate_public_report(report_id, "resolve", body, admin)


@app.post("/ingest")
async def ingest(body: ReadingIn):
    require_node(body.node_id)
    return await process_reading(body)


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()
    engine.clients.append(websocket)
    logger.debug("SkyGuard WebSocket connected (%s clients)", len(engine.clients))
    try:
        await websocket.send_json({
            "type": "system_status",
            "data": {
                "status": "ok",
                "detector_mode": ml_service.combined_mode,
                "ml_model": ml_service.status(),
                "mqtt": mqtt_service.status(),
                "communication_states": dict(engine.communication_state),
            },
        })
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=75.0)
            except asyncio.TimeoutError:
                # Frontend sends a heartbeat every 30 seconds.  A connection
                # that is silent for 75 seconds is stale; remove it so it can
                # never block future telemetry broadcasts.
                await websocket.close(code=1001, reason="heartbeat timeout")
                break

            try:
                frame = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                frame = {}
            if isinstance(frame, dict) and frame.get("type") == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "data": {"ts": frame.get("ts")},
                })
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if websocket in engine.clients:
            engine.clients.remove(websocket)
        logger.debug("SkyGuard WebSocket disconnected (%s clients)", len(engine.clients))
