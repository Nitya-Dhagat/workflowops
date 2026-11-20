"""Shared FastAPI dependencies."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.orchestration.engine import WorkflowEngine


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as s:
        yield s


def get_engine(request: Request) -> WorkflowEngine:
    engine: WorkflowEngine = request.app.state.engine
    return engine


def spawn(request: Request, coro: Coroutine[Any, Any, Any]) -> None:
    """Run a coroutine in the background, keeping a reference so it is not GC'd."""
    tasks: set[asyncio.Task[Any]] = request.app.state.tasks
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
EngineDep = Annotated[WorkflowEngine, Depends(get_engine)]
UUIDPath = uuid.UUID
