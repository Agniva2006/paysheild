import time
import json
import logging
import asyncio
import datetime
import numpy as np
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from config import (
    DEFAULT_INFERENCE_MODE,
    SAMPLE_TRANSACTIONS_PATH,
)
from engine.model_loader import get_model_container
from engine.feature_pipeline import get_feature_pipeline
from engine.policy_engine import get_policy_engine
from engine.telemetry import get_telemetry_monitor
from engine.explainer import RiskExplainer
from engine.guardrails import get_guardrails
from api.schemas import (
    TransactionInput,
    BatchTransactionRequest,
    DecisionResponse,
    BatchDecisionResponse,
    ThresholdsInfo,
    PolicyConfigResponse,
    PolicyRecalibrateRequest,
    PolicyRecalibrateResponse,
    StressTestRequest,
    StressTestResponse,
    StepUpVerifyRequest,
    StepUpVerifyResponse,
)

logger = logging.getLogger("payshield.api.routes")
router = APIRouter()

@router.post(
    "/score",
    response_model=DecisionResponse,
    summary="Evaluate single transaction",
    description="Processes incoming transaction, executes pre-ML guardrails, computes calibrated fraud risk, applies dynamic policy, and outputs explainable risk factors."
)
async def score_transaction(
    transaction: TransactionInput,
    mode: str = Query(DEFAULT_INFERENCE_MODE, pattern="^(fast|ensemble)$", description="Inference mode")
):
    try:
        pipeline = get_feature_pipeline()
        model_container = get_model_container()
        policy_engine = get_policy_engine()
        telemetry = get_telemetry_monitor()
        guardrails = get_guardrails()

        raw_dict = transaction.to_raw_dict()
        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        thresholds = {
            "t_stepup": policy_engine.stepup_threshold,
            "t_block": policy_engine.block_threshold,
        }

        # 1. Pre-ML Guardrails Check (Deterministic Defense-in-Depth)
        triggered, rule_id, rule_reason = guardrails.evaluate(raw_dict)
        if triggered:
            telemetry.record_decision(
                transaction_id=str(transaction.TransactionID),
                amount=transaction.TransactionAmt,
                score=1.0,
                action="BLOCK",
                thresholds=thresholds,
                friction_budget=policy_engine.friction_budget,
                latency_ms=0.5,
                risk_factors=[rule_reason],
                guardrail_triggered=True,
            )
            return DecisionResponse(
                transaction_id=str(transaction.TransactionID),
                amount=transaction.TransactionAmt,
                fraud_risk_score=1.0,
                action="BLOCK",
                action_reason=f"Pre-ML Hard Rule Block: {rule_reason}",
                risk_factors=[rule_reason],
                guardrail_triggered=True,
                guardrail_rule=rule_id,
                friction_budget=policy_engine.friction_budget,
                thresholds=ThresholdsInfo(**thresholds),
                inference_mode="guardrail_pre_ml",
                latency_ms=0.5,
                timestamp=now_ts,
            )

        # 2. Feature alignment & ML model inference
        features_df = pipeline.prepare_dataframe(raw_dict)
        probs, latency_ms = model_container.predict(features_df, mode=mode)
        score = float(probs[0])

        # 3. Dynamic policy decision
        action, reason = policy_engine.evaluate(score)

        # 4. Explainable AI (XAI) risk drivers
        risk_factors = RiskExplainer.explain_transaction(
            raw_data=raw_dict,
            score=score,
            action=action,
            t_stepup=policy_engine.stepup_threshold,
            t_block=policy_engine.block_threshold,
        )

        # 5. Telemetry feedback logging
        telemetry.record_decision(
            transaction_id=str(transaction.TransactionID),
            amount=transaction.TransactionAmt,
            score=score,
            action=action,
            thresholds=thresholds,
            friction_budget=policy_engine.friction_budget,
            latency_ms=latency_ms,
            risk_factors=risk_factors,
            guardrail_triggered=False,
        )

        return DecisionResponse(
            transaction_id=str(transaction.TransactionID),
            amount=transaction.TransactionAmt,
            fraud_risk_score=round(score, 6),
            action=action,
            action_reason=reason,
            risk_factors=risk_factors,
            guardrail_triggered=False,
            guardrail_rule=None,
            friction_budget=policy_engine.friction_budget,
            thresholds=ThresholdsInfo(**thresholds),
            inference_mode=mode,
            latency_ms=round(latency_ms, 2),
            timestamp=now_ts,
        )

    except Exception as e:
        logger.exception("Error scoring transaction")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post(
    "/score/batch",
    response_model=BatchDecisionResponse,
    summary="Batch evaluate transactions",
    description="Vectorized inference, guardrail checks, and policy evaluation for a batch of transactions."
)
async def score_batch_transactions(batch_req: BatchTransactionRequest):
    try:
        start_total = time.perf_counter()
        pipeline = get_feature_pipeline()
        model_container = get_model_container()
        policy_engine = get_policy_engine()
        telemetry = get_telemetry_monitor()
        guardrails = get_guardrails()

        raw_dicts = [t.to_raw_dict() for t in batch_req.transactions]
        features_df = pipeline.prepare_dataframe(raw_dicts)
        probs, inf_latency_ms = model_container.predict(features_df, mode=batch_req.inference_mode or "fast")

        decisions: List[DecisionResponse] = []
        action_counts = {"ALLOW": 0, "STEP-UP": 0, "BLOCK": 0}
        total_amt = 0.0

        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        per_item_lat = inf_latency_ms / max(len(raw_dicts), 1)

        thresholds = {
            "t_stepup": policy_engine.stepup_threshold,
            "t_block": policy_engine.block_threshold,
        }

        for txn, raw_d, score in zip(batch_req.transactions, raw_dicts, probs):
            s = float(score)
            total_amt += txn.TransactionAmt

            # Check guardrails
            triggered, rule_id, rule_reason = guardrails.evaluate(raw_d)
            if triggered:
                action = "BLOCK"
                reason = f"Pre-ML Hard Rule Block: {rule_reason}"
                risk_factors = [rule_reason]
                guardrail_trig = True
                guardrail_r = rule_id
                s = 1.0
            else:
                action, reason = policy_engine.evaluate(s)
                risk_factors = RiskExplainer.explain_transaction(
                    raw_data=raw_d,
                    score=s,
                    action=action,
                    t_stepup=policy_engine.stepup_threshold,
                    t_block=policy_engine.block_threshold,
                )
                guardrail_trig = False
                guardrail_r = None

            action_counts[action] += 1

            telemetry.record_decision(
                transaction_id=str(txn.TransactionID),
                amount=txn.TransactionAmt,
                score=s,
                action=action,
                thresholds=thresholds,
                friction_budget=policy_engine.friction_budget,
                latency_ms=per_item_lat,
                risk_factors=risk_factors,
                guardrail_triggered=guardrail_trig,
            )

            decisions.append(
                DecisionResponse(
                    transaction_id=str(txn.TransactionID),
                    amount=txn.TransactionAmt,
                    fraud_risk_score=round(s, 6),
                    action=action,
                    action_reason=reason,
                    risk_factors=risk_factors,
                    guardrail_triggered=guardrail_trig,
                    guardrail_rule=guardrail_r,
                    friction_budget=policy_engine.friction_budget,
                    thresholds=ThresholdsInfo(**thresholds),
                    inference_mode=batch_req.inference_mode or "fast",
                    latency_ms=round(per_item_lat, 2),
                    timestamp=now_ts,
                )
            )

        total_latency_ms = (time.perf_counter() - start_total) * 1000.0

        return BatchDecisionResponse(
            total_evaluated=len(decisions),
            decisions=decisions,
            action_counts=action_counts,
            total_amount_usd=round(total_amt, 2),
            summary_latency_ms=round(total_latency_ms, 2),
        )

    except Exception as e:
        logger.exception("Error in batch scoring")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post(
    "/step-up/verify",
    response_model=StepUpVerifyResponse,
    summary="Verify Step-Up secondary authentication (OTP)",
    description="Validates OTP for transactions flagged for STEP-UP, updating telemetry and releasing authorized transactions."
)
async def verify_stepup(req: StepUpVerifyRequest):
    telemetry = get_telemetry_monitor()
    res = telemetry.verify_stepup(transaction_id=req.transaction_id, otp_code=req.otp_code)
    return StepUpVerifyResponse(
        transaction_id=res["transaction_id"],
        amount=res.get("amount", 0.0),
        action=res["action"],
        status=res["status"],
        message=res["message"],
        timestamp=res["timestamp"],
    )

