"""IEEE-CIS transaction inference shared by merchant integration endpoints."""

import datetime

from api.schemas import DecisionResponse, ThresholdsInfo, TransactionInput
from config import DEFAULT_INFERENCE_MODE
from engine.explainer import RiskExplainer
from engine.feature_pipeline import get_feature_pipeline
from engine.guardrails import get_guardrails
from engine.model_loader import get_model_container
from engine.policy_engine import get_policy_engine
from engine.telemetry import get_telemetry_monitor


async def score_transaction(
    transaction: TransactionInput,
    mode: str = DEFAULT_INFERENCE_MODE,
    input_source: str = "integration",
) -> DecisionResponse:
    pipeline = get_feature_pipeline()
    model = get_model_container()
    policy = get_policy_engine()
    telemetry = get_telemetry_monitor()
    guardrails = get_guardrails()
    raw = transaction.to_raw_dict()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    thresholds = {"t_stepup": policy.stepup_threshold, "t_block": policy.block_threshold}

    triggered, rule_id, reason = guardrails.evaluate(raw)
    if triggered:
        score, action, factors, latency = 1.0, "BLOCK", [reason], 0.5
        action_reason = f"Pre-ML hard rule: {reason}"
        inference_mode = "guardrail_pre_ml"
    else:
        frame = pipeline.prepare_dataframe(raw)
        probabilities, latency = model.predict(frame, mode=mode)
        score = float(probabilities[0])
        action, action_reason = policy.evaluate(score)
        factors = RiskExplainer.explain_model_contributions(model.explain(frame, mode))
        inference_mode = mode

    model_features = set(pipeline.feature_names)
    provided_feature_count = sum(
        1 for name, value in raw.items()
        if name in model_features and value is not None
    )
    expected_feature_count = len(model_features)
    input_coverage = provided_feature_count / expected_feature_count if expected_feature_count else 0.0
    recommended_action = "REVIEW"
    quality_note = (
        f"{input_source.title()} input supplied {provided_feature_count} of {expected_feature_count} model fields. "
        "This output is advisory only: the saved model lacks its fitted categorical encoders and training-time group-statistic bundle, "
        "so feature parity with training is not guaranteed. Review the transaction; do not automate approval or blocking from this artifact."
    )

    if input_source != "manual":
        telemetry.record_decision(
            transaction_id=str(transaction.TransactionID),
            amount=transaction.TransactionAmt,
            score=score,
            action=action,
            thresholds=thresholds,
            friction_budget=policy.friction_budget,
            latency_ms=latency,
            risk_factors=factors,
            guardrail_triggered=triggered,
        )
    return DecisionResponse(
        transaction_id=str(transaction.TransactionID),
        amount=transaction.TransactionAmt,
        fraud_risk_score=round(score, 6),
        action=action,
        action_reason=action_reason,
        risk_factors=factors,
        guardrail_triggered=triggered,
        guardrail_rule=rule_id if triggered else None,
        friction_budget=policy.friction_budget,
        thresholds=ThresholdsInfo(**thresholds),
        inference_mode=inference_mode,
        latency_ms=round(latency, 2),
        timestamp=now,
        provided_feature_count=provided_feature_count,
        expected_feature_count=expected_feature_count,
        input_coverage=round(input_coverage, 4),
        score_calibrated=False,
        recommended_action=recommended_action,
        input_quality_note=quality_note,
    )
