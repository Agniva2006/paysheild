import os
import sqlite3
from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from api.authentication import get_current_user, normalize_email, normalize_domain, require_merchant_integration
from api.schemas import AuthResponse, LoginRequest, MerchantReportRequest, MerchantTransactionRequest, RegisterRequest, TransactionInput, URLAssessmentRequest
from engine import prototype_store
from engine.url_assessor import assess_url

router = APIRouter()


@router.get("/health")
async def health():
    from engine.model_loader import get_model_container
    model = get_model_container()
    database_ready = prototype_store.store_is_ready()
    payload = {
        "status": "healthy" if model.is_ready and database_ready else "not_ready",
        "url_assessment": True,
        "transaction_model_ready": model.is_ready,
        "database_ready": database_ready,
    }
    return JSONResponse(status_code=200 if model.is_ready and database_ready else 503, content=payload)


@router.post("/auth/register", response_model=AuthResponse, status_code=201)
async def register(req: RegisterRequest):
    try:
        user = prototype_store.register_user(normalize_email(req.email), req.password)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "An account with this email already exists.")
    return AuthResponse(access_token=prototype_store.create_session(user["id"]), user=user)


@router.post("/auth/login", response_model=AuthResponse)
async def login(req: LoginRequest):
    user = prototype_store.authenticate(normalize_email(req.email), req.password)
    if not user:
        raise HTTPException(401, "Email or password is incorrect.")
    return AuthResponse(access_token=prototype_store.create_session(user["id"]), user=user)


@router.get("/auth/me")
async def me(user: Dict[str, Any] = Depends(get_current_user)):
    return {key: user[key] for key in ("id", "email", "plan")}


@router.post("/auth/logout")
async def logout(request: Request, user: Dict[str, Any] = Depends(get_current_user)):
    authorization = request.headers.get("Authorization", "")
    prototype_store.delete_session(authorization.removeprefix("Bearer "))
    return {"status": "signed_out"}


