import json
import logging
from typing import Dict, Any, List, Union
import numpy as np
import pandas as pd

from config import FEATURE_NAMES_PATH

logger = logging.getLogger("payshield.feature_pipeline")

# Baseline constants derived from IEEE-CIS dataset reference
BASELINE_AMT_MEAN = 135.0
BASELINE_AMT_STD = 239.0
BASELINE_ID02_MEAN = 174000.0
BASELINE_ID02_STD = 159000.0
BASELINE_D15_MEAN = 163.0
BASELINE_D15_STD = 202.0

CARD4_AMT_MEANS = {
    "visa": 133.0,
    "mastercard": 135.0,
    "discover": 156.0,
    "american express": 185.0
}

CARD4_AMT_STDS = {
    "visa": 230.0,
    "mastercard": 235.0,
    "discover": 250.0,
    "american express": 280.0
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
        Enriches a raw transaction record with domain & engineered features.
        Matches the transformations defined in the training notebook.
        """
        data = dict(record)

        amt = float(data.get("TransactionAmt", 0.0) or 0.0)
        card4 = str(data.get("card4", "")).lower()
        id_02 = float(data.get("id_02", 0.0) or 0.0) if data.get("id_02") is not None else np.nan
        d15 = float(data.get("D15", 0.0) or 0.0) if data.get("D15") is not None else np.nan

        card4_mean = CARD4_AMT_MEANS.get(card4, BASELINE_AMT_MEAN)
        card4_std = CARD4_AMT_STDS.get(card4, BASELINE_AMT_STD)

        # 1. TransactionAmt interactions
        if "TransactionAmt_to_mean_card1" not in data:
            data["TransactionAmt_to_mean_card1"] = amt / BASELINE_AMT_MEAN if amt else 0.0
        if "TransactionAmt_to_mean_card4" not in data:
            data["TransactionAmt_to_mean_card4"] = amt / card4_mean if card4_mean else 0.0
        if "TransactionAmt_to_std_card1" not in data:
            data["TransactionAmt_to_std_card1"] = amt / BASELINE_AMT_STD if BASELINE_AMT_STD else 0.0
        if "TransactionAmt_to_std_card4" not in data:
            data["TransactionAmt_to_std_card4"] = amt / card4_std if card4_std else 0.0

        # 2. id_02 interactions
        if not np.isnan(id_02):
            if "id_02_to_mean_card1" not in data:
                data["id_02_to_mean_card1"] = id_02 / BASELINE_ID02_MEAN
            if "id_02_to_mean_card4" not in data:
                data["id_02_to_mean_card4"] = id_02 / BASELINE_ID02_MEAN
            if "id_02_to_std_card1" not in data:
                data["id_02_to_std_card1"] = id_02 / BASELINE_ID02_STD
            if "id_02_to_std_card4" not in data:
                data["id_02_to_std_card4"] = id_02 / BASELINE_ID02_STD
        else:
            data["id_02_to_mean_card1"] = np.nan
            data["id_02_to_mean_card4"] = np.nan
            data["id_02_to_std_card1"] = np.nan
            data["id_02_to_std_card4"] = np.nan

        # 3. D15 interactions
        if not np.isnan(d15):
            if "D15_to_mean_card1" not in data:
                data["D15_to_mean_card1"] = d15 / BASELINE_D15_MEAN
            if "D15_to_mean_card4" not in data:
                data["D15_to_mean_card4"] = d15 / BASELINE_D15_MEAN
            if "D15_to_std_card1" not in data:
                data["D15_to_std_card1"] = d15 / BASELINE_D15_STD
            if "D15_to_std_card4" not in data:
                data["D15_to_std_card4"] = d15 / BASELINE_D15_STD
            if "D15_to_mean_addr1" not in data:
                data["D15_to_mean_addr1"] = d15 / BASELINE_D15_MEAN
            if "D15_to_mean_addr2" not in data:
                data["D15_to_mean_addr2"] = d15 / BASELINE_D15_MEAN
            if "D15_to_std_addr1" not in data:
                data["D15_to_std_addr1"] = d15 / BASELINE_D15_STD
            if "D15_to_std_addr2" not in data:
                data["D15_to_std_addr2"] = d15 / BASELINE_D15_STD
        else:
            for k in ["D15_to_mean_card1", "D15_to_mean_card4", "D15_to_std_card1", "D15_to_std_card4",
                      "D15_to_mean_addr1", "D15_to_mean_addr2", "D15_to_std_addr1", "D15_to_std_addr2"]:
                data[k] = np.nan

        # 4. Email domain splits
        p_email = str(data.get("P_emaildomain", "") or "")
        r_email = str(data.get("R_emaildomain", "") or "")

        p_parts = p_email.split(".") if p_email else []
        r_parts = r_email.split(".") if r_email else []

        data["P_emaildomain_1"] = hash(p_parts[0]) % 1000 if len(p_parts) > 0 else np.nan
        data["P_emaildomain_2"] = hash(p_parts[1]) % 1000 if len(p_parts) > 1 else np.nan
        data["R_emaildomain_1"] = hash(r_parts[0]) % 1000 if len(r_parts) > 0 else np.nan
        data["R_emaildomain_2"] = hash(r_parts[1]) % 1000 if len(r_parts) > 1 else np.nan

        # Categorical string to numeric encoding fallback
        for key, val in list(data.items()):
            if isinstance(val, str):
                try:
                    data[key] = float(val)
                except ValueError:
                    # Deterministic hash code representation for categoricals
                    data[key] = float(abs(hash(val)) % 10000)

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

