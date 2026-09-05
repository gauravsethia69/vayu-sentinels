SENSOR_SPECS = {
    "DS18B20": {
        "manufacturer": "Analog Devices (originally Maxim Integrated)",
        "model": "DS18B20",
        "measurement_temperature_min_c": -55,
        "measurement_temperature_max_c": 125,
        "accuracy_specification": "±0.5°C from -10°C to +85°C",
        "operating_humidity_limits": None,
        "pressure_range_hpa": None,
        "expected_service_life": None,
        "datasheet_reference": "https://www.analog.com/media/en/technical-documentation/data-sheets/ds18b20.pdf",
        "notes": "Manufacturer lifetime not specified.",
    },
    "DHT22": {
        "manufacturer": "Aosong (Guangzhou) Electronics",
        "model": "DHT22 / AM2302",
        "measurement_temperature_min_c": -40,
        "measurement_temperature_max_c": 80,
        "operating_humidity_limits": [0, 99.9],
        "pressure_range_hpa": None,
        "accuracy_specification": "Temperature ±0.5°C typical; humidity ±2% RH at 25°C",
        "expected_service_life": None,
        "datasheet_reference": "https://digi-electronics.oss-us-west-1.aliyuncs.com/pdf/33301/AM2302-datasheet.pdf",
        "notes": "Manufacturer lifetime not specified.",
    },
    "BMP280": {
        "manufacturer": "Bosch Sensortec",
        "model": "BMP280",
        "measurement_temperature_min_c": -40,
        "measurement_temperature_max_c": 85,
        "operating_humidity_limits": None,
        "pressure_range_hpa": [300, 1100],
        "accuracy_specification": "Absolute pressure accuracy approximately ±1 hPa at 950–1050 hPa and 0–40°C",
        "expected_service_life": None,
        "datasheet_reference": "https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf",
        "notes": "Manufacturer lifetime not specified; temperature is primarily compensation data.",
    },
}


def sensor_specs():
    return [dict(item) for item in SENSOR_SPECS.values()]
