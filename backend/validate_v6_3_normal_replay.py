from pathlib import Path
from collections import Counter, deque

import numpy as np
import pandas as pd
import torch
from torch import nn


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "app" / "models"

MODEL_PATH = MODEL_DIR / "skyguard_pytorch_multiclass_v6_3_multistation_recovery.pt"

NORMAL_FILES = {
    "AWS_002": MODEL_DIR / "AWS_2.csv",
    "AWS_003": MODEL_DIR / "AWS_3.csv",
}

CONFIRM_CONFIDENCE = 0.70
CONFIRM_WINDOWS = 3
HARD_RECOVERY_REQUIRED = 3


class SkyGuardTemporalCNN(nn.Module):
    def __init__(self, n_features: int, n_classes: int):
        super().__init__()

        self.net = nn.Sequential(
            nn.Conv1d(n_features, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),

            nn.Conv1d(n_features=32, out_channels=48, kernel_size=3, padding=1)
            if False else nn.Conv1d(32, 48, kernel_size=3, padding=1),
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
    if pd.isna(value):
        return np.nan
    try:
        if value == "":
            return np.nan
        return float(value)
    except Exception:
        return np.nan


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


def load_model():
    package = torch.load(MODEL_PATH, map_location="cpu")

    model = SkyGuardTemporalCNN(
        n_features=len(package["feature_columns"]),
        n_classes=len(package["classes"]),
    )

    model.load_state_dict(package["model_state_dict"])
    model.eval()

    return model, package


def predict_window(model, package, x):
    feature_cols = package["feature_columns"]

    x = x[feature_cols].to_numpy(dtype=np.float32)

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
    label = package["classes"][pred_id]
    confidence = float(probs[pred_id])

    return label, confidence


def replay_node(node_id, csv_path, model, package):
    print("\n" + "=" * 80)
    print(f"V6.3 NORMAL REPLAY: {node_id}")
    print("=" * 80)

    df = pd.read_csv(csv_path)

    df["node_id"] = node_id

    required = [
        "timestamp",
        "node_id",
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        "bmp280_temperature_c",
        "bmp280_pressure_hpa",
    ]

    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"{csv_path.name} missing columns: {missing}")

    df = df[required].copy()

    for col in [
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        "bmp280_temperature_c",
        "bmp280_pressure_hpa",
    ]:
        df[col] = df[col].apply(safe_float)

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    df = add_features(df)

    seq_len = int(package["sequence_length"])

    raw_counts = Counter()
    high_conf_non_normal = Counter()
    confirmed_false_faults = Counter()

    same_label = None
    same_count = 0
    hard_fault_active = None
    hard_recovery_count = 0

    model_windows = 0

    for end in range(seq_len - 1, len(df)):
        current = df.iloc[end]
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
            hard_fault_active = hard_type
            hard_recovery_count = 0
            confirmed_false_faults[hard_type] += 1
            continue

        if hard_fault_active is not None:
            hard_recovery_count += 1
            if hard_recovery_count >= HARD_RECOVERY_REQUIRED:
                hard_fault_active = None
                hard_recovery_count = 0
            else:
                confirmed_false_faults[hard_fault_active] += 1
                continue

        window = df.iloc[end - seq_len + 1:end + 1]
        label, confidence = predict_window(model, package, window)

        model_windows += 1
        raw_counts[label] += 1

        if label == same_label:
            same_count += 1
        else:
            same_label = label
            same_count = 1

        if label != "normal" and confidence >= CONFIRM_CONFIDENCE:
            high_conf_non_normal[label] += 1

        if label in ("spike", "drift") and confidence >= CONFIRM_CONFIDENCE and same_count >= CONFIRM_WINDOWS:
            confirmed_false_faults[label] += 1

        # Freeze confirmation is intentionally stricter in production.
        # For normal replay, we still count repeated high-confidence freeze as risky.
        if label == "freeze" and confidence >= CONFIRM_CONFIDENCE and same_count >= CONFIRM_WINDOWS:
            confirmed_false_faults["freeze_risk_without_semantic_gate"] += 1

    print(f"Rows loaded: {len(df)}")
    print(f"Model windows: {model_windows}")
    print(f"Raw prediction counts: {dict(raw_counts)}")
    print(f"High-confidence non-normal: {dict(high_conf_non_normal)}")
    print(f"Confirmed false faults / risks: {dict(confirmed_false_faults)}")

    passed = (
        confirmed_false_faults == {}
        and sum(high_conf_non_normal.values()) <= 5
    )

    print("RESULT:", "PASS ✅" if passed else "CHECK ⚠️")

    return {
        "node_id": node_id,
        "rows": len(df),
        "model_windows": model_windows,
        "raw_counts": dict(raw_counts),
        "high_conf_non_normal": dict(high_conf_non_normal),
        "confirmed_false_faults": dict(confirmed_false_faults),
        "passed": passed,
    }


def main():
    model, package = load_model()

    print("=" * 80)
    print("SkyGuard V6.3 Normal Replay Validation")
    print("=" * 80)
    print("Model:", MODEL_PATH.name)
    print("Version:", package.get("model_version"))

    results = []

    for node_id, csv_path in NORMAL_FILES.items():
        results.append(
            replay_node(
                node_id=node_id,
                csv_path=csv_path,
                model=model,
                package=package,
            )
        )

    print("\n" + "=" * 80)
    print("FINAL NORMAL REPLAY SUMMARY")
    print("=" * 80)

    for result in results:
        print(
            result["node_id"],
            "PASS ✅" if result["passed"] else "CHECK ⚠️",
            "| raw:",
            result["raw_counts"],
            "| high-conf:",
            result["high_conf_non_normal"],
            "| confirmed/risk:",
            result["confirmed_false_faults"],
        )


if __name__ == "__main__":
    main()