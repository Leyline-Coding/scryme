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
- **Dependabot** — opens weekly, grouped update PRs for the backend (pip), the docs toolchain, the
  desktop app (npm), and the GitHub Actions themselves, and opens **security updates** promptly when
  an advisory affects a pinned dependency.
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

- **Read-only mode** — `SCRYME_READ_ONLY=true` disables all mutations (used for the public demo), so
  a publicly reachable instance can't be modified.
- **Scryfall API compliance** — the ingestion client sends a descriptive `User-Agent`/`Accept`,
  stays under Scryfall's rate limit, backs off on `429`, and uses cached bulk downloads (see
  [Architecture](architecture.md)).
- **Backups** — a portable JSON [backup & restore](../features/backup.md) (optionally encrypted, and
  schedulable to disk) protects your collection data.
- **Non-root container** — the production image runs as a non-root user with a healthcheck.

## Reporting a vulnerability

If you believe you've found a security issue, please open a
[GitHub issue](https://github.com/Leyline-Coding/scryme/issues) (or, for sensitive reports, a
private security advisory) rather than disclosing it publicly.
