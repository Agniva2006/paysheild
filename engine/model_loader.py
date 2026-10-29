import pickle
import time
import logging
import warnings
from typing import List, Tuple, Optional
import numpy as np
import pandas as pd

from config import MODELS_PKL_PATH, RESULT_DICT_PKL_PATH, DEFAULT_INFERENCE_MODE

logger = logging.getLogger("payshield.model_loader")

class ModelContainer:
    _instance: Optional["ModelContainer"] = None

    def __init__(self):
        self.models: List = []
        self.is_ready: bool = False
        self.load_error: Optional[str] = None
        self.load_duration_sec: float = 0.0

    @classmethod
    def get_instance(cls) -> "ModelContainer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load_models(self) -> bool:
        """Loads trained LightGBM models into memory."""
        if self.is_ready and self.models:
            return True

        start_time = time.time()
        logger.info("Initializing LightGBM model loader...")

        # Suppress benign scikit-learn version warning during unpickling
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning)
            try:
                if MODELS_PKL_PATH.exists():
                    logger.info(f"Loading models from: {MODELS_PKL_PATH}")
                    with open(MODELS_PKL_PATH, "rb") as f:
                        self.models = pickle.load(f)
                elif RESULT_DICT_PKL_PATH.exists():
                    logger.info(f"Falling back to models in: {RESULT_DICT_PKL_PATH}")
                    with open(RESULT_DICT_PKL_PATH, "rb") as f:
                        res = pickle.load(f)
                        self.models = res.get("models", [])
                else:
                    self.load_error = f"Model file not found at {MODELS_PKL_PATH} or {RESULT_DICT_PKL_PATH}"
                    logger.error(self.load_error)
                    return False

                if not isinstance(self.models, list) or len(self.models) == 0:
                    self.load_error = f"Invalid models payload loaded: {type(self.models)}"
                    logger.error(self.load_error)
                    return False

                self.load_duration_sec = time.time() - start_time
                self.is_ready = True
                logger.info(f"Successfully loaded {len(self.models)} LightGBM fold models in {self.load_duration_sec:.2f}s")
                return True

            except Exception as e:
                self.load_error = f"Failed to load model artifacts: {str(e)}"
                logger.exception(self.load_error)
                return False

    def predict(self, features_df: pd.DataFrame, mode: str = DEFAULT_INFERENCE_MODE) -> Tuple[np.ndarray, float]:
        """
        Runs model inference.
        Returns (probabilities array, inference_latency_ms).
        """
        if not self.is_ready or not self.models:
            success = self.load_models()
            if not success:
                raise RuntimeError(f"Models not ready for inference: {self.load_error}")

        start_t = time.perf_counter()

        if mode == "ensemble" and len(self.models) > 1:
            fold_probs = [m.predict_proba(features_df)[:, 1] for m in self.models]
            probs = np.mean(fold_probs, axis=0)
        else:
            # Fast mode: primary fold model
            probs = self.models[0].predict_proba(features_df)[:, 1]

        latency_ms = (time.perf_counter() - start_t) * 1000.0
        return np.clip(probs, 0.0, 1.0), latency_ms

# Global convenience accessor
def get_model_container() -> ModelContainer:
    return ModelContainer.get_instance()

