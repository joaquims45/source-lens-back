from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://sourcelens:sourcelens@127.0.0.1:55432/sourcelens"
    redis_url: str = "redis://127.0.0.1:56379/0"
    workspace_dir: Path = Path(".sourcelens/tmp")
    clone_timeout: int = Field(default=120, ge=1)
    job_timeout: int = Field(default=600, ge=1)
    max_clone_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
    max_text_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    max_file_bytes: int = Field(default=1024 * 1024, ge=1)
    max_files: int = Field(default=10_000, ge=1)
    max_entries: int = Field(default=50_000, ge=1)
    lease_seconds: int = Field(default=90, ge=10)
    max_job_attempts: int = Field(default=3, ge=1)
    # "hashing" (offline default), "voyage" or "openai".
    embedding_provider: str = "hashing"
    embedding_batch_size: int = Field(default=64, ge=1)
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-code-3"
    # "anthropic" (default) or "openai".
    agent_provider: str = "anthropic"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    # text-embedding-3-{small,large} support truncating to an arbitrary
    # `dimensions`, so this can match EMBEDDING_DIMENSIONS (256) exactly
    # without a schema migration.
    openai_embedding_model: str = "text-embedding-3-small"
    agent_model: str = DEFAULT_ANTHROPIC_MODEL
    agent_max_iterations: int = Field(default=6, ge=1)
    agent_search_k: int = Field(default=8, ge=1)

    @model_validator(mode="after")
    def _default_agent_model_for_provider(self) -> Self:
        # Only steps in if agent_model was left at the Anthropic default
        # while switching provider, so an explicit AGENT_MODEL always wins.
        if self.agent_provider == "openai" and self.agent_model == DEFAULT_ANTHROPIC_MODEL:
            self.agent_model = DEFAULT_OPENAI_MODEL
        return self

    # Comma-separated list of allowed browser origins. Local dev defaults to
    # the Vite dev server; a real deployment must set this explicitly.
    cors_allow_origins: str = "http://localhost:5173"
    # When set, every /api/v1 request must carry a matching X-API-Key header.
    # None (the default) keeps the local single-operator setup unauthenticated,
    # per the Milestone 0 security model — set this before exposing the
    # service publicly.
    api_key: str | None = None
    architecture_cache_ttl_seconds: int = Field(default=24 * 60 * 60, ge=1)

    @property
    def cors_allow_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
