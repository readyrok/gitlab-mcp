"""
Eval scenarios — the behavioral spec for the agent.

Each Scenario is a question plus expectations about how the agent should
answer it. This file is pure data: no test logic. Read it as the spec
for what 'the agent works correctly' means.

Expectations are deliberately loose:

  * must_call_tools — tools that MUST appear among the agent's calls.
    Subset semantics: extra calls are fine, missing ones fail.
  * must_not_call_tools — tools that must NOT be called. Catches the
    agent reaching for irrelevant tools.
  * answer_any_of — at least one of these substrings must be in the
    final answer (case-insensitive).
  * answer_all_of — every one of these substrings must be present.

We assert behaviors, not transcripts. Claude is non-deterministic; a
good agent reaches the right answer by several valid paths. Over-tight
assertions produce flaky evals, which are worse than no evals.

All scenarios target the seeded 'acme-*' workspace. If the seed data
changes, these expectations must change with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    """One behavioral evaluation case."""

    name: str
    question: str
    must_call_tools: list[str] = field(default_factory=list)
    must_not_call_tools: list[str] = field(default_factory=list)
    answer_any_of: list[str] = field(default_factory=list)
    answer_all_of: list[str] = field(default_factory=list)
    # A short note on what this scenario is really testing — shows up
    # in the eval report and documents intent.
    rationale: str = ""


# ---------------------------------------------------------------------------
# The scenario suite.
# ---------------------------------------------------------------------------

SCENARIOS: list[Scenario] = [
    # --- basic tool selection ----------------------------------------------
    Scenario(
        name="list_projects_basic",
        question="What projects do I have?",
        must_call_tools=["list_projects"],
        answer_any_of=["acme-order-service", "acme-inventory-api", "acme-web-frontend"],
        rationale="Simplest case — agent should call list_projects and name the projects.",
    ),
    Scenario(
        name="project_count",
        question="How many projects are in my workspace?",
        must_call_tools=["list_projects"],
        answer_any_of=["3", "three"],
        rationale="Agent must call the tool, not guess a number.",
    ),

    # --- merge requests ----------------------------------------------------
    Scenario(
        name="open_mrs_single_project",
        question="What merge requests are open in acme-order-service?",
        must_call_tools=["get_merge_requests"],
        answer_any_of=["SQLAlchemy", "Prometheus", "merge request"],
        rationale=(
            "Agent should resolve the project name to an id (via list_projects "
            "or memory) and call get_merge_requests scoped to it."
        ),
    ),
    Scenario(
        name="open_mrs_all_projects",
        question="What's open across all my projects?",
        must_call_tools=["list_projects", "get_merge_requests"],
        answer_any_of=["acme-order-service", "acme-inventory-api"],
        rationale="Cross-project — agent should list projects then fan out.",
    ),

    # --- issues ------------------------------------------------------------
    Scenario(
        name="search_issues_keyword",
        question="Are there any payment-related issues in acme-order-service?",
        must_call_tools=["search_issues"],
        answer_any_of=["payment", "webhook", "race condition"],
        rationale="Keyword search — agent should use search_issues with a query.",
    ),
    Scenario(
        name="search_issues_idempotency",
        question="Is there an issue about idempotency in the order service?",
        must_call_tools=["search_issues"],
        answer_any_of=["idempotency", "idempotent", "duplicate"],
        rationale="Specific keyword that matches a seeded issue title.",
    ),

    # --- pipelines ---------------------------------------------------------
    Scenario(
        name="pipeline_status_order_service",
        question="Did the latest pipeline pass for acme-order-service?",
        must_call_tools=["get_pipeline_status"],
        answer_any_of=["fail", "failed", "pass", "passed", "success"],
        rationale="Pipeline query — agent should call get_pipeline_status.",
    ),
    Scenario(
        name="pipeline_status_no_pipeline",
        question="What's the CI status for acme-web-frontend?",
        must_call_tools=["get_pipeline_status"],
        answer_any_of=["no pipeline", "no ci", "not configured", "no recent", "doesn't have"],
        rationale=(
            "acme-web-frontend has no pipelines seeded. Agent should call "
            "the tool, get an empty result, and say so plainly — not invent "
            "a status."
        ),
    ),

    # --- user activity -----------------------------------------------------
    Scenario(
        name="user_activity_basic",
        question="What has readyrok been working on recently?",
        must_call_tools=["get_user_activity"],
        answer_any_of=["commit", "merge request", "issue", "activity", "push"],
        rationale="Activity query — agent should call get_user_activity.",
    ),

    # --- tool discipline (negative tests) ----------------------------------
    Scenario(
        name="no_tools_for_greeting",
        question="Hi, what can you help me with?",
        must_not_call_tools=[
            "list_projects", "get_merge_requests", "search_issues",
            "get_pipeline_status", "get_user_activity",
        ],
        answer_any_of=["gitlab", "project", "merge request", "issue", "pipeline", "help"],
        rationale=(
            "A greeting needs no tools. Agent should describe its "
            "capabilities without calling anything — over-eager tool use "
            "is a real failure mode."
        ),
    ),
    Scenario(
        name="issue_question_no_pipeline_tool",
        question="Find me bugs in acme-inventory-api.",
        must_call_tools=["search_issues"],
        must_not_call_tools=["get_pipeline_status"],
        answer_any_of=["cache", "stock", "bug", "issue"],
        rationale=(
            "A bug-finding question is an issue search. Agent should NOT "
            "reach for the pipeline tool — tests tool-selection precision."
        ),
    ),

    # --- multi-step reasoning ----------------------------------------------
    Scenario(
        name="comparative_question",
        question="Which of my projects has the most open merge requests?",
        must_call_tools=["list_projects", "get_merge_requests"],
        answer_any_of=["acme-order-service", "order-service"],
        rationale=(
            "Comparative — agent must list projects, query MRs for each, "
            "and reason over the results. acme-order-service has the most "
            "(2 open). Tests multi-tool orchestration + reasoning."
        ),
    ),

    # --- harder: edge cases and synthesis ----------------------------------
    Scenario(
        name="ambiguous_project_name",
        question="What's the status of the order service?",
        must_call_tools=["list_projects"],
        answer_any_of=["acme-order-service", "order-service", "pipeline", "merge request"],
        rationale=(
            "The user says 'order service', not the exact slug "
            "'acme-order-service'. The agent must call list_projects to "
            "resolve the fuzzy name before it can act. Tests whether the "
            "list_projects tool description adequately signals 'use this "
            "to resolve ambiguous project names'."
        ),
    ),
    Scenario(
        name="declines_write_action",
        question="Delete the failed pipeline for acme-order-service.",
        must_not_call_tools=[
            # There is no delete tool — but the agent should also not
            # pretend it did the deletion via some other tool.
            "get_pipeline_status",
        ],
        answer_any_of=[
            "can't", "cannot", "unable", "read-only", "read only",
            "don't have", "not able", "no tool",
        ],
        rationale=(
            "The server is read-only — there is no delete capability. The "
            "agent must explain it can't do this rather than hallucinating "
            "a successful deletion. A confident false 'done!' here would be "
            "a serious failure mode. (must_not_call get_pipeline_status is "
            "a loose proxy for 'didn't go off and do something tangential' "
            "— if this proves too strict in practice we relax it.)"
        ),
    ),
    Scenario(
        name="standup_synthesis",
        question="Give me a standup-style summary of acme-order-service.",
        must_call_tools=["get_merge_requests"],
        answer_any_of=["merge request", "issue", "pipeline", "MR"],
        rationale=(
            "A standup summary needs synthesis across multiple tool types "
            "— open MRs, recent issues, pipeline health. We assert at least "
            "get_merge_requests is called and the answer touches multiple "
            "work categories. We deliberately don't require ALL three tools "
            "— a reasonable agent might judge two sufficient — but it "
            "should clearly do more than dump one tool's raw output."
        ),
    ),
    Scenario(
        name="nonexistent_user",
        question="What has the user definitely-not-a-real-person been working on?",
        must_call_tools=["get_user_activity"],
        answer_any_of=[
            "not found", "no user", "couldn't find", "could not find",
            "doesn't exist", "does not exist", "no such user", "unable to find",
        ],
        rationale=(
            "The username doesn't exist. get_user_activity resolves "
            "username->id first and raises GitLabNotFoundError on a miss. "
            "The agent should surface that plainly — not invent activity. "
            "Tests graceful handling of the not-found path end to end."
        ),
    ),
]