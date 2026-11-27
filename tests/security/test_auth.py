"""Authentication and authorization: identity comes from a verified token, roles are enforced."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from backend.api.auth import create_token
from backend.database.models import User
from backend.security.passwords import hash_password, verify_password
from tests.helpers import DEMO_PASSWORD, auth_headers

pytestmark = pytest.mark.integration


@pytest.fixture
async def anon(api: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://api")


def test_password_hashing_is_salted_and_verifiable() -> None:
    a, b = hash_password("s3cret"), hash_password("s3cret")
    assert a != b and verify_password("s3cret", a) and not verify_password("nope", a)
    assert not verify_password("s3cret", None) and not verify_password("s3cret", "plain")


async def test_every_api_route_requires_sign_in(anon: httpx.AsyncClient) -> None:
    for method, path in [
        ("GET", "/api/v1/requests"),
        ("POST", "/api/v1/requests"),
        ("GET", "/api/v1/approvals"),
        ("GET", "/api/v1/workflows/runs"),
        ("GET", "/api/v1/integrations"),
    ]:
        r = await anon.request(method, path, json={"description": "x"})
        assert r.status_code == 401, path
    assert (await anon.get("/health")).status_code == 200  # health stays public


async def test_login_rejects_bad_credentials_without_revealing_which(anon) -> None:  # type: ignore[no-untyped-def]
    bad_pw = await anon.post("/api/v1/auth/login", json={"user_id": "u_01", "password": "x"})
    no_user = await anon.post(
        "/api/v1/auth/login", json={"user_id": "nobody", "password": DEMO_PASSWORD}
    )
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json()["error"]["message"] == no_user.json()["error"]["message"]


async def test_inactive_user_cannot_sign_in(anon) -> None:  # type: ignore[no-untyped-def]
    r = await anon.post("/api/v1/auth/login", json={"user_id": "u_22", "password": DEMO_PASSWORD})
    assert r.status_code == 401


async def test_tampered_and_expired_tokens_are_rejected(anon, api) -> None:  # type: ignore[no-untyped-def]
    good = (await auth_headers(anon, "u_01"))["Authorization"].split()[1]
    forged = jwt.encode(
        {"sub": "u_10", "role": "reviewer", "exp": 9_999_999_999},
        "attacker-secret-that-is-at-least-32-bytes",
        algorithm="HS256",
    )
    user = User(id="u_01", name="x", email="x", role="requester")
    expired = create_token(user, api.state.jwt_secret, now=datetime.now(UTC) - timedelta(days=2))
    for token in (good[:-2] + "xx", forged, expired):
        r = await anon.get("/api/v1/requests", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


async def test_identity_comes_from_token_not_body(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.post(
        "/api/v1/requests", json={"description": "Onboard Juniper Logic", "requester_id": "u_10"}
    )
    assert r.status_code == 422  # the field no longer exists: no spoofing through the body
    r = await client.post("/api/v1/requests", json={"description": "Onboard Juniper Logic"})
    assert r.json()["requester_id"] == "u_01"


async def test_only_reviewers_decide_and_decision_is_attributed(client) -> None:  # type: ignore[no-untyped-def]
    rid = (
        await client.post("/api/v1/requests", json={"description": "Onboard Juniper Logic"})
    ).json()["id"]
    await client.post(f"/api/v1/workflows/{rid}/run?wait=true", json={"framework": "rules"})
    ap = (await client.get("/api/v1/approvals")).json()[0]
    body = {"decision": "approve", "action_hash": ap["action_hash"]}
    r = await client.post(f"/api/v1/approvals/{ap['id']}/decision", json=body)
    assert r.status_code == 403  # u_01 is a requester
    r = await client.post(
        f"/api/v1/approvals/{ap['id']}/decision",
        json=body,
        headers=await auth_headers(client, "u_11"),
    )
    assert r.status_code == 200 and r.json()["decided_by"] == "u_11"


async def test_created_account_can_sign_in_and_act_as_reviewer(anon, api) -> None:  # type: ignore[no-untyped-def]
    from backend.database.accounts import upsert_user

    async with api.state.sessionmaker() as s:
        await upsert_user(
            s, user_id="test.owner", name="Test Owner", password="owner-pass-1", cost=2**8
        )
    r = await anon.post(
        "/api/v1/auth/login", json={"user_id": "test.owner", "password": "owner-pass-1"}
    )
    assert r.status_code == 200 and r.json()["user"]["role"] == "reviewer"
    wrong = await anon.post(
        "/api/v1/auth/login", json={"user_id": "test.owner", "password": DEMO_PASSWORD}
    )
    assert wrong.status_code == 401  # named accounts never get the demo password


async def test_account_input_is_validated(api) -> None:  # type: ignore[no-untyped-def]
    from backend.database.accounts import upsert_user

    async with api.state.sessionmaker() as s:
        for uid, pw in [("ab", "longenough"), ("Bad Name", "longenough"), ("okname", "123")]:
            with pytest.raises(ValueError):
                await upsert_user(s, user_id=uid, name="x", password=pw, cost=2**8)


async def test_owner_account_is_created_from_settings(settings, mock_client) -> None:  # type: ignore[no-untyped-def]
    from backend.api.app import create_app

    s = settings.model_copy(
        update={
            "owner_user_id": "site.owner",
            "owner_name": "Site Owner",
            "owner_password": SecretStr("owner-secret-9"),
        }
    )
    app = create_app(s, mock_client=mock_client, create_schema=True)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c,
    ):
        r = await c.post(
            "/api/v1/auth/login", json={"user_id": "site.owner", "password": "owner-secret-9"}
        )
        assert r.status_code == 200 and r.json()["user"]["name"] == "Site Owner"
