from pathlib import Path
from collections import deque, Counter
import time
import csv

import numpy as np
import pandas as pd
import requests
import torch
from torch import nn


BASE_URL = "http://127.0.0.1:8000"

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "app" / "models"

MODEL_PATH = MODEL_DIR / "skyguard_pytorch_multiclass_v6_3_multistation_recovery.pt"
OUT_CSV = BASE_DIR / "shadow_live_v6_3_output.csv"

NODES = ["AWS_001", "AWS_002", "AWS_003"]

POLL_SECONDS = 2
DURATION_SECONDS = 5 * 60

CONFIRM_CONFIDENCE = 0.70
CONFIRM_WINDOWS = 3

FREEZE_STUCK_SECONDS = 60.0
FREEZE_TOLERANCE_C = 0.03
FREEZE_REFERENCE_CHANGE_C = 0.10

HARD_RECOVERY_REQUIRED = 3


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
        return np.nan
    try:
        if value == "":
            return np.nan
        return float(value)
    except Exception:
        return np.nan


def extract_sensor(data, top_key, sensor_keys):
    if top_key in data:
        return data.get(top_key)

    sensors = data.get("sensors") or {}

    for key in sensor_keys:
        if key in sensors:
            return sensors.get(key)

    return None


def fetch_latest(node_id):
    response = requests.get(
        f"{BASE_URL}/nodes/{node_id}/latest",
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def latest_to_row(node_id, data):
    return {
        "timestamp": data.get("timestamp"),
        "node_id": node_id,
        "ds18b20_temperature_c": safe_float(
            extract_sensor(
                data,
                "ds18b20_temperature_c",
                ["ds18b20_temperature_c", "ds18b20", "temperature_ds18b20"],
            )
        ),
        "dht22_temperature_c": safe_float(
            extract_sensor(
                data,
                "dht22_temperature_c",
                ["dht22_temperature_c", "dht22_temp", "temperature_dht22"],
            )
        ),
        "dht22_humidity_pct": safe_float(
            extract_sensor(
                data,
                "dht22_humidity_pct",
                ["dht22_humidity_pct", "humidity", "humidity_pct"],
            )
        ),
        "bmp280_temperature_c": safe_float(
            extract_sensor(
                data,
                "bmp280_temperature_c",
                ["bmp280_temperature_c", "bmp280_temp", "temperature_bmp280"],
            )
        ),
        "bmp280_pressure_hpa": safe_float(
            extract_sensor(
                data,
                "bmp280_pressure_hpa",
                ["bmp280_pressure_hpa", "pressure", "pressure_hpa"],
            )
        ),
    }


def add_features(df):
    df = df.copy()

    ds = df["ds18b20_temperature_c"]
    dht = df["dht22_temperature_c"]
    bmp = df["bmp280_temperature_c"]

    df["ds_missing"] = ds.isna().astype(float)

    df["ds_corrupt"] = (
        ds.notna()
        & ((ds <= -100) | (ds >= 100))
    ).astype(float)

    ref_temp = pd.concat([dht, bmp], axis=1).mean(axis=1)

    df["ds_clean"] = ds.copy()
    df.loc[df["ds_missing"] == 1, "ds_clean"] = ref_temp
    df.loc[df["ds_corrupt"] == 1, "ds_clean"] = ref_temp

    df["dht_clean"] = dht
    df["bmp_clean"] = bmp
    df["humidity_clean"] = df["dht22_humidity_pct"]
    df["pressure_clean"] = df["bmp280_pressure_hpa"]

    df["ds_dht_diff"] = df["ds_clean"] - df["dht_clean"]
    df["ds_bmp_diff"] = df["ds_clean"] - df["bmp_clean"]
    df["dht_bmp_diff"] = df["dht_clean"] - df["bmp_clean"]

    group_cols = ["node_id"]

    for col in [
        "ds_clean",
        "dht_clean",
        "bmp_clean",
        "humidity_clean",
        "pressure_clean",
        "ds_dht_diff",
        "ds_bmp_diff",
    ]:
        df[f"{col}_diff"] = (
            df.groupby(group_cols)[col]
            .diff()
            .fillna(0.0)
        )

    for window in [5, 12]:
        for col, out in [
            ("ds_clean", f"ds_range_{window}"),
            ("dht_clean", f"dht_range_{window}"),
            ("bmp_clean", f"bmp_range_{window}"),
        ]:
            rolling_max = (
                df.groupby(group_cols)[col]
                .rolling(window, min_periods=2)
                .max()
                .reset_index(level=0, drop=True)
            )

            rolling_min = (
                df.groupby(group_cols)[col]
                .rolling(window, min_periods=2)
                .min()
                .reset_index(level=0, drop=True)
            )

            df[out] = (rolling_max - rolling_min).fillna(0.0)

    return df


def freeze_gate(history_df):
    if len(history_df) < 20:
        return False

    df = history_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])

    if df.empty:
        return False

    end_time = df["timestamp"].iloc[-1]
    start_time = end_time - pd.Timedelta(seconds=FREEZE_STUCK_SECONDS)

    recent = df[
        (df["timestamp"] >= start_time)
        & (df["timestamp"] <= end_time)
    ]

    if len(recent) < 20:
        return False

    ds_range = (
        recent["ds18b20_temperature_c"].max()
        -
        recent["ds18b20_temperature_c"].min()
    )

    dht_range = (
        recent["dht22_temperature_c"].max()
        -
        recent["dht22_temperature_c"].min()
    )

    bmp_range = (
        recent["bmp280_temperature_c"].max()
        -
        recent["bmp280_temperature_c"].min()
    )

    reference_change = max(
        0.0 if pd.isna(dht_range) else float(dht_range),
        0.0 if pd.isna(bmp_range) else float(bmp_range),
    )

    return (
        not pd.isna(ds_range)
        and float(ds_range) <= FREEZE_TOLERANCE_C
        and reference_change >= FREEZE_REFERENCE_CHANGE_C
    )



