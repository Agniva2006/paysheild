# PayShield Backend: Constraint-Aware Fraud Intelligence API

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![LightGBM](https://img.shields.io/badge/LightGBM-4.0+-brightgreen.svg)](https://lightgbm.readthedocs.io/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/Tests-23%20Passed-success.svg)](file:///tests)

> **"Detect fraud without turning every suspicious transaction into customer friction."**  
> *Developed for SYNAPSE 1.0 (IEEE SMC KGEC × IEEE Kolkata Section) | Problem Statement: PS-02 (SYN26-CS02)*

---

## 📖 Table of Contents
1. [Overview & Core Value](#-overview--core-value)
2. [Project File Structure](#-project-file-structure)
3. [Quick Start (Local & Docker)](#-quick-start)
4. [API Specification & Endpoints](#-api-specification--endpoints)
5. [Frontend Developer Integration Guide](#-frontend-developer-integration-guide)
6. [Interactive Swagger UI & Sample Data](#-interactive-swagger-ui--sample-data)
7. [Running Automated Tests](#-running-automated-tests)
8. [Architecture Deep-Dive](#-architecture-deep-dive)

---

## 💡 Overview & Core Value

Conventional fraud detection is broken: binary allow/deny rules reject genuine customers, while static thresholds cannot adapt to changing business constraints.

**PayShield** is an enterprise-grade payment decision engine that:
1. **Substitutes Binary Blocking with a 3-Action Space**: `ALLOW` (low risk), `STEP-UP` (medium risk OTP challenge), and `BLOCK` (high risk).
2. **Guarantees Customer Friction Budgets**: Dynamically calculates the Step-Up threshold:
   $$T_{stepup}(B) = \text{Quantile}_{1-B}(S_{legit})$$
   ensuring that legitimate customer friction never exceeds configured budget $B$ (e.g. $2\%$).
3. **Zero-Retraining Live Recalibration**: Risk teams can update the friction budget live (e.g. $2\% \to 1\%$) in microseconds without retraining the ML model or restarting the server.
4. **Hybrid Defense-in-Depth**: Pre-ML deterministic guardrails (stolen card blacklists, amount ceilings, replay flood locks) combined with a 5-fold LightGBM ensemble.
5. **Explainable AI (XAI)**: Returns top observable risk drivers (`risk_factors`) for every evaluated transaction.
6. **Closed-Loop Resolution**: End-to-end OTP verification via `POST /api/v1/step-up/verify`.
7. **Real-time Live Streaming**: Server-Sent Events (SSE) stream (`GET /api/v1/stream`) to power real-time dashboard visualizations.

---

## 📂 Project File Structure

```
paysheild/
│
├── api/                         # REST API Layer
│   ├── routes.py                # Endpoints (/score, /step-up/verify, /stream, /policy, /metrics)
│   └── schemas.py               # Pydantic models for validation and responses
│
├── engine/                      # Core Intelligence & Business Logic
│   ├── model_loader.py          # Memory-cached 5-fold LightGBM inference container
│   ├── feature_pipeline.py      # Feature engineering (Amt ratios, D15, email domain splits)
│   ├── policy_engine.py         # Dynamic threshold engine & quantile calibration
│   ├── explainer.py             # Explainable AI (XAI) risk drivers extractor
│   ├── guardrails.py            # Pre-ML deterministic rule guardrails (blacklists, velocity)
│   └── telemetry.py             # Telemetry monitor, OTP challenge tracking & SSE event dispatch
│
├── data/                        # Model & Calibration Metadata
│   ├── feature_names.json       # 369 LightGBM feature names in exact model order
│   ├── calibration_stats.json   # Quantiles calculated from 590,540 out-of-fold predictions
│   └── sample_transactions.json # Verified low-risk, step-up, and block test transactions
│
├── training/                    # Model Training Pipeline & Artifacts
│   ├── train.py                 # Modular 5-fold CV LightGBM training script
│   ├── ieee_fraud_detection_colab.ipynb # Original research and experiment notebook
│   └── submission.csv           # Benchmark test predictions
│
├── docs/                        # Project Documentation
│   └── SYNAPSE_1.0.pdf          # Official hackathon presentation deck
│
├── tests/                       # Test Suite (23 automated tests)
│   ├── test_model_inference.py  # Model inference and pipeline unit tests
│   ├── test_policy_engine.py    # Policy evaluation & 2% -> 1% recalibration tests
│   ├── test_api_endpoints.py    # FastAPI endpoint integration tests
│   ├── test_stress.py           # Stress tests & latency benchmarking
│   └── test_advanced_features.py# Guardrails, XAI, OTP verify, and real-time streaming tests
│
├── ARCHITECTURE.md              # Deep-dive architectural whitepaper
├── config.py                    # Global system configuration and thresholds
├── main.py                      # FastAPI application entrypoint with CORS & lifespan warmup
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Production Docker container definition
├── docker-compose.yml           # Multi-platform container deployment
└── README.md                    # System documentation and developer guide
```

---

## 🚀 Quick Start

### Option 1: Run Locally

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the backend server:**
   ```bash
   python main.py
   ```
   *Or with Uvicorn:*
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. **Verify:** Open your browser to [http://localhost:8000/docs](http://localhost:8000/docs).

---

### Option 2: Run with Docker

```bash
docker compose up --build
```
The API is available at `http://localhost:8000`.

---

## 📡 API Specification & Endpoints

### 1. Evaluate Transaction
`POST /api/v1/score?mode=fast`

Evaluates an incoming payment, executes pre-ML guardrails, computes calibrated fraud probability, applies dynamic policy, and returns top risk drivers.

#### Request Payload:
```json
{
  "TransactionID": "TXN-98421",
  "TransactionAmt": 150.00,
  "ProductCD": "W",
  "card1": 1040,
  "card4": "visa",
  "card6": "debit",
  "addr1": 315.0,
  "addr2": 87.0,
  "P_emaildomain": "gmail.com",
  "DeviceType": "desktop"
}
```

#### Response Payload (`ALLOW`):
```json
{
  "transaction_id": "TXN-98421",
  "amount": 150.0,
  "fraud_risk_score": 0.004871,
  "action": "ALLOW",
  "action_reason": "Calibrated fraud risk (0.0049) < step-up threshold (0.7695). Transaction clears risk tolerance; seamless checkout permitted.",
  "risk_factors": [
    "Standard transaction amount within customer baseline",
    "Verified device identity and consistent geolocation"
  ],
  "guardrail_triggered": false,
  "guardrail_rule": null,
  "friction_budget": 0.02,
  "thresholds": {
    "t_stepup": 0.769512,
    "t_block": 0.85
  },
  "inference_mode": "fast",
  "latency_ms": 2.34,
  "timestamp": "2026-10-29T19:20:00Z"
}
```

---

### 2. Verify Step-Up Challenge (OTP)
`POST /api/v1/step-up/verify`

Resolves secondary authentication when a transaction received `STEP-UP`.

#### Request Payload:
```json
{
  "transaction_id": "TXN-98421",
  "otp_code": "123456"
}
```

#### Response Payload:
```json
{
  "transaction_id": "TXN-98421",
  "amount": 150.0,
  "action": "VERIFIED_ALLOW",
  "status": "SUCCESS",
  "message": "OTP verification successful. Transaction released and authorized.",
  "timestamp": "2026-10-29T19:20:15Z"
}
```
*(Demo OTP code is `123456`).*

---

### 3. Real-Time Event Stream (Server-Sent Events)
`GET /api/v1/stream`

Push connection emitting live transaction events and policy recalibrations in real time.

```
event: transaction
data: {"type": "NEW_TRANSACTION", "transaction_id": "TXN-98421", "amount": 150.0, "action": "ALLOW", "fraud_risk_score": 0.004871, ...}
```

---

### 4. Live Friction Budget Recalibration
`POST /api/v1/policy/recalibrate`

Updates the operational friction budget live (e.g. from 2% to 1%) without restarting or retraining.

#### Request Payload:
```json
{
  "friction_budget": 0.01,
  "block_threshold": 0.85
}
```

#### Response Payload:
```json
{
  "status": "success",
  "previous": {
    "friction_budget": 0.02,
    "stepup_threshold": 0.769512,
    "block_threshold": 0.85
  },
  "current": {
    "friction_budget": 0.01,
    "stepup_threshold": 0.988903,
    "block_threshold": 0.85
  },
  "fallback_active": false,
  "recalibrated_at": "2026-10-29T19:20:30Z",
  "requires_retraining": false,
  "requires_restart": false
}
```

---

### 5. Telemetry & Analytics
`GET /api/v1/metrics`

Returns live action distributions, friction budget compliance, fraud dollars prevented, and latency stats.

---

### 6. Mini Stress-Test (2% → 1% Simulation)
`POST /api/v1/stress-test`

Runs an automated stress test across a cohort of transactions, evaluating baseline (2%) vs target (1%) policy to demonstrate instant threshold adaptation.

---

## 💻 Frontend Developer Integration Guide

*(Share this section directly with your frontend / UI teammate!)*

### Connecting the Checkout "Pay" Button (JavaScript / React):

```javascript
const API_BASE = "http://localhost:8000/api/v1";

// 1. Called when user clicks "Pay"
async function handleCheckout(checkoutForm) {
  const payload = {
    TransactionAmt: parseFloat(checkoutForm.amount),
    ProductCD: "W",
    card4: checkoutForm.cardBrand.toLowerCase(), // "visa" or "mastercard"
    card6: checkoutForm.cardType.toLowerCase(),  // "debit" or "credit"
    P_emaildomain: checkoutForm.email.split("@")[1] || "gmail.com",
    DeviceType: /Mobi|Android/i.test(navigator.userAgent) ? "mobile" : "desktop",
    DeviceInfo: navigator.platform
  };

  try {
    const res = await fetch(`${API_BASE}/score`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const decision = await res.json();

    if (decision.action === "ALLOW") {
      // Direct success
      showOrderSuccessScreen(decision);
    } else if (decision.action === "STEP-UP") {
      // Open OTP modal, show reason & risk drivers
      openOtpModal({
        transactionId: decision.transaction_id,
        reason: decision.action_reason,
        riskFactors: decision.risk_factors
      });
    } else if (decision.action === "BLOCK") {
      // Decline notice
      showDeclineNotice(decision.action_reason);
    }
  } catch (err) {
    console.error("Payment check failed", err);
  }
}

// 2. Called when user enters OTP in Step-Up modal
async function submitOtpVerification(transactionId, otpCode) {
  const res = await fetch(`${API_BASE}/step-up/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      transaction_id: transactionId,
      otp_code: otpCode // Demo code: "123456"
    })
  });
  const result = await res.json();

  if (result.action === "VERIFIED_ALLOW") {
    closeOtpModal();
    showOrderSuccessScreen(result);
  } else {
    alert(result.message || "Invalid OTP code. Please retry.");
  }
}

// 3. Subscribing to Real-Time SSE Stream (for Dashboard)
function connectLiveDashboard(onEventReceived) {
  const eventSource = new EventSource(`${API_BASE}/stream`);
  eventSource.addEventListener("transaction", (e) => {
    const eventData = JSON.parse(e.data);
    onEventReceived(eventData);
  });
  return () => eventSource.close();
}
```

---

## 🧪 Interactive Swagger UI & Sample Data

1. Open [http://localhost:8000/docs](http://localhost:8000/docs) in your browser.
2. Call `GET /api/v1/samples` to fetch **pre-configured test transactions**:
   - `TXN-LOW-001`: Genuine domestic card purchase (Expected: `ALLOW`).
   - `TXN-STEPUP-002`: Unfamiliar mobile device with medium anomaly (Expected: `STEP-UP`).
   - `TXN-BLOCK-003`: Extreme velocity burst with proxy IP (Expected: `BLOCK`).
   - Blacklist test: pass `card1: 9999` to trigger instant pre-ML guardrail block.

---

## 🔬 Running Automated Tests

Run the complete test suite:

```bash
python -m pytest -v
```

All 23 tests verify:
- Pre-ML Guardrails & blacklist interception
- 5-Fold LightGBM inference & feature alignment
- Quantile interpolation & instant threshold shifts ($2\% \to 1\%$)
- Explainable AI (XAI) risk drivers
- OTP verification lifecycle
- Real-time subscriber queue dispatch
- Round-trip latency benchmarks (<15ms)

---

## 🏛️ Architecture Deep-Dive

For a complete breakdown of the mathematical formulations, risk quantile calibration, perception vs policy separation, and enterprise defense-in-depth, see:

👉 [**ARCHITECTURE.md**](file:///ARCHITECTURE.md)
