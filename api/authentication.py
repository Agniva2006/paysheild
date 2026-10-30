import re
import hmac
import os
from typing import Any, Dict

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from engine.prototype_store import get_user_for_token

bearer_scheme = HTTPBearer(auto_error=False)


def require_admin(admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> None:
    expected = os.getenv("PAYSHIELD_ADMIN_TOKEN", "")
    if not expected:
        if os.getenv("PAYSHIELD_ENV", "development").lower() != "production":
            return
        raise HTTPException(status_code=503, detail="Admin controls are disabled. Configure PAYSHIELD_ADMIN_TOKEN.")
    if admin_token is None or not hmac.compare_digest(admin_token, expected):
        raise HTTPException(status_code=403, detail="Admin authorization required.")


def require_merchant_integration(
    integration_key: str | None = Header(default=None, alias="X-Merchant-API-Key"),
) -> None:
    expected = os.getenv("PAYSHIELD_MERCHANT_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Transaction scoring integration is not configured.")
    if integration_key is None or not hmac.compare_digest(integration_key, expected):
        raise HTTPException(status_code=401, detail="Valid merchant integration credentials are required.")


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return email


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if domain.startswith(("https://", "http://")):
        domain = domain.split("://", 1)[1]
    domain = domain.split("/", 1)[0].split(":", 1)[0]
    if len(domain) > 253 or not re.fullmatch(r"(?=.{3,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
        raise HTTPException(status_code=422, detail="Provide a valid merchant domain, such as shop.example.com.")
    return domain


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to use this feature.")
    user = get_user_for_token(credentials.credentials)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")
    return user
