"""
Shared pytest fixtures.

`conftest.py` files are auto-discovered by pytest — fixtures defined here are
available to every test in this directory tree without explicit imports.
"""

from __future__ import annotations

import os

import pytest

from gitlab_mcp.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Prevent the real .env from leaking into tests.

    `autouse=True` applies this to every test automatically — no chance of a
    test accidentally hitting gitlab.com because someone forgot a fixture.
    """
    # Wipe any GitLab-related env vars that might be set on the dev machine.
    for key in list(os.environ):
        if key.upper().startswith("GITLAB_"):
            monkeypatch.delenv(key, raising=False)

    # Drop the cached Settings so subsequent get_settings() calls re-read.
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """A minimal valid Settings object for tests that need one."""
    return Settings(
        gitlab_url="https://gitlab.example.com",  # type: ignore[arg-type]
        gitlab_token="test-token-not-real",  # type: ignore[arg-type]
    )