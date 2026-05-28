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
from tests.evals.gitlab_scenarios import SCENARIOS, Scenario, check_scenario
from tests.evals.jira_scenarios import JIRA_SCENARIOS


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

@pytest.mark.evals
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
async def test_gitlab_scenario(scenario: Scenario) -> None:
    """Run one eval scenario end-to-end against the real agent."""
    async with live_agent() as agent:
        tool_names, answer = await _run(agent, scenario.question)

    failures = check_scenario(scenario, tool_names, answer)
    assert not failures, (
        f"\nScenario '{scenario.name}' failed:\n"
        f"  rationale: {scenario.rationale}\n"
        f"  question:  {scenario.question}\n"
        + "".join(f"  - {f}\n" for f in failures)
    )

@pytest.mark.evals
@pytest.mark.parametrize("scenario", JIRA_SCENARIOS, ids=lambda s: s.name)
async def test_jira_scenario(scenario: Scenario) -> None:
    """Run one Jira eval scenario end-to-end against the real Jira-backed agent.

    Distinct from test_scenario because it points the agent at the
    jira-mcp server instead of gitlab-mcp. Same agent, different
    server — the architecture's whole point.
    """
    async with live_agent(server_command=["uv", "run", "jira-mcp"]) as agent:
        tool_names, answer = await _run(agent, scenario.question)

    failures = check_scenario(scenario, tool_names, answer)
    assert not failures, (
        f"\nScenario '{scenario.name}' failed:\n"
        f"  rationale: {scenario.rationale}\n"
        f"  question:  {scenario.question}\n"
        + "".join(f"  - {f}\n" for f in failures)
    )