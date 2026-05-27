"""Typed exceptions for the Jira REST client.

Mirrors gitlab_mcp.errors in shape — each HTTP failure class becomes a
named exception so MCP tools can react specifically (e.g. surface 'not
found' to the LLM, retry on transient errors). Translation from raw
HTTP status to typed exception happens in one place in the client.
"""

from __future__ import annotations


class JiraError(Exception):
    """Base for all Jira-side errors raised by the client."""


class JiraAuthError(JiraError):
    """401 / 403 from Jira — bad credentials or insufficient permission."""


class JiraNotFoundError(JiraError):
    """404 from Jira — project, issue, or resource does not exist."""


class JiraRateLimitError(JiraError):
    """429 from Jira — back off and retry."""


class JiraServerError(JiraError):
    """5xx from Jira — upstream is unhealthy."""