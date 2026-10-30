"""SQLite persistence for accounts, URL assessments, merchant reports and billing state."""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from config import BASE_DIR

DB_PATH = Path(os.getenv("PAYSHIELD_DB_PATH", str(BASE_DIR / "data" / "payshield.db")))
SESSION_DAYS = 7
PBKDF2_ROUNDS = 240_000


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_store() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                plan TEXT NOT NULL DEFAULT 'free',
                subscription_status TEXT NOT NULL DEFAULT 'inactive',
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS url_assessments (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                submitted_url TEXT NOT NULL,
                domain TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                risk_level TEXT NOT NULL,
                signals_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                recommendations_json TEXT NOT NULL DEFAULT '[]',
                score_breakdown_json TEXT NOT NULL DEFAULT '{}',
                transaction_result_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_url_assessments_user_created
                ON url_assessments(user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS billing_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                received_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS merchant_reports (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                merchant_domain TEXT NOT NULL,
                category TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "plan" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'")
        if "subscription_status" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'inactive'")
        if "stripe_customer_id" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT")
        if "stripe_subscription_id" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT")
        conn.execute("UPDATE users SET plan='free',subscription_status='inactive' WHERE stripe_customer_id IS NULL")
        assessment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(url_assessments)").fetchall()}
        if "evidence_json" not in assessment_columns:
            conn.execute("ALTER TABLE url_assessments ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'")
        if "recommendations_json" not in assessment_columns:
            conn.execute("ALTER TABLE url_assessments ADD COLUMN recommendations_json TEXT NOT NULL DEFAULT '[]'")
        if "score_breakdown_json" not in assessment_columns:
            conn.execute("ALTER TABLE url_assessments ADD COLUMN score_breakdown_json TEXT NOT NULL DEFAULT '{}'")


def store_is_ready() -> bool:
    try:
        with _connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_user(email: str, password: str) -> Dict[str, Any]:
    import uuid

    initialize_store()
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    password_hash = f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${derived.hex()}"
    user = {"id": str(uuid.uuid4()), "email": email.strip().lower(), "password_hash": password_hash}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
            (user["id"], user["email"], password_hash, _utc_now()),
        )
    return {"id": user["id"], "email": user["email"], "plan": "free"}


def authenticate(email: str, password: str) -> Optional[Dict[str, Any]]:
    initialize_store()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not row:
        return None
    try:
        _, rounds, salt_hex, digest_hex = row["password_hash"].split("$", 3)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(digest.hex(), digest_hex):
        return None
    return {"id": row["id"], "email": row["email"], "plan": row["plan"]}


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(36)
    expiry = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
            (digest, user_id, expiry, _utc_now()),
        )
    return token


def get_user_for_token(token: str) -> Optional[Dict[str, Any]]:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.id,u.email,u.plan,u.subscription_status,s.expires_at "
            "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
            (digest,),
        ).fetchone()
        if row and row["expires_at"] <= _utc_now():
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
            return None
    if not row:
        return None
    return {"id": row["id"], "email": row["email"], "plan": row["plan"], "subscription_status": row["subscription_status"]}


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


def get_subscription(user_id: str) -> Dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT plan,subscription_status,created_at,stripe_customer_id,stripe_subscription_id FROM users WHERE id=?", (user_id,)).fetchone()
    return {"plan": row["plan"], "status": row["subscription_status"], "created_at": row["created_at"], "billing_portal_available": bool(row["stripe_customer_id"])}


