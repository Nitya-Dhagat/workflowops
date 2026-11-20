"""ASGI entrypoint: ``uvicorn backend.api.main:app``."""

from backend.api.app import create_app

app = create_app()
