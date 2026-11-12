"""
backend/app/db/migrate.py
=========================
Deterministic, idempotent SQL migration runner for MarginGuard.

HOW IT WORKS:
1. Connects to Supabase using DIRECT_URL (port 5432, session-mode pooler).
   DDL statements and advisory locks require a persistent session connection —
   the transaction-mode pooler (port 6543) cannot be used here.

2. Acquires a PostgreSQL advisory lock so that two migration processes started
   simultaneously (e.g., two Render deploy replicas racing at boot) cannot
   corrupt the database by running the same migration twice.

3. Bootstraps the schema_migrations tracking table if it does not yet exist.
   This is the "chicken-and-egg" step: we need somewhere to record which
   migrations have run, before we can check what has run.

4. Reads every *.sql file from backend/migrations/, sorted lexicographically.
   Lexicographic order means 001_... runs before 002_... runs before 003_...,
   which is the standard convention.

5. For each migration file:
   - Computes its SHA-256 checksum.
   - Checks schema_migrations:
       • Already applied with the same hash → skip (idempotent).
       • Already applied with a DIFFERENT hash → abort (tamper detected).
       • Not yet applied → execute within a database transaction.
   - On success, inserts a row into schema_migrations.
   - On failure, the transaction is rolled back and the script exits non-zero.

6. Releases the advisory lock in a `finally` block — guaranteed even if
   migration fails mid-flight or the process is interrupted.

ADVISORY LOCK SAFETY:
   We use pg_advisory_lock() / pg_advisory_unlock() (session-level) rather than
   pg_advisory_xact_lock() (transaction-level) because we need the lock to
   survive across multiple transactions (bootstrap + individual migrations).
   The lock is held on a constant numeric key derived from the application name.

RUNNING MIGRATIONS:
   From the workspace root (marginguard/):
       backend/.venv/Scripts/python.exe -m app.db.migrate

CREDENTIALS:
   Reads DIRECT_URL from backend/.env (never logs the raw value).
"""

import asyncio
import hashlib
import logging
import sys
import urllib.parse
from pathlib import Path

import asyncpg

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directory where migration SQL files live, relative to the workspace root.
# When running as `python -m app.db.migrate` from the workspace root, the
# current working directory is the workspace root, so this resolves correctly.
MIGRATIONS_DIR = Path("backend") / "migrations"

# Tracking table that records which migrations have been applied.
SCHEMA_MIGRATIONS_TABLE = "schema_migrations"

# Stable advisory lock key — derived from the application name so it doesn't
# accidentally collide with other applications sharing the same Postgres server.
# hashtext() is a Postgres built-in; we replicate the same hash in Python so the
# value is deterministic and documented rather than magic.
#
# NOTE: We pre-compute the integer rather than calling hashtext() in Python
# because hashtext() is Postgres-internal and not stable across versions for
# external use.  Instead, we use a fixed well-known constant and document it.
#
# The lock key 1_234_567_890 is arbitrary but fixed for this project.
ADVISORY_LOCK_KEY: int = 1_234_567_890


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------

