import json
import logging
from typing import Dict, Any, Tuple, Optional
import numpy as np

from config import (
    CALIBRATION_STATS_PATH,
    DEFAULT_FRICTION_BUDGET,
    MIN_FRICTION_BUDGET,
    MAX_FRICTION_BUDGET,
    DEFAULT_BLOCK_THRESHOLD,
    FALLBACK_STEPUP_THRESHOLD,
    FALLBACK_BLOCK_THRESHOLD,
)

logger = logging.getLogger("payshield.policy_engine")

class DynamicPolicyEngine:
    _instance: Optional["DynamicPolicyEngine"] = None

    def __init__(self, calibration_path=CALIBRATION_STATS_PATH):
        self.friction_budget: float = DEFAULT_FRICTION_BUDGET
        self.block_threshold: float = DEFAULT_BLOCK_THRESHOLD
        self.stepup_threshold: float = FALLBACK_STEPUP_THRESHOLD
        self.quantile_table: Dict[str, float] = {}
        self.is_calibrated: bool = False
        self.last_recalibrated_at: Optional[str] = None
        self.fallback_active: bool = False
        self._load_calibration_stats(calibration_path)

    @classmethod
    def get_instance(cls) -> "DynamicPolicyEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_calibration_stats(self, path):
        """Loads precomputed quantile table from out-of-fold calibration set."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.quantile_table = {float(k): float(v) for k, v in data.get("quantile_table", {}).items()}
            self.friction_budget = float(data.get("default_budget", DEFAULT_FRICTION_BUDGET))
            self.block_threshold = float(data.get("default_block_threshold", DEFAULT_BLOCK_THRESHOLD))
            
            # Recalculate T_stepup from loaded table
            self.stepup_threshold = self._compute_threshold(self.friction_budget)
            self.is_calibrated = True
            self.fallback_active = False
            logger.info(
                f"PolicyEngine initialized. Budget={self.friction_budget:.2%}, "
                f"T_stepup={self.stepup_threshold:.6f}, T_block={self.block_threshold:.4f}"
            )
        except Exception as e:
            logger.warning(f"Could not load calibration stats from {path}: {e}. Engaging safe fallback thresholds.")
            self.friction_budget = DEFAULT_FRICTION_BUDGET
            self.stepup_threshold = FALLBACK_STEPUP_THRESHOLD
            self.block_threshold = FALLBACK_BLOCK_THRESHOLD
            self.fallback_active = True
            self.is_calibrated = False

    def _compute_threshold(self, budget: float) -> float:
        """
        Computes dynamic T_stepup(B) = Quantile(1 - B) using linear interpolation
        over the empirical calibration distribution.
        """
        if not self.quantile_table:
            return FALLBACK_STEPUP_THRESHOLD

        sorted_budgets = sorted(self.quantile_table.keys())
        sorted_thresholds = [self.quantile_table[b] for b in sorted_budgets]

        # Invert: as budget B increases (more friction allowed), threshold decreases
        # np.interp expects xp to be increasing
        xp = np.array(sorted_budgets)
        yp = np.array(sorted_thresholds)

        interpolated_threshold = float(np.interp(budget, xp, yp))
        # Ensure threshold does not exceed block_threshold
        return min(interpolated_threshold, self.block_threshold - 0.001)

    def recalibrate(self, new_budget: Optional[float] = None, new_block_threshold: Optional[float] = None) -> Dict[str, Any]:
        """
        Live recalibration of operational policy thresholds WITHOUT model retraining or restart.
        """
        import datetime

        old_budget = self.friction_budget
        old_stepup = self.stepup_threshold
        old_block = self.block_threshold

        if new_block_threshold is not None:
            if 0.5 <= new_block_threshold <= 0.999:
                self.block_threshold = float(new_block_threshold)
            else:
                raise ValueError(f"Block threshold must be in range [0.5, 0.999], got {new_block_threshold}")

        if new_budget is not None:
            if MIN_FRICTION_BUDGET <= new_budget <= MAX_FRICTION_BUDGET:
                self.friction_budget = float(new_budget)
            else:
                raise ValueError(
                    f"Friction budget must be in range [{MIN_FRICTION_BUDGET:.3f}, {MAX_FRICTION_BUDGET:.3f}], "
                    f"got {new_budget}"
                )

        try:
            self.stepup_threshold = self._compute_threshold(self.friction_budget)
            self.fallback_active = False
        except Exception as e:
            logger.error(f"Recalibration calculation failed: {e}. Retaining last valid thresholds (failsafe).")
            self.fallback_active = True

        self.last_recalibrated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        logger.info(
            f"Policy recalibrated: Budget {old_budget:.2%} -> {self.friction_budget:.2%}, "
            f"T_stepup {old_stepup:.6f} -> {self.stepup_threshold:.6f}, T_block {old_block:.4f} -> {self.block_threshold:.4f}"
        )

        return {
            "status": "success",
            "previous": {
                "friction_budget": old_budget,
                "stepup_threshold": old_stepup,
                "block_threshold": old_block,
            },
            "current": {
                "friction_budget": self.friction_budget,
                "stepup_threshold": self.stepup_threshold,
                "block_threshold": self.block_threshold,
            },
            "fallback_active": self.fallback_active,
            "recalibrated_at": self.last_recalibrated_at,
            "requires_retraining": False,
            "requires_restart": False,
        }

    def evaluate(self, risk_score: float) -> Tuple[str, str]:
        """
        Three-action policy decision logic:
        1. score < T_stepup(B)           -> ALLOW
        2. T_stepup(B) <= score < T_block -> STEP-UP
        3. score >= T_block               -> BLOCK
        """
        score = float(risk_score)
        if score >= self.block_threshold:
            action = "BLOCK"
            reason = (
                f"Calibrated fraud risk ({score:.4f}) >= block threshold ({self.block_threshold:.4f}). "
                "High probability of fraudulent attack; hard denial executed."
            )
        elif score >= self.stepup_threshold:
            action = "STEP-UP"
            reason = (
                f"Calibrated fraud risk ({score:.4f}) between step-up ({self.stepup_threshold:.4f}) "
                f"and block ({self.block_threshold:.4f}) thresholds under {self.friction_budget:.2%} friction budget. "
                "Secondary authentication (OTP / 3DS / Biometrics) required."
            )
        else:
            action = "ALLOW"
            reason = (
                f"Calibrated fraud risk ({score:.4f}) < step-up threshold ({self.stepup_threshold:.4f}). "
                "Transaction clears risk tolerance; seamless checkout permitted."
            )

        return action, reason

    def get_state(self) -> Dict[str, Any]:
        """Returns the full runtime policy configuration."""
        return {
            "friction_budget": self.friction_budget,
            "stepup_threshold": self.stepup_threshold,
            "block_threshold": self.block_threshold,
            "is_calibrated": self.is_calibrated,
            "fallback_active": self.fallback_active,
            "last_recalibrated_at": self.last_recalibrated_at,
            "action_rules": {
                "ALLOW": f"score < {self.stepup_threshold:.6f}",
                "STEP-UP": f"{self.stepup_threshold:.6f} <= score < {self.block_threshold:.4f}",
                "BLOCK": f"score >= {self.block_threshold:.4f}",
            },
        }

def get_policy_engine() -> DynamicPolicyEngine:
    return DynamicPolicyEngine.get_instance()

