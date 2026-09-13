from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd
import torch
from torch import nn


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "app" / "models"

MODEL_PATH = MODEL_DIR / "skyguard_pytorch_multiclass_v6_3_multistation_recovery.pt"
CSV_PATH = MODEL_DIR / "AWS_001_EXTRA_REAL_RUN_2026_09_13.csv"

CONFIRM_CONFIDENCE = 0.70
CONFIRM_WINDOWS = 3

HARD_RECOVERY_REQUIRED = 3
NORMAL_RECOVERY_GRACE_ROWS = 3

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
        return self.head(self.net(x))


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
    df["ds_corrupt"] = (ds.notna() & ((ds <= -100) | (ds >= 100))).astype(float)

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
        df[f"{col}_diff"] = df.groupby(group_cols)[col].diff().fillna(0.0)

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


def predict_window(model, package, window):
    feature_cols = package["feature_columns"]

    x = window[feature_cols].to_numpy(dtype=np.float32)

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

    return label, confidence, {package["classes"][i]: float(probs[i]) for i in range(len(package["classes"]))}


def freeze_gate(df, end_idx, recent_freeze_probs):
    current_time = df.loc[end_idx, "timestamp"]

    if pd.isna(current_time):
        return False, "no_timestamp"

    recent_60 = df[
        (df["timestamp"] >= current_time - pd.Timedelta(seconds=FREEZE_STUCK_SECONDS))
        & (df["timestamp"] <= current_time)
    ]

    recent_75 = df[
        (df["timestamp"] >= current_time - pd.Timedelta(seconds=FREEZE_LONG_STUCK_SECONDS))
        & (df["timestamp"] <= current_time)
    ]

    if len(recent_60) >= 20:
        ds_range = recent_60["ds18b20_temperature_c"].max() - recent_60["ds18b20_temperature_c"].min()
        dht_range = recent_60["dht22_temperature_c"].max() - recent_60["dht22_temperature_c"].min()
        bmp_range = recent_60["bmp280_temperature_c"].max() - recent_60["bmp280_temperature_c"].min()

        ref_change = max(
            0.0 if pd.isna(dht_range) else float(dht_range),
            0.0 if pd.isna(bmp_range) else float(bmp_range),
        )

        if not pd.isna(ds_range):
            if float(ds_range) <= FREEZE_TOLERANCE_C and ref_change >= FREEZE_REFERENCE_CHANGE_C:
                return True, "reference_supported"

    if len(recent_75) >= 25:
        ds_range_long = recent_75["ds18b20_temperature_c"].max() - recent_75["ds18b20_temperature_c"].min()

        if len(recent_freeze_probs) >= FREEZE_LONG_REQUIRED_WINDOWS:
            avg_freeze_prob = float(np.mean(recent_freeze_probs[-FREEZE_LONG_REQUIRED_WINDOWS:]))

            if (
                not pd.isna(ds_range_long)
                and float(ds_range_long) <= FREEZE_TOLERANCE_C
                and avg_freeze_prob >= FREEZE_LONG_AVG_PROBABILITY
            ):
                return True, "long_duration_model_supported"

    return False, "blocked"


def build_events(df):
    events = []
    start = 0

    labels = df["condition"].tolist()

    for i in range(1, len(labels)):
        if labels[i] != labels[i - 1]:
            events.append((start, i - 1, labels[i - 1]))
            start = i

    events.append((start, len(labels) - 1, labels[-1]))
    return events


