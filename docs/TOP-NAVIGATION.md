# Unified navigation and UniFi integration status

> Historical top-navigation revision. The later workspace status change removes
> its Connected badge; see [STATUS.md](STATUS.md) and [REPOSITORY-VALIDATION.md](REPOSITORY-VALIDATION.md).

## This revision

The administrator workspace now uses one full-width, sticky header. The sidebar,
its permanent horizontal gutter, and the duplicate breadcrumb/status bar have
been removed. Streams, Library, Displays, Activity and (for local authentication)
Users are in the header alongside refresh/connection status, theme and account
controls. Sign out lives in the account disclosure; there are no duplicate mobile
controls.

Above 900 CSS pixels the navigation is inline in a 68-pixel header. At 900 pixels
and below the same navigation is a dropdown from a single 60-pixel header, rather
than a second permanent strip. Escape, outside click, leaving the disclosure with
Tab, and selection close it. Focus is recovered when a resize hides the focused
control. The active destination is marked with `aria-current`; the skip link and
native dialog behavior remain available. Long workspace/account names truncate
without pushing controls off screen.

Production changes are limited to `web/app/static/admin.html`, `admin.css` and
`admin.js`. The Displays page now calls these records **Playback destinations**
and explicitly distinguishes portal playback from UniFi console management.
No backend routes, schemas, session checks, playback assets, source approval
policy, converter code or NAS configuration changed. No new runtime dependency
was added. The archive's file comparison evidence records the scope.

## UniFi: what is and is not implemented

**There is no UniFi controller API integration in this build.** It does not log
in to a console, store UniFi credentials, discover/adopt physical hardware, alter
a device's Web-mode URL, reboot a Cast Pro, change its hardware power/volume, or
manage firmware. Those operations remain outside the portal.

What *is* implemented is content routing through a persistent portal display
record. For example, configure a Cast Pro in Web mode once with:

```text
https://play.signage.example.org/display/lobby-1
```

Changing that record's assigned stream inside the portal changes the playback
state returned at the same address. Content, banners and schedules remain portal
operations; they do not issue UniFi API calls. This is distinct from pointing a
device directly at a permanent `/stream/<key>` address, which follows that one
stream regardless of the portal's display assignments.

Portal Online/Offline information reflects player heartbeats. It is not a UniFi
controller inventory, physical power measurement, or hardware health check. An
Off assignment makes the web player blank; it does not switch off a television.

### Is a future controller integration possible?

This review did not verify an officially documented UniFi **Connect** endpoint
for changing Cast Pro Web-mode URLs. The official developer landing page resolved
to the Network API; Network API availability is not evidence of that Connect
operation. This is a verification limit, not proof that no local endpoint exists.

An unofficial local-API route does exist for some Connect displays: the
maintainer of `iamslan/ha-unifi-connect` documents web URL changes, page reload,
mode selection, power, brightness and volume in a Home Assistant integration.
Its README explicitly lists **Display SE 21** as supported and says other devices
are untested. It requires a local UniFi account with Connect management access;
that is a security and operational dependency that this portal has not added.

That project is evidence that local automation is feasible on at least its
supported model, not validation for a Cast Pro or for this portal. Any future
adapter should first verify operations on the actual console/application/firmware
combination, restrict privileges and allowed actions, keep credentials server-side,
and verify TLS. Normal signage playback should continue without the adapter.
No guessed endpoints or partially wired hardware-control buttons were added.

Primary sources checked in this revision:

- [Ubiquiti developer documentation](https://developer.ui.com/)
- [Getting Started with UniFi Connect](https://help.ui.com/hc/en-us/articles/21171115391255-Getting-Started-with-UniFi-Connect)
- [Integration author's supported operations and device limits](https://github.com/iamslan/ha-unifi-connect)

## Executed validation

| Check | Result | Scope |
|---|---|---|
| Python regression suite | **172 passed, 1 skipped** | Includes three new shell-contract tests; existing authentication, media, overlays and NAS tests remain in the run |
| Navigation DOM checks | Passed; no page errors | Actual administrator HTML/CSS/JS in Chromium with an offline, in-memory API adapter |
| Responsive navigation matrix | Passed | Five views at 320, 360, 390, 480, 600, 768, 900, 901, 1024, 1180, 1280, 1440 and 1920 pixels |
| Real in-process API + overlay browser checks | Passed; no page errors | Existing overlay suite: saved drafts/presets, countdown/expiry, ticker and transition behavior, graphics and video-element continuity |
| Python compilation / JavaScript syntax | Passed | Delivered source; not a clean image build |

Navigation checks include all five views, mobile disclosure focus, Escape and
outside-click dismissal, keyboard exit without a focus trap, resizing while a
navigation element is focused, long labels, theme changes, sample display
reassignment without URL changes, stream creation, banner editing and connection
failure/recovery. The display reassignment *browser demo* uses an in-memory mock;
the backend regression suite separately exercises real API routing.

Evidence:
[pytest](evidence/topnav-pytest.txt),
[JUnit](evidence/topnav-pytest.xml),
[navigation checks](evidence/topnav-ui/navigation-results.json),
[overlay checks](evidence/topnav-overlays/browser-results.json),
[environment](evidence/topnav-environment.json),
[production file comparison](evidence/topnav-production-changes.json).

The skipped test is legacy bcrypt migration because the bcrypt wheel is absent.
The recorded environment is not the newly pinned production dependency set.
Native browser file/network navigation is blocked in the authoring environment;
the preview was loaded as a local DOM document without altering browser policy.
These checks do not prove TLS/CSP/network behavior, release-image builds, actual
NAS reboot recovery, live HLS decoding, or physical Cast Pro behavior. No UniFi
console was contacted. The deployment gates in the earlier validation documents
remain open; this is a staging candidate, not a production certification.

## Preview and update

The optional single-file workspace preview is generated with:

```bash
python scripts/build-workspace-demo.py --output signage-topnav-preview.html
```

It contains fictional sample media and in-memory demo settings. Changes last only
for the open page. It has no real login, file upload/conversion, NAS connection,
live source forwarding, or UniFi control. The adapter in `scripts/` and the sample
fixture in `docs/` are not imported into the production application. The preview's
playback dialog is an illustration, not an end-to-end test of the real player.

From the immediately preceding **overlays** revision, redeploy/rebuild the web
service and refresh the administrator page. No database migration, stored playback
URL change, or UniFi configuration change is introduced by this layout update.
Keep existing data/media paths. From older releases, follow [UPGRADING.md](UPGRADING.md),
[PERSISTENCE.md](PERSISTENCE.md) and [OVERLAY-VALIDATION.md](OVERLAY-VALIDATION.md),
including the matched web/worker upgrade and clean build requirements.
