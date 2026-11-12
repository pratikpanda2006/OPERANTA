"""
backend/tests/test_database_live.py
=====================================
Live database integration tests for MarginGuard Milestone 6, Stage 3A.

SAFETY CONSTRAINTS
------------------
- These tests ONLY run when the environment variable TEST_DATABASE_URL is set
  to exactly "postgresql://postgres@127.0.0.1:5433/marginguard_test".
- The target host is validated to be a loopback address before any connection
  is opened. Tests refuse to run against any remote database.
- Destructive assertions use rolled-back transactions so seed data is preserved.
- No test modifies committed state in schema_migrations or the seed dataset.
- Tests MUST NOT be run against Supabase or any production / staging database.

RUNNING THESE TESTS
-------------------
From the workspace root (marginguard/), after Stage 3A local PostgreSQL
validation:

    $env:TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:5433/marginguard_test"
    $env:PYTHONPATH="backend"
    backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_database_live.py -v --asyncio-mode=auto

The offline tests (test_migrations_syntax.py, test_config.py) are run
separately and are NOT imported here.
"""

import asyncio
import hashlib
import os
import secrets
import urllib.parse
import uuid
from pathlib import Path

import asyncpg
import pytest

# ---------------------------------------------------------------------------
# Guard — this module must only target the approved local test database.
# ---------------------------------------------------------------------------

_APPROVED_URL = "postgresql://postgres@127.0.0.1:5433/marginguard_test"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

_parsed = urllib.parse.urlsplit(TEST_DATABASE_URL) if TEST_DATABASE_URL else None
_configured_host = (_parsed.hostname or "").lower() if _parsed else ""

_skip_reason = ""
if not TEST_DATABASE_URL:
    _skip_reason = "TEST_DATABASE_URL not set — skipping live database tests"
elif TEST_DATABASE_URL.rstrip("/") != _APPROVED_URL.rstrip("/"):
    _skip_reason = (
        f"TEST_DATABASE_URL does not match the approved local test URL "
        f"(expected '{_APPROVED_URL}', got '{TEST_DATABASE_URL}'). "
        "Live tests will not run against an unapproved target."
    )
elif _configured_host not in _LOOPBACK_HOSTS:
    _skip_reason = (
        f"TEST_DATABASE_URL host '{_configured_host}' is not a loopback address. "
        "Live integration tests refuse to execute against remote databases."
    )

_live = pytest.mark.skipif(bool(_skip_reason), reason=_skip_reason or "approved")


# ---------------------------------------------------------------------------
# Shared async connection helper — single source of truth for all
# administrative connections to the approved local test database.
# ---------------------------------------------------------------------------


async def _connect() -> asyncpg.Connection:
    """
    Open a direct asyncpg connection to the approved test database.

    Reads the connection URL from TEST_DATABASE_URL and the password from
    TEST_DATABASE_PASSWORD. Raises a clear error if either is missing or empty.
    Preserves the approved-database URL / loopback safety checks enforced at
    module scope above. SSL is explicitly disabled because the target is a
    loopback address with no TLS.
    """
    test_url = os.environ.get("TEST_DATABASE_URL", "")
    if not test_url:
        raise RuntimeError("TEST_DATABASE_URL environment variable is missing or empty.")

    test_password = os.environ.get("TEST_DATABASE_PASSWORD", "")
    if not test_password:
        raise RuntimeError("TEST_DATABASE_PASSWORD environment variable is missing or empty.")

    parsed = urllib.parse.urlsplit(test_url)
    host = (parsed.hostname or "").lower()
    if parsed.path != "/marginguard_test" or parsed.port != 5433 or host not in _LOOPBACK_HOSTS:
        raise RuntimeError("TEST_DATABASE_URL does not match the approved loopback test target.")

    return await asyncpg.connect(
        test_url,
        password=test_password,
        ssl=False,
    )


# ---------------------------------------------------------------------------
# asyncpg connection fixture — function-scoped, one fresh connection per test
# ---------------------------------------------------------------------------


@pytest.fixture()
async def pg():
    """
    Function-scoped asyncpg connection to the approved local test database.
    SSL is explicitly disabled because the target is a loopback address with no TLS.
    A fresh connection is opened before each test and closed after.
    """
    conn = await _connect()
    yield conn
    if not conn.is_closed():
        await conn.close()


# ---------------------------------------------------------------------------
# A. Table manifest — exactly 21 tables (ground truth from actual migration run)
# ---------------------------------------------------------------------------

EXPECTED_TABLES = sorted([
    "dispute_cases",
    "dispute_evidence",
    "financial_ledger",
    "inventory_items",
    "inventory_movements",
    "merchants",
    "operational_audit_log",
    "order_items",
    "order_refunds",
    "orders",
    "paypal_captures",
    "procurement_requests",
    "products",
    "purchase_order_approvals",
    "purchase_order_items",
    "purchase_orders",
    "schema_migrations",
    "spend_reservations",
    "supplier_offers",
    "suppliers",
    "webhook_events",
])


