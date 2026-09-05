import json
import sqlite3

from .config import DATABASE_PATH


def connect():
    db = sqlite3.connect(DATABASE_PATH, timeout=5, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 5000")
    return db


def init_db():
    with connect() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript('''
        CREATE TABLE IF NOT EXISTS sensor_readings (id INTEGER PRIMARY KEY, timestamp TEXT, node_id TEXT, temperature_c REAL, pressure_hpa REAL, humidity_pct REAL, source TEXT, quality TEXT, raw_json TEXT, trusted_json TEXT);
        CREATE TABLE IF NOT EXISTS anomaly_events (id INTEGER PRIMARY KEY, timestamp TEXT, node_id TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS sensor_health_history (id INTEGER PRIMARY KEY, timestamp TEXT, node_id TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS field_reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reporter_type TEXT NOT NULL,
            reporter_name TEXT,
            station_id TEXT,
            cluster_id TEXT,
            latitude REAL,
            longitude REAL,
            location_label TEXT,
            category TEXT NOT NULL,
            observation TEXT NOT NULL,
            severity TEXT NOT NULL,
            reporter_confidence TEXT NOT NULL,
            direction TEXT,
            radius_km REAL NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT,
            source TEXT NOT NULL,
            verification_state TEXT NOT NULL,
            corroboration_confidence INTEGER NOT NULL DEFAULT 0,
            verified_by_nodes TEXT NOT NULL DEFAULT '[]',
            evidence TEXT NOT NULL DEFAULT '[]',
            contradicting_evidence TEXT NOT NULL DEFAULT '[]',
            message TEXT NOT NULL DEFAULT '',
            notes TEXT,
            resolved_at TEXT,
            moderation_state TEXT
        );
        CREATE TABLE IF NOT EXISTS public_report_actions (
            id INTEGER PRIMARY KEY,
            report_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            admin_name TEXT NOT NULL,
            action TEXT NOT NULL,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS vision_events (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            node_id TEXT NOT NULL,
            source TEXT NOT NULL,
            vision_mode TEXT NOT NULL,
            detection_type TEXT NOT NULL,
            confidence REAL NOT NULL,
            severity TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sensor_inventory (
            sensor_id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL,
            model TEXT NOT NULL,
            installed_at TEXT NOT NULL,
            last_inspection_at TEXT,
            last_calibration_at TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS sensor_exposure_events (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            node_id TEXT NOT NULL,
            sensor_id TEXT,
            exposure_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_readings_node_timestamp ON sensor_readings(node_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_node_timestamp ON anomaly_events(node_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_health_node_timestamp ON sensor_health_history(node_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_field_reports_status_created ON field_reports(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_field_reports_category ON field_reports(category);
        ''')
        columns = {row[1] for row in db.execute("PRAGMA table_info(sensor_readings)")}
        additions = {
            "received_at": "TEXT",
            "sensor_timestamp": "TEXT",
            "raw_sensors_json": "TEXT",
            "device_json": "TEXT",
            "simulation_json": "TEXT",
            "communication_state": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE sensor_readings ADD COLUMN {name} {column_type}")
        field_columns = {row[1] for row in db.execute("PRAGMA table_info(field_reports)")}
        if "moderation_state" not in field_columns:
            db.execute("ALTER TABLE field_reports ADD COLUMN moderation_state TEXT")


def save_reading(raw, trusted):
    with connect() as db:
        db.execute(
            """INSERT INTO sensor_readings(
                timestamp,node_id,temperature_c,pressure_hpa,humidity_pct,source,quality,
                raw_json,trusted_json,received_at,sensor_timestamp,raw_sensors_json,
                device_json,simulation_json,communication_state
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                raw["timestamp"], raw["node_id"], raw["temperature_c"], raw["pressure_hpa"],
                raw["humidity_pct"], raw["source"], raw["quality"], json.dumps(raw),
                json.dumps(trusted), raw.get("received_at"), raw.get("sensor_timestamp"),
                json.dumps(raw.get("raw_sensors")), json.dumps(raw.get("device")),
                json.dumps(raw.get("simulation")), raw.get("communication_state"),
            ),
        )


def _parse_json_object(value):
    """Return a stored JSON object, or None for missing/corrupt legacy rows."""
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _bounded_limit(limit):
    return max(1, min(int(limit), 500))


def load_latest_reading(node_id):
    """Load the newest persisted raw reading for one station."""
    with connect() as db:
        rows = db.execute(
            "SELECT raw_json FROM sensor_readings WHERE node_id=? ORDER BY id DESC",
            (node_id,),
        )
        for row in rows:
            reading = _parse_json_object(row["raw_json"])
            if reading is not None:
                return reading
    return None


def load_reading_history(node_id, limit=100):
    """Load persisted raw readings in oldest-to-newest chart order."""
    with connect() as db:
        rows = db.execute(
            "SELECT raw_json FROM sensor_readings WHERE node_id=? ORDER BY id DESC LIMIT ?",
            (node_id, _bounded_limit(limit)),
        ).fetchall()
    readings = [_parse_json_object(row["raw_json"]) for row in reversed(rows)]
    return [reading for reading in readings if reading is not None]


def load_latest_trusted_reading(node_id):
    """Load the newest persisted trusted/corrected reading for one station."""
    with connect() as db:
        rows = db.execute(
            "SELECT trusted_json FROM sensor_readings WHERE node_id=? ORDER BY id DESC",
            (node_id,),
        )
        for row in rows:
            reading = _parse_json_object(row["trusted_json"])
            if reading is not None:
                return reading
    return None


def load_trusted_history(node_id, limit=100):
    """Load persisted trusted readings in oldest-to-newest chart order."""
    with connect() as db:
        rows = db.execute(
            "SELECT trusted_json FROM sensor_readings WHERE node_id=? ORDER BY id DESC LIMIT ?",
            (node_id, _bounded_limit(limit)),
        ).fetchall()
    readings = [_parse_json_object(row["trusted_json"]) for row in reversed(rows)]
    return [reading for reading in readings if reading is not None]


def load_reading_records(node_id=None, limit=100):
    """Load raw/trusted pairs without mixing persistent and in-memory rows."""
    bounded_limit = _bounded_limit(limit)
    with connect() as db:
        if node_id is None:
            rows = db.execute(
                "SELECT raw_json, trusted_json FROM sensor_readings ORDER BY id DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT raw_json, trusted_json FROM sensor_readings WHERE node_id=? ORDER BY id DESC LIMIT ?",
                (node_id, bounded_limit),
            ).fetchall()
    records = []
    for row in reversed(rows):
        raw = _parse_json_object(row["raw_json"])
        trusted = _parse_json_object(row["trusted_json"])
        if raw is not None and trusted is not None:
            records.append({"raw": raw, "trusted": trusted})
    return records


def save_event(event):
    with connect() as db: db.execute("INSERT INTO anomaly_events(timestamp,node_id,payload) VALUES(?,?,?)", (event['timestamp'],event['node_id'],json.dumps(event)))


def save_health(health):
    with connect() as db: db.execute("INSERT INTO sensor_health_history(timestamp,node_id,payload) VALUES(?,?,?)", (health['timestamp'],health['node_id'],json.dumps(health)))


FIELD_REPORT_JSON_COLUMNS = {
    "verified_by_nodes",
    "evidence",
    "contradicting_evidence",
}


def _field_report_from_row(row):
    if row is None:
        return None
    report = dict(row)
    for name in FIELD_REPORT_JSON_COLUMNS:
        report[name] = json.loads(report.get(name) or "[]")
    return report


def save_field_report(report):
    columns = (
        "id", "created_at", "updated_at", "reporter_type", "reporter_name",
        "station_id", "cluster_id", "latitude", "longitude", "location_label",
        "category", "observation", "severity", "reporter_confidence", "direction",
        "radius_km", "status", "expires_at", "source", "verification_state",
        "corroboration_confidence", "verified_by_nodes", "evidence",
        "contradicting_evidence", "message", "notes", "resolved_at", "moderation_state",
    )
    values = [json.dumps(report.get(name, [])) if name in FIELD_REPORT_JSON_COLUMNS else report.get(name) for name in columns]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{name}=excluded.{name}" for name in columns if name not in ("id", "created_at"))
    with connect() as db:
        db.execute(
            f"INSERT INTO field_reports({','.join(columns)}) VALUES({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            values,
        )


def load_field_report(report_id):
    with connect() as db:
        return _field_report_from_row(db.execute("SELECT * FROM field_reports WHERE id=?", (report_id,)).fetchone())


def load_field_reports():
    with connect() as db:
        return [_field_report_from_row(row) for row in db.execute("SELECT * FROM field_reports ORDER BY created_at DESC")]


def save_public_report_action(report_id, created_at, admin_name, action, notes=None):
    with connect() as db:
        db.execute(
            "INSERT INTO public_report_actions(report_id,created_at,admin_name,action,notes) VALUES(?,?,?,?,?)",
            (report_id, created_at, admin_name, action, notes),
        )


def save_vision_event(event):
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO vision_events(created_at,node_id,source,vision_mode,detection_type,confidence,severity,payload) VALUES(?,?,?,?,?,?,?,?)",
            (event["timestamp"], event["node_id"], event["source"], event["vision_mode"], event["type"], event["confidence"], event["severity"], json.dumps(event)),
        )
        return cursor.lastrowid


def load_vision_events(limit=100, node_id=None):
    with connect() as db:
        if node_id:
            rows = db.execute("SELECT payload FROM vision_events WHERE node_id=? ORDER BY id DESC LIMIT ?", (node_id, limit))
        else:
            rows = db.execute("SELECT payload FROM vision_events ORDER BY id DESC LIMIT ?", (limit,))
        return [json.loads(row[0]) for row in rows]


def ensure_sensor_inventory(records):
    with connect() as db:
        for record in records:
            db.execute(
                "INSERT OR IGNORE INTO sensor_inventory(sensor_id,node_id,model,installed_at,last_inspection_at,last_calibration_at,notes) VALUES(?,?,?,?,?,?,?)",
                (record["sensor_id"], record["node_id"], record["model"], record["installed_at"], record.get("last_inspection_at"), record.get("last_calibration_at"), record.get("notes")),
            )


def load_sensor_inventory(node_id=None):
    with connect() as db:
        query = "SELECT * FROM sensor_inventory" + (" WHERE node_id=?" if node_id else "") + " ORDER BY node_id, sensor_id"
        return [dict(row) for row in db.execute(query, (node_id,) if node_id else ())]


def save_exposure_event(event):
    with connect() as db:
        cursor = db.execute(
            "INSERT INTO sensor_exposure_events(created_at,node_id,sensor_id,exposure_type,severity,source_type,source_id,payload) VALUES(?,?,?,?,?,?,?,?)",
            (event["timestamp"], event["node_id"], event.get("sensor_id"), event["type"], event["severity"], event["source_type"], event.get("source_id"), json.dumps(event)),
        )
        return cursor.lastrowid


def load_exposure_events(node_id=None, limit=200):
    with connect() as db:
        if node_id:
            rows = db.execute("SELECT payload FROM sensor_exposure_events WHERE node_id=? ORDER BY id DESC LIMIT ?", (node_id, limit))
        else:
            rows = db.execute("SELECT payload FROM sensor_exposure_events ORDER BY id DESC LIMIT ?", (limit,))
        return [json.loads(row[0]) for row in rows]
