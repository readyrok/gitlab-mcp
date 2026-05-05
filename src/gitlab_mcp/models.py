"""
Pydantic models for the GitLab entities we care about.

Notes on the design:

  * We model only the fields we actually use in MCP tools. GitLab returns
    150+ fields per project; modeling all of them would be churn for no
    payoff. Adding fields later is a 1-line change.
  * `extra="ignore"` so unknown fields don't blow up — GitLab adds fields
    over time, and we don't want our code to break on a server upgrade.
  * `frozen=True` for immutability — easier to reason about, safer to
    pass across async boundaries.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _GitLabModel(BaseModel):
    """Common config for every GitLab model in this module."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        frozen=True,
    )


class Project(_GitLabModel):
    """A GitLab project (repo)."""

    id: int
    name: str
    path: str
    path_with_namespace: str
    description: str | None = None
    web_url: str
    default_branch: str | None = None
    visibility: str
    last_activity_at: datetime | None = None


class Author(_GitLabModel):
    """The user who authored an issue, MR, or comment."""

    id: int
    username: str
    name: str


class MergeRequest(_GitLabModel):
    """A GitLab merge request."""

    id: int
    iid: int           # project-local id (the !123 number users see)
    project_id: int
    title: str
    description: str | None = None
    state: str         # opened | closed | merged | locked
    draft: bool = False
    web_url: str
    source_branch: str
    target_branch: str
    author: Author
    created_at: datetime
    updated_at: datetime
    merged_at: datetime | None = None


class Issue(_GitLabModel):
    """A GitLab issue."""

    id: int
    iid: int
    project_id: int
    title: str
    description: str | None = None
    state: str         # opened | closed
    labels: list[str] = Field(default_factory=list)
    web_url: str
    author: Author
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class Pipeline(_GitLabModel):
    """A GitLab CI pipeline run."""

    id: int
    iid: int | None = None
    project_id: int
    sha: str
    ref: str           # branch or tag the pipeline ran on
    status: str        # success | failed | running | pending | canceled | ...
    source: str | None = None
    web_url: str
    created_at: datetime
    updated_at: datetime
    duration: int | None = None  # seconds

class UserActivity(_GitLabModel):
    """A summary of a user's recent activity in GitLab.

    This is *not* a 1:1 mapping of a GitLab API resource — there's no
    /activity endpoint. We aggregate raw events from /users/{id}/events
    into the shape an agent actually wants to reason about: counts by
    category, plus a few headline event titles.
    """

    username: str
    user_id: int
    since: datetime
    total_events: int
    pushes: int
    merge_requests_opened: int
    issues_opened: int
    comments: int
    recent_event_titles: list[str] = Field(default_factory=list)