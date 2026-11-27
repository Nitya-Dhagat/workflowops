"""Test helpers shared across test modules."""

from __future__ import annotations

import httpx

DEMO_PASSWORD = "workflowops-demo"


async def auth_headers(client: httpx.AsyncClient, user_id: str) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/login", json={"user_id": user_id, "password": DEMO_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
