"""Idempotently load reference data (users, workflow definition, integrations) into the DB."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Integration, User, Workflow
from backend.security.passwords import DEFAULT_N, hash_password_async

STAGES = [
    "intake",
    "investigation",
    "planning",
    "validation",
    "approval",
    "execution",
    "verification",
    "reporting",
]


async def seed_reference_data(
    session: AsyncSession,
    data_dir: Path,
    mock_url: str,
    demo_password: str = "",
    password_cost: int = DEFAULT_N,
) -> None:
    world = json.loads((data_dir / "seed" / "world.json").read_text(encoding="utf-8"))
    for u in world["users"]:
        row = await session.get(User, u["id"])
        if row is None:
            session.add(
                User(
                    id=u["id"],
                    name=u["name"],
                    email=u["email"],
                    role=u["role"],
                    department=u["department"],
                    active=u["active"],
                )
            )
        else:
            row.name, row.role, row.active = u["name"], u["role"], u["active"]
    await session.flush()
    if demo_password:  # only fills missing hashes; never overwrites a changed password
        for row in await session.scalars(select(User).where(User.password_hash.is_(None))):
            row.password_hash = await hash_password_async(demo_password, n=password_cost)
    if await session.get(Workflow, "partner_onboarding") is None:
        session.add(
            Workflow(
                id="partner_onboarding",
                version="1",
                description="Business request intake & resolution: partner onboarding",
                stages=STAGES,
            )
        )
    for iid, name in [
        ("crm", "CRM"),
        ("ticketing", "Ticketing"),
        ("knowledge_base", "Knowledge base"),
        ("notifications", "Notifications"),
    ]:
        row_i = await session.get(Integration, iid)
        if row_i is None:
            session.add(Integration(id=iid, name=name, base_url=mock_url))
        else:
            row_i.base_url = mock_url
    await session.commit()
