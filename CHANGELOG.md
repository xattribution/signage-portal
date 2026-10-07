# Change log

## Unreleased — quick start

`docker compose up -d` now runs with no `.env`, using Docker volumes and port 51480. A first-launch setup page creates the first administrator and names the workspace. A new Settings page edits the workspace name, clock format, seconds per slide, render size and access hostnames; values set in the environment take precedence and show as locked. Cookies default to `COOKIE_SECURE=auto`, and an empty `ALLOWED_HOSTS` accepts IP-address hosts plus hostnames listed in Settings. Host paths and NAS storage moved to `docker-compose.host-storage.yml`. See `docs/UPGRADING.md` before updating an existing installation.

## Unreleased — repository preparation and workspace freshness

Replaced the global “Connected” label/LED with a neutral Refresh action. Added explicit failure/offline notices, last-successful-refresh time, retry and focus recovery. Invalid successful-HTTP responses no longer masquerade as fresh workspace data. Existing cached state is retained on refresh failure.

Consolidated installation, operation, GitHub-publishing and status documentation. Added a private-only initial-publish helper, package-validation tests, CI configuration and repository hygiene files. The remote repository has not been created from this environment; see `docs/GITHUB.md`.

No backend route/schema, conversion contract, player URL, saved stream assignment, NAS policy or UniFi integration changed.

## Earlier source milestones

The supplied snapshots added unified top navigation; stream overlays, graphics and transitions; permanent channels, approved sources and NAS persistence; and the earlier interface/authentication/conversion hardening pass. Their dated or environment-specific validation records remain in `docs/`. They are not retroactive claims that this package was deployed or that a physical Cast Pro was tested.