def set_billing_customer(user_id: str, customer_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET stripe_customer_id=? WHERE id=?", (customer_id, user_id))


def get_billing_customer(user_id: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT stripe_customer_id FROM users WHERE id=?", (user_id,)).fetchone()
    return row["stripe_customer_id"] if row else None


def update_subscription(user_id: str, customer_id: str, subscription_id: str, status: str) -> None:
    active = status in {"active", "trialing"}
    with _connect() as conn:
        conn.execute("UPDATE users SET stripe_customer_id=?,stripe_subscription_id=?,subscription_status=?,plan=? WHERE id=?",
                     (customer_id, subscription_id, status, "premium" if active else "free", user_id))


def get_user_by_customer(customer_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT id,email FROM users WHERE stripe_customer_id=?", (customer_id,)).fetchone()
    return dict(row) if row else None


def record_billing_event(event_id: str, event_type: str) -> bool:
    with _connect() as conn:
        try:
            conn.execute("INSERT INTO billing_events(event_id,event_type,received_at) VALUES(?,?,?)", (event_id,event_type,_utc_now()))
            return True
        except sqlite3.IntegrityError:
            return False


def save_url_assessment(user_id: str, submitted_url: str, assessment: Dict[str, Any]) -> str:
    assessment_id = str(uuid.uuid4())
    from urllib.parse import urlsplit, urlunsplit
    parsed = urlsplit(assessment["url"])
    stored_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    with _connect() as conn:
        conn.execute(
            "INSERT INTO url_assessments(id,user_id,submitted_url,domain,risk_score,risk_level,signals_json,evidence_json,recommendations_json,score_breakdown_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (assessment_id, user_id, stored_url, assessment["domain"], assessment["risk_score"],
             assessment["risk_level"], json.dumps(assessment["signals"]), json.dumps(assessment.get("evidence", {})),
             json.dumps(assessment.get("recommendations", [])), json.dumps(assessment.get("score_breakdown", {})), _utc_now()),
        )
    return assessment_id


def get_user_assessment(user_id: str, assessment_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM url_assessments WHERE id=? AND user_id=?", (assessment_id, user_id)).fetchone()
    return row is not None


def get_assessment_owner(assessment_id: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM url_assessments WHERE id=?", (assessment_id,)).fetchone()
    return row["user_id"] if row else None


def get_url_assessment(user_id: str, assessment_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM url_assessments WHERE id=? AND user_id=?",
            (assessment_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "assessment_id": row["id"], "url": row["submitted_url"], "domain": row["domain"],
        "risk_score": row["risk_score"], "risk_level": row["risk_level"],
        "signals": json.loads(row["signals_json"]), "evidence": json.loads(row["evidence_json"]),
        "recommendations": json.loads(row["recommendations_json"]),
        "score_breakdown": json.loads(row["score_breakdown_json"]),
        "transaction_result": json.loads(row["transaction_result_json"]) if row["transaction_result_json"] else None,
        "created_at": row["created_at"],
    }


def attach_transaction_result(assessment_id: str, result: Dict[str, Any]) -> bool:
    with _connect() as conn:
        cursor = conn.execute("UPDATE url_assessments SET transaction_result_json=? WHERE id=?",
                              (json.dumps(result), assessment_id))
    return cursor.rowcount == 1


def get_url_assessments(user_id: str, limit: int = 25) -> list[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM url_assessments WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [{
        "assessment_id": row["id"], "url": row["submitted_url"], "domain": row["domain"],
        "risk_score": row["risk_score"], "risk_level": row["risk_level"],
        "signals": json.loads(row["signals_json"]),
        "evidence": json.loads(row["evidence_json"]),
        "recommendations": json.loads(row["recommendations_json"]),
        "score_breakdown": json.loads(row["score_breakdown_json"]),
        "transaction_result": json.loads(row["transaction_result_json"]) if row["transaction_result_json"] else None,
        "created_at": row["created_at"],
    } for row in rows]


def save_merchant_report(user_id: str, domain: str, category: str, note: str) -> Dict[str, Any]:
    report = {"id": str(uuid.uuid4()), "domain": domain.lower(), "category": category,
              "note": note, "created_at": _utc_now()}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO merchant_reports(id,user_id,merchant_domain,category,note,created_at) VALUES(?,?,?,?,?,?)",
            (report["id"], user_id, report["domain"], category, note, report["created_at"]),
        )
    return report


def get_merchant_reports(domain: str, limit: int = 20) -> list[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT category,note,created_at FROM merchant_reports WHERE merchant_domain=? ORDER BY created_at DESC LIMIT ?",
            (domain.lower(), limit),
        ).fetchall()
    return [{"category": row["category"], "note": row["note"], "created_at": row["created_at"],
             "verified": False} for row in rows]