@router.get(
    "/stream",
    summary="Real-time transaction event stream (SSE)",
    description="Server-Sent Events endpoint pushing live transaction evaluations and policy updates to connected dashboards."
)
async def event_stream():
    telemetry = get_telemetry_monitor()
    queue = telemetry.subscribe()

    async def sse_generator():
        try:
            yield f"event: connected\ndata: {json.dumps({'message': 'PayShield Live Stream Connected'})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: transaction\ndata: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            telemetry.unsubscribe(queue)

    return StreamingResponse(sse_generator(), media_type="text/event-stream")

@router.get(
    "/policy",
    response_model=PolicyConfigResponse,
    summary="Get current operational policy",
    description="Returns current friction budget, calibrated T_stepup and T_block thresholds, and active decision rules."
)
async def get_policy():
    policy_engine = get_policy_engine()
    return PolicyConfigResponse(**policy_engine.get_state())

@router.post(
    "/policy/recalibrate",
    response_model=PolicyRecalibrateResponse,
    summary="Live friction-budget recalibration",
    description="Dynamically updates the friction budget (e.g. 2% -> 1%) and re-indexes thresholds immediately without model retraining or restart."
)
async def recalibrate_policy(req: PolicyRecalibrateRequest):
    try:
        policy_engine = get_policy_engine()
        telemetry = get_telemetry_monitor()

        result = policy_engine.recalibrate(
            new_budget=req.friction_budget,
            new_block_threshold=req.block_threshold,
        )
        telemetry.record_policy_change(result)
        return PolicyRecalibrateResponse(**result)

    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        logger.exception("Recalibration failure")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get(
    "/metrics",
    summary="Telemetry & closed-loop feedback metrics",
    description="Returns live transaction statistics, action breakdown, fraud dollars prevented, and friction budget compliance."
)
async def get_metrics():
    policy_engine = get_policy_engine()
    telemetry = get_telemetry_monitor()
    return telemetry.get_metrics(active_budget=policy_engine.friction_budget)

