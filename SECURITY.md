# Security Policy

We take the security of this project seriously. Thank you for taking the time to report any vulnerabilities responsibly.

## Supported Versions

We actively release security patches for the following versions:

| Version   | Supported          |
| --------- | ------------------ |
| v0.26.x   | :white_check_mark: |
| < v0.26.0 | :x:                |

## Reporting a Vulnerability

### Primary Method (Preferred)
Because **Private Vulnerability Reporting** is enabled on this repository, please report all security vulnerabilities directly through GitHub:

1. Navigate to the main page of the repository.
2. Click on the **Security** tab.
3. Select **Advisories** on the left sidebar.
4. Click **Report a vulnerability**.
5. Fill out the form with detailed steps to reproduce the issue and submit.

This creates a private security advisory where we can discuss the issue, test fixes, and coordinate a public disclosure once a patch is ready.

### Alternative Method
If you encounter issues using GitHub's vulnerability reporting interface, you can contact the maintainers directly via email: `your-email@example.com`.

## What to Expect

- **Acknowledgment:** We aim to acknowledge receipt of your report within **48 hours**.
- **Assessment:** We will investigate and confirm the report within **5 business days**.
- **Fix & Disclosure:** If validated, we will work on a fix in a private branch, issue a release, and publish a CVE/Advisory giving appropriate credit to the reporter (unless anonymity is requested).

Please do not publicly disclose the vulnerability until a fix has been officially released.

## Automated Security & Quality Checks

To help catch vulnerabilities and quality regressions early, this project utilizes:
- **GitHub Code & Secret Scanning**
- **Dependabot Alerts & Automated Updates**
- **SonarQube SAST & Code Quality Analysis** (executed via Jenkins CI)
