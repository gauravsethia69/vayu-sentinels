from __future__ import annotations

from abc import ABC, abstractmethod
from statistics import mean, median, pstdev

from .config import (
    DRIFT_ACCUMULATION_THRESHOLDS,
    DRIFT_MIN_SAMPLES,
    DRIFT_SLOPE_THRESHOLDS,
    NODE_MISMATCH_THRESHOLDS,
    PEER_AGREEMENT_TOLERANCES,
    PHYSICAL_RANGES,
    REFERENCE_SENSOR_AGREEMENT_C,
    SPIKE_THRESHOLDS,
    TEMPERATURE_DISAGREEMENT_STRONG_C,
    TEMPERATURE_DISAGREEMENT_WARNING_C,
)


class Detector(ABC):
    @abstractmethod
    def detect(self, reading, history, peer): ...


class HeuristicDetector(Detector):
    mode = "heuristic_nonfreeze_safety"
    version = "heuristic-v2"
    keys = {
        "temperature": "temperature_c",
        "pressure": "pressure_hpa",
        "humidity": "humidity_pct",
    }

    def _event(
        self,
        *,
        node_id,
        anomaly_type,
        parameter,
        confidence,
        severity,
        observed_value,
        expected_value,
        corrected_value,
        reasons,
        factors,
        action,
        suspected_sensor=None,
        event_type="anomaly",
        **extra,
    ):
        def rounded(value):
            return round(value, 3) if isinstance(value, (int, float)) else value

        return {
            "node_id": node_id,
            "anomaly_type": anomaly_type,
            "event_type": event_type,
            "parameter": parameter,
            "suspected_sensor": suspected_sensor,
            "confidence": int(confidence),
            "severity": severity,
            "message": reasons[0] if reasons else anomaly_type.replace("_", " ").title(),
            "observed_value": rounded(observed_value),
            "expected_value": rounded(expected_value),
            "corrected_value": rounded(corrected_value),
            "reasons": reasons,
            "factor_contributions": factors,
            "recommended_action": action,
            "detector_mode": self.mode,
            "model_version": self.version,
            **extra,
        }

    def detect_missing(self, node_id, timestamp, fault_type="data_loss", reason=None):
        if reason is None:
            reason = (
                "sensor sample unavailable while station remains reachable"
                if fault_type == "data_loss"
                else "no station packet received within communication timeout"
            )
        event = self._event(
            node_id=node_id,
            anomaly_type=fault_type,
            parameter=None,
            confidence=85,
            severity="high",
            observed_value=None,
            expected_value=None,
            corrected_value=None,
            reasons=[reason],
            factors={"missing_packet": 45},
            action="Check sensor wiring, station communications, and power path.",
        )
        event["timestamp"] = timestamp
        return event

    def network_event(self, node_id, timestamp, anomaly_type, observed, expected, reason):
        confidence = {"network_delay": 70, "packet_gap": 76, "out_of_order_packet": 72}[anomaly_type]
        return {
            **self._event(
                node_id=node_id,
                anomaly_type=anomaly_type,
                parameter=None,
                confidence=confidence,
                severity="warning",
                observed_value=observed,
                expected_value=expected,
                corrected_value=None,
                reasons=[reason],
                factors={"packet_timing": confidence - 35},
                action="Inspect Wi-Fi signal, packet sequencing, and station connectivity.",
                event_type="communication",
            ),
            "timestamp": timestamp,
        }

    def recovery_event(self, node_id, timestamp, parameter, healthy_samples):
        return {
            **self._event(
                node_id=node_id,
                anomaly_type="sensor_recovered",
                parameter=parameter,
                confidence=100,
                severity="info",
                observed_value=None,
                expected_value=None,
                corrected_value=None,
                reasons=[f"{parameter.title()} sensor stable for {healthy_samples} consecutive readings"],
                factors={"healthy_streak": healthy_samples},
                action="Continue normal monitoring.",
                event_type="recovery",
                healthy_samples=healthy_samples,
                affected_sensor=parameter,
            ),
            "timestamp": timestamp,
        }

    def _cross_sensor_events(self, reading, history):
        sensors = reading.get("raw_sensors") or {}
        primary = sensors.get("ds18b20_temperature_c")
        dht = sensors.get("dht22_temperature_c")
        bmp = sensors.get("bmp280_temperature_c", sensors.get("bmp180_temperature_c"))
        if None in (primary, dht, bmp):
            return []

        references_agree = abs(dht - bmp) <= REFERENCE_SENSOR_AGREEMENT_C
        reference = median((dht, bmp))
        difference = abs(primary - reference)
        events = []
        if references_agree and difference > TEMPERATURE_DISAGREEMENT_WARNING_C:
            strong = difference > TEMPERATURE_DISAGREEMENT_STRONG_C
            confidence = min(98, round(68 + difference * 4))
            reasons = [
                f"DS18B20 differs from DHT22 by {abs(primary - dht):.2f} C",
                f"DS18B20 differs from the barometric reference by {abs(primary - bmp):.2f} C",
                "two independent reference temperature sensors agree",
            ]
            events.append(
                self._event(
                    node_id=reading["node_id"],
                    anomaly_type="sensor_disagreement",
                    parameter="temperature",
                    suspected_sensor="ds18b20",
                    confidence=confidence,
                    severity="high" if strong else "warning",
                    observed_value=primary,
                    expected_value=reference,
                    corrected_value=reference,
                    reasons=reasons,
                    factors={
                        "cross_sensor_disagreement": 40 if strong else 28,
                        "reference_sensor_consensus": 30,
                    },
                    action="Inspect or recalibrate the DS18B20 temperature sensor.",
                )
            )

        offsets = []
        for sample in history[-8:] + [reading]:
            sample_sensors = sample.get("raw_sensors") or {}
            sample_primary = sample_sensors.get("ds18b20_temperature_c")
            sample_reference = sample.get("reference_temperature_c")
            if sample_primary is not None and sample_reference is not None:
                offsets.append(abs(sample_primary - sample_reference))
        if len(offsets) >= 7:
            early = mean(offsets[:3])
            recent = mean(offsets[-3:])
            if recent > max(2.0, early + 1.2) and offsets[-1] > offsets[0] + 1.5:
                events.append(
                    self._event(
                        node_id=reading["node_id"],
                        anomaly_type="sensor_degradation",
                        parameter="temperature",
                        suspected_sensor="ds18b20",
                        confidence=min(92, round(62 + recent * 5)),
                        severity="high" if recent > 4 else "warning",
                        observed_value=primary,
                        expected_value=reference,
                        corrected_value=reference,
                        reasons=[
                            "persistent disagreement with redundant temperature sensors",
                            "cross-sensor deviation increased over the observation window",
                            "measurement bias/noise is increasing",
                        ],
                        factors={"increasing_sensor_offset": 38, "reference_sensor_consensus": 28},
                        action="Schedule DS18B20 inspection or recalibration.",
                    )
                )
        return events

    def detect(self, reading, history, peer):
        events = self._cross_sensor_events(reading, history)
        for parameter, key in self.keys.items():
            value = reading.get(key)
            if value is None:
                continue
            values = [sample.get(key) for sample in history[-20:] if sample.get(key) is not None]
            reasons = []
            factors = {}
            anomaly_type = None
            low, high = PHYSICAL_RANGES[parameter]
            expected = median(values[-8:]) if values else value
            peer_value = peer.get(key) if peer else None
            peer_agrees = bool(
                peer_value is not None
                and abs(value - peer_value) <= PEER_AGREEMENT_TOLERANCES[parameter]
            )

            if not low <= value <= high:
                anomaly_type = "out_of_range"
                reasons.append("outside configured physical range")
                factors["valid_range"] = 60

            if len(values) >= 3:
                delta = abs(value - values[-1])
                std_floor = 0.15 if parameter == "temperature" else 0.3
                std = max(pstdev(values[-8:]), std_floor)
                if delta > SPIKE_THRESHOLDS[parameter] and not peer_agrees and not anomaly_type:
                    anomaly_type = "spike"
                    reasons.extend(
                        [
                            "sudden rate of change exceeds threshold",
                            "nearby station does not show the same change",
                        ]
                    )
                    factors.update({"rate_of_change": 40, "peer_node_disagreement": 22})
                    if parameter == "temperature" and reading.get("reference_temperature_c") is not None:
                        reference_difference = abs(value - reading["reference_temperature_c"])
                        if reference_difference > TEMPERATURE_DISAGREEMENT_WARNING_C:
                            reasons.append("primary temperature disagrees with same-node reference sensors")
                            factors["cross_sensor_disagreement"] = 28

                drift_samples = history[-(DRIFT_MIN_SAMPLES - 1):] + [reading]
                drift_window = [sample.get(key) for sample in drift_samples]
                if (
                    len(drift_window) == DRIFT_MIN_SAMPLES
                    and all(item is not None for item in drift_window)
                    and not anomaly_type
                ):
                    differences = [
                        drift_window[index + 1] - drift_window[index]
                        for index in range(len(drift_window) - 1)
                    ]
                    monotonic = all(item > 0.01 for item in differences) or all(
                        item < -0.01 for item in differences
                    )
                    accumulated = abs(drift_window[-1] - drift_window[0])
                    slope = accumulated / (DRIFT_MIN_SAMPLES - 1)
                    reference_value = reading.get("reference_temperature_c")
                    same_node_reference_disagrees = bool(
                        parameter == "temperature"
                        and reference_value is not None
                        and abs(value - reference_value) > TEMPERATURE_DISAGREEMENT_WARNING_C
                    )
                    reference_support = bool(
                        (peer_value is not None and not peer_agrees) or same_node_reference_disagrees
                    )
                    if (
                        monotonic
                        and accumulated > DRIFT_ACCUMULATION_THRESHOLDS[parameter]
                        and slope >= DRIFT_SLOPE_THRESHOLDS[parameter]
                        and reference_support
                    ):
                        anomaly_type = "drift"
                        expected = peer_value if peer_value is not None else reference_value
                        reasons.extend(
                            [
                                "sustained monotonic deviation",
                                "accumulated offset exceeds threshold",
                                "independent reference remains outside the drift trend",
                            ]
                        )
                        factors.update(
                            {"temporal_drift": 40, "accumulated_offset": 25, "reference_consistency": 20}
                        )

                own_baseline_deviation = abs(value - expected)
                if (
                    peer_value is not None
                    and abs(value - peer_value) > NODE_MISMATCH_THRESHOLDS[parameter]
                    and own_baseline_deviation > PEER_AGREEMENT_TOLERANCES[parameter]
                    and not anomaly_type
                ):
                    anomaly_type = "node_mismatch"
                    expected = peer_value
                    reasons.append("recent nearby station reading differs beyond spatial tolerance")
                    factors["reference_node_mismatch"] = 35

                if own_baseline_deviation > 3 * std and not peer_agrees and not anomaly_type:
                    anomaly_type = "deviation"
                    reasons.append("isolated deviation from rolling baseline")
                    factors["rolling_deviation"] = 30

            if anomaly_type:
                score = min(
                    100,
                    45
                    + sum(factors.values())
                    + (20 if anomaly_type in ("out_of_range", "spike") else 0),
                )
                severity = (
                    "critical"
                    if anomaly_type == "out_of_range" or score >= 90
                    else "high"
                    if score >= 70
                    else "warning"
                )
                events.append(
                    self._event(
                        node_id=reading["node_id"],
                        anomaly_type=anomaly_type,
                        parameter=parameter,
                        confidence=score,
                        severity=severity,
                        observed_value=value,
                        expected_value=expected,
                        corrected_value=expected,
                        reasons=reasons,
                        factors=factors,
                        action="Inspect the sensor and use the trusted estimate until readings stabilize.",
                    )
                )

        temperature = reading.get("temperature_c")
        humidity = reading.get("humidity_pct")
        if temperature is not None and humidity is not None and temperature > 42 and humidity > 88:
            events.append(
                self._event(
                    node_id=reading["node_id"], anomaly_type="multivariate_inconsistency", parameter=None,
                    confidence=82, severity="high", observed_value=None, expected_value=None,
                    corrected_value=None, reasons=["hot temperature paired with implausibly high humidity"],
                    factors={"cross_parameter": 37}, action="Inspect the combined sensor readings.",
                )
            )
        return events
