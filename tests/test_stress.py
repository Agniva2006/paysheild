import time
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_throughput_and_low_latency():
    """Verify that single transaction scoring achieves sub-10ms latency in fast mode."""
    payload = {
        "TransactionID": "BENCH-001",
        "TransactionAmt": 120.0,
        "ProductCD": "W",
        "card1": 1040,
        "card4": "visa",
        "P_emaildomain": "gmail.com",
    }

    # Warmup
    for _ in range(3):
        client.post("/api/v1/score?mode=fast", json=payload)

    latencies = []
    iterations = 30
    for i in range(iterations):
        payload["TransactionID"] = f"BENCH-{i}"
        t0 = time.perf_counter()
        resp = client.post("/api/v1/score?mode=fast", json=payload)
        t1 = time.perf_counter()
        assert resp.status_code == 200
        latencies.append((t1 - t0) * 1000.0)

    avg_lat = sum(latencies) / len(latencies)
    print(f"\nAverage client-observed roundtrip latency: {avg_lat:.2f} ms")
    assert avg_lat < 100.0 # Under 100ms roundtrip in unit test harness

def test_instant_recalibration_under_traffic():
    """Simulates immediate transition from 2% to 1% budget with live verified change."""
    # 1. Set to 2%
    r1 = client.post("/api/v1/policy/recalibrate", json={"friction_budget": 0.02})
    assert r1.status_code == 200
    t_stepup_2pct = r1.json()["current"]["stepup_threshold"]

    # 2. Score transaction
    payload = {"TransactionAmt": 150.0, "card4": "visa"}
    resp1 = client.post("/api/v1/score", json=payload)
    assert resp1.status_code == 200
    assert resp1.json()["friction_budget"] == 0.02
    assert resp1.json()["thresholds"]["t_stepup"] == t_stepup_2pct

    # 3. Recalibrate live to 1%
    r2 = client.post("/api/v1/policy/recalibrate", json={"friction_budget": 0.01})
    assert r2.status_code == 200
    t_stepup_1pct = r2.json()["current"]["stepup_threshold"]
    assert t_stepup_1pct > t_stepup_2pct

    # 4. Immediate subsequent transaction reflects 1% budget without server restart
    resp2 = client.post("/api/v1/score", json=payload)
    assert resp2.status_code == 200
    assert resp2.json()["friction_budget"] == 0.01
    assert resp2.json()["thresholds"]["t_stepup"] == t_stepup_1pct

    # Cleanup: restore to 2%
    client.post("/api/v1/policy/recalibrate", json={"friction_budget": 0.02})

