import random
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)


# ============================================================
# CONFIG
# ============================================================

SEED = 42

SEQ_LEN = 12

EPOCHS = 70

BATCH_SIZE = 64

LEARNING_RATE = 0.001

WEIGHT_DECAY = 1e-4


BASE_DIR = Path(__file__).parent

AWS1_PATH = (
    BASE_DIR
    / "app"
    / "models"
    / "AWS_001_LABELLED_CLEAN_V3.csv"
)

AWS2_PATH = (
    BASE_DIR
    / "AWS_2.csv"
)

AWS3_PATH = (
    BASE_DIR
    / "AWS_3.csv"
)

OUTPUT_MODEL = (
    BASE_DIR
    / "app"
    / "models"
    / "skyguard_pytorch_multiclass_v5.pt"
)


CLASSES = [
    "normal",
    "spike",
    "freeze",
    "drift",
    "corruption",
    "data_loss",
]


SENSOR_COLS = [
    "ds18b20_temperature_c",
    "dht22_temperature_c",
    "dht22_humidity_pct",
    "bmp280_temperature_c",
    "bmp280_pressure_hpa",
]


# ============================================================
# REPRODUCIBILITY
# ============================================================

random.seed(SEED)

np.random.seed(SEED)

torch.manual_seed(SEED)


# ============================================================
# COLUMN HELPERS
# ============================================================

COLUMN_ALIASES = {

    "ds18b20_temperature_c": [
        "ds18b20_temperature_c",
        "ds18b20",
        "ds18b20_temp",
        "DS18B20",
    ],

    "dht22_temperature_c": [
        "dht22_temperature_c",
        "dht22_temp",
        "temperature_dht22",
        "DHT22_T",
    ],

    "dht22_humidity_pct": [
        "dht22_humidity_pct",
        "humidity",
        "dht22_humidity",
        "DHT22_H",
    ],

    "bmp280_temperature_c": [
        "bmp280_temperature_c",
        "bmp280_temp",
        "bmp180_temperature_c",
        "bmp180_temp",
        "BMP280_T",
        "BMP180_T",
    ],

    "bmp280_pressure_hpa": [
        "bmp280_pressure_hpa",
        "bmp180_pressure_hpa",
        "pressure_hpa",
        "pressure",
    ],
}


def find_column(df, aliases):

    for name in aliases:

        if name in df.columns:
            return name

    return None


def standardize_columns(df):

    result = pd.DataFrame()

    for target in SENSOR_COLS:

        source = find_column(
            df,
            COLUMN_ALIASES[target]
        )

        if source is None:

            raise ValueError(
                f"Could not find column for {target}.\n"
                f"Available columns:\n{df.columns.tolist()}"
            )

        result[target] = pd.to_numeric(
            df[source],
            errors="coerce"
        )

    return result


# ============================================================
# LOAD AWS_001
# ============================================================

print()
print("=" * 70)
print("LOADING AWS_001 LABELLED DATA")
print("=" * 70)

aws1_raw = pd.read_csv(
    AWS1_PATH
)

aws1_sensors = standardize_columns(
    aws1_raw
)

aws1 = aws1_sensors.copy()

aws1["condition"] = (
    aws1_raw["condition"]
    .astype(str)
    .str.strip()
    .str.lower()
)

aws1["node_id"] = "AWS_001"


print(
    aws1["condition"].value_counts()
)


# ============================================================
# LOAD AWS_002 + AWS_003 AS NORMAL HARD NEGATIVES
# ============================================================

def load_normal_station(path, node_id):

    raw = pd.read_csv(
        path
    )

    data = standardize_columns(
        raw
    )

    data["condition"] = "normal"

    data["node_id"] = node_id

    # --------------------------------------------------------
    # REMOVE PHYSICALLY INVALID NORMAL ROWS
    # --------------------------------------------------------

    ds = data[
        "ds18b20_temperature_c"
    ]

    corruption_mask = (
        ds.notna()
        &
        (
            (ds <= -100)
            |
            (ds >= 100)
        )
    )

    removed = int(
        corruption_mask.sum()
    )

    data = data.loc[
        ~corruption_mask
    ].reset_index(
        drop=True
    )

    print()
    print(
        f"{node_id}: "
        f"{len(data)} normal rows loaded"
    )

    print(
        f"{node_id}: "
        f"{removed} corrupt rows removed"
    )

    return data


