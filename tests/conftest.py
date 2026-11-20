from __future__ import annotations

import json
import os
import random
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from backend.api.app import create_app
from backend.core.config import REPO_ROOT, Settings
from mock_business_systems.app import create_app as create_mock_app


@pytest.fixture(scope="session")
def world() -> dict[str, Any]:
    return json.loads((REPO_ROOT / "data/seed/world.json").read_text(encoding="utf-8"))


@pytest.fixture
def world_date(world: dict[str, Any]) -> date:
    return date.fromisoformat(world["world_date"])


@pytest.fixture
def mock_app() -> FastAPI:
    return create_mock_app(REPO_ROOT / "data/seed/world.json", REPO_ROOT / "data/policies")


@pytest.fixture
async def mock_client(mock_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=mock_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        # Set WFO_TEST_DATABASE_URL to run the suite against Postgres (CI does).
        database_url=os.environ.get(
            "WFO_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
        ),
        mock_systems_url="http://mock",
        connector_timeout_s=0.5,
        retry_base_delay_s=0.0,
        retry_max_delay_s=0.0,
        log_json=False,
        log_level="WARNING",
    )


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def api(settings: Settings, mock_client: httpx.AsyncClient) -> AsyncIterator[FastAPI]:
    app = create_app(
        settings,
        mock_client=mock_client,
        create_schema=True,
        engine_kwargs={"sleep": _no_sleep, "rng": random.Random(0)},
    )
    if not settings.database_url.startswith("sqlite"):
        await _reset_db(settings.database_url)
    async with app.router.lifespan_context(app):
        yield app


async def _reset_db(url: str) -> None:
    from backend.database.models import Base
    from backend.database.session import make_engine

    engine = make_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(api: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://api"
    ) as c:
        yield c
