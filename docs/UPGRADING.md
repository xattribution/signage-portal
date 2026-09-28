# Upgrade from the supplied version

## Banner/graphics revision

Back up the database and media, then update **web and worker together**. New columns store per-stream presentation settings/revisions and the NAS transfer graphic/content choice; a new table stores copied presets. Existing stream keys, display slugs, assignments, sources and media paths are not renamed. Modern sessions are not deliberately revoked by this addition.

The worker needs the new CairoSVG/defusedxml requirements and Cairo runtime library. Build and test the complete pinned images in staging; local SVG tests ran on CairoSVG 2.8.2, while the release pin is 2.9.1 and was not installed here. Keep media parsing out of the web container.

Apply the updated playback proxy rule for **overlay.js / overlay.css** as well as player assets. Reload already-open player pages once after upgrading so they acquire the new code; the saved UniFi URLs do not change. Subsequent banner edits arrive through normal player state polling. Use **Add graphic** / NAS **As graphic** for transparency or GIF animation; existing uploads are not retroactively re-encoded.

Preserve the established database, `.env`, absolute mount paths and host NAS credentials. No new NAS mount or privileged web operation is required. Read [OVERLAYS.md](OVERLAYS.md) and [OVERLAY-VALIDATION.md](OVERLAY-VALIDATION.md) for behavior, limits and acceptance checks. Rollback requires a matching code/database/media snapshot, not a mixed old-worker/new-web pair.

---

## Additional changes in the office streams + NAS revision

Back up and stop both services before this migration. Preserve the current absolute state/media paths. Startup adds persistent channel keys, retired display slugs, source metadata and durable transfer jobs without renaming existing displays or media. Display slugs can no longer be changed, and deleted slugs are reserved; ordinary display names remain editable.

Read [PERSISTENCE.md](PERSISTENCE.md) before enabling NAS markers or switching to systemd. A missing marker now prevents startup when the guard is enabled. Do not auto-generate markers at boot. Use the matching browser and systemd Compose overlays, disable the previous stack's automatic restart, and build images before enabling the service. The web image build installs checksum-pinned HLS support and needs access to its official release archive.

External sources in production require a distinct playback hostname, exact source-origin allowlists and the updated player reverse proxy. Update web, worker and player assets together. This revision does not revoke existing modern-format sessions merely to add these features. The older signed-cookie migration described below applies only when upgrading from the original, pre-modernized app.

---

This is a coordinated application/worker update, not a CSS-only replacement. Test it against a copy of the installation first.

## Before changing the live service

1. Record the existing image versions, configuration, ownership/mount settings and player URLs. Plan a short interruption; tell gatekeepers their sessions will be invalidated.
2. Back up the database with SQLite's backup mechanism, or stop the web service before copying its local state. Back up originals and rendered media using a consistent NAS snapshot/recovery plan. Verify that a backup can be opened.
3. Stop **both** the old web service and old worker before switching their code or mounts. Do not share a live work directory between old and new workers.
4. Build and test the new images and dependency set in staging. The included requirements were updated, but their clean-network installation and Docker builds were unavailable in the revision environment.

## Configuration changes

Start from the new `.env.example` and transfer your values deliberately; do not overwrite it with the old file wholesale.

- `COOKIE_SECURE` now defaults to true. Production administrator routes require HTTPS. Insecure development requires an explicit opt-in.
- Host and origin allowlists are enforced. Include every actual administrator and player hostname/IP, and retain localhost/127.0.0.1 for the container health check.
- The host-published port defaults to loopback rather than every interface. Route player traffic through a restricted LAN listener, or explicitly configure the desired bind address and firewall.
- Set `PLAYER_BASE_URL` when copied player URLs must use a LAN origin rather than the administrator/Cloudflare hostname.
- `FORWARDED_ALLOW_IPS` must contain actual trusted proxy peers; wildcard trust is refused. A proxy's Docker bridge address may differ from its host-side address.
- There is no shared bootstrap password. Existing local accounts mean the bootstrap password is not needed. Clear old bootstrap credentials from the environment.
- The worker uses `CONVERT_WALL_SECONDS`, continues its heartbeat during long conversions, and receives the same aggregate output budget as the web service.

## Data and authentication changes

Startup adds the sessions, persistent login-attempt and ID-allocation tables and the upload generation column. Existing streams, displays, uploads and placements remain. An empty database still receives the default streams and displays.

All legacy signed session cookies stop working. Users sign in again; accepted bcrypt passwords are upgraded to Argon2id. The bcrypt compatibility package must be present. The legacy migration regression test was skipped locally because that wheel was not available, so run it after installing the supplied requirements.

Old bcrypt passwords cannot exceed 72 UTF-8 bytes. New Argon2id passwords can be longer. Password whitespace is not trimmed. A password reset revokes all current sessions for that user, including the session performing its own reset.

Processing uploads without a generation token or ticket are reissued by the ingest loop. Stale or malformed converter output is rejected rather than trusted. Interruptions around a handoff can therefore require Retry. An interruption that leaves the original missing must be resolved by restoring/re-uploading it; the application does not invent replacement content.

Existing rendered paths such as `/media/12/000.jpg` remain readable. New conversions use `/media/12/<generation>/000.jpg`; a retry cannot reuse the previous cache key. New upload IDs are allocated monotonically. Old files are not automatically re-encoded just because the code changed.

## API changes

Logout is now `POST /auth/logout`; `GET /logout` returns 405. Login and logout require the same `X-Signage: 1` write header as other mutations. Requests with an incompatible Origin/Host, unknown input fields, contradictory clear flags, invalid dates, duplicate/partial rotation orders or out-of-range numeric values can now fail instead of being silently accepted.

`GET /api/overview` supports conditional requests with ETag and a 304 response. `X-Server-Time` carries the updated clock for a 304. Upload details no longer expose the original on-disk name or conversion token; only up to 24 slide previews per upload are included, with a separate total count.

The admin JavaScript is an ES module and needs a reasonably current desktop/mobile browser. The player remains a separate conservative script.

## Acceptance and rollback

Before reconnecting all lobby players, test one account, one image, one deck, one video, a scheduled override, reassignment, logout invalidation and outage behavior on the actual hardware. Verify media range responses and both services' limits/mounts. Confirm copied player URLs reach the intended LAN listener.

Rollback means stopping both new services and restoring the old application/worker **and their matching database/content snapshot**, not merely switching one container image back. The old worker does not implement the new generation contract. Do not mix a post-upgrade database/work queue with an old worker and assume compatibility.

The web image explicitly installs the system `tzdata` package for IANA time-zone validation. This is clock data, not a media parser. Standalone installations must also provide an IANA time-zone database. This image change still requires the documented clean build.
