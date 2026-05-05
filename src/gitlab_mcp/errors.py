"""
Typed exceptions for the GitLab client.

Why a typed hierarchy and not just `httpx.HTTPStatusError` everywhere:

  * Callers (the MCP server) want to translate auth failures differently
    from rate limits differently from 5xx — easier with named types.
  * The MCP layer can map our exceptions to MCP-protocol error responses
    without leaking httpx internals into the protocol surface.
  * Tests can assert on `pytest.raises(GitLabAuthError)` — much more
    readable than checking response status codes.
"""

from __future__ import annotations


class GitLabError(Exception):
    """Base class for all GitLab client errors."""


class GitLabAuthError(GitLabError):
    """Token is missing, invalid, or has insufficient scope (401/403)."""


class GitLabNotFoundError(GitLabError):
    """The requested resource does not exist or isn't visible to the token (404)."""


class GitLabRateLimitError(GitLabError):
    """We've hit GitLab's API rate limit (429)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GitLabServerError(GitLabError):
    """GitLab returned a 5xx — usually transient."""