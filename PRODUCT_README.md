# PayShield

PayShield accepts a payment website URL, checks its URL structure, live public DNS, and HTTPS certificate, and explains the evidence. When configured, Google Web Risk adds a threat-intelligence check. Signed-in users can view saved assessments and submit unverified merchant reports. Premium subscriptions use Stripe Checkout.

## Run locally

1. Create a virtual environment and install dependencies: `pip install -r requirements.txt`.
2. Start the API: `uvicorn main:app --reload`.
3. Open `http://localhost:8000/app`. API documentation is at `/docs`.

SQLite stores accounts, sessions, assessments, merchant reports, and billing state in `data/payshield.db`. Set `PAYSHIELD_DB_PATH` to choose another persistent path.

## Product flow

1. Register or sign in with email and password.
2. Paste the payment website URL. PayShield returns a transparent rule-based signal index, score breakdown, check coverage, live DNS/TLS evidence, and configured threat-intelligence results, with three suggested actions and available user reports. The index is not a calibrated fraud probability; lookup failures reduce evidence coverage instead of counting as proof of fraud. It does not download or execute the website.
3. The optional manual form runs the saved LightGBM ensemble on the small set of fields entered, shows supplied-field coverage and model feature contributions, and recommends review. Its raw score is not a calibrated probability. A merchant integration can send transaction fields to `POST /api/v1/integrations/transactions/score` with `X-Merchant-API-Key`; a URL by itself cannot produce transaction risk.

The checked-in legacy model files do not include all training-time categorical encoders or group-statistic maps used by the notebook. PayShield no longer fabricates those statistics: unavailable engineered features remain missing, and the result reports input coverage and this artifact limitation. Reliable merchant decisions require retraining/exporting the model together with its preprocessing encoders and statistics, then validating on representative merchant traffic. Threshold quantiles tune the decision policy; they do not calibrate the model score as a probability.
4. Subscribe to Premium using Stripe Checkout. The account plan changes only after signed Stripe webhooks confirm subscription status.

The URL assessor never downloads or executes the submitted website. DNS and TLS requests are limited to public IP addresses, and the resolved IP is pinned for the TLS handshake to prevent DNS rebinding to private services. URL paths and queries are checked locally and discarded; only the origin is sent to Google Web Risk and retained. User-submitted merchant reports are unverified.

## API endpoints

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`
- `POST /api/v1/assessments/url` (bearer token)
- `POST /api/v1/assessments/{assessment_id}/transaction-score` (bearer token; supplied feature subset)
- `GET /api/v1/assessments/{assessment_id}` (bearer token; for integration result updates)
- `GET /api/v1/assessments/history` (bearer token)
- `POST /api/v1/merchant-reports` (bearer token)
- `POST /api/v1/integrations/transactions/score` (merchant API key)
- `GET /api/v1/billing/subscription` (bearer token)
- `POST /api/v1/billing/checkout-session` (bearer token)
- `POST /api/v1/billing/portal-session` (bearer token)
- `POST /api/v1/billing/webhook` (Stripe signature)
- `GET /api/v1/health`

## Configuration

Set `PAYSHIELD_MERCHANT_API_KEY` to enable transaction scoring. Set `GOOGLE_WEB_RISK_API_KEY` to enable Google Web Risk URL threat lookup; DNS and TLS checks still run without it. Web Risk requires a Google Cloud project with billing enabled. Google currently lists the first 100,000 Lookup API calls per month as free, then usage is billed. Configure Stripe with `STRIPE_SECRET_KEY`, `STRIPE_PREMIUM_PRICE_ID`, `STRIPE_WEBHOOK_SECRET`, and `PAYSHIELD_APP_URL`. Point the Stripe webhook at `/api/v1/billing/webhook` and enable checkout completion and subscription create/update/delete events. Without these credentials, those integrations report that they are not configured; PayShield does not grant Premium or invent an IEEE-CIS score.

For local PowerShell, set the threat-intelligence key before starting the server: `$env:GOOGLE_WEB_RISK_API_KEY="your-key"`. Set `$env:PAYSHIELD_MERCHANT_API_KEY="your-merchant-key"` to enable the transaction integration endpoint.
