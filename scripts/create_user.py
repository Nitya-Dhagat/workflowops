"""Create or update a user account in the database configured by WFO_DATABASE_URL.

The password is read from the WFO_NEW_USER_PASSWORD environment variable, or prompted for, so it
never appears in the repository.

Usage:
  uv run python scripts/create_user.py --id nityadhagat --name "Nitya Dhagat" --role reviewer
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os

from backend.core.config import Settings
from backend.database.accounts import upsert_user
from backend.database.session import make_engine, make_sessionmaker
from backend.domain.enums import UserRole


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", choices=[r.value for r in UserRole], default="reviewer")
    args = ap.parse_args()
    password = os.environ.get("WFO_NEW_USER_PASSWORD") or getpass.getpass("Password: ")
    settings = Settings()
    engine = make_engine(settings.database_url)
    async with make_sessionmaker(engine)() as s:
        user = await upsert_user(
            s,
            user_id=args.id,
            name=args.name,
            password=password,
            role=UserRole(args.role),
            cost=settings.password_hash_cost,
        )
        print(f"account ready: {user.id} ({user.role})")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