@_live
@pytest.mark.asyncio
async def test_table_manifest_count(pg):
    """Exactly 21 tables must exist in the public schema."""
    rows = await pg.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    )
    actual = sorted(r["table_name"] for r in rows)
    assert len(actual) == 21, (
        f"Expected 21 tables, found {len(actual)}.\nActual: {actual}"
    )


@_live
@pytest.mark.asyncio
async def test_table_manifest_names(pg):
    """All 21 expected table names must be present in the public schema."""
    rows = await pg.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    )
    actual = sorted(r["table_name"] for r in rows)
    assert actual == EXPECTED_TABLES, (
        f"Table name mismatch.\nExpected: {EXPECTED_TABLES}\nActual:   {actual}"
    )


# ---------------------------------------------------------------------------
# B. schema_migrations checksums
# ---------------------------------------------------------------------------

# The four migrations that must be recorded, in application order.
EXPECTED_MIGRATIONS = [
    "001_initial_schema.sql",
    "002_seed_northstar_living.sql",
    "003_fix_po_landed_cost_constraint.sql",
    "004_paypal_online_checkout.sql",
]

# Directory containing the migration SQL files in the repository.
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _sha256_file(filepath: Path) -> str:
    """Return lowercase hex SHA-256 digest of a migration SQL file's bytes."""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


@_live
@pytest.mark.asyncio
async def test_migration_checksums_recorded(pg):
    """
    All four applied migrations must be recorded in schema_migrations, in the
    expected sequence, with SHA-256 checksums that exactly match the migration
    source files in the repository. Fails if a migration is missing, duplicated,
    unexpectedly added, or has a mismatched checksum.
    """
    rows = await pg.fetch(
        "SELECT filename, checksum_sha256 FROM schema_migrations ORDER BY id"
    )

    # Exactly four records, in the expected sequence (no missing/dup/unexpected).
    assert [row["filename"] for row in rows] == EXPECTED_MIGRATIONS, (
        f"Expected migration sequence {EXPECTED_MIGRATIONS}, "
        f"got {[row['filename'] for row in rows]}"
    )

    for row in rows:
        fname = row["filename"]
        source = MIGRATIONS_DIR / fname
        assert source.exists(), f"Migration source file missing in repository: {fname}"

        expected_full = _sha256_file(source)
        actual = row["checksum_sha256"]
        assert actual.lower() == expected_full, (
            f"Checksum mismatch for {fname}: "
            f"expected {expected_full}, got {actual}"
        )


# ---------------------------------------------------------------------------
# C. Seed data reconciliation
# ---------------------------------------------------------------------------


@_live
@pytest.mark.asyncio
async def test_seed_merchant_northstar_exists(pg):
    """Northstar Living merchant record must exist."""
    row = await pg.fetchrow(
        "SELECT id, name FROM merchants WHERE id = 'merchant_northstar'"
    )
    assert row is not None, "Northstar Living merchant not found in merchants table"
    assert "northstar" in (row["name"] or "").lower() or "northstar" in row["id"].lower()


@_live
@pytest.mark.asyncio
async def test_seed_suppliers_count(pg):
    """Exactly 3 suppliers for merchant_northstar must be seeded."""
    count = await pg.fetchval(
        "SELECT COUNT(*) FROM suppliers WHERE merchant_id = 'merchant_northstar'"
    )
    assert count == 3, f"Expected 3 suppliers, found {count}"


@_live
@pytest.mark.asyncio
async def test_seed_blue_vase_product(pg):
    """Blue Ceramic Vase product must be seeded with correct costs."""
    row = await pg.fetchrow(
        "SELECT base_cost, selling_price FROM products WHERE id = 'prod_blue_vase'"
    )
    assert row is not None, "prod_blue_vase not found in products table"
    assert float(row["base_cost"]) == pytest.approx(4.10, abs=0.001), (
        f"Expected base_cost=4.10, got {row['base_cost']}"
    )
    assert float(row["selling_price"]) == pytest.approx(24.00, abs=0.001), (
        f"Expected selling_price=24.00, got {row['selling_price']}"
    )


@_live
@pytest.mark.asyncio
async def test_seed_inventory_blue_vase(pg):
    """Blue Ceramic Vase inventory must have correct quantities and reorder point."""
    row = await pg.fetchrow(
        "SELECT stock_in_store, reorder_point, available_in_store "
        "FROM inventory_items WHERE product_id = 'prod_blue_vase'"
    )
    assert row is not None, "inventory_items row for prod_blue_vase not found"
    assert row["stock_in_store"] == 12, (
        f"Expected stock_in_store=12, got {row['stock_in_store']}"
    )
    assert row["reorder_point"] == 14, (
        f"Expected reorder_point=14, got {row['reorder_point']}"
    )
    assert row["available_in_store"] == 12, (
        f"Expected available_in_store=12, got {row['available_in_store']}"
    )


