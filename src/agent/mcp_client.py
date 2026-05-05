"""
Layer 1: MCP client adapter.

Spawns the gitlab-mcp server as a subprocess and speaks JSON-RPC to it
over stdio. Exposes a small surface (`list_tools`, `call_tool`) for the
agent loop to consume.

This module knows about MCP. It does NOT know about Anthropic, Claude,
or any LLM provider — that separation is deliberate so the same adapter
could front any MCP server, or be swapped for a different transport
(SSE, HTTP) without touching the agent loop.

Design notes:

  * Used as an async context manager so the subprocess and JSON-RPC
    session are torn down cleanly even if the agent crashes.
  * Tool calls return the raw text content of the tool result. Higher
    layers decide how to interpret it.
  * No retries here — if a tool call fails, the agent loop sees the
    error and decides whether to retry, surface it to the user, or
    let Claude handle it.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any, Self

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("agent.mcp_client")


class MCPClientAdapter:
    """Thin adapter over an MCP stdio client session.

    Usage:
        async with MCPClientAdapter(["uv", "run", "gitlab-mcp"]) as mcp:
            tools = await mcp.list_tools()
            result = await mcp.call_tool("list_projects", {})
    """

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("command must be a non-empty list, e.g. ['uv', 'run', 'gitlab-mcp']")
        self._command = command
        self._env = env
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        # AsyncExitStack lets us register multiple async-context resources
        # (the stdio transport, the session) and tear them down in reverse
        # order on exit — even if startup or use raises.
        self._exit_stack = AsyncExitStack()

        params = StdioServerParameters(
            command=self._command[0],
            args=self._command[1:],
            env=self._env,
        )
        # stdio_client gives us the read/write streams; ClientSession wraps
        # them with the MCP protocol logic.
        read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session

        logger.info("agent.mcp_client.connected command=%s", self._command)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._session = None
        self._exit_stack = None
        logger.info("agent.mcp_client.disconnected")

    @property
    def session(self) -> ClientSession:
        """Raw session — exposed for tests; agent code should use the wrapper methods."""
        if self._session is None:
            raise RuntimeError("MCPClientAdapter must be used as an async context manager")
        return self._session

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions in the shape Anthropic's API expects.

        Anthropic's `tools` parameter takes objects with name, description,
        and input_schema. We translate the MCP `Tool` type into that shape
        here so the agent loop doesn't need to know about MCP types at all.
        """
        result = await self.session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool and return its text content.

        MCP tool results are a list of content blocks (text, image, etc.).
        Our tools always return JSON-serialized text, so we extract it here
        and hand back a single string — the agent loop will pass that
        string straight back to Claude as a tool_result.

        If the tool errors, surface the error text rather than raising.
        That way Claude sees the error and can decide what to do (retry,
        explain to the user, etc.) rather than the agent crashing.
        """
        logger.info("agent.mcp_client.call name=%s args=%s", name, arguments)
        result = await self.session.call_tool(name, arguments)

        # Concatenate text blocks — for our tools there's exactly one,
        # but be defensive in case future tools return multiple.
        text_parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                text_parts.append(text)

        text = "\n".join(text_parts) if text_parts else json.dumps({"result": None})

        if result.isError:
            logger.warning("agent.mcp_client.tool_error name=%s text=%s", name, text[:200])

        return text