@router.post("/assessments/url")
async def assess_payment_url(req: URLAssessmentRequest, user: Dict[str, Any] = Depends(get_current_user)):
    try:
        assessment = await run_in_threadpool(assess_url, req.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    assessment_id = prototype_store.save_url_assessment(user["id"], req.url, assessment)
    reports = prototype_store.get_merchant_reports(assessment["domain"])
    return {
        "assessment_id": assessment_id,
        "url_assessment": assessment,
        "recommendations": assessment["recommendations"],
        "merchant_reports": reports,
        "transaction_model": {
            "status": "awaiting_merchant_integration",
            "fraud_risk_score": None,
            "action": None,
            "message": "The IEEE-CIS model requires transaction features from a payment or merchant integration. A URL alone cannot produce a transaction score.",
        },
    }


@router.post("/integrations/transactions/score")
async def score_integrated_transaction(
    req: MerchantTransactionRequest,
    _integration: None = Depends(require_merchant_integration),
):
    owner = prototype_store.get_assessment_owner(req.assessment_id)
    if owner is None:
        raise HTTPException(404, "URL assessment not found.")
    from api.transaction_scoring import score_transaction
    try:
        decision = await score_transaction(req.transaction, req.inference_mode)
    except HTTPException:
        raise
    result = decision.model_dump()
    prototype_store.attach_transaction_result(req.assessment_id, result)
    return {"assessment_id": req.assessment_id, "transaction_model": result}


@router.post("/assessments/{assessment_id}/transaction-score")
async def score_user_transaction(
    assessment_id: str,
    transaction: TransactionInput,
    user: Dict[str, Any] = Depends(get_current_user),
):
    if not prototype_store.get_user_assessment(user["id"], assessment_id):
        raise HTTPException(404, "Assessment not found for this account.")
    from api.transaction_scoring import score_transaction
    try:
        decision = await score_transaction(transaction, "ensemble", input_source="manual")
    except Exception:
        raise HTTPException(503, "IEEE-CIS model scoring is unavailable. Check that the model artifacts are loaded and the transaction amount is valid.")
    result = decision.model_dump()
    result["model_input_note"] = result.get("input_quality_note")
    prototype_store.attach_transaction_result(assessment_id, result)
    return {"assessment_id": assessment_id, "transaction_model": result}


@router.get("/assessments/history")
async def history(limit: int = 25, user: Dict[str, Any] = Depends(get_current_user)):
    if not 1 <= limit <= 100:
        raise HTTPException(422, "Limit must be between 1 and 100.")
    effective_limit = min(limit, 5) if user["plan"] != "premium" else limit
    return {"items": prototype_store.get_url_assessments(user["id"], effective_limit), "limit": effective_limit}


@router.get("/assessments/{assessment_id}")
async def assessment_detail(assessment_id: str, user: Dict[str, Any] = Depends(get_current_user)):
    assessment = prototype_store.get_url_assessment(user["id"], assessment_id)
    if assessment is None:
        raise HTTPException(404, "Assessment not found.")
    return assessment


@router.post("/merchant-reports", status_code=201)
async def merchant_report(req: MerchantReportRequest, user: Dict[str, Any] = Depends(get_current_user)):
    domain = normalize_domain(req.domain)
    report = prototype_store.save_merchant_report(user["id"], domain, req.category, req.note.strip())
    return {"report": report, "verified": False, "notice": "User-submitted reports are unverified."}


@router.get("/billing/subscription")
async def subscription(user: Dict[str, Any] = Depends(get_current_user)):
    return prototype_store.get_subscription(user["id"])


@router.post("/billing/checkout-session")
async def create_checkout(user: Dict[str, Any] = Depends(get_current_user)):
    secret, price = os.getenv("STRIPE_SECRET_KEY"), os.getenv("STRIPE_PREMIUM_PRICE_ID")
    if not secret or not price:
        raise HTTPException(503, "Billing is not configured. Set STRIPE_SECRET_KEY and STRIPE_PREMIUM_PRICE_ID.")
    import stripe
    stripe.api_key = secret
    base = os.getenv("PAYSHIELD_APP_URL", "http://localhost:8000").rstrip("/")
    customer_id = prototype_store.get_billing_customer(user["id"])
    customer_fields = {"customer": customer_id} if customer_id else {"customer_email": user["email"]}
    session = stripe.checkout.Session.create(
        mode="subscription", line_items=[{"price": price, "quantity": 1}],
        success_url=f"{base}/app?billing=success", cancel_url=f"{base}/app?billing=cancel",
        client_reference_id=user["id"], **customer_fields,
        subscription_data={"metadata": {"user_id": user["id"]}},
    )
    return {"url": session.url}


@router.post("/billing/portal-session")
async def create_portal(user: Dict[str, Any] = Depends(get_current_user)):
    secret = os.getenv("STRIPE_SECRET_KEY")
    if not secret:
        raise HTTPException(503, "Billing is not configured.")
    customer = prototype_store.get_subscription(user["id"])
    if not customer["billing_portal_available"]:
        raise HTTPException(409, "No billing account is linked yet.")
    import stripe
    stripe.api_key = secret
    with sqlite3.connect(prototype_store.DB_PATH) as conn:
        row = conn.execute("SELECT stripe_customer_id FROM users WHERE id=?", (user["id"],)).fetchone()
    session = stripe.billing_portal.Session.create(customer=row[0], return_url=os.getenv("PAYSHIELD_APP_URL", "http://localhost:8000") + "/app")
    return {"url": session.url}


@router.post("/billing/webhook")
async def billing_webhook(request: Request, stripe_signature: str | None = Header(default=None, alias="Stripe-Signature")):
    secret, signing_secret = os.getenv("STRIPE_SECRET_KEY"), os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret or not signing_secret:
        raise HTTPException(503, "Billing webhook is not configured.")
    import stripe
    stripe.api_key = secret
    try:
        event = stripe.Webhook.construct_event(await request.body(), stripe_signature, signing_secret)
    except Exception:
        raise HTTPException(400, "Invalid billing webhook signature or payload.")
    if not prototype_store.record_billing_event(event.id, event.type):
        return {"received": True}
    obj = event.data.object
    if event.type == "checkout.session.completed":
        user_id = obj.get("client_reference_id")
        if user_id:
            prototype_store.set_billing_customer(user_id, obj.get("customer"))
            sub_id = obj.get("subscription")
            if sub_id:
                sub = stripe.Subscription.retrieve(sub_id)
                prototype_store.update_subscription(user_id, obj.get("customer"), sub_id, sub.status)
    elif event.type in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
        metadata = obj.get("metadata") or {}
        user_id = metadata.get("user_id")
        if not user_id:
            user = prototype_store.get_user_by_customer(obj.get("customer"))
            user_id = user["id"] if user else None
        if user_id:
            prototype_store.update_subscription(user_id, obj.get("customer"), obj.get("id"), obj.get("status", "canceled"))
    return {"received": True}
