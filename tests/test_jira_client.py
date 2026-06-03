"""Tests for JiraClient — the read-only Jira REST wrapper.

Same approach as test_gitlab_client.py: respx mocks the HTTP layer, so
these are fast (under 1s for the whole module) and never hit real Jira.

Six tests cover the surface:
  - list_projects: happy path, auth error, server error
  - search_issues: happy path with results, empty results, JQL forwarding
"""

from __future__ import annotations

import httpx
import pytest
import respx

from gitlab_mcp.config import Settings
from jira_mcp.errors import JiraAuthError, JiraServerError
from jira_mcp.jira_client import JiraClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """A Settings object populated only with Jira creds (other fields default)."""
    return Settings(
        gitlab_token="unused-for-jira-tests",  # type: ignore[arg-type]
        jira_url="https://acme.atlassian.net",  # type: ignore[arg-type]
        jira_email="seeder@example.com",
        jira_token="fake-jira-token",  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


@respx.mock
async def test_list_projects_returns_models(settings: Settings) -> None:
    respx.get(
        "https://acme.atlassian.net/rest/api/3/project/search"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {"id": "10000", "key": "SCRUM", "name": "Acme Platform",
                     "projectTypeKey": "software"},
                    {"id": "10001", "key": "AM", "name": "Acme Mobile",
                     "projectTypeKey": "software"},
                ]
            },
        )
    )

    async with JiraClient(settings) as client:
        projects = await client.list_projects()

    assert len(projects) == 2
    assert projects[0].key == "SCRUM"
    assert projects[1].key == "AM"
    # Aliased field should populate from the JSON's camelCase key.
    assert projects[0].project_type_key == "software"


@respx.mock
async def test_list_projects_auth_error_translates(settings: Settings) -> None:
    respx.get(
        "https://acme.atlassian.net/rest/api/3/project/search"
    ).mock(return_value=httpx.Response(401))

    async with JiraClient(settings) as client:
        with pytest.raises(JiraAuthError):
            await client.list_projects()


@respx.mock
async def test_list_projects_server_error_translates(settings: Settings) -> None:
    respx.get(
        "https://acme.atlassian.net/rest/api/3/project/search"
    ).mock(return_value=httpx.Response(503))

    async with JiraClient(settings) as client:
        with pytest.raises(JiraServerError):
            await client.list_projects()


# ---------------------------------------------------------------------------
# search_issues
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_issues_returns_models(settings: Settings) -> None:
    respx.get("https://acme.atlassian.net/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={
                "issues": [
                    {
                        "id": "20001",
                        "key": "SCRUM-1",
                        "fields": {
                            "summary": "Login fails on password reset",
                            "status": {"name": "To Do"},
                            "issuetype": {"name": "Bug"},
                        },
                    },
                    {
                        "id": "20002",
                        "key": "SCRUM-3",
                        "fields": {
                            "summary": "Add idempotency keys to charge endpoint",
                            "status": {"name": "In Progress"},
                            "issuetype": {"name": "Task"},
                        },
                    },
                ]
            },
        )
    )

    async with JiraClient(settings) as client:
        issues = await client.search_issues(project_key="SCRUM", query="login")

    assert len(issues) == 2
    assert issues[0].key == "SCRUM-1"
    assert issues[0].summary == "Login fails on password reset"
    assert issues[0].status == "To Do"
    assert issues[0].issue_type == "Bug"
    assert issues[1].status == "In Progress"


@respx.mock
async def test_search_issues_empty_results(settings: Settings) -> None:
    respx.get("https://acme.atlassian.net/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": []})
    )

    async with JiraClient(settings) as client:
        issues = await client.search_issues(project_key="AM", query="nothing")

    assert issues == []


@respx.mock
async def test_search_issues_builds_jql_with_project_and_query(
    settings: Settings,
) -> None:
    """JQL must scope by project and include the keyword. Exact JQL syntax
    matters because Jira parses it server-side — wrong syntax = 400."""
    route = respx.get("https://acme.atlassian.net/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": []})
    )

    async with JiraClient(settings) as client:
        await client.search_issues(project_key="SCRUM", query="payment")

    assert route.called
    sent_params = dict(route.calls.last.request.url.params)
    # Project scoping AND keyword text-search must both be in the JQL.
    assert 'project = "SCRUM"' in sent_params["jql"]
    assert 'text ~ "payment"' in sent_params["jql"]
    # Bounded result set, same as GitLab side.
    assert int(sent_params["maxResults"]) <= 100
    # Restrict the response shape we ask for — we don't need ADF descriptions.
    assert "summary" in sent_params["fields"]
    assert "status" in sent_params["fields"]
    assert "issuetype" in sent_params["fields"]