# Persistent NAS and reboot recovery

This is the **Linux + systemd + Docker Compose** deployment path for the office-signage revision. Templates require site-specific paths, share names, hostnames and certificates. They are not an installer that mounts an arbitrary share from the web UI. The containers do not receive mount privileges, NAS passwords or the Docker socket.

## What persists

| Item | Location | After restart |
|---|---|---|
| Streams, permanent channel keys, display assignments, schedules, users and sessions | Local `DATA_ROOT/signage.db` and its WAL | Reopened; not regenerated |
| Rate-limit secret | Local `DATA_ROOT/.secret_key` | Reused |
| Original and rendered media | NAS `originals/` and `rendered/` | Same content and URLs |
| URL sources | Local database | Same configured source; player reconnects |
| Approved shared folders | Host `/etc/signage/shares.json`, read-only container bind | Loaded on startup |
| Mount definitions and credentials | Host `/etc/fstab` and root-only credentials file | Managed by the operating system |
| NAS transfers | Local `library_transfers` table | Interrupted running jobs requeued; completed jobs not duplicated |

Browser uploads that have not yet reached the server are different: their local browser queue is not a durable NAS transfer job. A NAS transfer marked **Needs attention** remains failed until an administrator chooses **Retry**. Recovery does not guess missing content, undo intentional deletions or silently republish externally edited files.

## 1. Mount the correct storage

Keep the SQLite database on persistent **local disk**, not an SMB/NFS share, container writable layer or `/tmp`. For an existing deployment, retain its current absolute `DATA_ROOT`, `SPOOL_ROOT` and `MEDIA_ROOT`; do not switch to empty example folders and accidentally initialize a different installation.

For a new installation, create local state and the empty host mountpoints once:

```sh
sudo install -d -o 1000 -g 1000 -m 0700 /var/lib/signage/data /var/lib/signage/spool
sudo install -d -m 0750 /mnt/signage-media /mnt/signage-library
sudo install -d -m 0750 /etc/signage
```

Adapt **one** NFS or SMB option per mount from `deploy/fstab.example`. SMB credentials belong in `/etc/signage/nas.credentials`, owned by root with mode `0600`; do not place passwords in URLs, `.env`, browser forms or a Git repository. Grant the share account only the folders it needs. NFS exports need appropriate server-side UID/GID permissions and network ACLs. Retain `noexec,nosuid,nodev` and network-mount options; do not change NFS to `soft` simply to hide outages.

After editing `/etc/fstab`, reload systemd and mount explicitly:

```sh
sudo systemctl daemon-reload
sudo mount /mnt/signage-media
sudo mount /mnt/signage-library
findmnt -T /mnt/signage-media -o TARGET,SOURCE,FSTYPE,OPTIONS
findmnt -T /mnt/signage-library -o TARGET,SOURCE,FSTYPE,OPTIONS
```

**Inspect the output before creating content directories.** It must identify the intended NFS/CIFS share, not the host's ext4/overlay filesystem or just an autofs trigger. Only after that verification, create the following directories on the NAS and grant uid/gid `1000:1000` appropriate access:

```text
/mnt/signage-media/{originals,work,rendered}
/mnt/signage-library/{incoming,outgoing}
```

The browser's `incoming` mount is read-only. `outgoing` is writable only for explicit exports. These folders must not overlap the database, application code, originals, work or rendered folders. Imports go through the same validation/converter pipeline as uploads; the browser never serves arbitrary NAS files directly.

## 2. Set persistent volume identities

Generate two identifiers once, for example with `python3 -c 'import uuid; print(uuid.uuid4().hex)'`: one for managed media, another for the browse/export share. They are identifiers, not passwords. While the intended shares are mounted, write the media identifier into `.signage-volume-id` in **each** of `originals`, `work` and `rendered`. Write the library identifier into the same-named file in **both** `incoming` and `outgoing`. The web/worker uid must be able to read these small files. Do not make a boot script recreate them.

Set `MEDIA_VOLUME_ID` to the media identifier and leave `REQUIRE_MEDIA_MARKERS=true` in `.env`. Copy and edit:

```sh
sudo install -o root -g 1000 -m 0640 deploy/shares.example.json /etc/signage/shares.json
sudo install -o root -g root -m 0640 deploy/storage.example.json /etc/signage/storage.json
```

Replace every placeholder identifier and share source. `shares.json` uses **container paths** `/shares/incoming` and `/shares/outgoing`; `storage.json` uses **host paths** and the exact `SOURCE` reported by `findmnt` (such as `//nas.example.org/signage` for SMB). Keep these files operator-owned; the portal cannot edit mount policy.

The preflight opens directories without following symlinks, checks the descriptor's actual filesystem type and source, and checks the expected marker. A copied marker on an ordinary local directory does not satisfy the filesystem check. Neither the application nor the preflight creates replacement storage when the NAS is missing.

## 3. Build once, then let systemd own startup

