import csv
import time
from datetime import datetime

import requests


URL = "http://127.0.0.1:8000/nodes/AWS_001/latest"
OUTPUT = "aws1_event_validation.csv"

POLL_SECONDS = 2

EVENT_PLAN = [
    ("normal", 30),
    ("spike", 20),
    ("normal", 30),
    ("freeze", 100),
    ("normal", 30),
    ("drift", 30),
    ("normal", 30),
    ("data_loss", 20),
    ("normal", 30),
    ("corruption", 20),
    ("normal", 30),
]


def get_latest():
    response = requests.get(URL, timeout=5)
    response.raise_for_status()
    return response.json()


def safe_get(mapping, key):
    if not isinstance(mapping, dict):
        return None
    return mapping.get(key)


with open(
    OUTPUT,
    "w",
    newline="",
    encoding="utf-8"
) as file:

    writer = csv.writer(file)

    writer.writerow([
        "time",
        "expected_event",
        "timestamp",
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
        "freeze_gate_passed",
    ])

    print("=" * 60)
    print("SKYGUARD AWS_001 EVENT VALIDATION")
    print("=" * 60)

    print()
    print("IMPORTANT:")
    print("- Only inject the fault when instructed.")
    print("- Press each hardware button ONCE.")
    print("- Do not inject extra faults.")
    print("- Keep backend + MQTT running.")
    print()

    input("Press ENTER to begin...")

    for expected_event, duration in EVENT_PLAN:

        print()
        print("=" * 60)
        print(f"NEXT EVENT: {expected_event.upper()}")
        print(f"Recording duration: {duration} seconds")
        print("=" * 60)

        if expected_event == "normal":

            print("Do NOT press any fault button.")

        else:

            print(
                f"Press the {expected_event.upper()} "
                f"fault button NOW."
            )

            input(
                "Press ENTER here immediately "
                "after pressing the hardware button..."
            )

        start = time.monotonic()

        while (
            time.monotonic() - start
            <
            duration
        ):

            try:

                data = get_latest()

                sensors = data.get(
                    "sensors",
                    {}
                )

                summary = data.get(
                    "ai_summary",
                    {}
                )

                rf = summary.get(
                    "rf",
                    {}
                )

                pytorch = summary.get(
                    "pytorch",
                    {}
                )

                writer.writerow([
                    datetime.now().isoformat(
                        timespec="seconds"
                    ),

                    expected_event,

                    data.get(
                        "timestamp"
                    ),

                    sensors.get(
                        "ds18b20_temperature_c"
                    ),

                    sensors.get(
                        "dht22_temperature_c"
                    ),

                    sensors.get(
                        "bmp280_temperature_c"
                    ),

                    rf.get(
                        "normalized_prediction"
                    ),

                    rf.get(
                        "confidence"
                    ),

                    pytorch.get(
                        "normalized_prediction"
                    ),

                    pytorch.get(
                        "confidence"
                    ),

                    pytorch.get(
                        "confirmed"
                    ),

                    pytorch.get(
                        "confirmed_fault"
                    ),

                    summary.get(
                        "agreement"
                    ),

                    pytorch.get(
                        "freeze_gate_passed"
                    ),
                ])

                file.flush()

                print(
                    f"{expected_event:12} | "
                    f"DS={sensors.get('ds18b20_temperature_c')} | "
                    f"RF={rf.get('normalized_prediction')} | "
                    f"PT={pytorch.get('normalized_prediction')} | "
                    f"CONFIRMED={pytorch.get('confirmed_fault')}"
                )

            except Exception as exc:

                print(
                    "ERROR:",
                    exc
                )

            time.sleep(
                POLL_SECONDS
            )

print()
print("=" * 60)
print("VALIDATION COMPLETE")
print("=" * 60)
print("Saved:")
print(OUTPUT)