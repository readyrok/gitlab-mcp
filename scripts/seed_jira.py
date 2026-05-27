"""
Seed the demo Jira workspace with realistic issues.

Assumes three projects already exist in your Atlassian Cloud site:
  - Acme Platform (key: SCRUM)
  - Acme Mobile (key: AM)
  - Acme Infrastructure (key: AI)

The script does NOT create projects — you created them in the UI during
setup. It only creates issues, with ADF-formatted descriptions, across
the three projects.

Idempotency: before creating an issue, it searches for an existing one
with the same summary in the same project; if found, it skips. So the
script is safe to re-run after partial failures or after adding new
issues to the seed data.

Why ADF? Jira Cloud REST API v3 requires Atlassian Document Format —
a nested JSON structure — for description fields. Plain strings are
rejected with 400. A small _adf_paragraph helper builds the minimum
viable ADF structure for a one-paragraph description.

Why per-project issue-type lookup? Team-managed (next-gen) projects
scope issue types per-project, not globally. The script discovers each
project's available issue type IDs at runtime instead of assuming.

Run:
    uv run python scripts/seed_jira.py --seed

Auth: HTTP Basic with JIRA_EMAIL as username, JIRA_TOKEN as password.
Reads JIRA_URL/JIRA_EMAIL/JIRA_TOKEN from .env (same config layer as
the GitLab side).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import httpx

from gitlab_mcp.config import get_settings


# ---------------------------------------------------------------------------
# Seed data — three projects' worth of realistic issues.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeedIssue:
    """One issue to create in a project."""
    project_key: str
    summary: str
    description: str
    issue_type: str  # "Bug" / "Task" / "Story" — resolved to ID per project


SEED_ISSUES: list[SeedIssue] = [
    # --- Acme Platform (SCRUM) — backend service work --------------------
    SeedIssue(
        project_key="SCRUM",
        issue_type="Bug",
        summary="Login fails on password reset for SSO users",
        description=(
            "Users who signed up via SSO and then triggered a password "
            "reset are unable to log back in. The reset flow creates a "
            "local password, but the SSO link is broken in the process. "
            "Repro: sign up via Google SSO, then click 'forgot password' "
            "from the login page."
        ),
    ),
    SeedIssue(
        project_key="SCRUM",
        issue_type="Bug",
        summary="Payment webhook retries cause duplicate charges",
        description=(
            "When the payment processor retries a webhook delivery, we "
            "re-execute the charge logic instead of checking idempotency. "
            "Customers are seeing 2-3 charges for a single purchase. "
            "Need idempotency keys on the charge endpoint."
        ),
    ),
    SeedIssue(
        project_key="SCRUM",
        issue_type="Task",
        summary="Add idempotency keys to charge endpoint",
        description=(
            "Follow-up to the payment webhook duplicate-charges bug. "
            "Accept an Idempotency-Key header on POST /charges and store "
            "the result keyed by it for 24h. Subsequent requests with "
            "the same key return the original response without re-executing."
        ),
    ),
    SeedIssue(
        project_key="SCRUM",
        issue_type="Story",
        summary="Add SSO support for Microsoft accounts",
        description=(
            "Customers using Microsoft 365 want to sign in with their work "
            "accounts. Add Microsoft as an OAuth provider alongside Google. "
            "Should appear on the login screen with a 'Sign in with Microsoft' "
            "button."
        ),
    ),
    SeedIssue(
        project_key="SCRUM",
        issue_type="Task",
        summary="Migrate analytics pipeline to async writes",
        description=(
            "The analytics writer is currently synchronous on the request "
            "path, adding 50-200ms to every API call. Move it behind an "
            "async queue so request latency drops back to baseline."
        ),
    ),
    SeedIssue(
        project_key="SCRUM",
        issue_type="Bug",
        summary="Rate limiter incorrectly blocks legitimate burst traffic",
        description=(
            "The current per-user rate limiter uses a fixed 60-second window. "
            "Users doing a bulk import (legitimate use case) get blocked even "
            "though their average rate is well below the limit. Switch to a "
            "token-bucket algorithm."
        ),
    ),

    # --- Acme Mobile (AM) — mobile app issues ----------------------------
    SeedIssue(
        project_key="AM",
        issue_type="Bug",
        summary="Mobile app crashes on cold start when offline",
        description=(
            "If the user launches the app with no network connection, it "
            "crashes during the initial sync attempt instead of falling back "
            "to cached data. Affects both iOS and Android. Crash log "
            "attached in linked issue."
        ),
    ),
    SeedIssue(
        project_key="AM",
        issue_type="Bug",
        summary="Push notifications not delivered on iOS 17",
        description=(
            "After the iOS 17 release, push notifications stop arriving "
            "unless the app is in the foreground. Likely related to the "
            "new notification entitlement requirements. Need to update "
            "the entitlements file and re-submit."
        ),
    ),
    SeedIssue(
        project_key="AM",
        issue_type="Story",
        summary="Add dark mode toggle to settings",
        description=(
            "Users have asked for a dark mode toggle in the app settings. "
            "Should follow system setting by default with manual override. "
            "All screens need to be audited for contrast in dark mode."
        ),
    ),
    SeedIssue(
        project_key="AM",
        issue_type="Task",
        summary="Reduce APK size below 50MB",
        description=(
            "The Android APK is currently 73MB, which is hurting install "
            "conversion in markets with slow connections. Profile assets "
            "and dependencies; aim for under 50MB without removing features."
        ),
    ),
    SeedIssue(
        project_key="AM",
        issue_type="Bug",
        summary="App crashes when scrolling long order history",
        description=(
            "Users with 500+ orders see the app crash when scrolling the "
            "history view. Memory pressure spike suggests we're loading all "
            "rows instead of virtualizing. Switch to a lazy-loaded list."
        ),
    ),

    # --- Acme Infrastructure (AI) — platform / DevOps work ---------------
    SeedIssue(
        project_key="AI",
        issue_type="Task",
        summary="Migrate CI from Jenkins to GitHub Actions",
        description=(
            "Jenkins is increasingly painful to maintain — flaky agents, "
            "outdated plugins, no PR-level checks. Migrate all pipelines "
            "to GitHub Actions. Keep Jenkins read-only for 30 days after "
            "cutover for reference."
        ),
    ),
    SeedIssue(
        project_key="AI",
        issue_type="Story",
        summary="Set up centralized log aggregation",
        description=(
            "Logs are scattered across 12 services with no unified search. "
            "Stand up an ELK or Loki stack so on-call can grep across all "
            "services from one place. Retention: 30 days hot, 90 days cold."
        ),
    ),
    SeedIssue(
        project_key="AI",
        issue_type="Bug",
        summary="Kubernetes pods evicted during deploys",
        description=(
            "Rolling deploys occasionally evict healthy pods due to memory "
            "pressure on nodes. Resource requests/limits need to be tuned "
            "per service. Also consider PodDisruptionBudgets for critical "
            "deployments."
        ),
    ),
    SeedIssue(
        project_key="AI",
        issue_type="Task",
        summary="Rotate production database credentials quarterly",
        description=(
            "Security audit flagged that DB credentials haven't been rotated "
            "in 14 months. Set up an automated rotation process via Vault, "
            "with zero-downtime cutover. Schedule: every 90 days."
        ),
    ),
    SeedIssue(
        project_key="AI",
        issue_type="Bug",
        summary="Backup job silently fails on weekends",
        description=(
            "The nightly database backup hasn't run successfully on a weekend "
            "since the cron migration. No alerts fire because the job exits 0 "
            "even when the upload step fails. Fix the exit code and add alerting "
            "on backup age."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Atlassian Document Format helpers.
# ---------------------------------------------------------------------------


def _adf_paragraph(text: str) -> dict:
    """Build the minimal ADF document for a single paragraph of plain text.

    Jira Cloud REST API v3 rejects plain-string descriptions with 400.
    The smallest valid description is a doc with one paragraph containing
    one text node — that's what this returns.
    """
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Jira REST client (just enough for seeding — not the connector).
# ---------------------------------------------------------------------------


class JiraSeeder:
    """Minimal Jira REST client used only by this seed script.

    Deliberately separate from the (forthcoming) read-only connector
    client. Uses HTTP Basic auth with email + API token.
    """

    def __init__(self, base_url: str, email: str, token: str) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=(email, token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JiraSeeder":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- API operations --------------------------------------------------

    def list_projects(self) -> list[dict]:
        r = self._client.get("/rest/api/3/project/search")
        r.raise_for_status()
        return r.json()["values"]

    def list_issue_types_for_project(self, project_id_or_key: str) -> list[dict]:
        """Return issue types valid for this project.

        Team-managed projects have per-project issue type schemes, so a
        global /issuetype lookup doesn't give correct IDs. /project/{key}
        with ?expand=issueTypes does.
        """
        r = self._client.get(
            f"/rest/api/3/project/{project_id_or_key}",
            params={"expand": "issueTypes"},
        )
        r.raise_for_status()
        return r.json().get("issueTypes", [])

    def search_existing(self, project_key: str, summary: str) -> list[dict]:
        """Return issues in this project whose summary matches exactly.

        Used for idempotency: skip creating if already present. JQL exact-
        match on summary is done via 'summary ~ \"...\"' (text search,
        which is fuzzy) followed by an in-Python equality filter.
        """
        # Escape JQL-significant characters in the summary.
        escaped = summary.replace('"', '\\"')
        jql = f'project = "{project_key}" AND summary ~ "{escaped}"'
        r = self._client.get(
            "/rest/api/3/search",
            params={"jql": jql, "fields": "summary", "maxResults": 50},
        )
        r.raise_for_status()
        issues = r.json().get("issues", [])
        return [i for i in issues if i["fields"]["summary"] == summary]

    def create_issue(
        self,
        project_key: str,
        issue_type_id: str,
        summary: str,
        description: str,
    ) -> dict:
        payload = {
            "fields": {
                "project": {"key": project_key},
                "issuetype": {"id": issue_type_id},
                "summary": summary,
                "description": _adf_paragraph(description),
            }
        }
        r = self._client.post("/rest/api/3/issue", json=payload)
        if r.status_code >= 400:
            # Surface Jira's structured error response for easier debugging.
            print(f"  ERROR creating issue: {r.status_code} {r.text}", file=sys.stderr)
            r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Seeding logic.
# ---------------------------------------------------------------------------


def _resolve_issue_type_id(
    issue_types: list[dict], wanted_name: str
) -> str | None:
    """Find the ID of an issue type by name (case-insensitive)."""
    wanted = wanted_name.casefold()
    for it in issue_types:
        if it.get("name", "").casefold() == wanted:
            return str(it["id"])
    return None


def seed(seeder: JiraSeeder) -> tuple[int, int, int]:
    """Create the SEED_ISSUES. Returns (created, skipped, failed)."""
    # Build a per-project map of "issue type name (lowercased) -> id".
    issue_types_by_project: dict[str, list[dict]] = {}
    for project_key in {issue.project_key for issue in SEED_ISSUES}:
        try:
            issue_types_by_project[project_key] = seeder.list_issue_types_for_project(
                project_key
            )
        except httpx.HTTPStatusError as exc:
            print(
                f"  ERROR: could not load issue types for project '{project_key}': "
                f"{exc.response.status_code} {exc.response.text[:200]}",
                file=sys.stderr,
            )
            issue_types_by_project[project_key] = []

    created = skipped = failed = 0

    for issue in SEED_ISSUES:
        prefix = f"[{issue.project_key:<6}]"
        issue_type_id = _resolve_issue_type_id(
            issue_types_by_project.get(issue.project_key, []),
            issue.issue_type,
        )
        if not issue_type_id:
            available = [
                it.get("name") for it in issue_types_by_project.get(issue.project_key, [])
            ]
            print(
                f"  {prefix} SKIP '{issue.summary[:50]}' — issue type "
                f"'{issue.issue_type}' not available "
                f"(project has: {available})"
            )
            failed += 1
            continue

        # Idempotency check.
        try:
            existing = seeder.search_existing(issue.project_key, issue.summary)
        except httpx.HTTPStatusError:
            existing = []  # if search fails, try create — duplicate is harmless
        if existing:
            print(f"  {prefix} SKIP '{issue.summary[:50]}' — already exists")
            skipped += 1
            continue

        try:
            result = seeder.create_issue(
                project_key=issue.project_key,
                issue_type_id=issue_type_id,
                summary=issue.summary,
                description=issue.description,
            )
            print(f"  {prefix} CREATE {result['key']} — '{issue.summary[:50]}'")
            created += 1
        except httpx.HTTPStatusError:
            failed += 1

    return created, skipped, failed


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed the demo Jira workspace with realistic issues.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Actually create issues (omit to run in dry-mode and just list projects).",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.jira_url or not settings.jira_email or not settings.jira_token:
        print(
            "ERROR: JIRA_URL, JIRA_EMAIL, and JIRA_TOKEN must all be set in .env",
            file=sys.stderr,
        )
        return 1

    with JiraSeeder(
        base_url=str(settings.jira_url),
        email=settings.jira_email,
        token=settings.jira_token.get_secret_value(),
    ) as seeder:
        # Always list projects first — sanity check that credentials work.
        try:
            projects = seeder.list_projects()
        except httpx.HTTPStatusError as exc:
            print(
                f"ERROR: failed to list projects: {exc.response.status_code} "
                f"{exc.response.text[:200]}",
                file=sys.stderr,
            )
            return 1

        print(f"Found {len(projects)} project(s):")
        for p in projects:
            print(f"  - {p['key']:<6} {p['name']}")
        print()

        if not args.seed:
            print("Dry run — pass --seed to actually create issues.")
            return 0

        print(f"Seeding {len(SEED_ISSUES)} issues across "
              f"{len({i.project_key for i in SEED_ISSUES})} projects...")
        print()
        created, skipped, failed = seed(seeder)
        print()
        print(f"Done. created={created} skipped={skipped} failed={failed}")
        return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
