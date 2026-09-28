# Overlay revision — validation record

**Release status: staging candidate.** This record applies to the `signage-portal-overlays` source archive, extending the earlier streams/NAS build. It does not claim installation on an office server or validation on a physical UniFi display.

## Executed checks

| Check | Result | Evidence and scope |
|---|---|---|
| Entire Python regression suite | **169 passed, 1 skipped** | [Verbose run](evidence/overlays-pytest.txt), [JUnit XML](evidence/overlays-pytest.xml). 67 new tests beyond the 102-test baseline. |
| New editor/player browser checks | Passed, no JavaScript page errors | [Browser results](evidence/overlays-ui/browser-results.json). Chromium DOM, real in-process FastAPI API and fictional raster fixtures. |
| Existing live-source player lifecycle checks | Passed, no JavaScript page errors | [Player results](evidence/overlays-player-state.json). Mock HLS engine; no actual HLS decode. |
| Standalone offline editor preview | Passed | [Preview checks](evidence/overlays-demo.json). Editor opens, draft settings can be saved/reopened/reset, uploads are explicitly disabled. |
| Python compilation and JavaScript syntax | Passed | [Syntax record](evidence/overlays-syntax.txt). These checks are not a clean dependency build. |

The skipped test is legacy bcrypt-password migration because the bcrypt wheel was unavailable in this environment. Argon2/session functionality remains covered by the rest of the suite.

### Backend and conversion coverage

The new tests exercise persisted settings/presets, immutable channel keys, unchanged content version under appearance edits, separate presentation schedule edges, expired/invalid times, time zones, numeric/text bounds, revision conflicts, authenticated writes, playback-only hostname blocking and public raster-only references. A fresh Python process reopens saved presentation data and verifies absolute deadlines rather than starting new timers.

PNG alpha preservation, metadata removal, GIF frame duration/animation preservation, self-contained SVG rasterization, hostile SVG rejection and output-ingest validation are tested with actual raster decoders. The tests reject script, linked/embedded resources, XML entities/DTDs, processing instructions, unsafe attributes and unsupported SVG elements. Pixel/frame-work and SVG complexity budgets are exercised. A graphic referenced by either a preset or a stream cannot be deleted.

**Three real worker supervisor subprocess runs** convert PNG, SVG and GIF fixtures, create heartbeat/output manifests and pass the result through web-side ingestion. They exercise the worker's subprocess/resource-limit path on the authoring host. They are not a Docker, seccomp, network-namespace or kernel-filesystem-isolation test. SVG rasterization in these runs used installed CairoSVG 2.8.2, not the new release pin.

A temporary NAS-like local folder exercises **As graphic** import, preservation of that choice across database initialization/recovery, SVG inclusion in confined browsing, the normal-import rejection of raw SVG, and export of the original. The existing NAS path confinement, mount guard, recovery and no-overwrite tests also remain in the full suite. These are not actual SMB/NFS mount or host-reboot tests.

### Browser checks

The real administrator modules and real overlay/player renderer run in Chromium. The following were exercised:

- unpublished edits/cancel, publishing and reopening a countdown, named preset saving and clearing stale dates on load;
- independent top/bottom rendering, visible transform-based ticker movement, reserved-space geometry, and proportional 4K ribbon height;
- plain-text handling of an HTML-looking message without injecting DOM nodes;
- retaining an existing image/video element when only the ribbon settings change;
- ribbon expiry and countdown completion after a synthetic clock jump with failed polls;
- reduced-motion ticker suppression, loading a normalized GIF, and switching a full-screen GIF to its still poster;
- actual transition keyframes/duration and cleanup of the outgoing element;
- Screensaver clock artwork, keyboard focus containment, mobile horizontal-overflow checks and light/dark screenshots.

The native navigation probe returned `ERR_BLOCKED_BY_ADMINISTRATOR` for a local HTTP server. Consequently the UI checks use an **offline DOM/API bridge**, not normal browser navigation. Sample raster bytes are embedded as data URLs only in the harness. Production player sources continue to use the validated same-origin media URLs. The harness does not establish actual response-header CSP enforcement, browser HTTP caching, TLS, reverse-proxy/CORS behavior, audible playback, H.264/HLS decoding or physical Cast Pro compatibility. Browser media control methods are mocked where needed to test element lifecycle; element identity is not evidence of uninterrupted decoded video.

The standalone HTML preview uses the actual editor/renderer with a small in-memory API simulation. No files are uploaded, no office devices are contacted and no demo changes are written to the real portal. It is a visual/workflow preview, not a demonstration of backend security or server persistence.

Screenshots in [overlays-ui](evidence/overlays-ui/) use fictional office content. The sample graphic is not a customer logo or live operational status. Demo assets/presets are not seeded into a production database.

## Release gates that still require staging

**Dependency/build verification:** the authoring runtime is recorded in [environment JSON](evidence/overlays-environment.json). The worker release now pins CairoSVG 2.9.1 and defusedxml 0.7.1 alongside Pillow 12.3.0, and installs Cairo's runtime library. The available runtime has CairoSVG 2.8.2; the attempted new-wheel download returned no available distributions from this environment's package access. A clean installation of the pinned Python 3.12 environment, both Docker builds and a dependency/image vulnerability scan were not executed. Do not interpret local test success as validation of uninstalled dependencies. [PyPI's release page](https://pypi.org/project/CairoSVG/2.9.1/) establishes the selected release, not its installation here.

**Deployment/hardware:** test the updated playback-proxy asset allowlist, browser CSP, real formats and fonts, two-band overlays over a deck/video/live feed, reduced-motion fallback, audio policy and long-run memory use on the actual players. Test normal restart, NAS-late startup, an actual NAS outage/remount and host reboot. The Nginx allowlist was inspected and tested as source text, not executed by Nginx. Docker was unavailable.

**Previous gates remain:** the HLS dependency installer is still supplied without a bundled HLS binary; the optional relay has not been deployed; no verified UniFi Connect control API has been added. See [REVISION-VALIDATION.md](REVISION-VALIDATION.md) for the preceding revision's detailed boundary record. The overlay does not turn an HTML player into a full-channel RTSP/HLS encoder.

**Persistence is not uninterrupted delivery:** SQLite retains settings; media remains on configured storage. A dead network, missing NAS media, invalid device clock or unavailable server can still interrupt or mis-time a screen. Native GIF frame phases are not locked together. Treat manual status messages as operator-entered content, not an emergency-system or authoritative-status integration.

## Reproduce

Install the release dependencies into a clean environment and run:

```sh
sh scripts/check.sh
python scripts/overlay-browser-check.py --output docs/evidence/overlays-ui
python scripts/player-state-check.py
python scripts/build-overlay-demo.py --output /tmp/signage-overlay-preview.html
```

`check.sh` includes `pip check`; dependency conflicts must be resolved in that clean environment, not ignored. Browser scripts need Playwright and an installed Chromium executable. The new overlay browser check creates its own disposable local database and sample content. The earlier generic `ui-dom-check.py` requires a staging URL and an explicit mutation flag; never point it at production.

Before installing, read [OVERLAYS.md](OVERLAYS.md), [PERSISTENCE.md](PERSISTENCE.md) and [UPGRADING.md](UPGRADING.md). Preserve existing state/media paths and reload each player once after upgrading its assets; no playback address change is required.

The web image explicitly installs the system `tzdata` package for IANA time-zone validation. This is clock data, not a media parser. Standalone installations must also provide an IANA time-zone database. This image change still requires the documented clean build.
