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
from gitlab_mcp.gitlab_client import GitLabClient
from gitlab_mcp.models import Project


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