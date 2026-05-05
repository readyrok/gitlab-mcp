"""
Tests for GitLabClient.

We mock httpx with `respx` rather than hitting real GitLab. Tests run
in milliseconds, work offline, and don't burn API rate limit.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from datetime import datetime, timezone
from gitlab_mcp.config import Settings
from gitlab_mcp.errors import GitLabAuthError, GitLabNotFoundError, GitLabServerError
from gitlab_mcp.gitlab_client import GitLabClient
from gitlab_mcp.models import Issue, MergeRequest, Pipeline, Project


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

# ------------------------------------------------------------------
# search_issues
# ------------------------------------------------------------------

_FAKE_ISSUE = {
    "id": 9001,
    "iid": 3,
    "project_id": 81913181,
    "title": "POST /orders occasionally returns 500 under load",
    "description": "Reproduction: 200 req/s for 30s causes ~2% of requests to fail.",
    "state": "opened",
    "labels": ["bug", "priority::high", "area::api"],
    "web_url": "https://gitlab.com/sebastian/acme-order-service/-/issues/3",
    "author": {"id": 37913311, "username": "sebastian", "name": "Sebastian Luca"},
    "created_at": "2026-04-28T10:00:00.000Z",
    "updated_at": "2026-05-04T11:00:00.000Z",
    "closed_at": None,
    "weight": None,  # ignored field
}


@respx.mock
async def test_search_issues_returns_parsed_issues(fake_settings: Settings) -> None:
    route = respx.get(
        "https://gitlab.example.com/api/v4/projects/81913181/issues"
    ).mock(return_value=httpx.Response(200, json=[_FAKE_ISSUE]))

    async with GitLabClient(fake_settings) as client:
        issues = await client.search_issues(project_id=81913181, query="500")

    assert route.called
    assert len(issues) == 1
    assert isinstance(issues[0], Issue)
    assert issues[0].iid == 3
    assert "bug" in issues[0].labels

@respx.mock
async def test_search_issues_with_empty_query_returns_all_issues(
    fake_settings: Settings,
) -> None:
    """An empty query is valid — agents sometimes ask 'what issues exist?'
    without a specific keyword. GitLab returns everything matching state."""
    route = respx.get(
        "https://gitlab.example.com/api/v4/projects/81913181/issues"
    ).mock(return_value=httpx.Response(200, json=[_FAKE_ISSUE, _FAKE_ISSUE]))

    async with GitLabClient(fake_settings) as client:
        issues = await client.search_issues(project_id=81913181, query="")

    assert len(issues) == 2
    sent_request = route.calls.last.request
    assert sent_request.url.params["search"] == ""

# ------------------------------------------------------------------
# get_pipeline_status
# ------------------------------------------------------------------

_FAKE_PIPELINE = {
    "id": 7001,
    "iid": 1,
    "project_id": 81913181,
    "sha": "abc123def456",
    "ref": "main",
    "status": "failed",
    "source": "push",
    "web_url": "https://gitlab.com/sebastian/acme-order-service/-/pipelines/7001",
    "created_at": "2026-05-05T07:30:00.000Z",
    "updated_at": "2026-05-05T07:32:15.000Z",
    "duration": 135,
}


@respx.mock
async def test_get_pipeline_status_returns_recent_pipelines(
    fake_settings: Settings,
) -> None:
    route = respx.get(
        "https://gitlab.example.com/api/v4/projects/81913181/pipelines"
    ).mock(return_value=httpx.Response(200, json=[_FAKE_PIPELINE]))

    async with GitLabClient(fake_settings) as client:
        pipelines = await client.get_pipeline_status(project_id=81913181)

    assert route.called
    assert len(pipelines) == 1
    assert isinstance(pipelines[0], Pipeline)
    assert pipelines[0].status == "failed"
    assert pipelines[0].ref == "main"

@respx.mock
async def test_get_pipeline_status_raises_not_found_for_invalid_project(
    fake_settings: Settings,
) -> None:
    """GitLab returns 404 for non-existent project IDs — surface as typed error."""
    respx.get(
        "https://gitlab.example.com/api/v4/projects/99999999/pipelines"
    ).mock(return_value=httpx.Response(404, json={"message": "404 Project Not Found"}))

    async with GitLabClient(fake_settings) as client:
        with pytest.raises(GitLabNotFoundError):
            await client.get_pipeline_status(project_id=99999999)

# ------------------------------------------------------------------
# get_user_activity (and its _resolve_username helper)
# ------------------------------------------------------------------

@respx.mock
async def test_resolve_username_returns_user_id(fake_settings: Settings) -> None:
    """Looking up a username should hit /users?username=X and return the numeric id."""
    route = respx.get("https://gitlab.example.com/api/v4/users").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 37913311, "username": "sebastian", "name": "Sebastian Luca"}],
        )
    )

    async with GitLabClient(fake_settings) as client:
        user_id = await client._resolve_username("sebastian")

    assert route.called
    assert user_id == 37913311
    # Verify the username was passed as a query param
    assert route.calls.last.request.url.params["username"] == "sebastian"

@respx.mock
async def test_get_user_activity_aggregates_events_by_category(
    fake_settings: Settings,
) -> None:
    """Aggregates raw events from /users/{id}/events into a tidy summary."""
    # First HTTP call: username -> id
    respx.get("https://gitlab.example.com/api/v4/users").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 37913311, "username": "sebastian", "name": "Sebastian Luca"}],
        )
    )

    # Second HTTP call: events for that user.
    # GitLab returns a wide variety of action_name values; we model the
    # ones we care about and ignore the rest.
    fake_events = [
        {"action_name": "pushed to", "target_title": None, "created_at": "2026-05-04T10:00Z"},
        {"action_name": "pushed to", "target_title": None, "created_at": "2026-05-04T11:00Z"},
        {"action_name": "pushed to", "target_title": None, "created_at": "2026-05-04T12:00Z"},
        {"action_name": "opened",
         "target_type": "MergeRequest",
         "target_title": "Add idempotency keys",
         "created_at": "2026-05-03T09:00Z"},
        {"action_name": "opened",
         "target_type": "Issue",
         "target_title": "Race condition in webhook",
         "created_at": "2026-05-02T15:00Z"},
        {"action_name": "commented on",
         "target_type": "Issue",
         "target_title": "POST /orders 500s under load",
         "created_at": "2026-05-02T16:00Z"},
        # An event type we don't care about — should be ignored:
        {"action_name": "joined", "target_title": None, "created_at": "2026-05-01T08:00Z"},
    ]
    respx.get(
        "https://gitlab.example.com/api/v4/users/37913311/events"
    ).mock(return_value=httpx.Response(200, json=fake_events))

    async with GitLabClient(fake_settings) as client:
        activity = await client.get_user_activity(
            username="sebastian",
            since=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )

    assert activity.username == "sebastian"
    assert activity.user_id == 37913311
    assert activity.total_events == 7  # all events count toward total
    assert activity.pushes == 3
    assert activity.merge_requests_opened == 1
    assert activity.issues_opened == 1
    assert activity.comments == 1
    # We expose a few headline event titles for the agent to reason about.
    assert any("idempotency" in title.lower() for title in activity.recent_event_titles)

@respx.mock
async def test_get_user_activity_raises_not_found_for_unknown_username(
    fake_settings: Settings,
) -> None:
    """A non-existent username should raise immediately, not fall through to events."""
    # /users?username=ghost returns []
    respx.get("https://gitlab.example.com/api/v4/users").mock(
        return_value=httpx.Response(200, json=[])
    )

    async with GitLabClient(fake_settings) as client:
        with pytest.raises(GitLabNotFoundError) as exc_info:
            await client.get_user_activity(
                username="ghost",
                since=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )

    assert "ghost" in str(exc_info.value)

@respx.mock
async def test_get_logs_structured_call_info(
    fake_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every API call emits a structured log line with path, status, elapsed_ms."""
    respx.get("https://gitlab.example.com/api/v4/projects").mock(
        return_value=httpx.Response(200, json=[])
    )

    with caplog.at_level("INFO", logger="gitlab_mcp.client"):
        async with GitLabClient(fake_settings) as client:
            await client.list_projects()

    log_messages = [r.getMessage() for r in caplog.records]
    assert any("gitlab.api.call" in msg for msg in log_messages)
    assert any("status=200" in msg for msg in log_messages)
    assert any("path=/projects" in msg for msg in log_messages)

