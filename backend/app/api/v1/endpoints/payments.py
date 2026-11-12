"""
backend/app/api/v1/endpoints/payments.py
=========================================
M12.0 PayPal Sandbox customer checkout endpoints.

Routes:
- POST /api/v1/orders/online-checkout        create ONE internal order, reserve
                                              stock, and create the PayPal order.
- POST /api/v1/orders/online-checkout/{order_id}/capture
                                              capture + settle the payment.
- POST /api/v1/webhooks/paypal               verified webhook (signature + dedup).

Both the capture endpoint and the verified PAYMENT.CAPTURE.COMPLETED webhook call
the SAME canonical settlement operation (settle_capture) in payment_repo.py.
"""

import asyncio
import json
import logging
import urllib.parse
from decimal import Decimal
from typing import Any, Dict, Optional

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import get_db, get_merchant_context, get_paypal_client
from app.core.config import settings
from app.db.repositories.payment_repo import (
    attach_paypal_order,
    cancel_online_order_and_release,
    get_online_order_by_id,
    create_online_order,
    settle_capture,
)
from app.schemas.payments import (
    OnlineCaptureResult,
    OnlineCheckoutRequest,
    OnlineCheckoutResponse,
    PaymentCancelResponse,
    WebhookReceipt,
)
from app.services.paypal_client import PayPalAPIError, PayPalAuthError, PayPalClient
from app.services.paypal_webhook import verify_webhook_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders/online-checkout", tags=["PayPal Checkout"])
webhook_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

_PAYPAL_COMPLETED_EVENT = "PAYMENT.CAPTURE.COMPLETED"
_PAYPAL_DENIED_EVENT = "PAYMENT.CAPTURE.DENIED"
_CUSTOM_ID_SEPARATOR = ":"


# ---------------------------------------------------------------------------
# Pure builders / parsers
# ---------------------------------------------------------------------------
def build_paypal_order_payload(order: Dict[str, Any]) -> Dict[str, Any]:
    """Build the PayPal create-order request from server-derived totals."""
    total = order["total_amount"]
    subtotal = order["subtotal"]
    tax = order["tax_amount"]
    merchant_id = order["merchant_id"]
    order_id = order["order_id"]
    custom_id = f"{merchant_id}{_CUSTOM_ID_SEPARATOR}{order_id}"

    items = []
    for line in order["items"]:
        items.append(
            {
                "name": line["product_name"][:127],
                "quantity": str(line["quantity"]),
                "unit_amount": {
                    "currency_code": "USD",
                    "value": f"{line['unit_price']:.2f}",
                },
            }
        )

    # Build PayPal hosted-checkout return/cancel callbacks from the configured
    # application base URL. Local loopback is allowed; non-localhost (i.e.
    # production) MUST use HTTPS so the buyer is redirected securely — never a
    # production localhost callback.
    base = (settings.app_base_url or "http://127.0.0.1:8000").rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    hostname = (parsed.hostname or "").lower()
    if not parsed.scheme:
        raise ValueError("APP_BASE_URL must be an absolute URL.")
    if hostname not in ("127.0.0.1", "::1", "localhost") and parsed.scheme != "https":
        raise ValueError("Non-localhost APP_BASE_URL must use HTTPS for PayPal callbacks.")

    return_url = f"{base}/api/v1/orders/online-checkout/{order_id}/return"
    cancel_url = f"{base}/api/v1/orders/online-checkout/{order_id}/cancel"

    return {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": order_id,
                "custom_id": custom_id,
                "description": f"MarginGuard online order {order['order_number']}",
                "amount": {
                    "currency_code": "USD",
                    "value": f"{total:.2f}",
                    "breakdown": {
                        "item_total": {"currency_code": "USD", "value": f"{subtotal:.2f}"},
                        "tax_total": {"currency_code": "USD", "value": f"{tax:.2f}"},
                        "shipping": {"currency_code": "USD", "value": "0.00"},
                    },
                },
                "items": items,
            }
        ],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                    "user_action": "CONTINUE",
                    "brand_name": "MarginGuard",
                }
            }
        },
    }


