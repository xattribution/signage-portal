# Permanent playback, external sources and relay boundaries

## The operating model

The portal manages office signage content and assignments. A display is a durable destination, a stream is a reusable channel, and the Library holds file snapshots or approved URL sources. UniFi Connect configures the device to open the portal player; it does not need to manage each subsequent content change.

**Preferred: one permanent URL per physical display.** Add a display in the portal, copy its URL, and put that URL into the Cast Pro's Web mode configuration once:

```text
https://play.signage.example.org/display/lobby-1
```

Reassign Lobby 1 from Reception to Office News in the portal. That address remains unchanged. Editing a stream, changing its source, renaming the stream/display or rebooting the host does not require a UniFi URL edit. The display slug is now immutable; deleting a display retires the slug rather than allowing its old URL to be silently reused.

**Optional: one permanent URL per stream.** Every stream also receives a random, persistent channel key, exposed through **Copy URL**:

```text
https://play.signage.example.org/stream/<persistent-channel-key>
```

This follows that stream regardless of its name or content. A device hard-wired to a channel URL must be pointed elsewhere to show a different channel. That is why the per-display URL is the better default. Existing `/display/<slug>` and `/preview/<numeric-id>` links remain valid. Channel keys are initialized once during migration and stored in SQLite; numeric IDs are not recycled after deletion.

These are **HTML playback pages**, not encoded HLS outputs of the entire slide rotation. Cast Pro supports Web mode. Other devices need a suitable full-screen browser and compatible codecs, or an additional player/encoding adapter. An RTSP-only decoder cannot consume the composed HTML channel just because the path is called a stream.

## View-only is a boundary, not a confidentiality claim

Player pages contain no editing controls. Content/configuration mutations on playback routes are rejected, including when the request carries an admin cookie. A dedicated playback hostname exposes only the player page, player assets/APIs, rendered media and health check; admin/login/library/transfer routes return 404 there. Player GET polling still updates bounded display heartbeat telemetry. It cannot upload content, assign streams or administer users, but unauthenticated heartbeat reports can be spoofed.

Configure both hostnames in `ALLOWED_HOSTS`, set `ADMIN_ORIGINS`, `PLAYER_BASE_URL` and `PLAYER_ONLY_HOSTS`, and use `deploy/nginx.example.conf`. External sources in production require a separate playback **hostname** from the admin origin, not just a different URL path or port. Preview links redirect to that player origin. Cookies and authorization headers are stripped by the example playback reverse proxy. Set `FORWARDED_ALLOW_IPS` to the actual proxy peer seen by the web container; otherwise HTTPS scheme checks can fail or redirects can loop. Never use wildcard proxy trust.

URLs are public to machines allowed onto the playback network. A random channel key is not a login or an access-control token. Restrict viewing through network segmentation/firewalls and suitable TLS. Do not treat this release as a confidential-content portal with per-viewer authorization. Device cookies, NAS credentials and UniFi administrative credentials are not embedded in playback addresses.

## Add an external source

Use **Library → Add URL**, select a type and a title, then place it on a stream like any Library item. **Edit source** changes the upstream address without changing playback links.

| Type | Implementation | Conditions |
|---|---|---|
| Image URL | Browser image element | Direct HTTPS image, approved origin |
| Video URL | Browser video element | Direct MP4 or another format supported by the actual display browser; audio/autoplay rules still apply |
| Live HLS | Native HLS when available, local HLS.js fallback otherwise | Direct `.m3u8`, compatible codecs and CORS for the playback origin |
| Web page | Non-interactive sandboxed iframe | Page must permit embedding and operate without same-origin privileges, logins, popups, forms or top navigation |

A website's watch page is not a media feed. DRM services and websites that deny framing are not bypassed. Browser load events cannot reliably prove an embedded third-party page is healthy; test the actual page on the actual device.

Source hostnames must also differ from the administrator and playback hostnames; another port is not a cookie boundary. Operators approve exact origins using `SOURCE_MEDIA_ORIGINS` and `SOURCE_FRAME_ORIGINS`, for example:

```dotenv
SOURCE_MEDIA_ORIGINS=https://video.signage.example.org
SOURCE_FRAME_ORIGINS=https://boards.example.org
```

Use canonical lowercase origins without trailing slash, default-port suffixes, wildcards or paths. HLS child playlists, segments and keys may require additional media origins. The HLS JavaScript loader checks child-request origins too; the player response CSP is also restrictive. Removing approval suppresses already-saved sources. A policy change causes connected players to obtain a fresh page/CSP; ordinary content/source edits on approved origins do not reload the whole player.

The web backend never fetches arbitrary source URLs, follows their redirects or probes your LAN. This avoids turning the credential-bearing application into a general URL proxy. The approved source must be reachable by the displays. Browser-loaded URLs, including query parameters, are visible to viewers: do not paste private upstream tokens or passwords. Use a separately protected relay when upstream secrets must stay server-side. Use only content you are authorized to display or retransmit.

**Ready** on a URL source means its configuration was accepted, not that the backend measured an active broadcast. Live playback stays at the live edge instead of repeatedly seeking according to the slide clock. A source placed alone remains mounted across loop boundaries; a source in a mixed rotation has the selected dwell time. Live connections are not preloaded invisibly. Detected errors/stalls receive a clock fallback and bounded reconnect attempts. Different displays can have different live-stream latency; this is not frame-locked broadcast distribution.

