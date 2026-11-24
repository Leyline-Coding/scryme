# Quality & Security

scryme is developed with a layered set of automated checks so that changes are validated for
correctness, code quality, dependency health, and security before — and after — they reach `main`.
This page summarizes those measures.

## Change workflow

- **Branch per change** — `feat/*` / `fix/*` branches open a pull request into `main`.
- **Protected `main`** — a branch ruleset requires a pull request (with review) before merge.
- **Green CI required** — the GitHub Actions checks below must pass before a PR can be merged.
- **Migrations reviewed** — schema changes ship as Alembic migrations alongside the code that needs
  them.

## Continuous integration (GitHub Actions)

Every pull request and push to `main` runs, on GitHub-hosted runners:

| Check | What it does |
| --- | --- |
| **Lint** | `ruff check src tests` — style and common-bug lints |
| **Tests + coverage** | the full `pytest` suite (400+ tests) against a real PostgreSQL 16 service, emitting `coverage.xml` |
| **Dependency audit** | `pip-audit` fails the build if a dependency has a known vulnerability |
| **CodeQL** | GitHub's static analysis (SAST) for both Python and JavaScript/TypeScript, on every PR plus a weekly scheduled scan |

Published container images are additionally scanned:

| Check | What it does |
| --- | --- |
| **Trivy image scan** | scans each released image for OS/library vulnerabilities (HIGH/CRITICAL), reporting to the repository's Security tab |

## Dependency & supply-chain hygiene

- **Pinned dependencies** — `requirements.txt` pins exact versions; the container image builds from
  those pins for reproducibility.
- **Dependabot** — opens weekly update PRs for the backend (pip), the docs toolchain, the
  desktop app (npm), the GitHub Actions themselves, and container images (Dockerfiles, Compose
  files and the demo's Kubernetes manifests), and opens **security updates** promptly when an
  advisory affects a pinned dependency.
- **Pinned GitHub Actions** — every action is referenced by full commit SHA (with the release in a
  trailing comment), so a retagged or compromised action release cannot change what CI runs.
  Workflows default to a read-only token; only the jobs that publish get write access.
- **pip-audit gate** — because the audit runs in CI (above), a vulnerable dependency blocks the
  merge rather than shipping silently.

## Static analysis & quality gate

Beyond CodeQL, the repository ships a **SonarQube** pipeline (`Jenkinsfile` +
`sonar-project.properties`) that runs the test suite with coverage and then a SonarQube analysis
gated on a **quality gate** (bugs, code smells, coverage, duplication). This runs on a self-hosted
Jenkins and complements — it does not replace — the required GitHub Actions gate.

## Repository security features

- **Secret scanning** and **push protection** are enabled, so credentials are detected in history
  and blocked from being pushed.
- **Dependabot alerts** and **automated security fixes** are enabled at the repository level.
- Findings from CodeQL and Trivy surface in the repository's **Security → Code scanning** tab.

## Runtime & data safety

- **Read-only mode** — `SCRYME_READ_ONLY=true` refuses all mutations and disables the AI features
  (used for the public demo), so a publicly reachable instance can't be modified.
- **Access control** — scryme has no accounts; `SCRYME_API_TOKEN` protects the web UI and API when
  set. See [Securing your install](../getting-started/self-hosting.md#securing-your-install) and the
  threat model in [`SECURITY.md`](https://github.com/Leyline-Coding/scryme/blob/main/SECURITY.md).
- **Scryfall API compliance** — the ingestion client sends a descriptive `User-Agent`/`Accept`,
  stays under Scryfall's rate limit, backs off on `429`, and uses cached bulk downloads (see
  [Architecture](architecture.md)).
- **Backups** — a portable JSON [backup & restore](../features/backup.md) (optionally encrypted, and
  schedulable to disk) protects your collection data.
- **Non-root container** — the production image runs as a non-root user with a healthcheck.

## Reporting a vulnerability

If you believe you've found a security issue, please report it privately through the repository's
**Security → Advisories → Report a vulnerability** form (GitHub Private Vulnerability Reporting)
rather than opening a public issue. Details are in
[`SECURITY.md`](https://github.com/Leyline-Coding/scryme/blob/main/SECURITY.md).
