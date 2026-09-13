from __future__ import annotations

import json
from pathlib import Path
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score, f1_score


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "app" / "models"

CSV_PATHS = [
    MODEL_DIR / "AWS_001_LABELLED_CLEAN_V3.csv",
    MODEL_DIR / "AWS_001_EXTRA_REAL_RUN_2026_09_13.csv",
]

MODEL_OUT = MODEL_DIR / "skyguard_pytorch_multiclass_v6_robust.pt"
META_OUT = MODEL_DIR / "skyguard_pytorch_multiclass_v6_robust_metadata.json"

SEED = 42
SEQ_LEN = 12
BATCH_SIZE = 32
EPOCHS = 120
LR = 1e-3
WEIGHT_DECAY = 1e-4
PATIENCE = 20

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


def set_seed(seed: int = 42):
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def safe_float(value):
    if pd.isna(value):
        return np.nan
    try:
        return float(value)
    except Exception:
        return np.nan


def load_one_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"CSV missing: {path}")

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
        raise ValueError(f"{path.name} has unknown labels: {unknown}")

    return df


def load_data() -> pd.DataFrame:
    frames = []

    for path in CSV_PATHS:
        one = load_one_csv(path)
        frames.append(one)
        print(f"Loaded {path.name}: {len(one)} rows")

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["source_file", "node_id", "timestamp"]).reset_index(drop=True)

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


