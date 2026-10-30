import time
import json
import logging
import asyncio
import datetime
import uuid
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from fastapi import Header

from config import (
    DEFAULT_INFERENCE_MODE,
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
    RegisterRequest,
    LoginRequest,
    AuthResponse,
    URLAssessmentRequest,
    MerchantTransactionRequest,
    MerchantReportRequest,
)
from api.authentication import (
    bearer_scheme,
    get_current_user,
    normalize_email,
    normalize_domain,
    require_admin,
    require_merchant_integration,
)
from engine import prototype_store
from engine.url_assessor import assess_url
from api.transaction_scoring import score_transaction as score_transaction_model

logger = logging.getLogger("payshield.api.routes")
router = APIRouter()


@router.post(
    "/score",
    response_model=DecisionResponse,
    summary="Evaluate single transaction",
    description="Scores an IEEE-CIS transaction payload, reports input coverage, and applies the configured score threshold policy."
)
async def score_transaction(
    transaction: TransactionInput,
    mode: str = Query(DEFAULT_INFERENCE_MODE, pattern="^(fast|ensemble)$", description="Inference mode"),
    _integration: None = Depends(require_merchant_integration),
):
    try:
        return await score_transaction_model(transaction, mode, input_source="integration")
    except Exception as e:
        logger.exception("Error scoring transaction")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to score transaction. Check backend readiness and request fields.")

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
        guardrail_results = [guardrails.evaluate(raw_d) for raw_d in raw_dicts]
        clean_indices = [index for index, result in enumerate(guardrail_results) if not result[0]]
        probabilities_by_index: Dict[int, float] = {}
        contributions_by_index: Dict[int, list] = {}
        inf_latency_ms = 0.0
        if clean_indices:
            clean_rows = [raw_dicts[index] for index in clean_indices]
            features_df = pipeline.prepare_dataframe(clean_rows)
            clean_probs, inf_latency_ms = model_container.predict(
                features_df, mode=batch_req.inference_mode or "fast"
            )
            probabilities_by_index = {index: float(prob) for index, prob in zip(clean_indices, clean_probs)}
            explanations = model_container.explain_many(features_df, mode=batch_req.inference_mode or "fast")
            contributions_by_index = {index: row for index, row in zip(clean_indices, explanations)}

        decisions: List[DecisionResponse] = []
        action_counts = {"ALLOW": 0, "STEP-UP": 0, "BLOCK": 0}
        total_amt = 0.0

        now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        per_item_lat = inf_latency_ms / max(len(clean_indices), 1) if clean_indices else 0.0

        thresholds = {
            "t_stepup": policy_engine.stepup_threshold,
            "t_block": policy_engine.block_threshold,
        }

        for index, (txn, raw_d) in enumerate(zip(batch_req.transactions, raw_dicts)):
            triggered, rule_id, rule_reason = guardrail_results[index]
            s = 1.0 if triggered else probabilities_by_index[index]
            total_amt += txn.TransactionAmt

            if triggered:
                action = "BLOCK"
                reason = f"Pre-ML Hard Rule Block: {rule_reason}"
                risk_factors = [rule_reason]
                guardrail_trig = True
                guardrail_r = rule_id
                s = 1.0
            else:
                action, reason = policy_engine.evaluate(s)
                risk_factors = RiskExplainer.explain_model_contributions(contributions_by_index.get(index, []))
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
                    provided_feature_count=sum(1 for key, value in raw_d.items() if key in set(pipeline.feature_names) and value is not None),
                    expected_feature_count=len(pipeline.feature_names),
                    input_coverage=(sum(1 for key, value in raw_d.items() if key in set(pipeline.feature_names) and value is not None) / len(pipeline.feature_names)) if pipeline.feature_names else 0.0,
                    score_calibrated=False,
                    recommended_action=action,
                    input_quality_note="Raw LightGBM score; missing features remain missing. The legacy artifact lacks full encoder and group-statistic metadata.",
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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to score this batch. Check backend readiness and request fields.")

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
async def recalibrate_policy(req: PolicyRecalibrateRequest, _admin: None = Depends(require_admin)):
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
async def reset_telemetry(_admin: None = Depends(require_admin)):
    telemetry = get_telemetry_monitor()
    telemetry.reset()
    return {"status": "success", "message": "Telemetry metrics reset."}

@router.post(
    "/stress-test",
    response_model=StressTestResponse,
    summary="Mini stress test: 2% -> 1% friction budget transition",
    description="Demonstrates instant threshold recalibration (e.g. 2% to 1%) without model retraining or restart, reporting the resulting action distribution shift."
)
async def run_stress_test(req: StressTestRequest, _admin: None = Depends(require_admin)):
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
        "database_ready": prototype_store.store_is_ready(),
    }


@router.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register_account(req: RegisterRequest):
    """Create a demo account. Passwords are salted and hashed before storage."""
    email = normalize_email(req.email)
    try:
        user = prototype_store.register_user(email, req.password)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")
    token = prototype_store.create_session(user["id"])
    return AuthResponse(access_token=token, user=user)


