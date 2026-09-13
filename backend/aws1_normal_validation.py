import csv
import time
from datetime import datetime

import requests


URL = "http://127.0.0.1:8000/nodes/AWS_001/latest"
OUTPUT = "aws1_normal_validation.csv"

DURATION_MINUTES = 10
INTERVAL_SECONDS = 2

samples = int((DURATION_MINUTES * 60) / INTERVAL_SECONDS)


with open(OUTPUT, "w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)

    writer.writerow([
        "time",
        "ds18b20",
        "dht22_temp",
        "bmp280_temp",
        "rf_prediction",
        "rf_confidence",
        "pytorch_prediction",
        "pytorch_confidence",
        "pytorch_confirmed",
        "confirmed_fault",
        "agreement",
    ])

    print("AWS_001 NORMAL VALIDATION")
    print("Do NOT inject any faults.")
    print(f"Collecting {DURATION_MINUTES} minutes...\n")

    for index in range(samples):
        try:
            response = requests.get(URL, timeout=5)
            data = response.json()

            sensors = data.get("sensors", {})
            summary = data.get("ai_summary", {})

            rf = summary.get("rf", {})
            pytorch = summary.get("pytorch", {})

            row = [
                datetime.now().isoformat(timespec="seconds"),
                sensors.get("ds18b20_temperature_c"),
                sensors.get("dht22_temperature_c"),
                sensors.get("bmp280_temperature_c"),
                rf.get("normalized_prediction"),
                rf.get("confidence"),
                pytorch.get("normalized_prediction"),
                pytorch.get("confidence"),
                pytorch.get("confirmed"),
                pytytorch_fault
                if (pytytorch_fault := pytorch.get("confirmed_fault"))
                else None,
                summary.get("agreement"),
            ]

            writer.writerow(row)
            file.flush()

            print(
                f"{index + 1:03}/{samples} | "
                f"DS={row[1]} | "
                f"RF={row[4]} {row[5]} | "
                f"PT={row[6]} {row[7]} | "
                f"confirmed={row[8]} | "
                f"fault={row[9]}"
            )

        except Exception as exc:
            print("ERROR:", exc)

        time.sleep(INTERVAL_SECONDS)


print("\nFINISHED")
print("Saved:", OUTPUT)