"""
MCP server entry point.

Wraps GitLabClient as a set of MCP tools using FastMCP.

Lifecycle: the GitLabClient is created once when the server starts and
torn down when it stops. FastMCP's `lifespan` context manager handles
this — same pattern as ASGI lifespans, FastAPI, etc.

Tools share the single client via the lifespan context. This is faster
(connection pooling) and cleaner than creating a client per tool call.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from mcp.server.fastmcp import Context, FastMCP

from gitlab_mcp.config import get_settings
from gitlab_mcp.gitlab_client import GitLabClient

logger = logging.getLogger("gitlab_mcp.server")


@dataclass
class ServerContext:
    """Lifespan-scoped state shared by every tool call.

    FastMCP injects this into tool functions that ask for the request
    context. Keeps the GitLabClient out of module-level globals — easier
    to reason about and easier to swap out in tests.
    """

    gitlab: GitLabClient


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[ServerContext]:
    """Create the GitLabClient on startup, close it cleanly on shutdown."""
    settings = get_settings()
    logger.info("server.starting gitlab_url=%s", settings.gitlab_url)

    async with GitLabClient(settings) as client:
        yield ServerContext(gitlab=client)

    logger.info("server.stopped")


# The server instance itself. Tools are registered against it via @mcp.tool()
# in the next step.
mcp = FastMCP(
    name="gitlab-mcp",
    instructions=(
        "Read-only access to GitLab. Use these tools to answer questions "
        "about projects, merge requests, issues, pipelines, and user activity."
    ),
    lifespan=lifespan,
)

@mcp.tool(
    description=(
        "List all GitLab projects the configured user belongs to.\n\n"
        "Use this when the user asks what projects/repos/codebases exist, "
        "wants an overview of what's available, or before drilling into "
        "any specific project. Returns each project's id (needed for other "
        "tools), name, namespace path, default branch, and last-activity "
        "timestamp.\n\n"
        "Most other tools require a project_id, so this is typically the "
        "first tool to call when the user names a project ambiguously "
        "(e.g. 'the order service' rather than giving an id)."
    )
)

async def list_projects(ctx: Context) -> list[dict]:
    """Tool implementation: list accessible projects."""
    server_ctx: ServerContext = ctx.request_context.lifespan_context
    projects = await server_ctx.gitlab.list_projects()
    # Convert Pydantic models to dicts so the JSON shape is predictable
    # for the LLM. mode='json' serializes datetimes to ISO 8601 strings.
    return [p.model_dump(mode="json") for p in projects]

@mcp.tool(
    description=(
        "List merge requests in a GitLab project, filtered by state.\n\n"
        "Use this when the user asks about open MRs / pull requests, wants "
        "to see what's waiting for review, or asks 'what's blocked' on a "
        "project. Each MR includes title, state (opened/closed/merged), "
        "draft flag, author, branches, and timestamps.\n\n"
        "Args:\n"
        "  project_id: numeric GitLab project id (get from list_projects).\n"
        "  state: one of 'opened' (default), 'closed', 'merged', 'locked', "
        "or 'all'. Default 'opened' covers the most common question "
        "('what's pending?')."
    )
)
async def get_merge_requests(
    ctx: Context,
    project_id: int,
    state: str = "opened",
) -> list[dict]:
    server_ctx: ServerContext = ctx.request_context.lifespan_context
    mrs = await server_ctx.gitlab.get_merge_requests(project_id=project_id, state=state)
    return [m.model_dump(mode="json") for m in mrs]


@mcp.tool(
    description=(
        "Search issues in a GitLab project by keyword.\n\n"
        "Use this when the user asks about bugs, feature requests, or any "
        "work items in a project — and wants to filter by topic. Examples: "
        "'are there any open performance issues?', 'find bugs related to "
        "payments'.\n\n"
        "For 'show me ALL issues' (no keyword), pass an empty query string.\n\n"
        "Args:\n"
        "  project_id: numeric GitLab project id (get from list_projects).\n"
        "  query: free-text search; matches title and description.\n"
        "  state: 'opened', 'closed', or 'all' (default 'all')."
    )
)
async def search_issues(
    ctx: Context,
    project_id: int,
    query: str,
    state: str = "all",
) -> list[dict]:
    server_ctx: ServerContext = ctx.request_context.lifespan_context
    issues = await server_ctx.gitlab.search_issues(
        project_id=project_id, query=query, state=state
    )
    return [i.model_dump(mode="json") for i in issues]


@mcp.tool(
    description=(
        "Get the most recent CI pipelines for a GitLab project.\n\n"
        "Use this when the user asks 'did the build pass?', 'is CI green?', "
        "'what broke the pipeline?', or wants to see recent pipeline "
        "history. Returns each pipeline's status (success/failed/running/"
        "pending/canceled), ref (branch), sha, duration, and web url.\n\n"
        "Pipelines are returned newest-first. The first result is almost "
        "always what 'did it pass?' refers to.\n\n"
        "Args:\n"
        "  project_id: numeric GitLab project id (get from list_projects).\n"
        "  limit: how many recent pipelines to return (1-100, default 10)."
    )
)
async def get_pipeline_status(
    ctx: Context,
    project_id: int,
    limit: int = 10,
) -> list[dict]:
    server_ctx: ServerContext = ctx.request_context.lifespan_context
    pipelines = await server_ctx.gitlab.get_pipeline_status(
        project_id=project_id, limit=limit
    )
    return [p.model_dump(mode="json") for p in pipelines]


@mcp.tool(
    description=(
        "Summarize a user's recent activity across GitLab.\n\n"
        "Use this when the user asks what someone has been working on, "
        "wants a status update on a colleague, or asks for standup-style "
        "summaries. Returns a structured summary: counts of pushes / merge "
        "requests opened / issues opened / comments, plus titles of "
        "headline activity.\n\n"
        "This is NOT a list of every event — it's an aggregated summary "
        "designed for an agent to reason about. For raw event history, "
        "the user should go to the GitLab UI.\n\n"
        "Args:\n"
        "  username: GitLab username (case-sensitive).\n"
        "  since: ISO date string (YYYY-MM-DD) for the lower bound. "
        "Default is 7 days ago if omitted."
    )
)
async def get_user_activity(
    ctx: Context,
    username: str,
    since: str | None = None,
) -> dict:
    server_ctx: ServerContext = ctx.request_context.lifespan_context
    if since:
        since_dt = datetime.fromisoformat(since).replace(tzinfo=UTC)
    else:
        since_dt = datetime.now(UTC) - timedelta(days=7)
    activity = await server_ctx.gitlab.get_user_activity(
        username=username, since=since_dt
    )
    return activity.model_dump(mode="json")

def main() -> None:
    """Console-script entrypoint, registered in pyproject.toml as `gitlab-mcp`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    # `stdio` is the default MCP transport — server reads JSON-RPC from
    # stdin and writes to stdout. The MCP Inspector and most agents use it.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()