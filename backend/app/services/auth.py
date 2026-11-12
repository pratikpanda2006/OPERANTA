"""
backend/app/services/auth.py
============================
OPERANTA authentication primitives.

- Password hashing uses salted PBKDF2-HMAC-SHA256 (Python standard library) with
  a per-user random salt and a configurable iteration count. Plaintext passwords
  are never stored.
- Session tokens are compact, signed (HMAC-SHA256) payloads carrying the user id
  (``sub``), the server-resolved merchant id (``mid``) and an absolute expiry
  (``exp``). These are self-contained so a synchronous FastAPI dependency can
  verify them without a database round-trip.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

PBKDF2_ITERATIONS = 120_000
_ALGO_LABEL = "pbkdf2_sha256"


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return a salted PBKDF2-HMAC-SHA256 hash string for ``password``."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "{0}${1}${2}${3}".format(
        _ALGO_LABEL,
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """Verify ``password`` against a stored hash string. Never raises on bad input."""
    try:
        algo, iter_s, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO_LABEL:
            return False
        iterations = int(iter_s)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_b64.encode("ascii"))
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def create_session_token(secret: str, user_id: str, merchant_id: str, ttl_minutes: int = 720) -> str:
    """Create a signed session token (``<payload>.<signature>``)."""
    exp = int(time.time()) + int(ttl_minutes) * 60
    payload = {"sub": user_id, "mid": merchant_id, "exp": exp}
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return "{0}.{1}".format(payload_b64, _b64url_encode(signature))


def verify_session_token(secret: str, token: str) -> Optional[dict]:
    """Verify a signed session token; return its payload or None if invalid/expired."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(
            secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(provided_sig, expected_sig):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp") and int(payload["exp"]) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
