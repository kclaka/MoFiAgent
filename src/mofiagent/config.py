from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PositiveFloat, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded through a single, validated boundary."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MOFI_",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)

    database_dsn: SecretStr | None = None
    migrations_dir: Path = Path("migrations")
    db_pool_min_size: int = Field(default=1, ge=0, le=10)
    db_pool_max_size: int = Field(default=4, ge=1, le=20)
    db_pool_timeout_seconds: PositiveFloat = 5.0
    db_connect_timeout_seconds: PositiveFloat = 5.0
    db_startup_timeout_seconds: PositiveFloat = 30.0

    google_cloud_project: str | None = None
    google_cloud_location: str = "us"
    vertex_model: str | None = "gemini-3.5-flash"

    max_agent_rounds: int = Field(default=4, ge=1, le=8)
    request_timeout_seconds: PositiveFloat = 20.0
    question_deadline_seconds: PositiveFloat = 90.0
    session_lease_seconds: int = Field(default=150, ge=30, le=900)

    @model_validator(mode="after")
    def validate_deadlines(self) -> "Settings":
        if self.session_lease_seconds < self.question_deadline_seconds + 30:
            raise ValueError(
                "MOFI_SESSION_LEASE_SECONDS must be at least 30 seconds longer than "
                "MOFI_QUESTION_DEADLINE_SECONDS"
            )
        return self

    def validate_for_database(self) -> None:
        if self.database_dsn is None:
            raise ValueError("MOFI_DATABASE_DSN is required")
        if self.db_pool_min_size > self.db_pool_max_size:
            raise ValueError("MOFI_DB_POOL_MIN_SIZE cannot exceed MOFI_DB_POOL_MAX_SIZE")

    def validate_for_service(self) -> None:
        self.validate_for_database()
        if not self.google_cloud_project:
            raise ValueError("MOFI_GOOGLE_CLOUD_PROJECT is required to use Vertex AI")
        if not self.vertex_model:
            raise ValueError("MOFI_VERTEX_MODEL is required to use Vertex AI")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