def parse_capture_response(data: Dict[str, Any], order_total: Decimal) -> Optional[Dict[str, Any]]:
    """
    Extract a capture detailing dict from a PayPal capture response.

    Returns None when the order has not produced a capture yet (e.g. still
    APPROVED/PENDING with no capture record).
    """
    order_status = data.get("status", "")
    purchase_units = data.get("purchase_units") or []
    if not purchase_units:
        return None
    captures = (purchase_units[0].get("payments") or {}).get("captures") or []
    if captures:
        cap = captures[0]
        amount_value = cap.get("amount", {}).get("value")
        currency = cap.get("amount", {}).get("currency_code", "USD")
        try:
            amount = Decimal(str(amount_value)) if amount_value is not None else order_total
        except Exception:
            amount = order_total
        return {
            "paypal_capture_id": cap.get("id", ""),
            "amount": amount,
            "currency": currency,
            "status": str(cap.get("status") or order_status).lower(),
            "raw_payload": cap,
        }
    # No capture yet: use the intended order total as a pending capture amount.
    return {
        "paypal_capture_id": "",
        "amount": order_total,
        "currency": "USD",
        "status": str(order_status).lower(),
        "raw_payload": data,
    }


def _log_paypal_error(exc: Exception) -> None:
    """Log sanitized provider diagnostics (HTTP status, error name, debug id).

    Never logs secrets, tokens, or full provider bodies.
    """
    if isinstance(exc, PayPalAPIError):
        reason = exc.reason
        name = debug_id = None
        if isinstance(reason, dict):
            name = reason.get("name")
            debug_id = reason.get("debug_id")
            if not name:
                for d in reason.get("details") or []:
                    if isinstance(d, dict) and d.get("issue"):
                        name = d.get("issue")
                        break
        logger.error(
            "PayPal API error (HTTP=%s) name=%s debug_id=%s",
            exc.status_code,
            name,
            debug_id,
        )
    else:
        logger.error("PayPal payment error: %s", str(exc))


async def _get_order_for_callback(conn: asyncpg.Connection, merchant_id: str, order_id: str, token: str) -> Dict[str, Any]:
    """Validate a PayPal return/cancel callback: tenant-scoped order lookup and
    provider ``token`` must match the stored ``paypal_order_id``. Never trusts a
    client-supplied merchant id."""
    order = await get_online_order_by_id(conn, merchant_id, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")
    if order.get("paypal_order_id") != token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PayPal callback token.")
    return order


async def _cancel_pending_checkout(conn: asyncpg.Connection, merchant_id: str, order_id: str) -> PaymentCancelResponse:
    """Cancel a still-pending internal online order and release its online
    reservation exactly once. Idempotent for repeat calls; rejects
    completed/captured orders (never releases stock belonging to a paid order)."""
    order = await get_online_order_by_id(conn, merchant_id, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")
    if order["status"] == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot cancel an order with a completed capture.",
        )
    if order["status"] == "cancelled":
        return PaymentCancelResponse(
            merchant_id=merchant_id,
            order_id=order_id,
            status="cancelled",
            cancelled=False,
            message="Order is already cancelled.",
        )
    if order["channel"] != "online":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only online checkouts can be cancelled here.",
        )
    async with conn.transaction():
        cancelled = await cancel_online_order_and_release(conn, merchant_id, order_id)
    if cancelled:
        return PaymentCancelResponse(
            merchant_id=merchant_id,
            order_id=order_id,
            status="cancelled",
            cancelled=True,
            message="Order cancelled and reservation released.",
        )
    return PaymentCancelResponse(
        merchant_id=merchant_id,
        order_id=order_id,
        status=order["status"],
        cancelled=False,
        message="No pending order to cancel.",
    )


def _capture_response_payload(result: Dict[str, Any], order: Dict[str, Any]) -> OnlineCaptureResult:
    return OnlineCaptureResult(
        merchant_id=order["merchant_id"],
        order_id=order["id"],
        order_number=order["order_number"],
        order_status=result.get("order_status", order["status"]),
        capture_status=result.get("capture_status", "completed"),
        paypal_capture_id=result["paypal_capture_id"],
        captured_amount=result["captured_amount"],
        currency=result["currency"],
        settled=result.get("settled", False),
        already_settled=result.get("already_settled", False),
        revenue_ledger_id=result.get("revenue_ledger_id"),
    )


