# Test evidence and release gates

This file separates tests actually executed during the revision from checks still required on the deployment host. Successful local tests are not a production security certification.

## Executed

### Python regression suite

**58 passed, 1 skipped.** Run against the revised application using temporary isolated databases/media directories, not the sample or a production database. The final JUnit XML and textual result are in `docs/evidence/`.

Covered cases include:

- Login CSRF/origin/fetch-metadata guards; malformed/unapproved Host headers; production HTTPS enforcement; wildcard proxy rejection; oversized Content-Length handling; secure-header policy; redacted validation errors.
- Opaque session storage, logout revocation, password-reset revocation, expiry, Argon2id verification, whitespace preservation and concurrent persistent login throttling.
- Locally signed RSA Cloudflare-like JWTs: accepted required claims, missing claims, wrong audience, expired token, incorrect type, forged signature and email allowlist rejection. JWKS lookup was mocked; no live Cloudflare edge was contacted.
- Authorization before upload parsing, declared/streamed size caps, duplicate files, disguised media, immutable media paths, single-range 206 responses and path traversal rejection.
- Hostile worker output: symlinks, a directory symlink, FIFO, multiple hard links, HTML content, extra files, path traversal, malformed slide entries/manifests, stale generations and an aggregate output budget.
- New upload ID non-reuse; generation-specific retries; atomic display edits; strict request models; overview ETag and secret-field removal; exact rotation ordering; merged schedule validation; Off/Blank/Screensaver behavior; epoch-zero override/end-time boundaries.

**Skipped:** legacy bcrypt-to-Argon2id migration. The bcrypt wheel was unavailable in the local environment. The test exists and must run, without skipping, after installing the release dependencies.

### Real converter/ingest checks

The local supervisor launched resource-limited conversion jobs, and the running web service ingested their output:

| Input | Observed result |
|---|---|
| Six JPEG sample uploads | Six ready uploads, one rendered image each |
| Two-page PDF | Ready; two rendered pages |
| Two-slide PPTX | Ready; LibreOffice conversion produced two rendered pages |
| Two-second H.264/AAC MP4 | Ready; one video and thumbnail |

The local worker was configured with a 3072 MiB per-job address-space limit. These checks establish that the available conversion tools could handle those specific fixtures and complete the web/worker contract. They were **not** run in the supplied Docker images or in a separately verified network namespace/noexec mount configuration. Four-hour videos, 4K device playback, 500-page documents and cancellation/timeout under real production load remain unverified.

### Browser DOM/interaction checks

Chromium rendered the actual administrator HTML, CSS and JavaScript at desktop and 390-pixel mobile widths. No page-level JavaScript errors were observed. Native-dialog focus containment, Escape dismissal, stream creation, view navigation, mobile horizontal-overflow checks and failure/recovery messaging passed. Light/dark previews were captured with fictional sample content.

**Important harness limitation:** browser navigation/network access was restricted by the environment. The checks used an `about:blank` DOM, actual local app assets, embedded sample thumbnails and an HTTPX bridge to the running local API. Browser policy was not changed. This tests DOM behavior and the API-backed flow but is **not** an ordinary browser/network end-to-end test: browser cookie policy, imported-module delivery, real fetch/CORS/CSP enforcement and media decoding on the target device still require normal-origin testing.

JavaScript files also passed `node --check`; Python modules passed compilation checks. This is not a complete static-analysis, accessibility or penetration-test result.

## Exact environment distinction

The local test environment provided Python 3.13.5, FastAPI 0.128.2, Starlette 0.50.0, Uvicorn 0.48.0, python-multipart 0.0.29, Argon2-cffi 25.1.0, PyJWT 2.13.0, cryptography 46.0.4, Pillow 12.3.0, pytest 9.0.2, HTTPX 0.28.1 and Playwright 1.57.0. The recorded package list in `docs/evidence/local-environment.json` is the source for these values.

The delivered production requirements instead select **FastAPI 0.141.1, Starlette 1.7.0 and bcrypt 5.0.0**, alongside the other listed direct dependencies. Their upstream metadata/API signatures were inspected, but package download/installation was blocked here. Therefore the local passing suite is **not evidence that this updated dependency set has resolved or passed**. Docker targets Python 3.12 and Debian Bookworm, which also differs from the local Python interpreter.

There is no full transitive hash lock, image digest lock, completed pip-audit run, Debian CVE scan or SBOM attestation in this package. Do not infer one from version pins or security-oriented configuration.

## Required before deployment

Use a clean, network-enabled Python 3.12 environment to install `requirements-dev.txt`. Run `scripts/check.sh` and require that the bcrypt migration case does not skip. Resolve any installation or compatibility failures before advancing. Install pip-audit in that tool environment and run `scripts/release-check.sh`; inspect the actual dependency report and scan both built images, including OS packages.

Validate the reverse proxy and actual trusted peer addresses. Confirm plain HTTP administrator access is refused, HTTPS login works, the session cookie is Secure/HttpOnly/SameSite=Strict with the `__Host-` prefix, cross-origin writes fail and a captured old token fails after logout. Exercise ordinary browser module loading and the delivered CSP, not the offline rendering harness.

Inspect running container user IDs, mounts, rootfs, network isolation, capabilities, process/memory limits and health status. Confirm the worker cannot access the DB, secret key or rendered mount. Verify NAS ownership/quotas and that a missing NAS mount cannot silently become a host-local content directory. Try a slow/cancelled upload, a corrupt deck, timeout, worker restart and database/content restore.

Finally, test a physical Cast Pro: image and H.264/AAC decoding, autoplay/audio, synchronized streams, override start/end, reassignment, server outage/reboot and at least an overnight memory/playback run. Screenshots and a passing API suite cannot substitute for this acceptance step.

## Optional DOM harness reproduction

`scripts/ui-dom-check.py` is included to reproduce the constrained DOM/API check against a **disposable staging instance**. It creates and deletes a temporary stream and expects at least the seeded default streams/displays. Supply `SIGNAGE_TEST_USER`, `SIGNAGE_TEST_PASSWORD`, `--base-url`, `--media-root` (the staging rendered directory), `--output`, an installed `--browser` path and the explicit `--allow-staging-mutations` flag. Never point a test mutation harness at the live lobby installation. The portable wrapper's arguments were syntax-checked; the executed harness was the corresponding locally configured version described above.