@_live
@pytest.mark.asyncio
async def test_seed_inventory_movement_reconciliation(pg):
    """
    Query actual records from inventory_movements to reconcile movement history
    against stored inventory balance:
      1. Opening inventory: +14 (mov_opening_vase_01, movement_type='opening_balance')
      2. POS sale: -2 (mov_pos_sale_vase_01, movement_type='pos_sale')
      3. Sum of quantity_delta: 14 + (-2) = 12
      4. Compares with inventory_items.stock_in_store = 12, reorder_point = 14
    """
    merchant_id = "merchant_northstar"
    product_id = "prod_blue_vase"

    # Query individual movements
    opening_row = await pg.fetchrow(
        """
        SELECT quantity_delta, balance_after, movement_type
        FROM inventory_movements
        WHERE merchant_id = $1 AND product_id = $2 AND movement_type = 'opening_balance'
        """,
        merchant_id, product_id,
    )
    assert opening_row is not None, "Missing opening_balance movement record"
    assert opening_row["quantity_delta"] == 14, f"Expected opening +14, got {opening_row['quantity_delta']}"
    assert opening_row["balance_after"] == 14, f"Expected opening balance_after=14, got {opening_row['balance_after']}"

    sale_row = await pg.fetchrow(
        """
        SELECT quantity_delta, balance_after, movement_type
        FROM inventory_movements
        WHERE merchant_id = $1 AND product_id = $2 AND movement_type = 'pos_sale'
        """,
        merchant_id, product_id,
    )
    assert sale_row is not None, "Missing pos_sale movement record"
    assert sale_row["quantity_delta"] == -2, f"Expected pos_sale -2, got {sale_row['quantity_delta']}"
    assert sale_row["balance_after"] == 12, f"Expected pos_sale balance_after=12, got {sale_row['balance_after']}"

    # Sum signed movement quantities
    movement_sum = await pg.fetchval(
        """
        SELECT SUM(quantity_delta)
        FROM inventory_movements
        WHERE merchant_id = $1 AND product_id = $2
        """,
        merchant_id, product_id,
    )
    assert movement_sum == 12, f"Expected movement sum=12, got {movement_sum}"

    # Reconcile against inventory_items
    item_row = await pg.fetchrow(
        """
        SELECT stock_in_store, available_in_store, reorder_point
        FROM inventory_items
        WHERE merchant_id = $1 AND product_id = $2
        """,
        merchant_id, product_id,
    )
    assert item_row is not None, "Missing inventory_items record"
    assert item_row["stock_in_store"] == movement_sum, (
        f"Inventory balance ({item_row['stock_in_store']}) does not reconcile with movement sum ({movement_sum})"
    )
    assert item_row["available_in_store"] == 12, f"Expected available_in_store=12, got {item_row['available_in_store']}"
    assert item_row["reorder_point"] == 14, f"Expected reorder_point=14, got {item_row['reorder_point']}"


@_live
@pytest.mark.asyncio
async def test_seed_financial_ledger_balance(pg):
    """
    Financial ledger running_envelope_balance must reflect 325.00
    (500 envelope allocation - 175 prior commitments).
    """
    total = await pg.fetchval(
        "SELECT SUM(amount) FROM financial_ledger WHERE merchant_id = 'merchant_northstar'"
    )
    assert total is not None, "financial_ledger has no rows for merchant_northstar"
    assert float(total) == pytest.approx(325.00, abs=0.01), (
        f"Expected SUM(amount)=325.00, got {total}"
    )

    latest_balance = await pg.fetchval(
        "SELECT running_envelope_balance FROM financial_ledger "
        "WHERE merchant_id = 'merchant_northstar' ORDER BY id DESC LIMIT 1"
    )
    assert float(latest_balance) == pytest.approx(325.00, abs=0.01), (
        f"Expected running_envelope_balance=325.00, got {latest_balance}"
    )


# ---------------------------------------------------------------------------
# D. CHECK constraint enforcement
# ---------------------------------------------------------------------------