# ------------------------------------------------------------------
# pagination
# ------------------------------------------------------------------

@respx.mock
async def test_list_projects_paginates_via_link_header(fake_settings: Settings) -> None:
    """list_projects follows the Link: rel='next' header until exhausted."""
    base = "https://gitlab.example.com/api/v4/projects"

    # Page 1 — points at page 2 via Link header.
    page1 = [_FAKE_PROJECT, _FAKE_PROJECT]
    respx.get(base, params={"membership": "true", "simple": "false", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(
            200,
            json=page1,
            headers={"Link": f'<{base}?page=2&per_page=100>; rel="next"'},
        )
    )

    # Page 2 — no Link header, so pagination stops.
    page2 = [_FAKE_PROJECT]
    respx.get(base, params={"page": "2", "per_page": "100"}).mock(
        return_value=httpx.Response(200, json=page2)  # no Link: stops here
    )

    async with GitLabClient(fake_settings) as client:
        projects = await client.list_projects()

    # 2 from page 1 + 1 from page 2
    assert len(projects) == 3


@respx.mock
async def test_pagination_respects_max_pages_cap(fake_settings: Settings) -> None:
    """Pagination stops at the configured max_pages even if more data exists."""
    base = "https://gitlab.example.com/api/v4/projects"

    # Every page has a Link pointing to the next — but we should stop at the cap.
    # Setup: pages 1, 2, 3 all return data + a "next" link. The client should
    # only fetch up to max_pages (which list_projects sets to 5 by default,
    # but we'll override below for this test by using a high-volume scenario).
    for page in range(1, 11):
        respx.get(base, params={"page": str(page), "per_page": "100"}).mock(
            return_value=httpx.Response(
                200,
                json=[_FAKE_PROJECT],
                headers={"Link": f'<{base}?page={page+1}&per_page=100>; rel="next"'},
            )
        )
    # Page 1 is special — no `page` param yet
    respx.get(base, params={"membership": "true", "simple": "false", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(
            200,
            json=[_FAKE_PROJECT],
            headers={"Link": f'<{base}?page=2&per_page=100>; rel="next"'},
        )
    )

    async with GitLabClient(fake_settings) as client:
        projects = await client.list_projects()

    # Default cap is 5 pages, so we should have 5 items, not 10+.
    assert len(projects) == 5