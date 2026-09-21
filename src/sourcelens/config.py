from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