aws2 = load_normal_station(
    AWS2_PATH,
    "AWS_002"
)

aws3 = load_normal_station(
    AWS3_PATH,
    "AWS_003"
)


# ============================================================
# COMBINE DATA
# ============================================================

all_data = pd.concat(
    [
        aws1,
        aws2,
        aws3,
    ],
    ignore_index=True
)


all_data = all_data[
    all_data["condition"].isin(
        CLASSES
    )
].reset_index(
    drop=True
)


print()
print("=" * 70)
print("COMBINED ROW COUNTS")
print("=" * 70)

print(
    all_data["condition"].value_counts()
)

print()

print(
    all_data.groupby(
        [
            "node_id",
            "condition",
        ]
    ).size()
)


# ============================================================
# CREATE CONTIGUOUS GROUPS
#
# Prevent train/test leakage between neighbouring windows.
#
# A new group starts when:
# - node changes
# - condition changes
# - group becomes too long
# ============================================================

GROUP_MAX_ROWS = 40


groups = []

group_id = 0

last_node = None

last_condition = None

rows_in_group = 0


for _, row in all_data.iterrows():

    node = row["node_id"]

    condition = row["condition"]

    new_group = False

    if node != last_node:
        new_group = True

    elif condition != last_condition:
        new_group = True

    elif rows_in_group >= GROUP_MAX_ROWS:
        new_group = True

    if new_group:

        group_id += 1

        rows_in_group = 0

    groups.append(
        group_id
    )

    rows_in_group += 1

    last_node = node

    last_condition = condition


all_data["group_id"] = groups


# ============================================================
# SPLIT GROUPS BY CLASS
#
# 70% train
# 15% validation
# 15% test
# ============================================================

group_table = (

    all_data
    .groupby("group_id")
    .agg(
        condition=("condition", "first"),
        node_id=("node_id", "first"),
        rows=("condition", "size"),
    )
    .reset_index()
)


train_groups = []

val_groups = []

test_groups = []


rng = random.Random(
    SEED
)


for condition in CLASSES:

    class_groups = (
        group_table[
            group_table["condition"]
            ==
            condition
        ]["group_id"]
        .tolist()
    )

    rng.shuffle(
        class_groups
    )

    n = len(
        class_groups
    )

    if n == 1:

        train_groups.extend(
            class_groups
        )

        continue

    if n == 2:

        train_groups.append(
            class_groups[0]
        )

        test_groups.append(
            class_groups[1]
        )

        continue

    n_test = max(
        1,
        round(
            n * 0.15
        )
    )

    n_val = max(
        1,
        round(
            n * 0.15
        )
    )

    if (
        n_test
        +
        n_val
        >= n
    ):

        n_test = 1
        n_val = 1

    test_groups.extend(
        class_groups[
            :n_test
        ]
    )

    val_groups.extend(
        class_groups[
            n_test:
            n_test + n_val
        ]
    )

    train_groups.extend(
        class_groups[
            n_test + n_val:
        ]
    )


train_df = all_data[
    all_data["group_id"].isin(
        train_groups
    )
].copy()

val_df = all_data[
    all_data["group_id"].isin(
        val_groups
    )
].copy()

test_df = all_data[
    all_data["group_id"].isin(
        test_groups
    )
].copy()


print()
print("=" * 70)
print("SPLIT ROW COUNTS")
print("=" * 70)

print(
    "TRAIN:",
    len(train_df)
)

print(
    train_df[
        "condition"
    ].value_counts()
)

print()

print(
    "VALIDATION:",
    len(val_df)
)

print(
    val_df[
        "condition"
    ].value_counts()
)

print()

print(
    "TEST:",
    len(test_df)
)

print(
    test_df[
        "condition"
    ].value_counts()
)


# ============================================================
# TRAINING MEDIANS
# ============================================================

medians = {}

for column in SENSOR_COLS:

    value = train_df[
        column
    ].median(
        skipna=True
    )

    medians[column] = float(
        value
    )


print()
print("Training medians:")

print(
    medians
)


# ============================================================
# RAW TRAINING MATRIX FOR SCALER
# ============================================================

train_raw = train_df[
    SENSOR_COLS
].to_numpy(
    dtype=np.float32
)


median_array = np.array(
    [
        medians[c]
        for c in SENSOR_COLS
    ],
    dtype=np.float32
)


