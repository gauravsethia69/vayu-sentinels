from __future__ import annotations

from pathlib import Path
from collections import deque
from datetime import datetime, timezone
import math
import threading

import numpy as np
import torch
from torch import nn


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"

MODEL_NAME = "skyguard_pytorch_multiclass_v6_3_multistation_recovery.pt"
MODEL_PATH = MODEL_DIR / MODEL_NAME

SUPPORTED_NODES = ["AWS_001", "AWS_002", "AWS_003"]

MODEL_VERSION = "V6.3"
VALIDATION_ROLE = "guarded multi-station live-validated"

CONFIRM_CONFIDENCE = 0.70
CONFIRM_WINDOWS = 3

HARD_RECOVERY_REQUIRED = 3

FREEZE_STUCK_SECONDS = 60.0
FREEZE_TOLERANCE_C = 0.03
FREEZE_REFERENCE_CHANGE_C = 0.10
FREEZE_LONG_STUCK_SECONDS = 75.0
FREEZE_LONG_REQUIRED_WINDOWS = 8
FREEZE_LONG_AVG_PROBABILITY = 0.60


class SkyGuardTemporalCNN(nn.Module):
    def __init__(self, n_features: int, n_classes: int):
        super().__init__()

        self.net = nn.Sequential(
            nn.Conv1d(n_features, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),

            nn.Conv1d(32, 48, kernel_size=3, padding=1),
            nn.BatchNorm1d(48),
            nn.ReLU(),

            nn.Conv1d(48, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.ReLU(),

            nn.AdaptiveAvgPool1d(1),
        )

        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(0.25),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(0.15),
            nn.Linear(32, n_classes),
        )

    def forward(self, x):
        x = x.transpose(1, 2)
        x = self.net(x)
        return self.head(x)


def safe_float(value):
    if value is None:
        return None

    try:
        if value == "":
            return None

        value = float(value)

        if math.isnan(value) or math.isinf(value):
            return None

        return value

    except Exception:
        return None


def mean_ignore_none(values):
    clean = [float(v) for v in values if v is not None]

    if not clean:
        return None

    return float(sum(clean) / len(clean))


def is_missing(value):
    return value is None


def is_corrupt_ds(value):
    if value is None:
        return False

    return value <= -100 or value >= 100


