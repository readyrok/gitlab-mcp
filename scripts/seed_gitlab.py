"""
Seed script for the GitLab MCP project.

Creates three dummy projects under the authenticated user's namespace,
populated with realistic issues, merge requests, and CI pipelines.

Usage:
    # First time: create everything from scratch
    python scripts/seed_gitlab.py --seed

    # Reset everything and start over
    python scripts/seed_gitlab.py --wipe --seed

    # Just show what would happen, don't actually call the API
    python scripts/seed_gitlab.py --seed --dry-run

Environment variables (loaded from .env if present):
    GITLAB_URL     default: https://gitlab.com
    GITLAB_TOKEN   required: a Personal Access Token with `api` scope
                   (note: writes need `api`, not `read_api` — we drop back
                   down to `read_api` for the MCP server itself)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration & logging
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed")

GITLAB_URL = os.environ.get("GITLAB_URL", "https://gitlab.com").rstrip("/")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")

# A namespace prefix so we never touch projects we didn't create.
# Every project name created by this script starts with this prefix.
PROJECT_PREFIX = "acme-"


# ---------------------------------------------------------------------------
# Data model: what the seeded world looks like
# ---------------------------------------------------------------------------


@dataclass
class IssueSpec:
    title: str
    description: str
    labels: list[str] = field(default_factory=list)
    close_after_create: bool = False
    comments: list[str] = field(default_factory=list)


@dataclass
class MergeRequestSpec:
    title: str
    description: str
    source_branch: str
    target_branch: str = "main"
    draft: bool = False
    merge_after_create: bool = False
    close_after_create: bool = False


@dataclass
class ProjectSpec:
    name: str           # short slug, prefix is added automatically
    description: str
    issues: list[IssueSpec]
    mrs: list[MergeRequestSpec]
    add_ci: bool = False
    fail_ci: bool = False  # if True, push a second commit that breaks the pipeline


# ---------------------------------------------------------------------------
# The world we're seeding
# ---------------------------------------------------------------------------


def build_world() -> list[ProjectSpec]:
    """The fictional 'Acme Robotics' engineering org."""

    order_service = ProjectSpec(
        name="order-service",
        description="Acme Robotics — order intake and fulfillment service.",
        add_ci=True,
        fail_ci=True,  # we want a failing pipeline in the demo
        issues=[
            IssueSpec(
                title="POST /orders occasionally returns 500 under load",
                description=(
                    "Reproduction: 200 req/s for 30s causes ~2% of requests "
                    "to fail with a 500. Stack traces point at the connection pool."
                ),
                labels=["bug", "priority::high", "area::api"],
                comments=[
                    "Reproduced locally with k6. Pool size is 10, way too low.",
                    "Bumping to 50 helps but doesn't fix the root cause.",
                ],
            ),
            IssueSpec(
                title="Add idempotency keys to POST /orders",
                description="Clients retry on network errors, causing duplicate orders.",
                labels=["enhancement", "priority::high", "area::api"],
            ),
            IssueSpec(
                title="Race condition in payment confirmation webhook",
                description="Two concurrent webhook deliveries can both mark an order paid.",
                labels=["bug", "priority::high", "area::payments"],
            ),
            IssueSpec(
                title="Refactor OrderRepository to use async SQLAlchemy",
                description="Current sync queries block the event loop on slow DB calls.",
                labels=["tech-debt", "area::api"],
            ),
            IssueSpec(
                title="Document the order state machine",
                description="New devs keep getting confused about valid transitions.",
                labels=["documentation"],
            ),
            IssueSpec(
                title="Add Prometheus metrics for queue depth",
                description="We're flying blind on backpressure.",
                labels=["enhancement", "area::ops"],
            ),
            IssueSpec(
                title="Rate limit /orders/search endpoint",
                description="Search endpoint is being hammered by a misbehaving client.",
                labels=["bug", "priority::medium", "area::api"],
            ),
            IssueSpec(
                title="Migrate from logback to structured JSON logs",
                description="Closed: completed in last sprint.",
                labels=["tech-debt"],
                close_after_create=True,
            ),
            IssueSpec(
                title="Fix flaky test_concurrent_checkout integration test",
                description="Closed: deflaked by adding a barrier.",
                labels=["bug", "area::tests"],
                close_after_create=True,
            ),
            IssueSpec(
                title="Upgrade to Python 3.12",
                description="Closed: done.",
                labels=["tech-debt"],
                close_after_create=True,
            ),
            IssueSpec(
                title="Add OpenAPI spec generation",
                description="Closed: shipped, see /docs endpoint.",
                labels=["enhancement", "documentation"],
                close_after_create=True,
            ),
            IssueSpec(
                title="Investigate memory leak in long-lived workers",
                description="Closed: was a third-party library bug, fixed by upgrading.",
                labels=["bug"],
                close_after_create=True,
            ),
        ],
        mrs=[
            MergeRequestSpec(
                title="Add idempotency keys to POST /orders",
                description="Closes the duplicate-order issue. Adds Idempotency-Key header support.",
                source_branch="feat/idempotency-keys",
                merge_after_create=True,
            ),
            MergeRequestSpec(
                title="Bump connection pool size and add metrics",
                description="Short-term fix for the 500s issue. Proper fix to follow.",
                source_branch="fix/connection-pool",
                merge_after_create=True,
            ),
            MergeRequestSpec(
                title="WIP: async SQLAlchemy migration",
                description="Big refactor, still in progress. Do not merge.",
                source_branch="refactor/async-sqlalchemy",
                draft=True,
            ),
            MergeRequestSpec(
                title="Add Prometheus metrics for queue depth",
                description="Ready for review. See linked issue.",
                source_branch="feat/prometheus-metrics",
            ),
            MergeRequestSpec(
                title="Experimental: replace JSON with msgpack",
                description="Closed without merging — perf gains weren't worth the complexity.",
                source_branch="experiment/msgpack",
                close_after_create=True,
            ),
        ],
    )

    inventory_api = ProjectSpec(
        name="inventory-api",
        description="Acme Robotics — internal inventory and stock-level API.",
        add_ci=True,
        fail_ci=False,  # this one's pipeline passes
        issues=[
            IssueSpec(
                title="Stock level cache invalidation is too aggressive",
                description="We invalidate on every write, even for unrelated SKUs.",
                labels=["bug", "priority::medium"],
            ),
            IssueSpec(
                title="Add bulk stock-update endpoint",
                description="Warehouse team needs to update 1000+ SKUs at once.",
                labels=["enhancement", "area::api"],
            ),
            IssueSpec(
                title="Replace in-memory cache with Redis",
                description="Multi-instance deployment requires shared cache.",
                labels=["tech-debt", "area::ops"],
            ),
            IssueSpec(
                title="Document stock reservation semantics",
                description="What happens if a reservation expires mid-checkout?",
                labels=["documentation"],
            ),
            IssueSpec(
                title="Fix off-by-one in low-stock alerts",
                description="Closed: alerts fired at threshold+1.",
                labels=["bug"],
                close_after_create=True,
            ),
            IssueSpec(
                title="Add health check endpoint",
                description="Closed: /healthz now exists.",
                labels=["enhancement"],
                close_after_create=True,
            ),
        ],
        mrs=[
            MergeRequestSpec(
                title="Add bulk stock-update endpoint",
                description="Implements the bulk endpoint requested by warehouse team.",
                source_branch="feat/bulk-update",
            ),
            MergeRequestSpec(
                title="Fix off-by-one in low-stock alerts",
                description="One-line fix.",
                source_branch="fix/low-stock-off-by-one",
                merge_after_create=True,
            ),
        ],
    )

    web_frontend = ProjectSpec(
        name="web-frontend",
        description="Acme Robotics — customer-facing web app.",
        add_ci=False,  # quietest project, no pipeline
        fail_ci=False,
        issues=[
            IssueSpec(
                title="Checkout button unresponsive on Safari iOS 17",
                description="Tap event doesn't fire. Looks like a known Safari bug.",
                labels=["bug", "priority::medium"],
            ),
            IssueSpec(
                title="Add dark mode",
                description="Designs landed last week.",
                labels=["enhancement"],
            ),
            IssueSpec(
                title="Improve Lighthouse score on /products page",
                description="Currently 62, target is 85+.",
                labels=["tech-debt", "performance"],
            ),
            IssueSpec(
                title="Migrate from CRA to Vite",
                description="Closed: shipped.",
                labels=["tech-debt"],
                close_after_create=True,
            ),
        ],
        mrs=[
            MergeRequestSpec(
                title="Add dark mode toggle and theme provider",
                description="Implements dark mode. Screenshots in description.",
                source_branch="feat/dark-mode",
            ),
        ],
    )

    return [order_service, inventory_api, web_frontend]


# ---------------------------------------------------------------------------
# Thin GitLab API client
# ---------------------------------------------------------------------------


class GitLabClient:
    """
    Minimal sync GitLab client. We use sync here because seeding is a
    one-shot script — no need for the async machinery that the MCP server
    will use later.
    """

    def __init__(self, base_url: str, token: str, dry_run: bool = False):
        self.base_url = f"{base_url}/api/v4"
        self.dry_run = dry_run
        self.client = httpx.Client(
            headers={"PRIVATE-TOKEN": token},
            timeout=30.0,
        )

    def close(self) -> None:
        self.client.close()

    # -- core HTTP helpers --------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        if self.dry_run and method != "GET":
            log.info("[dry-run] %s %s  payload=%s", method, path, kwargs.get("json"))
            return {}
        # Tiny retry loop for transient failures.
        for attempt in range(3):
            try:
                resp = self.client.request(method, url, **kwargs)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", "2"))
                    log.warning("Rate limited, sleeping %ss", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except httpx.HTTPStatusError as e:
                if attempt == 2 or e.response.status_code < 500:
                    log.error("HTTP %s on %s %s: %s",
                              e.response.status_code, method, path, e.response.text[:200])
                    raise
                time.sleep(1 + attempt)
        raise RuntimeError("unreachable")

    def get(self, path: str, **kw: Any) -> Any:
        return self._request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self._request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> Any:
        return self._request("PUT", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self._request("DELETE", path, **kw)

    # -- typed helpers we actually use --------------------------------------

    def current_user(self) -> dict[str, Any]:
        return self.get("/user")

    def list_my_projects(self) -> list[dict[str, Any]]:
        # `owned=true` so we don't accidentally touch projects we're just a member of
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.get("/projects", params={"owned": "true", "per_page": 100, "page": page})
            if not batch:
                break
            out.extend(batch)
            page += 1
        return out

    def create_project(self, name: str, description: str) -> dict[str, Any]:
        return self.post("/projects", json={
            "name": name,
            "description": description,
            "visibility": "private",
            "initialize_with_readme": True,
            "default_branch": "main",
        })

    def delete_project(self, project_id: int) -> None:
        self.delete(f"/projects/{project_id}")

    def create_issue(self, project_id: int, spec: IssueSpec) -> dict[str, Any]:
        return self.post(f"/projects/{project_id}/issues", json={
            "title": spec.title,
            "description": spec.description,
            "labels": ",".join(spec.labels),
        })

    def close_issue(self, project_id: int, issue_iid: int) -> None:
        self.put(f"/projects/{project_id}/issues/{issue_iid}", json={"state_event": "close"})

    def add_issue_note(self, project_id: int, issue_iid: int, body: str) -> None:
        self.post(f"/projects/{project_id}/issues/{issue_iid}/notes", json={"body": body})

    def create_branch(self, project_id: int, branch: str, ref: str = "main") -> None:
        self.post(f"/projects/{project_id}/repository/branches",
                  params={"branch": branch, "ref": ref})

    def commit_file(
        self,
        project_id: int,
        branch: str,
        path: str,
        content: str,
        message: str,
        action: str = "create",
    ) -> None:
        self.post(f"/projects/{project_id}/repository/commits", json={
            "branch": branch,
            "commit_message": message,
            "actions": [{"action": action, "file_path": path, "content": content}],
        })

    def create_mr(self, project_id: int, spec: MergeRequestSpec) -> dict[str, Any]:
        title = f"Draft: {spec.title}" if spec.draft else spec.title
        return self.post(f"/projects/{project_id}/merge_requests", json={
            "source_branch": spec.source_branch,
            "target_branch": spec.target_branch,
            "title": title,
            "description": spec.description,
        })

    def merge_mr(self, project_id: int, mr_iid: int) -> None:
        # Polling loop — GitLab needs a moment to compute mergeability.
        for _ in range(10):
            mr = self.get(f"/projects/{project_id}/merge_requests/{mr_iid}")
            status = mr.get("detailed_merge_status") or mr.get("merge_status")
            if status in ("mergeable", "can_be_merged"):
                self.put(f"/projects/{project_id}/merge_requests/{mr_iid}/merge", json={})
                return
            time.sleep(1)
        log.warning("MR !%s never became mergeable, leaving it open", mr_iid)

    def close_mr(self, project_id: int, mr_iid: int) -> None:
        self.put(f"/projects/{project_id}/merge_requests/{mr_iid}",
                 json={"state_event": "close"})


# ---------------------------------------------------------------------------
# Pipeline content — the YAML we commit to trigger CI
# ---------------------------------------------------------------------------

PASSING_CI_YAML = """\
# Minimal pipeline that always passes — used to demo MCP pipeline queries.
stages: [lint, test]

