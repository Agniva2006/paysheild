# PayShield: System Architecture & Design Philosophy

> **"Detect fraud without turning every suspicious transaction into customer friction."**  
> *Architectural Blueprint & Deep Technical Whitepaper — Problem Statement PS-02 (SYN26-CS02)*

---

## 1. Executive Problem: The Payment Trilemma

In global digital commerce, risk engineering is plagued by what we call the **Payment Trilemma**:

```
                       [ 1. Minimize Fraud Losses ]
                                  ▲
                                 / \
                                /   \
                               /     \
   [ 2. Zero Customer Friction ] ◄───► [ 3. Zero False Rejections ]
```

Historically, fraud platforms modeled this as a **binary classification problem**:
- **0 = Legitimate** $\longrightarrow$ `ALLOW`
- **1 = Fraudulent** $\longrightarrow$ `BLOCK`

### Why Binary Classifiers Fail in Real Commerce
1. **The Cost Asymmetry:** Missing a \$50 fraud charge costs \$50 plus chargeback fees. But blocking a legitimate \$50 customer permanently destroys their customer lifetime value (LTV), costing \$300+ in lost future orders and driving them to competitors.
2. **The Uncertainty Void:** In reality, modern payments feature massive grey zones. A customer ordering from a new device, a foreign VPN IP, or during travel isn't necessarily a fraudster—they are an uncertain signal. Binary systems are forced to guess: either let suspicious transactions slide (costing money) or block good customers (destroying conversion).
3. **The Static Threshold Trap:** Conventional systems hardcode a fixed decision boundary (e.g., `score > 0.70`). When merchant marketing launches a flash sale, order volume spikes, false positives surge, and support desks get flooded. To fix this, engineering teams must retrain models or restart services—a painful, risky downtime event during peak revenue hours.

---

## 2. Core Philosophy: "Score First, Decide Second"

PayShield rejects the idea that a machine learning model should directly make business decisions. 

Instead, PayShield establishes a strict separation between **Perception** and **Policy**:

```
┌────────────────────────────────────────┐       ┌────────────────────────────────────────┐
│           PERCEPTION LAYER             │       │             POLICY LAYER               │
│                                        │       │                                        │
│  "What is the statistical probability  │  ──►  │  "What action should the business      │
│   that this transaction is fraud?"     │       │   take under today's friction budget?" │
└────────────────────────────────────────┘       └────────────────────────────────────────┘
```

1. **The Perception Layer (ML Engine):** Its sole job is to produce a stable, calibrated, continuous risk score:
   $$\hat{s} = P(\text{fraud} \mid \mathbf{x}) \in [0.00, 1.00]$$
2. **The Policy Layer (Decision Engine):** Translates that statistical probability into an operational action based on real-time business constraints, risk appetite, and customer tolerance.

---

## 3. The Three-Action Decision Space

Rather than a binary Allow/Block, PayShield operates on a **three-way action space**:

```
Calibrated Risk (s) ──►  [ 0.00 ....................... T_stepup(B) ....................... T_block ....................... 1.00 ]
                                    │                                  │                                  │
                                    ▼                                  ▼                                  ▼
                                  ALLOW                             STEP-UP                             BLOCK
                               (Low Risk)                        (Medium Risk)                       (High Risk)
                            Frictionless Pay                     2FA / OTP / 3DS                   Hard Security Stop
```

| Action | Risk Band | User Experience | Business Rationale |
| :--- | :--- | :--- | :--- |
| **`ALLOW`** | $s < T_{stepup}(B)$ | 0-friction instant checkout. No OTP prompts, no SMS waits. | Maximizes checkout conversion for genuine buyers. |
| **`STEP-UP`** | $T_{stepup}(B) \le s < T_{block}$ | Dynamic challenge: 6-digit OTP, 3D-Secure 2.0 challenge, or biometric prompt. | Resolves ambiguity without losing the sale. Genuine users verify in 5 seconds; fraudsters cannot complete it. |
| **`BLOCK`** | $s \ge T_{block}$ | Hard denial with clear security reason. | Halts blatant, high-conviction attacks and compromised credentials. |

---

## 4. The Friction Budget Constraint & Quantile Calibration

