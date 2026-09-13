from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd
import torch
from torch import nn


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "app" / "models"

MODEL_PATH = MODEL_DIR / "skyguard_pytorch_multiclass_v6_1_candidate.pt"

CSV_CANDIDATES = [
    MODEL_DIR / "AWS_001_EXTRA_REAL_RUN_2026_09_13.csv",
    MODEL_DIR / "AWS_001_EVENT_VALIDATION.csv",
    BASE_DIR / "AWS_001_EXTRA_REAL_RUN_2026_09_13.csv",
]

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
            nn.Dropout(0.20),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(0.10),
            nn.Linear(32, n_classes),
        )

    def forward(self, x):
        x = x.transpose(1, 2)
        x = self.net(x)
        return self.head(x)


def find_csv():
    for path in CSV_CANDIDATES:
        if path.exists():
            return path

    checked = "\n".join(str(p) for p in CSV_CANDIDATES)
    raise FileNotFoundError(f"No AWS_001 event CSV found. Checked:\n{checked}")


def safe_float(value):
    if pd.isna(value):
        return np.nan
    try:
        return float(value)
    except Exception:
        return np.nan


def load_event_csv(path: Path):
    df = pd.read_csv(path)

    required = [
        "timestamp",
        "node_id",
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        "bmp280_temperature_c",
        "bmp280_pressure_hpa",
        "condition",
    ]

    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"{path.name} missing columns: {missing}")

    df = df[required].copy()
    df["source_file"] = path.name

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    numeric_cols = [
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        "bmp280_temperature_c",
        "bmp280_pressure_hpa",
    ]

    for col in numeric_cols:
        df[col] = df[col].apply(safe_float)

    df["condition"] = df["condition"].astype(str).str.lower().str.strip()

    return df


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

    group_cols = ["source_file", "node_id"]

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
                .reset_index(level=[0, 1], drop=True)
            )

            rolling_min = (
                df.groupby(group_cols)[col]
                .rolling(window, min_periods=2)
                .min()
                .reset_index(level=[0, 1], drop=True)
            )

            df[out] = (rolling_max - rolling_min).fillna(0.0)

    return df


def create_windows(df, feature_cols, seq_len):
    xs = []
    end_indices = []

    values = df[feature_cols].to_numpy(dtype=np.float32)

    for end in range(seq_len - 1, len(df)):
        start = end - seq_len + 1
        x = values[start:end + 1].copy()

        if np.isnan(x).any():
            col_means = np.nanmean(x, axis=0)
            col_means = np.where(np.isnan(col_means), 0.0, col_means)
            inds = np.where(np.isnan(x))
            x[inds] = np.take(col_means, inds[1])

        xs.append(x)
        end_indices.append(end)

    return np.asarray(xs, dtype=np.float32), end_indices


def freeze_gate(df, end_idx):
    end_time = df.loc[end_idx, "timestamp"]
    start_time = end_time - pd.Timedelta(seconds=FREEZE_STUCK_SECONDS)

    recent = df[
        (df["timestamp"] >= start_time)
        & (df["timestamp"] <= end_time)
    ]

    if len(recent) < 20:
        return False

    ds_range = recent["ds18b20_temperature_c"].max() - recent["ds18b20_temperature_c"].min()
    dht_range = recent["dht22_temperature_c"].max() - recent["dht22_temperature_c"].min()
    bmp_range = recent["bmp280_temperature_c"].max() - recent["bmp280_temperature_c"].min()

    reference_change = max(
        0.0 if pd.isna(dht_range) else float(dht_range),
        0.0 if pd.isna(bmp_range) else float(bmp_range),
    )

    return (
        not pd.isna(ds_range)
        and float(ds_range) <= FREEZE_TOLERANCE_C
        and reference_change >= FREEZE_REFERENCE_CHANGE_C
    )


def mark_event_groups(df):
    df = df.copy()

    event_ids = []
    event_id = 0
    prev_label = None

    for label in df["condition"].tolist():
        if label != prev_label:
            event_id += 1
        event_ids.append(event_id)
        prev_label = label

    df["event_id"] = event_ids
    return df


