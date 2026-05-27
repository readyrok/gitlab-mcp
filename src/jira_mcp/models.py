from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Project(BaseModel):

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str = Field(description="Numeric Jira project ID as a string (Jira returns strings).")
    key: str = Field(description="Short alphanumeric project key (e.g. 'SCRUM', 'AM').")
    name: str = Field(description="Human-readable project name.")
    project_type_key: str | None = Field(
        default=None,
        alias="projectTypeKey",
        description="'software', 'business', or 'service_desk'.",
    )


class Issue(BaseModel):
    """A Jira issue — bug, task, story, etc."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    id: str = Field(description="Numeric issue ID as a string.")
    key: str = Field(description="Issue key, e.g. 'SCRUM-12'.")
    summary: str = Field(description="One-line issue summary (title).")
    status: str = Field(description="Current workflow status, e.g. 'To Do', 'In Progress', 'Done'.")
    issue_type: str = Field(description="Issue type name: 'Bug', 'Task', 'Story', etc.")
