# Office streams + NAS revision: validation record

**Status: staging candidate.** This record applies to the source delivered in `signage-portal-streams-nas.zip`. It separates executed checks from deployment templates and untested hardware behavior. The archive's `MANIFEST.sha256` identifies the packaged files.

## Executed checks

| Check | Result | Evidence / scope |
|---|---|---|
| Python regression suite | **102 passed, 1 skipped** | [`streams-nas-pytest.txt`](evidence/streams-nas-pytest.txt), [JUnit XML](evidence/streams-nas-pytest.xml) |
| Admin interface checks | Passed; no JavaScript page errors | [`streams-nas-browser.json`](evidence/streams-nas-browser.json) |
| Player state/lifecycle checks | Passed; no JavaScript page errors | [`streams-nas-player.json`](evidence/streams-nas-player.json) |
| Python compilation | Passed | `python -m compileall -q web worker scripts` |
| JavaScript syntax | Passed | `node --check web/app/static/player.js`; module syntax check of `admin.js` |
| Configuration file parsing | Passed | Python JSON/YAML parsing; **not** Docker Compose merge/runtime validation |
| systemd timer calendar expression | Parsed successfully | `*-*-* *:*:00,30`; full service validation could not resolve absent Docker service/executable |

The skipped test exercises migration from a legacy bcrypt password hash. The local environment lacked the bcrypt wheel. Argon2 and the current session/authentication paths were exercised. See [`streams-nas-environment.json`](evidence/streams-nas-environment.json) for the actual test environment. It is not the newly pinned production dependency set and is not a substitute for a clean Python 3.12/image build.

### Regression coverage added in this revision

The tests exercise permanent channel keys across stream renames and source changes; display reassignment without URL changes; immutable/retired display slugs; non-reused identifiers; and rejection of administrative routes and content mutations at the playback-only hostname. They cover exact approved source origins, forbidden protocols/credentials, separate admin/player/source hostnames, source approval withdrawal, and player-specific CSP without relaxing the admin policy.

NAS tests exercise path confinement, symlink rejection, marker checks, rejection of a local filesystem where a network share is required, read-only export restrictions, bounded queues, import snapshots, no-overwrite export, interrupted-acknowledgement recovery, and durable job records. A **fresh Python process** reopens the same database and verifies saved channel keys, assignments, source configuration and a queued transfer. The conversion worker's startup guard is checked before it can create missing work paths. SQLite connections explicitly select `synchronous=FULL`.

These checks use temporary local fixtures and controlled filesystem mocks where needed. They are **not** evidence of SMB/NFS kernel behavior, physical power-loss durability, an actual host reboot, or a successful mount on the user's NAS.

### Admin browser checks

The actual administrator HTML/CSS/JavaScript ran in Chromium through `scripts/ui-dom-check.py`, with an **offline DOM harness connected to the local application's real HTTP API**. Checks included stream creation, dialog focus containment, no mobile horizontal overflow, failed-request recovery, URL source creation, shared-folder browsing, NAS-fixture import, and original-file export. Screenshots in [`streams-nas-ui/`](evidence/streams-nas-ui/) use fictional staging content.

Normal browser navigation to the local server was blocked by the execution environment. Accordingly this harness does **not** prove browser network behavior, TLS, reverse-proxy rules, actual response-header CSP enforcement, CORS, media decoding, or physical display compatibility. Server response headers and authorization were separately checked by API tests.

### Player checks

`scripts/player-state-check.py` loads the delivered player code with a mock manifest and mock HLS engine. It checks permanent-channel endpoint selection, keeping a single live feed alive instead of recreating/rewinding it on each loop, nested HLS origin checks, HLS cleanup on source changes, iframe sandbox flags, and clearing content for a deleted channel. It does **not** decode HLS/video or contact a live source.

## Not executed: release/deployment gates

1. **Clean dependency installation and image build.** Package/release downloads were unavailable and Docker was absent. Resolve/install the pinned dependencies in a clean environment, run tests there, build both images, and scan the images and transitive dependencies. The HLS build stage must succeed rather than being skipped.
2. **HLS binary acquisition and real playback.** The source archive includes the checksum-verifying HLS.js 1.7.3 installer, not compiled HLS.js. The release archive digest was verified against upstream release metadata, and an incorrect archive is rejected in tests. The actual release archive could not be downloaded here; successful extraction/build and playback with that binary are unverified. Use the documented network build or exact offline archive input.
3. **Actual NAS mounts and host reboot.** Adapt and validate `fstab`, markers, UID/ACL mapping, host preflight, Compose overlays and systemd units on the deployment host. Test a normal reboot, NAS-late startup, unavailable/wrong share, an interrupted import/export, and recovery after a true unmount/remount. Templates have not been installed on the user's host.
4. **TLS/proxy/device network behavior.** Validate the supplied Nginx example, certificates, stable DNS/DHCP addressing, allowed hosts, precise proxy peer trust, playback-only route filtering, VLAN rules and external-source CORS. The proxy configuration was not executed here.
5. **Physical displays.** Run Cast Pro Web mode and other chosen players through uploaded images/videos, schedules, live feeds, reconnects, audio policy and overnight playback. No Cast Pro was available. This is browser-delivered signage, not a universal encoded HLS output or a frame-locked broadcast system.
6. **Optional relay.** The separate MediaMTX deployment is an operator-configured example; it was not started. Choose and scan a pinned image, restrict ingress/egress, verify the input codecs, HLS output, CORS, TLS, credentials handling and resource limits. Transcoding and arbitrary RTSP/RTMP/SRT input from the portal UI are not implemented.
7. **UniFi control API.** No supported Connect Web-mode assignment endpoint was verified or implemented. No UniFi console was contacted or modified. Permanent per-display URLs already allow routine reassignment inside the portal without that integration.

## Operational limits that remain

Persistence is not uninterrupted playback or backup. The saved playlist is not a complete offline media cache. A NAS outage can interrupt media access; hard-mount kernel I/O may stall; a true remount may require recreating containers to obtain the current mount. Startup checks intentionally reject missing or wrong storage rather than create local substitutes. Durable failed transfers stay visible and can be retried; they are not promised to resume at the last byte.

Public player URLs are view-only, **not confidential access control**. Anyone able to reach them can view their content. Restrict the playback network as appropriate; do not embed secrets in source URLs. There is no claim of perfect security or production certification.

## Earlier evidence

`AUDIT.md`, `TESTING.md`, the older non-prefixed evidence files, and `docs/previews/` describe the previous modernization pass. They remain for continuity. Their conversion checks were not all rerun in this extension; the current authoritative regression/browser records are the `streams-nas-*` files linked above.

Read [PERSISTENCE.md](PERSISTENCE.md), [STREAMS-AND-SOURCES.md](STREAMS-AND-SOURCES.md), and [UPGRADING.md](UPGRADING.md) before deployment. Preserve existing data paths and take a consistent database/media backup before replacing a running installation.