@_live
@pytest.mark.asyncio
async def test_constraint_po_landed_cost_ceiling():
    """purchase_orders.total_landed_cost > 150.00 must violate CHECK constraint."""
    conn = await _connect()
    try:
        merchant_id = "merchant_northstar"
        supplier_id = await conn.fetchval(
            "SELECT id FROM suppliers WHERE merchant_id = $1 LIMIT 1", merchant_id
        )
        assert supplier_id, "No supplier found for constraint test"

        violated = False
        try:
            await conn.execute("BEGIN")
            test_id = f"test-po-{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO purchase_orders (
                    id, merchant_id, supplier_id, po_number, status,
                    item_subtotal, total_landed_cost, has_unknown_costs,
                    payment_method, payment_status, is_illustrative
                ) VALUES ($1, $2, $3, $4, 'draft', 0.00, 150.01, FALSE,
                          'paypal_payouts', 'unpaid', TRUE)
                """,
                test_id, merchant_id, supplier_id, f"PO-TEST-{test_id}",
            )
        except asyncpg.CheckViolationError:
            violated = True
        finally:
            await conn.execute("ROLLBACK")

        assert violated, "Expected CHECK violation for total_landed_cost = 150.01"
    finally:
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_constraint_po_unknown_cost_nullifies_total():
    """has_unknown_costs=TRUE with a non-NULL total_landed_cost must be rejected."""
    conn = await _connect()
    try:
        merchant_id = "merchant_northstar"
        supplier_id = await conn.fetchval(
            "SELECT id FROM suppliers WHERE merchant_id = $1 LIMIT 1", merchant_id
        )
        assert supplier_id

        violated = False
        try:
            await conn.execute("BEGIN")
            test_id = f"test-po-{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO purchase_orders (
                    id, merchant_id, supplier_id, po_number, status,
                    item_subtotal, total_landed_cost, has_unknown_costs,
                    payment_method, payment_status, is_illustrative
                ) VALUES ($1, $2, $3, $4, 'draft', 0.00, 100.00, TRUE,
                          'paypal_payouts', 'unpaid', TRUE)
                """,
                test_id, merchant_id, supplier_id, f"PO-TEST-{test_id}",
            )
        except asyncpg.CheckViolationError:
            violated = True
        finally:
            await conn.execute("ROLLBACK")

        assert violated, (
            "Expected CHECK violation for has_unknown_costs=TRUE with total_landed_cost=100.00"
        )
    finally:
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_constraint_po_negative_shipping():
    """purchase_orders.shipping_cost < 0 must be rejected."""
    conn = await _connect()
    try:
        merchant_id = "merchant_northstar"
        supplier_id = await conn.fetchval(
            "SELECT id FROM suppliers WHERE merchant_id = $1 LIMIT 1", merchant_id
        )
        assert supplier_id

        violated = False
        try:
            await conn.execute("BEGIN")
            test_id = f"test-po-{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO purchase_orders (
                    id, merchant_id, supplier_id, po_number, status,
                    item_subtotal, shipping_cost, has_unknown_costs,
                    payment_method, payment_status, is_illustrative
                ) VALUES ($1, $2, $3, $4, 'draft', 0.00, -1.00, FALSE,
                          'paypal_payouts', 'unpaid', TRUE)
                """,
                test_id, merchant_id, supplier_id, f"PO-TEST-{test_id}",
            )
        except asyncpg.CheckViolationError:
            violated = True
        finally:
            await conn.execute("ROLLBACK")

        assert violated, "Expected CHECK violation for shipping_cost = -1.00"
    finally:
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_constraint_po_negative_total_landed_cost():
    """purchase_orders.total_landed_cost < 0 must be rejected."""
    conn = await _connect()
    try:
        merchant_id = "merchant_northstar"
        supplier_id = await conn.fetchval(
            "SELECT id FROM suppliers WHERE merchant_id = $1 LIMIT 1", merchant_id
        )
        assert supplier_id

        violated = False
        try:
            await conn.execute("BEGIN")
            test_id = f"test-po-{uuid.uuid4().hex[:8]}"
            await conn.execute(
                """
                INSERT INTO purchase_orders (
                    id, merchant_id, supplier_id, po_number, status,
                    item_subtotal, total_landed_cost, has_unknown_costs,
                    payment_method, payment_status, is_illustrative
                ) VALUES ($1, $2, $3, $4, 'draft', 0.00, -0.01, FALSE,
                          'paypal_payouts', 'unpaid', TRUE)
                """,
                test_id, merchant_id, supplier_id, f"PO-TEST-{test_id}",
            )
        except asyncpg.CheckViolationError:
            violated = True
        finally:
            await conn.execute("ROLLBACK")

        assert violated, "Expected CHECK violation for total_landed_cost = -0.01"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# E. Runtime Composite Foreign-Key Verification
# ---------------------------------------------------------------------------


@_live
@pytest.mark.asyncio
async def test_fk_cross_merchant_composite_rejection():
    """
    Verify merchant-scoped composite foreign keys reject child records referencing
    a parent belonging to another merchant:
    inventory_items (merchant_two, prod_blue_vase) must be rejected because
    prod_blue_vase belongs to merchant_northstar.
    """
    conn = await _connect()
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            # Create a second merchant
            await conn.execute("INSERT INTO merchants (id, name) VALUES ('merchant_two', 'Second Merchant')")

            violated = False
            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO inventory_items (
                            merchant_id, id, product_id, stock_in_store, reserved_in_store,
                            stock_online, reserved_online, reorder_point, daily_demand, lead_days, safety_stock
                        ) VALUES ('merchant_two', 'inv_cross', 'prod_blue_vase', 10, 0, 0, 0, 5, 1, 1, 1)
                        """
                    )
            except asyncpg.ForeignKeyViolationError:
                violated = True

            assert violated, "Expected composite FK violation for cross-merchant inventory item insert"
        finally:
            await tr.rollback()
    finally:
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_fk_refund_relationships():
    """
    Verify refund-to-order and refund-to-capture composite foreign keys:
      1. Refund referencing a nonexistent order is rejected.
      2. Refund referencing a valid capture but an order different from capture's order_id is rejected.
      3. Valid matching (merchant_id, order_id, capture_id) is accepted.
    """
    conn = await _connect()
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            # Seed a valid paypal_capture for order_pos_demo_01
            await conn.execute(
                """
                INSERT INTO paypal_captures (
                    merchant_id, id, order_id, paypal_capture_id, status, captured_amount, amount_refunded, currency
                ) VALUES ('merchant_northstar', 'cap_demo_01', 'order_pos_demo_01', 'PAYPAL_CAP_01', 'completed', 51.84, 0.00, 'USD')
                """
            )

            # 1. Nonexistent order rejection
            nonexistent_order_rejected = False
            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO order_refunds (
                            merchant_id, id, order_id, capture_id, amount, status, reason, idempotency_key
                        ) VALUES ('merchant_northstar', 'ref_bad_order', 'order_nonexistent', 'cap_demo_01', 10.00, 'pending', 'test', 'idemp_bad_order')
                        """
                    )
            except asyncpg.ForeignKeyViolationError:
                nonexistent_order_rejected = True

            assert nonexistent_order_rejected, "Expected FK violation when order_id does not exist"

            # Create a second order belonging to merchant_northstar
            await conn.execute(
                """
                INSERT INTO orders (
                    merchant_id, id, order_number, channel, status, subtotal, tax_amount, shipping_amount, total_amount, payment_method
                ) VALUES ('merchant_northstar', 'order_pos_demo_02', 'POS-2026-002', 'pos', 'completed', 20.00, 1.60, 0.00, 21.60, 'card_in_store')
                """
            )

            # 2. Mismatched order/capture rejection: ref references order_pos_demo_02 but cap belongs to order_pos_demo_01
            mismatch_rejected = False
            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO order_refunds (
                            merchant_id, id, order_id, capture_id, amount, status, reason, idempotency_key
                        ) VALUES ('merchant_northstar', 'ref_mismatch', 'order_pos_demo_02', 'cap_demo_01', 10.00, 'pending', 'test', 'idemp_mismatch')
                        """
                    )
            except asyncpg.ForeignKeyViolationError:
                mismatch_rejected = True

            assert mismatch_rejected, (
                "Expected composite FK violation: capture_id does not belong to specified order_id"
            )

            # 3. Valid matching relationship is accepted
            await conn.execute(
                """
                INSERT INTO order_refunds (
                    merchant_id, id, order_id, capture_id, amount, status, reason, idempotency_key
                ) VALUES ('merchant_northstar', 'ref_valid', 'order_pos_demo_01', 'cap_demo_01', 10.00, 'pending', 'test', 'idemp_valid')
                """
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM order_refunds WHERE id = 'ref_valid'"
            )
            assert count == 1, "Valid refund should be successfully inserted"
        finally:
            await tr.rollback()
    finally:
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_fk_procurement_request_delete_restricted():
    """
    Verify purchase_orders -> procurement_requests foreign key enforces ON DELETE RESTRICT:
      1. Check catalog confdeltype is 'r' (RESTRICT).
      2. Attempting to delete a procurement_request referenced by a purchase_order is rejected at runtime.
    """
    conn = await _connect()
    try:
        # Check pg_constraint catalog definition
        action = await conn.fetchval(
            """
            SELECT confdeltype::text
            FROM pg_constraint
            WHERE conname = 'purchase_orders_merchant_id_request_id_fkey'
            """
        )
        assert action == "r", f"Expected ON DELETE RESTRICT ('r'), got '{action}'"

        tr = conn.transaction()
        await tr.start()
        try:
            # Create procurement request
            await conn.execute(
                """
                INSERT INTO procurement_requests (
                    merchant_id, id, request_number, target_budget, status
                ) VALUES ('merchant_northstar', 'req_test_01', 'REQ-001', 100.00, 'approved')
                """
            )
            # Create dependent purchase order
            await conn.execute(
                """
                INSERT INTO purchase_orders (
                    merchant_id, id, po_number, request_id, supplier_id, status,
                    item_subtotal, total_landed_cost, has_unknown_costs, payment_method, payment_status, is_illustrative
                ) VALUES ('merchant_northstar', 'po_test_01', 'PO-001', 'req_test_01', 'sup_supplier_a', 'draft',
                          50.00, 50.00, FALSE, 'paypal_payouts', 'unpaid', TRUE)
                """
            )

            # Attempt deletion
            delete_blocked = False
            try:
                async with conn.transaction():
                    await conn.execute(
                        "DELETE FROM procurement_requests WHERE merchant_id = 'merchant_northstar' AND id = 'req_test_01'"
                    )
            except (asyncpg.ForeignKeyViolationError, asyncpg.RestrictViolationError):
                delete_blocked = True

            assert delete_blocked, "Deleting a referenced procurement_request must be blocked by RESTRICT"
        finally:
            await tr.rollback()
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# F. Trigger immutability enforcement
# ---------------------------------------------------------------------------