lint:
  stage: lint
  image: python:3.12-slim
  script:
    - echo "Linting..."
    - python -c "print('ok')"

test:
  stage: test
  image: python:3.12-slim
  script:
    - echo "Running tests..."
    - python -c "assert 1 + 1 == 2"
"""

FAILING_CI_PATCH = """\
# Same as before, but the test stage now fails on purpose so we have
# a failing pipeline in the agent demo.
stages: [lint, test]

lint:
  stage: lint
  image: python:3.12-slim
  script:
    - echo "Linting..."
    - python -c "print('ok')"

test:
  stage: test
  image: python:3.12-slim
  script:
    - echo "Running tests..."
    - python -c "assert 1 + 1 == 3, 'arithmetic is broken'"
"""


# ---------------------------------------------------------------------------
# Seeding & wiping logic
# ---------------------------------------------------------------------------


def wipe(client: GitLabClient) -> None:
    """Delete every project under the user's namespace whose name starts with PROJECT_PREFIX."""
    log.info("Listing existing projects...")
    projects = client.list_my_projects()
    targets = [p for p in projects if p["path"].startswith(PROJECT_PREFIX)]
    if not targets:
        log.info("Nothing to wipe.")
        return

    log.info("Found %d acme-* projects to delete:", len(targets))
    for p in targets:
        log.info("  - %s (id=%s)", p["path_with_namespace"], p["id"])

    for p in targets:
        log.info("Deleting %s ...", p["path_with_namespace"])
        client.delete_project(p["id"])

    # GitLab deletes are async. Give it a moment so subsequent creates don't
    # collide with names that are still being torn down.
    log.info("Waiting 10s for deletions to settle...")
    time.sleep(10)


