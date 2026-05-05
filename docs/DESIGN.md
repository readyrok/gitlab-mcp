# Design

This document captures the *why* behind the design choices in this project.
It's a living artifact — entries are added as decisions get made, not after
the fact. Treat it as a small ADR (Architecture Decision Record) log.

---

## 1. Why MCP, not a direct GitLab integration?

A naive design would be: agent calls Anthropic API, agent has GitLab API
keys, agent calls GitLab directly. That works for one agent and one tool.

It does **not** scale to an organization with:

- Many agents (chat, code review, on-call, retrospectives, …)
- Many tools (GitLab, Jira, Confluence, Polarion, internal services)
- Per-team auth boundaries

MCP solves this by standardizing the contract between agents and tools. The
GitLab integration becomes a server that any MCP-compatible client can use,
without each client knowing GitLab specifics. New agents inherit the
integration for free; new tools (e.g. a Polarion MCP server) plug in the same
way without touching agent code.

**Tradeoff:** an extra protocol layer adds complexity for a single-agent
demo. The architecture only pays off at scale — but the JD describes scale
(40,000+ engineers), so the architecture is the point.

---

## 2. Two-token security model

The seed script needs `api` scope (writes); the MCP server only needs
`read_api`. Rather than reuse one broad token:

- **Seeder token** — `api` scope, 7-day expiry, **revoked immediately after
  the demo data is seeded**.
- **Runtime token** — `read_api` only, 30-day expiry, lives in `.env`.

The runtime token literally cannot create, modify, or delete anything in
GitLab. If the MCP server is compromised, the blast radius is "an attacker
can read what they could already see by being a user." No data destruction,
no privilege escalation.

This is the principle of least privilege applied to a real boundary, not as
a slogan.

---

## 3. Configuration: pydantic-settings, not os.environ

See `src/gitlab_mcp/config.py`. Three concrete benefits over `os.getenv()`
calls scattered through the codebase:

1. **Validation at startup** — wrong types or missing required values fail
   immediately with a readable error, not as a 500 in the middle of a
   tool call.
2. **`SecretStr` for the token** — when a Settings object is logged or
   serialized in error output, the token shows as `**********`. Hard to
   leak by accident.
3. **Easy to override in tests** — `Settings(gitlab_token="fake")` is a
   one-liner. No fragile env-var monkeypatching in every test.

---

## 4. Open questions (to revisit)

These are decisions deferred to later in the project:

- **Tool granularity.** Five coarse-grained tools (e.g. `get_merge_requests`)
  vs. many narrow tools (`list_open_mrs`, `list_draft_mrs`, …). Coarse is
  more flexible for the LLM but harder to describe well; narrow constrains
  the LLM but multiplies surface area. Will decide once I see the agent
  in action and observe failure modes. (Day 2.)
- **Pagination.** GitLab paginates everything at 20 / 100 items. Should
  tools auto-paginate (simpler for the LLM, expensive for big projects) or
  expose a cursor (more complex for the LLM, cheaper)? Lean towards
  capped auto-pagination with a clear cap in the response. (Day 1, block 4.)
- **Caching.** No caching in the v1. If demo response times are bad, add
  short-TTL caching at the GitLab client layer. (Day 3 if needed.)

---

## 5. What I'd change at scale (Thales-relevant)

To extend this from "demo" to "platform serving 40,000 engineers":

- **Auth.** Replace static PAT with per-user OAuth — the server passes the
  caller's identity through, GitLab enforces permissions. No shared bot
  account, no over-privileged service token.
- **Multi-server discovery.** A GitLab MCP server is one of many. A
  registry pattern (or MCP's own composition primitives) lets agents find
  the right server for a given query.
- **Rate-limiting & quotas.** GitLab's API limits become a contention
  point at scale. The MCP server needs its own rate limiter / coalescing
  cache so 100 concurrent agents don't 429 each other.
- **Observability.** Structured logs per tool call, tied to a trace id
  the agent provides. Without this, debugging "why did the agent decide X"
  is impossible at scale.

---

*Add new entries below this line as decisions get made.*