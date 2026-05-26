"""
Eval report runner.

Runs the full behavioral eval suite against the real agent and prints a
formatted report — pass rate, per-category breakdown, timing.

This is the human-facing companion to `pytest -m evals`: pytest is for
CI and pass/fail gating; this script is for reading. Same scenarios,
same checks (imported from tests/evals/eval_scenarios.py — no
duplication).

Usage:
    uv run python scripts/run_evals.py

Requires ANTHROPIC_API_KEY and GITLAB_TOKEN (real credentials — this
hits real Claude and real GitLab). Costs ~$0.40 per full run.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Make both the package source and the tests/evals dir importable.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from agent.loop import AgentLoop, TextEvent, ToolCallEvent  # noqa: E402
from agent.mcp_client import MCPClientAdapter  # noqa: E402
from gitlab_mcp.config import get_settings  # noqa: E402
from tests.evals.eval_scenarios import SCENARIOS, Scenario, check_scenario  # noqa: E402


# Map each scenario to a human-readable category for the breakdown.
# Derived from the scenario name prefix / intent.
_CATEGORY: dict[str, str] = {
    "list_projects_basic": "basic",
    "project_count": "basic",
    "open_mrs_single_project": "merge requests",
    "open_mrs_all_projects": "merge requests",
    "search_issues_keyword": "issues",
    "search_issues_idempotency": "issues",
    "pipeline_status_order_service": "pipelines",
    "pipeline_status_no_pipeline": "pipelines",
    "user_activity_basic": "user activity",
    "no_tools_for_greeting": "tool discipline",
    "issue_question_no_pipeline_tool": "tool discipline",
    "comparative_question": "reasoning",
    "ambiguous_project_name": "edge cases",
    "declines_write_action": "edge cases",
    "standup_synthesis": "reasoning",
    "nonexistent_user": "edge cases",
}


async def _run_one(agent: AgentLoop, scenario: Scenario) -> tuple[list[str], str]:
    """Drive one scenario; return (tool names called, answer text)."""
    tool_names: list[str] = []
    text_chunks: list[str] = []
    async for event in agent.ask(scenario.question):
        if isinstance(event, ToolCallEvent):
            tool_names.append(event.name)
        elif isinstance(event, TextEvent):
            text_chunks.append(event.text)
    return tool_names, " ".join(text_chunks)


async def main() -> int:
    settings = get_settings()
    if settings.anthropic_api_key is None:
        print("ERROR: ANTHROPIC_API_KEY not set — evals need real credentials.")
        return 1

    print("=" * 64)
    print("  gitlab-mcp agent — eval report")
    print("=" * 64)
    print(f"  Running {len(SCENARIOS)} scenarios against real Claude + GitLab...")
    print()

    results: list[tuple[Scenario, bool, float, list[str]]] = []
    suite_start = time.perf_counter()

    for scenario in SCENARIOS:
        # Fresh agent per scenario — no history bleed. Opening the MCP
        # connection inside this task keeps anyio's cancel scope happy.
        scenario_start = time.perf_counter()
        async with MCPClientAdapter(["uv", "run", "gitlab-mcp"]) as mcp:
            agent = AgentLoop(settings=settings, mcp=mcp)
            tool_names, answer = await _run_one(agent, scenario)
        elapsed = time.perf_counter() - scenario_start

        failures = check_scenario(scenario, tool_names, answer)
        passed = not failures
        results.append((scenario, passed, elapsed, failures))

        marker = "PASS" if passed else "FAIL"
        category = _CATEGORY.get(scenario.name, "uncategorized")
        print(f"  [{marker}]  {scenario.name:<32} {category:<16} {elapsed:5.1f}s")
        for f in failures:
            print(f"           └─ {f}")

    suite_elapsed = time.perf_counter() - suite_start

    # ----- summary --------------------------------------------------------
    passed_count = sum(1 for _, p, _, _ in results if p)
    total = len(results)
    pct = (passed_count / total * 100) if total else 0.0

    print()
    print("-" * 64)
    print(f"  Pass rate: {passed_count}/{total} ({pct:.0f}%)   "
          f"total {suite_elapsed:.0f}s")
    print()

    # Per-category breakdown.
    by_cat: dict[str, list[bool]] = {}
    for scenario, passed, _, _ in results:
        cat = _CATEGORY.get(scenario.name, "uncategorized")
        by_cat.setdefault(cat, []).append(passed)

    print("  By category:")
    for cat in sorted(by_cat):
        outcomes = by_cat[cat]
        cat_pass = sum(outcomes)
        print(f"    {cat:<20} {cat_pass}/{len(outcomes)}")

    print("-" * 64)

    # Exit non-zero if anything failed — usable as a CI gate later.
    return 0 if passed_count == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))