# ---------------------------------------------------------------------------
# Create checkout (one internal order, then PayPal order)
# ---------------------------------------------------------------------------
@router.post("", response_model=OnlineCheckoutResponse)
async def create_checkout(
    checkout_req: OnlineCheckoutRequest,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
    paypal: PayPalClient = Depends(get_paypal_client),
) -> OnlineCheckoutResponse:
    """
    Create ONE internal pending order + items, reserve stock, then create the
    PayPal order.

    The PayPal network call happens OUTSIDE any database transaction / inventory
    lock: the internal order + reservation are committed first, PayPal is called
    with a deterministic PayPal-Request-Id, and the PayPal order id is persisted
    in a second transaction.

    - Definite provider failure (PayPal returned an error) -> cancel the order
      and release its reservation.
    - Ambiguous failure (timeout / transport error) -> the order is left pending
      with its reservation so it can be reconciled; it is never prematurely
      released.
    """
    # 1. Commit the internal pending order + reservation (no network I/O).
    try:
        async with conn.transaction():
            order = await create_online_order(conn, merchant_id, checkout_req)
    except ValueError as ve:
        logger.warning("Checkout validation failed for merchant '%s': %s", merchant_id, ve)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve)) from ve

    # Deterministic idempotency/reconciliation key for PayPal.
    request_id = f"mg-cr-{merchant_id}-{order['order_id']}"
    payload = build_paypal_order_payload(order)

    # 2. Call PayPal OUTSIDE any DB transaction / inventory lock.
    try:
        paypal_order = await paypal.create_order(payload, request_id=request_id)
    except (PayPalAPIError, PayPalAuthError) as e:
        # Definite provider failure -> cancel + release reservation.
        _log_paypal_error(e)
        async with conn.transaction():
            await cancel_online_order_and_release(conn, merchant_id, order["order_id"])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create PayPal order.",
        ) from e
    except httpx.TimeoutException as e:
        # Ambiguous timeout: PayPal may have created the order. Keep it pending.
        logger.error("PayPal create-order timed out for merchant '%s' (request_id=%s)", merchant_id, request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PayPal request timed out; order is left pending for reconciliation.",
        ) from e
    except httpx.TransportError as e:
        # Ambiguous transport-level failure. Keep the reservation.
        logger.error("PayPal create-order transport error for merchant '%s': %s", merchant_id, e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PayPal unavailable; order is left pending for reconciliation.",
        ) from e

    paypal_order_id = paypal_order.get("id")
    if not paypal_order_id:
        logger.error("PayPal create-order returned no order id for merchant '%s'", merchant_id)
        async with conn.transaction():
            await cancel_online_order_and_release(conn, merchant_id, order["order_id"])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PayPal did not return an order id.",
        )
    approval_url = PayPalClient.extract_approve_url(paypal_order)
    if not approval_url:
        logger.error("PayPal create-order returned no approval action link for merchant '%s'", merchant_id)
        async with conn.transaction():
            await cancel_online_order_and_release(conn, merchant_id, order["order_id"])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PayPal order is missing an approval action link.",
        )

    # 3. Persist the PayPal order id in a NEW transaction.
    async with conn.transaction():
        await attach_paypal_order(conn, merchant_id, order["order_id"], paypal_order_id)

    return OnlineCheckoutResponse(
        merchant_id=merchant_id,
        order_id=order["order_id"],
        order_number=order["order_number"],
        paypal_order_id=paypal_order_id,
        approval_url=approval_url,
        subtotal=order["subtotal"],
        tax_amount=order["tax_amount"],
        total_amount=order["total_amount"],
        status="pending",
        items=order["items"],
    )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
