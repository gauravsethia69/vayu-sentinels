from pathlib import Path
from collections import Counter, deque

import numpy as np
import pandas as pd
import torch
from torch import nn


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "app" / "models"

MODEL_PATH = MODEL_DIR / "skyguard_pytorch_multiclass_v6_1_candidate.pt"

SEQ_LABEL_NORMAL = "normal"
CONFIRM_CONFIDENCE = 0.70
CONFIRM_WINDOWS = 3

FREEZE_STUCK_SECONDS = 60.0
FREEZE_TOLERANCE_C = 0.03
FREEZE_REFERENCE_CHANGE_C = 0.10


CSV_CANDIDATES = {
    "AWS_002": [
        MODEL_DIR / "AWS_2.csv",
        MODEL_DIR / "AWS_2(2).csv",
        MODEL_DIR / "AWS_002.csv",
    ],
    "AWS_003": [
        MODEL_DIR / "AWS_3.csv",
        MODEL_DIR / "AWS_3(2).csv",
        MODEL_DIR / "AWS_003.csv",
    ],
}


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


def find_csv(node_id: str) -> Path:
    for path in CSV_CANDIDATES[node_id]:
        if path.exists():
            return path

    searched = "\n".join(str(p) for p in CSV_CANDIDATES[node_id])
    raise FileNotFoundError(f"No CSV found for {node_id}. Checked:\n{searched}")


def safe_float(value):
    if pd.isna(value):
        return np.nan
    try:
        return float(value)
    except Exception:
        return np.nan


def load_replay_csv(path: Path, node_id: str) -> pd.DataFrame:
    df = pd.read_csv(path)

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
        raise ValueError(f"{path.name} missing columns: {missing}")

    df = df[required].copy()
    df["source_file"] = path.name
    df["node_id"] = node_id
    df["condition"] = "normal"

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    numeric_cols = [
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        "bmp280_temperature_c",
        "bmp280_pressure_hpa",
    ]

    for col in numeric_cols:
        df[col] = df[col].apply(safe_float)

    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
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


def validate_node(node_id, model, package, device):
    csv_path = find_csv(node_id)
    raw_df = load_replay_csv(csv_path, node_id)

    hard_invalid = (
        raw_df["ds18b20_temperature_c"].isna()
        | (raw_df["ds18b20_temperature_c"] <= -100)
        | (raw_df["ds18b20_temperature_c"] >= 100)
    )

    skipped_hard_invalid = int(hard_invalid.sum())
    df = raw_df[~hard_invalid].copy().reset_index(drop=True)

    df = add_features(df)

    feature_cols = package["feature_columns"]
    seq_len = int(package["sequence_length"])
    classes = package["classes"]

    x, end_indices = create_windows(df, feature_cols, seq_len)

    mean = np.asarray(package["mean"], dtype=np.float32)
    std = np.asarray(package["std"], dtype=np.float32)
    std = np.where(std < 1e-6, 1.0, std)

    x = (x - mean) / std

    xb = torch.tensor(x, dtype=torch.float32).to(device)

    model.eval()
    with torch.no_grad():
        logits = model(xb)
        probs = torch.softmax(logits, dim=1).cpu().numpy()

    pred_ids = probs.argmax(axis=1)
    confs = probs.max(axis=1)

    raw_counts = Counter()
    raw_non_normal = []
    high_conf_non_normal = []
    confirmed_false = []

    same_label = None
    same_count = 0

    for i, pred_id in enumerate(pred_ids):
        label = classes[int(pred_id)]
        conf = float(confs[i])
        end_idx = end_indices[i]
        timestamp = df.loc[end_idx, "timestamp"]

        raw_counts[label] += 1

        if label != "normal":
            raw_non_normal.append((timestamp, label, conf))

        if label != "normal" and conf >= CONFIRM_CONFIDENCE:
            high_conf_non_normal.append((timestamp, label, conf))

        if label == same_label:
            same_count += 1
        else:
            same_label = label
            same_count = 1

        confirmed = False

        if label in ("spike", "drift") and conf >= CONFIRM_CONFIDENCE and same_count >= CONFIRM_WINDOWS:
            confirmed = True

        if label == "freeze" and conf >= CONFIRM_CONFIDENCE and same_count >= CONFIRM_WINDOWS:
            confirmed = freeze_gate(df, end_idx)

        # Corruption/data_loss are hard-rule faults in live backend.
        # We do not confirm them from raw model output alone.
        if confirmed:
            confirmed_false.append((timestamp, label, conf))

    total_windows = len(pred_ids)

    print("\n" + "=" * 80)
    print(f"{node_id} V6.1 NORMAL REPLAY")
    print("=" * 80)
    print(f"CSV: {csv_path.name}")
    print(f"Rows loaded: {len(raw_df)}")
    print(f"Rows skipped due hard invalid DS18B20: {skipped_hard_invalid}")
    print(f"Model windows: {total_windows}")

    print("\nRaw prediction counts:")
    for label in classes:
        print(f"{label}: {raw_counts.get(label, 0)}")

    print("\nRaw non-normal windows:")
    print(f"{len(raw_non_normal)} / {total_windows}")

    print("\nHigh-confidence non-normal windows:")
    print(f"{len(high_conf_non_normal)} / {total_windows}")

    print("\nConfirmed false faults:")
    print(f"{len(confirmed_false)} / {total_windows}")

    if confirmed_false:
        print("\nFirst confirmed false faults:")
        for item in confirmed_false[:10]:
            print(item)

    if len(confirmed_false) == 0:
        print("\nRESULT: PASS ✅")
    else:
        print("\nRESULT: FAIL ❌")


def main():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model missing: {MODEL_PATH}")

    package = torch.load(MODEL_PATH, map_location="cpu")

    device = torch.device("cpu")

    model = SkyGuardTemporalCNN(
        n_features=len(package["feature_columns"]),
        n_classes=len(package["classes"]),
    ).to(device)

    model.load_state_dict(package["model_state_dict"])

    print("=" * 80)
    print("SkyGuard V6.1 Candidate Normal Transfer Replay")
    print("=" * 80)
    print(f"Model: {MODEL_PATH.name}")
    print(f"Version: {package.get('model_version')}")

    validate_node("AWS_002", model, package, device)
    validate_node("AWS_003", model, package, device)


if __name__ == "__main__":
    main()