"""
Integration tests for the MCP server.

These tests catch a class of bug the unit tests miss:

  * tool registered but with broken signature
  * lifespan context not flowing into tool functions
  * Pydantic models not serializing cleanly through model_dump

We don't repeat per-tool happy-path coverage — that's already in
test_gitlab_client.py. We test the server-level wiring once.

Strategy: rather than drive a full FastMCP session in-process (which
requires pinning to a specific SDK version's session API), we invoke
the registered tool functions directly with a hand-rolled Context that
exposes the lifespan-style ServerContext. This tests *our* code — the
tool functions — while keeping the SDK's session machinery out of the
test path.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from gitlab_mcp.config import Settings
from gitlab_mcp.gitlab_client import GitLabClient
from gitlab_mcp.server import ServerContext, mcp

_FAKE_PROJECT = {
    "id": 81913181,
    "name": "acme-order-service",
    "path": "acme-order-service",
    "path_with_namespace": "test/acme-order-service",
    "description": "test project",
    "web_url": "https://gitlab.example.com/test/acme-order-service",
    "default_branch": "main",
    "visibility": "private",
    "last_activity_at": "2026-05-05T08:00:00.000Z",
}


def _fake_context(server_ctx: ServerContext) -> SimpleNamespace:
    """Build a duck-typed Context that exposes `request_context.lifespan_context`.

    Our tools only ever read `ctx.request_context.lifespan_context`, so a
    namespace with that single attribute path is enough. We don't depend
    on the real FastMCP Context class, which keeps the test stable across
    SDK versions.
    """
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=server_ctx)
    )


@pytest.fixture
async def server_ctx() -> ServerContext:
    """A ServerContext pointing at a GitLabClient with mocked HTTP."""
    settings = Settings(
        gitlab_url="https://gitlab.example.com",  # type: ignore[arg-type]
        gitlab_token="test-token",  # type: ignore[arg-type]
    )
    client = GitLabClient(settings)
    yield ServerContext(gitlab=client)
    await client._http.aclose()


async def test_server_lists_all_five_tools() -> None:
    """All five tools are registered with the expected names."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "list_projects",
        "get_merge_requests",
        "search_issues",
        "get_pipeline_status",
        "get_user_activity",
    }


@respx.mock
async def test_list_projects_tool_end_to_end(server_ctx: ServerContext) -> None:
    """End-to-end: tool function -> GitLabClient -> mocked HTTP -> serialized dict."""
    respx.get("https://gitlab.example.com/api/v4/projects").mock(
        return_value=httpx.Response(200, json=[_FAKE_PROJECT])
    )

    # Pull the registered tool function out of the manager and call it
    # directly with our fake context. This exercises the actual code path
    # the MCP runtime would execute, minus the protocol wrapping.
    tool = mcp._tool_manager.get_tool("list_projects")
    assert tool is not None, "list_projects must be registered"

    result = await tool.fn(ctx=_fake_context(server_ctx))

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "acme-order-service"
    assert result[0]["id"] == 81913181
    # Confirm Pydantic's mode='json' serialization actually fired
    # (datetimes become strings, not datetime objects):
    assert isinstance(result[0]["last_activity_at"], str)