@router.get(
    "/telemetry/recent",
    summary="Recent transaction decisions stream",
    description="Returns the most recent decision events for live dashboard and telemetry monitors."
)
async def get_recent_telemetry(limit: int = Query(50, ge=1, le=500)):
    telemetry = get_telemetry_monitor()
    return telemetry.get_recent_events(limit=limit)

@router.post(
    "/telemetry/reset",
    summary="Reset telemetry counters",
    description="Resets all telemetry counters and recent events."
)
async def reset_telemetry():
    telemetry = get_telemetry_monitor()
    telemetry.reset()
    return {"status": "success", "message": "Telemetry metrics reset."}

@router.post(
    "/stress-test",
    response_model=StressTestResponse,
    summary="Mini stress test: 2% -> 1% friction budget transition",
    description="Demonstrates instant threshold recalibration (e.g. 2% to 1%) without model retraining or restart, reporting the resulting action distribution shift."
)
async def run_stress_test(req: StressTestRequest):
    start_t = time.perf_counter()
    policy_engine = get_policy_engine()

    np.random.seed(42)
    cohort_scores = np.concatenate([
        np.random.beta(0.5, 20.0, int(req.sample_size * 0.90)),
        np.random.uniform(0.10, 0.85, int(req.sample_size * 0.08)),
        np.random.uniform(0.85, 0.99, int(req.sample_size * 0.02)),
    ])
    np.random.shuffle(cohort_scores)

    # 1. Baseline Policy
    policy_engine.recalibrate(new_budget=req.from_budget, new_block_threshold=req.block_threshold)
    base_stepup_th = policy_engine.stepup_threshold
    base_actions = {"ALLOW": 0, "STEP-UP": 0, "BLOCK": 0}
    for s in cohort_scores:
        act, _ = policy_engine.evaluate(s)
        base_actions[act] += 1

    # 2. Target Policy
    policy_engine.recalibrate(new_budget=req.to_budget, new_block_threshold=req.block_threshold)
    target_stepup_th = policy_engine.stepup_threshold
    target_actions = {"ALLOW": 0, "STEP-UP": 0, "BLOCK": 0}
    for s in cohort_scores:
        act, _ = policy_engine.evaluate(s)
        target_actions[act] += 1

    base_stepup = base_actions["STEP-UP"]
    target_stepup = target_actions["STEP-UP"]
    reduction_pct = (
        round(((base_stepup - target_stepup) / max(base_stepup, 1)) * 100.0, 2)
        if base_stepup > 0 else 0.0
    )

    elapsed_ms = (time.perf_counter() - start_t) * 1000.0

    # Restore active policy state to baseline after stress test simulation
    policy_engine.recalibrate(new_budget=req.from_budget, new_block_threshold=req.block_threshold)

    return StressTestResponse(
        transactions_evaluated=len(cohort_scores),
        baseline_policy={
            "friction_budget": req.from_budget,
            "stepup_threshold": round(base_stepup_th, 6),
            "block_threshold": req.block_threshold,
            "action_counts": base_actions,
            "stepup_rate": round(base_actions["STEP-UP"] / len(cohort_scores), 4),
        },
        target_policy={
            "friction_budget": req.to_budget,
            "stepup_threshold": round(target_stepup_th, 6),
            "block_threshold": req.block_threshold,
            "action_counts": target_actions,
            "stepup_rate": round(target_actions["STEP-UP"] / len(cohort_scores), 4),
        },
        action_shift={
            "allow_change": target_actions["ALLOW"] - base_actions["ALLOW"],
            "stepup_change": target_actions["STEP-UP"] - base_actions["STEP-UP"],
            "block_change": target_actions["BLOCK"] - base_actions["BLOCK"],
        },
        stepup_reduction_pct=reduction_pct,
        requires_retraining=False,
        requires_restart=False,
        execution_time_ms=round(elapsed_ms, 2),
    )

@router.get(
    "/samples",
    summary="Pre-configured sample transactions",
    description="Returns verified sample transactions representing low-risk (ALLOW), medium-risk (STEP-UP), and high-risk (BLOCK) payment activities."
)
async def get_sample_transactions():
    try:
        with open(SAMPLE_TRANSACTIONS_PATH, "r", encoding="utf-8") as f:
            samples = json.load(f)
        return samples
    except Exception as e:
        logger.error(f"Error loading sample transactions: {e}")
        return []

@router.get(
    "/health",
    summary="Health check & system status",
    description="Returns backend readiness, model load status, and runtime details."
)
async def health_check():
    model_container = get_model_container()
    if not model_container.is_ready:
        model_container.load_models()
    policy_engine = get_policy_engine()

    return {
        "status": "healthy" if model_container.is_ready else "degraded",
        "models_loaded": len(model_container.models),
        "is_ready": model_container.is_ready,
        "load_duration_sec": round(model_container.load_duration_sec, 2),
        "policy_calibrated": policy_engine.is_calibrated,
        "fallback_active": policy_engine.fallback_active,
        "active_budget": policy_engine.friction_budget,
        "active_stepup_threshold": policy_engine.stepup_threshold,
    }
