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

from mcp.server.fastmcp import FastMCP

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