def recent_sensor_df(history, max_rows=16):
    df = pd.DataFrame(list(history)).tail(max_rows).copy()

    needed = [
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "bmp280_temperature_c",
    ]

    for col in needed:
        if col not in df.columns:
            return pd.DataFrame()

        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=needed)

    return df


def spike_physics_gate(history):
    df = recent_sensor_df(history, max_rows=8)

    if len(df) < 2:
        return False

    current = df.iloc[-1]
    previous = df.iloc[-2]

    current_ref = np.nanmean([
        current["dht22_temperature_c"],
        current["bmp280_temperature_c"],
    ])

    previous_ref = np.nanmean([
        previous["dht22_temperature_c"],
        previous["bmp280_temperature_c"],
    ])

    current_ds = float(current["ds18b20_temperature_c"])
    previous_ds = float(previous["ds18b20_temperature_c"])

    current_bias = current_ds - current_ref
    previous_bias = previous_ds - previous_ref

    ds_jump = abs(current_ds - previous_ds)
    bias_jump = abs(current_bias - previous_bias)

    return (
        abs(current_bias) >= 2.0
        or ds_jump >= 1.5
        or bias_jump >= 1.2
    )


def drift_physics_gate(history):
    df = recent_sensor_df(history, max_rows=16)

    if len(df) < 8:
        return False

    ds = df["ds18b20_temperature_c"].to_numpy(dtype=float)
    dht = df["dht22_temperature_c"].to_numpy(dtype=float)
    bmp = df["bmp280_temperature_c"].to_numpy(dtype=float)

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



def has_high_conf_streak(state, label):
    recent = list(state.get("recent_predictions", []))

    if len(recent) < CONFIRM_WINDOWS:
        return False

    recent = recent[-CONFIRM_WINDOWS:]

    return all(
        pred == label and conf is not None and float(conf) >= CONFIRM_CONFIDENCE
        for pred, conf in recent
    )


def load_model():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Missing model: {MODEL_PATH}")

    package = torch.load(MODEL_PATH, map_location="cpu")

    model = SkyGuardTemporalCNN(
        n_features=len(package["feature_columns"]),
        n_classes=len(package["classes"]),
    )

    model.load_state_dict(package["model_state_dict"])
    model.eval()

    return model, package


