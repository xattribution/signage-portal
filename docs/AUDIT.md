# Engineering review and changes

## Assessment

The supplied architecture had the right high-level separation for a small signage service. The weak point was the distance between some security claims and the exact code paths that were supposed to enforce them. The UI also needed a clearer working hierarchy, stronger recovery states and fewer incidental implementation details exposed to an operator.

This revision retains streams, reusable uploads, the public player model, local SQLite and a separate converter. It changes the implementation where that improves an identifiable workflow or boundary. It does not introduce a frontend framework, a broker, a generic plug-in layer, fabricated dashboards or an AI feature.

Priority labels below are engineering triage, not CVSS scores or a penetration-test certification. Findings are from inspection of the supplied ZIP and the regression cases in this package. Upstream references are separately identified at the end.

## Material findings and implemented controls

| Priority | Finding in the supplied version | Implemented change / evidence |
|---|---|---|
| High | The upload limit was applied after framework multipart parsing/spooling, so the advertised file cap did not bound all preceding intake work. | ASGI byte counting before parsing; declared/actual size limits; body-read deadline; authentication before `request.form`; one file/one field; concurrent-upload admission. Tests cover anonymous upload, oversized declared/chunked bodies, disguised files and extra files. |
| High | Login did not share the normal mutation guard. HTTP administration and wildcard proxy/host behavior also needed explicit boundaries. | Guard login/logout and every unsafe method; exact origins, fetch metadata and a required header; validate one Host; fail closed for production HTTP administration and unsafe startup settings. |
| High | Signed cookies were not centrally revocable when a particular session logged out. | Random opaque tokens, hashed server-side records, absolute expiry and per-user generation checks. Logout deletes the current session; password changes revoke all account sessions. |
| High | Converter output checking and copying used separate path-based operations. A compromised writer could change an entry between validation and use. | Pin an output-directory descriptor; no-follow/nonblocking file opens; reject nonregular/multiple-link files; copy from that same descriptor; verify the copied signatures and source identity; bound enumeration and aggregate bytes. Adversarial fixtures cover links, FIFOs, traversal, extra files, stale generations and malformed manifests. |
| High | Dependency pins were old and did not deliberately select the later Starlette security fixes. | Updated direct runtime pins, explicit Starlette dependency and a release-check script. **Clean resolution, vulnerability scan and image build remain unverified here.** An updated version number alone is not a security result. |
| Medium | The worker could look dead throughout a lengthy conversion; timeout/process cleanup and in-process image decoding had weaker bounds than the documentation implied. | A lightweight supervisor refreshes heartbeat during work, starts a separate resource-limited job session, observes cancellation and kills/reaps its process group. Startup/cancellation also clean scratch output. Real JPEG/PDF/PPTX/MP4 conversion passed locally; Docker isolation still needs verification. |
| Medium | Reusing a rendered URL after conversion could return stale cached content. Upload IDs were also reusable after deletion. | Generation-specific immutable media paths; retry token checks; monotonic upload ID allocation. Legacy rendered URLs remain compatible. Regression tests cover non-reuse, retry generation and range serving. |
| Medium | Multiple small schedule/database queries and unconditional UI refresh work added unnecessary work. | One overview snapshot and reusable schedule indexes; ETag/304 responses; view-specific change signatures; visible-tab polling; one in-flight request with deadlines and backoff. This reduces unnecessary work, but no production throughput improvement percentage is claimed. |
| Medium | Uncoordinated upload requests and weak error recovery made the browser and server less predictable under load. | Sequential cancellable upload queue, explicit conversion/retry states, busy controls, timeout errors, persistent connection warning and recovery. |
| Medium | Schedule mutations could be partially valid or inconsistent after merging with the existing placement. | Strict request models, merged date validation, atomic display changes and complete duplicate-free rotation orders. Epoch-zero and override end boundaries have regression cases. |
| Medium | Player playlist changes could retain stale prepared video resources; stale override state could survive an outage. | Explicit media release and URL-aware identity, bounded single-flight polling, checked cached state, and stopping known expired offline overrides after a grace period. Hardware memory/codec/long-run behavior remains unmeasured. |
| Medium | An `apt-get ... || true` chain could mask more than the intended optional package removal. | Fail-fast Docker build steps; only purge actually installed unwanted packages; `pip check`; explicit non-root groups; loopback publishing and bounded container logs. Images still need real builds/scanning. |
| Usability | Operator controls competed with structure, and keyboard/mobile/error states were secondary. | Content-led stream cards; compact Screensaver; separate fleet cards; native dialogs and label association; keyboard reassignment and ordering; theme/reduced-motion support; visible stale-state messaging. |

