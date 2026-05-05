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
import logging
import time

from datetime import datetime
from gitlab_mcp.config import Settings
from gitlab_mcp.errors import (
    GitLabAuthError,
    GitLabError,
    GitLabNotFoundError,
    GitLabRateLimitError,
    GitLabServerError,
)
from gitlab_mcp.models import Issue, MergeRequest, Pipeline, Project, UserActivity

logger = logging.getLogger("gitlab_mcp.client")

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

    @staticmethod
    def _translate_error_response(response: httpx.Response, path: str) -> GitLabError:
        """Map an HTTP error response to one of our typed exceptions.

        Returning the exception (rather than raising) lets the caller decide
        whether to raise immediately or wrap with extra context.
        """
        if response.status_code in (401, 403):
            return GitLabAuthError(
                f"GitLab rejected token (HTTP {response.status_code}): "
                f"check scope and expiry"
            )
        if response.status_code == 404:
            return GitLabNotFoundError(f"not found: {path}")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            return GitLabRateLimitError(
                "rate limited by GitLab",
                retry_after=float(retry_after) if retry_after else None,
            )
        if 500 <= response.status_code < 600:
            return GitLabServerError(f"GitLab {response.status_code} on {path}")
        return GitLabError(
            f"unexpected {response.status_code} on {path}: {response.text[:200]}"
        )
    
    # ------------------------------------------------------------------
    # Internal request helper — every API call goes through this so that
    # error translation, headers, and (later) pagination stay in one place.
    # ------------------------------------------------------------------
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Perform an authenticated GET against the GitLab API.

        Every call goes through here, so this is also where we log
        request/response timing. Structured key=value pairs make the
        log lines easy to grep and easy to ship to a log aggregator
        without further parsing.
        """
        start = time.perf_counter()
        try:
            response = await self._http.get(path, params=params)
        except httpx.TimeoutException as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "gitlab.api.timeout path=%s params=%s elapsed_ms=%.0f",
                path, params, elapsed_ms,
            )
            raise GitLabError(f"timeout calling {path}") from exc
        except httpx.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "gitlab.api.network_error path=%s params=%s elapsed_ms=%.0f error=%s",
                path, params, elapsed_ms, exc,
            )
            raise GitLabError(f"network error calling {path}: {exc}") from exc

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "gitlab.api.call path=%s params=%s status=%d elapsed_ms=%.0f",
            path, params, response.status_code, elapsed_ms,
        )

        if response.is_success:
            return response.json()

        raise self._translate_error_response(response, path)
    
    async def _get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 5,
        per_page: int = 100,
    ) -> list[Any]:
        """Follow GitLab's `Link: rel="next"` pagination, capped at max_pages.

        Why bounded: if we let an LLM pull 5,000 items in one tool call,
        we waste prompt budget on data the agent will never use, and the
        Anthropic API may reject the oversized response. Default cap is
        500 items (5 pages * 100), which is enough for any realistic
        question without breaking the prompt.

        Returns a flat list of all items across pages.
        """
        params = dict(params or {})
        params.setdefault("per_page", str(per_page))

        all_items: list[Any] = []
        for page in range(1, max_pages + 1):
            params["page"] = str(page)
            # We re-implement the GET here (rather than calling _get) so we
            # can inspect the Link header, which _get discards.
            start = time.perf_counter()
            response = await self._http.get(path, params=params)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "gitlab.api.call path=%s page=%d status=%d elapsed_ms=%.0f",
                path, page, response.status_code, elapsed_ms,
            )

            if not response.is_success:
                raise self._translate_error_response(response, path)
            
            page_items = response.json()
            all_items.extend(page_items)

            # Check Link header for rel="next". If absent, we're done.
            link_header = response.headers.get("Link", "")
            if 'rel="next"' not in link_header:
                break

        return all_items

    # ------------------------------------------------------------------
    # Public API: one method per MCP tool.
    # ------------------------------------------------------------------
    async def list_projects(self) -> list[Project]:
        """List projects accessible to the configured token.

        Uses `membership=true` so we only get projects the token's owner is
        actually a member of — not the entire universe of public projects
        on the instance. Paginated up to 5 pages (500 projects).

        Filters out pending-deletion projects: GitLab.com soft-deletes for
        30 days and continues returning them in the API with a renamed path
        (`*-deletion_scheduled-NNNN`). Since the agent can't actually do
        anything with these and they pollute the prompt, we drop them here.
        """
        data = await self._get_paginated(
            "/projects",
            params={"membership": "true", "simple": "false"},
        )
        return [
            Project.model_validate(item)
            for item in data
            if "deletion_scheduled" not in item.get("path", "")
        ]
    
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
    
    async def get_user_activity(
        self,
        username: str,
        since: datetime,
        max_events: int = 100,
    ) -> UserActivity:
        """Summarize a user's recent activity.

        Performs a username -> user_id lookup, then aggregates events
        from /users/{id}/events into category counts. The agent gets
        a small structured summary instead of a fire-hose of raw events.

        Args:
            username: GitLab username (case-sensitive).
            since: Lower bound for event recency (ISO 8601 date in UTC).
            max_events: Cap on raw events fetched. GitLab paginates events;
                we don't want to download a year of activity for a chatty user.

        Returns:
            A UserActivity summary.
        """
        user_id = await self._resolve_username(username)
        raw_events = await self._get(
            f"/users/{user_id}/events",
            params={
                "after": since.date().isoformat(),
                "per_page": str(min(max(max_events, 1), 100)),
            },
        )
        return self._aggregate_events(
            username=username,
            user_id=user_id,
            since=since,
            raw_events=raw_events,
        )

    @staticmethod
    def _aggregate_events(
        username: str,
        user_id: int,
        since: datetime,
        raw_events: list[dict[str, Any]],
    ) -> UserActivity:
        """Reduce a raw event stream into a UserActivity summary.

        Pure function — no I/O, easy to unit-test in isolation if we ever
        need to. Kept as a staticmethod inside the class to keep the
        related code together.
        """
        pushes = 0
        mrs_opened = 0
        issues_opened = 0
        comments = 0
        titles: list[str] = []

        for event in raw_events:
            action = event.get("action_name", "")
            target_type = event.get("target_type") or ""
            title = event.get("target_title")

            if action == "pushed to":
                pushes += 1
            elif action == "opened" and target_type == "MergeRequest":
                mrs_opened += 1
                if title:
                    titles.append(title)
            elif action == "opened" and target_type == "Issue":
                issues_opened += 1
                if title:
                    titles.append(title)
            elif action == "commented on":
                comments += 1
                if title:
                    titles.append(title)
            # All other action_names (joined, accepted, closed, etc.) are
            # counted toward total_events but not categorized — by design.

        return UserActivity(
            username=username,
            user_id=user_id,
            since=since,
            total_events=len(raw_events),
            pushes=pushes,
            merge_requests_opened=mrs_opened,
            issues_opened=issues_opened,
            comments=comments,
            recent_event_titles=titles[:10],  # cap for prompt budget
        )