def shadow_predict(node_id, history, state, model, package):
    seq_len = int(package["sequence_length"])
    classes = package["classes"]
    feature_cols = package["feature_columns"]

    current = history[-1]
    ds = current["ds18b20_temperature_c"]

    hard_fault = False
    hard_type = None

    if pd.isna(ds):
        hard_fault = True
        hard_type = "data_loss"
    elif float(ds) <= -100 or float(ds) >= 100:
        hard_fault = True
        hard_type = "corruption"

    if hard_fault:
        state["hard_fault_active"] = hard_type
        state["hard_recovery_count"] = 0
    elif state["hard_fault_active"] is not None:
        state["hard_recovery_count"] += 1
        if state["hard_recovery_count"] >= HARD_RECOVERY_REQUIRED:
            state["hard_fault_active"] = None
            state["hard_recovery_count"] = 0

    if len(history) < seq_len:
        return {
            "ready": False,
            "warming_up": True,
            "readings": len(history),
            "prediction": None,
            "confidence": None,
            "confirmed": bool(state["hard_fault_active"]),
            "confirmed_fault": state["hard_fault_active"],
            "hard_fault": hard_fault,
            "hard_fault_type": hard_type,
        }

    df = pd.DataFrame(list(history))
    df = add_features(df)

    x = df[feature_cols].tail(seq_len).to_numpy(dtype=np.float32)

    if np.isnan(x).any():
        col_means = np.nanmean(x, axis=0)
        col_means = np.where(np.isnan(col_means), 0.0, col_means)
        inds = np.where(np.isnan(x))
        x[inds] = np.take(col_means, inds[1])

    mean = np.asarray(package["mean"], dtype=np.float32)
    std = np.asarray(package["std"], dtype=np.float32)
    std = np.where(std < 1e-6, 1.0, std)

    x = (x - mean) / std

    xb = torch.tensor(x[None, :, :], dtype=torch.float32)

    with torch.no_grad():
        logits = model(xb)
        probs = torch.softmax(logits, dim=1).numpy()[0]

    pred_id = int(np.argmax(probs))
    prediction = classes[pred_id]
    confidence = float(probs[pred_id])

    if prediction == state["same_label"]:
        state["same_count"] += 1
    else:
        state["same_label"] = prediction
        state["same_count"] = 1

    state.setdefault("recent_predictions", deque(maxlen=CONFIRM_WINDOWS))
    state["recent_predictions"].append((prediction, confidence))

    confirmed = False
    confirmed_fault = None

    if hard_fault:
        confirmed = True
        confirmed_fault = hard_type

    elif state["hard_fault_active"] is not None:
        confirmed = True
        confirmed_fault = state["hard_fault_active"]

    elif prediction == "spike":
        if has_high_conf_streak(state, "spike") and spike_physics_gate(history):
            confirmed = True
            confirmed_fault = "spike"

    elif prediction == "drift":
        if has_high_conf_streak(state, "drift") and drift_physics_gate(history):
            confirmed = True
            confirmed_fault = "drift"

    elif prediction == "freeze":
        if has_high_conf_streak(state, "freeze"):
            if freeze_gate(pd.DataFrame(list(history))):
                confirmed = True
                confirmed_fault = "freeze"

    return {
        "ready": True,
        "warming_up": False,
        "readings": len(history),
        "prediction": prediction,
        "confidence": confidence,
        "confirmed": confirmed,
        "confirmed_fault": confirmed_fault,
        "hard_fault": hard_fault,
        "hard_fault_type": hard_type,
        "probabilities": {
            classes[i]: float(probs[i])
            for i in range(len(classes))
        },
    }


