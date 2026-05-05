"""
Tests for GitLabClient.

We mock httpx with `respx` rather than hitting real GitLab. Tests run
in milliseconds, work offline, and don't burn API rate limit.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from gitlab_mcp.config import Settings
from gitlab_mcp.errors import GitLabAuthError, GitLabServerError
from gitlab_mcp.gitlab_client import GitLabClient
from gitlab_mcp.models import MergeRequest, Project


# A canned project payload shaped like a real GitLab response.
# We keep it small — just the fields our Project model actually parses.
_FAKE_PROJECT = {
    "id": 81913181,
    "name": "acme-order-service",
    "path": "acme-order-service",
    "path_with_namespace": "sebastian/acme-order-service",
    "description": "Acme Robotics — order intake service.",
    "web_url": "https://gitlab.com/sebastian/acme-order-service",
    "default_branch": "main",
    "visibility": "private",
    "last_activity_at": "2026-05-05T08:00:00.000Z",
    # Plus a bunch of fields we ignore — proves extra="ignore" works:
    "container_registry_enabled": True,
    "shared_runners_enabled": True,
}


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(
        gitlab_url="https://gitlab.example.com",  # type: ignore[arg-type]
        gitlab_token="test-token",  # type: ignore[arg-type]
    )


@respx.mock
async def test_list_projects_returns_parsed_projects(fake_settings: Settings) -> None:
    # Arrange: stub the /projects endpoint with a single project.
    route = respx.get("https://gitlab.example.com/api/v4/projects").mock(
        return_value=httpx.Response(200, json=[_FAKE_PROJECT])
    )

    # Act
    async with GitLabClient(fake_settings) as client:
        projects = await client.list_projects()

    # Assert: route was called, response was parsed into our model.
    assert route.called
    assert len(projects) == 1
    assert isinstance(projects[0], Project)
    assert projects[0].id == 81913181
    assert projects[0].name == "acme-order-service"
    assert projects[0].path_with_namespace == "sebastian/acme-order-service"

@respx.mock
async def test_list_projects_sends_private_token_header(fake_settings: Settings) -> None:
    """The PRIVATE-TOKEN header is the linchpin of GitLab auth — verify it's set."""
    route = respx.get("https://gitlab.example.com/api/v4/projects").mock(
        return_value=httpx.Response(200, json=[])
    )

    async with GitLabClient(fake_settings) as client:
        await client.list_projects()

    sent_request = route.calls.last.request
    assert sent_request.headers["PRIVATE-TOKEN"] == "test-token"

@respx.mock
async def test_list_projects_raises_auth_error_on_401(fake_settings: Settings) -> None:
    """A 401 from GitLab should surface as a typed GitLabAuthError, not a raw HTTPError."""
    respx.get("https://gitlab.example.com/api/v4/projects").mock(
        return_value=httpx.Response(401, json={"message": "401 Unauthorized"})
    )

    async with GitLabClient(fake_settings) as client:
        with pytest.raises(GitLabAuthError) as exc_info:
            await client.list_projects()

    # The error message should be readable enough that someone debugging
    # the MCP server in production knows what to fix.
    assert "401" in str(exc_info.value) or "scope" in str(exc_info.value).lower()


@respx.mock
async def test_list_projects_raises_auth_error_on_403(fake_settings: Settings) -> None:
    """403 (insufficient scope) maps to the same exception as 401 — they're both 'fix your token'."""
    respx.get("https://gitlab.example.com/api/v4/projects").mock(
        return_value=httpx.Response(403, json={"message": "403 Forbidden"})
    )

    async with GitLabClient(fake_settings) as client:
        with pytest.raises(GitLabAuthError):
            await client.list_projects()

@respx.mock
async def test_list_projects_returns_empty_list_when_no_projects(
    fake_settings: Settings,
) -> None:
    """A user with no accessible projects gets an empty list, not an error."""
    respx.get("https://gitlab.example.com/api/v4/projects").mock(
        return_value=httpx.Response(200, json=[])
    )

    async with GitLabClient(fake_settings) as client:
        projects = await client.list_projects()

    assert projects == []

@respx.mock
async def test_list_projects_raises_server_error_on_5xx(fake_settings: Settings) -> None:
    """5xx responses are typically transient — surface them as a typed error so callers can retry."""
    respx.get("https://gitlab.example.com/api/v4/projects").mock(
        return_value=httpx.Response(503, text="Service Unavailable")
    )

    async with GitLabClient(fake_settings) as client:
        with pytest.raises(GitLabServerError) as exc_info:
            await client.list_projects()

    assert "503" in str(exc_info.value)

    # ------------------------------------------------------------------
# get_merge_requests
# ------------------------------------------------------------------

_FAKE_MR = {
    "id": 5001,
    "iid": 12,
    "project_id": 81913181,
    "title": "Add idempotency keys to POST /orders",
    "description": "Closes the duplicate-order issue.",
    "state": "opened",
    "draft": False,
    "web_url": "https://gitlab.com/sebastian/acme-order-service/-/merge_requests/12",
    "source_branch": "feat/idempotency-keys",
    "target_branch": "main",
    "author": {"id": 37913311, "username": "sebastian", "name": "Sebastian Luca"},
    "created_at": "2026-05-01T08:00:00.000Z",
    "updated_at": "2026-05-04T16:30:00.000Z",
    "merged_at": None,
    # Plus extra fields we ignore:
    "work_in_progress": False,
    "milestone": None,
}


@respx.mock
async def test_get_merge_requests_returns_parsed_mrs(fake_settings: Settings) -> None:
    route = respx.get(
        "https://gitlab.example.com/api/v4/projects/81913181/merge_requests"
    ).mock(return_value=httpx.Response(200, json=[_FAKE_MR]))

    async with GitLabClient(fake_settings) as client:
        mrs = await client.get_merge_requests(project_id=81913181, state="opened")

    assert route.called
    assert len(mrs) == 1
    assert isinstance(mrs[0], MergeRequest)
    assert mrs[0].iid == 12
    assert mrs[0].title.startswith("Add idempotency keys")
    assert mrs[0].author.username == "sebastian"

@respx.mock
async def test_get_merge_requests_passes_state_filter_to_api(
    fake_settings: Settings,
) -> None:
    """The state argument must reach GitLab as a query parameter."""
    route = respx.get(
        "https://gitlab.example.com/api/v4/projects/81913181/merge_requests"
    ).mock(return_value=httpx.Response(200, json=[]))

    async with GitLabClient(fake_settings) as client:
        await client.get_merge_requests(project_id=81913181, state="merged")

    sent_request = route.calls.last.request
    # respx exposes query params via the URL object
    assert sent_request.url.params["state"] == "merged"
    assert sent_request.url.params["order_by"] == "updated_at"