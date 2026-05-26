"""
Fixtures and helpers for live evals.

Unlike the unit tests, evals construct a REAL agent: a real gitlab-mcp
subprocess plus real Anthropic calls. They need ANTHROPIC_API_KEY and
GITLAB_TOKEN in the environment / .env.

Important design note on why there's no `live_agent` *fixture*:

  The MCP stdio client uses anyio task groups internally. anyio requires
  the task that opens a cancel scope to be the same task that closes it.
  pytest-asyncio runs async-generator fixture *setup* and *teardown* in
  different tasks — so wrapping the MCP connection in a fixture raises
  'Attempted to exit cancel scope in a different task'.

  The fix: don't put the connection in a fixture. Provide a plain async
  context manager (`live_agent`) that each eval opens and closes inside
  its own test body — a single task — satisfying anyio's rule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from agent.loop import AgentLoop
from agent.mcp_client import MCPClientAdapter
from gitlab_mcp.config import get_settings


@pytest.fixture(autouse=True)
def _evals_dont_isolate_env() -> None:
    """Override the parent conftest's autouse env-isolation.

    tests/conftest.py scrubs GITLAB_* env vars before every test so unit
    tests can't hit real GitLab. Evals *want* real credentials, so this
    same-named, more-specific fixture no-ops that protection.
    """
    # Intentionally empty.


@asynccontextmanager
async def live_agent() -> AsyncIterator[AgentLoop]:
    """Async context manager yielding a real AgentLoop.

    Use inside an eval test body:

        async with live_agent() as agent:
            async for event in agent.ask("..."):
                ...

    NOT a pytest fixture — see the module docstring for why. Opening and
    closing the MCP connection inside the test's own task is what keeps
    anyio's cancel-scope rule satisfied.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        pytest.skip("ANTHROPIC_API_KEY not set — evals require real credentials")

    async with MCPClientAdapter(["uv", "run", "gitlab-mcp"]) as mcp:
        yield AgentLoop(settings=settings, mcp=mcp)