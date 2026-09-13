from app.live_inference import SkyGuardLiveClassifier

MODEL_PATH = "app/models/skyguard_pytorch_multiclass_v2.pt"

detector = SkyGuardLiveClassifier(MODEL_PATH)

samples = [
    (26.50, 27.40, 82.5, 27.54, 945.66),
    (26.50, 27.40, 82.4, 27.55, 945.66),
    (26.56, 27.40, 82.4, 27.55, 945.67),
    (26.56, 27.40, 82.3, 27.56, 945.67),
    (26.56, 27.50, 82.3, 27.56, 945.68),
    (26.62, 27.50, 82.2, 27.57, 945.68),
    (26.62, 27.50, 82.2, 27.57, 945.68),
    (26.62, 27.50, 82.1, 27.58, 945.69),
    (26.69, 27.50, 82.1, 27.58, 945.69),
    (26.69, 27.50, 82.0, 27.59, 945.70),
    (26.69, 27.60, 82.0, 27.59, 945.70),
    (26.75, 27.60, 81.9, 27.60, 945.71),
]

for i, s in enumerate(samples, 1):
    sample = {
        "ds18b20_temperature_c": s[0],
        "dht22_temperature_c": s[1],
        "dht22_humidity_pct": s[2],
        "bmp280_temperature_c": s[3],
        "bmp280_pressure_hpa": s[4],
    }

    result = detector.update(sample)
    print(i, result)