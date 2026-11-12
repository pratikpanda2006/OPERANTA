"""
backend/app/services/paypal_credentials.py
===========================================
Merchant-specific PayPal credential storage and resolution.

Two distinct credential paths are supported:

1. Northstar demo merchant (``settings.demo_merchant_id``)
   Uses the EXISTING environment-based PayPal Sandbox credentials
   (``PAYPAL_CLIENT_ID`` / ``PAYPAL_CLIENT_SECRET``). This path is untouched so
   the existing hackathon demo integration keeps working exactly as before.

2. Normal SaaS merchants
   Credentials live in ``merchant_paypal_integrations``. The client secret is
   stored ONLY as a Fernet (AES-GCM) ciphertext and decrypted on the backend at
   request time. It is never exposed to the frontend.

Security invariants:
- ``merchant_northstar`` NEVER reads another merchant's credentials.
- A normal merchant NEVER falls back to Northstar's environment credentials.
- If a merchant has no valid integration, ``None`` is returned so callers fail
  with a clear "PayPal not configured" response.
- No credentials, access tokens or full authentication headers are logged.
"""

import base64
import hashlib
import logging
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings

logger = logging.getLogger(__name__)


def _make_fernet_from_key(key: str) -> Fernet:
    """Build a Fernet instance from an arbitrary-length string key."""
    material = hashlib.sha256(key.encode("utf-8")).digest()
    token = base64.urlsafe_b64encode(material)
    return Fernet(token)


def encrypt_secret(plaintext: str, key: str) -> str:
    """Return a Fernet (AES-GCM) ciphertext for ``plaintext``."""
    return _make_fernet_from_key(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, key: str) -> str:
    """Decrypt a Fernet ciphertext. Raises ValueError on tampering/bad key."""
    try:
        return _make_fernet_from_key(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        logger.error("Failed to decrypt merchant PayPal secret: bad ciphertext or key.")
        raise ValueError("Merchant PayPal secret could not be decrypted.") from exc


def mask_client_id(client_id: str) -> str:
    """Return a masked rendering of a client id for status responses."""
    if len(client_id) <= 6:
        return "****"
    return client_id[:4] + "…" + client_id[-4:]


def _get_encryption_key(settings: Settings) -> str:
    key = settings.paypal_credentials_encryption_key
    if not key or not key.get_secret_value():
        raise ValueError(
            "PAYPAL_CREDENTIALS_ENCRYPTION_KEY is not configured. "
            "Set it to enable merchant-specific PayPal credential storage."
        )
    return key.get_secret_value()


async def resolve_paypal_credentials(
    settings: Settings,
    merchant_id: str,
    conn: Any,
) -> Optional[Dict[str, str]]:
    """
    Resolve the active PayPal credentials for ``merchant_id``.

    Returns a dict with keys ``client_id``, ``client_secret``, ``base_url`` and
    ``webhook_id`` (webhook_id may be None), or ``None`` when the merchant has
    no configured/valid integration.

    Never falls back to Northstar credentials for other merchants, and never
    returns credentials to the caller of an unconfigured merchant.
    """
    # Northstar / demo merchant -> legacy environment-based credentials.
    if merchant_id == settings.demo_merchant_id:
        cid = settings.paypal_client_id.get_secret_value() if settings.paypal_client_id else None
        csecret = settings.paypal_client_secret.get_secret_value() if settings.paypal_client_secret else None
        if not cid or not csecret:
            logger.warning("Northstar demo merchant has no environment-based PayPal credentials configured.")
            return None
        webhook = settings.paypal_webhook_id.get_secret_value() if settings.paypal_webhook_id else None
        return {
            "client_id": cid,
            "client_secret": csecret,
            "base_url": settings.paypal_base_url,
            "webhook_id": webhook,
        }

    # Normal SaaS merchant -> merchant-specific stored credentials.
    row = await conn.fetchrow(
        "SELECT environment, client_id, encrypted_client_secret, webhook_id, connection_status "
        "FROM merchant_paypal_integrations WHERE merchant_id = $1",
        merchant_id,
    )
    if row is None or row["connection_status"] != "connected":
        return None

    key = _get_encryption_key(settings)
    secret = decrypt_secret(row["encrypted_client_secret"], key)

    if row["environment"] == "live":
        base_url = "https://api-m.paypal.com"
    else:
        base_url = settings.paypal_base_url

    return {
        "client_id": row["client_id"],
        "client_secret": secret,
        "base_url": base_url,
        "webhook_id": row["webhook_id"],
    }