@router.post("/auth/login", response_model=AuthResponse)
async def login_account(req: LoginRequest):
    user = prototype_store.authenticate(normalize_email(req.email), req.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect.")
    token = prototype_store.create_session(user["id"])
    return AuthResponse(access_token=token, user=user)


@router.get("/auth/me")
async def get_account(user: Dict[str, Any] = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "plan": user["plan"]}


@router.post("/auth/logout")
async def logout_account(
    credentials=Depends(bearer_scheme),
    user: Dict[str, Any] = Depends(get_current_user),
):
    prototype_store.delete_session(credentials.credentials)
    return {"status": "success", "message": "Signed out."}


@router.get("/subscription")
async def subscription_status(user: Dict[str, Any] = Depends(get_current_user)):
    return prototype_store.get_subscription(user["id"])


@router.post("/subscription/demo-upgrade")
async def demo_upgrade(user: Dict[str, Any] = Depends(get_current_user)):
    """Demo-only plan switch; this endpoint does not collect payment details."""
    return prototype_store.set_demo_premium(user["id"])


@router.get("/demo/merchants")
async def demo_merchants():
    registry = _load_demo_merchants()
    return [
        {"domain": domain, "display_name": record["display_name"], "risk_level": record["risk_level"], "demo_data": True}
        for domain, record in registry.items()
    ]


@router.post("/demo/checkout")
async def demo_checkout(
    req: DemoCheckoutRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Score a bundled IEEE-CIS example through a simulated merchant checkout."""
    domain = normalize_domain(req.merchant_domain)
    with open(SAMPLE_TRANSACTIONS_PATH, "r", encoding="utf-8") as file:
        samples = json.load(file)
    sample = next((item for item in samples if item.get("id") == req.scenario_id), None)
    if sample is None:
        raise HTTPException(status_code=404, detail="The selected demo scenario was not found.")

    transaction_payload = dict(sample["data"])
    transaction_id = f"DEMO-{uuid.uuid4().hex[:10].upper()}"
    transaction_payload["TransactionID"] = transaction_id
    transaction = TransactionInput.model_validate(transaction_payload)
    decision = await score_transaction(transaction, mode=req.inference_mode)
    decision_data = decision.model_dump()
    merchant = _merchant_assessment(domain)
    assessment_id = str(uuid.uuid4())

    result = {
        "assessment_id": assessment_id,
        "scenario_id": req.scenario_id,
        "scenario_description": sample.get("description", "Bundled demo transaction"),
        "merchant": merchant,
        "transaction": decision_data,
        "recommendation": {
            "ALLOW": "The transaction model did not flag this demo transaction. Review the merchant information before proceeding.",
            "STEP-UP": "The model recommends an additional verification step in this simulated checkout.",
            "BLOCK": "The model or a deterministic rule flagged this demo transaction. The simulated checkout is stopped.",
        }[decision_data["action"]],
        "checkout_simulation": "NOT_CHARGED",
        "step_up": ({
            "required": True,
            "assessment_id": assessment_id,
            "demo_code": "123456",
            "expires_in_minutes": 10,
        } if decision_data["action"] == "STEP-UP" else {"required": False}),
        "demo_data": True,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    prototype_store.save_assessment(
        assessment_id=assessment_id,
        user_id=user["id"],
        merchant_domain=domain,
        scenario=req.scenario_id,
        transaction_id=transaction_id,
        model_score=decision_data["fraud_risk_score"],
        action=decision_data["action"],
        response=result,
    )
    if decision_data["action"] == "STEP-UP":
        prototype_store.create_demo_challenge(assessment_id, user["id"], transaction_id)
    return result


@router.post("/demo/step-up/verify")
async def verify_demo_step_up(
    req: DemoStepUpVerifyRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    outcome = prototype_store.verify_demo_challenge(req.assessment_id, user["id"], req.otp_code)
    if outcome["status"] == "NOT_FOUND":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No pending challenge exists for this assessment.")
    messages = {
        "VERIFIED": "Demo verification passed. The simulated transaction is marked as verified; no payment was made.",
        "PENDING": "That demo code is incorrect. You can try again.",
        "FAILED": "The demo challenge is locked after three incorrect attempts.",
        "EXPIRED": "The demo challenge expired. Run checkout again to create a new challenge.",
    }
    return {
        "assessment_id": req.assessment_id,
        "transaction_id": outcome.get("transaction_id"),
        "status": outcome["status"],
        "verified": outcome["status"] == "VERIFIED",
        "attempts_remaining": outcome.get("attempts_remaining", 0),
        "message": messages[outcome["status"]],
        "demo_only": True,
    }


@router.get("/assessments/history")
async def assessment_history(
    limit: int = Query(25, ge=1, le=100),
    user: Dict[str, Any] = Depends(get_current_user),
):
    # Free accounts get a small recent-history view; Premium is simulated.
    effective_limit = min(limit, 5) if user["plan"] != "premium" else limit
    return {
        "items": prototype_store.get_assessments(user["id"], effective_limit),
        "limit": effective_limit,
        "plan": user["plan"],
        "premium_demo": user["plan"] != "premium",
    }


@router.post("/merchant-reports", status_code=status.HTTP_201_CREATED)
async def submit_merchant_report(
    req: MerchantReportRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    domain = normalize_domain(req.merchant_domain)
    report = prototype_store.save_merchant_report(user["id"], domain, req.category, req.note.strip())
    report["verified"] = False
    return {"status": "received", "report": report,
            "notice": "This user-submitted report is unverified and is not proof of fraud."}