class GuardedV63NodeClassifier:
    def __init__(self, node_id: str, model, package):
        self.node_id = node_id
        self.model = model
        self.package = package

        self.classes = list(package["classes"])
        self.feature_columns = list(package["feature_columns"])
        self.sequence_length = int(package["sequence_length"])

        self.mean = np.asarray(package["mean"], dtype=np.float32)
        self.std = np.asarray(package["std"], dtype=np.float32)
        self.std = np.where(self.std < 1e-6, 1.0, self.std)

        self.history = deque(maxlen=180)
        self.recent_predictions = deque(maxlen=CONFIRM_WINDOWS)
        self.recent_freeze_probs = deque(maxlen=30)

        self.same_label = None
        self.same_count = 0

        self.hard_fault_active = None
        self.hard_recovery_count = 0

        self.predictions = 0
        self.last_prediction_at = None

        self.lock = threading.Lock()

    def _sensor_value(self, sensors: dict, *keys):
        for key in keys:
            if key in sensors:
                return safe_float(sensors.get(key))
        return None

    def _row_from_sensors(self, sensors: dict):
        now = datetime.now(timezone.utc)

        return {
            "timestamp": now,
            "node_id": self.node_id,
            "ds18b20_temperature_c": self._sensor_value(
                sensors,
                "ds18b20_temperature_c",
                "ds18b20",
                "temperature_ds18b20",
            ),
            "dht22_temperature_c": self._sensor_value(
                sensors,
                "dht22_temperature_c",
                "dht22_temp",
                "temperature_dht22",
            ),
            "dht22_humidity_pct": self._sensor_value(
                sensors,
                "dht22_humidity_pct",
                "humidity",
                "humidity_pct",
            ),
            "bmp280_temperature_c": self._sensor_value(
                sensors,
                "bmp280_temperature_c",
                "bmp280_temp",
                "temperature_bmp280",
            ),
            "bmp280_pressure_hpa": self._sensor_value(
                sensors,
                "bmp280_pressure_hpa",
                "pressure",
                "pressure_hpa",
            ),
        }

    def _clean_row(self, row):
        ds = row["ds18b20_temperature_c"]
        dht = row["dht22_temperature_c"]
        bmp = row["bmp280_temperature_c"]

        ds_missing = 1.0 if is_missing(ds) else 0.0
        ds_corrupt = 1.0 if is_corrupt_ds(ds) else 0.0

        ref_temp = mean_ignore_none([dht, bmp])

        ds_clean = ds

        if ds_missing or ds_corrupt:
            ds_clean = ref_temp

        return {
            "ds_clean": ds_clean,
            "dht_clean": dht,
            "bmp_clean": bmp,
            "humidity_clean": row["dht22_humidity_pct"],
            "pressure_clean": row["bmp280_pressure_hpa"],
            "ds_missing": ds_missing,
            "ds_corrupt": ds_corrupt,
        }

    def _range_last(self, cleaned_rows, col, window):
        values = [
            row[col]
            for row in cleaned_rows[-window:]
            if row.get(col) is not None
        ]

        if len(values) < 2:
            return 0.0

        return float(max(values) - min(values))

    def _build_features(self):
        rows = list(self.history)
        cleaned = [self._clean_row(row) for row in rows]

        feature_rows = []

        for i, clean in enumerate(cleaned):
            prev = cleaned[i - 1] if i > 0 else None

            ds_clean = clean["ds_clean"]
            dht_clean = clean["dht_clean"]
            bmp_clean = clean["bmp_clean"]

            ds_dht_diff = None
            ds_bmp_diff = None
            dht_bmp_diff = None

            if ds_clean is not None and dht_clean is not None:
                ds_dht_diff = ds_clean - dht_clean

            if ds_clean is not None and bmp_clean is not None:
                ds_bmp_diff = ds_clean - bmp_clean

            if dht_clean is not None and bmp_clean is not None:
                dht_bmp_diff = dht_clean - bmp_clean

            base = {
                "ds_clean": ds_clean,
                "dht_clean": dht_clean,
                "bmp_clean": bmp_clean,
                "humidity_clean": clean["humidity_clean"],
                "pressure_clean": clean["pressure_clean"],
                "ds_missing": clean["ds_missing"],
                "ds_corrupt": clean["ds_corrupt"],
                "ds_dht_diff": ds_dht_diff,
                "ds_bmp_diff": ds_bmp_diff,
                "dht_bmp_diff": dht_bmp_diff,
            }

            for col in [
                "ds_clean",
                "dht_clean",
                "bmp_clean",
                "humidity_clean",
                "pressure_clean",
                "ds_dht_diff",
                "ds_bmp_diff",
            ]:
                current_value = base.get(col)
                previous_value = None

                if prev is not None:
                    if col in ("ds_dht_diff", "ds_bmp_diff"):
                        prev_ds = prev.get("ds_clean")
                        prev_dht = prev.get("dht_clean")
                        prev_bmp = prev.get("bmp_clean")

                        if col == "ds_dht_diff" and prev_ds is not None and prev_dht is not None:
                            previous_value = prev_ds - prev_dht

                        if col == "ds_bmp_diff" and prev_ds is not None and prev_bmp is not None:
                            previous_value = prev_ds - prev_bmp
                    else:
                        previous_value = prev.get(col)

                if current_value is None or previous_value is None:
                    base[f"{col}_diff"] = 0.0
                else:
                    base[f"{col}_diff"] = float(current_value - previous_value)

            for window in [5, 12]:
                base[f"ds_range_{window}"] = self._range_last(cleaned[:i + 1], "ds_clean", window)
                base[f"dht_range_{window}"] = self._range_last(cleaned[:i + 1], "dht_clean", window)
                base[f"bmp_range_{window}"] = self._range_last(cleaned[:i + 1], "bmp_clean", window)

            feature_rows.append(base)

        matrix = []

        for row in feature_rows[-self.sequence_length:]:
            matrix.append([
                row.get(col)
                for col in self.feature_columns
            ])

        x = np.asarray(matrix, dtype=np.float32)

        if np.isnan(x).any():
            col_means = np.nanmean(x, axis=0)
            col_means = np.where(np.isnan(col_means), 0.0, col_means)
            inds = np.where(np.isnan(x))
            x[inds] = np.take(col_means, inds[1])

        x = (x - self.mean) / self.std

        return x

    def _has_high_conf_streak(self, label):
        recent = list(self.recent_predictions)

        if len(recent) < CONFIRM_WINDOWS:
            return False

        recent = recent[-CONFIRM_WINDOWS:]

        return all(
            pred == label and conf is not None and float(conf) >= CONFIRM_CONFIDENCE
            for pred, conf in recent
        )

    def _recent_sensor_rows(self, max_rows=16):
        rows = []

        for row in list(self.history)[-max_rows:]:
            ds = row["ds18b20_temperature_c"]
            dht = row["dht22_temperature_c"]
            bmp = row["bmp280_temperature_c"]

            if ds is None or dht is None or bmp is None:
                continue

            if is_corrupt_ds(ds):
                continue

            rows.append(row)

        return rows

    def _spike_physics_gate(self):
        rows = self._recent_sensor_rows(max_rows=8)

        if len(rows) < 2:
            return False

        current = rows[-1]
        previous = rows[-2]

        current_ref = mean_ignore_none([
            current["dht22_temperature_c"],
            current["bmp280_temperature_c"],
        ])

        previous_ref = mean_ignore_none([
            previous["dht22_temperature_c"],
            previous["bmp280_temperature_c"],
        ])

        if current_ref is None or previous_ref is None:
            return False

        current_ds = current["ds18b20_temperature_c"]
        previous_ds = previous["ds18b20_temperature_c"]

        current_bias = current_ds - current_ref
        previous_bias = previous_ds - previous_ref

        ds_jump = abs(current_ds - previous_ds)
        bias_jump = abs(current_bias - previous_bias)

        return (
            abs(current_bias) >= 2.0
            or ds_jump >= 1.5
            or bias_jump >= 1.2
        )

    def _drift_physics_gate(self):
        rows = self._recent_sensor_rows(max_rows=16)

        if len(rows) < 8:
            return False

        ds = np.asarray([row["ds18b20_temperature_c"] for row in rows], dtype=float)
        dht = np.asarray([row["dht22_temperature_c"] for row in rows], dtype=float)
        bmp = np.asarray([row["bmp280_temperature_c"] for row in rows], dtype=float)

        ref = np.nanmean(np.vstack([dht, bmp]), axis=0)
        bias = ds - ref

        ds_range = float(np.nanmax(ds) - np.nanmin(ds))
        ref_range = float(np.nanmax(ref) - np.nanmin(ref))
        bias_range = float(np.nanmax(bias) - np.nanmin(bias))
        bias_delta = float(bias[-1] - bias[0])

        x = np.arange(len(bias), dtype=float)
        slope = float(np.polyfit(x, bias, 1)[0])

        return (
            abs(bias_delta) >= 0.50
            and bias_range >= 0.50
            and ds_range >= 0.45
            and abs(slope) >= 0.035
            and (ds_range - ref_range) >= 0.20
        )

    def _freeze_gate(self):
        rows = self._recent_sensor_rows(max_rows=180)

        if len(rows) < 20:
            return False, "insufficient_history"

        end_time = rows[-1]["timestamp"]

        recent_60 = [
            row for row in rows
            if (end_time - row["timestamp"]).total_seconds() <= FREEZE_STUCK_SECONDS
        ]

        recent_75 = [
            row for row in rows
            if (end_time - row["timestamp"]).total_seconds() <= FREEZE_LONG_STUCK_SECONDS
        ]

        if len(recent_60) >= 20:
            ds_values = [row["ds18b20_temperature_c"] for row in recent_60]
            dht_values = [row["dht22_temperature_c"] for row in recent_60]
            bmp_values = [row["bmp280_temperature_c"] for row in recent_60]

            ds_range = max(ds_values) - min(ds_values)
            dht_range = max(dht_values) - min(dht_values)
            bmp_range = max(bmp_values) - min(bmp_values)

            reference_change = max(dht_range, bmp_range)

            if ds_range <= FREEZE_TOLERANCE_C and reference_change >= FREEZE_REFERENCE_CHANGE_C:
                return True, "reference_supported"

        if len(recent_75) >= 25:
            ds_values = [row["ds18b20_temperature_c"] for row in recent_75]
            ds_range_long = max(ds_values) - min(ds_values)

            recent_probs = list(self.recent_freeze_probs)[-FREEZE_LONG_REQUIRED_WINDOWS:]

            if len(recent_probs) >= FREEZE_LONG_REQUIRED_WINDOWS:
                avg_freeze_prob = float(np.mean(recent_probs))

                if (
                    ds_range_long <= FREEZE_TOLERANCE_C
                    and avg_freeze_prob >= FREEZE_LONG_AVG_PROBABILITY
                ):
                    return True, "long_duration_model_supported"

        return False, "blocked"

    def process(self, sensors: dict):
        with self.lock:
            if sensors is None:
                sensors = {}

            row = self._row_from_sensors(sensors)
            self.history.append(row)

            ds = row["ds18b20_temperature_c"]

            hard_fault = False
            hard_fault_type = None

            if ds is None:
                hard_fault = True
                hard_fault_type = "data_loss"

            elif is_corrupt_ds(ds):
                hard_fault = True
                hard_fault_type = "corruption"

            if hard_fault:
                self.hard_fault_active = hard_fault_type
                self.hard_recovery_count = 0

            elif self.hard_fault_active is not None:
                self.hard_recovery_count += 1

                if self.hard_recovery_count >= HARD_RECOVERY_REQUIRED:
                    self.hard_fault_active = None
                    self.hard_recovery_count = 0

            if len(self.history) < self.sequence_length:
                return {
                    "supported": True,
                    "model": MODEL_NAME,
                    "model_version": MODEL_VERSION,
                    "validation_role": VALIDATION_ROLE,
                    "prediction": hard_fault_type,
                    "normalized_prediction": hard_fault_type,
                    "confidence": 1.0 if hard_fault else None,
                    "ready": False,
                    "warming_up": True,
                    "confirmed": bool(self.hard_fault_active),
                    "confirmed_fault": self.hard_fault_active,
                    "hard_fault": hard_fault,
                    "hard_fault_type": hard_fault_type,
                    "safety_layer": "v6_3_guarded",
                    "readings": len(self.history),
                }

            x = self._build_features()
            xb = torch.tensor(x[None, :, :], dtype=torch.float32)

            with torch.no_grad():
                logits = self.model(xb)
                probs = torch.softmax(logits, dim=1).cpu().numpy()[0]

            pred_id = int(np.argmax(probs))
            prediction = self.classes[pred_id]
            confidence = float(probs[pred_id])

            probabilities = {
                self.classes[i]: float(probs[i])
                for i in range(len(self.classes))
            }

            self.recent_freeze_probs.append(probabilities.get("freeze", 0.0))

            if prediction == self.same_label:
                self.same_count += 1
            else:
                self.same_label = prediction
                self.same_count = 1

            self.recent_predictions.append((prediction, confidence))

            confirmed = False
            confirmed_fault = None
            gate_reason = None

            if hard_fault:
                confirmed = True
                confirmed_fault = hard_fault_type

            elif self.hard_fault_active is not None:
                confirmed = True
                confirmed_fault = self.hard_fault_active

            elif prediction == "spike":
                if self._has_high_conf_streak("spike") and self._spike_physics_gate():
                    confirmed = True
                    confirmed_fault = "spike"
                    gate_reason = "spike_physics_gate"

            elif prediction == "drift":
                if self._has_high_conf_streak("drift") and self._drift_physics_gate():
                    confirmed = True
                    confirmed_fault = "drift"
                    gate_reason = "drift_physics_gate"

            elif prediction == "freeze":
                gate_ok, freeze_path = self._freeze_gate()
                gate_reason = freeze_path

                if self._has_high_conf_streak("freeze") and gate_ok:
                    confirmed = True
                    confirmed_fault = "freeze"

            self.predictions += 1
            self.last_prediction_at = datetime.now(timezone.utc).isoformat()

            return {
                "supported": True,
                "model": MODEL_NAME,
                "model_version": MODEL_VERSION,
                "validation_role": VALIDATION_ROLE,
                "prediction": prediction,
                "normalized_prediction": prediction,
                "confidence": confidence,
                "ready": True,
                "warming_up": False,
                "confirmed": confirmed,
                "confirmed_fault": confirmed_fault,
                "hard_fault": hard_fault,
                "hard_fault_type": hard_fault_type,
                "probabilities": probabilities,
                "safety_layer": "v6_3_guarded",
                "gate_reason": gate_reason,
                "same_count": self.same_count,
                "readings": len(self.history),
            }