missing_mask = np.isnan(
    train_raw
)


train_filled = train_raw.copy()


for i in range(
    len(SENSOR_COLS)
):

    train_filled[
        missing_mask[:, i],
        i
    ] = median_array[i]


scaler_mean = train_filled.mean(
    axis=0
).astype(
    np.float32
)


scaler_scale = train_filled.std(
    axis=0
).astype(
    np.float32
)


scaler_scale = np.where(
    scaler_scale < 1e-6,
    1.0,
    scaler_scale
).astype(
    np.float32
)


# ============================================================
# FEATURE FUNCTION
# ============================================================

def make_base_features(df):

    raw = df[
        SENSOR_COLS
    ].to_numpy(
        dtype=np.float32
    )

    ds_missing = np.isnan(
        raw[:, 0]
    ).astype(
        np.float32
    )

    filled = raw.copy()

    for i in range(
        len(SENSOR_COLS)
    ):

        mask = np.isnan(
            filled[:, i]
        )

        filled[
            mask,
            i
        ] = median_array[i]

    z = (
        filled
        -
        scaler_mean
    ) / scaler_scale

    ds_dht = (
        filled[:, 0]
        -
        filled[:, 1]
    ) / 3.0

    ds_bmp = (
        filled[:, 0]
        -
        filled[:, 3]
    ) / 3.0

    base = np.column_stack(
        [
            z,
            ds_missing,
            ds_dht,
            ds_bmp,
        ]
    ).astype(
        np.float32
    )

    return base


# ============================================================
# WINDOW CREATION
#
# Windows never cross group boundaries.
# ============================================================

CLASS_TO_INDEX = {
    name: i
    for i, name
    in enumerate(CLASSES)
}


def build_windows(df):

    X = []

    y = []

    metadata = []

    for group_id, group in df.groupby(
        "group_id",
        sort=False
    ):

        group = group.reset_index(
            drop=True
        )

        if len(group) < SEQ_LEN:
            continue

        base = make_base_features(
            group
        )

        condition = group.loc[
            0,
            "condition"
        ]

        node_id = group.loc[
            0,
            "node_id"
        ]

        # --------------------------------------------------------
        # Sliding windows
        # --------------------------------------------------------

        for start in range(
            0,
            len(group) - SEQ_LEN + 1
        ):

            window = base[
                start:
                start + SEQ_LEN
            ].copy()

            # ----------------------------------------------------
            # SAME LIVE-INFERENCE TRANSFORMS AS V3
            # ----------------------------------------------------

            window[:, :5] = (
                window[:, :5]
                -
                window[0, :5]
            )

            window[:, 6:8] = (
                window[:, 6:8]
                -
                window[0, 6:8]
            )

            dif = np.zeros(
                (
                    SEQ_LEN,
                    5
                ),
                dtype=np.float32
            )

            dif[1:] = (
                window[1:, :5]
                -
                window[:-1, :5]
            )

            stuck = np.zeros(
                (
                    SEQ_LEN,
                    1
                ),
                dtype=np.float32
            )

            stuck[1:, 0] = (
                np.abs(
                    dif[1:, 0]
                )
                <
                1e-8
            ).astype(
                np.float32
            )

            final_features = np.hstack(
                [
                    window,
                    dif,
                    stuck,
                ]
            ).astype(
                np.float32
            )

            X.append(
                final_features
            )

            y.append(
                CLASS_TO_INDEX[
                    condition
                ]
            )

            metadata.append(
                (
                    node_id,
                    condition,
                    group_id,
                )
            )

    return (
        np.asarray(
            X,
            dtype=np.float32
        ),
        np.asarray(
            y,
            dtype=np.int64
        ),
        metadata,
    )


X_train, y_train, meta_train = (
    build_windows(
        train_df
    )
)

X_val, y_val, meta_val = (
    build_windows(
        val_df
    )
)

X_test, y_test, meta_test = (
    build_windows(
        test_df
    )
)


print()
print("=" * 70)
print("WINDOW COUNTS")
print("=" * 70)

print(
    "Train:",
    len(X_train)
)

print(
    "Validation:",
    len(X_val)
)

print(
    "Test:",
    len(X_test)
)


N_FEATURES = (
    X_train.shape[2]
)


print(
    "Features per reading:",
    N_FEATURES
)