def main():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model missing: {MODEL_PATH}")

    csv_path = find_csv()
    package = torch.load(MODEL_PATH, map_location="cpu")

    classes = package["classes"]
    feature_cols = package["feature_columns"]
    seq_len = int(package["sequence_length"])

    device = torch.device("cpu")

    model = SkyGuardTemporalCNN(
        n_features=len(feature_cols),
        n_classes=len(classes),
    ).to(device)

    model.load_state_dict(package["model_state_dict"])
    model.eval()

    raw_df = load_event_csv(csv_path)
    raw_df = mark_event_groups(raw_df)
    df = add_features(raw_df)

    x, end_indices = create_windows(df, feature_cols, seq_len)

    mean = np.asarray(package["mean"], dtype=np.float32)
    std = np.asarray(package["std"], dtype=np.float32)
    std = np.where(std < 1e-6, 1.0, std)

    x = (x - mean) / std
    xb = torch.tensor(x, dtype=torch.float32).to(device)

    with torch.no_grad():
        logits = model(xb)
        probs = torch.softmax(logits, dim=1).cpu().numpy()

    pred_ids = probs.argmax(axis=1)
    confs = probs.max(axis=1)

    raw_counts = Counter()
    confirmed_counts = Counter()
    label_window_counts = Counter()

    same_label = None
    same_count = 0

    hard_fault_active = None
    hard_recovery_count = 0

    rows = []

    for i, pred_id in enumerate(pred_ids):
        end_idx = end_indices[i]
        row = df.loc[end_idx]

        actual = row["condition"]
        timestamp = row["timestamp"]
        label = classes[int(pred_id)]
        conf = float(confs[i])

        label_window_counts[actual] += 1
        raw_counts[label] += 1

        ds = row["ds18b20_temperature_c"]

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
        elif hard_fault_active is not None:
            hard_recovery_count += 1
            if hard_recovery_count >= HARD_RECOVERY_REQUIRED:
                hard_fault_active = None
                hard_recovery_count = 0

        if label == same_label:
            same_count += 1
        else:
            same_label = label
            same_count = 1

        confirmed = False
        confirmed_fault = None

        if hard_fault:
            confirmed = True
            confirmed_fault = hard_type

        elif hard_fault_active is not None:
            confirmed = True
            confirmed_fault = hard_fault_active

        elif label in ("spike", "drift"):
            if conf >= CONFIRM_CONFIDENCE and same_count >= CONFIRM_WINDOWS:
                confirmed = True
                confirmed_fault = label

        elif label == "freeze":
            if conf >= CONFIRM_CONFIDENCE and same_count >= CONFIRM_WINDOWS:
                if freeze_gate(df, end_idx):
                    confirmed = True
                    confirmed_fault = "freeze"

        if confirmed and confirmed_fault:
            confirmed_counts[confirmed_fault] += 1

        rows.append({
            "timestamp": timestamp,
            "actual": actual,
            "raw_prediction": label,
            "confidence": conf,
            "confirmed": confirmed,
            "confirmed_fault": confirmed_fault,
            "event_id": int(row["event_id"]),
        })

    result_df = pd.DataFrame(rows)

    print("=" * 80)
    print("SkyGuard V6.1 Candidate AWS_001 Event Replay")
    print("=" * 80)
    print(f"CSV: {csv_path.name}")
    print(f"Model: {MODEL_PATH.name}")
    print(f"Version: {package.get('model_version')}")
    print(f"Rows loaded: {len(raw_df)}")
    print(f"Model windows: {len(result_df)}")

    print("\nActual window labels:")
    for label, count in label_window_counts.items():
        print(f"{label}: {count}")

    print("\nRaw prediction counts:")
    for label in classes:
        print(f"{label}: {raw_counts.get(label, 0)}")

    print("\nConfirmed fault counts:")
    for label in classes:
        if label != "normal":
            print(f"{label}: {confirmed_counts.get(label, 0)}")

    print("\nEvent detection summary:")
    event_summaries = []

    for event_id, part in result_df.groupby("event_id"):
        actual = part["actual"].iloc[0]

        if actual == "normal":
            false_confirmed = part[
                (part["confirmed"] == True)
                & (part["confirmed_fault"].notna())
            ]

            event_summaries.append({
                "event_id": int(event_id),
                "actual": actual,
                "windows": len(part),
                "detected": len(false_confirmed) == 0,
                "confirmed_faults": sorted(set(false_confirmed["confirmed_fault"].dropna())),
            })

        else:
            hit = part[
                (part["confirmed"] == True)
                & (part["confirmed_fault"] == actual)
            ]

            event_summaries.append({
                "event_id": int(event_id),
                "actual": actual,
                "windows": len(part),
                "detected": len(hit) > 0,
                "confirmed_faults": sorted(set(part["confirmed_fault"].dropna())),
            })

    passed = True

    for item in event_summaries:
        status = "PASS" if item["detected"] else "FAIL"
        if not item["detected"]:
            passed = False

        print(
            f"event={item['event_id']} | "
            f"actual={item['actual']} | "
            f"windows={item['windows']} | "
            f"{status} | "
            f"confirmed={item['confirmed_faults']}"
        )

    print("\nFinal decision:")
    if passed:
        print("AWS_001 EVENT REPLAY: PASS ✅")
        print("V6.1 can move to shadow-live testing, not direct replacement yet.")
    else:
        print("AWS_001 EVENT REPLAY: FAIL ❌")
        print("Keep current live routing: AWS_001 V3, AWS_002/AWS_003 V5.")

    out_path = BASE_DIR / "v6_1_aws1_event_replay_output.csv"
    result_df.to_csv(out_path, index=False)
    print(f"\nSaved detailed replay output: {out_path}")


if __name__ == "__main__":
    main()