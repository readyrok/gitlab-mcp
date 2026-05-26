"""
Behavioral evaluations of the agent against real Claude + real GitLab.

Gated behind `pytest.mark.evals` and `--ignore=tests/evals` in default
addopts. To run the whole suite:

    uv run pytest -m evals --no-cov tests/evals

A single scenario:

    uv run pytest -m evals --no-cov tests/evals -k comparative_question

Each eval drives the real agent loop, captures events, and checks the
scenario's tool-call and answer-content expectations. We assert
behaviors, not transcripts — see eval_scenarios.py for the rationale.
"""

from __future__ import annotations

import pytest

from agent.loop import AgentLoop, TextEvent, ToolCallEvent

from tests.evals.conftest import live_agent
from tests.evals.eval_scenarios import SCENARIOS, Scenario


async def _run(agent: AgentLoop, question: str) -> tuple[list[str], str]:
    """Drive one question; return (tool names called, full answer text)."""
    tool_names: list[str] = []
    text_chunks: list[str] = []
    async for event in agent.ask(question):
        if isinstance(event, ToolCallEvent):
            tool_names.append(event.name)
        elif isinstance(event, TextEvent):
            text_chunks.append(event.text)
    return tool_names, " ".join(text_chunks)


def _check_scenario(
    scenario: Scenario,
    tool_names: list[str],
    answer: str,
) -> list[str]:
    """Return a list of failure messages — empty list means the scenario passed."""
    failures: list[str] = []
    answer_lower = answer.lower()

    for tool in scenario.must_call_tools:
        if tool not in tool_names:
            failures.append(
                f"expected tool '{tool}' to be called; calls were {tool_names}"
            )

    for tool in scenario.must_not_call_tools:
        if tool in tool_names:
            failures.append(
                f"tool '{tool}' should NOT have been called; calls were {tool_names}"
            )

    if scenario.answer_any_of:
        if not any(s.lower() in answer_lower for s in scenario.answer_any_of):
            failures.append(
                f"answer contained none of {scenario.answer_any_of}; "
                f"answer was: {answer[:300]}"
            )

    for needed in scenario.answer_all_of:
        if needed.lower() not in answer_lower:
            failures.append(
                f"answer missing required substring '{needed}'; "
                f"answer was: {answer[:300]}"
            )

    return failures


@pytest.mark.evals
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_scenario(scenario: Scenario) -> None:
    """Run one eval scenario end-to-end against the real agent."""
    async with live_agent() as agent:
        tool_names, answer = await _run(agent, scenario.question)

    failures = _check_scenario(scenario, tool_names, answer)
    assert not failures, (
        f"\nScenario '{scenario.name}' failed:\n"
        f"  rationale: {scenario.rationale}\n"
        f"  question:  {scenario.question}\n"
        + "".join(f"  - {f}\n" for f in failures)
    )