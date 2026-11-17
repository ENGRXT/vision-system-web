# Security Policy

## Supported versions
Security fixes are applied to the latest published version on the default
branch. No formal support window is currently established.

## Reporting a vulnerability
Please do not disclose vulnerabilities in public issues or pull requests.
Use GitHub's private vulnerability reporting feature for this repository, if
available. Include:
- A clear description and potential impact.
- Reproduction steps or a proof of concept that does not expose real data.
- Affected versions or commits.
- Any suggested mitigation.

Please allow maintainers reasonable time to investigate and coordinate a fix
before public disclosure.

## Deployment guidance
This project should not be exposed to untrusted networks without a security
review. Configure authentication, TLS termination, network access controls,
secret management, backups, and data-retention policies appropriate to your
environment. Example credentials and local-development defaults must never be
used in production.

Do not upload customer images, personal information, credentials, or
confidential operational data to public issues.