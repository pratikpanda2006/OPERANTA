"""
backend/app/db/repositories/merchant_repo.py
============================================
Data access layer for merchant profiles, spending limits, and spend envelope tracking.
All queries strictly enforce tenant scoping via positional parameters.

SCHEMA ALIGNMENT NOTES (migration 001):
  merchants columns used:
    - id                    (VARCHAR 64)
    - active_demo_week_cap  → aliased as demo_envelope_cap
    - per_order_limit       → aliased as max_order_cost_ceiling
    - created_at            → aliased as merchant_created_at
  financial_ledger column:
    - running_envelope_balance  (NOT "running_balance")

  merchants does NOT have: demo_envelope_cap, max_order_cost_ceiling, active_demo_week_start
  These are derived / aliased from the actual schema columns.
"""

from decimal import Decimal
from typing import Optional
import asyncpg


async def get_spend_envelope(
    conn: asyncpg.Connection,
    merchant_id: str,
) -> Optional[dict]:
    """
    Retrieve the spend envelope metrics for the active demo week for a specific merchant.
    Derives committed spend and remaining balance from the append-only financial ledger.

    Column mapping from migration 001:
      - merchants.active_demo_week_cap  → demo_envelope_cap  ($500.00 for Northstar)
      - merchants.per_order_limit       → max_order_cost_ceiling ($150.00)
      - merchants.created_at            → merchant_created_at
      - financial_ledger.running_envelope_balance → available_spend
      - committed_spend = demo_envelope_cap - available_spend

    Returns:
        dict matching SpendEnvelopeResponse schema, or None if merchant does not exist.
    """
    merchant_row = await conn.fetchrow(
        """
        SELECT
            id                   AS merchant_id,
            active_demo_week_cap AS demo_envelope_cap,
            per_order_limit      AS max_order_cost_ceiling,
            created_at           AS merchant_created_at
        FROM merchants
        WHERE id = $1
        """,
        merchant_id,
    )
    if not merchant_row:
        return None

    cap = Decimal(str(merchant_row["demo_envelope_cap"]))
    max_ceiling = Decimal(str(merchant_row["max_order_cost_ceiling"]))
    created_at = merchant_row["merchant_created_at"]

    # Query the latest ledger running balance for this merchant.
    # NOTE: the column is running_envelope_balance in migration 001, NOT running_balance.
    ledger_row = await conn.fetchrow(
        """
        SELECT running_envelope_balance
        FROM financial_ledger
        WHERE merchant_id = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        merchant_id,
    )

    if ledger_row and ledger_row["running_envelope_balance"] is not None:
        available = Decimal(str(ledger_row["running_envelope_balance"]))
        committed = cap - available
    else:
        # Default uncommitted baseline if no ledger transactions have occurred
        available = cap
        committed = Decimal("0.00")

    return {
        "merchant_id": merchant_id,
        "demo_envelope_cap": cap,
        "max_order_cost_ceiling": max_ceiling,
        "committed_spend": committed,
        "available_spend": available,
        "merchant_created_at": created_at,
        "currency": "USD",
    }