def make_event_groups(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    group_ids = []
    group_id = 0

    prev_source = None
    prev_node = None
    prev_label = None

    for _, row in df.iterrows():
        source = row["source_file"]
        node = row["node_id"]
        label = row["condition"]

        if source != prev_source or node != prev_node or label != prev_label:
            group_id += 1

        group_ids.append(group_id)

        prev_source = source
        prev_node = node
        prev_label = label

    df["event_group"] = group_ids
    return df


def create_windows(df: pd.DataFrame):
    xs = []
    ys = []
    groups = []

    for _, part in df.groupby(["source_file", "node_id"]):
        part = part.reset_index(drop=True)

        values = part[FEATURE_COLS].to_numpy(dtype=np.float32)
        labels = part["condition"].tolist()
        event_groups = part["event_group"].tolist()

        for end in range(SEQ_LEN - 1, len(part)):
            start = end - SEQ_LEN + 1

            x = values[start:end + 1].copy()
            label = labels[end]
            group = event_groups[end]

            if np.isnan(x).any():
                col_means = np.nanmean(x, axis=0)
                col_means = np.where(np.isnan(col_means), 0.0, col_means)
                inds = np.where(np.isnan(x))
                x[inds] = np.take(col_means, inds[1])

            xs.append(x)
            ys.append(LABEL_TO_ID[label])
            groups.append(group)

    return (
        np.asarray(xs, dtype=np.float32),
        np.asarray(ys, dtype=np.int64),
        np.asarray(groups, dtype=np.int64),
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


def split_by_event_group(x, y, groups):
    unique_groups = np.unique(groups)

    group_to_label = {}
    for group in unique_groups:
        labels = y[groups == group]
        group_to_label[group] = Counter(labels.tolist()).most_common(1)[0][0]

    label_to_groups = defaultdict(list)
    for group, label in group_to_label.items():
        label_to_groups[label].append(group)

    train_groups = []
    remaining_groups = []

    for label_id, group_list in label_to_groups.items():
        group_list = list(group_list)
        np.random.shuffle(group_list)

        # Always keep at least one group of each class in train where possible.
        train_groups.append(group_list[0])
        remaining_groups.extend(group_list[1:])

    if len(remaining_groups) < 4:
        # Very small dataset fallback.
        extra_train, temp_groups = train_test_split(
            remaining_groups,
            test_size=0.50,
            random_state=SEED,
        ) if len(remaining_groups) >= 2 else (remaining_groups, [])
    else:
        extra_train, temp_groups = train_test_split(
            remaining_groups,
            test_size=0.30,
            random_state=SEED,
        )

    train_groups = np.asarray(list(train_groups) + list(extra_train))
    temp_groups = np.asarray(temp_groups)

    if len(temp_groups) >= 2:
        val_groups, test_groups = train_test_split(
            temp_groups,
            test_size=0.50,
            random_state=SEED,
        )
    else:
        val_groups = temp_groups
        test_groups = temp_groups

    train_mask = np.isin(groups, train_groups)
    val_mask = np.isin(groups, val_groups)
    test_mask = np.isin(groups, test_groups)

    return (
        x[train_mask],
        y[train_mask],
        x[val_mask],
        y[val_mask],
        x[test_mask],
        y[test_mask],
    )


def standardize(x_train, x_val, x_test):
    mean = x_train.reshape(-1, x_train.shape[-1]).mean(axis=0)
    std = x_train.reshape(-1, x_train.shape[-1]).std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)

    return (
        (x_train - mean) / std,
        (x_val - mean) / std if len(x_val) else x_val,
        (x_test - mean) / std if len(x_test) else x_test,
        mean,
        std,
    )


def build_sampler(y_train):
    counts = Counter(y_train.tolist())
    weights = []

    for label_id in y_train:
        label_name = ID_TO_LABEL[int(label_id)]
        base = 1.0 / max(1, counts[int(label_id)])

        multiplier = {
            "normal": 1.0,
            "spike": 2.0,
            "freeze": 2.0,
            "drift": 2.5,
            "corruption": 3.0,
            "data_loss": 3.0,
        }[label_name]

        weights.append(base * multiplier)

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

    if not true:
        return 0.0, 0.0, true, pred

    acc = accuracy_score(true, pred)
    macro_f1 = f1_score(
        true,
        pred,
        labels=list(range(len(LABELS))),
        average="macro",
        zero_division=0,
    )

    return acc, macro_f1, true, pred


def train():
    set_seed(SEED)

    print("=" * 80)
    print("SkyGuard PyTorch V6 Robust Training")
    print("=" * 80)

    df = load_data()

    print("\nRaw label counts:")
    print(df["condition"].value_counts())

    df = add_features(df)
    df = make_event_groups(df)

    x, y, groups = create_windows(df)

    print("\nWindow label counts:")
    for label_id, count in sorted(Counter(y.tolist()).items()):
        print(f"{ID_TO_LABEL[label_id]}: {count}")

    x_train, y_train, x_val, y_val, x_test, y_test = split_by_event_group(
        x,
        y,
        groups,
    )

    print("\nSplit window counts:")
    print("train:", Counter(y_train.tolist()))
    print("val:  ", Counter(y_val.tolist()))
    print("test: ", Counter(y_test.tolist()))

    if len(x_train) < 30:
        raise ValueError("Training split too small. Add more data.")

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
        sampler=build_sampler(y_train),
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
    class_weights = []

    for i in range(len(LABELS)):
        class_weights.append(
            len(y_train) / max(1, class_counts.get(i, 1))
        )

    class_weights = torch.tensor(class_weights, dtype=torch.float32)
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

        val_acc, val_f1, _, _ = evaluate(model, val_loader, device)

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
        "model_version": "V6_ROBUST",
        "note": (
            "PyTorch gives raw temporal predictions only. "
            "Final freeze confirmation must use the live semantic gate. "
            "Do not confirm freeze from only 5 repeated readings."
        ),
        "freeze_gate_recommendation": {
            "min_stuck_seconds": 60.0,
            "ds_stuck_tolerance_c": 0.03,
            "reference_change_min_c": 0.10,
            "min_model_confidence": 0.70,
            "confirmation_windows": 3,
        },
    }

    torch.save(package, MODEL_OUT)

    metadata = {
        "model": MODEL_OUT.name,
        "csv_files": [path.name for path in CSV_PATHS],
        "labels": LABELS,
        "feature_columns": FEATURE_COLS,
        "sequence_length": SEQ_LEN,
        "test_accuracy": test_acc,
        "test_macro_f1": test_macro_f1,
        "best_val_macro_f1": best_val_f1,
        "raw_label_counts": df["condition"].value_counts().to_dict(),
        "window_label_counts": {
            ID_TO_LABEL[int(k)]: int(v)
            for k, v in Counter(y.tolist()).items()
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
        "warning": (
            "Use this as a candidate model only. Compare against V3/V5 live "
            "behavior before replacing the deployed routing."
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