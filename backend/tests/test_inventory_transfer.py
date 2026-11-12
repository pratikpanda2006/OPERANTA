"""
backend/tests/test_inventory_transfer.py
=========================================
Offline unit tests for the M12.3.4 store→online inventory transfer operation.

Uses a minimal fake asyncpg connection (no live PostgreSQL, no network, no
PayPal). Covers:
- successful one-unit transfer of the Blue Vase (store 12→11, online 0→1,
  total on hand preserved, movement + audit records written)
- insufficient unreserved store stock rejection
- invalid (zero/negative) quantity rejection
- cross-merchant product rejection
- duplicate idempotency-key replay rejection
- reservations respected (store stock available net of reservations)
"""

import contextlib

import pytest

from app.db.repositories.inventory_repo import (
    InventoryTransferError,
    TransferDuplicateError,
    TransferInsufficientStockError,
    TransferNotFoundError,
    transfer_store_to_online,
)

MERCHANT = "merchant_northstar"
PRODUCT = "prod_blue_vase"


def _inv(store=12, reserved_store=0, online=0, reserved_online=0):
    return {
        "stock_in_store": store,
        "reserved_in_store": reserved_store,
        "stock_online": online,
        "reserved_online": reserved_online,
    }


class FakeConn:
    """Minimal asyncpg-like connection for repository unit tests."""

    def __init__(self):
        self.fetchrow_q = []
        self.fetch_q = []
        self.execute_sql = []
        self.execute_args = []
        self.all_sql = []
        self._fr = 0
        self._f = 0

    def add_fetchrow(self, row):
        self.fetchrow_q.append(row)

    def add_fetch(self, rows):
        self.fetch_q.append(rows)

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, sql, *args):
        self.all_sql.append(sql)
        if self._fr < len(self.fetchrow_q):
            row = self.fetchrow_q[self._fr]
            self._fr += 1
            return row
        return None

    async def fetch(self, sql, *args):
        self.all_sql.append(sql)
        if self._f < len(self.fetch_q):
            rows = self.fetch_q[self._f]
            self._f += 1
            return rows
        return []

    async def execute(self, sql, *args):
        self.execute_sql.append(sql)
        self.execute_args.append(args)
        self.all_sql.append(sql)
        return "UPDATE 1"


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_transfer_of_one_blue_vase():
    conn = FakeConn()
    conn.add_fetchrow(_inv(store=12, online=0))  # locked inventory row
    conn.add_fetch([])  # no existing idempotency keys

    result = await transfer_store_to_online(
        conn,
        merchant_id=MERCHANT,
        product_id=PRODUCT,
        quantity=1,
        idempotency_key="kt01-bluevase",
        reason="first paypal sandbox checkout",
    )

    assert result["merchant_id"] == MERCHANT
    assert result["product_id"] == PRODUCT
    assert result["quantity"] == 1
    assert result["stock_in_store"] == 11
    assert result["stock_online"] == 1
    # total on hand preserved: 11 + 1 == 12
    assert result["stock_in_store"] + result["stock_online"] == 12
    assert result["available_in_store"] == 11
    assert result["available_online"] == 1
    assert len(result["movement_ids"]) == 2
    assert result["reference_id"].startswith("xfer_")

    # Exactly one inventory_items UPDATE touching only the two channel balances.
    updates = [s for s in conn.execute_sql if "UPDATE inventory_items" in s]
    assert len(updates) == 1
    assert "stock_in_store" in updates[0] and "stock_online" in updates[0]
    assert "reserved_in_store" not in updates[0] and "reserved_online" not in updates[0]

    # Exactly two movement inserts, one per channel, using the transfer convention.
    movs = [s for s in conn.execute_sql if "INSERT INTO inventory_movements" in s]
    assert len(movs) == 2
    assert all("'transfer'" in s for s in movs)  # movement_type + reference_type

    # Bind args: (id, merchant_id, product_id, channel, delta, balance, ref_id, key)
    mov_args = [a for s, a in zip(conn.execute_sql, conn.execute_args)
                if "INSERT INTO inventory_movements" in s]
    channels = [a[3] for a in mov_args]
    deltas = [a[4] for a in mov_args]
    balances = [a[5] for a in mov_args]
    assert sorted(channels) == ["in_store", "online"]
    assert sorted(deltas) == [-1, 1]
    assert sorted(balances) == [1, 11]
    assert all(a[6] == result["reference_id"] for a in mov_args)
    assert all(a[7].startswith("kt01-bluevase_") for a in mov_args)

    # Exactly one audit event with server-derived actor.
    audits = [s for s in conn.execute_sql if "INSERT INTO operational_audit_log" in s]
    assert len(audits) == 1
    audit_args = [a for s, a in zip(conn.execute_sql, conn.execute_args)
                  if "INSERT INTO operational_audit_log" in s]
    assert audit_args[0][0] == MERCHANT
    assert audit_args[0][1] == "merchant_owner"
    assert audit_args[0][2] == f"owner_{MERCHANT}"  # server-derived actor
    assert audit_args[0][3] == PRODUCT