def main():
    model, package = load_model()

    print("=" * 80)
    print("SkyGuard V6.3 Shadow Live Test")
    print("=" * 80)
    print(f"Model: {MODEL_PATH.name}")
    print(f"Version: {package.get('model_version')}")
    print("Live backend routing is NOT changed.")
    print("Press Ctrl+C to stop early.")
    print("=" * 80)

    histories = {
        node: deque(maxlen=120)
        for node in NODES
    }

    states = {
        node: {
            "same_label": None,
            "same_count": 0,
            "hard_fault_active": None,
            "hard_recovery_count": 0,
        }
        for node in NODES
    }

    seen_ts = {node: set() for node in NODES}

    summary = {
        node: {
            "shadow_raw": Counter(),
            "shadow_confirmed": Counter(),
            "live_confirmed": Counter(),
            "agreements": 0,
            "comparisons": 0,
        }
        for node in NODES
    }

    fields = [
        "timestamp",
        "node_id",
        "live_model_version",
        "live_prediction",
        "live_confidence",
        "live_confirmed",
        "live_confirmed_fault",
        "shadow_model_version",
        "shadow_prediction",
        "shadow_confidence",
        "shadow_confirmed",
        "shadow_confirmed_fault",
        "agreement",
    ]

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        start = time.time()

        try:
            while time.time() - start < DURATION_SECONDS:
                for node in NODES:
                    try:
                        data = fetch_latest(node)
                        ts = data.get("timestamp")

                        if not ts or ts in seen_ts[node]:
                            continue

                        seen_ts[node].add(ts)

                        row = latest_to_row(node, data)
                        histories[node].append(row)

                        live_pt = (
                            data.get("ai_summary", {})
                            .get("pytorch", {})
                        )

                        shadow = shadow_predict(
                            node,
                            histories[node],
                            states[node],
                            model,
                            package,
                        )

                        live_prediction = live_pt.get("normalized_prediction") or live_pt.get("prediction")
                        live_confirmed = bool(live_pt.get("confirmed", False))
                        live_confirmed_fault = live_pt.get("confirmed_fault")

                        shadow_prediction = shadow.get("prediction")
                        shadow_confirmed = bool(shadow.get("confirmed", False))
                        shadow_confirmed_fault = shadow.get("confirmed_fault")

                        agreement = None
                        if live_prediction is not None and shadow_prediction is not None:
                            agreement = live_prediction == shadow_prediction
                            summary[node]["comparisons"] += 1
                            if agreement:
                                summary[node]["agreements"] += 1

                        if shadow_prediction:
                            summary[node]["shadow_raw"][shadow_prediction] += 1

                        if shadow_confirmed_fault:
                            summary[node]["shadow_confirmed"][shadow_confirmed_fault] += 1

                        if live_confirmed_fault:
                            summary[node]["live_confirmed"][live_confirmed_fault] += 1

                        writer.writerow({
                            "timestamp": ts,
                            "node_id": node,
                            "live_model_version": live_pt.get("model_version"),
                            "live_prediction": live_prediction,
                            "live_confidence": live_pt.get("confidence"),
                            "live_confirmed": live_confirmed,
                            "live_confirmed_fault": live_confirmed_fault,
                            "shadow_model_version": package.get("model_version"),
                            "shadow_prediction": shadow_prediction,
                            "shadow_confidence": shadow.get("confidence"),
                            "shadow_confirmed": shadow_confirmed,
                            "shadow_confirmed_fault": shadow_confirmed_fault,
                            "agreement": agreement,
                        })

                        live_conf = live_pt.get("confidence")
                        shadow_conf = shadow.get("confidence")

                        live_conf_txt = (
                            f"{float(live_conf):.3f}"
                            if live_conf is not None
                            else "-"
                        )

                        shadow_conf_txt = (
                            f"{float(shadow_conf):.3f}"
                            if shadow_conf is not None
                            else "-"
                        )

                        print(
                            f"{node} | "
                            f"live={live_pt.get('model_version')}:{live_prediction}:{live_conf_txt}:"
                            f"confirmed={live_confirmed_fault} | "
                            f"shadow=V6.3:{shadow_prediction}:{shadow_conf_txt}:"
                            f"confirmed={shadow_confirmed_fault} | "
                            f"agree={agreement}"
                        )

                    except Exception as exc:
                        print(node, "ERROR:", exc)

                time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("\nStopped by user.")

    print("\n" + "=" * 80)
    print("SHADOW LIVE SUMMARY")
    print("=" * 80)

    for node in NODES:
        comp = summary[node]["comparisons"]
        agree = summary[node]["agreements"]
        agreement_pct = (agree / comp * 100) if comp else 0.0

        print(f"\n{node}")
        print(f"comparisons: {comp}")
        print(f"agreement: {agree}/{comp} = {agreement_pct:.2f}%")
        print(f"shadow raw: {dict(summary[node]['shadow_raw'])}")
        print(f"shadow confirmed: {dict(summary[node]['shadow_confirmed'])}")
        print(f"live confirmed: {dict(summary[node]['live_confirmed'])}")

    print("\nSaved:")
    print(OUT_CSV)


if __name__ == "__main__":
    main()