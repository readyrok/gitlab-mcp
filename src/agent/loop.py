"""
Layer 2: agentic loop.

Owns:
  * an Anthropic client
  * an MCPClientAdapter
  * a conversation history (list of messages)
  * a system prompt

Exposes a single async generator method, `ask(question)`, that yields
events as the loop progresses. The CLI consumes those events and prints
them in real time — that's what makes the demo feel alive instead of
hanging silently for 10 seconds while Claude thinks.

Why an async generator and not a callback or final-only return:
  * Streaming UX without complicated callback plumbing.
  * Clean separation: this layer says *what* happens, the CLI decides
    *how* to display it. Tomorrow if we add a TUI or a web frontend,
    only the CLI changes.

The system prompt is intentionally short. Lots of agent tutorials lean
on long prompts to compensate for unclear tool descriptions; the inverse
is the better discipline: invest in tool descriptions, keep the system
prompt minimal, let Claude trust the tools.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import time

import anthropic

from agent.mcp_client import MCPClientAdapter
from gitlab_mcp.config import Settings

logger = logging.getLogger("agent.loop")


SYSTEM_PROMPT = """\
You are a developer-productivity assistant with access to a GitLab \
workspace via tools. You help engineers and managers answer questions \
about projects, merge requests, issues, CI pipelines, and team activity.

When a question requires GitLab data, call the appropriate tool rather \
than guessing. Most questions about specific projects need a project_id, \
which you can get from list_projects.

**Match the response length to what was asked.** If the user asks for \
"just names" or "a quick summary", give exactly that — don't pad with \
descriptions and timestamps. If the user asks for details, then go \
deep.

Be concise by default. Prefer bullet points or short paragraphs. When \
you summarize multiple items, name them — don't just give counts. If a \
tool returns an error, explain it plainly to the user and suggest what \
they could try.

You can call multiple tools in sequence. Plan your approach, execute \
it, then summarize the result.\
"""


# ----- Events the loop yields ------------------------------------------------

@dataclass
class TextEvent:
    """A piece of natural-language text from Claude."""
    text: str


@dataclass
class ToolCallEvent:
    """Claude requested a tool call."""
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    """The result of a tool call (after we executed it)."""
    name: str
    result_preview: str  # first ~200 chars, for display
    is_error: bool


@dataclass
class ErrorEvent:
    """Something went wrong inside the loop (e.g. iteration cap hit)."""
    message: str

@dataclass
class UsageEvent:
    """Emitted once at the end of an ask() call — the cost/latency trace.

    This is the observability hook: every question produces a measurable
    record of what it cost in tokens, API round-trips, tool calls, and
    wall-clock time. At scale, this data is how you answer 'is the agent
    expensive?' and 'why is it slow?'.
    """
    input_tokens: int
    output_tokens: int
    api_round_trips: int
    tool_calls: int
    latency_seconds: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

AgentEvent = TextEvent | ToolCallEvent | ToolResultEvent | ErrorEvent | UsageEvent


# ----- The loop itself -------------------------------------------------------

@dataclass
class AgentLoop:
    """Orchestrates Anthropic + the MCP server.

    Maintains conversation history across calls to `ask()`, so the REPL
    can have natural follow-up turns ("and what about issues?") that
    refer back to previous tool results.
    """

    settings: Settings
    mcp: MCPClientAdapter
    _client: anthropic.AsyncAnthropic = field(init=False)
    _history: list[dict[str, Any]] = field(default_factory=list, init=False)
    _tools: list[dict[str, Any]] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.settings.anthropic_api_key is None:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Add it to .env to run the agent."
            )
        self._client = anthropic.AsyncAnthropic(
            api_key=self.settings.anthropic_api_key.get_secret_value(),
        )

    async def _ensure_tools_loaded(self) -> list[dict[str, Any]]:
        """Lazy-load the tool list from the MCP server on first use."""
        if self._tools is None:
            self._tools = await self.mcp.list_tools()
            logger.info("agent.loop.tools_loaded count=%d", len(self._tools))
        return self._tools

    async def ask(self, question: str) -> AsyncIterator[AgentEvent]:
        """Send a question through the agentic loop, yielding events as they happen.

        Yields a sequence of TextEvent / ToolCallEvent / ToolResultEvent / ErrorEvent.
        Conversation history is preserved across calls so follow-up questions work.
        """
        tools = await self._ensure_tools_loaded()

        # Append the user message to the persistent history.
        self._history.append({"role": "user", "content": question})

        # Append the user message to the persistent history.
        self._history.append({"role": "user", "content": question})

        # Observability counters for this question.
        start_time = time.perf_counter()
        total_input_tokens = 0
        total_output_tokens = 0
        api_round_trips = 0
        tool_call_count = 0

        for iteration in range(self.settings.agent_max_iterations):
            logger.info("agent.loop.iteration n=%d history_len=%d", iteration + 1, len(self._history))

            response = await self._client.messages.create(
                model=self.settings.anthropic_model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=self._history,
            )

            api_round_trips += 1
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # The response.content is a list of blocks: text, tool_use, etc.
            # Append the *whole* assistant response to history before processing —
            # Anthropic requires the prior assistant turn to be intact when we
            # send tool_result back.
            self._history.append({"role": "assistant", "content": response.content})

            # Surface any text the assistant produced.
            for block in response.content:
                if block.type == "text" and block.text:
                    yield TextEvent(text=block.text)

            # If Claude didn't ask for any tools, we're done.
            # If Claude didn't ask for any tools, we're done.
            if response.stop_reason != "tool_use":
                yield UsageEvent(
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    api_round_trips=api_round_trips,
                    tool_calls=tool_call_count,
                    latency_seconds=time.perf_counter() - start_time,
                )
                return

            # Otherwise, execute every tool_use block in order, collecting
            # their results into a single user message (Anthropic's convention).
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                yield ToolCallEvent(name=block.name, arguments=dict(block.input))

                tool_call_count += 1

                try:
                    result_text = await self.mcp.call_tool(
                        block.name, dict(block.input)
                    )
                    is_error = False
                except Exception as exc:
                    # Hard failure (transport down, etc.). Hand the error to
                    # Claude as a tool result so it can recover gracefully.
                    result_text = json.dumps({"error": str(exc)})
                    is_error = True
                    logger.exception("agent.loop.tool_exception name=%s", block.name)

                yield ToolResultEvent(
                    name=block.name,
                    result_preview=result_text[:200],
                    is_error=is_error,
                )

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                    "is_error": is_error,
                })

            self._history.append({"role": "user", "content": tool_results})

        # If we fell out of the for loop, we hit the iteration cap.
        yield UsageEvent(
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            api_round_trips=api_round_trips,
            tool_calls=tool_call_count,
            latency_seconds=time.perf_counter() - start_time,
        )
        
        yield ErrorEvent(
            message=(
                f"Hit the {self.settings.agent_max_iterations}-iteration safety cap. "
                "This usually means a tool keeps erroring or the model is stuck in a "
                "loop. Try rephrasing the question or check the logs."
            )
        )

    def reset(self) -> None:
        """Clear conversation history. Useful between distinct sessions."""
        self._history = []
        logger.info("agent.loop.history_reset")