@_live
@pytest.mark.asyncio
async def test_trigger_financial_ledger_immutable():
    """UPDATE on financial_ledger must be rejected by the immutability trigger."""
    conn = await _connect()
    try:
        ledger_id = await conn.fetchval(
            "SELECT id FROM financial_ledger WHERE merchant_id = 'merchant_northstar' LIMIT 1"
        )
        assert ledger_id is not None, "No financial_ledger rows to test trigger against"

        blocked = False
        try:
            await conn.execute("BEGIN")
            await conn.execute(
                "UPDATE financial_ledger SET amount = 999 WHERE id = $1", ledger_id
            )
        except Exception as e:
            err_msg = str(e).lower()
            if any(kw in err_msg for kw in ("append-only", "immutable", "cannot update", "raise", "prohibited")):
                blocked = True
            else:
                raise
        finally:
            await conn.execute("ROLLBACK")

        assert blocked, "Expected immutability trigger to block UPDATE on financial_ledger"
    finally:
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_trigger_inventory_movements_immutable():
    """DELETE on inventory_movements must be rejected by the immutability trigger."""
    conn = await _connect()
    try:
        movement_id = await conn.fetchval(
            "SELECT id FROM inventory_movements WHERE merchant_id = 'merchant_northstar' LIMIT 1"
        )
        assert movement_id is not None, "No inventory_movements rows to test trigger against"

        blocked = False
        try:
            await conn.execute("BEGIN")
            await conn.execute(
                "DELETE FROM inventory_movements WHERE id = $1", movement_id
            )
        except Exception as e:
            err_msg = str(e).lower()
            if any(kw in err_msg for kw in ("append-only", "immutable", "cannot delete", "raise", "prohibited")):
                blocked = True
            else:
                raise
        finally:
            await conn.execute("ROLLBACK")

        assert blocked, "Expected immutability trigger to block DELETE on inventory_movements"
    finally:
        await conn.close()


