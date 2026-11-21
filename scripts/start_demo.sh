#!/bin/sh
# Public demo entrypoint: mock business systems + API in one container (used by render.yaml).
set -e
uvicorn mock_business_systems.app:app --host 127.0.0.1 --port 8100 &
alembic upgrade head
exec uvicorn backend.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
