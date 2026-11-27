"""Create or update a named (non-demo) user account."""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import User
from backend.domain.enums import UserRole
from backend.security.passwords import DEFAULT_N, hash_password_async, verify_password_async

USER_ID = re.compile(r"^[a-z0-9_.-]{3,64}$")


async def upsert_user(
    session: AsyncSession,
    *,
    user_id: str,
    name: str,
    password: str,
    role: UserRole = UserRole.REVIEWER,
    department: str = "operations",
    cost: int = DEFAULT_N,
) -> User:
    """Create the account, or update its name/role/password. Commits."""
    if not USER_ID.match(user_id):
        raise ValueError("username must be 3-64 chars: lowercase letters, digits, _ . -")
    if len(password) < 6:
        raise ValueError("password must be at least 6 characters")
    user = await session.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            name=name,
            email=f"{user_id}@workflowops.example",
            role=role,
            department=department,
            active=True,
        )
        session.add(user)
    else:
        user.name, user.role, user.active = name, role, True
    if not await verify_password_async(password, user.password_hash):  # avoid needless rehash
        user.password_hash = await hash_password_async(password, n=cost)
    await session.commit()
    return user
