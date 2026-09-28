# Security

This is a staging candidate. Passing tests and documented isolation do not establish complete security or certify a production deployment. Current executed checks and outstanding gates are in [docs/REPOSITORY-VALIDATION.md](docs/REPOSITORY-VALIDATION.md).

## Reporting

Contact the repository owner privately before posting a vulnerability publicly. Use GitHub's private vulnerability-reporting channel when the owner has enabled it; this package does not claim that remote setting is enabled. Do not include production `.env` files, NAS credentials, tokens, cookies, personal information, internal content or live database dumps in issues. Redact logs and use minimal synthetic reproductions. No response-time or security-support SLA is promised.

## Deployment boundary

Public playback URLs provide view-only content, not per-viewer authentication. Keep public/unclassified content appropriate to the network where it is reachable. Protect the administrator origin with TLS and authentication. Keep upstream secrets out of browser-loaded URLs. All local admin accounts currently have full access.

Maintain the isolated network-less worker, restrictive upload/converter contracts, separate playback hostname and host-managed NAS mounts. Never run the portal privileged or bind the Docker socket into it. Keep database and secrets on persistent local storage. Audit dependency pins and scan OS packages/images before deployment and on an ongoing maintenance schedule.

The initial GitHub publisher uses an exact package manifest and rejects known sensitive paths. It is a packaging safeguard, not a substitute for reviewing content for secrets. No cloud deployment or GitHub credential is bundled.
