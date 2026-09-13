import csv
import time
from pathlib import Path

from app.live_inference import SkyGuardLiveClassifier


BASE_DIR = Path(__file__).parent

MODEL_PATH = (
    BASE_DIR
    / "app"
    / "models"
    / "skyguard_pytorch_multiclass_v5.pt"
)

AWS2_CSV = Path(r"C:\SIH\SKYGUARD\SkyGuard_FINAL_LIVE_HARDWARE_RELEASE\backend\AWS_2.csv")
AWS3_CSV = Path(r"C:\SIH\SKYGUARD\SkyGuard_FINAL_LIVE_HARDWARE_RELEASE\backend\AWS_3.csv")

# Replay with realistic spacing so time-based freeze logic works.
REPLAY_DELAY_SECONDS = 0.05

# We simulate roughly 2 seconds between original sensor packets.
SIMULATED_PACKET_SECONDS = 2.0


def to_float(value):
    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    try:
        return float(value)
    except ValueError:
        return None


def find_value(row, possible_names):
    for name in possible_names:
        if name in row:
            return row[name]

    return None


def validate_station(name, csv_path):
    print()
    print("=" * 70)
    print(f"VALIDATING {name}")
    print("=" * 70)

    classifier = SkyGuardLiveClassifier(
        str(MODEL_PATH)
    )

    total = 0
    ready_rows = 0
    raw_normal = 0
    raw_freeze = 0
    raw_drift = 0
    raw_spike = 0
    raw_other = 0

    confirmed_faults = []

    # Important:
    # current live classifier uses time.monotonic().
    # For offline replay, we temporarily advance time artificially.
    original_monotonic = time.monotonic

    simulated_time = [1000.0]

    def fake_monotonic():
        return simulated_time[0]

    time.monotonic = fake_monotonic

    try:
        with open(
            csv_path,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(file)

            for row_number, row in enumerate(
                reader,
                start=2
            ):
                total += 1

                ds = to_float(
                    find_value(
                        row,
                        [
                            "ds18b20_temperature_c",
                            "ds18b20",
                            "ds18b20_temp",
                            "DS18B20",
                        ]
                    )
                )

                dht_temp = to_float(
                    find_value(
                        row,
                        [
                            "dht22_temperature_c",
                            "dht22_temp",
                            "temperature_dht22",
                            "DHT22_T",
                        ]
                    )
                )

                humidity = to_float(
                    find_value(
                        row,
                        [
                            "dht22_humidity_pct",
                            "humidity",
                            "dht22_humidity",
                            "DHT22_H",
                        ]
                    )
                )

                bmp_temp = to_float(
                    find_value(
                        row,
                        [
                            "bmp280_temperature_c",
                            "bmp280_temp",
                            "bmp180_temperature_c",
                            "bmp180_temp",
                            "BMP280_T",
                            "BMP180_T",
                        ]
                    )
                )

                pressure = to_float(
                    find_value(
                        row,
                        [
                            "bmp280_pressure_hpa",
                            "bmp180_pressure_hpa",
                            "pressure_hpa",
                            "pressure",
                        ]
                    )
                )

                # Exclude obviously corrupted values from
                # normal-only transfer validation.
                if ds is not None and ds <= -100:
                    print(
                        f"Skipping corrupted normal row "
                        f"{row_number}: DS={ds}"
                    )

                    simulated_time[0] += (
                        SIMULATED_PACKET_SECONDS
                    )

                    continue

                reading = {
                    "ds18b20_temperature_c": ds,
                    "dht22_temperature_c": dht_temp,
                    "dht22_humidity_pct": humidity,
                    "bmp280_temperature_c": bmp_temp,
                    "bmp280_pressure_hpa": pressure,
                }

                result = classifier.update(
                    reading
                )

                if result.get("ready"):
                    ready_rows += 1

                    prediction = result.get(
                        "prediction"
                    )

                    if prediction == "normal":
                        raw_normal += 1
                    elif prediction == "freeze":
                        raw_freeze += 1
                    elif prediction == "drift":
                        raw_drift += 1
                    elif prediction == "spike":
                        raw_spike += 1
                    else:
                        raw_other += 1

                    confirmed_fault = result.get(
                        "confirmed_fault"
                    )

                    if confirmed_fault is not None:
                        confirmed_faults.append(
                            {
                                "row": row_number,
                                "fault": confirmed_fault,
                                "prediction": prediction,
                                "confidence": result.get(
                                    "confidence"
                                ),
                                "freeze_gate": result.get(
                                    "freeze_gate"
                                ),
                            }
                        )

                simulated_time[0] += (
                    SIMULATED_PACKET_SECONDS
                )

                time.sleep(
                    REPLAY_DELAY_SECONDS
                )

    finally:
        time.monotonic = original_monotonic

    print()
    print(f"{name} RESULTS")
    print("-" * 40)

    print(
        f"Total usable rows: {total}"
    )

    print(
        f"Model-ready rows: {ready_rows}"
    )

    print(
        f"Raw normal predictions: {raw_normal}"
    )

    print(
        f"Raw freeze predictions: {raw_freeze}"
    )

    print(
        f"Raw drift predictions: {raw_drift}"
    )

    print(
        f"Raw spike predictions: {raw_spike}"
    )

    print(
        f"Other predictions: {raw_other}"
    )

    print(
        f"CONFIRMED FALSE FAULTS: "
        f"{len(confirmed_faults)}"
    )

    if confirmed_faults:
        print()
        print("False confirmed fault examples:")

        for item in confirmed_faults[:10]:
            print(item)

    else:
        print()
        print(
            "PASS: no confirmed fault "
            "during normal historical data."
        )

    return len(
        confirmed_faults
    )


aws2_false_faults = validate_station(
    "AWS_002",
    AWS2_CSV
)

aws3_false_faults = validate_station(
    "AWS_003",
    AWS3_CSV
)

print()
print("=" * 70)
print("FINAL TRANSFER CHECK")
print("=" * 70)

print(
    f"AWS_002 confirmed false faults: "
    f"{aws2_false_faults}"
)

print(
    f"AWS_003 confirmed false faults: "
    f"{aws3_false_faults}"
)

if (
    aws2_false_faults == 0
    and
    aws3_false_faults == 0
):
    print()
    print(
        "PASS: shared PyTorch V3 appears safe "
        "for observational use on AWS_002/AWS_003."
    )

else:
    print()
    print(
        "CAUTION: keep PyTorch disabled on "
        "AWS_002/AWS_003 for now."
    )