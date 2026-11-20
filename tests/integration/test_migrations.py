"""Migrations must build the exact schema the ORM models describe."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from backend.core.config import REPO_ROOT


@pytest.mark.integration
def test_upgrade_check_downgrade(tmp_path: Path) -> None:
    env = {"WFO_DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}"}
    import os

    env = {**os.environ, **env}
    for args in (["upgrade", "head"], ["check"], ["downgrade", "base"]):
        r = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
