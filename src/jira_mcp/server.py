"""Jira MCP server — exposes a read-only Jira connector as 2 tools.

Sibling of gitlab_mcp.server. Same structure:
  - FastMCP instance with a lifespan that creates one shared JiraClient
  - a ServerContext dataclass holding that client
  - @mcp.tool() registrations whose descriptions are the LLM-facing
    contract for when/how to call each tool
  - tools return model_dump(mode="json") dicts; stdio transport

The deliberately small surface (2 tools) is the point: it proves the
agent architecture generalizes to a second backend without the agent
changing at all. Run the agent against it with:

    uv run agent --server-command "uv run jira-mcp"
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import Context, FastMCP

from gitlab_mcp.config import get_settings
from jira_mcp.jira_client import JiraClient

logger = logging.getLogger("jira_mcp.server")


@dataclass
class ServerContext:
    """Shared state available to every tool via the lifespan context."""

    jira: JiraClient


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[ServerContext]:
    """Create one JiraClient for the server's lifetime."""
    settings = get_settings()
    logger.info("jira.server.starting jira_url=%s", settings.jira_url)
    async with JiraClient(settings) as jira:
        yield ServerContext(jira=jira)
    logger.info("jira.server.stopped")


mcp = FastMCP("jira-mcp", lifespan=_lifespan)


@mcp.tool(
    description=(
        "List all Jira projects the configured account can access.\n\n"
        "Returns each project's key (e.g. 'SCRUM', 'AM'), numeric id, and "
        "name. Call this FIRST whenever you need a project key for "
        "search_issues — never guess a project key. Most questions about "
        "a specific project start here to resolve its key.\n\n"
        "Examples: 'what projects are there?', 'list my Jira projects'."
    )
)
async def list_projects(ctx: Context) -> list[dict]:
    server_ctx: ServerContext = ctx.request_context.lifespan_context
    projects = await server_ctx.jira.list_projects()
    return [p.model_dump(mode="json") for p in projects]


@mcp.tool(
    description=(
        "Search issues within a Jira project by free-text keyword.\n\n"
        "Use this for questions about bugs, tasks, stories, or any work "
        "items in a project. Returns each matching issue's key (e.g. "
        "'SCRUM-12'), summary, status, and type.\n\n"
        "IMPORTANT: project_key is a short alphanumeric Jira project key "
        "like 'SCRUM' or 'AM' — NOT a numeric id, and NOT a guess. Obtain "
        "it by calling list_projects first if you don't already know it.\n\n"
        "For all issues in a project, pass an empty string as the query.\n\n"
        "Examples: 'find login bugs in the mobile project', 'what crash "
        "issues are there?', 'show all issues in SCRUM'.\n\n"
        "Args:\n"
        "  project_key: short Jira project key (obtain via list_projects).\n"
        "  query: free-text keyword; matches summary, description, comments. "
        "Pass '' for all issues."
    )
)
async def search_issues(ctx: Context, project_key: str, query: str = "") -> list[dict]:
    server_ctx: ServerContext = ctx.request_context.lifespan_context
    issues = await server_ctx.jira.search_issues(project_key=project_key, query=query)
    return [i.model_dump(mode="json") for i in issues]


def main() -> None:
    """Console-script entry point — runs the server over stdio."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()