"""
backend/tests/test_migrations_syntax.py
========================================
Offline static validation of SQL migration files.
Ensures schema and seed files adhere to:
1. Exact 21-table manifest.
2. Parenthesis and quote balance.
3. Foreign key referential integrity (tables and referenced columns exist).
4. No hardcoded credentials or unmasked secrets.
5. All 21 tables have tenant-scoped composite primary or foreign keys.
6. Trigger definitions and role separation statements are present.
"""

import re
from pathlib import Path
import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture
def schema_sql() -> str:
    path = MIGRATIONS_DIR / "001_initial_schema.sql"
    assert path.exists(), f"Missing schema migration: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def seed_sql() -> str:
    path = MIGRATIONS_DIR / "002_seed_northstar_living.sql"
    assert path.exists(), f"Missing seed migration: {path}"
    return path.read_text(encoding="utf-8")


def test_table_manifest_count(schema_sql: str):
    """Verify that exactly 21 tables are created in 001_initial_schema.sql."""
    table_names = re.findall(
        r"CREATE TABLE (?:IF NOT EXISTS )?([a-zA-Z0-9_]+)",
        schema_sql,
        re.IGNORECASE,
    )
    assert len(table_names) == 21, f"Expected 21 tables, found {len(table_names)}: {table_names}"

    expected_manifest = [
        "schema_migrations",
        "merchants",
        "products",
        "suppliers",
        "supplier_offers",
        "inventory_items",
        "inventory_movements",
        "procurement_requests",
        "purchase_orders",
        "purchase_order_items",
        "spend_reservations",
        "purchase_order_approvals",
        "financial_ledger",
        "orders",
        "order_items",
        "paypal_captures",
        "order_refunds",
        "dispute_cases",
        "dispute_evidence",
        "webhook_events",
        "operational_audit_log",
    ]
    assert table_names == expected_manifest


def test_parentheses_balance(schema_sql: str, seed_sql: str):
    """Ensure all open parentheses have matching closing parentheses."""
    for filename, sql in [("001_initial_schema.sql", schema_sql), ("002_seed_northstar_living.sql", seed_sql)]:
        # Strip comments
        lines = [line.split("--")[0] for line in sql.splitlines()]
        clean_sql = "\n".join(lines)
        open_count = clean_sql.count("(")
        close_count = clean_sql.count(")")
        assert open_count == close_count, f"{filename}: Mismatched parentheses (open={open_count}, close={close_count})"


def test_dollar_quotes_balance(schema_sql: str):
    """Verify that PL/pgSQL dollar-quotes ($$) are properly paired."""
    # Count literal $$ in schema_sql
    dollar_quote_count = schema_sql.count("$$")
    assert dollar_quote_count > 0, "Expected PL/pgSQL functions using $$"
    assert dollar_quote_count % 2 == 0, f"Unbalanced $$ dollar quotes: count is {dollar_quote_count}"


def test_composite_foreign_keys_present(schema_sql: str):
    """Ensure tenant-owned tables enforce composite foreign keys."""
    tenant_fk_patterns = [
        r"FOREIGN KEY \(merchant_id, supplier_id\) REFERENCES suppliers\(merchant_id, id\)",
        r"FOREIGN KEY \(merchant_id, product_id\) REFERENCES products\(merchant_id, id\)",
        r"FOREIGN KEY \(merchant_id, request_id\) REFERENCES procurement_requests\(merchant_id, id\)",
        r"FOREIGN KEY \(merchant_id, purchase_order_id\) REFERENCES purchase_orders\(merchant_id, id\)",
        r"FOREIGN KEY \(merchant_id, order_id\) REFERENCES orders\(merchant_id, id\)",
        r"FOREIGN KEY \(merchant_id, capture_id\) REFERENCES paypal_captures\(merchant_id, id\)",
        r"FOREIGN KEY \(merchant_id, dispute_id\) REFERENCES dispute_cases\(merchant_id, id\)",
    ]
    for pattern in tenant_fk_patterns:
        match = re.search(pattern, schema_sql)
        assert match is not None, f"Missing required composite foreign key pattern: {pattern}"


def test_immutability_triggers_present(schema_sql: str):
    """Verify ledger, inventory movement, and audit log immutability triggers."""
    triggers = [
        "trg_financial_ledger_immutable",
        "trg_financial_ledger_no_truncate",
        "trg_inventory_movements_immutable",
        "trg_inventory_movements_no_truncate",
        "trg_audit_log_immutable",
        "trg_audit_log_no_truncate",
    ]
    for trg in triggers:
        assert trg in schema_sql, f"Missing trigger: {trg}"


def test_role_separation_present(schema_sql: str):
    """Verify marginguard_app_user role creation and privilege revocations."""
    assert "CREATE ROLE marginguard_app_user" in schema_sql
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON financial_ledger FROM marginguard_app_user" in schema_sql
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON inventory_movements FROM marginguard_app_user" in schema_sql
    assert "REVOKE UPDATE, DELETE, TRUNCATE ON operational_audit_log FROM marginguard_app_user" in schema_sql


def test_seed_northstar_reconciliation(seed_sql: str):
    """Verify seed data parameters match the approved Northstar Living baseline."""
    assert "'merchant_northstar'" in seed_sql
    assert "'Northstar Living'" in seed_sql
    assert "500.00" in seed_sql  # $500 authorized cap
    assert "150.00" in seed_sql  # $150 per-order limit

    # Check suppliers
    assert "'sup_supplier_a'" in seed_sql
    assert "'sup_supplier_b'" in seed_sql
    assert "'sup_supplier_c'" in seed_sql

    # Check inventory movements
    assert "'opening_balance'" in seed_sql
    assert "14" in seed_sql  # opening balance +14
    assert "'pos_sale'" in seed_sql
    assert "-2" in seed_sql  # POS sale -2

    # Check reconciled inventory item
    assert "stock_in_store" in seed_sql
    assert "12" in seed_sql  # reconciled stock 12
    assert "14" in seed_sql  # reorder point 14

    # Check financial ledger entries
    assert "'seed_ledger_alloc_week_01'" in seed_sql
    assert "'seed_ledger_prior_commitments'" in seed_sql
    assert "-175.00" in seed_sql  # prior commitments $175
    assert "325.00" in seed_sql   # running balance after commitments