# ---------------------------------------------------------------------------
# Insufficient store stock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_insufficient_unreserved_store_stock_rejected():
    conn = FakeConn()
    conn.add_fetchrow(_inv(store=2, online=0))  # only 2 unreserved units
    conn.add_fetch([])

    with pytest.raises(TransferInsufficientStockError):
        await transfer_store_to_online(
            conn, merchant_id=MERCHANT, product_id=PRODUCT,
            quantity=5, idempotency_key="kt02-insufficient",
        )

    # No mutation executed.
    assert conn.execute_sql == []


@pytest.mark.asyncio
async def test_insufficient_when_reservations_consume_store_stock():
    conn = FakeConn()
    conn.add_fetchrow(_inv(store=12, reserved_store=10, online=0))  # avail=2
    conn.add_fetch([])

    with pytest.raises(TransferInsufficientStockError):
        await transfer_store_to_online(
            conn, merchant_id=MERCHANT, product_id=PRODUCT,
            quantity=5, idempotency_key="kt03-reserved",
        )
    assert conn.execute_sql == []


# ---------------------------------------------------------------------------
# Invalid quantity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("qty", [0, -3])
async def test_invalid_quantity_rejected(qty):
    conn = FakeConn()
    with pytest.raises(InventoryTransferError):
        await transfer_store_to_online(
            conn, merchant_id=MERCHANT, product_id=PRODUCT,
            quantity=qty, idempotency_key="kt04-badqty",
        )
    # Nothing read or written.
    assert conn.all_sql == []
    assert conn.execute_sql == []


# ---------------------------------------------------------------------------
# Cross-merchant product
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_merchant_product_rejected():
    conn = FakeConn()
    conn.add_fetchrow(None)  # product not found for this merchant
    conn.add_fetch([])

    with pytest.raises(TransferNotFoundError):
        await transfer_store_to_online(
            conn, merchant_id=MERCHANT, product_id=PRODUCT,
            quantity=1, idempotency_key="kt05-crossmerchant",
        )
    assert conn.execute_sql == []


# ---------------------------------------------------------------------------
# Duplicate / idempotency replay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_idempotency_key_rejected_no_double_transfer():
    conn = FakeConn()
    conn.add_fetchrow(_inv(store=12, online=0))
    conn.add_fetch([{"idempotency_key": "kt06-bluevase_prod_blue_vase:in_store"}])

    with pytest.raises(TransferDuplicateError):
        await transfer_store_to_online(
            conn, merchant_id=MERCHANT, product_id=PRODUCT,
            quantity=1, idempotency_key="kt06-bluevase",
        )
    # No inventory mutation on replay.
    assert all("UPDATE inventory_items" not in s for s in conn.execute_sql)


# ---------------------------------------------------------------------------
# Reservations preserved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transfer_preserves_reservations():
    conn = FakeConn()
    conn.add_fetchrow(_inv(store=12, reserved_store=2, online=0, reserved_online=0))
    conn.add_fetch([])

    result = await transfer_store_to_online(
        conn, merchant_id=MERCHANT, product_id=PRODUCT,
        quantity=1, idempotency_key="kt07-respected",
    )

    # Available store stock net of reservations: 11 - 2 = 9.
    assert result["stock_in_store"] == 11
    assert result["available_in_store"] == 9
    assert result["stock_online"] == 1
    assert result["available_online"] == 1

    update = next(s for s in conn.execute_sql if "UPDATE inventory_items" in s)
    # Reservations are never part of the update.
    assert "reserved_in_store" not in update and "reserved_online" not in update
