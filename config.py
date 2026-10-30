from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent

# File paths
MODELS_PKL_PATH = BASE_DIR / "lgb_models.pkl"
RESULT_DICT_PKL_PATH = BASE_DIR / "result_dict_lgb.pkl"
FEATURE_NAMES_PATH = BASE_DIR / "data" / "feature_names.json"
CALIBRATION_STATS_PATH = BASE_DIR / "data" / "calibration_stats.json"
SAMPLE_TRANSACTIONS_PATH = BASE_DIR / "data" / "sample_transactions.json"

# Operational Policy Defaults
DEFAULT_FRICTION_BUDGET = 0.02        # 2% legitimate friction budget
MIN_FRICTION_BUDGET = 0.001           # 0.1% minimum
MAX_FRICTION_BUDGET = 0.10            # 10% maximum
DEFAULT_BLOCK_THRESHOLD = 0.85        # Hard block threshold

# Safe Fallback Defaults (in case calibration is unavailable)
FALLBACK_STEPUP_THRESHOLD = 0.769512  # Empirical 2% quantile
FALLBACK_BLOCK_THRESHOLD = 0.85       # Safe hard block

# Inference Settings
DEFAULT_INFERENCE_MODE = "fast"       # "fast" (Model 0, ~1-3ms) or "ensemble" (5-fold avg)

# Telemetry Settings
MAX_RECENT_DECISIONS_BUFFER = 500

# API Configuration
API_TITLE = "PayShield: Constraint-Aware Fraud Decision API"
API_VERSION = "1.0.0"
API_DESCRIPTION = (
    "PayShield assesses payment website URLs and explains observable URL signals. IEEE-CIS transaction "
    "scoring is available through authenticated merchant integrations that provide transaction features."
)

