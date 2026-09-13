from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score, f1_score


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "app" / "models"

CSV_SOURCES = [
    {
        "path": MODEL_DIR / "AWS_001_LABELLED_CLEAN_V3.csv",
        "default_condition": None,
    },
    {
        "path": MODEL_DIR / "AWS_001_EXTRA_REAL_RUN_2026_09_13.csv",
        "default_condition": None,
    },
    {
        "path": MODEL_DIR / "AWS_2.csv",
        "default_condition": "normal",
        "force_node_id": "AWS_002",
    },
    {
        "path": MODEL_DIR / "AWS_3.csv",
        "default_condition": "normal",
        "force_node_id": "AWS_003",
    },
]

MODEL_OUT = MODEL_DIR / "skyguard_pytorch_multiclass_v6_3_multistation_recovery.pt"
META_OUT = MODEL_DIR / "skyguard_pytorch_multiclass_v6_3_multistation_recovery_metadata.json"

SEED = 42
SEQ_LEN = 12
BATCH_SIZE = 32
EPOCHS = 120
LR = 8e-4
WEIGHT_DECAY = 1e-4
PATIENCE = 22

LABELS = [
    "normal",
    "spike",
    "freeze",
    "drift",
    "corruption",
    "data_loss",
]

LABEL_TO_ID = {label: i for i, label in enumerate(LABELS)}
ID_TO_LABEL = {i: label for label, i in LABEL_TO_ID.items()}


def set_seed(seed=42):
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def safe_float(value):
    if pd.isna(value):
        return np.nan
    try:
        if value == "":
            return np.nan
        return float(value)
    except Exception:
        return np.nan


def load_one_source(source: dict) -> pd.DataFrame:
    path = Path(source["path"])

    if not path.exists():
        raise FileNotFoundError(f"CSV missing: {path}")

    df = pd.read_csv(path)

    required_base = [
        "timestamp",
        "node_id",
        "ds18b20_temperature_c",
        "dht22_temperature_c",
        "dht22_humidity_pct",
        "bmp280_temperature_c",
        "bmp280_pressure_hpa",
    ]

    missing = [col for col in required_base if col not in df.columns]
    if missing:
        raise ValueError(f"{path.name} missing columns: {missing}")

    keep_cols = required_base.copy()

    if "condition" in df.columns:
        keep_cols.append("condition")

    df = df[keep_cols].copy()

    if source.get("force_node_id"):
        df["node_id"] = source["force_node_id"]

    if "condition" not in df.columns:
        default_condition = source.get("default_condition")
        if default_condition is None:
            raise ValueError(f"{path.name} has no condition column.")
        df["condition"] = default_condition

    df["source_file"] = path.name

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])

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

    unknown = sorted(set(df["condition"]) - set(LABELS))
    if unknown:
        raise ValueError(f"{path.name} unknown labels: {unknown}")

    print(f"Loaded {path.name}: {len(df)} rows")
    return df


def load_data() -> pd.DataFrame:
    frames = []

    for source in CSV_SOURCES:
        frames.append(load_one_source(source))

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(
        ["source_file", "node_id", "timestamp"]
    ).reset_index(drop=True)

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


FEATURE_COLS = [
    "ds_clean",
    "dht_clean",
    "bmp_clean",
    "humidity_clean",
    "pressure_clean",
    "ds_missing",
    "ds_corrupt",
    "ds_dht_diff",
    "ds_bmp_diff",
    "dht_bmp_diff",
    "ds_clean_diff",
    "dht_clean_diff",
    "bmp_clean_diff",
    "humidity_clean_diff",
    "pressure_clean_diff",
    "ds_range_5",
    "dht_range_5",
    "bmp_range_5",
    "ds_range_12",
    "dht_range_12",
    "bmp_range_12",
]


