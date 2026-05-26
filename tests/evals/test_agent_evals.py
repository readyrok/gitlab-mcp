"""
Behavioral evaluations of the agent against real Claude + real GitLab.

Gated behind `pytest.mark.evals` and `--ignore=tests/evals` in the
default addopts. To run:

    uv run pytest -m evals --no-cov tests/evals

Or a single eval:

    uv run pytest -m evals --no-cov tests/evals -k <name>

Each eval drives the real agent loop, captures events, and asserts on
tool-call patterns plus content presence in the final answer. We
deliberately don't assert exact transcripts — Claude is non-deterministic
and a good agent reaches the same answer via several valid paths. We
assert *behaviors*, not transcripts.
"""

from __future__ import annotations

import pytest

from agent.loop import AgentLoop, TextEvent, ToolCallEvent

from tests.evals.conftest import live_agent


async def _run(agent: AgentLoop, question: str) -> tuple[list[ToolCallEvent], str]:
    """Drive one question, return (tool calls made, full answer text)."""
    tool_calls: list[ToolCallEvent] = []
    text_chunks: list[str] = []
    async for event in agent.ask(question):
        if isinstance(event, ToolCallEvent):
            tool_calls.append(event)
        elif isinstance(event, TextEvent):
            text_chunks.append(event.text)
    return tool_calls, " ".join(text_chunks)


@pytest.mark.evals
async def test_eval_smoke() -> None:
    """Simplest eval: agent answers a trivial question.

    Verifies the harness works before writing real scenarios.
    """
    async with live_agent() as agent:
        tool_calls, answer = await _run(agent, "how many projects do I have?")

    assert any(tc.name == "list_projects" for tc in tool_calls), (
        f"expected list_projects to be called, got: {[tc.name for tc in tool_calls]}"
    )
    answer_lower = answer.lower()
    assert "3" in answer_lower or "three" in answer_lower, (
        f"expected mention of '3' in answer, got: {answer[:200]}"
    )