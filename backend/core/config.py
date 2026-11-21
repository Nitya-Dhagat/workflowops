"""Platform configuration, loaded from environment variables (prefix ``WFO_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WFO_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "sqlite+aiosqlite:///./workflowops.db"
    mock_systems_url: str = "http://localhost:8100"
    log_level: str = "INFO"
    log_json: bool = True

    # Workflow / policy knobs
    medium_risk_requires_approval: bool = False
    approval_ttl_hours: int = 72
    connector_timeout_s: float = 5.0
    max_action_attempts: int = 4
    retry_base_delay_s: float = 0.2
    retry_max_delay_s: float = 3.0
    investigation_timeout_s: float = 15.0
    investigation_concurrency: int = 3

    data_dir: Path = REPO_ROOT / "data"

    # Deployment: comma-separated CORS origins, an optional origin regex (e.g. Vercel URLs),
    # and an optional framework allowlist (a public demo exposes only "rules").
    cors_origins: str = "http://localhost:3000"
    cors_origin_regex: str = ""
    enabled_frameworks: str = ""

    # LLM (one provider/model for every framework so comparisons are fair).
    llm_model: str = "gemini-3.5-flash"
    llm_temperature: float = 0.0
    google_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="GOOGLE_API_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