def create_windows(df: pd.DataFrame):
    xs = []
    ys = []
    recovery_flags = []
    boundary_flags = []
    source_files = []
    node_ids = []

    for (source_file, node_id), part in df.groupby(["source_file", "node_id"]):
        part = part.reset_index(drop=True)

        values = part[FEATURE_COLS].to_numpy(dtype=np.float32)
        labels = part["condition"].tolist()

        for end in range(SEQ_LEN - 1, len(part)):
            start = end - SEQ_LEN + 1

            x = values[start:end + 1].copy()
            window_labels = labels[start:end + 1]
            end_label = labels[end]

            boundary = any(label != end_label for label in window_labels)

            recovery_normal = (
                end_label == "normal"
                and any(label != "normal" for label in window_labels[:-1])
            )

            if np.isnan(x).any():
                col_means = np.nanmean(x, axis=0)
                col_means = np.where(np.isnan(col_means), 0.0, col_means)
                inds = np.where(np.isnan(x))
                x[inds] = np.take(col_means, inds[1])

            xs.append(x)
            ys.append(LABEL_TO_ID[end_label])
            recovery_flags.append(bool(recovery_normal))
            boundary_flags.append(bool(boundary))
            source_files.append(source_file)
            node_ids.append(node_id)

    return (
        np.asarray(xs, dtype=np.float32),
        np.asarray(ys, dtype=np.int64),
        np.asarray(recovery_flags, dtype=bool),
        np.asarray(boundary_flags, dtype=bool),
        np.asarray(source_files),
        np.asarray(node_ids),
    )


class WindowDataset(Dataset):
    def __init__(self, x, y):
        self.x = torch.tensor(x, dtype=torch.float32)
        self.y = torch.tensor(y, dtype=torch.long)

    def __len__(self):
        return len(self.x)

    def __getitem__(self, idx):
        return self.x[idx], self.y[idx]


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


def stratified_split(x, y, recovery_flags, boundary_flags, source_files, node_ids):
    idx = np.arange(len(y))

    train_idx, temp_idx = train_test_split(
        idx,
        test_size=0.30,
        random_state=SEED,
        stratify=y,
    )

    val_idx, test_idx = train_test_split(
        temp_idx,
        test_size=0.50,
        random_state=SEED,
        stratify=y[temp_idx],
    )

    return (
        x[train_idx],
        y[train_idx],
        recovery_flags[train_idx],
        boundary_flags[train_idx],
        source_files[train_idx],
        node_ids[train_idx],

        x[val_idx],
        y[val_idx],
        recovery_flags[val_idx],
        boundary_flags[val_idx],
        source_files[val_idx],
        node_ids[val_idx],

        x[test_idx],
        y[test_idx],
        recovery_flags[test_idx],
        boundary_flags[test_idx],
        source_files[test_idx],
        node_ids[test_idx],
    )


def standardize(x_train, x_val, x_test):
    mean = x_train.reshape(-1, x_train.shape[-1]).mean(axis=0)
    std = x_train.reshape(-1, x_train.shape[-1]).std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)

    return (
        (x_train - mean) / std,
        (x_val - mean) / std,
        (x_test - mean) / std,
        mean,
        std,
    )


def build_sampler(y_train, recovery_train, boundary_train, source_train, node_train):
    counts = Counter(y_train.tolist())
    weights = []

    for label_id, is_recovery, is_boundary, source_file, node_id in zip(
        y_train,
        recovery_train,
        boundary_train,
        source_train,
        node_train,
    ):
        label_name = ID_TO_LABEL[int(label_id)]

        base = 1.0 / np.sqrt(max(1, counts[int(label_id)]))

        if label_name == "normal":
            multiplier = 1.25

            # Important: make AWS_002/AWS_003 normal data strong hard-negatives
            # against false freeze transfer.
            if node_id in ("AWS_002", "AWS_003"):
                multiplier *= 2.5

            if is_recovery:
                multiplier *= 3.5

        else:
            multiplier = {
                "spike": 1.25,
                "freeze": 1.15,
                "drift": 1.25,
                "corruption": 1.10,
                "data_loss": 1.10,
            }[label_name]

            if is_boundary:
                multiplier *= 0.75

        weights.append(float(base * multiplier))

    return WeightedRandomSampler(
        weights=torch.DoubleTensor(weights),
        num_samples=len(weights),
        replacement=True,
    )


def evaluate(model, loader, device):
    model.eval()
    true = []
    pred = []

    with torch.no_grad():
        for xb, yb in loader:
            xb = xb.to(device)
            logits = model(xb)
            preds = torch.argmax(logits, dim=1).cpu().numpy()

            pred.extend(preds.tolist())
            true.extend(yb.numpy().tolist())

    acc = accuracy_score(true, pred)
    macro_f1 = f1_score(
        true,
        pred,
        labels=list(range(len(LABELS))),
        average="macro",
        zero_division=0,
    )

    return acc, macro_f1, true, pred