@router.post("/{order_id}/capture", response_model=OnlineCaptureResult)
async def capture_online_order(
    order_id: str,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
    paypal: PayPalClient = Depends(get_paypal_client),
) -> OnlineCaptureResult:
    """Capture an approved PayPal order and settle the internal order."""
    order = await get_online_order_by_id(conn, merchant_id, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")
    if order["channel"] != "online":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not an online order.")

    # Webhook already settled this order before the capture endpoint ran.
    if order["status"] == "completed":
        existing = await conn.fetchrow(
            "SELECT * FROM paypal_captures WHERE merchant_id = $1 AND order_id = $2 AND status = 'completed'",
            merchant_id,
            order_id,
        )
        if existing:
            result = {
                "order_status": order["status"],
                "capture_status": existing["status"],
                "paypal_capture_id": existing["paypal_capture_id"],
                "captured_amount": Decimal(str(existing["captured_amount"])),
                "currency": str(existing["currency"]),
                "settled": True,
                "already_settled": True,
                "revenue_ledger_id": None,
            }
            return _capture_response_payload(result, order)

    if not order.get("paypal_order_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order has no associated PayPal order.",
        )

    paypal_order_id = str(order["paypal_order_id"])
    capture_request_id = f"mg-cp-{merchant_id}-{order_id}"
    try:
        capture_data = await paypal.capture_order(paypal_order_id, request_id=capture_request_id)
    except PayPalAPIError as e:
        # A webhook may have already captured it (ORDER_ALREADY_CAPTURED).
        reason = str(e.reason).lower() if e.reason else ""
        if "already_captured" in reason:
            existing = await conn.fetchrow(
                "SELECT * FROM paypal_captures WHERE merchant_id = $1 AND order_id = $2 AND status = 'completed'",
                merchant_id,
                order_id,
            )
            if not existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="PayPal reports the order was already captured, but no local capture exists.",
                ) from e
            result = {
                "order_status": order["status"],
                "capture_status": existing["status"],
                "paypal_capture_id": existing["paypal_capture_id"],
                "captured_amount": Decimal(str(existing["captured_amount"])),
                "currency": str(existing["currency"]),
                "settled": True,
                "already_settled": True,
                "revenue_ledger_id": None,
            }
            return _capture_response_payload(result, order)
        logger.error("PayPal capture failed for order '%s': %s", order_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to capture PayPal order.",
        ) from e
    except PayPalAuthError as e:
        logger.error("PayPal auth failed during capture for order '%s': %s", order_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to authenticate with PayPal.",
        ) from e

    capture_details = parse_capture_response(capture_data, Decimal(str(order["total_amount"])))

    # A completed capture is settled with the canonical operation.
    if capture_details and capture_details["status"] == "completed":
        async with conn.transaction():
            result = await settle_capture(conn, merchant_id, order_id, capture_details)
        return _capture_response_payload(result, order)

    # Pending / not yet captured: persist a pending capture record (no settlement).
    if capture_details and capture_details["paypal_capture_id"]:
        async with conn.transaction():
            result = await settle_capture(conn, merchant_id, order_id, capture_details)
        return _capture_response_payload(result, order)

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="PayPal order is not yet capturable.",
    )


# ---------------------------------------------------------------------------
# Webhook (single canonical route)
# ---------------------------------------------------------------------------
@webhook_router.post("/paypal", response_model=WebhookReceipt)
async def receive_paypal_webhook(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
) -> WebhookReceipt:
    """Receive, verify, deduplicate, and reconcile a PayPal webhook."""
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8", errors="replace")

    webhook_id = settings.paypal_webhook_id
    if not webhook_id:
        # Fail closed: webhook registration has not happened yet.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PayPal webhook is not configured; signature verification unavailable.",
        )

    # 1. Signature verification.
    cert_http = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    try:
        signature_valid = await verify_webhook_signature(
            transmission_id=request.headers.get("PayPal-Transmission-Id", ""),
            transmission_time=request.headers.get("PayPal-Transmission-Time", ""),
            webhook_id=webhook_id.get_secret_value(),
            raw_body=body_str,
            signature_b64=request.headers.get("PayPal-Transmission-Sig", ""),
            cert_url=request.headers.get("PayPal-Cert-Url", ""),
            auth_algo=request.headers.get("PayPal-Auth-Algo", ""),
            http=cert_http,
        )
    finally:
        await cert_http.aclose()
    if not signature_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature.")

    try:
        event = json.loads(body_str)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed webhook payload.")

    event_id = event.get("id", "")
    event_type = event.get("event_type", "")
    if not event_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook missing event id.")

    # 2. Deduplication + transactional processing.
    # Claim, settlement and the 'processed' status update all commit atomically.
    # On any processing/database failure the whole transaction rolls back, so the
    # event can be retried later without duplicating inventory movements or revenue.
    async with conn.transaction():
        inserted = await conn.execute(
            """
            INSERT INTO webhook_events (provider, event_id, event_type, status, payload)
            VALUES ('paypal', $1, $2, 'processing', $3::jsonb)
            ON CONFLICT (provider, event_id) DO NOTHING
            """,
            event_id,
            event_type,
            body_str,
        )
        if inserted == "INSERT 0 0":
            existing = await conn.fetchrow(
                "SELECT status FROM webhook_events WHERE provider = 'paypal' AND event_id = $1",
                event_id,
            )
            if existing and existing["status"] == "processed":
                # Successfully processed duplicate -> ack without settling again.
                return WebhookReceipt(event_id=event_id, event_type=event_type, status="duplicate")
            # Otherwise this is a retry of a previously failed/interrupted event:
            # re-mark it as processing and reprocess below.
            await conn.execute(
                "UPDATE webhook_events SET status = 'processing', error_message = NULL, updated_at = now() "
                "WHERE provider = 'paypal' AND event_id = $1",
                event_id,
            )

        try:
            if event_type == _PAYPAL_COMPLETED_EVENT:
                await _process_capture_completed(conn, event)
                status_label = "processed"
            elif event_type == _PAYPAL_DENIED_EVENT:
                logger.info("PayPal capture denied for event %s", event_id)
                status_label = "processed"
            else:
                # Other events (APPROVED, etc.) are acknowledged but ignored.
                status_label = "ignored"
        except Exception as exc:
            # Roll everything back (claim + any partial settlement) so the event
            # remains retriable.
            logger.error("Webhook event %s processing failed: %s", event_id, exc)
            raise

        await conn.execute(
            "UPDATE webhook_events SET status = $1, error_message = NULL, updated_at = now() "
            "WHERE provider = 'paypal' AND event_id = $2",
            status_label,
            event_id,
        )

    return WebhookReceipt(event_id=event_id, event_type=event_type, status=status_label)


