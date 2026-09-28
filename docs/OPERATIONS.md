# Operations and maintenance

## Daily workflow

Use **Library** to upload/import files or add approved sources. Wait for conversion before publishing file items. Use **Place** for rotation or a bounded override. Use **Streams → Banners & motion** for an unpublished overlay draft and **Apply to stream** when ready. Use **Displays** for portal assignments and permanent URLs, not hardware control. The Activity log records changes.

A NAS import is a snapshot: editing the file on the NAS does not silently change its already-imported rendition. Export writes a unique original-file copy; it does not overwrite existing destination content. Interrupted transfer records remain in the persistent database. See [PERSISTENCE.md](PERSISTENCE.md) for retry and recovery semantics.

## Check problems at the appropriate layer

| Symptom | Check |
|---|---|
| Workspace updates unavailable | Browser/server/proxy reachability and web logs; the last overview may be stale |
| Browser is offline | Browser network report; retry once networking returns |
| Display heartbeat is stale | Player URL, device browser, network path and last-seen time—not merely television power |
| Converter offline or queued files not progressing | Worker logs, work-directory access/heartbeat and NAS mount permissions |
| NAS browser/import/export fails | Approved share config, source/marker identity, ACLs, job error and actual mount state |
| Raw URL/HLS not playing | Allowed origins, actual direct source, CORS, codec/browser support and HLS asset installation |
| Deck fonts/layout differ | Conversion logs, installed/embedded fonts; use PDF for more predictable static layouts |

For a systemd-managed deployment:

```sh
sudo systemctl status signage.service signage.timer
sudo journalctl -u signage.service --since today
cd /opt/signage
sudo docker compose --project-name signage -f docker-compose.yml -f docker-compose.nas.yml -f deploy/compose.systemd.yml logs --tail=200 web worker
```

Use the same project name and files as the installed units. Do not launch a second stack while troubleshooting. Do not remove read-only mounts, media markers, TLS requirements or container isolation to silence a failing check.

## Backup and restore

Back up persistent local database state, local secret/configuration, and NAS originals/rendered media as a matching set. SQLite needs its backup mechanism or a stopped service for a consistent copy; blindly copying an active database while ignoring the WAL is not the documented recovery path. Restrict backup permissions. The runtime files and real logs do not belong in Git.

For a coordinated maintenance snapshot, stop both `signage.timer` and `signage.service`, capture the state/configuration and NAS snapshot, then start both. On restore, use matching application/database/media versions, preserve volume IDs, restore ownership and re-run the storage preflight. Test restoration before depending on a backup.

## Upgrade safely

Read [UPGRADING.md](UPGRADING.md) for schema and web/worker compatibility changes. For this status/documentation pass, no backend schema, media contract, player route or saved address changed. Rebuild/redeploy the web image and refresh the administrator page; keep the worker image compatible with the existing graphics/source revision.

With systemd managing the service:

```sh
sudo systemctl stop signage.timer signage.service
# Take a consistent backup; install/review the next source revision.
cd /opt/signage
sudo docker compose --project-name signage -f docker-compose.yml -f docker-compose.nas.yml -f deploy/compose.systemd.yml build --pull
sudo python3 scripts/storage-preflight.py /etc/signage/storage.json
sudo systemctl start signage.timer signage.service
```

This creates a maintenance interruption. A production image rollout and database rollback need a tested plan; no automated deployment or rollback action is configured in this repository. Do not switch to the example's empty state directories on an existing install. After a true NAS remount, restart the service to recreate bind mounts as documented in the persistence guide.

## Release checks

Run `scripts/check.sh` in a clean environment; then run `scripts/release-check.sh` with a staging `.env`, Docker, Node and pip-audit installed. It checks configured builds; it does not deploy services. Add your image/OS scan and real display, proxy, NAS, reboot and overnight acceptance evidence. Read [REPOSITORY-VALIDATION.md](REPOSITORY-VALIDATION.md), not a past screenshot or stale test count, for the current evidence boundary.