Install this release under `/opt/signage`, adapt `.env`, and read `STREAMS-AND-SOURCES.md` for playback host/TLS settings. The base Compose file keeps the standalone deployment available; add the NAS overlay for browsing. All bind mounts use `create_host_path: false`.

```sh
cd /opt/signage
# Build while online; no downloading/building is required during a normal reboot.
sudo docker compose --project-name signage -f docker-compose.yml -f docker-compose.host-storage.yml -f docker-compose.nas.yml -f deploy/compose.systemd.yml build --pull
sudo python3 scripts/storage-preflight.py /etc/signage/storage.json
sudo install -m 0644 deploy/signage.service /etc/systemd/system/signage.service
sudo install -m 0644 deploy/signage.timer /etc/systemd/system/signage.timer
sudo systemctl daemon-reload
sudo systemctl enable --now signage.timer
sudo systemctl start signage.service
```

Edit the unit's `WorkingDirectory`, `RequiresMountsFor` and command paths when your locations differ. Do not run an old standalone Compose stack alongside it. Stop the existing stack with its original project name before migrating. Back up first.

The systemd overlay sets Docker restart policy to **no** for these two containers. This is deliberate: a Docker-daemon auto-restart must not race ahead of required NAS mounts. The service orders itself after the mount units, runs the preflight, then starts Compose in the foreground. Its `--force-recreate` obtains fresh bind mounts while preserving host data. The timer retries inactive/failed startup every 30 seconds, including an initial mount dependency failure. An already-running service is not restarted by each timer tick. The service also restarts after an unexpected exit; neither mechanism bypasses the storage preflight.

For planned maintenance, stop **both** timer and service:

```sh
sudo systemctl stop signage.timer signage.service
# ...maintenance...
sudo systemctl start signage.timer signage.service
```

Otherwise the enabled timer will start an intentionally stopped service again. Build new images explicitly before a code upgrade: the boot command uses `--no-build --pull never`.

## 4. What happens during failure

An absent/incorrect media mount prevents startup. A missing browse share fails browsing/import/export rather than creating local substitutes. The database is still persisted on local disk. The queue has one transfer worker and at most two concurrent directory scans; a hung NFS syscall cannot create unlimited transfer threads. Browse requests have a response deadline, but kernel-level hard-NFS I/O is not always cancellable. A NAS failure can still interrupt playback or stall transfers; this is not a high-availability cluster.

If a mount is actually removed and recreated while containers are running, their existing private bind mounts may retain the old mount. Recover the host mount, then restart `signage.service` so containers are recreated with fresh binds. Ordinary transient network reconnection is not the same as a remount. Never repair either condition by creating empty `originals`, `work` or `rendered` folders on local disk.

Imports use a persistent job identity. If the original was accepted before an interrupted acknowledgement, retry recognizes the existing library item. Exports persist the expected content digest before atomic no-overwrite publication; recovery can recognize a file published before the completion transaction. Export uses Linux no-replace rename when available, otherwise a hard-link publication. A NAS supporting neither fails explicitly; the portal never substitutes an overwriting rename. Directory/file permissions or NAS server capabilities can therefore require operator attention.

SQLite connections explicitly request `synchronous=FULL`; file writes are flushed before acknowledgement where implemented. Physical power-loss durability still depends on the host filesystem, NAS, write caches and their flush semantics. The test suite simulates interrupted operations and a fresh process, not a physical power cut.

## 5. Acceptance checks on the real installation

Start with one display and non-sensitive content. Confirm the same playback URL and assignments after a web restart, Docker restart and full host reboot. Boot once with the NAS unavailable: the service must remain stopped/failed with no local media replacement. Restore the NAS and verify the retry timer starts it. Repeat an import and an export around a controlled restart and check for duplicates or overwritten files. Test a runtime NAS interruption and a deliberate remount separately. Inspect `journalctl -u signage.service` and the Activity/transfer records.

Keep stable DNS and a reserved/static server address, with certificates trusted by the displays. Persistence is not a backup. Back up local SQLite using its backup mechanism or while stopped, preserve configuration/secrets, snapshot NAS originals and rendered media, and test restoration as one matching set.

References: [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/), [Compose up lifecycle](https://docs.docker.com/reference/cli/docker/compose/up/), [systemd unit dependencies](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html), [systemd timers](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html).

## Presentation-layer persistence

The banner revision stores stream presentation JSON/revisions and named preset copies in the same local SQLite database. Timers are absolute timestamps, not relative process timers. The transfer queue also records whether a NAS item was requested as a graphic, so recovering a queued SVG import retains its conversion mode. Graphic originals and normalized PNG/GIF media use the existing guarded NAS folders. No extra mount, host privilege or secret exposed to the browser is needed.

Saving those records was tested, including reopening presentation state in a fresh Python process. Actual host reboot, network remount and physical-display validation remain staging requirements. Settings persistence is not a guarantee of cached media or an accurate device clock while offline.
