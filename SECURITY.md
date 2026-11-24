# Security Policy

We take the security of this project seriously. Thank you for taking the time to report
vulnerabilities responsibly.

## Supported versions

Security fixes are released for the current release line only. Upgrade to the latest release
before reporting; older lines do not receive patches.

| Version           | Supported |
| ----------------- | --------- |
| 0.28.x            | Yes       |
| 0.27.x and older  | No        |

## Reporting a vulnerability

Report vulnerabilities through **GitHub Private Vulnerability Reporting**, which is enabled on
this repository. It is the only reporting channel.

1. Open the repository's **Security** tab.
2. Select **Advisories** in the sidebar.
3. Click **Report a vulnerability**.
4. Describe the issue, the affected version, and the steps to reproduce it.

This opens a private advisory visible only to you and the maintainers, where the issue can be
discussed, a fix tested, and disclosure coordinated. Please do not open a public issue, pull
request or discussion for a suspected vulnerability, and do not disclose it publicly until a fix
has been released.

## What to expect

- **Acknowledgment:** we aim to acknowledge a report within **48 hours**.
- **Assessment:** we aim to confirm or decline it within **5 business days**.
- **Fix and disclosure:** a confirmed issue is fixed in a private branch, shipped in a release, and
  published as a GitHub Security Advisory crediting the reporter (unless you ask to stay anonymous).

## Threat model

Knowing what scryme does and does not defend against helps both operators and reporters.

**scryme is a single-user application with no accounts.** One deployment holds one collection.
There are no users, roles or per-user permissions: anyone the app lets in can do everything.

- **Access control is one shared secret, and it is off by default.** When `SCRYME_API_TOKEN` is
  set, the web UI (through a `/login` page) and the JSON API both require it. When it is not set,
  **anyone who can reach the port has full access** — including changing or deleting the
  collection and using any configured AI provider key. That default suits an instance only its
  owner can reach.
- **So keep an unauthenticated instance unreachable.** Bind it to localhost (`SCRYME_BIND=127.0.0.1`
  with the bundled Docker Compose stack) or put it behind a reverse proxy that authenticates.
  If anyone else can reach the port — a shared LAN, a VPS, a tunnel — set `SCRYME_API_TOKEN`.
  When the instance has a DNS name, also set `SCRYME_ALLOWED_HOSTS` so it only answers to that
  name (this stops DNS-rebinding attacks through a visitor's browser).
- **TLS is not provided.** The app and the bundled nginx speak plain HTTP; terminate TLS at your
  own proxy if traffic leaves a network you trust.
- **Desktop LAN sharing is for trusted networks.** By default the desktop app serves
  only the machine it runs on. Turning on LAN sharing opens it, over plain HTTP, to every device on
  the network (optionally behind an access code); use it at home, not on public or shared Wi-Fi.
- **Read-only demo mode.** With `SCRYME_READ_ONLY=true` (used by the public demo) the server
  refuses requests that would change stored data and disables the AI features. Everything in a read-only
  instance is still readable by anyone who can reach it — do not put private data in one.
- **Desktop builds are unsigned and do not install updates by themselves.** The installers are
  not code-signed or notarized, so your operating system cannot vouch for where they came from:
  download them only from this repository's GitHub Releases page. The app does not silently
  download and install new versions; updating is something you choose to do.
- **The database is not a security boundary.** The bundled Compose stack does not publish
  PostgreSQL, and its password defaults to a well-known value unless you set `POSTGRES_PASSWORD`.
  Anyone with access to the database has the whole collection; with the data volume as well, they
  also have the key file that decrypts the stored AI provider key.

Reports are in scope when they break one of these guarantees — for example bypassing
`SCRYME_API_TOKEN`, writing to a read-only instance, reaching another site's data through the
app (SSRF), cross-site request forgery, script injection, or code execution through an import or
restore. "An instance with no token set, exposed to the internet, can be modified by anyone" is
the documented default, not a vulnerability.

Deployment guidance is in the
[self-hosting](https://docs.scryme.app/getting-started/self-hosting/) and
[configuration](https://docs.scryme.app/getting-started/configuration/) docs.

## Automated checks

These run on the repository today:

- **CodeQL** — static analysis of the Python backend and the JavaScript desktop shell on every
  pull request and push to `main`, plus a weekly scheduled scan (`.github/workflows/codeql.yml`).
- **pip-audit** — fails the test workflow when a pinned backend dependency has a known
  vulnerability (`.github/workflows/test.yml`).
- **Trivy** — scans each published container image for HIGH/CRITICAL OS and library
  vulnerabilities and reports them to code scanning; it does not block publishing
  (`.github/workflows/publish-image.yml`).
- **Dependabot** — weekly update pull requests for the backend and docs (pip), the desktop app
  (npm), GitHub Actions, and container base images, plus security updates (`.github/dependabot.yml`).
  GitHub Actions are pinned to full commit SHAs.
- **SonarQube** — code-quality and coverage analysis with a quality gate, run from a self-hosted
  Jenkins (`Jenkinsfile`, `sonar-project.properties`). It complements the GitHub Actions checks
  and is not required for merging.
