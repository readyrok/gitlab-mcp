"""Jira MCP server — a read-only Jira connector exposing 2 tools.

Built as a sibling of `gitlab_mcp` to demonstrate that the agent
architecture generalizes: a second connector is a new package, zero
agent changes.

The 2-tool surface (list_projects + search_issues) is deliberately
minimal — the goal is to prove the pattern, not match the GitLab
connector's depth.
"""