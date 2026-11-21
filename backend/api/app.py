"""FastAPI application factory. No framework-specific logic lives here."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from backend.api.routes import approvals, integrations, requests, workflows
from backend.core.config import Settings, get_settings
from backend.core.logging import configure_logging, get_logger
from backend.database.models import Base
from backend.database.seed import seed_reference_data
from backend.database.session import make_engine, make_sessionmaker
from backend.frameworks.rules.adapter import RulesAdapter
from backend.integrations.connectors import ConnectorSet
from backend.llm.client import GeminiClient
from backend.orchestration.engine import AdapterFactory, WorkflowEngine

log = get_logger(__name__)


@lru_cache(maxsize=4)
def _gemini_client(key: str, model: str, temperature: float) -> GeminiClient:
    # SDK client construction is slow (~3s, blocking); build it once per process, lazily.
    return GeminiClient(key, model, temperature)


def default_adapters(settings: Settings) -> dict[str, AdapterFactory]:
    adapters: dict[str, AdapterFactory] = {"rules": RulesAdapter}
    key = settings.google_api_key.get_secret_value()
    if key:
        # Imported lazily: ADK and LangGraph are heavy, and a demo without a key needs neither.
        from backend.frameworks.adk.adapter import ADKAdapter
        from backend.frameworks.gemini.adapter import GeminiAdapter
        from backend.frameworks.langgraph.adapter import LangGraphAdapter

        model, temp = settings.llm_model, settings.llm_temperature
        adapters["gemini"] = lambda gw: GeminiAdapter(gw, _gemini_client(key, model, temp))
        adapters["langgraph"] = lambda gw: LangGraphAdapter(gw, _gemini_client(key, model, temp))
        os.environ.setdefault("GOOGLE_API_KEY", key)  # ADK reads the key from the environment
        adapters["adk"] = lambda gw: ADKAdapter(gw, settings.llm_model)
    if settings.enabled_frameworks:  # e.g. "rules" for a public demo without an LLM key
        allowed = {f.strip() for f in settings.enabled_frameworks.split(",")}
        adapters = {k: v for k, v in adapters.items() if k in allowed}
    return adapters


def create_app(
    settings: Settings | None = None,
    *,
    mock_client: httpx.AsyncClient | None = None,
    create_schema: bool | None = None,
    engine_kwargs: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = make_engine(settings.database_url)
        sm = make_sessionmaker(db)
        auto = (
            create_schema
            if create_schema is not None
            else settings.database_url.startswith("sqlite")
        )
        if auto:
            async with db.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        async with sm() as s:
            await seed_reference_data(s, settings.data_dir, settings.mock_systems_url)
        client = mock_client or httpx.AsyncClient(base_url=settings.mock_systems_url)
        connectors = ConnectorSet(client, settings.connector_timeout_s)
        app.state.settings = settings
        app.state.sessionmaker = sm
        app.state.connectors = connectors
        app.state.engine = WorkflowEngine(
            sm, connectors, default_adapters(settings), settings, **(engine_kwargs or {})
        )
        app.state.tasks = set()
        try:
            await app.state.engine.recover()
        except Exception as e:  # mock systems may not be up yet; recovery retried later
            log.warning("startup_recovery_failed", error=str(e))
        yield
        for t in list(app.state.tasks):
            t.cancel()
        await asyncio.gather(*app.state.tasks, return_exceptions=True)
        if mock_client is None:
            await client.aclose()
        await db.dispose()

    app = FastAPI(
        title="WorkflowOps API",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": exc.status_code, "message": exc.detail}}, status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": 422,
                    "message": "validation error",
                    "details": jsonable(exc.errors()),
                }
            },
            status_code=422,
        )

    @app.get("/api/v1/frameworks")
    async def frameworks() -> list[str]:
        return sorted(app.state.engine.adapters)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/api/v1/docs")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for r in (requests.router, workflows.router, approvals.router, integrations.router):
        app.include_router(r, prefix="/api/v1")
    return app


def jsonable(obj: Any) -> Any:
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(obj, custom_encoder={Exception: str})