### HLS build dependency

`web/install_hls.py` downloads the official HLS.js **1.7.3** release archive, verifies a pinned SHA-256, and installs the light player with its upstream notice and provenance. Docker runs this during the web image build, not on a display at runtime. The build must succeed before Chromium HLS fallback is available.

This authoring environment could not download the binary release archive. The delivered source includes the installer, not a pre-bundled HLS binary. Run the Docker build on a network-enabled staging host. For a controlled offline build, obtain the exact upstream release archive and place it at `web/hls-archive/release.zip` before building. The installer detects it and performs the same checksum check; the disposable build stage copies only installed player assets into the final image. Verify the resulting `HLS-PROVENANCE.json`. A standalone non-Docker installation can instead run `python web/install_hls.py /path/to/release.zip`. Never replace this with an unpinned runtime CDN script.

The light build intentionally does not target DRM or an advanced multi-audio/subtitle service. Validate your broadcast's video/audio formats on the display. A failed HLS dependency install should stop the release build, not be ignored.

## RTSP cameras and broadcast relays

`deploy/relay/` provides an **optional, separately deployed MediaMTX configuration**, not an enabled portal-side proxy. The example pulls one fixed, operator-approved RTSP source and exposes read-only HLS at:

```text
https://video.signage.example.org/office-live/index.m3u8
```

Add that HLS address to the portal Library after the relay and TLS proxy are tested. MediaMTX source configuration remains server-side; the portal does not accept arbitrary RTSP credentials or mutate the relay configuration. Changing this fixed upstream address in the relay preserves both its HLS output URL and the portal's playback URLs.

The example disables publishing, API, metrics and unnecessary protocol listeners. It runs as a non-root, read-only service with bounded memory/CPU, a loopback-published HLS port, a separate network and **no portal database, NAS or converter mounts**. The operator must additionally restrict its egress to approved input servers and limit viewing at the TLS/network edge. A Docker bridge is not an outbound firewall. Choose and scan a pinned `MEDIAMTX_IMAGE`; it is deliberately not a floating `latest` dependency.

Configure the approved H.264/AAC source, CORS playback origin, TLS hostname and read permission for each explicit relay path. The source can be pulled on demand to avoid opening one upstream connection per display. This is a relay/remux path, **not a transcoder**: incompatible codecs require a separate, resource-limited transcoding stage, which is not included here. The office file-conversion worker remains network-less. RTSP/RTMP/SRT ingest from arbitrary UI URLs and full composited-channel video encoding are not implemented.

Ubiquiti explicitly notes that Cast Pro does not natively support Protect camera streaming. This optional browser/HLS route must not be represented as a supported native Protect integration. Real camera/broadcast and Cast Pro testing remains necessary.

## UniFi API integration status

This revision does not authenticate to or modify a UniFi console. The official developer entry point reviewed exposed Network API documentation, while Connect's official guide describes management in the Connect application; a supported Connect endpoint for this particular Web-mode assignment operation was not verified. That is not proof no local/undocumented endpoint exists. No guessed controller endpoints or stored full-console admin account have been added.

Per-display stable URLs remove the need for that API in routine content and stream assignment workflows. Adoption, initial Web-mode configuration, firmware/device settings and physical output management stay in UniFi. A future verified integration could help provision devices without becoming a runtime dependency of their content routing.

An updated review found an unofficial local Connect API implementation in
[iamslan/ha-unifi-connect](https://github.com/iamslan/ha-unifi-connect). Its author
lists Web URL, reload, mode and some hardware controls, but explicitly supports
Display SE 21 and describes other models as untested. This makes local automation
a credible integration direction, **not a verified Cast Pro feature or something
implemented in this portal**. See [TOP-NAVIGATION.md](TOP-NAVIGATION.md) for the
current implementation boundary and validation record.

## Added API surface

Admin authentication and the existing write guard apply throughout:

```text
POST /api/feeds                         create source
PUT  /api/feeds/{upload_id}             update source, preserving placements
GET  /api/shares                        list approved folder names (no NAS I/O)
GET  /api/shares/{id}/browse?path=       confined, paginated browse
POST /api/shares/{id}/import            queue a snapshot import
POST /api/shares/{id}/export            queue an original-file export
GET  /api/transfers                     recent durable job states
POST /api/transfers/{id}/retry          retry a failed transfer
```

Public additions: `GET /stream/{key}` and `GET /api/player/channel/{key}`. The latter returns only playback state, not administrative data. Shared-folder roots, host mount paths, credentials and raw originals are not published through these player APIs.

Read [PERSISTENCE.md](PERSISTENCE.md) before installing and [REVISION-VALIDATION.md](REVISION-VALIDATION.md) for what was actually tested.

Primary references reviewed September 27, 2026: [Ubiquiti Cast Pro](https://store.ui.com/us/en/products/uc-cast-pro), [Connect setup](https://help.ui.com/hc/en-us/articles/21171115391255-Getting-Started-with-UniFi-Connect), [UniFi developer entry point](https://developer.ui.com/), [MediaMTX RTSP sources](https://mediamtx.org/docs/publish/rtsp-cameras-and-servers), [MediaMTX configuration](https://mediamtx.org/docs/references/configuration-file), [HLS.js release](https://github.com/video-dev/hls.js/releases/tag/v1.7.3).
