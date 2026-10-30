import json
import logging
from typing import Dict, Any, List, Union
import numpy as np
import pandas as pd

from config import FEATURE_NAMES_PATH

logger = logging.getLogger("payshield.feature_pipeline")


IEEE_CATEGORY_ENCODINGS = {
    # The training notebook applies sklearn LabelEncoder, which sorts labels.
    "ProductCD": {"c": 0, "h": 1, "r": 2, "s": 3, "w": 4, "nan": 5},
    "card4": {"american express": 0, "discover": 1, "mastercard": 2, "nan": 3, "visa": 4},
    "card6": {"charge card": 0, "credit": 1, "debit": 2, "debit or credit": 3, "nan": 4},
    "DeviceType": {"desktop": 0, "mobile": 1, "nan": 2},
    **{f"M{i}": {"f": 0, "t": 1, "nan": 2} for i in range(1, 10)},
}

class FeaturePipeline:
    def __init__(self, feature_names_path=FEATURE_NAMES_PATH):
        self.feature_names: List[str] = []
        self._load_feature_names(feature_names_path)

    def _load_feature_names(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.feature_names = json.load(f)
            logger.info(f"Loaded {len(self.feature_names)} expected feature names")
        except Exception as e:
            logger.error(f"Failed loading feature names from {path}: {e}")
            self.feature_names = []

    def transform_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Applies only transforms that can be reproduced from available metadata.
        Unavailable training-time encoders and group statistics stay missing.
        """
        data = dict(record)

        # The training-time group means/stds and fitted categorical vocabularies
        # are not bundled with this legacy model. Never fabricate their values.
        # Preserve merchant-provided, training-compatible engineered features;
        # otherwise LightGBM receives missing values for those columns.
        for name in (
            "TransactionAmt_to_mean_card1", "TransactionAmt_to_mean_card4",
            "TransactionAmt_to_std_card1", "TransactionAmt_to_std_card4",
            "id_02_to_mean_card1", "id_02_to_mean_card4",
            "id_02_to_std_card1", "id_02_to_std_card4",
            "D15_to_mean_card1", "D15_to_mean_card4", "D15_to_std_card1", "D15_to_std_card4",
            "D15_to_mean_addr1", "D15_to_mean_addr2", "D15_to_std_addr1", "D15_to_std_addr2",
        ):
            data.setdefault(name, np.nan)

        # 4. Email domain splits
        p_email = str(data.get("P_emaildomain", "") or "")
        r_email = str(data.get("R_emaildomain", "") or "")

        # These were LabelEncoded using the complete training vocabulary. The fitted
        # vocabulary is not present in the saved model artifacts, so unknown domain
        # categories must remain missing instead of receiving fabricated hash codes.
        data.setdefault("P_emaildomain_1", np.nan)
        data.setdefault("P_emaildomain_2", np.nan)
        data.setdefault("R_emaildomain_1", np.nan)
        data.setdefault("R_emaildomain_2", np.nan)

        # Match the training notebook's sorted LabelEncoder codes when available.
        for key, val in list(data.items()):
            if val is None and key in IEEE_CATEGORY_ENCODINGS:
                data[key] = float(IEEE_CATEGORY_ENCODINGS[key]["nan"])
                continue
            if isinstance(val, str):
                try:
                    data[key] = float(val)
                except ValueError:
                    encoded = IEEE_CATEGORY_ENCODINGS.get(key, {}).get(val.strip().lower())
                    data[key] = float(encoded) if encoded is not None else np.nan

        # These numeric-looking fields were LabelEncoded from string values in
        # training. Without the saved LabelEncoder classes, raw identifiers are
        # not valid model codes; missing is safer than passing the raw number.
        for key in ("card1", "card2", "card3", "card5", "addr1", "addr2"):
            if key in data and data[key] is not None:
                data[key] = np.nan

        # LabelEncoder saw these category vocabularies during training, including
        # the literal "nan" value for missing entries. Preserve that trained code.
        for key, encoding in IEEE_CATEGORY_ENCODINGS.items():
            if key not in data:
                data[key] = float(encoding["nan"])

        return data

    def prepare_dataframe(self, records: Union[Dict[str, Any], List[Dict[str, Any]]]) -> pd.DataFrame:
        """
        Accepts single record dict or list of records.
        Returns a DataFrame strictly aligned to the 369 model feature names in exact order.
        """
        if isinstance(records, dict):
            records = [records]

        transformed = [self.transform_record(r) for r in records]
        df = pd.DataFrame(transformed)

        # Align to expected 369 columns, filling missing with NaN
        aligned_df = df.reindex(columns=self.feature_names)
        
        # Ensure all columns are numeric/float
        aligned_df = aligned_df.astype(float)
        return aligned_df

# Convenience singleton instance
_feature_pipeline_instance: Union[FeaturePipeline, None] = None

def get_feature_pipeline() -> FeaturePipeline:
    global _feature_pipeline_instance
    if _feature_pipeline_instance is None:
        _feature_pipeline_instance = FeaturePipeline()
    return _feature_pipeline_instance

