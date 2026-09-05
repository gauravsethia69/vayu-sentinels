import sqlite3

c = sqlite3.connect("skyguard.db")

rows = c.execute(
    """
    SELECT id, timestamp, node_id, temperature_c,
           pressure_hpa, humidity_pct, source,
           quality, communication_state
    FROM sensor_readings
    WHERE node_id = ?
    ORDER BY id DESC
    LIMIT 5
    """,
    ("AWS_003",)
).fetchall()

for row in rows:
    print(row)

c.close()
