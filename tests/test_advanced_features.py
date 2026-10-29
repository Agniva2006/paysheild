import pytest
from fastapi.testclient import TestClient
from main import app
from engine.telemetry import get_telemetry_monitor

client = TestClient(app)

def test_explainable_ai_risk_factors():
    """Verify that transactions return actionable XAI risk drivers."""
    payload = {
        "TransactionID": "TEST-XAI-01",
        "TransactionAmt": 12500.0,
        "ProductCD": "W",
        "card1": 1040,
        "card4": "visa",
        "P_emaildomain": "protonmail.com",
        "C1": 12.0,
    }
    response = client.post("/api/v1/score", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "risk_factors" in data
    assert isinstance(data["risk_factors"], list)
    assert len(data["risk_factors"]) >= 1
    factors_str = " ".join(data["risk_factors"]).lower()
    assert "amount" in factors_str or "email" in factors_str or "velocity" in factors_str

def test_pre_ml_guardrail_blacklisted_card():
    """Verify that blacklisted card triggers instant hard block before ML."""
    payload = {
        "TransactionID": "TEST-GUARDRAIL-CARD",
        "TransactionAmt": 45.0,
        "card1": 9999,  # In stolen card blacklist
    }
    response = client.post("/api/v1/score", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "BLOCK"
    assert data["guardrail_triggered"] is True
    assert data["guardrail_rule"] == "RULE_BLACKLISTED_CARD_BIN"
    assert data["inference_mode"] == "guardrail_pre_ml"

def test_pre_ml_guardrail_extreme_amount():
    """Verify that extreme amount triggers instant hard block."""
    payload = {
        "TransactionID": "TEST-GUARDRAIL-AMT",
        "TransactionAmt": 100000.0,  # Exceeds $50,000 ceiling
    }
    response = client.post("/api/v1/score", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "BLOCK"
    assert data["guardrail_triggered"] is True
    assert data["guardrail_rule"] == "RULE_AMOUNT_CEILING_BREACH"

def test_step_up_verification_loop():
    """Verify end-to-end OTP challenge resolution."""
    txn_id = "TXN-STEPUP-TEST-99"

    # 1. Trigger Step-Up by scoring a medium risk transaction
    payload = {
        "TransactionID": txn_id,
        "TransactionAmt": 850.0,
        "card1": 4461,
        "card4": "mastercard",
        "P_emaildomain": "anonymous.com",
        "C1": 7.0,
    }
    score_resp = client.post("/api/v1/score", json=payload)
    assert score_resp.status_code == 200

    # 2. Test invalid OTP -> Challenge Failed
    fail_verify = client.post("/api/v1/step-up/verify", json={"transaction_id": txn_id, "otp_code": "000000"})
    assert fail_verify.status_code == 200
    fail_data = fail_verify.json()
    assert fail_data["action"] == "CHALLENGE_FAILED"
    assert fail_data["status"] == "FAILED"

    # 3. Test valid OTP -> Verified Allow
    success_verify = client.post("/api/v1/step-up/verify", json={"transaction_id": txn_id, "otp_code": "123456"})
    assert success_verify.status_code == 200
    succ_data = success_verify.json()
    assert succ_data["action"] == "VERIFIED_ALLOW"
    assert succ_data["status"] == "SUCCESS"

def test_realtime_subscriber_broadcast():
    """Verify that telemetry dispatches live events to subscriber queues."""
    telemetry = get_telemetry_monitor()
    queue = telemetry.subscribe()
    try:
        # Score a transaction
        client.post("/api/v1/score", json={"TransactionAmt": 75.0, "card4": "visa"})
        
        # Verify event was delivered to queue
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["type"] == "NEW_TRANSACTION"
        assert event["amount"] == 75.0
    finally:
        telemetry.unsubscribe(queue)
