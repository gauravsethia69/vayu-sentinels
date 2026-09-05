from __future__ import annotations

from statistics import median
from typing import Any


def normalize_reading(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert legacy or physical multi-sensor input into one detector contract."""
    data = dict(payload)
    data.setdefault("source", "esp32")
    sensors = data.get("sensors") or {}
    if not sensors:
        temperature = float(data["temperature_c"])
        return {
            **data,
            "primary_temperature_c": temperature,
            "reference_temperature_c": temperature,
            "temperature_consensus_c": temperature,
            "raw_sensors": {
                "legacy_temperature_c": temperature,
                "legacy_humidity_pct": float(data["humidity_pct"]),
                "legacy_pressure_hpa": float(data["pressure_hpa"]),
            },
            "normalization_mode": "legacy",
        }

    barometric_temperature = sensors.get("bmp280_temperature_c", sensors.get("bmp180_temperature_c"))
    barometric_pressure = sensors.get("bmp280_pressure_hpa", sensors.get("bmp180_pressure_hpa"))
    temperatures = [
        sensors.get("ds18b20_temperature_c"),
        sensors.get("dht22_temperature_c"),
        barometric_temperature,
    ]
    available_temperatures = [float(value) for value in temperatures if value is not None]
    references = [
        float(value)
        for value in (sensors.get("dht22_temperature_c"), barometric_temperature)
        if value is not None
    ]
    primary = sensors.get("ds18b20_temperature_c")
    consensus = float(median(available_temperatures))
    reference = float(median(references)) if references else consensus
    canonical_temperature = float(primary) if primary is not None else consensus
    humidity = sensors.get("dht22_humidity_pct")
    pressure = barometric_pressure
    barometric_prefix = "bmp280" if any(name.startswith("bmp280_") for name in sensors) else "bmp180"
    known_channels = (
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        f"{barometric_prefix}_temperature_c",
        f"{barometric_prefix}_pressure_hpa",
    )
    missing_sensors = [name for name in known_channels if sensors.get(name) is None]

    return {
        **data,
        "temperature_c": canonical_temperature,
        "pressure_hpa": float(pressure) if pressure is not None else None,
        "humidity_pct": float(humidity) if humidity is not None else None,
        "primary_temperature_c": float(primary) if primary is not None else None,
        "reference_temperature_c": reference,
        "temperature_consensus_c": consensus,
        "raw_sensors": {
            **dict(sensors),
            "barometric_temperature_c": float(barometric_temperature) if barometric_temperature is not None else None,
            "barometric_pressure_hpa": float(barometric_pressure) if barometric_pressure is not None else None,
        },
        "missing_sensors": missing_sensors,
        "normalization_mode": "multi_sensor",
        "barometric_temperature_c": float(barometric_temperature) if barometric_temperature is not None else None,
        "sensor_models": {
            "temperature_primary": "DS18B20",
            "humidity_reference": "DHT22",
            "barometric": "BMP280" if sensors.get("bmp280_pressure_hpa") is not None else "BMP180_legacy",
        },
    }