Every digital business has an executive constraint called the **Customer Friction Budget ($B$)**:
> *"No more than $B\%$ (e.g. 2%) of our legitimate customers should ever experience an OTP challenge."*

### Mathematical Formulation
Let $S_{legit}$ be the distribution of calibrated risk scores over legitimate transactions in the calibration/validation population ($N = 590,540$).

To guarantee that the expected legitimate customer friction does not exceed $B$, the dynamic Step-Up threshold $T_{stepup}(B)$ is defined as the upper $(1 - B)$ quantile of the legitimate score distribution:

$$T_{stepup}(B) = \text{Quantile}_{1 - B}(S_{legit})$$

Such that:
$$P(s \ge T_{stepup}(B) \mid \text{Legitimate}) \le B$$

### Live Recalibration (Zero Retraining, Zero Restart)
When a merchant experiences a shift in business posture:
- **Black Friday / Festive Sale:** High traffic, risk team wants lower customer friction $\longrightarrow$ Budget lowered: $2\% \to 1\%$.
- **High Fraud Outbreak / Target Attack:** Merchant wants stricter screening $\longrightarrow$ Budget increased: $1\% \to 3\%$.

```
Budget = 2% (Standard)  ──►  T_stepup = 0.769512
Budget = 1% (Low Friction) ─► T_stepup = 0.988903  (Step-up bar raised; fewer users challenged)
Budget = 3% (High Caution)  ─► T_stepup = 0.174071  (Step-up bar lowered; more users challenged)
```

**Key Architectural Feat:** This threshold calculation is performed in **microseconds** via precomputed quantile interpolation. The underlying LightGBM model weights are **never touched**, and the server requires **zero restarts**.

---

## 5. End-to-End System Pipeline

```
                              [ Incoming Payment Request ]
                                           │
                                           ▼
                       ┌───────────────────────────────────────┐
                       │   PRE-ML HYBRID RULE GUARDRAILS       │
                       │   - Stolen Card BIN Blacklist         │
                       │   - Hard Amount Ceiling ($50,000)     │
                       │   - Rapid Card Flood Replay (>5 / 30s)│
                       │   - Sanctioned Country Codes          │
                       └───────────────────────────────────────┘
                                  │                  │
                           [Rule Triggered]      [Clean]
                                  │                  │
                                  ▼                  ▼
                            Instant BLOCK     ┌───────────────────────────────────────┐
                              (<0.5 ms)       │      FEATURE PREPARATION PIPELINE     │
                                              │   - Card amount deviation ratios      │
                                              │   - Timedelta (D15) normalizations    │
                                              │   - Identity / id_02 consistency      │
                                              │   - Email domain splitting            │
                                              │   - 369-feature vector alignment      │
                                              └───────────────────────────────────────┘
                                                             │
                                                             ▼
                                              ┌───────────────────────────────────────┐
                                              │       LIGHTGBM INFERENCE ENGINE       │
                                              │   - 5-Fold Gradient Boosted Trees     │
                                              │   - Fast Mode (~1-3ms, Primary Fold)  │
                                              │   - Ensemble Mode (~5-10ms, Mean 5)   │
                                              └───────────────────────────────────────┘
                                                             │
                                                             ▼
                                              ┌───────────────────────────────────────┐
                                              │       DYNAMIC POLICY ENGINE           │
                                              │   - Dynamic T_stepup(B) evaluation    │
                                              │   - Fixed / Configurable T_block      │
                                              │   - Safe fallback protection          │
                                              └───────────────────────────────────────┘
                                                             │
                                                             ▼
                                              ┌───────────────────────────────────────┐
                                              │       EXPLAINABLE AI (XAI) LAYER      │
                                              │   - Observable risk driver extraction │
                                              │   - Actionable business explanations  │
                                              └───────────────────────────────────────┘
                                                             │
                                        ┌────────────────────┴────────────────────┐
                                        ▼                                         ▼
                             [ Decision JSON Response ]               [ Real-Time SSE Stream ]
                             - Action: ALLOW / STEP-UP / BLOCK        - Live Dashboard Push
                             - Score & Thresholds                     - Telemetry Metrics
                             - Top 3 Risk Factors                     - Closed-Loop Feedback
```