# ============================================================
# CLASS WEIGHTS
# ============================================================

counts = np.bincount(
    y_train,
    minlength=len(CLASSES)
).astype(
    np.float32
)


weights = (
    counts.sum()
    /
    (
        len(CLASSES)
        *
        np.maximum(
            counts,
            1
        )
    )
)


class_weights = torch.tensor(
    weights,
    dtype=torch.float32
)


print()
print("Training window counts by class:")

for i, name in enumerate(
    CLASSES
):

    print(
        f"{name:12}: "
        f"{int(counts[i])}"
    )


print()

print(
    "Class weights:",
    weights
)


# ============================================================
# TORCH DATA
# ============================================================

# ============================================================
# V5 TRAINING SAMPLER
# Preserve cross-station normal learning while strengthening
# genuine AWS_001 freeze examples.
# ============================================================

train_tensor_x = torch.from_numpy(
    X_train
)

train_tensor_y = torch.from_numpy(
    y_train
)

train_dataset = (
    torch.utils.data.TensorDataset(
        train_tensor_x,
        train_tensor_y,
    )
)


FREEZE_INDEX = CLASS_TO_INDEX[
    "freeze"
]


sample_weights = np.ones(
    len(y_train),
    dtype=np.float64
)


# Stronger emphasis on genuine freeze windows
sample_weights[
    y_train == FREEZE_INDEX
] = 3.0


sampler = (
    torch.utils.data.WeightedRandomSampler(
        weights=torch.tensor(
            sample_weights,
            dtype=torch.double
        ),
        num_samples=len(
            sample_weights
        ),
        replacement=True
    )
)


train_loader = (
    torch.utils.data.DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE,
        sampler=sampler,
    )
)


 

# ============================================================
# MODEL
# ============================================================

class SkyGuardTemporalCNN(nn.Module):

    def __init__(
        self,
        n_features,
        n_classes
    ):

        super().__init__()

        self.net = nn.Sequential(

            nn.Conv1d(
                n_features,
                32,
                kernel_size=3,
                padding=1
            ),

            nn.ReLU(),

            nn.BatchNorm1d(
                32
            ),

            nn.Conv1d(
                32,
                48,
                kernel_size=3,
                padding=1
            ),

            nn.ReLU(),

            nn.BatchNorm1d(
                48
            ),

            nn.Conv1d(
                48,
                64,
                kernel_size=3,
                padding=1
            ),

            nn.ReLU(),

            nn.AdaptiveAvgPool1d(
                1
            ),
        )

        self.head = nn.Sequential(

            nn.Flatten(),

            nn.Dropout(
                0.20
            ),

            nn.Linear(
                64,
                32
            ),

            nn.ReLU(),

            nn.Dropout(
                0.10
            ),

            nn.Linear(
                32,
                n_classes
            ),
        )

    def forward(
        self,
        x
    ):

        x = x.transpose(
            1,
            2
        )

        x = self.net(
            x
        )

        return self.head(
            x
        )


model = SkyGuardTemporalCNN(
    N_FEATURES,
    len(CLASSES)
)


criterion = nn.CrossEntropyLoss(
    weight=class_weights
)


optimizer = torch.optim.AdamW(
    model.parameters(),
    lr=LEARNING_RATE,
    weight_decay=WEIGHT_DECAY
)


# ============================================================
# EVALUATION
# ============================================================

def evaluate(
    X,
    y
):

    model.eval()

    with torch.no_grad():

        logits = model(
            torch.from_numpy(
                X
            )
        )

        pred = torch.argmax(
            logits,
            dim=1
        ).cpu().numpy()

    accuracy = accuracy_score(
        y,
        pred
    )

    macro_f1 = f1_score(
        y,
        pred,
        average="macro",
        zero_division=0
    )

    return (
        accuracy,
        macro_f1,
        pred,
    )


# ============================================================
# TRAIN
# ============================================================

best_val_f1 = -1.0

best_state = None

patience = 12

bad_epochs = 0


print()
print("=" * 70)
print("TRAINING PYTORCH V5")
print("=" * 70)