## Design changes

The visual direction is deliberately restrained: graphite navigation, neutral working surfaces, blue primary actions, typography and spacing doing the grouping, with stream colors restricted to useful identifiers. Content previews carry the visual weight. There are no decorative gradients, glass panels, stock illustrations, floating novelty widgets or “secure” badges substituting for actual controls.

The change is not only visual. Streams remains the center of the job. Library is reusable content rather than another dashboard. Displays makes the distinction between scheduled content and a reported heartbeat explicit. Dialogs provide focused edits and safe exits, and polling avoids rebuilding a focused control or active drag operation. The mobile layout preserves the same tasks rather than shrinking a desktop table.

Native dialogs were exercised for keyboard focus containment and dismissal. Automated accessibility certification, assistive-technology testing and complete contrast/touch-target auditing were not performed. “Accessible” should not be read as a claim of WCAG conformance.

## Security boundaries and residual risks

**A compromised converter is still a serious event.** It can read the originals visible in its container, write to work, consume resources and create syntactically acceptable malicious media. No-follow descriptor checks close particular filesystem handoff attacks; they do not prove content integrity against every malicious decoder input. Image/video signatures are not a deep sanitization proof.

**Network isolation and mount separation are important but not absolute containment.** A kernel/runtime escape, unsafe host configuration, host administrator compromise, shared-mount mistake or NAS ownership error can defeat the intended separation. `noexec` does not prohibit interpreted execution or reading data. CSP constrains active web content; it is not a media-decoder patch.

**Public players are not authenticated devices.** Anyone permitted to reach those routes can read public content and send fake heartbeats. All gatekeepers still have equivalent control. The audit database is not a tamper-evident external log. Future roles, per-display credentials or remote audit forwarding need a separate requirements decision, not claims that they already exist.

**Availability still depends on operations.** Upload counts, process limits, per-job output budgets and timeouts do not replace NAS/spool quotas, connection controls, backup/restore checks or free-space monitoring. Account/IP throttles can themselves be used to cause temporary account lockout. Kernel-blocked NAS I/O can outlast application deadlines. Run one web process and one worker.

**Patch status is not established by this review.** Direct dependencies have updated pins, but transitive hashes and base-image digests are not locked. No complete Python/OS image vulnerability scan ran here. Build, resolve, audit, scan and test the exact release images before deployment; record their digests and package manifests.

**Playback guarantees remain bounded.** Sync depends on the device, decoding and network. Cached JSON is not an offline media pack. The actual Cast Pro, a long-duration video, repeated reboots, a 24-hour memory soak, heavy concurrent uploads and a real NAS failure were not tested here. No “30 ms” or other unmeasured synchronization claim is carried forward.

## Upstream context, consulted separately from the supplied project

The following references informed dependency and control choices; they are not evidence that this application passed an independent audit.

- Starlette release notes, including multipart and Range handling fixes: https://starlette.dev/release-notes/
- Starlette Range-header DoS advisory: https://github.com/Kludex/starlette/security/advisories/GHSA-7f5h-v6xp-fcq8
- Starlette multipart rollover advisory: https://github.com/Kludex/starlette/security/advisories/GHSA-2c2j-9gv5-cj73
- FastAPI and Starlette release metadata: https://pypi.org/project/fastapi/ and https://pypi.org/project/starlette/
- OWASP File Upload guidance: https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html
- OWASP Password Storage guidance: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- Cloudflare Access JWT validation: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/

See [TESTING.md](TESTING.md) for the separation between executed tests and release checks still required.
