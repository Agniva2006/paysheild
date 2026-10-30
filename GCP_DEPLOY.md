# Deploy PayShield on Google Cloud

This guide deploys the current SQLite-backed application on a Compute Engine VM. The VM's persistent boot disk stores the Docker volume, so the database survives container restarts and VM reboots. Do not move this SQLite setup to multiple app replicas; use a PostgreSQL adapter and Cloud SQL before scaling horizontally.

## 1. Prepare the project and domain

- Confirm the project starts locally with `uvicorn main:app --reload` and `/app` opens.
- In Google Cloud Console, select or create a project and enable Compute Engine.
- Reserve a static external IP, create an Ubuntu 24.04 LTS VM, and attach that IP.
- Create a DNS `A` record for a domain you control pointing to the VM IP.
- In the VM network firewall, allow TCP 80 and 443. Limit SSH (TCP 22) to your own IP where possible. Do not open port 8000 publicly.

## 2. Install Docker and Caddy on the VM

Connect using the VM's SSH button, then run:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Install Caddy using its official Ubuntu package instructions at [caddyserver.com/docs/install](https://caddyserver.com/docs/install). Caddy will terminate HTTPS and proxy requests to the app on localhost.

## 3. Copy the code to the VM

Push this project to a private Git repository, then clone it on the VM. Do not commit `.env`, Stripe credentials, or API keys.

```bash
git clone YOUR_PRIVATE_REPOSITORY_URL payshield
cd payshield
```

The compose file binds the app to `127.0.0.1:8000` and stores SQLite in a persistent Docker volume named `payshield-runtime`.

## 4. Configure production secrets

Create `.env` in the project directory on the VM:

```bash
nano .env
```

Set these values (replace every placeholder):

```dotenv
PAYSHIELD_ENV=production
PAYSHIELD_DB_PATH=/app/runtime/payshield.db
PAYSHIELD_ADMIN_TOKEN=GENERATE_A_LONG_RANDOM_VALUE
PAYSHIELD_MERCHANT_API_KEY=GENERATE_A_DIFFERENT_LONG_RANDOM_VALUE
PAYSHIELD_FRONTEND_ORIGINS=
PAYSHIELD_APP_URL=https://YOUR_DOMAIN
STRIPE_SECRET_KEY=YOUR_STRIPE_SECRET_KEY
STRIPE_PREMIUM_PRICE_ID=YOUR_STRIPE_RECURRING_PRICE_ID
STRIPE_WEBHOOK_SECRET=YOUR_STRIPE_WEBHOOK_SIGNING_SECRET
GOOGLE_WEB_RISK_API_KEY=YOUR_GOOGLE_WEB_RISK_API_KEY
```

Use `openssl rand -hex 32` to generate the admin and merchant keys. Restrict the file and ensure Git ignores it:

```bash
chmod 600 .env
```

Keep the Stripe key in Stripe test mode while preparing the hackathon launch. Create a recurring Premium price in Stripe and use its Price ID. If billing is not ready, leave Stripe variables empty; checkout reports that billing is not configured and does not grant a subscription.

## 5. Add HTTPS reverse proxy

Replace `YOUR_DOMAIN` with the DNS name pointing to this VM:

```bash
echo 'YOUR_DOMAIN {
  reverse_proxy 127.0.0.1:8000
}' | sudo tee /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy obtains and renews the TLS certificate after DNS resolves and ports 80/443 are reachable.

## 6. Start PayShield

```bash
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs -f payshield-backend
```

Open `https://YOUR_DOMAIN/app`. Check `https://YOUR_DOMAIN/api/v1/health` and `https://YOUR_DOMAIN/docs`.

## 7. Configure Stripe webhook

In the Stripe Dashboard, add `https://YOUR_DOMAIN/api/v1/billing/webhook` as a webhook destination. Subscribe it to `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`, and `customer.subscription.deleted`. Copy its signing secret to `STRIPE_WEBHOOK_SECRET` in `.env`, then restart:

```bash
sudo docker compose up -d
```

Test registration, URL assessment, Stripe test checkout, and subscription cancellation. An account becomes Premium only after Stripe sends a valid signed event.

## 8. Connect an IEEE-CIS transaction source

Set the same `PAYSHIELD_MERCHANT_API_KEY` on the merchant integration service. Send `POST /api/v1/integrations/transactions/score` with `X-Merchant-API-Key`, the URL assessment ID, and actual model features in the `transaction` object. A pasted URL does not contain IEEE-CIS transaction features and therefore receives only the URL risk score and three URL-based recommendations.

## Operations

- Deploy updates: `git pull` then `sudo docker compose up -d --build`.
- Inspect logs: `sudo docker compose logs --tail=200 payshield-backend`.
- Back up the named volume before VM replacement or disk maintenance. Verify backups by restoring to a separate VM.
- Keep one application instance while using SQLite. Add PostgreSQL/Cloud SQL before using multiple replicas.
- Restrict VM SSH, rotate exposed keys, and never put secrets in frontend JavaScript or a committed file.
