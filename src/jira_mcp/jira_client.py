"""Async Jira Cloud REST client — read-only, used by the MCP server.

Mirrors the design of gitlab_mcp.gitlab_client:

  - Async httpx with one shared client per process lifetime.
  - Structured key=value logging at the API boundary.
  - One central place that translates HTTP status to typed exceptions
    (_translate_error_response), called by every method.
  - Tool-shaped methods (list_projects, search_issues) that return
    Pydantic models, not raw dicts — bounded, type-safe outputs.

Differences from the GitLab side:

  - HTTP Basic auth (email + API token) instead of bearer header.
  - JQL query construction for search_issues.
  - Restricted 'fields' parameter on /search so Jira doesn't ship us
    the giant ADF description blob on every issue.
"""

from __future__ import annotations

import logging
import time
from types import TracebackType
from typing import Any, Self

import httpx

from gitlab_mcp.config import Settings
from jira_mcp.errors import (
    JiraAuthError,
    JiraError,
    JiraNotFoundError,
    JiraRateLimitError,
    JiraServerError,
)
from jira_mcp.models import Issue, Project


logger = logging.getLogger("jira_mcp.client")


# Bounded by default — same reasoning as gitlab_mcp pagination cap.
_MAX_RESULTS = 100


# Fields we always ask Jira for. Crucially excludes 'description' (ADF
# JSON, large, low signal) — see DESIGN.md on bounded outputs for LLMs.
_ISSUE_FIELDS = "summary,status,issuetype"


class JiraClient:
    """Read-only Jira Cloud client.

    Use as an async context manager:

        async with JiraClient(settings) as client:
            projects = await client.list_projects()
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.jira_url or not settings.jira_email or not settings.jira_token:
            raise JiraError(
                "JIRA_URL, JIRA_EMAIL, and JIRA_TOKEN must all be set "
                "in .env to use JiraClient."
            )
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            base_url=str(self._settings.jira_url).rstrip("/"),
            auth=(
                self._settings.jira_email,
                self._settings.jira_token.get_secret_value(),
            ),
            headers={"Accept": "application/json"},
            timeout=self._settings.request_timeout_seconds,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ----- low-level GET --------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET; translate non-2xx into typed JiraError subclasses."""
        if self._client is None:
            raise JiraError(
                "JiraClient used outside of 'async with' — call within the context manager."
            )

        start = time.perf_counter()
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException:
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(
                "jira.api.timeout path=%s elapsed_ms=%.0f", path, elapsed
            )
            raise JiraServerError(f"Jira timeout after {elapsed:.0f}ms on {path}")

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "jira.api.call path=%s status=%d elapsed_ms=%.0f",
            path,
            response.status_code,
            elapsed,
        )

        if response.status_code >= 400:
            self._translate_error_response(response)

        return response.json()

    @staticmethod
    def _translate_error_response(response: httpx.Response) -> None:
        """Single point of truth for HTTP -> typed exception mapping.

        Same pattern as GitLabClient._translate_error_response: if Jira
        ever adds a new error class (or we add a new error subtype), the
        one-place rule means there's exactly one site to update.
        """
        status = response.status_code
        path = response.request.url.path
        if status in (401, 403):
            raise JiraAuthError(
                f"Jira auth failed ({status}) on {path} — check JIRA_EMAIL and JIRA_TOKEN."
            )
        if status == 404:
            raise JiraNotFoundError(f"Jira: not found: {path}")
        if status == 429:
            raise JiraRateLimitError(f"Jira rate-limited ({status}) on {path}")
        if status >= 500:
            raise JiraServerError(f"Jira upstream error ({status}) on {path}")
        # Anything else 4xx that we haven't classified: surface as generic.
        raise JiraError(
            f"Jira error ({status}) on {path}: {response.text[:200]}"
        )

    # ----- tool surface ---------------------------------------------------

    async def list_projects(self) -> list[Project]:
        """List all projects the configured account can see.

        Mirrors the role of GitLabClient.list_projects: the entry point
        every other tool depends on for resolving a project key/id.
        """
        data = await self._get("/rest/api/3/project/search")
        return [Project.model_validate(p) for p in data.get("values", [])]

    async def search_issues(
        self,
        project_key: str,
        query: str,
    ) -> list[Issue]:
        """Search issues in a project by free-text keyword.

        Builds JQL on the caller's behalf — the LLM doesn't need to know
        JQL syntax, just the project key and the keyword.
        """
        # JQL: scope by project, full-text match on summary+description+comments.
        # The text~"..." operator is Jira's full-text search; project="X"
        # restricts the scope.
        escaped_project = project_key.replace('"', '\\"')
        escaped_query = query.replace('"', '\\"')
        jql_parts = [f'project = "{escaped_project}"']
        if query.strip():
            jql_parts.append(f'text ~ "{escaped_query}"')
        jql = " AND ".join(jql_parts)

        data = await self._get(
            "/rest/api/3/search/jql",
            params={
                "jql": jql,
                "fields": _ISSUE_FIELDS,
                "maxResults": _MAX_RESULTS,
            },
        )

        issues: list[Issue] = []
        for raw in data.get("issues", []):
            fields = raw.get("fields", {})
            issues.append(
                Issue.model_validate(
                    {
                        "id": raw["id"],
                        "key": raw["key"],
                        "summary": fields.get("summary", ""),
                        "status": fields.get("status", {}).get("name", "Unknown"),
                        "issue_type": fields.get("issuetype", {}).get("name", "Unknown"),
                    }
                )
            )
        return issues