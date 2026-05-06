"""
Tests for AgentLoop.

We mock Anthropic and MCPClientAdapter rather than hit real services:
  * Anthropic costs money per call
  * MCP adapter spawns subprocesses (slow, fragile)
  * We want to test orchestration logic, not the dependencies

The mocks are tiny purpose-built fakes, not a mocking library — easier
to read in 6 months than dense `mock.patch` decorators would be.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.loop import (
    AgentLoop,
    ErrorEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from gitlab_mcp.config import Settings


# ----- Fake Anthropic response shapes ---------------------------------------

@dataclass
class _FakeBlock:
    """Stand-in for an Anthropic content block (text or tool_use)."""
    type: str
    text: str | None = None
    name: str | None = None
    id: str | None = None
    input: dict | None = None


@dataclass
class _FakeResponse:
    """Stand-in for an Anthropic Message response."""
    content: list[_FakeBlock]
    stop_reason: str  # "end_turn" or "tool_use"


@dataclass
class _FakeMessages:
    """Stand-in for client.messages — returns scripted responses in sequence."""
    scripted: list[_FakeResponse]
    calls_received: list[dict] = field(default_factory=list)
    _next: int = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls_received.append(kwargs)
        if self._next >= len(self.scripted):
            # Default to end_turn so a misconfigured test doesn't hang.
            return _FakeResponse(
                content=[_FakeBlock(type="text", text="(unscripted response)")],
                stop_reason="end_turn",
            )
        resp = self.scripted[self._next]
        self._next += 1
        return resp


@dataclass
class _FakeAnthropicClient:
    messages: _FakeMessages


# ----- Fake MCP adapter -----------------------------------------------------

@dataclass
class _FakeMCPAdapter:
    """Stand-in for MCPClientAdapter. Returns canned tool results."""
    tools: list[dict]
    canned_results: dict[str, str] = field(default_factory=dict)
    calls_received: list[tuple[str, dict]] = field(default_factory=list)

    async def list_tools(self) -> list[dict]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.calls_received.append((name, arguments))
        return self.canned_results.get(name, '{"result": []}')


# ----- Helpers --------------------------------------------------------------

def _make_settings() -> Settings:
    return Settings(
        gitlab_url="https://gitlab.example.com",  # type: ignore[arg-type]
        gitlab_token="test-token",  # type: ignore[arg-type]
        anthropic_api_key="test-anthropic-key",  # type: ignore[arg-type]
        agent_max_iterations=5,
    )


def _make_loop(
    scripted_responses: list[_FakeResponse],
    tools: list[dict] | None = None,
    canned_tool_results: dict[str, str] | None = None,
) -> tuple[AgentLoop, _FakeAnthropicClient, _FakeMCPAdapter]:
    """Construct an AgentLoop with both dependencies mocked."""
    fake_anthropic = _FakeAnthropicClient(
        messages=_FakeMessages(scripted=scripted_responses)
    )
    fake_mcp = _FakeMCPAdapter(
        tools=tools or [{"name": "list_projects", "description": "...", "input_schema": {}}],
        canned_results=canned_tool_results or {},
    )
    loop = AgentLoop(settings=_make_settings(), mcp=fake_mcp)  # type: ignore[arg-type]
    # Bypass the real Anthropic client construction: __post_init__ already
    # built one, but we replace it with our fake here.
    loop._client = fake_anthropic  # type: ignore[assignment]
    return loop, fake_anthropic, fake_mcp


async def _collect(events: AsyncIterator) -> list:
    return [e async for e in events]


# ----- Tests ----------------------------------------------------------------

async def test_loop_handles_single_tool_call_then_text() -> None:
    """Most common flow: one tool call, then a text answer."""
    scripted = [
        # Iteration 1: Claude asks to call list_projects.
        _FakeResponse(
            content=[
                _FakeBlock(type="text", text="Let me check."),
                _FakeBlock(
                    type="tool_use",
                    id="tu_1",
                    name="list_projects",
                    input={},
                ),
            ],
            stop_reason="tool_use",
        ),
        # Iteration 2: Claude returns a final answer.
        _FakeResponse(
            content=[_FakeBlock(type="text", text="You have 3 projects.")],
            stop_reason="end_turn",
        ),
    ]
    loop, _anthropic, mcp = _make_loop(
        scripted,
        canned_tool_results={"list_projects": '{"result": [{"id": 1}]}'},
    )

    events = await _collect(loop.ask("what projects do i have?"))

    # Order: text -> tool_use -> tool_result -> text
    types = [type(e).__name__ for e in events]
    assert types == ["TextEvent", "ToolCallEvent", "ToolResultEvent", "TextEvent"]

    tool_call = events[1]
    assert isinstance(tool_call, ToolCallEvent)
    assert tool_call.name == "list_projects"

    tool_result = events[2]
    assert isinstance(tool_result, ToolResultEvent)
    assert not tool_result.is_error

    final_text = events[3]
    assert isinstance(final_text, TextEvent)
    assert "3 projects" in final_text.text

    # MCP was called exactly once with the right tool.
    assert mcp.calls_received == [("list_projects", {})]


async def test_loop_handles_pure_text_answer_without_tools() -> None:
    """Follow-up questions that need no tools should exit after one round-trip."""
    scripted = [
        _FakeResponse(
            content=[_FakeBlock(type="text", text="As I said, you have 3 projects.")],
            stop_reason="end_turn",
        ),
    ]
    loop, anthropic, mcp = _make_loop(scripted)

    events = await _collect(loop.ask("how many projects again?"))

    assert len(events) == 1
    assert isinstance(events[0], TextEvent)
    assert mcp.calls_received == []  # no tools called
    assert len(anthropic.messages.calls_received) == 1  # one round trip


async def test_loop_yields_error_event_at_iteration_cap() -> None:
    """Pathological case: Claude keeps asking for tools forever."""
    # Scripted responses: every iteration says 'tool_use' and never end_turn.
    # We need at least agent_max_iterations + 1 of these so the cap actually fires.
    scripted = [
        _FakeResponse(
            content=[
                _FakeBlock(type="tool_use", id=f"tu_{i}", name="list_projects", input={}),
            ],
            stop_reason="tool_use",
        )
        for i in range(20)
    ]
    loop, _anthropic, _mcp = _make_loop(
        scripted,
        canned_tool_results={"list_projects": '{"result": []}'},
    )

    events = await _collect(loop.ask("loop forever"))

    # Exactly one ErrorEvent at the end.
    assert isinstance(events[-1], ErrorEvent)
    assert "iteration safety cap" in events[-1].message
    # And we should see exactly agent_max_iterations (5) tool calls before it.
    tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(tool_calls) == 5