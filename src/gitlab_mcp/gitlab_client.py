"""
Async GitLab API client.

Design notes:

  * Async because the MCP SDK is async-native — sync would mean wrapping
    every call in asyncio.run(), which is ugly and doesn't scale.
  * One client instance per MCP server lifecycle, not per request — httpx
    pools connections, so reusing the client is faster and friendlier
    to GitLab's rate limiter.
  * Used as an async context manager (`async with`) so connections are
    cleanly torn down even if the server crashes mid-request.
  * Returns Pydantic models, not raw dicts — typing carries through to
    the MCP tool layer where schemas matter.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx

from gitlab_mcp.config import Settings
from gitlab_mcp.errors import (
    GitLabAuthError,
    GitLabError,
    GitLabNotFoundError,
    GitLabRateLimitError,
    GitLabServerError,
)
from gitlab_mcp.models import Issue, MergeRequest, Pipeline, Project


class GitLabClient:
    """Async client for the subset of GitLab's REST API we expose via MCP."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.gitlab_api_base,
            headers={
                "PRIVATE-TOKEN": settings.gitlab_token.get_secret_value(),
                "Accept": "application/json",
            },
            timeout=settings.request_timeout_seconds,
        )

    # ------------------------------------------------------------------
    # async context manager: `async with GitLabClient(...) as client:`
    # ------------------------------------------------------------------
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Internal request helper — every API call goes through this so that
    # error translation, headers, and (later) pagination stay in one place.
    # ------------------------------------------------------------------
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise GitLabError(f"timeout calling {path}") from exc
        except httpx.HTTPError as exc:
            raise GitLabError(f"network error calling {path}: {exc}") from exc

        if response.is_success:
            return response.json()

        # Translate HTTP errors into our typed hierarchy.
        if response.status_code in (401, 403):
            raise GitLabAuthError(
                f"GitLab rejected token (HTTP {response.status_code}): "
                f"check scope and expiry"
            )
        if response.status_code == 404:
            raise GitLabNotFoundError(f"not found: {path}")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise GitLabRateLimitError(
                "rate limited by GitLab",
                retry_after=float(retry_after) if retry_after else None,
            )
        if 500 <= response.status_code < 600:
            raise GitLabServerError(f"GitLab {response.status_code} on {path}")

        raise GitLabError(f"unexpected {response.status_code} on {path}: {response.text[:200]}")

    # ------------------------------------------------------------------
    # Public API: one method per MCP tool.
    # ------------------------------------------------------------------
    async def list_projects(self) -> list[Project]:
        """List projects accessible to the configured token.

        Uses `membership=true` so we only get projects the token's owner is
        actually a member of — not the entire universe of public projects
        on the instance.
        """
        data = await self._get("/projects", params={"membership": "true", "simple": "false"})
        return [Project.model_validate(item) for item in data]
    
    async def get_merge_requests(
        self,
        project_id: int,
        state: str = "opened",
    ) -> list[MergeRequest]:
        """List merge requests for a project, optionally filtered by state.

        Args:
            project_id: The numeric GitLab project ID.
            state: One of 'opened', 'closed', 'merged', 'locked', or 'all'.
                Defaults to 'opened' since that's the most common agent query.

        Returns:
            A list of MergeRequest models, ordered most-recently-updated first.
        """
        data = await self._get(
            f"/projects/{project_id}/merge_requests",
            params={"state": state, "order_by": "updated_at", "sort": "desc"},
        )
        return [MergeRequest.model_validate(item) for item in data]
    
    async def search_issues(
        self,
        project_id: int,
        query: str,
        state: str = "all",
    ) -> list[Issue]:
        """Search issues in a project by keyword.

        Args:
            project_id: The numeric GitLab project ID.
            query: Free-text search query — matches title and description.
            state: 'opened', 'closed', or 'all'. Defaults to 'all' so the
                agent gets every relevant issue regardless of state.

        Returns:
            Issues whose title or description contains the query, ordered
            most-recently-updated first.
        """
        data = await self._get(
            f"/projects/{project_id}/issues",
            params={
                "search": query,
                "state": state,
                "order_by": "updated_at",
                "sort": "desc",
            },
        )
        return [Issue.model_validate(item) for item in data]
    
    async def get_pipeline_status(
        self,
        project_id: int,
        limit: int = 10,
    ) -> list[Pipeline]:
        """Get the most recent pipelines for a project.

        Args:
            project_id: The numeric GitLab project ID.
            limit: How many pipelines to return (1-100, default 10).
                Capped low because the agent rarely needs deep history —
                it's almost always asking 'did the latest build pass?'.

        Returns:
            Pipelines ordered most-recent-first.
        """
        data = await self._get(
            f"/projects/{project_id}/pipelines",
            params={"per_page": str(min(max(limit, 1), 100)), "order_by": "id", "sort": "desc"},
        )
        return [Pipeline.model_validate(item) for item in data]
    
    async def _resolve_username(self, username: str) -> int:
        """Look up the numeric user ID for a username.

        GitLab's events endpoint requires the numeric ID. Agents naturally
        deal in usernames, so this helper bridges the gap. Cached at the
        GitLab side via Last-Modified so repeated lookups are cheap.
        """
        data = await self._get("/users", params={"username": username})
        if not data:
            raise GitLabNotFoundError(f"no user found with username '{username}'")
        return int(data[0]["id"])