class StationAwarePyTorchDetector:
    def __init__(self):
        self.loaded = False
        self.error = None
        self.package = None
        self.model = None
        self.nodes = {}

        self._load()

    def _load(self):
        print("\n" + "=" * 70)
        print("[PYTORCH] Loading V6.3 guarded multi-station model")
        print("=" * 70)

        try:
            if not MODEL_PATH.exists():
                raise FileNotFoundError(str(MODEL_PATH))

            package = torch.load(MODEL_PATH, map_location="cpu")

            model = SkyGuardTemporalCNN(
                n_features=len(package["feature_columns"]),
                n_classes=len(package["classes"]),
            )

            model.load_state_dict(package["model_state_dict"])
            model.eval()

            self.package = package
            self.model = model

            for node_id in SUPPORTED_NODES:
                self.nodes[node_id] = GuardedV63NodeClassifier(
                    node_id=node_id,
                    model=model,
                    package=package,
                )

            self.loaded = True
            self.error = None

            print("[PYTORCH] V6.3 guarded model loaded successfully")
            print("[PYTORCH] Model:", MODEL_NAME)
            print("[PYTORCH] Version:", package.get("model_version"))
            print("[PYTORCH] Nodes:", ", ".join(SUPPORTED_NODES))
            print("=" * 70)

        except Exception as exc:
            self.loaded = False
            self.error = f"{type(exc).__name__}: {exc}"

            print("[PYTORCH] V6.3 guarded model DISABLED:")
            print(self.error)
            print("=" * 70)

    def process(self, node_id: str, sensors: dict):
        if node_id not in SUPPORTED_NODES:
            return {
                "supported": False,
                "model": MODEL_NAME,
                "model_version": MODEL_VERSION,
                "validation_role": "unsupported node",
                "prediction": None,
                "normalized_prediction": None,
                "confidence": None,
                "ready": False,
                "warming_up": False,
                "confirmed": False,
                "confirmed_fault": None,
                "hard_fault": False,
                "hard_fault_type": None,
                "error": f"Unsupported node: {node_id}",
            }

        if not self.loaded:
            return {
                "supported": True,
                "model": MODEL_NAME,
                "model_version": MODEL_VERSION,
                "validation_role": VALIDATION_ROLE,
                "prediction": None,
                "normalized_prediction": None,
                "confidence": None,
                "ready": False,
                "warming_up": False,
                "confirmed": False,
                "confirmed_fault": None,
                "hard_fault": False,
                "hard_fault_type": None,
                "error": self.error,
            }

        return self.nodes[node_id].process(sensors or {})

    def status(self):
        node_status = {}

        for node_id in SUPPORTED_NODES:
            classifier = self.nodes.get(node_id)

            node_status[node_id] = {
                "loaded": bool(self.loaded and classifier is not None),
                "model": MODEL_NAME,
                "model_version": MODEL_VERSION,
                "validation_role": VALIDATION_ROLE,
                "model_exists": MODEL_PATH.exists(),
                "error": self.error,
                "classes": list(self.package["classes"]) if self.loaded and self.package else [],
                "sequence_length": int(self.package["sequence_length"]) if self.loaded and self.package else None,
                "feature_count": len(self.package["feature_columns"]) if self.loaded and self.package else None,
                "freeze_semantic_gate": {
                    "enabled": True,
                    "min_confidence": CONFIRM_CONFIDENCE,
                    "confirmation_windows": CONFIRM_WINDOWS,
                    "ds_stuck_min_seconds": FREEZE_STUCK_SECONDS,
                    "ds_stuck_tolerance_c": FREEZE_TOLERANCE_C,
                    "reference_change_min_c": FREEZE_REFERENCE_CHANGE_C,
                },
                "safety_gates": {
                    "hard_faults": ["data_loss", "corruption"],
                    "spike_physics_gate": True,
                    "drift_physics_gate": True,
                    "freeze_semantic_gate": True,
                    "high_confidence_streak": CONFIRM_WINDOWS,
                    "hard_recovery_required": HARD_RECOVERY_REQUIRED,
                },
                "readings": len(classifier.history) if classifier else 0,
                "predictions": classifier.predictions if classifier else 0,
                "last_prediction_at": classifier.last_prediction_at if classifier else None,
            }

        return {
            "loaded": self.loaded,
            "any_loaded": self.loaded,
            "station_aware": True,
            "guarded": True,
            "supported_nodes": SUPPORTED_NODES,
            "routing": {
                "AWS_001": "PyTorch V6.3 guarded",
                "AWS_002": "PyTorch V6.3 guarded",
                "AWS_003": "PyTorch V6.3 guarded",
            },
            "nodes": node_status,
            "error": self.error,
        }


pytorch_detector = StationAwarePyTorchDetector()