@_live
@pytest.mark.asyncio
async def test_trigger_audit_log_immutable():
    """TRUNCATE on operational_audit_log must be rejected by the immutability trigger."""
    conn = await _connect()
    try:
        blocked = False
        try:
            await conn.execute("BEGIN")
            await conn.execute("TRUNCATE operational_audit_log")
        except Exception as e:
            err_msg = str(e).lower()
            if any(kw in err_msg for kw in ("append-only", "immutable", "cannot truncate", "raise", "prohibited")):
                blocked = True
            else:
                raise
        finally:
            await conn.execute("ROLLBACK")

        assert blocked, "Expected immutability trigger to block TRUNCATE on operational_audit_log"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# G. Runtime role isolation & Temporary LOGIN Role Verification
# ---------------------------------------------------------------------------


@_live
@pytest.mark.asyncio
async def test_role_marginguard_app_user_is_nologin(pg):
    """marginguard_app_user must be a NOLOGIN role."""
    row = await pg.fetchrow(
        "SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb "
        "FROM pg_roles WHERE rolname = 'marginguard_app_user'"
    )
    assert row is not None, "Role marginguard_app_user does not exist"
    assert row["rolcanlogin"] is False, "marginguard_app_user must be NOLOGIN"
    assert row["rolsuper"] is False, "marginguard_app_user must NOT be superuser"
    assert row["rolcreaterole"] is False, "marginguard_app_user must NOT have CREATEROLE"
    assert row["rolcreatedb"] is False, "marginguard_app_user must NOT have CREATEDB"


@_live
@pytest.mark.asyncio
async def test_role_schema_migrations_not_accessible_to_app_user(pg):
    """marginguard_app_user must NOT have SELECT on schema_migrations."""
    has_priv = await pg.fetchval(
        "SELECT has_table_privilege('marginguard_app_user', 'schema_migrations', 'SELECT')"
    )
    assert has_priv is False, (
        "marginguard_app_user must NOT have SELECT on schema_migrations, but it does"
    )


@_live
@pytest.mark.asyncio
async def test_role_app_user_can_access_domain_tables(pg):
    """marginguard_app_user must have SELECT on domain tables."""
    for table in ("orders", "products", "inventory_items", "suppliers"):
        has_priv = await pg.fetchval(
            f"SELECT has_table_privilege('marginguard_app_user', '{table}', 'SELECT')"
        )
        assert has_priv is True, (
            f"marginguard_app_user must have SELECT on '{table}', but it does not"
        )


@_live
@pytest.mark.asyncio
async def test_sequence_schema_migrations_not_accessible_to_app_user(pg):
    """marginguard_app_user must NOT have USAGE on the schema_migrations sequence."""
    seq_name = await pg.fetchval(
        "SELECT sequence_name FROM information_schema.sequences "
        "WHERE sequence_schema = 'public' AND sequence_name LIKE 'schema_migrations%'"
    )
    if seq_name is None:
        pytest.skip("schema_migrations sequence not found — may use different naming")

    has_priv = await pg.fetchval(
        f"SELECT has_sequence_privilege('marginguard_app_user', '{seq_name}', 'USAGE')"
    )
    assert has_priv is False, (
        f"marginguard_app_user must NOT have USAGE on '{seq_name}', but it does"
    )


