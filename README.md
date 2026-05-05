# gitlab-mcp

An [MCP](https://modelcontextprotocol.io/) server that exposes GitLab as a set
of tools an AI agent can call. Built as a study project around Anthropic's
Model Context Protocol — the same protocol Claude Desktop, Cursor, and other
agentic clients use to connect LLMs to external data and tools.

> **Status:** in active development. Tools and APIs may shift between commits.

## Why this exists

Modern engineering orgs are starting to deploy AI agents that act on real
systems — GitLab, Jira, internal databases, CI tooling. MCP standardizes how
agents talk to those systems so each integration doesn't have to be reinvented.

This project demonstrates:

- A real MCP server implemented in Python with the official SDK
- A read-only GitLab integration with **5 tools** (projects, MRs, issues,
  pipelines, user activity)
- A small CLI agent (using the Anthropic API) that consumes the server and
  answers natural-language questions about a GitLab workspace
- The full lifecycle: TDD on the API client, integration tests on the server,
  Docker packaging, CI on every push

## Architecture

```
                     ┌──────────────────┐
                     │   CLI Agent      │      (Anthropic API +
                     │   (src/agent)    │       MCP client SDK)
                     └────────┬─────────┘
                              │  MCP protocol (stdio / SSE)
                     ┌────────▼─────────┐
                     │   MCP Server     │
                     │ (src/gitlab_mcp) │
                     └────────┬─────────┘
                              │  REST + read_api PAT
                     ┌────────▼─────────┐
                     │     GitLab       │
                     └──────────────────┘
```

See [`docs/DESIGN.md`](docs/DESIGN.md) for the design decisions behind the
above, including the tool surface, auth model, and what I'd change at scale.

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies
uv sync --all-extras

# Configure
cp .env.example .env
# Edit .env: set GITLAB_TOKEN to a read_api PAT from gitlab.com

# Run the tests
uv run pytest

# Run the server (stdio transport — used by MCP clients)
uv run gitlab-mcp
```

For a full walkthrough of how the test environment was provisioned —
including the seed script that creates the `acme-*` demo projects — see
[`docs/SETUP.md`](docs/SETUP.md).

## Tools exposed by the server

| Tool                  | Purpose                                       |
|-----------------------|-----------------------------------------------|
| `list_projects`       | Enumerate projects accessible to the token   |
| `get_merge_requests`  | List MRs by project and state                |
| `search_issues`       | Find issues by keyword across a project      |
| `get_pipeline_status` | Most recent pipelines for a project          |
| `get_user_activity`   | Recent commits / MRs / comments by a user    |

Each tool's schema, description, and rationale lives in
[`docs/DESIGN.md`](docs/DESIGN.md).

## Project layout

```
gitlab-mcp/
├── src/gitlab_mcp/      # the MCP server package
├── src/agent/           # the demo CLI agent (added in day 2)
├── tests/               # pytest suite
├── scripts/             # seed_gitlab.py — provisions the demo data
└── docs/                # SETUP.md, DESIGN.md
```

## License

MIT.