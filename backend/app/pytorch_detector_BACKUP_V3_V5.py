from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

from .live_inference import SkyGuardLiveClassifier


# ============================================================
# SKYGUARD STATION-AWARE PYTORCH SERVICE
# ============================================================
#
# Final validated routing:
#
# AWS_001 -> PyTorch V3
#            Proven on real live:
#            spike / freeze / drift / hard faults
#
# AWS_002 -> PyTorch V5
#            Normal historical replay:
#            0 confirmed false faults
#
# AWS_003 -> PyTorch V5
#            Normal historical replay:
#            0 confirmed false faults
#
# Hybrid RF v6 remains untouched and remains the primary model.
# ============================================================


MODEL_DIR = (
    Path(__file__).resolve().parent
    / "models"
)


MODEL_BY_NODE = {

    "AWS_001":
        "skyguard_pytorch_multiclass_v3.pt",

    "AWS_002":
        "skyguard_pytorch_multiclass_v5.pt",

    "AWS_003":
        "skyguard_pytorch_multiclass_v5.pt",
}


class PyTorchDetectorService:
    """
    Station-aware wrapper around SkyGuard PyTorch models.

    IMPORTANT:
    - AWS_001 uses validated PyTorch V3.
    - AWS_002 and AWS_003 use transfer-safe PyTorch V5.
    - Every station gets an INDEPENDENT classifier instance.
    - Temporal buffers are NEVER shared between stations.
    - Confirmation/recovery state is NEVER shared.
    - Freeze histories are NEVER shared.
    - Hybrid RF v6 is not replaced or modified.
    - PyTorch remains an additional observational /
      validation layer in the backend.
    """

    def __init__(self):

        # ----------------------------------------------------
        # One independent classifier per physical station
        # ----------------------------------------------------

        self.detectors: Dict[
            str,
            SkyGuardLiveClassifier
        ] = {}

        # One independent lock per classifier.
        self.locks: Dict[
            str,
            Lock
        ] = {}

        # Model path used by every node.
        self.model_paths: Dict[
            str,
            Path
        ] = {}

        # Per-node loading errors.
        self.errors: Dict[
            str,
            Optional[str]
        ] = {}

        # Per-node loaded state.
        self.loaded_nodes: Dict[
            str,
            bool
        ] = {}

        self._load_models()

    # ========================================================
    # MODEL LOADING
    # ========================================================

    def _load_models(
        self
    ):

        print()
        print(
            "=" * 70
        )

        print(
            "[PYTORCH] Loading station-aware models"
        )

        print(
            "=" * 70
        )

        for (
            node_id,
            model_filename
        ) in MODEL_BY_NODE.items():

            model_path = (
                MODEL_DIR
                / model_filename
            )

            self.model_paths[
                node_id
            ] = model_path

            self.locks[
                node_id
            ] = Lock()

            self.errors[
                node_id
            ] = None

            self.loaded_nodes[
                node_id
            ] = False

            try:

                if not model_path.exists():

                    raise FileNotFoundError(
                        f"PyTorch model not found: "
                        f"{model_path}"
                    )

                detector = (
                    SkyGuardLiveClassifier(
                        str(
                            model_path
                        )
                    )
                )

                self.detectors[
                    node_id
                ] = detector

                self.loaded_nodes[
                    node_id
                ] = True

                self.errors[
                    node_id
                ] = None

                print()

                print(
                    f"[PYTORCH] {node_id} READY"
                )

                print(
                    f"[PYTORCH] {node_id} model: "
                    f"{model_filename}"
                )

                print(
                    f"[PYTORCH] {node_id} classes: "
                    f"{detector.classes}"
                )

                print(
                    f"[PYTORCH] {node_id} "
                    f"sequence length: "
                    f"{detector.seq_len}"
                )

            except Exception as exc:

                self.loaded_nodes[
                    node_id
                ] = False

                self.errors[
                    node_id
                ] = (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                print()

                print(
                    f"[PYTORCH] {node_id} DISABLED:"
                )

                print(
                    self.errors[
                        node_id
                    ]
                )

                # IMPORTANT:
                # Do not crash FastAPI.
                # RF v6 / MQTT / REST must remain functional.

        print()

        print(
            "=" * 70
        )

    # ========================================================
    # SENSOR EXTRACTION
    # ========================================================

    @staticmethod
    def _sensor_value(
        sensors: Dict[str, Any],
        key: str,
    ):

        value = sensors.get(
            key
        )

        if value is None:
            return None

        try:

            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            return None

    def _build_reading(
        self,
        sensors: Dict[str, Any],
    ) -> Dict[
        str,
        Optional[float]
    ]:

        """
        Convert the backend sensor dictionary into exactly
        the format expected by SkyGuardLiveClassifier.
        """

        return {

            "ds18b20_temperature_c":
                self._sensor_value(
                    sensors,
                    "ds18b20_temperature_c",
                ),

            "dht22_temperature_c":
                self._sensor_value(
                    sensors,
                    "dht22_temperature_c",
                ),

            "dht22_humidity_pct":
                self._sensor_value(
                    sensors,
                    "dht22_humidity_pct",
                ),

            "bmp280_temperature_c":
                self._sensor_value(
                    sensors,
                    "bmp280_temperature_c",
                ),

            "bmp280_pressure_hpa":
                self._sensor_value(
                    sensors,
                    "bmp280_pressure_hpa",
                ),
        }

    # ========================================================
    # MODEL INFORMATION
    # ========================================================

    def _model_info(
        self,
        node_id: str,
    ) -> Dict[str, Any]:

        model_path = self.model_paths.get(
            node_id
        )

        model_name = (
            model_path.name
            if model_path is not None
            else None
        )

        if node_id == "AWS_001":

            model_version = "V3"

            validation_role = (
                "AWS_001 live-fault validated"
            )

        elif node_id in (
            "AWS_002",
            "AWS_003",
        ):

            model_version = "V5"

            validation_role = (
                "cross-station normal-transfer validated"
            )

        else:

            model_version = None
            validation_role = None

        return {

            "model":
                model_name,

            "model_version":
                model_version,

            "validation_role":
                validation_role,
        }

    # ========================================================
    # LIVE INFERENCE
    # ========================================================

    def process(
        self,
        node_id: str,
        sensors: Dict[str, Any],
    ) -> Dict[str, Any]:

        # ----------------------------------------------------
        # Unsupported station
        # ----------------------------------------------------

        if node_id not in MODEL_BY_NODE:

            return {

                "enabled":
                    False,

                "supported":
                    False,

                "node_id":
                    node_id,

                "reason":
                    (
                        "PyTorch currently validated "
                        "for AWS_001, AWS_002 and AWS_003"
                    ),
            }

        model_info = self._model_info(
            node_id
        )

        # ----------------------------------------------------
        # Model failed to load
        # ----------------------------------------------------

        detector = self.detectors.get(
            node_id
        )

        if (
            not self.loaded_nodes.get(
                node_id,
                False
            )
            or
            detector is None
        ):

            return {

                "enabled":
                    False,

                "supported":
                    True,

                "node_id":
                    node_id,

                **model_info,

                "error":
                    self.errors.get(
                        node_id
                    ),
            }

        # ----------------------------------------------------
        # Prepare sensor data
        # ----------------------------------------------------

        reading = self._build_reading(
            sensors
        )

        # ----------------------------------------------------
        # Stateful temporal inference
        #
        # IMPORTANT:
        # Each node uses its OWN detector and OWN lock.
        # ----------------------------------------------------

        try:

            node_lock = self.locks[
                node_id
            ]

            with node_lock:

                result = detector.update(
                    reading
                )

        except Exception as exc:

            return {

                "enabled":
                    True,

                "supported":
                    True,

                "node_id":
                    node_id,

                **model_info,

                "ready":
                    False,

                "error":
                    (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
            }

        # ----------------------------------------------------
        # WARM-UP
        # ----------------------------------------------------

        if not result.get(
            "ready",
            False
        ):

            freeze_gate = result.get(
                "freeze_gate",
                {}
            )

            return {

                "enabled":
                    True,

                "supported":
                    True,

                "node_id":
                    node_id,

                **model_info,

                "ready":
                    False,

                "warming_up":
                    True,

                "readings_needed":
                    result.get(
                        "readings_needed",
                        0
                    ),

                "confirmed":
                    result.get(
                        "confirmed",
                        False
                    ),

                "confirmed_fault":
                    result.get(
                        "confirmed_fault"
                    ),

                # --------------------------------------------
                # HARD SAFETY
                # --------------------------------------------

                "hard_fault":
                    result.get(
                        "hard_fault",
                        False
                    ),

                "hard_fault_type":
                    result.get(
                        "hard_fault_type"
                    ),

                "hard_fault_active":
                    result.get(
                        "hard_fault_active",
                        False
                    ),

                "hard_recovery_count":
                    result.get(
                        "hard_recovery_count",
                        0
                    ),

                "hard_recovery_required":
                    result.get(
                        "hard_recovery_required",
                        3
                    ),

                # --------------------------------------------
                # AI TEMPORAL STATE
                # --------------------------------------------

                "ai_confirmed_fault":
                    result.get(
                        "ai_confirmed_fault"
                    ),

                "ai_low_confidence_count":
                    result.get(
                        "ai_low_confidence_count",
                        0
                    ),

                "ai_low_confidence_threshold":
                    result.get(
                        "ai_low_confidence_threshold"
                    ),

                "ai_low_confidence_clear_windows":
                    result.get(
                        "ai_low_confidence_clear_windows"
                    ),

                # --------------------------------------------
                # FREEZE GATE
                # --------------------------------------------

                "freeze_gate":
                    freeze_gate,

                "freeze_gate_passed":
                    freeze_gate.get(
                        "freeze_gate_passed",
                        False
                    ),
            }

        # ----------------------------------------------------
        # NORMAL MODEL OUTPUT
        # ----------------------------------------------------

        freeze_gate = result.get(
            "freeze_gate",
            {}
        )

        # Some versions expose freeze_gate_passed directly,
        # while others expose it inside freeze_gate.
        freeze_gate_passed = result.get(
            "freeze_gate_passed"
        )

        if freeze_gate_passed is None:

            freeze_gate_passed = (
                freeze_gate.get(
                    "freeze_gate_passed",
                    False
                )
            )

        return {

            "enabled":
                True,

            "supported":
                True,

            "node_id":
                node_id,

            **model_info,

            "ready":
                True,

            "warming_up":
                False,

            # ------------------------------------------------
            # RAW NEURAL NETWORK OUTPUT
            # ------------------------------------------------

            "prediction":
                result.get(
                    "prediction"
                ),

            "confidence":
                result.get(
                    "confidence",
                    0.0
                ),

            # ------------------------------------------------
            # FINAL STABLE PYTORCH / HARD-RULE DECISION
            # ------------------------------------------------

            "confirmed":
                result.get(
                    "confirmed",
                    False
                ),

            "confirmed_fault":
                result.get(
                    "confirmed_fault"
                ),

            # ------------------------------------------------
            # HARD SAFETY LAYER
            # ------------------------------------------------

            "hard_fault":
                result.get(
                    "hard_fault",
                    False
                ),

            "hard_fault_type":
                result.get(
                    "hard_fault_type"
                ),

            "hard_fault_active":
                result.get(
                    "hard_fault_active",
                    False
                ),

            "hard_recovery_count":
                result.get(
                    "hard_recovery_count",
                    0
                ),

            "hard_recovery_required":
                result.get(
                    "hard_recovery_required",
                    3
                ),

            # ------------------------------------------------
            # AI HYSTERESIS / TEMPORAL STATE
            # ------------------------------------------------

            "ai_confirmed_fault":
                result.get(
                    "ai_confirmed_fault"
                ),

            "same_prediction_windows":
                result.get(
                    "same_prediction_windows",
                    0
                ),

            "normal_recovery_count":
                result.get(
                    "normal_recovery_count",
                    0
                ),

            "normal_recovery_required":
                result.get(
                    "normal_recovery_required",
                    3
                ),

            "ai_low_confidence_count":
                result.get(
                    "ai_low_confidence_count",
                    0
                ),

            "ai_low_confidence_threshold":
                result.get(
                    "ai_low_confidence_threshold"
                ),

            "ai_low_confidence_clear_windows":
                result.get(
                    "ai_low_confidence_clear_windows"
                ),

            # ------------------------------------------------
            # FREEZE SEMANTIC GATE
            # ------------------------------------------------

            "freeze_gate_passed":
                freeze_gate_passed,

            "freeze_gate":
                freeze_gate,

            "freeze_min_confidence":
                result.get(
                    "freeze_min_confidence"
                ),

            "freeze_confirmation_windows":
                result.get(
                    "freeze_confirmation_windows"
                ),

            "ds_stuck_min_seconds":
                result.get(
                    "ds_stuck_min_seconds"
                ),

            "ds_stuck_tolerance_c":
                result.get(
                    "ds_stuck_tolerance_c"
                ),

            "reference_change_min_c":
                result.get(
                    "reference_change_min_c"
                ),

            # ------------------------------------------------
            # ALL CLASS PROBABILITIES
            # ------------------------------------------------

            "probabilities":
                result.get(
                    "probabilities",
                    {}
                ),
        }

    # ========================================================
    # RESET ONE NODE
    # ========================================================

    def reset_node(
        self,
        node_id: str,
    ) -> bool:

        detector = self.detectors.get(
            node_id
        )

        if detector is None:
            return False

        node_lock = self.locks[
            node_id
        ]

        with node_lock:

            detector.reset_state()

        return True

    # ========================================================
    # RESET
    #
    # Kept for compatibility with any existing backend code
    # that already calls pytorch_detector.reset().
    # ========================================================

    def reset(
        self
    ) -> bool:

        reset_any = False

        for node_id in MODEL_BY_NODE:

            detector = self.detectors.get(
                node_id
            )

            if detector is None:
                continue

            node_lock = self.locks[
                node_id
            ]

            with node_lock:

                detector.reset_state()

            reset_any = True

        return reset_any

    # ========================================================
    # STATUS
    # ========================================================

    def status(
        self
    ) -> Dict[str, Any]:

        node_status = {}

        for node_id in MODEL_BY_NODE:

            detector = self.detectors.get(
                node_id
            )

            model_path = self.model_paths.get(
                node_id
            )

            info = self._model_info(
                node_id
            )

            freeze_semantic_gate = {
                "enabled":
                    False
            }

            if detector is not None:

                freeze_semantic_gate = {

                    "enabled":
                        True,

                    "min_confidence":
                        getattr(
                            detector,
                            "freeze_min_confidence",
                            None
                        ),

                    "confirmation_windows":
                        getattr(
                            detector,
                            "freeze_confirmation_windows",
                            None
                        ),

                    "ds_stuck_min_seconds":
                        getattr(
                            detector,
                            "ds_stuck_min_seconds",
                            None
                        ),

                    "ds_stuck_tolerance_c":
                        getattr(
                            detector,
                            "ds_stuck_tolerance_c",
                            None
                        ),

                    "reference_change_min_c":
                        getattr(
                            detector,
                            "reference_change_min_c",
                            None
                        ),
                }

            node_status[
                node_id
            ] = {

                "loaded":
                    self.loaded_nodes.get(
                        node_id,
                        False
                    ),

                **info,

                "model_exists":
                    (
                        model_path.exists()
                        if model_path is not None
                        else False
                    ),

                "error":
                    self.errors.get(
                        node_id
                    ),

                "classes":
                    (
                        list(
                            detector.classes
                        )
                        if detector is not None
                        else []
                    ),

                "sequence_length":
                    (
                        detector.seq_len
                        if detector is not None
                        else None
                    ),

                "freeze_semantic_gate":
                    freeze_semantic_gate,
            }

        all_loaded = all(
            self.loaded_nodes.get(
                node_id,
                False
            )
            for node_id
            in MODEL_BY_NODE
        )

        any_loaded = any(
            self.loaded_nodes.get(
                node_id,
                False
            )
            for node_id
            in MODEL_BY_NODE
        )

        return {

            "loaded":
                all_loaded,

            "any_loaded":
                any_loaded,

            "station_aware":
                True,

            "supported_nodes":
                list(
                    MODEL_BY_NODE.keys()
                ),

            "routing": {

                "AWS_001":
                    "PyTorch V3",

                "AWS_002":
                    "PyTorch V5",

                "AWS_003":
                    "PyTorch V5",
            },

            "nodes":
                node_status,
        }


# ============================================================
# SINGLE GLOBAL SERVICE
#
# Internally contains one independent classifier per station.
# ============================================================

pytorch_detector = PyTorchDetectorService()