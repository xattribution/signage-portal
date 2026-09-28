# Repository preparation: validation record

**Staging candidate; remote publication not executed.** This record applies to the repository-preparation/status revision, based on the supplied `signage-portal-topnav.zip`. Earlier evidence in this tree is historical and is not a substitute for these checks or release acceptance.

## Executed

| Check | Result | Evidence and boundary |
|---|---|---|
| Python regression suite | **183 passed, 1 skipped** | [Log](evidence/repository-pytest.txt), [JUnit](evidence/repository-pytest.xml). Includes inherited API/security/overlay/NAS tests and new initial-publish package checks |
| Navigation and status DOM checks | Passed; no page errors | [Results](evidence/repository-ui/navigation-results.json). Five views at 13 widths, keyboard/mobile behavior, failure/retry, browser offline/online events, malformed HTTP 200 and valid 304 handling |
| Focused freshness behavior | Passed; no page errors | [Results](evidence/repository-freshness.json). First-load failures/retry, automatic backoff recovery without a click, request abort deadline and rejection of initial 304 without cached state |
| Overlay UI with real in-process API | Passed; no page errors | [Results](evidence/repository-overlays/browser-results.json). Real FastAPI API with disposable storage, offline Chromium DOM, synthetic time/media transport |
| Python, JavaScript and shell syntax | Passed | Python compileall; Node syntax checks on admin/player/UI modules; shell syntax checks |
| Workflow/metadata syntax | Parsed locally | YAML parsing only; GitHub has not executed the workflow |
| Initial-publish file selection | Locally tested | Unit tests reject sensitive paths, symlinks, traversal, changed contents and duplicate manifest entries; unlisted runtime `.env` is not selected |

The skipped test is legacy bcrypt-password migration because bcrypt is absent locally. The actual package environment is recorded in [repository-environment.json](evidence/repository-environment.json). Local Python is 3.13 and the installed FastAPI/Starlette/CairoSVG versions differ from the declared release pins. This is **not** a clean target-Python-3.12 dependency installation.

The focused deadline check shortens the request timeout to 200 ms only inside a disposable test document to exercise the actual AbortController path. The shipped request deadline remains 15 seconds. Network errors and offline events are simulated; no real host network outage is claimed.

The overlay browser checks validate that the new overview guard accepts real API payloads, not just the demo adapter. Media transport, TLS, browser response-header enforcement and physical output remain outside that harness. Sample images are documentation fixtures, not office uploads.

## Scope of production changes

Administrator HTML/CSS/JavaScript and the shared UI icon helper changed. The persistent global Connected LED was removed; explicit stale-workspace warnings and retry remain. No server route, database schema, player URL, media converter, NAS mount policy or UniFi adapter changed.

Repository work adds organized guides, private-publish tooling, CI metadata, file exclusions and executable permissions for operator scripts. The CI uses verified upstream commit IDs for the checkout/Python/Node actions and a read-only repository token. No secrets, auto-deployment or registry publishing are configured. Adding the workflow is not evidence that CI passed on GitHub.

## Not executed

**GitHub creation and push:** the session's GitHub connector had read actions but no create/push action, and there was no authenticated CLI in the container. The prepared helper was checked locally, not used against a live GitHub account. No remote URL, branch protection or security-reporting setting was created or verified. See [GITHUB.md](GITHUB.md).

**Release gates remain open:** clean installation of declared dependencies; Docker/HLS asset build; dependency and OS-image scans; TLS/reverse-proxy and browser-network checks; actual NAS mount/reboot/outage/remount behavior; real Cast Pro/other-device decoding, live feeds, audio and overnight playback. No UniFi controller was contacted or modified.

The source SHA-256 manifest identifies the packaged file contents. It is neither a security audit nor a signed release attestation. Preserve current database/media paths, back up before upgrades, and follow [INSTALL.md](INSTALL.md), [PERSISTENCE.md](PERSISTENCE.md) and [UPGRADING.md](UPGRADING.md).
