import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert data["status"] == "operational"
    assert "endpoints" in data

def test_health_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["models_loaded"] >= 1

def test_get_policy_endpoint():
    response = client.get("/api/v1/policy")
    assert response.status_code == 200
    data = response.json()
    assert "friction_budget" in data
    assert "stepup_threshold" in data
    assert "block_threshold" in data

def test_score_single_transaction_allow():
    payload = {
        "TransactionID": "TEST-ALLOW-01",
        "TransactionAmt": 35.0,
        "ProductCD": "W",
        "card1": 1040,
        "card4": "visa",
        "card6": "debit",
        "P_emaildomain": "gmail.com",
    }
    response = client.post("/api/v1/score?mode=fast", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["transaction_id"] == "TEST-ALLOW-01"
    assert 0.0 <= data["fraud_risk_score"] <= 1.0
    assert data["action"] in ["ALLOW", "STEP-UP", "BLOCK"]
    assert "action_reason" in data
    assert "latency_ms" in data

def test_score_batch():
    payload = {
        "transactions": [
            {"TransactionID": "BATCH-1", "TransactionAmt": 25.0, "card4": "visa"},
            {"TransactionID": "BATCH-2", "TransactionAmt": 500.0, "card4": "mastercard"},
            {"TransactionID": "BATCH-3", "TransactionAmt": 2500.0, "card4": "discover"}
        ],
        "inference_mode": "fast"
    }
    response = client.post("/api/v1/score/batch", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["total_evaluated"] == 3
    assert len(data["decisions"]) == 3
    assert "action_counts" in data
    assert "total_amount_usd" in data

def test_policy_recalibration():
    # Recalibrate to 1.5% friction budget
    payload = {"friction_budget": 0.015, "block_threshold": 0.88}
    response = client.post("/api/v1/policy/recalibrate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["current"]["friction_budget"] == 0.015
    assert data["current"]["block_threshold"] == 0.88
    assert data["requires_retraining"] is False

    # Reset back to 2%
    client.post("/api/v1/policy/recalibrate", json={"friction_budget": 0.02, "block_threshold": 0.85})

def test_metrics_endpoint():
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "total_transactions" in data
    assert "action_distribution" in data
    assert "friction_compliance" in data

def test_stress_test_endpoint():
    payload = {
        "sample_size": 50,
        "from_budget": 0.02,
        "to_budget": 0.01,
        "block_threshold": 0.85
    }
    response = client.post("/api/v1/stress-test", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["transactions_evaluated"] == 50
    assert "baseline_policy" in data
    assert "target_policy" in data
    assert "action_shift" in data
    assert data["requires_retraining"] is False
    assert data["requires_restart"] is False

def test_samples_endpoint():
    response = client.get("/api/v1/samples")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

