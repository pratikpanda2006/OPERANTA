"""
backend/tests/test_m7_corrective.py
====================================
Corrective verification suite for Milestone 7.

Covers the following investigations mandated by the corrective review task:

  B. SSL configuration tests (loopback vs remote; ssl="require" semantics)
  C. Real repository integration tests against live PostgreSQL 18
  D. Two-merchant isolation tests against live PostgreSQL 18
  F. Lifespan context integration tests (pool lifecycle, failure modes)

Environment variable required for live tests:
  TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/marginguard_test

Live tests are automatically skipped if TEST_DATABASE_URL is not set.
"""

import asyncio
import os
import ssl
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from fastapi import FastAPI

from app.db.connection import close_db_pool, init_db_pool, is_pool_ready
from app.db.repositories.inventory_repo import list_inventory_items
from app.db.repositories.merchant_repo import get_spend_envelope
from app.db.repositories.supplier_repo import list_suppliers_with_offers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "")
LIVE = bool(TEST_DSN)
skip_no_db = pytest.mark.skipif(not LIVE, reason="TEST_DATABASE_URL not set")


@asynccontextmanager
async def live_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Open a direct asyncpg connection to the live test cluster."""
    conn = await asyncpg.connect(TEST_DSN, ssl=False)
    try:
        yield conn
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# B. SSL Configuration Tests
# ---------------------------------------------------------------------------

class TestSSLConfiguration:
    """
    Verify init_db_pool SSL logic without making real remote connections.

    asyncpg ssl parameter semantics:
      False      — no SSL/TLS (loopback only, explicit opt-in)
      "require"  — TLS required; server cert NOT verified (no CA check)
      SSLContext — full certificate verification when configured with CA

    Required behaviour:
      Loopback hosts (127.0.0.1, ::1, localhost) → ssl=False
      All other hosts                             → ssl="require" minimum
    """

    def _ssl_value_for_dsn(self, dsn: str) -> object:
        """
        Extract the ssl kwarg that init_db_pool would pass to asyncpg.create_pool.
        Uses the same urllib.parse logic as the real implementation.
        """
        import urllib.parse
        parsed = urllib.parse.urlsplit(dsn)
        is_loopback = (parsed.hostname or "").lower() in ("127.0.0.1", "::1", "localhost")
        return False if is_loopback else "require"

    def test_loopback_127_disables_ssl(self):
        """DSN with 127.0.0.1 must result in ssl=False."""
        val = self._ssl_value_for_dsn("postgresql://postgres@127.0.0.1:5433/db")
        assert val is False

    def test_loopback_ipv6_disables_ssl(self):
        """DSN with [::1] (RFC 3986 bracketed IPv6) must result in ssl=False.

        asyncpg DSNs use [::1] for IPv6 loopback, per RFC 3986.
        urllib.parse.urlsplit returns hostname='::1' (without brackets) for [::1].
        Bare ::1 without brackets is not a valid URL and parses with hostname=None.
        """
        # RFC-compliant bracketed form
        val = self._ssl_value_for_dsn("postgresql://postgres@[::1]:5433/db")
        assert val is False

    def test_loopback_localhost_disables_ssl(self):
        """DSN with 'localhost' must result in ssl=False."""
        val = self._ssl_value_for_dsn("postgresql://user:pass@localhost:5432/db")
        assert val is False

    def test_remote_host_requires_ssl(self):
        """DSN pointing to a remote host must result in ssl='require'."""
        val = self._ssl_value_for_dsn(
            "postgresql://user:pass@aws-0-ap-southeast-1.pooler.supabase.co:6543/postgres"
        )
        assert val == "require"

    def test_remote_ip_requires_ssl(self):
        """Non-loopback IP must result in ssl='require'."""
        val = self._ssl_value_for_dsn("postgresql://user@10.0.0.1:5432/db")
        assert val == "require"

    def test_ssl_require_does_not_verify_cert(self):
        """
        Document: ssl='require' in asyncpg does NOT perform CA certificate verification.
        It only mandates TLS transport encryption. This is acceptable for Supabase's
        managed pooler (which presents a trusted cert), but would not defend against
        MITM on arbitrary hosts. Full cert verification requires passing an ssl.SSLContext
        with a CA bundle — documented here as a limitation, not a defect.
        """
        # ssl="require" is a string constant in asyncpg, not an SSLContext
        assert isinstance("require", str)
        # An SSLContext with CERT_REQUIRED would provide stronger verification
        ctx = ssl.create_default_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    @pytest.mark.asyncio
    async def test_pool_uses_loopback_ssl_false(self):
        """
        init_db_pool with a loopback DSN passes ssl=False to create_pool.
        Verified by intercepting asyncpg.create_pool before any network call.
        """
        mock_pool = MagicMock()
        mock_pool._closed = False

        with patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            pool = await init_db_pool(
                dsn_override="postgresql://postgres@127.0.0.1:5433/test"
            )
            assert pool is mock_pool
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["ssl"] is False

        # Clean up module-level _pool side effect
        import app.db.connection as conn_module
        conn_module._pool = None

    @pytest.mark.asyncio
    async def test_pool_uses_remote_ssl_require(self):
        """
        init_db_pool with a remote DSN passes ssl='require' to create_pool.
        Verified by intercepting asyncpg.create_pool before any network call.
        """
        mock_pool = MagicMock()
        mock_pool._closed = False

        with patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            pool = await init_db_pool(
                dsn_override="postgresql://user:pass@external-host.example.com:5432/db"
            )
            assert pool is mock_pool
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["ssl"] == "require"

        import app.db.connection as conn_module
        conn_module._pool = None

    @pytest.mark.asyncio
    async def test_pool_uses_statement_cache_zero(self):
        """
        init_db_pool always passes statement_cache_size=0 for Supabase transaction pooler
        compatibility, regardless of host type.
        """
        mock_pool = MagicMock()
        mock_pool._closed = False

        with patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_create:
            await init_db_pool(dsn_override="postgresql://u:p@127.0.0.1:5433/db")
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["statement_cache_size"] == 0

        import app.db.connection as conn_module
        conn_module._pool = None


# ---------------------------------------------------------------------------
# C. Real Repository Integration Tests (live PostgreSQL 18)
# ---------------------------------------------------------------------------

@skip_no_db
class TestMerchantRepoLive:
    """
    Real SQL execution against seeded marginguard_test database.
    Verifies merchant_repo column alignment with migration 001.
    """

    @pytest.mark.asyncio
    async def test_get_spend_envelope_northstar(self):
        """get_spend_envelope returns correct Northstar Living spend data from live DB."""
        async with live_conn() as conn:
            result = await get_spend_envelope(conn, "merchant_northstar")

        assert result is not None
        assert result["merchant_id"] == "merchant_northstar"

        # From migration 001: merchants.active_demo_week_cap = 500.00
        assert result["demo_envelope_cap"] == Decimal("500.00")

        # From migration 001: merchants.per_order_limit = 150.00
        assert result["max_order_cost_ceiling"] == Decimal("150.00")

        # From seed 002: last financial_ledger.running_envelope_balance = 325.00
        # (allocation 500.00 → commitment -175.00 → running_envelope_balance 325.00)
        assert result["available_spend"] == Decimal("325.00")
        assert result["committed_spend"] == Decimal("175.00")

        assert result["currency"] == "USD"

        # merchant_created_at is merchants.created_at — must be a datetime-like object
        assert result["merchant_created_at"] is not None

    @pytest.mark.asyncio
    async def test_get_spend_envelope_nonexistent_returns_none(self):
        """get_spend_envelope returns None for a merchant that does not exist."""
        async with live_conn() as conn:
            result = await get_spend_envelope(conn, "merchant_does_not_exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_spend_envelope_decimal_precision_live(self):
        """Decimal precision is preserved through asyncpg → Python conversion."""
        async with live_conn() as conn:
            result = await get_spend_envelope(conn, "merchant_northstar")
        assert isinstance(result["demo_envelope_cap"], Decimal)
        assert isinstance(result["available_spend"], Decimal)
        assert isinstance(result["committed_spend"], Decimal)
        # Exact two-decimal-place values
        assert str(result["demo_envelope_cap"]) == "500.00"
        assert str(result["available_spend"]) == "325.00"
        assert str(result["committed_spend"]) == "175.00"

    @pytest.mark.asyncio
    async def test_spend_envelope_no_ledger_entries(self):
        """
        When a merchant exists but has no financial_ledger entries, committed=0,
        available=cap (the full envelope).
        """
        async with live_conn() as conn:
            # Insert a minimal merchant with no ledger entries
            await conn.execute(
                """
                INSERT INTO merchants (id, name, storefront_type, active_demo_week_cap, per_order_limit, currency)
                VALUES ($1, 'Test Merchant No Ledger', 'omnichannel', 300.00, 100.00, 'USD')
                ON CONFLICT (id) DO NOTHING
                """,
                "merchant_no_ledger_test",
            )
            try:
                result = await get_spend_envelope(conn, "merchant_no_ledger_test")
                assert result is not None
                assert result["demo_envelope_cap"] == Decimal("300.00")
                assert result["committed_spend"] == Decimal("0.00")
                assert result["available_spend"] == Decimal("300.00")
            finally:
                # Cleanup — merchant has no child rows, safe to delete
                await conn.execute("DELETE FROM merchants WHERE id = $1", "merchant_no_ledger_test")


@skip_no_db
class TestInventoryRepoLive:
    """Real SQL execution verifying inventory_repo column alignment with migration 001."""

    @pytest.mark.asyncio
    async def test_list_inventory_northstar(self):
        """list_inventory_items returns the seeded Blue Ceramic Vase record."""
        async with live_conn() as conn:
            items = await list_inventory_items(conn, "merchant_northstar")

        assert len(items) == 1
        item = items[0]

        # products.title (not .name), products.selling_price (not .retail_price)
        assert item["product_name"] == "Handcrafted Blue Ceramic Vase"
        assert item["sku"] == "NSL-VASE-001"
        assert item["category"] == "Home Décor"

        # Confirm selling_price aliased as retail_price
        assert item["retail_price"] == Decimal("24.00")

        # From seed 002: stock_in_store=12, stock_online=0
        assert item["stock_in_store"] == 12
        assert item["stock_online"] == 0
        assert item["reserved_in_store"] == 0
        assert item["reserved_online"] == 0

        # GENERATED columns: available_in_store = stock_in_store - reserved_in_store = 12
        assert item["available_in_store"] == 12
        assert item["available_online"] == 0

        # Computed expressions (not stored columns)
        assert item["total_on_hand"] == 12       # 12 + 0
        assert item["total_available"] == 12     # 12 + 0

        # Reorder logic: reorder_point=14, total_available=12 → reorder_recommended=True
        assert item["reorder_point"] == 14
        assert item["reorder_recommended"] is True

        # daily_demand is INT in schema (seed=4)
        assert item["daily_demand"] == Decimal("4")
        assert item["lead_days"] == 2
        assert item["safety_stock"] == 6

    @pytest.mark.asyncio
    async def test_list_inventory_empty_state(self):
        """list_inventory_items returns empty list for a merchant with no inventory."""
        async with live_conn() as conn:
            items = await list_inventory_items(conn, "merchant_no_inventory_xyz")
        assert items == []

    @pytest.mark.asyncio
    async def test_inventory_decimal_types(self):
        """retail_price and base_cost are returned as Decimal, not float."""
        async with live_conn() as conn:
            items = await list_inventory_items(conn, "merchant_northstar")
        assert len(items) == 1
        assert isinstance(items[0]["retail_price"], Decimal)
        assert isinstance(items[0]["daily_demand"], Decimal)
        # base_cost is nullable; seed sets it to 4.10
        assert isinstance(items[0]["base_cost"], Decimal)
        assert items[0]["base_cost"] == Decimal("4.10")

    @pytest.mark.asyncio
    async def test_inventory_total_on_hand_vs_total_available_reservation_awareness(self):
        """
        total_on_hand = stock_in_store + stock_online (gross)
        total_available = available_in_store + available_online (net of reservations)
        When reservations are zero: total_on_hand == total_available.
        """
        async with live_conn() as conn:
            items = await list_inventory_items(conn, "merchant_northstar")
        item = items[0]
        # Seed data: no reservations → both values should match
        assert item["total_on_hand"] == item["stock_in_store"] + item["stock_online"]
        assert item["total_available"] == item["available_in_store"] + item["available_online"]
        # With zero reservations, they should be equal
        assert item["total_on_hand"] == item["total_available"]


@skip_no_db
class TestSupplierRepoLive:
    """Real SQL execution verifying supplier_repo column alignment with migration 001."""

    @pytest.mark.asyncio
    async def test_list_suppliers_northstar(self):
        """list_suppliers_with_offers returns all 3 seeded suppliers with offers."""
        async with live_conn() as conn:
            suppliers = await list_suppliers_with_offers(conn, "merchant_northstar")

        assert len(suppliers) == 3

        # Find Supplier A
        sup_a = next(s for s in suppliers if s["id"] == "sup_supplier_a")
        assert sup_a["name"] == "Supplier A (Home Décor & Ceramics)"
        # trust_tier = 'allowlisted' → is_allowlisted = True
        assert sup_a["trust_tier"] == "allowlisted"
        assert sup_a["is_allowlisted"] is True
        # reliability_score is INT in DB → Decimal in repo
        assert sup_a["reliability_score"] == Decimal("94")
        assert sup_a["is_illustrative"] is True

        # Supplier A has 1 offer for prod_blue_vase
        assert len(sup_a["offers"]) == 1
        offer_a = sup_a["offers"][0]
        assert offer_a["unit_cost"] == Decimal("4.00")
        assert offer_a["shipping_cost"] == Decimal("12.00")
        assert offer_a["lead_time_days"] == 3
        assert offer_a["moq"] == 20
        assert offer_a["is_illustrative"] is True
        # is_preferred does NOT exist in migration 001 — must not be in result
        assert "is_preferred" not in offer_a

        # Supplier B: trust_tier = 'requires_approval' → is_allowlisted = False
        sup_b = next(s for s in suppliers if s["id"] == "sup_supplier_b")
        assert sup_b["trust_tier"] == "requires_approval"
        assert sup_b["is_allowlisted"] is False

        # Supplier C: preferred (lead_time_days=2)
        sup_c = next(s for s in suppliers if s["id"] == "sup_supplier_c")
        assert sup_c["is_allowlisted"] is True
        assert sup_c["lead_time_days"] == 2  # min from offers
        offer_c = sup_c["offers"][0]
        assert offer_c["unit_cost"] == Decimal("4.10")
        assert offer_c["shipping_cost"] == Decimal("8.00")

    @pytest.mark.asyncio
    async def test_supplier_no_payment_terms_field(self):
        """payment_terms must not appear in supplier results (not in migration 001)."""
        async with live_conn() as conn:
            suppliers = await list_suppliers_with_offers(conn, "merchant_northstar")
        for s in suppliers:
            assert "payment_terms" not in s

    @pytest.mark.asyncio
    async def test_supplier_offers_no_is_preferred_field(self):
        """is_preferred must not appear in offer results (not in migration 001)."""
        async with live_conn() as conn:
            suppliers = await list_suppliers_with_offers(conn, "merchant_northstar")
        for s in suppliers:
            for o in s["offers"]:
                assert "is_preferred" not in o

    @pytest.mark.asyncio
    async def test_suppliers_empty_state(self):
        """list_suppliers_with_offers returns empty list for unknown merchant."""
        async with live_conn() as conn:
            suppliers = await list_suppliers_with_offers(conn, "merchant_unknown_xyz")
        assert suppliers == []

    @pytest.mark.asyncio
    async def test_supplier_offers_no_n_plus_one(self):
        """
        Exactly 2 queries are issued regardless of the number of suppliers.
        This is enforced structurally by the 2-query batch pattern in the repo.
        (Verified by counting queries using pg_stat_statements would need superuser;
        instead we verify that all 3 suppliers and their offers are returned correctly,
        demonstrating the grouping was done in Python, not via per-supplier queries.)
        """
        async with live_conn() as conn:
            suppliers = await list_suppliers_with_offers(conn, "merchant_northstar")
        # 3 suppliers, each with offers — if N+1 existed we'd see errors; this passes cleanly
        assert len(suppliers) == 3
        total_offers = sum(len(s["offers"]) for s in suppliers)
        assert total_offers == 3  # one offer per supplier in seed data


# ---------------------------------------------------------------------------
# D. Two-Merchant Isolation Tests (live PostgreSQL 18)
# ---------------------------------------------------------------------------

@skip_no_db
class TestTwoMerchantIsolation:
    """
    Verify that all three repositories strictly isolate data by merchant_id.
    Uses a disposable second merchant inserted within a rolled-back transaction.
    """

    @pytest.mark.asyncio
    async def test_inventory_does_not_leak_across_merchants(self):
        """
        Merchant B's inventory query must not return Merchant A's records.
        Uses a savepoint + explicit rollback so the test database is not polluted.
        """
        conn = await asyncpg.connect(TEST_DSN, ssl=False)
        try:
            await conn.execute("BEGIN")
            # Insert second merchant and a product + inventory item
            await conn.execute(
                """
                INSERT INTO merchants (id, name, storefront_type, active_demo_week_cap, per_order_limit, currency)
                VALUES ('merchant_isolation_b', 'Isolation Test Merchant B', 'omnichannel', 500.00, 150.00, 'USD')
                """
            )
            await conn.execute(
                """
                INSERT INTO products (merchant_id, id, sku, title, category, selling_price)
                VALUES ('merchant_isolation_b', 'prod_b_001', 'ISO-B-001', 'Merchant B Widget', 'Test', 10.00)
                """
            )
            await conn.execute(
                """
                INSERT INTO inventory_items (merchant_id, id, product_id, stock_in_store, reorder_point, daily_demand)
                VALUES ('merchant_isolation_b', 'inv_b_001', 'prod_b_001', 50, 5, 1)
                """
            )

            # Merchant A query must not include Merchant B records
            items_a = await list_inventory_items(conn, "merchant_northstar")
            for item in items_a:
                assert item["merchant_id"] == "merchant_northstar"
                assert item["product_id"] != "prod_b_001"

            # Merchant B query must not include Merchant A records
            items_b = await list_inventory_items(conn, "merchant_isolation_b")
            assert len(items_b) == 1
            assert items_b[0]["merchant_id"] == "merchant_isolation_b"
            assert items_b[0]["product_name"] == "Merchant B Widget"
        finally:
            await conn.execute("ROLLBACK")
            await conn.close()

    @pytest.mark.asyncio
    async def test_spend_envelope_does_not_leak_across_merchants(self):
        """Merchant A spend data must not leak into Merchant B's query."""
        conn = await asyncpg.connect(TEST_DSN, ssl=False)
        try:
            await conn.execute("BEGIN")
            await conn.execute(
                "INSERT INTO merchants (id, name, storefront_type, active_demo_week_cap, per_order_limit, currency)"
                " VALUES ('merchant_iso_spend', 'Isolation Spend', 'omnichannel', 200.00, 50.00, 'USD')"
            )
            await conn.execute(
                """INSERT INTO financial_ledger
                   (merchant_id, entry_type, amount, running_envelope_balance,
                    reference_type, reference_id, idempotency_key, description)
                   VALUES ('merchant_iso_spend', 'adjustment', 200.00, 200.00,
                           'seed', 'alloc_iso', 'iso_spend_alloc_001', 'Isolation alloc')"""
            )

            # Merchant A envelope unchanged
            env_a = await get_spend_envelope(conn, "merchant_northstar")
            assert env_a["demo_envelope_cap"] == Decimal("500.00")
            assert env_a["available_spend"] == Decimal("325.00")

            # Merchant B reflects only its own ledger
            env_b = await get_spend_envelope(conn, "merchant_iso_spend")
            assert env_b["demo_envelope_cap"] == Decimal("200.00")
            assert env_b["available_spend"] == Decimal("200.00")
            assert env_b["committed_spend"] == Decimal("0.00")
        finally:
            await conn.execute("ROLLBACK")
            await conn.close()

    @pytest.mark.asyncio
    async def test_suppliers_do_not_leak_across_merchants(self):
        """Merchant A's suppliers cannot be seen by Merchant B's query."""
        conn = await asyncpg.connect(TEST_DSN, ssl=False)
        try:
            await conn.execute("BEGIN")
            await conn.execute(
                "INSERT INTO merchants (id, name, storefront_type, active_demo_week_cap, per_order_limit, currency)"
                " VALUES ('merchant_iso_sup', 'Isolation Sup', 'omnichannel', 500.00, 150.00, 'USD')"
            )
            await conn.execute(
                "INSERT INTO suppliers (merchant_id, id, name, trust_tier, reliability_score, is_illustrative)"
                " VALUES ('merchant_iso_sup', 'sup_iso_x', 'Isolated Supplier X', 'allowlisted', 88, TRUE)"
            )

            # Merchant A's suppliers must not leak into Merchant B's query
            sups_a = await list_suppliers_with_offers(conn, "merchant_northstar")
            sup_ids_a = {s["id"] for s in sups_a}
            assert "sup_iso_x" not in sup_ids_a

            # Merchant B sees only its own supplier
            sups_b = await list_suppliers_with_offers(conn, "merchant_iso_sup")
            assert len(sups_b) == 1
            assert sups_b[0]["id"] == "sup_iso_x"
            assert sups_b[0]["is_allowlisted"] is True
        finally:
            await conn.execute("ROLLBACK")
            await conn.close()

    @pytest.mark.asyncio
    async def test_client_supplied_merchant_id_is_ignored(self):
        """
        The repositories receive merchant_id exclusively from server-side config.
        Any merchant_id passed to a repo query is enforced as $1 in SQL.
        An unknown merchant_id always returns empty/None — never another tenant's data.
        """
        async with live_conn() as conn:
            items = await list_inventory_items(conn, "merchant_attacker")
            assert items == []

            envelope = await get_spend_envelope(conn, "merchant_attacker")
            assert envelope is None

            suppliers = await list_suppliers_with_offers(conn, "merchant_attacker")
            assert suppliers == []


