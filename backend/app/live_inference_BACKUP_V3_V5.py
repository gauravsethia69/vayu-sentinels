import time
from collections import deque

import numpy as np
import torch
import torch.nn as nn


# ============================================================
# SKYGUARD PYTORCH V3 TEMPORAL MODEL
# ============================================================


class SkyGuardTemporalCNN(nn.Module):
    def __init__(self, n_features, n_classes):
        super().__init__()

        self.net = nn.Sequential(
            nn.Conv1d(
                n_features,
                32,
                kernel_size=3,
                padding=1
            ),
            nn.ReLU(),
            nn.BatchNorm1d(32),

            nn.Conv1d(
                32,
                48,
                kernel_size=3,
                padding=1
            ),
            nn.ReLU(),
            nn.BatchNorm1d(48),

            nn.Conv1d(
                48,
                64,
                kernel_size=3,
                padding=1
            ),
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

        # Input:
        # [batch, time, features]
        #
        # Conv1D expects:
        # [batch, features, time]

        x = x.transpose(1, 2)

        x = self.net(x)

        return self.head(x)


# ============================================================
# LIVE CLASSIFIER
# ============================================================


class SkyGuardLiveClassifier:
    """
    SkyGuard PyTorch V3 live temporal classifier.

    HARD FAULTS
    -----------
    data_loss
    corruption

    Deterministic safety rules.

    AI FAULTS
    ---------
    spike
    freeze
    drift

    Spike and drift:
        confidence + temporal persistence

    Freeze:
        confidence + persistence +
        real DS18B20 stuck-duration validation

    Freeze has two confirmation paths:

    PATH A
    ------
    DS stuck >= 60 seconds
    + reference temperature movement >= 0.10 C
    + PyTorch freeze >= 0.70
    + >= 3 consecutive freeze predictions

    PATH B
    ------
    Used when ambient conditions are extremely stable.

    DS stuck >= 75 seconds
    + >= 8 consecutive freeze predictions
    + average recent freeze probability >= 0.60

    This avoids making reference movement mandatory.
    """

    AI_FAULTS = {
        "spike",
        "freeze",
        "drift",
    }

    HARD_FAULTS = {
        "data_loss",
        "corruption",
    }

    def __init__(self, model_path):

        # ========================================================
        # LOAD CHECKPOINT
        # ========================================================

        ckpt = torch.load(
            model_path,
            map_location="cpu",
            weights_only=False
        )

        self.classes = ckpt["classes"]

        self.sensor_cols = ckpt["sensor_columns"]

        self.seq_len = int(
            ckpt["seq_len"]
        )

        # ========================================================
        # TRAINING PREPROCESSING
        # ========================================================

        self.medians = np.array(
            [
                ckpt["medians"][c]
                for c in self.sensor_cols
            ],
            dtype=np.float32
        )

        self.mean = np.array(
            ckpt["scaler_mean"],
            dtype=np.float32
        )

        self.scale = np.array(
            ckpt["scaler_scale"],
            dtype=np.float32
        )

        # Protect against zero scaler variance.
        self.scale = np.where(
            np.abs(self.scale) < 1e-12,
            1.0,
            self.scale
        ).astype(np.float32)

        # ========================================================
        # GENERAL AI CONFIRMATION
        # ========================================================

        self.min_conf = float(
            ckpt.get(
                "recommended_min_confidence",
                0.70
            )
        )

        self.confirmation_windows = int(
            ckpt.get(
                "confirmation_windows",
                3
            )
        )

        self.ai_normal_recovery_required = 3

        # ========================================================
        # LOW-CONFIDENCE DECAY
        # ========================================================

        self.ai_low_confidence_threshold = 0.55

        self.ai_low_confidence_clear_windows = 5

        # ========================================================
        # HARD-FAULT RECOVERY
        # ========================================================

        self.hard_recovery_required = 3

        # ========================================================
        # FREEZE CONFIRMATION SETTINGS
        # ========================================================

        # --------------------------------------------------------
        # Standard freeze confidence
        # --------------------------------------------------------

        self.freeze_min_confidence = 0.70

        self.freeze_confirmation_windows = 3

        # --------------------------------------------------------
        # DS must remain effectively unchanged.
        #
        # DS18B20 resolution naturally produces repeated values,
        # therefore 45 seconds proved too aggressive.
        # --------------------------------------------------------

        self.ds_stuck_min_seconds = 60.0

        self.ds_stuck_tolerance_c = 0.03

        # --------------------------------------------------------
        # Reference-supported path
        # --------------------------------------------------------

        self.reference_change_min_c = 0.10

        # --------------------------------------------------------
        # Long-duration fallback path
        #
        # Allows a real freeze to be confirmed even if DHT/BMP
        # remain nearly stationary because ambient conditions are
        # stable.
        # --------------------------------------------------------

        self.freeze_long_stuck_seconds = 75.0

        self.freeze_long_required_windows = 8

        self.freeze_long_avg_probability = 0.60

        # Keep enough history for all freeze checks.
        self.freeze_history_seconds = 150.0

        # ========================================================
        # MODEL
        # ========================================================

        self.model = SkyGuardTemporalCNN(
            ckpt["n_features"],
            len(self.classes)
        )

        self.model.load_state_dict(
            ckpt["model_state_dict"]
        )

        self.model.eval()

        # ========================================================
        # MODEL TEMPORAL BUFFER
        # ========================================================

        self.rows = deque(
            maxlen=self.seq_len
        )

        # ========================================================
        # PHYSICAL SENSOR HISTORY
        # ========================================================

        self.physical_history = deque()

        # ========================================================
        # RECENT FREEZE MODEL EVIDENCE
        # ========================================================

        # Stores recent PyTorch freeze probabilities.
        self.freeze_probability_history = deque(
            maxlen=20
        )

        # ========================================================
        # RAW MODEL PERSISTENCE
        # ========================================================

        self.last_pred = None

        self.same_pred_count = 0

        # ========================================================
        # AI FAULT STATE
        # ========================================================

        self.ai_confirmed_fault = None

        self.ai_normal_recovery_count = 0

        self.ai_low_confidence_count = 0

        # ========================================================
        # HARD FAULT STATE
        # ========================================================

        self.hard_confirmed_fault = None

        self.hard_recovery_count = 0

    # ============================================================
    # SAFE SENSOR PARSING
    # ============================================================

    @staticmethod
    def _safe_float(value):

        if value is None:
            return None

        try:
            value = float(value)

        except (
            TypeError,
            ValueError
        ):
            return None

        if not np.isfinite(value):
            return None

        return value

    # ============================================================
    # HARD FAULT DETECTION
    # ============================================================

    def _detect_hard_fault(self, reading):
        """
        Deterministic physical safety layer.

        Missing DS18B20:
            data_loss

        Invalid / impossible DS18B20:
            corruption
        """

        raw_ds = reading.get(
            "ds18b20_temperature_c"
        )

        # --------------------------------------------------------
        # DATA LOSS
        # --------------------------------------------------------

        if raw_ds is None:
            return "data_loss"

        # --------------------------------------------------------
        # CORRUPTION
        # --------------------------------------------------------

        try:
            ds = float(raw_ds)

        except (
            TypeError,
            ValueError
        ):
            return "corruption"

        if not np.isfinite(ds):
            return "corruption"

        if ds <= -100 or ds >= 100:
            return "corruption"

        return None

    # ============================================================
    # HARD FAULT STATE MACHINE
    # ============================================================

    def _update_hard_fault_state(
        self,
        current_hard_fault
    ):

        hard_recovered = False

        # ========================================================
        # HARD FAULT PRESENT
        # ========================================================

        if current_hard_fault is not None:

            if (
                self.hard_confirmed_fault
                != current_hard_fault
            ):

                # Hard fault overrides any AI state.
                self.ai_confirmed_fault = None

                self.ai_normal_recovery_count = 0

                self.ai_low_confidence_count = 0

                self.last_pred = None

                self.same_pred_count = 0

                self.freeze_probability_history.clear()

            self.hard_confirmed_fault = (
                current_hard_fault
            )

            self.hard_recovery_count = 0

            return False

        # ========================================================
        # HARD FAULT RECOVERY
        # ========================================================

        if self.hard_confirmed_fault is not None:

            self.hard_recovery_count += 1

            if (
                self.hard_recovery_count
                >= self.hard_recovery_required
            ):

                self.hard_confirmed_fault = None

                self.hard_recovery_count = 0

                # Remove contaminated temporal state.
                self.rows.clear()

                self.physical_history.clear()

                self.freeze_probability_history.clear()

                self.last_pred = None

                self.same_pred_count = 0

                self.ai_confirmed_fault = None

                self.ai_normal_recovery_count = 0

                self.ai_low_confidence_count = 0

                hard_recovered = True

        else:

            self.hard_recovery_count = 0

        return hard_recovered

    # ============================================================
    # PHYSICAL SENSOR HISTORY
    # ============================================================

    def _update_physical_history(
        self,
        reading
    ):

        now = time.monotonic()

        ds = self._safe_float(
            reading.get(
                "ds18b20_temperature_c"
            )
        )

        dht = self._safe_float(
            reading.get(
                "dht22_temperature_c"
            )
        )

        bmp = self._safe_float(
            reading.get(
                "bmp280_temperature_c"
            )
        )

        self.physical_history.append(
            {
                "time": now,
                "ds": ds,
                "dht": dht,
                "bmp": bmp,
            }
        )

        cutoff = (
            now
            -
            self.freeze_history_seconds
        )

        while (
            self.physical_history
            and
            self.physical_history[0]["time"]
            <
            cutoff
        ):

            self.physical_history.popleft()

    # ============================================================
    # PHYSICAL FREEZE ANALYSIS
    # ============================================================

    def _analyze_freeze_physics(self):
        """
        Analyse DS18B20 and reference sensors.

        This method does NOT itself decide whether the model has
        enough confidence.

        It only describes the physical evidence.
        """

        diagnostics = {
            "freeze_gate_passed": False,

            "freeze_path": None,

            "ds_stuck_seconds": 0.0,

            "ds_range_c": None,

            "ds_is_stuck": False,

            "dht_range_c": None,

            "bmp_range_c": None,

            "reference_change_c": 0.0,

            "reference_support": False,

            "long_stuck_support": False,

            "recent_freeze_average_probability": 0.0,

            "recent_freeze_probability_samples": 0,
        }

        if len(self.physical_history) < 2:

            return diagnostics

        now = self.physical_history[-1]["time"]

        # ========================================================
        # STANDARD 60-SECOND WINDOW
        # ========================================================

        standard_start = (
            now
            -
            self.ds_stuck_min_seconds
        )

        standard_recent = [
            item

            for item
            in self.physical_history

            if item["time"]
            >= standard_start
        ]

        if len(standard_recent) < 2:

            return diagnostics

        actual_duration = (
            standard_recent[-1]["time"]
            -
            standard_recent[0]["time"]
        )

        diagnostics[
            "ds_stuck_seconds"
        ] = float(
            actual_duration
        )

        # Allow small scheduling jitter.
        standard_duration_ready = (
            actual_duration
            >=
            (
                self.ds_stuck_min_seconds
                -
                3.0
            )
        )

        # ========================================================
        # DS RANGE
        # ========================================================

        ds_values = [
            item["ds"]

            for item
            in standard_recent

            if item["ds"] is not None
        ]

        if len(ds_values) < 2:

            return diagnostics

        ds_range = float(
            max(ds_values)
            -
            min(ds_values)
        )

        diagnostics[
            "ds_range_c"
        ] = ds_range

        ds_is_stuck = (
            standard_duration_ready
            and
            ds_range
            <=
            self.ds_stuck_tolerance_c
        )

        diagnostics[
            "ds_is_stuck"
        ] = bool(
            ds_is_stuck
        )

        if not ds_is_stuck:

            return diagnostics

        # ========================================================
        # REFERENCE SENSOR MOVEMENT
        # ========================================================

        dht_values = [
            item["dht"]

            for item
            in standard_recent

            if item["dht"] is not None
        ]

        bmp_values = [
            item["bmp"]

            for item
            in standard_recent

            if item["bmp"] is not None
        ]

        if len(dht_values) >= 2:

            dht_range = float(
                max(dht_values)
                -
                min(dht_values)
            )

        else:

            dht_range = 0.0

        if len(bmp_values) >= 2:

            bmp_range = float(
                max(bmp_values)
                -
                min(bmp_values)
            )

        else:

            bmp_range = 0.0

        diagnostics[
            "dht_range_c"
        ] = dht_range

        diagnostics[
            "bmp_range_c"
        ] = bmp_range

        reference_change = max(
            dht_range,
            bmp_range
        )

        diagnostics[
            "reference_change_c"
        ] = float(
            reference_change
        )

        reference_support = (
            reference_change
            >=
            self.reference_change_min_c
        )

        diagnostics[
            "reference_support"
        ] = bool(
            reference_support
        )

        # ========================================================
        # LONG-STUCK PHYSICAL CHECK
        # ========================================================

        long_start = (
            now
            -
            self.freeze_long_stuck_seconds
        )

        long_recent = [
            item

            for item
            in self.physical_history

            if item["time"]
            >= long_start
        ]

        long_stuck_support = False

        if len(long_recent) >= 2:

            long_duration = (
                long_recent[-1]["time"]
                -
                long_recent[0]["time"]
            )

            long_ds_values = [
                item["ds"]

                for item
                in long_recent

                if item["ds"] is not None
            ]

            if len(long_ds_values) >= 2:

                long_ds_range = float(
                    max(long_ds_values)
                    -
                    min(long_ds_values)
                )

                long_stuck_support = (
                    long_duration
                    >=
                    (
                        self.freeze_long_stuck_seconds
                        -
                        3.0
                    )
                    and
                    long_ds_range
                    <=
                    self.ds_stuck_tolerance_c
                )

        diagnostics[
            "long_stuck_support"
        ] = bool(
            long_stuck_support
        )

        # ========================================================
        # RECENT MODEL FREEZE PROBABILITY
        # ========================================================

        recent_probs = list(
            self.freeze_probability_history
        )

        if recent_probs:

            avg_probability = float(
                np.mean(
                    recent_probs[
                        -
                        self.freeze_long_required_windows:
                    ]
                )
            )

            sample_count = min(
                len(recent_probs),
                self.freeze_long_required_windows
            )

        else:

            avg_probability = 0.0

            sample_count = 0

        diagnostics[
            "recent_freeze_average_probability"
        ] = avg_probability

        diagnostics[
            "recent_freeze_probability_samples"
        ] = sample_count

        return diagnostics

    # ============================================================
    # FREEZE CONFIRMATION
    # ============================================================

    def _freeze_can_confirm(
        self,
        confidence,
        diagnostics
    ):
        """
        Two-path freeze confirmation.

        PATH A:
            DS stuck >=60 sec
            reference moved >=0.10 C
            confidence >=0.70
            >=3 consecutive freeze predictions

        PATH B:
            DS stuck >=75 sec
            >=8 consecutive freeze predictions
            average recent freeze probability >=0.60
        """

        diagnostics[
            "freeze_gate_passed"
        ] = False

        diagnostics[
            "freeze_path"
        ] = None

        # --------------------------------------------------------
        # Must first satisfy the basic DS stuck condition.
        # --------------------------------------------------------

        if not diagnostics.get(
            "ds_is_stuck",
            False
        ):

            return False

        # ========================================================
        # PATH A:
        # reference-supported freeze
        # ========================================================

        path_a = (
            confidence
            >=
            self.freeze_min_confidence

            and

            self.same_pred_count
            >=
            self.freeze_confirmation_windows

            and

            diagnostics.get(
                "reference_support",
                False
            )
        )

        if path_a:

            diagnostics[
                "freeze_gate_passed"
            ] = True

            diagnostics[
                "freeze_path"
            ] = "reference_supported"

            return True

        # ========================================================
        # PATH B:
        # long-duration strong model persistence
        # ========================================================

        avg_probability = float(
            diagnostics.get(
                "recent_freeze_average_probability",
                0.0
            )
        )

        probability_samples = int(
            diagnostics.get(
                "recent_freeze_probability_samples",
                0
            )
        )

        path_b = (
            diagnostics.get(
                "long_stuck_support",
                False
            )

            and

            self.same_pred_count
            >=
            self.freeze_long_required_windows

            and

            probability_samples
            >=
            self.freeze_long_required_windows

            and

            avg_probability
            >=
            self.freeze_long_avg_probability
        )

        if path_b:

            diagnostics[
                "freeze_gate_passed"
            ] = True

            diagnostics[
                "freeze_path"
            ] = "long_duration_model_supported"

            return True

        return False

    # ============================================================
    # ROW FEATURE EXTRACTION
    # ============================================================

    def _row_features(
        self,
        reading
    ):

        raw_values = []

        for col in self.sensor_cols:

            value = self._safe_float(
                reading.get(
                    col
                )
            )

            if value is None:

                raw_values.append(
                    np.nan
                )

            else:

                raw_values.append(
                    value
                )

        raw = np.array(
            raw_values,
            dtype=np.float32
        )

        # ========================================================
        # DS MISSING FLAG
        # ========================================================

        ds_missing = float(
            np.isnan(
                raw[0]
            )
        )

        # ========================================================
        # MEDIAN FILL
        # ========================================================

        filled = raw.copy()

        missing = np.isnan(
            filled
        )

        filled[
            missing
        ] = self.medians[
            missing
        ]

        # ========================================================
        # STANDARDIZE
        # ========================================================

        z = (
            filled
            -
            self.mean
        ) / self.scale

        # ========================================================
        # SENSOR DISAGREEMENT
        # ========================================================

        ds_dht = (
            filled[0]
            -
            filled[1]
        ) / 3.0

        ds_bmp = (
            filled[0]
            -
            filled[3]
        ) / 3.0

        base = np.concatenate(
            [
                z,

                np.array(
                    [
                        ds_missing,
                        ds_dht,
                        ds_bmp,
                    ],
                    dtype=np.float32
                )
            ]
        )

        return base.astype(
            np.float32
        )

    # ============================================================
    # AI FAULT STATE MACHINE
    # ============================================================

    def _update_ai_fault_state(
        self,
        label,
        confidence,
        probability_map,
        freeze_gate_passed
    ):

        # Neural DATA LOSS / CORRUPTION are never responsible for
        # final hard-fault confirmation.
        if label in self.HARD_FAULTS:

            label_for_ai = None

        else:

            label_for_ai = label

        # ========================================================
        # NO AI FAULT CONFIRMED
        # ========================================================

        if self.ai_confirmed_fault is None:

            self.ai_normal_recovery_count = 0

            self.ai_low_confidence_count = 0

            # ----------------------------------------------------
            # FREEZE
            # ----------------------------------------------------

            if label_for_ai == "freeze":

                if freeze_gate_passed:

                    self.ai_confirmed_fault = (
                        "freeze"
                    )

                return

            # ----------------------------------------------------
            # SPIKE / DRIFT
            # ----------------------------------------------------

            if (
                label_for_ai
                in self.AI_FAULTS

                and

                confidence
                >=
                self.min_conf

                and

                self.same_pred_count
                >=
                self.confirmation_windows
            ):

                self.ai_confirmed_fault = (
                    label_for_ai
                )

            return

        # ========================================================
        # AI FAULT ALREADY CONFIRMED
        # ========================================================

        confirmed_fault = (
            self.ai_confirmed_fault
        )

        confirmed_fault_probability = float(
            probability_map.get(
                confirmed_fault,
                0.0
            )
        )

        # ========================================================
        # LOW-CONFIDENCE DECAY
        # ========================================================

        if (
            confirmed_fault_probability
            <
            self.ai_low_confidence_threshold
        ):

            self.ai_low_confidence_count += 1

        else:

            self.ai_low_confidence_count = 0

        if (
            self.ai_low_confidence_count
            >=
            self.ai_low_confidence_clear_windows
        ):

            self.ai_confirmed_fault = None

            self.ai_low_confidence_count = 0

            self.ai_normal_recovery_count = 0

            return

        # ========================================================
        # MODEL SAYS NORMAL
        # ========================================================

        if label_for_ai == "normal":

            self.ai_normal_recovery_count += 1

            if (
                self.ai_normal_recovery_count
                >=
                self.ai_normal_recovery_required
            ):

                self.ai_confirmed_fault = None

                self.ai_normal_recovery_count = 0

                self.ai_low_confidence_count = 0

            return

        # ========================================================
        # SAME CONFIRMED FAULT
        # ========================================================

        if (
            label_for_ai
            ==
            self.ai_confirmed_fault
        ):

            self.ai_normal_recovery_count = 0

            return

        # ========================================================
        # DIFFERENT AI FAULT
        # ========================================================

        self.ai_normal_recovery_count = 0

        # --------------------------------------------------------
        # Switching TO freeze still requires freeze gate.
        # --------------------------------------------------------

        if label_for_ai == "freeze":

            if freeze_gate_passed:

                self.ai_confirmed_fault = (
                    "freeze"
                )

                self.ai_low_confidence_count = 0

            return

        # --------------------------------------------------------
        # SPIKE / DRIFT
        # --------------------------------------------------------

        if (
            label_for_ai
            in self.AI_FAULTS

            and

            confidence
            >=
            self.min_conf

            and

            self.same_pred_count
            >=
            self.confirmation_windows
        ):

            self.ai_confirmed_fault = (
                label_for_ai
            )

            self.ai_low_confidence_count = 0

    # ============================================================
    # FINAL CONFIRMED FAULT
    # ============================================================

    def _final_confirmed_fault(self):

        if (
            self.hard_confirmed_fault
            is not None
        ):

            return (
                self.hard_confirmed_fault
            )

        return (
            self.ai_confirmed_fault
        )

    # ============================================================
    # LIVE UPDATE
    # ============================================================

    def update(
        self,
        reading
    ):

        # ========================================================
        # 1. HARD FAULT CHECK
        # ========================================================

        current_hard_fault = (
            self._detect_hard_fault(
                reading
            )
        )

        self._update_hard_fault_state(
            current_hard_fault
        )

        # ========================================================
        # 2. PHYSICAL HISTORY
        # ========================================================

        if current_hard_fault is None:

            self._update_physical_history(
                reading
            )

        # ========================================================
        # 3. MODEL ROW
        # ========================================================

        row = self._row_features(
            reading
        )

        self.rows.append(
            row
        )

        # ========================================================
        # 4. WARM-UP
        # ========================================================

        if len(self.rows) < self.seq_len:

            confirmed_fault = (
                self._final_confirmed_fault()
            )

            return {
                "ready": False,

                "warming_up": True,

                "readings_needed":
                    self.seq_len
                    -
                    len(self.rows),

                "confirmed":
                    confirmed_fault
                    is not None,

                "confirmed_fault":
                    confirmed_fault,

                "hard_fault":
                    self.hard_confirmed_fault
                    is not None,

                "hard_fault_type":
                    self.hard_confirmed_fault,

                "hard_fault_active":
                    current_hard_fault
                    is not None,

                "hard_recovery_count":
                    self.hard_recovery_count,

                "hard_recovery_required":
                    self.hard_recovery_required,

                "ai_confirmed_fault":
                    self.ai_confirmed_fault,

                "ai_low_confidence_count":
                    self.ai_low_confidence_count,

                "ai_low_confidence_threshold":
                    self.ai_low_confidence_threshold,

                "ai_low_confidence_clear_windows":
                    self.ai_low_confidence_clear_windows,
            }

        # ========================================================
        # 5. TEMPORAL WINDOW
        # ========================================================

        w = np.stack(
            self.rows
        ).copy()

        # First five channels = standardized physical sensors.
        w[:, :5] = (
            w[:, :5]
            -
            w[0, :5]
        )

        # Relative disagreement channels.
        w[:, 6:8] = (
            w[:, 6:8]
            -
            w[0, 6:8]
        )

        # ========================================================
        # 6. FIRST DIFFERENCES
        # ========================================================

        dif = np.zeros(
            (
                self.seq_len,
                5
            ),
            dtype=np.float32
        )

        dif[1:] = (
            w[1:, :5]
            -
            w[:-1, :5]
        )

        # ========================================================
        # 7. MODEL DS STUCKNESS FEATURE
        # ========================================================

        stuck = np.zeros(
            (
                self.seq_len,
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

        # ========================================================
        # 8. FINAL FEATURE MATRIX
        # ========================================================

        feat = np.hstack(
            [
                w,
                dif,
                stuck
            ]
        ).astype(
            np.float32
        )

        x = torch.from_numpy(
            feat[None]
        )

        # ========================================================
        # 9. INFERENCE
        # ========================================================

        with torch.no_grad():

            logits = self.model(
                x
            )

            probs = torch.softmax(
                logits,
                dim=1
            )[0].cpu().numpy()

        idx = int(
            np.argmax(
                probs
            )
        )

        label = self.classes[
            idx
        ]

        confidence = float(
            probs[
                idx
            ]
        )

        probability_map = {
            class_name:
                float(
                    probability
                )

            for (
                class_name,
                probability
            )
            in zip(
                self.classes,
                probs
            )
        }

        # ========================================================
        # 10. RAW PREDICTION PERSISTENCE
        # ========================================================

        if label == self.last_pred:

            self.same_pred_count += 1

        else:

            self.last_pred = label

            self.same_pred_count = 1

        # ========================================================
        # 11. FREEZE PROBABILITY HISTORY
        # ========================================================

        freeze_probability = float(
            probability_map.get(
                "freeze",
                0.0
            )
        )

        self.freeze_probability_history.append(
            freeze_probability
        )

        # ========================================================
        # 12. FREEZE PHYSICAL ANALYSIS
        # ========================================================

        freeze_diagnostics = (
            self._analyze_freeze_physics()
        )

        freeze_gate_passed = False

        if label == "freeze":

            freeze_gate_passed = (
                self._freeze_can_confirm(
                    confidence,
                    freeze_diagnostics
                )
            )

        else:

            freeze_diagnostics[
                "freeze_gate_passed"
            ] = False

        # ========================================================
        # 13. AI STATE MACHINE
        # ========================================================

        if (
            self.hard_confirmed_fault
            is None
        ):

            self._update_ai_fault_state(
                label,
                confidence,
                probability_map,
                freeze_gate_passed
            )

        # ========================================================
        # 14. FINAL SYSTEM FAULT
        # ========================================================

        confirmed_fault = (
            self._final_confirmed_fault()
        )

        confirmed = (
            confirmed_fault
            is not None
        )

        # ========================================================
        # 15. RESULT
        # ========================================================

        return {
            "ready": True,

            "warming_up": False,

            # ----------------------------------------------------
            # RAW MODEL
            # ----------------------------------------------------

            "prediction":
                label,

            "confidence":
                confidence,

            "probabilities":
                probability_map,

            # ----------------------------------------------------
            # FINAL CONFIRMED SYSTEM STATE
            # ----------------------------------------------------

            "confirmed":
                confirmed,

            "confirmed_fault":
                confirmed_fault,

            # ----------------------------------------------------
            # HARD SAFETY
            # ----------------------------------------------------

            "hard_fault":
                self.hard_confirmed_fault
                is not None,

            "hard_fault_type":
                self.hard_confirmed_fault,

            "hard_fault_active":
                current_hard_fault
                is not None,

            "hard_recovery_count":
                self.hard_recovery_count,

            "hard_recovery_required":
                self.hard_recovery_required,

            # ----------------------------------------------------
            # AI TEMPORAL STATE
            # ----------------------------------------------------

            "ai_confirmed_fault":
                self.ai_confirmed_fault,

            "same_prediction_windows":
                self.same_pred_count,

            "normal_recovery_count":
                self.ai_normal_recovery_count,

            "normal_recovery_required":
                self.ai_normal_recovery_required,

            "ai_low_confidence_count":
                self.ai_low_confidence_count,

            "ai_low_confidence_threshold":
                self.ai_low_confidence_threshold,

            "ai_low_confidence_clear_windows":
                self.ai_low_confidence_clear_windows,

            # ----------------------------------------------------
            # FREEZE VALIDATION
            # ----------------------------------------------------

            "freeze_gate_passed":
                freeze_gate_passed,

            "freeze_gate":
                freeze_diagnostics,

            "freeze_min_confidence":
                self.freeze_min_confidence,

            "freeze_confirmation_windows":
                self.freeze_confirmation_windows,

            "ds_stuck_min_seconds":
                self.ds_stuck_min_seconds,

            "ds_stuck_tolerance_c":
                self.ds_stuck_tolerance_c,

            "reference_change_min_c":
                self.reference_change_min_c,

            "freeze_long_stuck_seconds":
                self.freeze_long_stuck_seconds,

            "freeze_long_required_windows":
                self.freeze_long_required_windows,

            "freeze_long_avg_probability":
                self.freeze_long_avg_probability,
        }

    # ============================================================
    # RESET STATE
    # ============================================================

    def reset_state(self):

        self.rows.clear()

        self.physical_history.clear()

        self.freeze_probability_history.clear()

        self.last_pred = None

        self.same_pred_count = 0

        self.ai_confirmed_fault = None

        self.ai_normal_recovery_count = 0

        self.ai_low_confidence_count = 0

        self.hard_confirmed_fault = None

        self.hard_recovery_count = 0