def print_split_counts(name, y_values, node_values):
    print(name, {ID_TO_LABEL[int(k)]: int(v) for k, v in Counter(y_values.tolist()).items()})
    print(name + "_nodes", dict(Counter(node_values.tolist())))


def train():
    set_seed(SEED)

    print("=" * 80)
    print("SkyGuard PyTorch V6.3 Multi-Station Recovery-Aware Training")
    print("=" * 80)

    df = load_data()

    print("\nRaw label counts:")
    print(df["condition"].value_counts())

    print("\nRaw node counts:")
    print(df["node_id"].value_counts())

    df = add_features(df)

    (
        x,
        y,
        recovery_flags,
        boundary_flags,
        source_files,
        node_ids,
    ) = create_windows(df)

    print("\nWindow label counts:")
    for label_id, count in sorted(Counter(y.tolist()).items()):
        print(f"{ID_TO_LABEL[int(label_id)]}: {count}")

    print("\nWindow node counts:")
    print(dict(Counter(node_ids.tolist())))

    print("\nSpecial window counts:")
    print("recovery_normal_windows:", int(recovery_flags.sum()))
    print("boundary_windows:", int(boundary_flags.sum()))

    (
        x_train,
        y_train,
        recovery_train,
        boundary_train,
        source_train,
        node_train,

        x_val,
        y_val,
        recovery_val,
        boundary_val,
        source_val,
        node_val,

        x_test,
        y_test,
        recovery_test,
        boundary_test,
        source_test,
        node_test,
    ) = stratified_split(
        x,
        y,
        recovery_flags,
        boundary_flags,
        source_files,
        node_ids,
    )

    print("\nSplit window counts:")
    print_split_counts("train", y_train, node_train)
    print_split_counts("val", y_val, node_val)
    print_split_counts("test", y_test, node_test)

    print("\nRecovery windows in split:")
    print("train:", int(recovery_train.sum()))
    print("val:  ", int(recovery_val.sum()))
    print("test: ", int(recovery_test.sum()))

    x_train, x_val, x_test, mean, std = standardize(
        x_train,
        x_val,
        x_test,
    )

    train_ds = WindowDataset(x_train, y_train)
    val_ds = WindowDataset(x_val, y_val)
    test_ds = WindowDataset(x_test, y_test)

    train_loader = DataLoader(
        train_ds,
        batch_size=BATCH_SIZE,
        sampler=build_sampler(
            y_train,
            recovery_train,
            boundary_train,
            source_train,
            node_train,
        ),
    )

    val_loader = DataLoader(
        val_ds,
        batch_size=BATCH_SIZE,
        shuffle=False,
    )

    test_loader = DataLoader(
        test_ds,
        batch_size=BATCH_SIZE,
        shuffle=False,
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\nDevice: {device}")

    model = SkyGuardTemporalCNN(
        n_features=len(FEATURE_COLS),
        n_classes=len(LABELS),
    ).to(device)

    class_counts = Counter(y_train.tolist())
    weights = []

    for i in range(len(LABELS)):
        count = max(1, class_counts.get(i, 1))
        weight = np.sqrt(len(y_train) / count)
        weight = min(weight, 2.0)
        weights.append(weight)

    class_weights = torch.tensor(weights, dtype=torch.float32)
    class_weights = class_weights / class_weights.mean()
    class_weights = class_weights.to(device)

    criterion = nn.CrossEntropyLoss(weight=class_weights)

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=LR,
        weight_decay=WEIGHT_DECAY,
    )

    best_val_f1 = -1.0
    best_state = None
    bad_epochs = 0

    for epoch in range(1, EPOCHS + 1):
        model.train()
        total_loss = 0.0

        for xb, yb in train_loader:
            xb = xb.to(device)
            yb = yb.to(device)

            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()

            total_loss += loss.item() * len(xb)

        train_loss = total_loss / max(1, len(train_ds))

        val_acc, val_f1, _, _ = evaluate(
            model,
            val_loader,
            device,
        )

        print(
            f"Epoch {epoch:03d} | "
            f"loss={train_loss:.4f} | "
            f"val_acc={val_acc:.4f} | "
            f"val_macro_f1={val_f1:.4f}"
        )

        if val_f1 > best_val_f1:
            best_val_f1 = val_f1
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }
            bad_epochs = 0
        else:
            bad_epochs += 1

        if bad_epochs >= PATIENCE:
            print(f"\nEarly stopping at epoch {epoch}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    test_acc, test_macro_f1, test_true, test_pred = evaluate(
        model,
        test_loader,
        device,
    )

    print("\n" + "=" * 80)
    print("TEST RESULTS")
    print("=" * 80)
    print(f"Accuracy: {test_acc:.4f}")
    print(f"Macro-F1: {test_macro_f1:.4f}")

    print(
        classification_report(
            test_true,
            test_pred,
            labels=list(range(len(LABELS))),
            target_names=LABELS,
            zero_division=0,
        )
    )

    package = {
        "model_state_dict": model.state_dict(),
        "classes": LABELS,
        "label_to_id": LABEL_TO_ID,
        "id_to_label": ID_TO_LABEL,
        "feature_columns": FEATURE_COLS,
        "sequence_length": SEQ_LEN,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "architecture": "SkyGuardTemporalCNN",
        "model_version": "V6_3_MULTISTATION_RECOVERY_AWARE",
        "note": (
            "Multi-station recovery-aware candidate. Includes AWS_002/AWS_003 "
            "normal hard-negative data to reduce cross-station false freeze. "
            "PyTorch gives raw temporal predictions only; final freeze "
            "confirmation must use live semantic gate."
        ),
        "freeze_gate_recommendation": {
            "min_stuck_seconds": 60.0,
            "ds_stuck_tolerance_c": 0.03,
            "reference_change_min_c": 0.10,
            "min_model_confidence": 0.70,
            "confirmation_windows": 3,
        },
        "deployment_warning": (
            "Do not replace V3/V5 until normal replay, AWS_001 event replay, "
            "and shadow-live testing pass."
        ),
    }

    torch.save(package, MODEL_OUT)

    metadata = {
        "model": MODEL_OUT.name,
        "csv_sources": [
            {
                "path": str(source["path"]),
                "default_condition": source.get("default_condition"),
                "force_node_id": source.get("force_node_id"),
            }
            for source in CSV_SOURCES
        ],
        "labels": LABELS,
        "feature_columns": FEATURE_COLS,
        "sequence_length": SEQ_LEN,
        "test_accuracy": test_acc,
        "test_macro_f1": test_macro_f1,
        "best_val_macro_f1": best_val_f1,
        "raw_label_counts": df["condition"].value_counts().to_dict(),
        "raw_node_counts": df["node_id"].value_counts().to_dict(),
        "window_label_counts": {
            ID_TO_LABEL[int(k)]: int(v)
            for k, v in Counter(y.tolist()).items()
        },
        "window_node_counts": dict(Counter(node_ids.tolist())),
        "special_window_counts": {
            "recovery_normal_windows": int(recovery_flags.sum()),
            "boundary_windows": int(boundary_flags.sum()),
        },
        "split_counts": {
            "train": {
                ID_TO_LABEL[int(k)]: int(v)
                for k, v in Counter(y_train.tolist()).items()
            },
            "val": {
                ID_TO_LABEL[int(k)]: int(v)
                for k, v in Counter(y_val.tolist()).items()
            },
            "test": {
                ID_TO_LABEL[int(k)]: int(v)
                for k, v in Counter(y_test.tolist()).items()
            },
        },
        "split_node_counts": {
            "train": dict(Counter(node_train.tolist())),
            "val": dict(Counter(node_val.tolist())),
            "test": dict(Counter(node_test.tolist())),
        },
        "recovery_windows_in_split": {
            "train": int(recovery_train.sum()),
            "val": int(recovery_val.sum()),
            "test": int(recovery_test.sum()),
        },
        "warning": (
            "Candidate only. Must pass replay and shadow-live before deployment."
        ),
    }

    META_OUT.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    print("\nSaved model:")
    print(MODEL_OUT)

    print("\nSaved metadata:")
    print(META_OUT)


if __name__ == "__main__":
    train()