def main():
    print("=" * 80)
    print("SkyGuard V6.3 AWS_001 Event Replay Validation")
    print("=" * 80)

    if not MODEL_PATH.exists():
        raise FileNotFoundError(MODEL_PATH)

    if not CSV_PATH.exists():
        raise FileNotFoundError(CSV_PATH)

    model, package = load_model()

    print("Model:", MODEL_PATH.name)
    print("Version:", package.get("model_version"))

    df = pd.read_csv(CSV_PATH)

    if "node_id" not in df.columns:
        df["node_id"] = "AWS_001"

    required = [
        "timestamp",
        "node_id",
        "condition",
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        "bmp280_temperature_c",
        "bmp280_pressure_hpa",
    ]

    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    df = df[required].copy()

    df["condition"] = df["condition"].astype(str).str.lower().str.strip()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    for col in [
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        "bmp280_temperature_c",
        "bmp280_pressure_hpa",
    ]:
        df[col] = df[col].apply(safe_float)

    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = add_features(df)

    seq_len = int(package["sequence_length"])

    raw_counts = Counter()
    confirmed_counts = Counter()
    actual_counts = Counter(df["condition"].tolist())

    same_label = None
    same_count = 0

    hard_fault_active = None
    hard_recovery_count = 0

    row_results = []
    recent_freeze_probs = []

    for end in range(len(df)):
        actual = df.loc[end, "condition"]
        ds = df.loc[end, "ds18b20_temperature_c"]

        raw_label = None
        confidence = None
        confirmed_fault = None
        freeze_path = None

        if pd.isna(ds):
            confirmed_fault = "data_loss"
            hard_fault_active = "data_loss"
            hard_recovery_count = 0

        elif float(ds) <= -100 or float(ds) >= 100:
            confirmed_fault = "corruption"
            hard_fault_active = "corruption"
            hard_recovery_count = 0

        else:
            if hard_fault_active is not None:
                hard_recovery_count += 1

                if hard_recovery_count < HARD_RECOVERY_REQUIRED:
                    confirmed_fault = hard_fault_active
                else:
                    hard_fault_active = None
                    hard_recovery_count = 0

            if end >= seq_len - 1:
                window = df.iloc[end - seq_len + 1:end + 1]
                raw_label, confidence, probs = predict_window(model, package, window)

                raw_counts[raw_label] += 1
                recent_freeze_probs.append(probs.get("freeze", 0.0))

                if raw_label == same_label:
                    same_count += 1
                else:
                    same_label = raw_label
                    same_count = 1

                if confirmed_fault is None:
                    if raw_label in ("spike", "drift"):
                        if confidence >= CONFIRM_CONFIDENCE and same_count >= CONFIRM_WINDOWS:
                            confirmed_fault = raw_label

                    elif raw_label == "freeze":
                        gate_ok, freeze_path = freeze_gate(df, end, recent_freeze_probs)

                        if confidence >= CONFIRM_CONFIDENCE and same_count >= CONFIRM_WINDOWS and gate_ok:
                            confirmed_fault = "freeze"

        if confirmed_fault is not None:
            confirmed_counts[confirmed_fault] += 1

        row_results.append({
            "idx": end,
            "timestamp": df.loc[end, "timestamp"],
            "actual": actual,
            "raw": raw_label,
            "confidence": confidence,
            "confirmed_fault": confirmed_fault,
            "freeze_path": freeze_path,
        })

    print("\nActual row labels:")
    print(dict(actual_counts))

    print("\nRaw prediction counts:")
    print(dict(raw_counts))

    print("\nConfirmed fault counts:")
    print(dict(confirmed_counts))

    events = build_events(df)

    print("\n" + "=" * 80)
    print("EVENT DETECTION SUMMARY")
    print("=" * 80)

    all_pass = True

    for event_no, (start, end, label) in enumerate(events, start=1):
        rows = [r for r in row_results if start <= r["idx"] <= end]
        confirmed_inside = [r["confirmed_fault"] for r in rows if r["confirmed_fault"] is not None]

        passed = True
        reason = ""

        if label == "normal":
            checked_rows = rows[NORMAL_RECOVERY_GRACE_ROWS:]
            bad = [r["confirmed_fault"] for r in checked_rows if r["confirmed_fault"] is not None]

            if bad:
                passed = False
                reason = f"false confirmed after grace: {bad[:5]}"
            else:
                reason = "normal clear"

        else:
            expected = label
            if label == "data_loss":
                expected = "data_loss"
            elif label == "corruption":
                expected = "corruption"

            if expected not in confirmed_inside:
                passed = False
                reason = f"missing confirmed {expected}"
            else:
                reason = f"confirmed {expected}"

        if not passed:
            all_pass = False

        print(
            f"event {event_no:02d} | rows {start:03d}-{end:03d} | "
            f"actual={label:10s} | "
            f"{'PASS ✅' if passed else 'FAIL ❌'} | {reason}"
        )

    print("\n" + "=" * 80)
    print("FINAL RESULT")
    print("=" * 80)

    if all_pass:
        print("AWS_001 EVENT REPLAY: PASS ✅")
    else:
        print("AWS_001 EVENT REPLAY: CHECK / FAIL ⚠️")

    print("\nDecision:")
    print("Do NOT deploy V6.3 until this replay and shadow-live both pass.")


if __name__ == "__main__":
    main()