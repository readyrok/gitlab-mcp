"""
Application configuration.

We use pydantic-settings to load config from environment variables (with .env
fallback for local dev). The benefits over a hand-rolled os.environ approach:

  * type validation — wrong types fail at startup, not at first use
  * required vs optional is explicit in the model
  * one Settings object passed around, vs imports of os.getenv scattered
    through the codebase
  * easy to override in tests by constructing Settings(...) directly

This pattern follows the "12-factor app" config principle: configuration is
read from the environment, never baked into code.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded once at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Surface unknown env vars instead of silently ignoring them — a typo
        # in GITLAB_TOKEN_ should fail loudly, not run with no auth.
        extra="ignore",
        case_sensitive=False,
    )

    gitlab_url: HttpUrl = Field(
        default="https://gitlab.com",  # type: ignore[arg-type]
        description="Base URL of the GitLab instance.",
    )

    gitlab_token: SecretStr = Field(
        ...,  # required: server cannot start without a token
        description="GitLab Personal Access Token with read_api scope.",
    )

    request_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        description="HTTP timeout for GitLab API calls.",
    )

    log_level: str = Field(
        default="INFO",
        description="Python logging level for the server.",
    )

    # ----- Anthropic / agent settings -----
    # Optional in the runtime config because the gitlab-mcp server doesn't
    # need them; only the agent CLI does. Pydantic-settings will read them
    # from .env or environment if present.
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="Anthropic API key. Required only when running the agent.",
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-6",
        description="Claude model identifier for the agent.",
    )
    agent_max_iterations: int = Field(
        default=25,
        ge=1,
        le=100,
        description="Cap on tool-call iterations per question (loop safety net).",
    )

    @property
    def gitlab_api_base(self) -> str:
        """Convenience: the /api/v4 base URL the rest of the code uses."""
        return f"{str(self.gitlab_url).rstrip('/')}/api/v4"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Cached settings accessor.

    Cached so the .env file is read once per process. Tests that need
    different settings can call `get_settings.cache_clear()` or construct
    Settings(...) directly.
    """
    return Settings()  # type: ignore[call-arg]