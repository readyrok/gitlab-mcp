# Setup

This document captures how the test environment was provisioned. It exists
both as a record for me and as evidence to anyone reading the repo that the
demo data is reproducible — not handcrafted screenshots.

## Prerequisites

- A free GitLab.com account.
- Python 3.11+.
- An Anthropic API key (only needed when running the agent, not the seed script).

## 1. Create the seeding token

This token is **only for seeding**. The MCP server itself will use a separate,
narrower token (see step 4).

1. Go to <https://gitlab.com/-/user_settings/personal_access_tokens>.
2. Name: `mcp-gitlab-seeder`.
3. Expiration: 7 days. (We delete this token as soon as seeding is done.)
4. Scope: `api` (writes — needed to create projects, issues, MRs).
5. Copy the token.

## 2. Configure the script

```bash
cd scripts
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp ../.env.example ../.env
# Edit ../.env and paste your seeder token into GITLAB_TOKEN
```

## 3. Run it

Smoke-test first, no API calls:

```bash
python seed_gitlab.py --seed --dry-run
```

Then for real:

```bash
python seed_gitlab.py --seed
```

If you need to start over (the script is idempotent in spirit but not in
implementation — re-running `--seed` will create duplicate projects with
suffixed names):

```bash
python seed_gitlab.py --wipe --seed
```

## 4. Create the runtime token

Once seeding is done, **revoke the seeder token** and create a new one:

1. Same URL as before.
2. Name: `mcp-gitlab-server-dev`.
3. Expiration: 30 days.
4. Scope: `read_api` only.
5. This is the token the MCP server uses. It cannot write or destroy anything.

This two-token setup is the principle of least privilege in practice — and
it's a real talking point for the interview.

## 5. The seeded world

Three projects under your namespace, all prefixed `acme-`:

| Project              | Issues (open / closed) | MRs (open / merged / closed) | Pipelines |
|----------------------|------------------------|------------------------------|-----------|
| `acme-order-service` | 7 / 5                  | 2 / 2 / 1                    | one pass, one fail |
| `acme-inventory-api` | 4 / 2                  | 1 / 1 / 0                    | passing |
| `acme-web-frontend`  | 3 / 1                  | 1 / 0 / 0                    | none |

The asymmetry is intentional: it gives the agent something to compare across
projects in demos.

## 6. Project IDs

Fill these in after running `--seed` (the script prints them in a summary block):

- `acme-order-service` — id=`<81913181>`
- `acme-inventory-api` — id=`<81913196>`
- `acme-web-frontend`  — id=`<81913213>`

## Jira connector setup

The Jira connector runs against a real Atlassian Cloud site.

1. Create a free Atlassian Cloud account and site at
   https://www.atlassian.com/software/jira/free (free tier, up to 10
   users). Note your site URL: `https://<your-site>.atlassian.net`.

2. Create three projects (any names; the demo uses Acme Platform,
   Acme Mobile, Acme Infrastructure). Team-managed (next-gen) is fine.

3. Create an API token at
   https://id.atlassian.com/manage-profile/security/api-tokens and
   copy it.

4. Add to `.env`:
    JIRA_URL=https://<your-site>.atlassian.net
    JIRA_EMAIL=<your-atlassian-account-email>
    JIRA_TOKEN=<your-api-token>

5. Seed demo issues:

```bash
   uv run python scripts/seed_jira.py          # dry run — lists projects
   uv run python scripts/seed_jira.py --seed   # creates ~16 issues
```

Note: Atlassian API tokens are account-scoped, not permission-scoped
(unlike GitLab PATs). The connector's read-only guarantee is enforced
in code — the Jira client only performs GET requests. See DESIGN.md §12.
