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

## 4. Single point of error translation

The GitLab client originally translated HTTP errors to typed exceptions
inline in `_get`. When pagination was added, `_get_paginated` needed
similar logic but with access to the raw response (for the `Link` header),
so the error-translation block was duplicated — minus the 5xx and 429
branches, which I overlooked.

The existing test for 5xx errors caught the regression on the very next
pytest run. The fix was a small refactor: extract `_translate_error_response`
as a static helper that both code paths call. One place to update when
GitLab adds a new error class, instead of two-and-divergent.

The general principle: **duplication is fine until you find a divergence**,
at which point a regression has already happened and you refactor under
pressure. Test coverage on the divergent paths is what keeps the cost low.

---

## 5. Bounded pagination, not unbounded

GitLab paginates every list endpoint at 20 items by default, 100 maximum.
Tools have three options:

1. **Return only page 1.** Simple, but silently truncates.
2. **Auto-paginate, unbounded.** Complete data, but a busy project with
   500 open MRs blows the LLM's prompt budget and costs the user 10x in
   tokens for data the agent will never use.
3. **Auto-paginate, capped.** Bounded output, agent gets a representative
   slice, large lists fail loudly rather than silently.

Chose option 3. The default cap is 5 pages × 100 items = 500 items per
tool call. `_get_paginated` exposes `max_pages` so a future tool that
genuinely needs unbounded data can opt out.

The general principle this captures: **bounded outputs matter when the
consumer is an LLM with a finite prompt budget**. Same reasoning drives
`recent_event_titles[:10]` in `get_user_activity` and `min(max(limit, 1), 100)`
clamping in `get_pipeline_status`. Tool authors think about quality of
output for an LLM consumer, not completeness for a human reader.

**What this would look different at scale.** The current cap is hard-coded.
A multi-tenant production system would make it dynamic per-caller (some
agents have larger context windows than others) and add a `truncated:
True` flag in the response so the agent can mention to the user that
data was clipped, rather than misleading them.

---

## 6. Structured logging at the API boundary

Every call through `_get` and `_get_paginated` emits a single log line
in `key=value` format: 

gitlab.api.call    path=/projects status=200 elapsed_ms=143
gitlab.api.timeout path=/projects/9999 elapsed_ms=30000

Three reasons this is worth doing day-one rather than retrofitting later:

1. **Debugging.** When a tool returns wrong data, the trace shows exactly
   which API call to look at.
2. **Demo legibility.** During the agent demo, these lines stream below
   the natural-language output — the audience can see "the agent decided
   to call list_projects, then get_merge_requests on each one."
3. **Observability story for scale.** At 40,000 engineers, "why did the
   agent decide X?" is unanswerable without tool-call traces tied to a
   request id. Doing it now means the pattern is established when the
   server gets per-request tracing later.

Format choice: stdlib `logging` with key=value, no structured-logging
library. Reasoning: zero added dependencies, greppable as plain text,
trivially parsed by any aggregator. A logging library would be premature
complexity at this stage.

## 7. Tool descriptions vs. system prompt — where to put behaviour

