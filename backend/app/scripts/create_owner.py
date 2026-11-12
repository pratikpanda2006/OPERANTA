"""
backend/app/scripts/create_owner.py
===================================
Bootstrap an OPERANTA owner account against the LIVE database.

Usage (from the backend/ directory):

    python -m app.scripts.create_owner \
        --email owner@operanta.io --password <strong-password> \
        [--merchant-id merchant_northstar] [--display-name "Owner"]

This is the supported, intentional way to create the first operational account.
It hashes the password with salted PBKDF2-HMAC-SHA256 and NEVER stores or logs
the plaintext password. It is idempotent: running it again updates the account.

Do not use this script for routine user management; it exists to seed the owner
so the login screen has a real, verifiable account.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as a module from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.connection import init_db_pool, close_db_pool
from app.services.auth import hash_password


async def _main(args: argparse.Namespace) -> int:
    pool = await init_db_pool()
    if pool is None:
        print("ERROR: could not initialise database pool.", file=sys.stderr)
        return 1
    try:
        password_hash = hash_password(args.password)
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO operanta_users
                        (merchant_id, email, password_hash, display_name, role)
                    VALUES ($1, $2, $3, $4, 'owner')
                    ON CONFLICT (lower(email)) DO UPDATE
                        SET password_hash = EXCLUDED.password_hash,
                            display_name = EXCLUDED.display_name,
                            updated_at = now()
                    RETURNING id, email, merchant_id, role
                    """,
                    args.merchant_id,
                    args.email.strip().lower(),
                    password_hash,
                    args.display_name,
                )
        print(
            "OK: owner account ready for %s (merchant=%s, role=%s, id=%s)"
            % (row["email"], row["merchant_id"], row["role"], row["id"])
        )
        return 0
    finally:
        await close_db_pool(pool)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap an OPERANTA owner account")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True, help="Strong password (never stored in plaintext)")
    parser.add_argument("--merchant-id", default="merchant_northstar")
    parser.add_argument("--display-name", default=None)
    args = parser.parse_args()

    if len(args.password) < 8:
        print("ERROR: password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