---

## 6. Hybrid Defense-in-Depth: Pre-ML Guardrails

Machine learning models excel at discovering complex non-linear interactions across hundreds of variables. However, they should never be burdened with trivial, non-negotiable compliance rules.

PayShield implements an ultra-fast **Pre-ML Guardrails layer** ([guardrails.py](file:///c:/Users/User/Desktop/projects/paysheild/engine/guardrails.py)):
1. **Compromised Card Blacklists:** Known stolen card numbers (e.g. BIN `9999`, `1111`, `404040`) are intercepted in `< 0.5ms`.
2. **Platform Amount Ceilings:** Transactions exceeding hard platform liability limits (e.g., $> \$50,000$) are halted immediately.
3. **Replay Flood Locks:** If the same payment card identifier is submitted $> 5$ times in 30 seconds, an automated velocity lock trips to prevent botnet card testing.
4. **Sanctioned Geographies:** Payments originating from or routed to prohibited jurisdiction codes are blocked for regulatory compliance.

---

## 7. Explainable AI (XAI) & Observable Drivers

Under modern financial regulations (e.g., Equal Credit Opportunity Act, GDPR Article 22), automated systems must not produce unexplainable rejections.

The PayShield Explainer ([explainer.py](file:///c:/Users/User/Desktop/projects/paysheild/engine/explainer.py)) evaluates feature deviations against dataset baselines and attaches the **Top 3 Risk Drivers** to every single response:
- *"Transaction amount (\$12,500.00) is 92.6x higher than standard card baseline"*
- *"Severe velocity burst: 12 rapid payment attempts registered in short window"*
- *"High-risk or privacy-shielded email domain detected (protonmail.com)"*
- *"Significant identity-device inconsistency flag (negative id_01 index)"*

---

## 8. Closing the Loop: Step-Up Verification Lifecycle

A common flaw in academic fraud systems is treating Step-Up as an end state. In PayShield, Step-Up is an **active authentication challenge**:

```
   [ Transaction Initiated ]
               │
               ▼
   [ Action = 'STEP-UP' ] ────────► App shows OTP modal;
               │                     Backend registers challenge in pending memory
               ▼
   [ Customer Submits OTP ]
               │
               ▼
[ POST /api/v1/step-up/verify ] ──► Validates 6-digit challenge code
               │
       ┌───────┴───────┐
       ▼               ▼
[ Code Valid ]   [ Code Invalid ]
       │               │
       ▼               ▼
VERIFIED_ALLOW   CHALLENGE_FAILED
 (Payment Won)    (Attack Blocked)
```

This closes the revenue loop:
- Telemetry tracks the **Resolution Rate**: what percentage of stepped-up customers successfully authorize.
- Authorized transactions are recorded as protected revenue rather than lost friction.

---

## 9. Closed-Loop Telemetry & Real-Time Streaming

PayShield features an integrated telemetry engine ([telemetry.py](file:///c:/Users/User/Desktop/projects/paysheild/engine/telemetry.py)):
- **Sliding-Window Action Breakdown:** Live percentages of Allow vs. Step-Up vs. Block.
- **Budget Compliance Monitor:** Evaluates whether observed legitimate Step-Up rate exceeds configured budget $B$.
- **Financial Impact Tracker:** Quantifies total dollars processed vs. **Fraud Dollars Prevented**.
- **Server-Sent Events (SSE) Broadcast:** Via `GET /api/v1/stream`, any frontend dashboard receives instant JSON events whenever a transaction is evaluated or an OTP is verified.

---

## 10. Reliability & Failsafe Architecture

1. **Missing Feature Resilience:** LightGBM handles missing inputs natively via NaN split routing. When a browser checkout sends only 6 fields, the pipeline imputes standard ratios and lets the trees navigate default branches cleanly.
2. **Safe Fallback Thresholds:** If the calibration distribution table is ever lost or corrupted, the policy engine automatically engages hardcoded empirical fallback thresholds (`T_stepup = 0.769512`, `T_block = 0.8500`) to guarantee continuous uptime.
3. **In-Memory Model Warmup:** Models are pre-warmed during FastAPI application lifespan startup, eliminating cold-start penalties for incoming checkout transactions.