Two surfaces influence what the agent does:

  * **Per-tool descriptions** (in `server.py`'s `@mcp.tool(description=...)`)
    — read by the LLM when deciding *which* tool to call.
  * **System prompt** (in `agent/loop.py`'s `SYSTEM_PROMPT`)
    — read by the LLM as global behaviour guidance for every turn.

Rule of thumb:

  * If the issue is "Claude picked the wrong tool", fix the tool
    description. Tools should be self-describing — when, what, and how.
  * If the issue is "Claude is too verbose / always uses bullets / 
    speaks in the wrong tone", fix the system prompt.

Concrete tuning during Day 2:

  * Observed: when asked "list project names", Claude returned full
    project details (description, IDs, timestamps).
  * Diagnosis: per-tool descriptions can't anticipate response-length
    preferences — that's a global behaviour, not a tool-selection issue.
  * Fix: added a "match the response length to what was asked" line
    to the system prompt.
  * Result: short questions now get short answers; the agent still
    goes deep when explicitly asked.

The general principle: **tool descriptions answer "should I call this?"
The system prompt answers "how should I respond?" Keep behaviour in the
right place.**

## 8. Three-layer agent: protocol, orchestration, presentation

The CLI agent is split into three modules with strict responsibility
boundaries:

  * `agent/mcp_client.py` (Layer 1): wraps the MCP SDK's stdio client.
    Spawns the server as a subprocess, exchanges JSON-RPC, exposes
    `list_tools()` and `call_tool()`. Knows about MCP, knows nothing
    about LLMs.
  * `agent/loop.py` (Layer 2): owns the Anthropic client, conversation
    history, and the agentic loop. Yields events as the loop progresses.
    Knows about LLMs, knows nothing about terminals.
  * `agent/cli.py` (Layer 3): renders events to a terminal, runs the
    REPL, parses CLI flags. Knows nothing about LLMs or MCP — just
    consumes events.

Each layer has a different rate of change and a different reason to
be tested:

  * The MCP layer changes when the SDK does (rare).
  * The loop changes during prompt-engineering iteration (often).
  * The CLI changes when UX requirements shift (occasional).

Tangling them means a CLI tweak risks breaking the protocol code, or
a prompt experiment risks breaking the terminal rendering. Same
principle as the GitLab client's `_get` (HTTP) vs. tool methods
(business logic) split — different domain, identical instinct.

**Why streaming events instead of returning a final answer.** A
synchronous `ask() -> str` would block the terminal silently for
several seconds while Claude reasons and tools run. That kills the
demo. Yielding events (`ToolCallEvent`, `ToolResultEvent`,
`TextEvent`) as they happen turns those silent seconds into a live
trace of the agent's reasoning. The CLI prints each event the moment
it arrives; the user sees the agent thinking. Async generators
(`async def ... yield`) make this clean — no callback indirection,
no separate streaming abstraction.

**Why history is persistent across `ask()` calls.** Follow-up
questions in the REPL ("and what about issues?") only work if Claude
sees the prior tool results in context. Each `ask()` appends to a
single `_history` list owned by the loop. `reset()` is exposed for
fresh starts. This shifts the cost: Anthropic's per-call token
spend grows over a session because we resend history every turn.
For a 3-day demo, that cost is invisible; in production it would
warrant prompt caching (Anthropic supports it natively) — explicitly
out of scope for this project.

## 9. Testing strategy: three files, three scopes

The test suite is split into three files reflecting the architecture:

  * `tests/test_gitlab_client.py` — unit tests for the GitLab REST
    wrapper. Fast (httpx mocked via `respx`), TDD-driven during Day 1.
    Covers happy paths, every error class in the typed hierarchy,
    pagination, parameter forwarding.
  * `tests/test_server.py` — integration tests for the MCP layer.
    Verifies all tools are registered with the right names and that
    the wiring from a tool function through the lifespan-shared client
    actually works end-to-end (against a `respx`-mocked GitLab).
  * `tests/test_agent_loop.py` — orchestration tests for the agent.
    Hand-rolled fakes for both Anthropic and the MCP adapter; tests
    the loop's logic (event order, history growth, iteration cap)
    without burning real API credits or spawning subprocesses.

Two principles I tried to keep:

  * **Don't repeat coverage between files.** The server tests don't
    re-test the GitLab client's error handling — that's already covered.
    The agent tests don't re-test the server's tool registrations —
    same reason. Each file owns its scope.
  * **Hand-rolled fakes when they're clearer than `mock.patch`.** The
    agent tests use small `@dataclass` stand-ins for Anthropic responses
    and the MCP adapter. They're more lines of code than a `mock.patch`
    one-liner would be, but they read like documentation and the IDE
    catches typos. In a 6-month-old codebase that's a real win.

What's deliberately *not* tested:

  * Real Anthropic round-trips. Cost and flakiness aren't worth the
    marginal coverage. The protocol itself was verified manually via
    MCP Inspector during development.
  * Real GitLab calls. Same reasoning, plus the seeded data shouldn't
    be a test dependency.
  * The CLI's terminal output. Could be done with `capsys` but the
    rendering logic is trivial enough that a regression would be
    obvious.

The result is 25 tests, ~85% coverage on the two main packages, and
a test run under 1 second on cold start. Fast enough that running
the suite is part of the inner loop, not a chore.

## 10. Open questions (to revisit)

Decisions deferred to later in the project:

- **Tool granularity.** Five coarse-grained tools (e.g. `get_merge_requests`)
  vs. many narrow tools (`list_open_mrs`, `list_draft_mrs`, …). Coarse is
  more flexible for the LLM but harder to describe well; narrow constrains
  the LLM but multiplies surface area. Will decide once I see the agent
  in action and observe failure modes. (Day 2.)
- **Caching.** No caching in the v1. If demo response times are bad, add
  short-TTL caching at the GitLab client layer. (Day 3 if needed.)
- **Retry on 5xx.** Currently no retry — caller sees `GitLabServerError`
  immediately. A small bounded retry (1 retry with 1s backoff) would
  smooth over transient blips. Defer until I see whether real GitLab
  returns 5xxs in practice during the demo.

## 11. What I'd change at scale (Thales-relevant)

To extend this from "demo" to "platform serving 40,000 engineers":

- **Auth.** Replace static PAT with per-user OAuth — the server passes
  the caller's identity through, GitLab enforces permissions. No shared
  bot account, no over-privileged service token.
- **Multi-server discovery.** A GitLab MCP server is one of many. A
  registry pattern (or MCP's own composition primitives) lets agents
  find the right server for a given query.
- **Rate-limiting & quotas.** GitLab's API limits become a contention
  point at scale. The MCP server needs its own rate limiter / coalescing
  cache so 100 concurrent agents don't 429 each other.
- **Observability.** The structured logs from section 6, plus tracing
  tied to a request id the agent provides. Without this, debugging "why
  did the agent decide X" is impossible at scale.
- **Tenancy.** The client today is single-token. Multi-tenant means
  per-request token routing, separate connection pools, and audit logs
  showing which tenant's token made which call.

---

*Add new entries below this line as decisions get made.*