@_live
@pytest.mark.asyncio
async def test_effective_login_role_permissions():
    """
    Phase E: Create a temporary local LOGIN role inheriting marginguard_app_user.
    Connect as this temporary role and verify:
      1. session_user is the temporary LOGIN role.
      2. No superuser / createrole / createdb attributes.
      3. SELECT on domain tables succeeds.
      4. SELECT on schema_migrations is denied by ACL.
      5. USAGE on schema_migrations_id_seq is denied by ACL.
      6. UPDATE / DELETE / TRUNCATE on immutable tables (financial_ledger, inventory_movements,
         operational_audit_log) is denied by ACL (prior to trigger evaluation).
      7. Role is cleanly dropped after test.
    """
    admin_conn = await _connect()
    role_name = "marginguard_test_login"
    password = secrets.token_hex(16)

    try:
        # Create temporary LOGIN role with membership in marginguard_app_user
        await admin_conn.execute(f"DROP ROLE IF EXISTS {role_name};")
        await admin_conn.execute(
            f"CREATE ROLE {role_name} WITH LOGIN PASSWORD '{password}' INHERIT IN ROLE marginguard_app_user;"
        )

        # Connect as the temporary LOGIN role
        login_url = f"postgresql://{role_name}:{password}@127.0.0.1:5433/marginguard_test"
        login_conn = await asyncpg.connect(login_url, ssl=False)
        try:
            # 1. Identity
            user_info = await login_conn.fetchrow("SELECT session_user, current_user;")
            assert user_info["session_user"] == role_name
            assert user_info["current_user"] == role_name

            # 2. No elevated attributes
            attrs = await admin_conn.fetchrow(
                "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = $1",
                role_name,
            )
            assert attrs["rolsuper"] is False
            assert attrs["rolcreaterole"] is False
            assert attrs["rolcreatedb"] is False

            # 3. Permitted access: SELECT on products
            prods = await login_conn.fetch("SELECT id FROM products LIMIT 1;")
            assert len(prods) == 1

            # 4. Denied access: SELECT on schema_migrations
            denied_migrations = False
            try:
                await login_conn.execute("SELECT * FROM schema_migrations;")
            except asyncpg.InsufficientPrivilegeError:
                denied_migrations = True
            assert denied_migrations, "Expected ACL permission denied on schema_migrations"

            # 5. Denied access: Sequence usage
            denied_seq = False
            try:
                await login_conn.execute("SELECT nextval('schema_migrations_id_seq');")
            except asyncpg.InsufficientPrivilegeError:
                denied_seq = True
            assert denied_seq, "Expected ACL permission denied on schema_migrations_id_seq"

            # 6. Immutable table ACL denials (UPDATE, DELETE, TRUNCATE)
            # 6a. financial_ledger UPDATE denied by ACL
            denied_ledger_update = False
            try:
                await login_conn.execute("UPDATE financial_ledger SET amount = 1 WHERE 1=0;")
            except asyncpg.InsufficientPrivilegeError:
                denied_ledger_update = True
            assert denied_ledger_update, "Expected ACL permission denied on UPDATE financial_ledger"

            # 6b. inventory_movements DELETE denied by ACL
            denied_mov_delete = False
            try:
                await login_conn.execute("DELETE FROM inventory_movements WHERE 1=0;")
            except asyncpg.InsufficientPrivilegeError:
                denied_mov_delete = True
            assert denied_mov_delete, "Expected ACL permission denied on DELETE inventory_movements"

            # 6c. operational_audit_log TRUNCATE denied by ACL
            denied_audit_truncate = False
            try:
                await login_conn.execute("TRUNCATE operational_audit_log;")
            except asyncpg.InsufficientPrivilegeError:
                denied_audit_truncate = True
            assert denied_audit_truncate, "Expected ACL permission denied on TRUNCATE operational_audit_log"

        finally:
            await login_conn.close()

    finally:
        # Clean up temporary role
        await admin_conn.execute(f"DROP ROLE IF EXISTS {role_name};")
        await admin_conn.close()


# ---------------------------------------------------------------------------
# H. Valid insert smoke test — domain table DML succeeds and rolls back
# ---------------------------------------------------------------------------