for epoch in range(
    1,
    EPOCHS + 1
):

    model.train()

    total_loss = 0.0

    for xb, yb in train_loader:

        optimizer.zero_grad()

        logits = model(
            xb
        )

        loss = criterion(
            logits,
            yb
        )

        loss.backward()

        optimizer.step()

        total_loss += (
            loss.item()
            *
            len(xb)
        )

    train_loss = (
        total_loss
        /
        len(train_dataset)
    )

    val_acc, val_f1, _ = evaluate(
        X_val,
        y_val
    )

    print(
        f"Epoch {epoch:02d} | "
        f"loss={train_loss:.4f} | "
        f"val_acc={val_acc * 100:.2f}% | "
        f"val_macro_f1={val_f1 * 100:.2f}%"
    )

    if val_f1 > best_val_f1:

        best_val_f1 = val_f1

        best_state = {
            k:
                v.detach()
                .cpu()
                .clone()

            for k, v
            in model.state_dict().items()
        }

        bad_epochs = 0

    else:

        bad_epochs += 1

    if bad_epochs >= patience:

        print()
        print(
            "Early stopping."
        )

        break


# ============================================================
# RESTORE BEST MODEL
# ============================================================

model.load_state_dict(
    best_state
)


# ============================================================
# TEST
# ============================================================

test_acc, test_f1, test_pred = evaluate(
    X_test,
    y_test
)


print()
print("=" * 70)
print("V5 HELD-OUT TEST RESULTS")
print("=" * 70)

print(
    f"Accuracy: "
    f"{test_acc * 100:.2f}%"
)

print(
    f"Macro-F1: "
    f"{test_f1 * 100:.2f}%"
)


print()
print(
    classification_report(
        y_test,
        test_pred,
        labels=list(
            range(
                len(CLASSES)
            )
        ),
        target_names=CLASSES,
        digits=4,
        zero_division=0
    )
)


print(
    "Confusion matrix:"
)

print(
    confusion_matrix(
        y_test,
        test_pred,
        labels=list(
            range(
                len(CLASSES)
            )
        )
    )
)


# ============================================================
# PER-NODE NORMAL FALSE POSITIVE CHECK
# ============================================================

print()
print("=" * 70)
print("NORMAL TRANSFER CHECK")
print("=" * 70)


meta_test_array = np.array(
    meta_test,
    dtype=object
)


for node in [
    "AWS_001",
    "AWS_002",
    "AWS_003",
]:

    indexes = [
        i

        for i, item
        in enumerate(
            meta_test
        )

        if (
            item[0] == node
            and
            item[1] == "normal"
        )
    ]

    if not indexes:

        print(
            f"{node}: "
            "no held-out normal windows"
        )

        continue

    true = y_test[
        indexes
    ]

    pred = test_pred[
        indexes
    ]

    normal_index = (
        CLASS_TO_INDEX[
            "normal"
        ]
    )

    false_faults = int(
        np.sum(
            pred
            !=
            normal_index
        )
    )

    total = len(
        indexes
    )

    rate = (
        false_faults
        /
        total
        *
        100
    )

    print(
        f"{node}: "
        f"{false_faults}/{total} "
        f"raw false-fault windows "
        f"({rate:.2f}%)"
    )


# ============================================================
# SAVE V4 CHECKPOINT
# ============================================================

checkpoint = {

    "version":
        "SkyGuard PyTorch Multiclass V5",

    "classes":
        CLASSES,

    "sensor_columns":
        SENSOR_COLS,

    "seq_len":
        SEQ_LEN,

    "n_features":
        N_FEATURES,

    "medians":
        medians,

    "scaler_mean":
        scaler_mean.tolist(),

    "scaler_scale":
        scaler_scale.tolist(),

    "model_state_dict":
        model.state_dict(),

    "recommended_min_confidence":
        0.70,

    "confirmation_windows":
        3,

    "test_accuracy":
        float(
            test_acc
        ),

    "test_macro_f1":
        float(
            test_f1
        ),

    "best_validation_macro_f1":
        float(
            best_val_f1
        ),

    "training_notes": (
        "V5 multi-station training. "
        "AWS_001 provides six labelled classes. "
        "AWS_002 and AWS_003 provide normal hard-negative "
        "AWS_001 freeze windows receive 3x weighted sampling "
        "to preserve genuine freeze sensitivity."),
}


torch.save(
    checkpoint,
    OUTPUT_MODEL
)


print()
print("=" * 70)
print("V5 SAVED")
print("=" * 70)

print(
    OUTPUT_MODEL
)

print()

print(
    "IMPORTANT: V3 WAS NOT MODIFIED."
)