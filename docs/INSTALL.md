# Install the office signage service

For a quick evaluation or a small LAN install, the two commands in the [README](../README.md#quick-start) are enough. This guide is the production path: verified host storage, TLS, and systemd startup.

This guide is for a Linux host with systemd and Docker Engine/Compose, persistent local storage, and an SMB/NFS share. The supplied configuration is a template, not a claim that your NAS, proxy or Cast Pro has been tested.

## 1. Put the code on the host

Place a clean checkout or extracted source package at `/opt/signage`. Keep the application code separate from state. For an existing installation, retain its exact data/media paths and read [UPGRADING.md](UPGRADING.md) before replacing anything. Back up first.

GitHub is source hosting, not the server running Python, SQLite and media conversion. Publishing the repository does not deploy the portal. A static GitHub Pages site cannot run these backend services.

## 2. Prepare durable storage

Follow [PERSISTENCE.md](PERSISTENCE.md), in order. It is the authoritative mount/startup procedure. It covers root-restricted SMB credentials or NFS permissions, host mounts, UID/GID mapping, marker files, `/etc/signage/shares.json` and `/etc/signage/storage.json`.

Use persistent local paths for `DATA_ROOT` and `SPOOL_ROOT`; use the NAS for `MEDIA_ROOT`. Approved browse/export folders are separate from the managed originals/work/rendered folders. Inspect the actual mounted filesystem before creating content directories or marker files. Do not recreate marker files at boot or let a missing NAS become an empty local folder.

## 3. Configure application and playback addresses

From `/opt/signage`:

```sh
cp .env.example .env
chmod 600 .env
```

Edit the following settings. The complete set is documented in `.env.example` and read in `web/app/config.py`. Production uses host paths, so every Compose command adds `-f docker-compose.host-storage.yml`. Settings you put in `.env` are locked on the portal's Settings page.

| Setting | What to supply |
|---|---|
| `MEDIA_ROOT`, `DATA_ROOT`, `SPOOL_ROOT` | Your actual persistent host paths |
| `MEDIA_VOLUME_ID` | The identifier placed on the verified media share |
| `REQUIRE_MEDIA_MARKERS` | Keep `true` for this deployment |
| `SITE_NAME` | The workspace title |
| `SIGNAGE_PORT`, `SIGNAGE_BIND` | Published port (default 51480) and interface; use `127.0.0.1` behind the proxy |
| `ADMIN_USER`, `ADMIN_PASSWORD` | Optional. Leave blank to create the first administrator on the setup page; `SETUP_TOKEN` can require a code there |
| `ALLOWED_HOSTS` | Actual admin/player hostnames plus health-check names, no schemes/ports |
| `ADMIN_ORIGINS` | Complete HTTPS administrator origin |
| `PLAYER_BASE_URL`, `PLAYER_ONLY_HOSTS` | Separate view-only playback origin/hostname |
| `FORWARDED_ALLOW_IPS` | Actual trusted reverse-proxy peer addresses; never `*` |
| NAS browser variables | Paths/IDs matching the NAS Compose overlay and host configuration |
| `SOURCE_MEDIA_ORIGINS`, `SOURCE_FRAME_ORIGINS` | Exact approved origins, or empty to disable external sources |

Set `COOKIE_SECURE=true`, keep `ALLOW_INSECURE_DEV=false`, and publish on loopback (`SIGNAGE_BIND=127.0.0.1`). Adapt `deploy/nginx.example.conf`, install certificates trusted by your browsers/displays, and validate both hostnames. Do not put NAS or UniFi administrator credentials in `.env`. Read [STREAMS-AND-SOURCES.md](STREAMS-AND-SOURCES.md) before enabling external sources.

With `ADMIN_PASSWORD` blank, the first visit opens the setup page, which only accepts LAN and loopback clients and closes for good once an account exists. If you set `ADMIN_PASSWORD` instead, remove it from `.env` after confirming the account exists; later password changes happen through the administrator workflow. Keep the local database and rate-limit secret private and backed up.

## 4. Build and install startup units

Run the build, storage preflight and systemd installation commands in [PERSISTENCE.md, section 3](PERSISTENCE.md#3-build-once-then-let-systemd-own-startup). Use its matched Compose project name and overlays consistently. Do not simultaneously run a second standalone Compose stack.

The build needs package-network access, including the pinned HLS.js archive, or the exact checksum-verified offline input described in [STREAMS-AND-SOURCES.md](STREAMS-AND-SOURCES.md#hls-build-dependency). Do not bypass a failed dependency download or verification. Startup uses built images and should not download new packages during a reboot.

The systemd timer retries startup after late NAS availability. Stop both timer and service for planned maintenance, or the timer can start an intentionally stopped service again.

## 5. Bring up one display first

Sign into the administrator hostname, add/rename a playback destination, and copy its **display URL**. In UniFi Connect, configure the Cast Pro to open it in Web mode. Another chosen display needs a compatible full-screen browser/player.

Upload one image and one slide deck, wait for conversion, and place them on a stream. Assign the destination to that stream. Test a stream change and banner edit without editing the UniFi URL. Initial device provisioning and hardware settings stay in UniFi; this release has no controller integration.

## 6. Acceptance before office rollout

Run the release/dependency checks, scan images, and validate your proxy and playback-only boundary. Test actual decoding, fonts, dimensions, audio/autoplay behavior, overlays and a real upstream live feed as applicable. Run the host-reboot and NAS-outage/remount checks in [PERSISTENCE.md](PERSISTENCE.md#5-acceptance-checks-on-the-real-installation). Confirm backup restoration with matching database/media state.

See [REPOSITORY-VALIDATION.md](REPOSITORY-VALIDATION.md) for what has actually been executed. Do not treat a passing DOM test or a fresh workspace response as proof that a physical display rendered the content.
