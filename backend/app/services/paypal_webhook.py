"""
backend/app/services/paypal_webhook.py
======================================
PayPal webhook signature verification.

PayPal signs webhook bodies with an RSA SHA-256 signature over a message
composed of the CRC32 (decimal representation) of:

    transmission_id + transmission_time + webhook_id + raw_body

The certificate public key is fetched from the ``PayPal-Cert-Url`` header and
cached in memory. Verification is only attempted when a webhook ID is
configured (``PAYPAL_WEBHOOK_ID``); otherwise webhooks fail closed.

Security notes:
- The CRC32 is computed from the ORIGINAL raw HTTP body bytes (not a
  re-serialised JSON document).
- The certificate URL is validated before any fetch (HTTPS + PayPal-owned
  host) to prevent SSRF / arbitrary certificate fetching.
- Required headers are validated before any cryptography or network work.
"""

import asyncio
import base64
import logging
import time
import urllib.parse
import zlib
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import load_pem_public_key

logger = logging.getLogger(__name__)

# PayPal certificate URLs are served from paypal.com / paypalobjects.com domains.
_ALLOWED_CERT_HOST_SUFFIXES = ("paypal.com", "paypalobjects.com")
_CERT_CACHE_TTL_SECONDS = 24 * 60 * 60

# url -> (public_key_object, fetched_at_monotonic)
_CERT_CACHE: Dict[str, Tuple[Any, float]] = {}
_CERT_CACHE_LOCK = asyncio.Lock()


def _crc32_decimal(data: str) -> str:
    """Return the UNSIGNED CRC32 of ``data`` as a decimal string (PayPal format)."""
    return str(zlib.crc32(data.encode("utf-8")) & 0xFFFFFFFF)


def _validate_cert_url(cert_url: str) -> str:
    """
    Validate a PayPal certificate URL: https + a PayPal-owned host.

    Raises ValueError for any non-conforming URL (blocks SSRF / arbitrary
    certificate fetching).
    """
    try:
        parsed = urllib.parse.urlsplit(cert_url)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("Invalid certificate URL") from exc

    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host:
        raise ValueError("Certificate URL must be HTTPS on a PayPal-owned host")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_CERT_HOST_SUFFIXES):
        raise ValueError("Certificate URL host is not PayPal-owned")
    return cert_url


async def _fetch_public_key(cert_url: str, http: Any) -> Any:
    """Fetch and parse the PayPal certificate public key PEM (URL already validated)."""
    resp = await http.get(cert_url)
    resp.raise_for_status()
    pem = getattr(resp, "text", "")
    if not pem:
        raise ValueError("Empty certificate body")
    return load_pem_public_key(pem.encode("utf-8"))


async def _get_public_key_cached(cert_url: str, http: Any) -> Any:
    """Return a cached certificate public key, fetching it at most once per TTL."""
    now = time.monotonic()
    entry = _CERT_CACHE.get(cert_url)
    if entry and (now - entry[1]) < _CERT_CACHE_TTL_SECONDS:
        return entry[0]

    async with _CERT_CACHE_LOCK:
        entry = _CERT_CACHE.get(cert_url)
        if entry and (time.monotonic() - entry[1]) < _CERT_CACHE_TTL_SECONDS:
            return entry[0]
        key = await _fetch_public_key(cert_url, http)
        _CERT_CACHE[cert_url] = (key, time.monotonic())
        return key


async def verify_webhook_signature(
    *,
    transmission_id: str,
    transmission_time: str,
    webhook_id: str,
    raw_body: str,
    signature_b64: str,
    cert_url: str,
    auth_algo: str,
    http: Any,
) -> bool:
    """
    Verify a PayPal webhook signature.

    Returns True on success, False on any failure (missing header, unsupported
    algorithm, invalid certificate URL, bad signature). The caller gates on
    ``webhook_id`` being configured before calling this function.
    """
    # 1. Validate required headers (fail before any network/crypto work).
    if not all([transmission_id, transmission_time, webhook_id, signature_b64, cert_url, auth_algo]):
        logger.warning("Webhook rejected: missing required PayPal headers")
        return False

    # 2. Algorithm check (before fetching any certificate).
    if auth_algo.strip().upper() != "SHA256WITHRSA":
        logger.warning("Rejecting webhook with unsupported auth algo: %s", auth_algo)
        return False

    # 3. Decode signature.
    try:
        signature = base64.b64decode(signature_b64)
    except Exception:
        logger.warning("Webhook signature is not valid base64")
        return False

    # 4. Validate certificate URL (SSRF guard).
    try:
        _validate_cert_url(cert_url)
    except ValueError as exc:
        logger.warning("Webhook certificate URL rejected: %s", exc)
        return False

    # 5. Build the signed message from the raw body (decimal CRC32).
    crc = _crc32_decimal(transmission_id + transmission_time + webhook_id + raw_body)
    message = crc.encode("utf-8")

    # 6. Fetch (cached) certificate public key.
    try:
        public_key = await _get_public_key_cached(cert_url, http)
    except Exception as exc:
        logger.warning("Could not fetch PayPal webhook certificate: %s", exc)
        return False

    # 7. Verify.
    try:
        public_key.verify(signature, message, padding.PKCS1v15(), SHA256())
        return True
    except Exception:
        logger.warning("PayPal webhook signature verification failed")
        return False
