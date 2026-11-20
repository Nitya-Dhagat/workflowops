from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi import Request as FastAPIRequest
from sqlalchemy import select

from backend.api.deps import SessionDep
from backend.database.models import Integration, utcnow
from backend.integrations.connectors import ConnectorSet

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("")
async def list_integrations(session: SessionDep) -> list[dict[str, Any]]:
    rows = await session.scalars(select(Integration).order_by(Integration.id))
    return [
        {
            "id": i.id,
            "name": i.name,
            "base_url": i.base_url,
            "last_status": i.last_status,
            "last_checked_at": i.last_checked_at,
        }
        for i in rows
    ]


@router.post("/{integration_id}/test")
async def test_integration(
    integration_id: str, session: SessionDep, http: FastAPIRequest
) -> dict[str, Any]:
    row = await session.get(Integration, integration_id)
    if row is None:
        raise HTTPException(404, "integration not found")
    connectors: ConnectorSet = http.app.state.connectors
    health = await connectors.by_name(integration_id).health_check()
    row.last_status = "ok" if health.ok else "down"
    row.last_checked_at = utcnow()
    await session.commit()
    return health.model_dump()