def seed_project(client: GitLabClient, spec: ProjectSpec) -> dict[str, Any]:
    name = f"{PROJECT_PREFIX}{spec.name}"
    log.info("Creating project: %s", name)
    project = client.create_project(name, spec.description)
    pid = project["id"]
    log.info("  -> id=%s, url=%s", pid, project.get("web_url"))

    # ----- Issues -----
    log.info("  Seeding %d issues...", len(spec.issues))
    for issue_spec in spec.issues:
        issue = client.create_issue(pid, issue_spec)
        iid = issue["iid"]
        for comment in issue_spec.comments:
            client.add_issue_note(pid, iid, comment)
        if issue_spec.close_after_create:
            client.close_issue(pid, iid)

    # ----- CI pipeline (must come before MRs so MRs run pipelines too) -----
    if spec.add_ci:
        log.info("  Adding .gitlab-ci.yml on main")
        client.commit_file(pid, "main", ".gitlab-ci.yml",
                           PASSING_CI_YAML, "ci: add minimal pipeline")
        if spec.fail_ci:
            log.info("  Pushing a follow-up commit that breaks the pipeline")
            time.sleep(2)  # tiny gap so the two pipelines are clearly distinct
            client.commit_file(pid, "main", ".gitlab-ci.yml",
                               FAILING_CI_PATCH,
                               "ci: tighten test assertion (this will fail)",
                               action="update")

    # ----- Merge requests -----
    log.info("  Seeding %d merge requests...", len(spec.mrs))
    for mr_spec in spec.mrs:
        client.create_branch(pid, mr_spec.source_branch, ref="main")
        # Make a real commit on the branch so the MR has a diff
        client.commit_file(
            pid,
            mr_spec.source_branch,
            f"NOTES_{mr_spec.source_branch.replace('/', '_')}.md",
            f"# {mr_spec.title}\n\n{mr_spec.description}\n",
            f"docs: notes for {mr_spec.title}",
        )
        mr = client.create_mr(pid, mr_spec)
        mr_iid = mr["iid"]
        if mr_spec.merge_after_create:
            client.merge_mr(pid, mr_iid)
        elif mr_spec.close_after_create:
            client.close_mr(pid, mr_iid)

    log.info("  Done with %s", name)
    return project