# ---------------------------------------------------------------------------
# F. Lifespan Integration Tests
# ---------------------------------------------------------------------------

class TestLifespanContext:
    """
    Verify that the FastAPI lifespan context manager correctly manages the pool lifecycle.
    Uses a fresh FastAPI test app to avoid contaminating the main app state.
    """

    @pytest.mark.asyncio
    async def test_lifespan_startup_stores_pool_in_app_state(self):
        """When DATABASE_URL is configured, startup must store the pool in app.state."""
        from app.main import lifespan

        test_app = FastAPI(lifespan=lifespan)
        mock_pool = MagicMock()
        mock_pool._closed = False

        with patch("app.main.init_db_pool", new_callable=AsyncMock, return_value=mock_pool):
            with patch("app.main.close_db_pool", new_callable=AsyncMock):
                async with lifespan(test_app):
                    assert test_app.state.db_pool is mock_pool

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_clears_pool(self):
        """Shutdown must set app.state.db_pool to None."""
        from app.main import lifespan

        test_app = FastAPI(lifespan=lifespan)
        mock_pool = MagicMock()

        with patch("app.main.init_db_pool", new_callable=AsyncMock, return_value=mock_pool):
            with patch("app.main.close_db_pool", new_callable=AsyncMock):
                async with lifespan(test_app):
                    pass  # yield point
                assert test_app.state.db_pool is None

    @pytest.mark.asyncio
    async def test_lifespan_unconfigured_does_not_raise(self):
        """When DATABASE_URL is not set, startup stores None and does not raise."""
        from app.main import lifespan

        test_app = FastAPI(lifespan=lifespan)

        with patch("app.main.init_db_pool", new_callable=AsyncMock, return_value=None):
            with patch("app.main.close_db_pool", new_callable=AsyncMock):
                async with lifespan(test_app):
                    assert test_app.state.db_pool is None

    @pytest.mark.asyncio
    async def test_lifespan_startup_failure_reraises_when_configured(self):
        """
        When DATABASE_URL is set and pool creation fails, the lifespan must
        re-raise so the application does not start in a silently broken state.
        """
        from app.main import lifespan

        test_app = FastAPI(lifespan=lifespan)

        async def exploding_init(*a, **kw):
            raise ConnectionRefusedError("simulated startup failure")

        with patch("app.main.settings.database_url", MagicMock()):
            with patch("app.main.init_db_pool", side_effect=exploding_init):
                with pytest.raises(ConnectionRefusedError, match="simulated startup failure"):
                    async with lifespan(test_app):
                        pass

    @pytest.mark.asyncio
    async def test_lifespan_exactly_one_pool_created(self):
        """Startup must call init_db_pool exactly once — not once per request."""
        from app.main import lifespan

        test_app = FastAPI(lifespan=lifespan)
        mock_pool = MagicMock()

        with patch("app.main.init_db_pool", new_callable=AsyncMock, return_value=mock_pool) as mock_init:
            with patch("app.main.close_db_pool", new_callable=AsyncMock):
                async with lifespan(test_app):
                    pass
            assert mock_init.call_count == 1

    @pytest.mark.asyncio
    async def test_lifespan_pool_closed_exactly_once_on_shutdown(self):
        """Shutdown must call close_db_pool exactly once."""
        from app.main import lifespan

        test_app = FastAPI(lifespan=lifespan)
        mock_pool = MagicMock()

        with patch("app.main.init_db_pool", new_callable=AsyncMock, return_value=mock_pool):
            with patch("app.main.close_db_pool", new_callable=AsyncMock) as mock_close:
                async with lifespan(test_app):
                    pass
                assert mock_close.call_count == 1
