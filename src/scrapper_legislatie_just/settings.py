"""Validated application settings loaded from the environment and ``.env``.

The web service needs two secrets; the indexer does not. Keeping them in a
separate :class:`WebSettings` model means the scheduler container can start
without admin credentials, while the API fails fast at startup if they are
missing or too short.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CommaList = Annotated[list[str], NoDecode]

DEFAULT_HOSTS = [
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
]
DEFAULT_ORIGINS = ["http://localhost:8000"]


def _split_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    """Configuration shared by the web app, the indexer and the tool layer."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    public_laws_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000/laws")
    mcp_allowed_hosts: CommaList = Field(default_factory=lambda: list(DEFAULT_HOSTS))
    mcp_allowed_origins: CommaList = Field(
        default_factory=lambda: list(DEFAULT_ORIGINS)
    )
    mcp_disable_dns_rebinding: bool = False

    web_workers: int = Field(default=2, ge=1, le=32)
    web_host: str = "0.0.0.0"
    web_port: int = Field(default=8000, ge=1, le=65535)

    reindex_on_start: bool = False
    reindex_ocr: bool = True

    @field_validator("mcp_allowed_hosts", "mcp_allowed_origins", mode="before")
    @classmethod
    def _parse_comma_list(cls, value: str | list[str] | None) -> list[str]:
        return _split_csv(value)

    @property
    def laws_base_url(self) -> str:
        return str(self.public_laws_base_url).rstrip("/")


class WebSettings(Settings):
    """Settings that only the HTTP API needs."""

    admin_secret_key: SecretStr = Field(..., min_length=1)
    token_signing_secret: SecretStr = Field(..., min_length=16)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_web_settings() -> WebSettings:
    return WebSettings()