def seed_all(client: GitLabClient) -> list[dict[str, Any]]:
    log.info("Authenticated as: %s", client.current_user().get("username"))
    world = build_world()
    created = []
    for spec in world:
        created.append(seed_project(client, spec))
    return created


def print_summary(projects: list[dict[str, Any]]) -> None:
    print()
    print("=" * 70)
    print("Seed complete. Save these IDs in docs/SETUP.md:")
    print("=" * 70)
    for p in projects:
        print(f"  {p['path_with_namespace']:<45} id={p['id']}")
    print()
    print("Verify with:")
    print('  curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \\')
    print(f'    "{GITLAB_URL}/api/v4/projects?membership=true"')
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true",
                        help="create the acme-* projects and populate them")
    parser.add_argument("--wipe", action="store_true",
                        help="delete all acme-* projects under your account")
    parser.add_argument("--dry-run", action="store_true",
                        help="log what would happen without calling the API for writes")
    args = parser.parse_args()

    if not (args.seed or args.wipe):
        parser.error("Pass at least one of --seed or --wipe")

    if not GITLAB_TOKEN:
        log.error("GITLAB_TOKEN is not set. Put it in a .env file or export it.")
        return 1

    client = GitLabClient(GITLAB_URL, GITLAB_TOKEN, dry_run=args.dry_run)
    try:
        if args.wipe:
            wipe(client)
        if args.seed:
            created = seed_all(client)
            if not args.dry_run:
                print_summary(created)
        return 0
    except httpx.HTTPError as e:
        log.error("Aborting due to HTTP error: %s", e)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