async def _process_capture_completed(conn: asyncpg.Connection, event: Dict[str, Any]) -> None:
    """Settle a completed capture, reconciling with the internal order."""
    resource = event.get("resource") or {}
    capture = resource.get("capture", resource) if isinstance(resource, dict) else {}
    custom_id = str(capture.get("custom_id") or resource.get("custom_id") or "")
    capture_id = str(capture.get("id") or resource.get("id") or "")
    if not capture_id:
        raise ValueError("Capture event has no capture id.")
    if not custom_id:
        raise ValueError("Capture event has no custom_id linking an internal order.")

    # custom_id format is "<merchant_id>:<order_id>".
    if _CUSTOM_ID_SEPARATOR not in custom_id:
        raise ValueError("Invalid custom_id format.")
    merchant_id, order_id = custom_id.split(_CUSTOM_ID_SEPARATOR, 1)

    amount_block = capture.get("amount") or {}
    try:
        amount = Decimal(str(amount_block.get("value")))
    except (TypeError, ValueError):
        raise ValueError("Capture event amount is missing or invalid.")
    currency = str(amount_block.get("currency_code") or "USD").upper()

    capture_details = {
        "paypal_capture_id": capture_id,
        "amount": amount,
        "currency": currency,
        "status": "completed",
        "raw_payload": capture,
    }
    await settle_capture(conn, merchant_id, order_id, capture_details)


# ---------------------------------------------------------------------------
# PayPal hosted-checkout return / cancel callbacks + owner cancellation
# ---------------------------------------------------------------------------
@router.get("/{order_id}/return")
async def paypal_return(
    order_id: str,
    token: str = Query(..., description="PayPal order token"),
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
):
    """Read-only return redirect after buyer approval. NEVER auto-captures; the
    merchant must complete the existing capture action."""
    order = await _get_order_for_callback(conn, merchant_id, order_id, token)
    return {
        "merchant_id": merchant_id,
        "order_id": order_id,
        "order_number": order["order_number"],
        "status": order["status"],
        "message": "Approval received. The merchant must complete the capture action.",
    }


@router.get("/{order_id}/cancel", response_model=PaymentCancelResponse)
async def paypal_cancel(
    order_id: str,
    token: str = Query(..., description="PayPal order token"),
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> PaymentCancelResponse:
    """PayPal cancel redirect: cancels only a still-pending order (releases its
    reservation exactly once); rejects completed/captured orders."""
    await _get_order_for_callback(conn, merchant_id, order_id, token)
    return await _cancel_pending_checkout(conn, merchant_id, order_id)


@router.post("/{order_id}/cancel", response_model=PaymentCancelResponse)
async def owner_cancel_checkout(
    order_id: str,
    merchant_id: str = Depends(get_merchant_context),
    conn: asyncpg.Connection = Depends(get_db),
) -> PaymentCancelResponse:
    """Tenant-scoped owner cancellation of an existing stale PENDING internal
    checkout, releasing its online reservation exactly once. Safe and
    idempotent on repeat calls."""
    return await _cancel_pending_checkout(conn, merchant_id, order_id)
