"""Integration tests for the Jira MCP server.

Same approach as test_server.py for the GitLab side: we invoke the
registered tool functions through FastMCP with a hand-built Context,
against a respx-mocked Jira. This verifies the wiring — tool registered,
lifespan client reachable, result serialized — not the client internals
(those are covered by test_jira_client.py).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from gitlab_mcp.config import Settings
from jira_mcp.jira_client import JiraClient
from jira_mcp.server import ServerContext, list_projects, search_issues


@pytest.fixture
def settings() -> Settings:
    return Settings(
        gitlab_token="unused",  # type: ignore[arg-type]
        jira_url="https://acme.atlassian.net",  # type: ignore[arg-type]
        jira_email="seeder@example.com",
        jira_token="fake-token",  # type: ignore[arg-type]
    )


def _make_ctx(client: JiraClient) -> SimpleNamespace:
    """Build a minimal object shaped like FastMCP's Context.

    The tools only touch ctx.request_context.lifespan_context, so we only
    need to populate that path.
    """
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=ServerContext(jira=client)
        )
    )


@respx.mock
async def test_list_projects_tool_returns_serialized_dicts(settings: Settings) -> None:
    respx.get("https://acme.atlassian.net/rest/api/3/project/search").mock(
        return_value=httpx.Response(
            200,
            json={"values": [
                {"id": "10000", "key": "SCRUM", "name": "Acme Platform",
                 "projectTypeKey": "software"},
            ]},
        )
    )

    async with JiraClient(settings) as client:
        ctx = _make_ctx(client)
        result = await list_projects(ctx)  # type: ignore[arg-type]

    assert isinstance(result, list)
    assert result[0]["key"] == "SCRUM"
    assert result[0]["name"] == "Acme Platform"


@respx.mock
async def test_search_issues_tool_returns_serialized_dicts(settings: Settings) -> None:
    respx.get("https://acme.atlassian.net/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={"issues": [
                {
                    "id": "20001",
                    "key": "SCRUM-1",
                    "fields": {
                        "summary": "Login fails on password reset",
                        "status": {"name": "To Do"},
                        "issuetype": {"name": "Bug"},
                    },
                },
            ]},
        )
    )

    async with JiraClient(settings) as client:
        ctx = _make_ctx(client)
        result = await search_issues(ctx, project_key="SCRUM", query="login")  # type: ignore[arg-type]

    assert isinstance(result, list)
    assert result[0]["key"] == "SCRUM-1"
    assert result[0]["status"] == "To Do"
    assert result[0]["issue_type"] == "Bug"