BOOTSTRAP_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE} (
    id               SERIAL PRIMARY KEY,
    filename         TEXT        NOT NULL UNIQUE,
    checksum_sha256  TEXT        NOT NULL,
    applied_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

ACQUIRE_LOCK_SQL = f"SELECT pg_advisory_lock({ADVISORY_LOCK_KEY})"
RELEASE_LOCK_SQL = f"SELECT pg_advisory_unlock({ADVISORY_LOCK_KEY})"


# ---------------------------------------------------------------------------
# Core migration logic
# ---------------------------------------------------------------------------

def _sha256(content: str) -> str:
    """Return the lowercase hex SHA-256 digest of a UTF-8 string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _discover_migration_files() -> list[Path]:
    """
    Return all *.sql files in MIGRATIONS_DIR, sorted lexicographically.
    Raises FileNotFoundError if the directory does not exist.
    """
    if not MIGRATIONS_DIR.exists():
        raise FileNotFoundError(
            f"Migrations directory not found: {MIGRATIONS_DIR.resolve()}\n"
            "Create it and add migration SQL files before running the migrator."
        )
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return files


async def _bootstrap(conn: asyncpg.Connection) -> None:  # type: ignore[type-arg]
    """
    Ensure the schema_migrations tracking table exists.
    This runs BEFORE we try to read from it.
    Uses IF NOT EXISTS so it is safe to run on every startup.
    """
    logger.info("Bootstrapping %s table (IF NOT EXISTS)…", SCHEMA_MIGRATIONS_TABLE)
    await conn.execute(BOOTSTRAP_SQL)


async def _already_applied(conn: asyncpg.Connection, filename: str) -> str | None:  # type: ignore[type-arg]
    """
    Return the stored checksum if the migration file has already been applied,
    or None if it has not been seen before.
    """
    row = await conn.fetchrow(
        f"SELECT checksum_sha256 FROM {SCHEMA_MIGRATIONS_TABLE} WHERE filename = $1",
        filename,
    )
    return row["checksum_sha256"] if row else None


async def _record_migration(
    conn: asyncpg.Connection,  # type: ignore[type-arg]
    filename: str,
    checksum: str,
) -> None:
    """
    Insert a record into schema_migrations to mark a migration as applied.
    Called INSIDE the same transaction that executed the migration SQL, so
    if the migration fails the record is also rolled back automatically.
    """
    await conn.execute(
        f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE} (filename, checksum_sha256) "
        "VALUES ($1, $2)",
        filename,
        checksum,
    )


async def run_migrations(conn: asyncpg.Connection) -> None:  # type: ignore[type-arg]
    """
    Main migration execution routine.

    Acquires the advisory lock, bootstraps the tracking table, then processes
    each migration file in lexicographic order.  The lock is released in a
    `finally` block to guarantee it is always returned — even if this coroutine
    raises an exception or the process is killed.

    Args:
        conn: An asyncpg connection obtained from DIRECT_URL (session pooler).
              It must be a raw, long-lived connection — NOT a pooler connection.
    """
    lock_acquired = False
    try:
        # Step 1 — Acquire advisory lock to serialise concurrent migrators.
        logger.info(
            "Acquiring advisory lock (key=%d) to serialise migration runs…",
            ADVISORY_LOCK_KEY,
        )
        await conn.execute(ACQUIRE_LOCK_SQL)
        lock_acquired = True
        logger.info("Advisory lock acquired.")

        # Step 2 — Bootstrap the tracking table so we can read from it.
        await _bootstrap(conn)

        # Step 3 — Discover migration files.
        migration_files = _discover_migration_files()
        if not migration_files:
            logger.info("No migration files found in %s. Nothing to do.", MIGRATIONS_DIR)
            return

        logger.info("Found %d migration file(s) to evaluate.", len(migration_files))

        applied = 0
        skipped = 0

        for migration_path in migration_files:
            filename = migration_path.name
            sql_content = migration_path.read_text(encoding="utf-8")
            checksum = _sha256(sql_content)

            stored_checksum = await _already_applied(conn, filename)

            if stored_checksum is not None:
                if stored_checksum == checksum:
                    logger.info("  SKIP  %s (already applied, checksum matches)", filename)
                    skipped += 1
                    continue
                else:
                    # Tamper detected — the file on disk differs from what was
                    # applied to the database.  This is a critical integrity error.
                    raise RuntimeError(
                        f"TAMPER DETECTED: {filename}\n"
                        f"  Expected checksum : {stored_checksum}\n"
                        f"  On-disk checksum  : {checksum}\n"
                        "The migration file has been modified after it was applied. "
                        "Do NOT re-run this migration. Create a new migration instead."
                    )

            # Migration has not been applied yet — execute it.
            logger.info("  APPLY %s…", filename)

            # Each migration runs inside its own transaction so that:
            # - If the SQL succeeds, both the DDL and the schema_migrations
            #   record are committed atomically.
            # - If the SQL fails, neither is committed and we can report the
            #   exact error without a partially applied migration.
            async with conn.transaction():
                await conn.execute(sql_content)
                await _record_migration(conn, filename, checksum)

            logger.info("  DONE  %s (checksum: %s…)", filename, checksum[:12])
            applied += 1

        logger.info(
            "Migration run complete: %d applied, %d skipped.",
            applied,
            skipped,
        )

    except Exception:
        # Re-raise after logging so the caller / __main__ can handle exit code.
        logger.exception("Migration run FAILED. See error above.")
        raise

    finally:
        # Always release the lock — even if we raised above.
        if lock_acquired:
            try:
                await conn.execute(RELEASE_LOCK_SQL)
                logger.info("Advisory lock released (key=%d).", ADVISORY_LOCK_KEY)
            except Exception as release_error:
                # This is unusual — the connection may have dropped.
                # Log it but do not suppress the original exception.
                logger.error(
                    "Failed to release advisory lock: %s. "
                    "Lock will be automatically released when the connection closes.",
                    release_error,
                )


# ---------------------------------------------------------------------------
# Entry point: python -m app.db.migrate
# ---------------------------------------------------------------------------

async def _main() -> None:
    """
    CLI entry point.  Opens a single dedicated connection using DIRECT_URL,
    runs all pending migrations, then closes the connection.

    Uses a single connection (not a pool) because:
    - Migrations are sequential; a pool offers no benefit.
    - Advisory lock semantics are session-level; using a pool could accidentally
      release the lock when a connection is returned mid-migration.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not settings.direct_url:
        logger.error(
            "DIRECT_URL is not configured. "
            "Cannot run migrations without a direct database connection. "
            "Set DIRECT_URL in backend/.env"
        )
        sys.exit(1)

    logger.info(
        "Migration runner starting → %s",
        settings.safe_direct_url,  # masked — never logs the real password
    )

    # ---------------------------------------------------------------------------
    # SSL configuration — loopback-safe override
    # ---------------------------------------------------------------------------
    # By default we require TLS (ssl="require") for all connections.  This is
    # the correct setting for Supabase and any remote PostgreSQL host.
    #
    # For local offline validation ONLY, the operator may set:
    #   MIGRATE_ALLOW_INSECURE_LOCAL=true
    # The runner then verifies that DIRECT_URL targets a loopback address
    # (127.0.0.1, ::1, or localhost) before disabling TLS.  Any attempt to
    # disable TLS while pointing at a non-loopback host is treated as a
    # misconfiguration and the runner exits immediately.
    direct_url_raw = settings.direct_url.get_secret_value()
    parsed_url = urllib.parse.urlsplit(direct_url_raw)
    host = (parsed_url.hostname or "").lower()
    is_loopback = host in ("127.0.0.1", "::1", "localhost")

    if settings.migrate_allow_insecure_local:
        if not is_loopback:
            logger.error(
                "MIGRATE_ALLOW_INSECURE_LOCAL=true is set but DIRECT_URL resolves to "
                "host '%s' which is NOT a loopback address. "
                "Refusing to disable TLS for a non-local connection. "
                "Unset MIGRATE_ALLOW_INSECURE_LOCAL or point DIRECT_URL at 127.0.0.1 / ::1 / localhost.",
                host,
            )
            sys.exit(1)
        ssl_setting: str | bool = False
        logger.warning(
            "TLS disabled for local loopback migration connection "
            "(host=%s, port=%s). MIGRATE_ALLOW_INSECURE_LOCAL=true.",
            host,
            parsed_url.port,
        )
    else:
        ssl_setting = "require"

    conn: asyncpg.Connection | None = None  # type: ignore[type-arg]
    try:
        conn = await asyncpg.connect(
            dsn=direct_url_raw,
            ssl=ssl_setting,
            # statement_cache_size is NOT set to 0 here — DIRECT_URL uses
            # a persistent session connection where prepared statements work fine.
        )
        await run_migrations(conn)

    except Exception:
        sys.exit(1)

    finally:
        if conn is not None:
            await conn.close()
            logger.debug("Migration connection closed.")


if __name__ == "__main__":
    asyncio.run(_main())
