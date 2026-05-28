# gitlab-mcp

[![CI](https://github.com/readyrok/gitlab-mcp/actions/workflows/ci.yaml/badge.svg)](https://github.com/readyrok/gitlab-mcp/actions/workflows/ci.yaml/badge.svg)

> A read-only [Model Context Protocol](https://modelcontextprotocol.io/) server
> exposing GitLab as five tools an AI agent can use, plus a CLI agent that
> drives it through the Anthropic API.

Built as a study project to understand MCP from both sides — the protocol's
server surface (how tools are described and executed) and its client surface
(how an agent discovers and orchestrates them). The whole stack runs locally
against your own GitLab and Anthropic credentials.

[![asciicast](https://asciinema.org/a/nEFlQfXTL8O5qldu.svg)](https://asciinema.org/a/nEFlQfXTL8O5qldu)

## Why this exists

Modern engineering organizations are starting to deploy AI agents that act on
real systems — GitLab, Jira, internal services, CI tooling. MCP is the
emerging standard for how an agent discovers and uses such tools, and it's
the same protocol Claude Desktop, Cursor, and other production clients use.

This project demonstrates the full stack:

- A real **MCP server** exposing 5 GitLab capabilities (projects, merge
  requests, issues, pipelines, user activity), with LLM-facing tool
  descriptions tuned for accuracy.
- A real **MCP client** + agentic loop that uses the Anthropic API to drive
  the server. Streams tool calls live so you can watch the agent reason.
- The lifecycle around it: TDD on the GitLab client, server-level integration
  tests, structured logging at API boundaries, bounded pagination for LLM
  consumption, a typed exception hierarchy with single-point translation.

The detailed reasoning behind every architectural choice — and what would
change at scale — lives in [`docs/DESIGN.md`](docs/DESIGN.md).

## Architecture

```mermaid
flowchart TD
    user[User<br/>natural-language questions]
    user --> cli[CLI / REPL<br/>src/agent/cli.py]
    cli --> loop[AgentLoop<br/>src/agent/loop.py]
    loop -->|messages.create<br/>+ tool definitions| anthropic[Anthropic API<br/>Claude Sonnet]
    anthropic -->|tool_use blocks| loop
    loop -->|call_tool over JSON-RPC| mcpc[MCPClientAdapter<br/>src/agent/mcp_client.py]
    mcpc -->|stdio subprocess| server[FastMCP Server<br/>src/gitlab_mcp/server.py]
    server -->|method calls| client[GitLabClient<br/>src/gitlab_mcp/gitlab_client.py]
    client -->|REST + read_api PAT| gitlab[GitLab REST API]
```

Three things worth noting about the layout:

- **The agent has no GitLab knowledge.** It speaks MCP. Swap the server and it
  runs against any other MCP-compatible service.
- **The MCP server has no agent knowledge.** It exposes tools. Any
  MCP-compatible client (Claude Desktop, Cursor, your own agent) can use it.
- **The boundary between agent and server is a real subprocess speaking
  JSON-RPC over stdio.** It's not a glorified function call dressed up as a
  protocol — same transport Claude Desktop uses against MCP servers in
  production.

## Tools exposed

Each tool's full LLM-facing description lives in
[`src/gitlab_mcp/server.py`](src/gitlab_mcp/server.py); these are the
one-line summaries.

| Tool | Purpose |
|---|---|
| `list_projects` | Enumerate projects accessible to the configured user |
| `get_merge_requests` | List MRs by project and state (opened / closed / merged / locked / all) |
| `search_issues` | Find issues by free-text search within a project |
| `get_pipeline_status` | Most recent CI pipelines for a project, newest-first |
| `get_user_activity` | Aggregated summary of a user's recent commits / MRs / issues / comments |

## A second connector: Jira

To show the architecture generalizes, the repo includes a second MCP
server — a read-only Jira connector — built as a parallel package to
the GitLab one (`src/jira_mcp/`, mirroring `src/gitlab_mcp/`). It
exposes two tools: `list_projects` and `search_issues`.

The agent doesn't change to use it. Same loop, same CLI — you just point
the server command at the Jira server:

```bash
uv run agent --server-command "uv run jira-mcp" "find login bugs in the mobile project"
```

The agent has no GitLab knowledge and no Jira knowledge — it speaks MCP.
A new backend is a new package plus a console-script entry; the agent is
untouched. See [`docs/DESIGN.md`](docs/DESIGN.md) §12 for the
architectural reasoning (parallel packages vs. shared base, one config
layer for N connectors, the Jira security-model difference, and a real
endpoint-deprecation bug the live integration caught).

Jira setup (Atlassian Cloud account + API token) is documented in
[`docs/SETUP.md`](docs/SETUP.md).

## Quickstart

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and a GitLab account.

```bash
# 1. Install dependencies
uv sync --all-extras

# 2. Configure
cp .env.example .env
# Edit .env: set GITLAB_TOKEN (read_api scope is enough) and ANTHROPIC_API_KEY

# 3. (Optional) Seed a demo workspace under your GitLab account
#    Creates three projects with realistic issues, MRs, and pipelines.
#    See docs/SETUP.md for full details, including the two-token security model.
uv run python scripts/seed_gitlab.py --seed

# 4. Run the agent
uv run agent                              # interactive REPL
uv run agent "what's broken in CI?"       # single-shot
uv run agent --verbose "..."              # show every tool call as it happens

# 5. Or run the MCP server directly (e.g. for Claude Desktop, Cursor, the MCP Inspector)
uv run gitlab-mcp

## Running the server in Docker

A `Dockerfile` and `compose.yml` are included so you can run the MCP
server in a container — useful when wiring it into Claude Desktop,
Cursor, or another MCP client that prefers a stable subprocess target.

```bash
docker compose build
docker compose run --rm server
```

The image is multi-stage (uv-based builder, slim runtime) and runs as
a non-root user. The agent itself isn't containerized — it spawns the
server as a local subprocess, which is faster and simpler for the
demo flow above.

```

## Project layout

```
gitlab-mcp/
├── src/
│   ├── gitlab_mcp/         # the MCP server
│   │   ├── server.py         # FastMCP entry; tool registrations
│   │   ├── gitlab_client.py  # async GitLab REST client (paginated, typed errors)
│   │   ├── models.py         # Pydantic models per entity
│   │   ├── errors.py         # typed exception hierarchy
│   │   └── config.py         # pydantic-settings — single config surface
│   └── agent/              # the CLI agent
│       ├── cli.py            # REPL + single-shot mode
│       ├── loop.py           # agentic loop (Anthropic + tool dispatch)
│       └── mcp_client.py     # stdio MCP client adapter
├── tests/                  # 25 tests across unit + integration
├── scripts/seed_gitlab.py  # provisions the demo workspace
└── docs/
    ├── SETUP.md            # how the demo workspace was provisioned
    └── DESIGN.md           # architectural decisions + what would change at scale
```

## Tests

```bash
uv run pytest          # 25 tests, ~85% coverage
uv run pytest --cov    # detailed coverage report
uv run ruff check      # lint
```

The test suite is split across three files reflecting the architecture:

- `tests/test_gitlab_client.py` — unit tests for the GitLab client. TDD-driven;
  the git history shows the test→implementation→test rhythm.
- `tests/test_server.py` — integration tests for the MCP layer.
- `tests/test_agent_loop.py` — agentic loop tests with hand-rolled fakes for
  Anthropic and the MCP client.

## License

MIT.