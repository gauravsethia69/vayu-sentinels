from __future__ import annotations

import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from .config import (
    ML_CONFIDENCE_THRESHOLD,
    ML_ENABLED,
    FREEZE_ML_STRONG_CONFIDENCE,
    ML_MODEL_PATH,
    ML_MIN_SAMPLES,
)


MODEL_VERSION = "skyguard_final_hybrid_rf_v6"
DETECTOR_MODE = "hybrid_rf_v6"


class HybridRFService:
    """Optional Random-Forest layer fused with SkyGuard's existing safety detector.

    The trained artifact classifies normal / temperature spike / temperature drift /
    temperature freeze. Deterministic quality and communication checks remain in the
    backend because corruption, missing fields, and total packet loss should not depend
    on an ML prediction.

    Model loading is deliberately fail-open for non-freeze safety checks. If the
    artifact is unavailable, heuristic spike/drift/cross-sensor checks can continue,
    but temperature freeze is intentionally unavailable without the Random Forest.
    """

    def __init__(self):
        self.enabled = ML_ENABLED
        self.model_path = Path(ML_MODEL_PATH)
        self.confidence_threshold = ML_CONFIDENCE_THRESHOLD
        self.min_samples = ML_MIN_SAMPLES
        self.loaded = False
        self.error: str | None = None
        self.artifact: dict[str, Any] | None = None
        self.model = None
        self.features: list[str] = []
        self.predictions = 0
        self.predictions_by_label: Counter[str] = Counter()
        self.last_prediction_at: str | None = None
        self._np = None
        if self.enabled:
            self._load()

    def _load(self):
        try:
            import joblib
            import numpy as np

            artifact = joblib.load(self.model_path)
            model = artifact["environmental_model"]
            features = list(artifact["environmental_feature_names"])
            if not features:
                raise ValueError("ML artifact contains no feature names")
            self.artifact = artifact
            self.model = model
            self.features = features
            self._np = np
            self.loaded = True
            self.error = None
        except Exception as exc:  # backend must still start for demo fallback
            self.loaded = False
            self.error = f"{type(exc).__name__}: {exc}"

    @property
    def combined_mode(self) -> str:
        return "hybrid_rf_v6+heuristic_nonfreeze_safety" if self.loaded else "heuristic_nonfreeze_safety"

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "loaded": self.loaded,
            "model_version": MODEL_VERSION if self.loaded else None,
            "detector_mode": self.combined_mode,
            "model_path": str(self.model_path),
            "feature_count": len(self.features),
            "confidence_threshold": self.confidence_threshold,
            "min_samples": self.min_samples,
            "predictions": self.predictions,
            "predictions_by_label": dict(self.predictions_by_label),
            "last_prediction_at": self.last_prediction_at,
            "error": self.error,
            "fallback": "heuristic-nonfreeze-v2" if not self.loaded else None,
        }

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _extract_channels(reading: dict[str, Any]) -> dict[str, float | None]:
        raw = reading.get("raw_sensors") or reading.get("sensors") or {}
        baro_temp = raw.get("bmp280_temperature_c", raw.get("bmp180_temperature_c"))
        baro_pressure = raw.get("bmp280_pressure_hpa", raw.get("bmp180_pressure_hpa"))
        return {
            "ds18b20_temperature_c": raw.get("ds18b20_temperature_c"),
            "dht22_temperature_c": raw.get("dht22_temperature_c"),
            "dht22_humidity_pct": raw.get("dht22_humidity_pct", reading.get("humidity_pct")),
            "bmp280_temperature_c": baro_temp,
            "bmp280_pressure_hpa": baro_pressure if baro_pressure is not None else reading.get("pressure_hpa"),
        }

    @staticmethod
    def _is_corrupt(name: str, value: float | None) -> bool:
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        if name == "ds18b20_temperature_c":
            return value in (-127.0, 85.0) or not (-55.0 <= value <= 125.0)
        if name == "dht22_temperature_c":
            return not (-40.0 <= value <= 80.0)
        if name == "dht22_humidity_pct":
            return not (0.0 <= value <= 100.0)
        if name == "bmp280_temperature_c":
            return not (-40.0 <= value <= 85.0)
        if name == "bmp280_pressure_hpa":
            return not (300.0 <= value <= 1200.0)
        return False

    @staticmethod
    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @classmethod
    def _std(cls, values: list[float]) -> float:
        if len(values) <= 1:
            return 0.0
        avg = cls._mean(values)
        return math.sqrt(sum((value - avg) ** 2 for value in values) / len(values))

    @staticmethod
    def _abs_movement(samples: list[dict[str, Any]], key: str) -> float:
        values = [sample[key] for sample in samples]
        if len(values) < 2:
            return 0.0
        return sum(abs(current - previous) for previous, current in zip(values[:-1], values[1:]))

    @staticmethod
    def _change_count(samples: list[dict[str, Any]], key: str) -> int:
        values = [sample[key] for sample in samples]
        if len(values) < 2:
            return 0
        return sum(current != previous for previous, current in zip(values[:-1], values[1:]))

    @staticmethod
    def _slope_per_min(samples: list[dict[str, Any]], key: str) -> float:
        if len(samples) < 3:
            return 0.0
        t0 = samples[0]["_timestamp"]
        xs = [(sample["_timestamp"] - t0).total_seconds() / 60.0 for sample in samples]
        ys = [sample[key] for sample in samples]
        xm = sum(xs) / len(xs)
        ym = sum(ys) / len(ys)
        denom = sum((x - xm) ** 2 for x in xs)
        if denom <= 1e-12:
            return 0.0
        return sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / denom

    @classmethod
    def _window(cls, samples: list[dict[str, Any]], seconds: int) -> list[dict[str, Any]]:
        if not samples:
            return []
        current = samples[-1]["_timestamp"]
        return [
            sample
            for sample in samples
            if 0 <= (current - sample["_timestamp"]).total_seconds() <= seconds
        ]

    def _samples(self, reading: dict[str, Any], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        for item in [*history, reading]:
            channels = self._extract_channels(item)
            if any(value is None for value in channels.values()):
                continue
            if any(self._is_corrupt(name, float(value)) for name, value in channels.items()):
                continue
            timestamp = self._parse_time(item.get("sensor_timestamp") or item.get("timestamp"))
            if timestamp is None:
                continue
            sample = {name: float(value) for name, value in channels.items()}
            sample["_timestamp"] = timestamp
            samples.append(sample)
        samples.sort(key=lambda item: item["_timestamp"])
        return samples

    def _build_features(self, reading: dict[str, Any], history: list[dict[str, Any]]) -> tuple[dict[str, float] | None, dict[str, Any]]:
        if reading.get("normalization_mode") != "multi_sensor":
            return None, {"reason": "legacy_payload_not_supported_by_rf"}

        current_channels = self._extract_channels(reading)
        missing = [name for name, value in current_channels.items() if value is None]
        corrupt = [
            name
            for name, value in current_channels.items()
            if value is not None and self._is_corrupt(name, float(value))
        ]
        if corrupt:
            return None, {"quality_gate": "data_corruption", "sensors": corrupt}
        if missing:
            return None, {"quality_gate": "sensor_missing", "sensors": missing}

        samples = self._samples(reading, history)
        if len(samples) < self.min_samples:
            return None, {"reason": "ml_warmup", "samples": len(samples)}

        w30 = self._window(samples, 30)
        w60 = self._window(samples, 60)
        current = samples[-1]
        ds = current["ds18b20_temperature_c"]
        dht = current["dht22_temperature_c"]
        humidity = current["dht22_humidity_pct"]
        bmp = current["bmp280_temperature_c"]
        pressure = current["bmp280_pressure_hpa"]

        ds30 = [sample["ds18b20_temperature_c"] for sample in w30]
        ds60 = [sample["ds18b20_temperature_c"] for sample in w60]
        reference_movement_30 = (
            self._abs_movement(w30, "dht22_temperature_c")
            + self._abs_movement(w30, "bmp280_temperature_c")
        ) / 2.0
        reference_movement_60 = (
            self._abs_movement(w60, "dht22_temperature_c")
            + self._abs_movement(w60, "bmp280_temperature_c")
        ) / 2.0
        ds_movement_30 = self._abs_movement(w30, "ds18b20_temperature_c")
        ds_movement_60 = self._abs_movement(w60, "ds18b20_temperature_c")

        same_start = current["_timestamp"]
        for sample in reversed(samples[:-1]):
            if sample["ds18b20_temperature_c"] != ds:
                break
            same_start = sample["_timestamp"]
        same_duration = max(0.0, (current["_timestamp"] - same_start).total_seconds())

        features = {
            "ds18b20_temperature_c": ds,
            "dht22_temperature_c": dht,
            "dht22_humidity_pct": humidity,
            "bmp280_temperature_c": bmp,
            "bmp280_pressure_hpa": pressure,
            "ds_minus_dht_c": ds - dht,
            "ds_minus_bmp_c": ds - bmp,
            "dht_minus_bmp_c": dht - bmp,
            "temperature_spread_c": max(ds, dht, bmp) - min(ds, dht, bmp),
            "ds_mean_30s": self._mean(ds30),
            "ds_std_30s": self._std(ds30),
            "ds_range_30s": (max(ds30) - min(ds30)) if ds30 else 0.0,
            "ds_slope_c_per_min_30s": self._slope_per_min(w30, "ds18b20_temperature_c"),
            "ds_mean_60s": self._mean(ds60),
            "ds_std_60s": self._std(ds60),
            "ds_range_60s": (max(ds60) - min(ds60)) if ds60 else 0.0,
            "ds_slope_c_per_min_60s": self._slope_per_min(w60, "ds18b20_temperature_c"),
            "ds_abs_movement_30s": ds_movement_30,
            "ds_abs_movement_60s": ds_movement_60,
            "reference_abs_movement_30s": reference_movement_30,
            "reference_abs_movement_60s": reference_movement_60,
            "ds_to_reference_movement_ratio_30s": ds_movement_30 / (reference_movement_30 + 0.01),
            "ds_to_reference_movement_ratio_60s": ds_movement_60 / (reference_movement_60 + 0.01),
            "ds_change_count_30s": float(self._change_count(w30, "ds18b20_temperature_c")),
            "ds_change_count_60s": float(self._change_count(w60, "ds18b20_temperature_c")),
            "ds_unique_count_30s": float(len(set(ds30))),
            "ds_unique_count_60s": float(len(set(ds60))),
            "ds_same_value_duration_s": same_duration,
            "humidity_slope_pct_per_min_30s": self._slope_per_min(w30, "dht22_humidity_pct"),
            "pressure_slope_hpa_per_min_30s": self._slope_per_min(w30, "bmp280_pressure_hpa"),
        }
        missing_feature_names = [name for name in self.features if name not in features]
        if missing_feature_names:
            return None, {"reason": "feature_contract_mismatch", "missing_features": missing_feature_names}
        return features, {
            "samples": len(samples),
            "window_30s_points": len(w30),
            "window_60s_points": len(w60),
            "window_span_seconds": round((samples[-1]["_timestamp"] - samples[0]["_timestamp"]).total_seconds(), 3),
        }

    def assess(self, reading: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
        base = {
            "enabled": self.enabled,
            "loaded": self.loaded,
            "detector_mode": DETECTOR_MODE,
            "model_version": MODEL_VERSION,
            "prediction": None,
            "confidence": None,
            "probabilities": {},
            "ready": False,
            "source": "random_forest" if self.loaded else "heuristic_nonfreeze_fallback",
        }
        if not self.enabled or not self.loaded or self.model is None or self._np is None:
            base["reason"] = self.error or "ml_disabled"
            return base

        features, context = self._build_features(reading, history)
        base.update(context)
        if features is None:
            quality_gate = context.get("quality_gate")
            if quality_gate:
                base.update(prediction=quality_gate, confidence=1.0, ready=True, source="quality_gate")
            return base

        row = self._np.array([[float(features[name]) for name in self.features]], dtype=float)
        probabilities = self.model.predict_proba(row)[0]
        classes = self.model.classes_
        probability_map = {str(label): float(probability) for label, probability in zip(classes, probabilities)}
        prediction = str(classes[int(self._np.argmax(probabilities))])
        confidence = float(max(probabilities))

        # Training deliberately treated freeze as meaningful only after persistent
        # repetition and drift as a temporal pattern. Keep that discipline live.
        if prediction == "temperature_freeze" and features["ds_same_value_duration_s"] < 20.0:
            base.update(
                prediction="normal",
                confidence=probability_map.get("normal", 0.0),
                probabilities=probability_map,
                ready=True,
                suppressed_prediction="temperature_freeze",
                suppressed_reason="freeze requires >=20 s same-value persistence",
            )
            return base
        if prediction == "temperature_drift" and context.get("window_span_seconds", 0.0) < 10.0:
            base.update(
                prediction="normal",
                confidence=probability_map.get("normal", 0.0),
                probabilities=probability_map,
                ready=True,
                suppressed_prediction="temperature_drift",
                suppressed_reason="drift requires >=10 s temporal context",
            )
            return base

        # Genuine weather movement should be coherent across the three local
        # temperature channels. The RF was trained on isolated DS18B20 faults,
        # so suppress spike/drift classifications when DS18B20, DHT22 and BMP280
        # move together and remain tightly clustered. Heuristic peer-coherence
        # logic remains authoritative for genuine weather change.
        movement_ratio = features["ds_to_reference_movement_ratio_30s"]
        if (
            prediction in ("temperature_spike", "temperature_drift")
            and features["temperature_spread_c"] <= 1.2
            and 0.35 <= movement_ratio <= 2.8
            and features["reference_abs_movement_30s"] >= 0.05
        ):
            base.update(
                prediction="normal",
                confidence=probability_map.get("normal", 0.0),
                probabilities=probability_map,
                ready=True,
                suppressed_prediction=prediction,
                suppressed_reason="coherent multi-sensor temperature movement resembles genuine weather change",
            )
            return base

        self.predictions += 1
        self.predictions_by_label[prediction] += 1
        self.last_prediction_at = reading.get("timestamp")
        base.update(
            prediction=prediction,
            confidence=round(confidence, 6),
            probabilities={key: round(value, 6) for key, value in probability_map.items()},
            ready=True,
            feature_summary={
                "ds_std_30s": round(features["ds_std_30s"], 4),
                "ds_range_30s": round(features["ds_range_30s"], 4),
                "ds_slope_c_per_min_30s": round(features["ds_slope_c_per_min_30s"], 4),
                "ds_same_value_duration_s": round(features["ds_same_value_duration_s"], 3),
                "ds_to_reference_movement_ratio_30s": round(features["ds_to_reference_movement_ratio_30s"], 4),
                "reference_abs_movement_30s": round(features["reference_abs_movement_30s"], 4),
                "temperature_spread_c": round(features["temperature_spread_c"], 4),
            },
        )
        return base

    @staticmethod
    def _sensor_parameter(sensor_name: str) -> tuple[str | None, str | None]:
        if sensor_name == "ds18b20_temperature_c":
            return "temperature", "ds18b20"
        if sensor_name == "dht22_temperature_c":
            return "temperature", "dht22"
        if sensor_name == "dht22_humidity_pct":
            return "humidity", "dht22"
        if sensor_name == "bmp280_temperature_c":
            return "temperature", "bmp280"
        if sensor_name == "bmp280_pressure_hpa":
            return "pressure", "bmp280"
        return None, None

    def _quality_event(self, reading: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any] | None:
        prediction = assessment.get("prediction")
        sensors = assessment.get("sensors") or []
        if prediction not in ("sensor_missing", "data_corruption") or not sensors:
            return None

        sensor_name = sensors[0]
        parameter, suspected_sensor = self._sensor_parameter(sensor_name)
        raw = reading.get("raw_sensors") or {}
        observed = raw.get(sensor_name)
        corrected = None
        if parameter == "temperature":
            corrected = reading.get("temperature_consensus_c") or reading.get("reference_temperature_c")

        if prediction == "sensor_missing":
            anomaly_type = "data_loss"
            severity = "high"
            message = f"{sensor_name} channel is missing while the station continues reporting."
            action = "Inspect the missing sensor channel while continuing with redundant validated references."
        else:
            anomaly_type = "data_corruption"
            severity = "critical"
            message = f"Corrupted or implausible sensor channel: {sensor_name}"
            action = "Reject the corrupted channel and inspect the sensor/data path."

        return {
            "node_id": reading["node_id"],
            "timestamp": reading.get("timestamp"),
            "anomaly_type": anomaly_type,
            "event_type": "anomaly",
            "parameter": parameter,
            "suspected_sensor": suspected_sensor,
            "confidence": 100,
            "severity": severity,
            "message": message,
            "observed_value": float(observed) if isinstance(observed, (int, float)) else None,
            "expected_value": corrected,
            "corrected_value": corrected,
            "reasons": [message, "deterministic quality gate evaluated before Random Forest inference"],
            "factor_contributions": {"data_quality_gate": 100},
            "recommended_action": action,
            "detector_mode": "hybrid_quality_gate",
            "model_version": MODEL_VERSION,
            "ml_prediction": prediction,
            "ml_confidence": 1.0,
            "ml_source": "quality_gate",
        }

    def _ml_event(self, reading: dict[str, Any], assessment: dict[str, Any]) -> dict[str, Any] | None:
        prediction = assessment.get("prediction")
        confidence = assessment.get("confidence")
        if prediction not in ("temperature_spike", "temperature_drift", "temperature_freeze"):
            return None
        if confidence is None or float(confidence) < self.confidence_threshold:
            return None

        anomaly_type = prediction.replace("temperature_", "")
        raw = reading.get("raw_sensors") or {}
        observed = raw.get("ds18b20_temperature_c", reading.get("temperature_c"))
        references = [
            raw.get("dht22_temperature_c"),
            raw.get("bmp280_temperature_c", raw.get("bmp180_temperature_c")),
        ]
        references = [float(value) for value in references if value is not None]
        expected = float(median(references)) if references else reading.get("reference_temperature_c")
        feature_summary = assessment.get("feature_summary") or {}
        probability_pct = int(round(float(confidence) * 100))
        severity = "critical" if probability_pct >= 95 and anomaly_type == "spike" else "high" if probability_pct >= 85 else "warning"
        reasons = [
            f"Random Forest classified {prediction.replace('_', ' ')} with {probability_pct}% confidence",
        ]
        if anomaly_type == "spike":
            reasons.append(f"30 s temperature range is {feature_summary.get('ds_range_30s', 0):.2f} C")
        elif anomaly_type == "drift":
            reasons.append(f"30 s temperature slope is {feature_summary.get('ds_slope_c_per_min_30s', 0):.2f} C/min")
        else:
            reasons.append(f"DS18B20 repeated for {feature_summary.get('ds_same_value_duration_s', 0):.1f} s")
            reasons.append("reference temperature sensors provide independent context")

        return {
            "node_id": reading["node_id"],
            "timestamp": reading.get("timestamp"),
            "anomaly_type": anomaly_type,
            "event_type": "anomaly",
            "parameter": "temperature",
            "suspected_sensor": "ds18b20",
            "confidence": probability_pct,
            "severity": severity,
            "message": reasons[0],
            "observed_value": round(float(observed), 3) if observed is not None else None,
            "expected_value": round(float(expected), 3) if expected is not None else None,
            "corrected_value": round(float(expected), 3) if expected is not None else None,
            "reasons": reasons,
            "factor_contributions": {
                "random_forest_probability": probability_pct,
                "temporal_window": min(100, int(round(float(assessment.get("window_span_seconds", 0.0))))),
            },
            "recommended_action": {
                "spike": "Validate DS18B20 against redundant temperature sensors and inspect transient interference.",
                "drift": "Inspect DS18B20 calibration and compare its trend with redundant sensors and peers.",
                "freeze": "Inspect DS18B20 wiring/data line; use redundant or peer temperature until recovery.",
            }[anomaly_type],
            "detector_mode": DETECTOR_MODE,
            "model_version": MODEL_VERSION,
            "ml_prediction": prediction,
            "ml_confidence": float(confidence),
            "ml_probabilities": assessment.get("probabilities", {}),
            "ml_source": "environmental_random_forest",
        }

    def fuse_events(
        self,
        reading: dict[str, Any],
        heuristic_events: list[dict[str, Any]],
        assessment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        events = list(heuristic_events)
        candidate = self._quality_event(reading, assessment) or self._ml_event(reading, assessment)
        if candidate is not None and candidate.get("ml_source") == "quality_gate":
            parameter = candidate.get("parameter")
            # Quality gates are authoritative for the affected raw channel. Avoid
            # duplicate/contradictory heuristic events for the same parameter.
            events = [event for event in events if event.get("parameter") != parameter]
            events.append(candidate)
            return events
        if (
            candidate is not None
            and candidate.get("anomaly_type") == "freeze"
            and candidate.get("parameter") == "temperature"
            and float(candidate.get("ml_confidence") or 0.0) >= FREEZE_ML_STRONG_CONFIDENCE
        ):
            # Temperature freeze is ML-only. When RF has strong freeze evidence,
            # do not let a conflicting heuristic subtype hide that classification.
            events = [event for event in events if event.get("parameter") != "temperature"]
            events.append(candidate)
            return events

        if candidate is None:
            # Keep any heuristic safety event even if ML says normal; attach the
            # independent assessment for transparent disagreement in the UI/API.
            if assessment.get("ready") and assessment.get("prediction") is not None:
                for event in events:
                    if event.get("parameter") == "temperature":
                        event.setdefault("ml_prediction", assessment.get("prediction"))
                        event.setdefault("ml_confidence", assessment.get("confidence"))
                        event.setdefault("ml_probabilities", assessment.get("probabilities", {}))
            return events

        matching = next(
            (
                event
                for event in events
                if event.get("anomaly_type") == candidate.get("anomaly_type")
                and event.get("parameter") == candidate.get("parameter")
            ),
            None,
        )
        if matching is not None:
            heuristic_mode = matching.get("detector_mode")
            heuristic_version = matching.get("model_version")
            matching["heuristic_detector_mode"] = heuristic_mode
            matching["heuristic_model_version"] = heuristic_version
            matching["detector_mode"] = "hybrid_rf_v6_fused"
            matching["model_version"] = MODEL_VERSION
            matching["ml_prediction"] = candidate.get("ml_prediction")
            matching["ml_confidence"] = candidate.get("ml_confidence")
            matching["ml_probabilities"] = candidate.get("ml_probabilities", {})
            matching["ml_source"] = candidate.get("ml_source")
            matching["confidence"] = max(matching.get("confidence", 0), candidate.get("confidence", 0))
            matching.setdefault("reasons", []).append(
                f"Hybrid RF v6 independently supports this classification ({candidate.get('confidence', 0)}% confidence)."
            )
            matching.setdefault("factor_contributions", {})["random_forest_probability"] = candidate.get("confidence", 0)
            return events

        # If another strong temperature heuristic disagrees on subtype, keep one
        # operational event and expose the ML assessment additively rather than
        # manufacturing a simultaneous multi-fault on the same sensor.
        conflicting = [event for event in events if event.get("parameter") == candidate.get("parameter")]
        if conflicting:
            strongest = max(conflicting, key=lambda item: item.get("confidence", 0))
            strongest["ml_prediction"] = candidate.get("ml_prediction")
            strongest["ml_confidence"] = candidate.get("ml_confidence")
            strongest["ml_probabilities"] = candidate.get("ml_probabilities", {})
            strongest["ml_source"] = candidate.get("ml_source")
            return events

        events.append(candidate)
        return events


ml_service = HybridRFService()
