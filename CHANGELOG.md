# Change log

## Unreleased — repository preparation and workspace freshness

Replaced the global “Connected” label/LED with a neutral Refresh action. Added explicit failure/offline notices, last-successful-refresh time, retry and focus recovery. Invalid successful-HTTP responses no longer masquerade as fresh workspace data. Existing cached state is retained on refresh failure.

Consolidated installation, operation, GitHub-publishing and status documentation. Added a private-only initial-publish helper, package-validation tests, CI configuration and repository hygiene files. The remote repository has not been created from this environment; see `docs/GITHUB.md`.

No backend route/schema, conversion contract, player URL, saved stream assignment, NAS policy or UniFi integration changed.

## Earlier source milestones

The supplied snapshots added unified top navigation; stream overlays, graphics and transitions; permanent channels, approved sources and NAS persistence; and the earlier interface/authentication/conversion hardening pass. Their dated or environment-specific validation records remain in `docs/`. They are not retroactive claims that this package was deployed or that a physical Cast Pro was tested.
