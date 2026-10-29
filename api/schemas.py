from typing import Dict, Any, List, Optional, Union, Literal
from pydantic import BaseModel, Field, ConfigDict
import uuid
import datetime

class TransactionInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    TransactionID: Optional[Union[int, str]] = Field(
        default_factory=lambda: f"TXN-{uuid.uuid4().hex[:8].upper()}",
        description="Unique identifier for transaction"
    )
    TransactionAmt: float = Field(..., gt=0.0, description="Transaction payment amount in USD / local currency")
    ProductCD: Optional[Union[str, int]] = Field("W", description="Product code (e.g. W, C, H, R, S)")
    card1: Optional[Union[float, int]] = Field(None, description="Card attribute 1")
    card2: Optional[Union[float, int]] = Field(None, description="Card attribute 2")
    card3: Optional[Union[float, int]] = Field(None, description="Card attribute 3")
    card4: Optional[str] = Field("visa", description="Card brand (visa, mastercard, discover, etc.)")
    card5: Optional[Union[float, int]] = Field(None, description="Card attribute 5")
    card6: Optional[str] = Field("debit", description="Card type (credit, debit, etc.)")
    addr1: Optional[Union[float, int]] = Field(None, description="Billing/Shipping region code")
    addr2: Optional[Union[float, int]] = Field(None, description="Billing/Shipping country code")
    dist1: Optional[float] = Field(None, description="Distance 1")
    P_emaildomain: Optional[str] = Field(None, description="Purchaser email domain")
    R_emaildomain: Optional[str] = Field(None, description="Recipient email domain")
    DeviceType: Optional[str] = Field(None, description="Client device type (mobile, desktop)")
    DeviceInfo: Optional[str] = Field(None, description="Device info / browser")
    features: Optional[Dict[str, Any]] = Field(None, description="Optional raw features map")

    def to_raw_dict(self) -> Dict[str, Any]:
        """Flattens model fields and extra fields into a single dict."""
        res = self.model_dump(exclude={"features"}, exclude_none=False)
        if self.features and isinstance(self.features, dict):
            res.update(self.features)
        # Include extra fields passed at root
        if hasattr(self, "__pydantic_extra__") and self.__pydantic_extra__:
            res.update(self.__pydantic_extra__)
        return res

class BatchTransactionRequest(BaseModel):
    transactions: List[TransactionInput] = Field(..., min_length=1, max_length=1000)
    inference_mode: Optional[Literal["fast", "ensemble"]] = Field("fast", description="Inference mode")

class ThresholdsInfo(BaseModel):
    t_stepup: float = Field(..., description="Dynamic step-up threshold")
    t_block: float = Field(..., description="Hard block threshold")

class DecisionResponse(BaseModel):
    transaction_id: str
    amount: float
    fraud_risk_score: float = Field(..., ge=0.0, le=1.0, description="Calibrated fraud probability")
    action: Literal["ALLOW", "STEP-UP", "BLOCK"]
    action_reason: str
    risk_factors: List[str] = Field(default_factory=list, description="Top observable drivers and explanation")
    guardrail_triggered: bool = Field(False, description="Whether pre-ML deterministic guardrail executed")
    guardrail_rule: Optional[str] = Field(None, description="Triggered guardrail rule identifier")
    friction_budget: float
    thresholds: ThresholdsInfo
    inference_mode: str
    latency_ms: float
    timestamp: str

class StepUpVerifyRequest(BaseModel):
    transaction_id: str = Field(..., description="Transaction ID requiring step-up challenge")
    otp_code: str = Field(..., description="6-digit authentication code (demo default: '123456')")

class StepUpVerifyResponse(BaseModel):
    transaction_id: str
    amount: float
    action: str
    status: str
    message: str
    timestamp: str

class BatchDecisionResponse(BaseModel):
    total_evaluated: int
    decisions: List[DecisionResponse]
    action_counts: Dict[str, int]
    total_amount_usd: float
    summary_latency_ms: float

class PolicyConfigResponse(BaseModel):
    friction_budget: float
    stepup_threshold: float
    block_threshold: float
    is_calibrated: bool
    fallback_active: bool
    action_rules: Dict[str, str]
    last_recalibrated_at: Optional[str] = None

class PolicyRecalibrateRequest(BaseModel):
    friction_budget: Optional[float] = Field(
        None, ge=0.001, le=0.10, description="Target legitimate friction budget (e.g. 0.01 for 1%, 0.02 for 2%)"
    )
    block_threshold: Optional[float] = Field(
        None, ge=0.50, le=0.999, description="Hard block threshold"
    )

class PolicyRecalibrateResponse(BaseModel):
    status: str
    previous: Dict[str, float]
    current: Dict[str, float]
    fallback_active: bool
    recalibrated_at: str
    requires_retraining: bool = False
    requires_restart: bool = False

class StressTestRequest(BaseModel):
    sample_size: int = Field(100, ge=10, le=5000, description="Number of synthetic/cached transactions to evaluate")
    from_budget: float = Field(0.02, ge=0.001, le=0.10, description="Initial friction budget (e.g. 2%)")
    to_budget: float = Field(0.01, ge=0.001, le=0.10, description="Shifted friction budget (e.g. 1%)")
    block_threshold: float = Field(0.85, ge=0.50, le=0.999)

class StressTestResponse(BaseModel):
    transactions_evaluated: int
    baseline_policy: Dict[str, Any]
    target_policy: Dict[str, Any]
    action_shift: Dict[str, Any]
    stepup_reduction_pct: float
    requires_retraining: bool = False
    requires_restart: bool = False
    execution_time_ms: float

