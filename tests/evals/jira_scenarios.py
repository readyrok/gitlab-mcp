"""Jira-side eval scenarios.

Mirrors gitlab_mcp's eval_scenarios.py in shape but is deliberately
smaller — the Jira connector has 2 tools, not 5, so the surface to
exercise is narrower. Four scenarios cover what matters: basic tool
selection, keyword search against real seeded data, ambiguous-name
resolution, and two-tool composition.

All scenarios target the seeded Atlassian workspace (3 'Acme' projects
with ~16 issues). If the seed data changes, expectations must change
with it. See scripts/seed_jira.py for the source data.
"""

from __future__ import annotations

# Re-use the Scenario dataclass and checker from the GitLab evals — same
# shape, same logic. The two suites share their behavioral-spec primitives
# even though they target different connectors.
from tests.evals.gitlab_scenarios import Scenario, check_scenario  # noqa: F401

JIRA_SCENARIOS: list[Scenario] = [
    Scenario(
        name="jira_list_projects_basic",
        question="What projects are in Jira?",
        must_call_tools=["list_projects"],
        answer_any_of=["Acme Platform", "Acme Mobile", "Acme Infrastructure",
                       "SCRUM", "AM", "AI"],
        rationale=(
            "Simplest Jira eval — agent should call list_projects and "
            "name the projects (either by display name or by key)."
        ),
    ),
    Scenario(
        name="jira_search_issues_keyword",
        question="Find login bugs in the Acme Platform project.",
        must_call_tools=["search_issues"],
        answer_any_of=["login", "password", "SSO", "SCRUM-"],
        rationale=(
            "Keyword search against real seeded data. The 'Login fails "
            "on password reset for SSO users' bug should match. Tests "
            "that search_issues is called and returns the right shape."
        ),
    ),
    Scenario(
        name="jira_ambiguous_project_name",
        question="Are there any crash bugs in the mobile project?",
        must_call_tools=["list_projects", "search_issues"],
        answer_any_of=["crash", "cold start", "scrolling", "AM-"],
        rationale=(
            "The user says 'mobile project', not the exact key 'AM'. "
            "The agent must call list_projects to resolve the fuzzy name "
            "before search_issues. Tests whether the list_projects tool "
            "description's 'call this FIRST' guidance lands — a lesson "
            "carried forward from the GitLab side."
        ),
    ),
    Scenario(
        name="jira_two_tool_composition",
        question=(
            "What backup or deployment issues are there in the "
            "infrastructure project?"
        ),
        must_call_tools=["list_projects", "search_issues"],
        answer_any_of=[
            "backup", "deploy", "Kubernetes", "evict", "AI-",
        ],
        rationale=(
            "Two-tool composition: resolve 'infrastructure project' to "
            "'AI' via list_projects, then search. Multiple seed issues "
            "match (backup silently fails, Kubernetes pods evicted, etc.) "
            "so any of several keywords prove success."
        ),
    ),
]