@_live
@pytest.mark.asyncio
async def test_domain_table_insert_and_rollback():
    """
    A well-formed INSERT into orders must succeed within a transaction,
    then be rolled back so seed data is not polluted.
    Columns verified against actual schema: order_number, channel, status, subtotal, tax_amount, shipping_amount, total_amount, payment_method.
    """
    conn = await _connect()
    try:
        merchant_id = "merchant_northstar"
        test_id = f"test-order-{uuid.uuid4().hex[:8]}"
        order_num = f"ORD-{test_id}"

        await conn.execute("BEGIN")
        try:
            await conn.execute(
                """
                INSERT INTO orders (
                    id, merchant_id, order_number, channel, status,
                    subtotal, tax_amount, shipping_amount, total_amount, payment_method
                ) VALUES ($1, $2, $3, 'pos', 'completed', 10.00, 0.80, 0.00, 10.80, 'card_in_store')
                """,
                test_id, merchant_id, order_num,
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM orders WHERE id = $1", test_id
            )
            assert count == 1, "INSERT within transaction must be visible before commit"
        finally:
            await conn.execute("ROLLBACK")

        # After rollback, the row must not persist
        count_after = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE id = $1", test_id
        )
        assert count_after == 0, "Rolled-back INSERT must not persist"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# I. Supabase Data API Privilege Hardening & PUBLIC Execution Tests
# ---------------------------------------------------------------------------


@_live
@pytest.mark.asyncio
async def test_supabase_api_roles_denied_table_access(pg):
    """
    Verify that anon, authenticated, and service_role (if present) are completely
    stripped of table privileges on all MarginGuard domain and system tables.
    """
    api_roles = ["anon", "authenticated", "service_role"]
    domain_tables = [
        "orders", "products", "inventory_items", "financial_ledger",
        "purchase_orders", "merchants", "schema_migrations"
    ]

    for role in api_roles:
        # Check if role exists in this test cluster
        role_exists = await pg.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
        )
        if not role_exists:
            continue

        for table in domain_tables:
            for priv in ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]:
                has_priv = await pg.fetchval(
                    f"SELECT has_table_privilege('{role}', '{table}', '{priv}')"
                )
                assert has_priv is False, (
                    f"Role '{role}' should NOT have '{priv}' on table '{table}', but does"
                )


@_live
@pytest.mark.asyncio
async def test_supabase_api_roles_denied_sequence_and_function_access(pg):
    """
    Verify that anon, authenticated, and service_role (if present) have no USAGE
    on sequences and no direct EXECUTE on MarginGuard functions.
    Also verify PUBLIC has no EXECUTE on trigger routines in information_schema.
    """
    api_roles = ["anon", "authenticated", "service_role"]
    test_functions = [
        "prevent_ledger_tampering",
        "prevent_inventory_movement_tampering",
        "prevent_audit_log_tampering"
    ]

    # Verify PUBLIC has no EXECUTE on trigger routines in information_schema
    public_privs = await pg.fetch(
        """
        SELECT routine_name, grantee, privilege_type
        FROM information_schema.routine_privileges
        WHERE routine_name = ANY($1::text[])
          AND grantee = 'PUBLIC'
        """,
        test_functions,
    )
    assert len(public_privs) == 0, f"PUBLIC should have no routine privileges: {public_privs}"

    # Verify API roles have no function execute and no sequence usage
    for role in api_roles:
        role_exists = await pg.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role
        )
        if not role_exists:
            continue

        role_privs = await pg.fetch(
            """
            SELECT routine_name, grantee, privilege_type
            FROM information_schema.routine_privileges
            WHERE routine_name = ANY($1::text[])
              AND grantee = $2
            """,
            test_functions,
            role,
        )
        assert len(role_privs) == 0, f"Role '{role}' should have no routine privileges: {role_privs}"

        # Sequence usage
        has_seq = await pg.fetchval(
            f"SELECT has_sequence_privilege('{role}', 'financial_ledger_id_seq', 'USAGE')"
        )
        assert has_seq is False, (
            f"Role '{role}' should NOT have USAGE on financial_ledger_id_seq, but does"
        )


@_live
@pytest.mark.asyncio
async def test_future_default_privileges_hardened(pg):
    """
    Verify that future default privileges for role postgres in schema public
    do not auto-grant tables/sequences to anon, authenticated, or service_role,
    and that global future routine defaults do not grant EXECUTE to PUBLIC.
    """
    # Check pg_default_acl for role postgres
    rows = await pg.fetch("""
        SELECT
            pg_get_userbyid(d.defaclrole) as defaclrole,
            n.nspname as schema_name,
            d.defaclobjtype as objtype,
            d.defaclacl::text as acl
        FROM pg_default_acl d
        LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
        WHERE d.defaclrole = 'postgres'::regrole
        ORDER BY schema_name, objtype;
    """)

    for r in rows:
        acl_str = r["acl"] or ""
        schema = r["schema_name"]
        objtype = r["objtype"]

        # In schema public, anon and authenticated should not be granted
        if schema == "public":
            assert "anon=" not in acl_str, f"Future default ACL for anon found in public: {acl_str}"
            assert "authenticated=" not in acl_str, f"Future default ACL for authenticated found in public: {acl_str}"

        # On routines, PUBLIC should not have default EXECUTE
        if objtype == "f":
            assert "=X" not in acl_str and ",=X" not in acl_str, (
                f"Future routine default ACL still grants EXECUTE to PUBLIC: {acl_str}"
            )
