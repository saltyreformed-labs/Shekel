# Shekel Operations Runbook: Troubleshooting

Section 7 of the runbook, whose contents and quick reference are
[`docs/runbook.md`](../runbook.md).

## 7. Troubleshooting

### 7.1 Common Issues

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| App unreachable externally | cloudflared service down | `sudo systemctl status cloudflared` then `sudo systemctl restart cloudflared` |
| App unreachable externally | Docker containers down | `docker compose ps` then `docker compose up -d` |
| "Access Denied" on all requests | Cloudflare Access misconfigured | Check Access policy in Zero Trust dashboard; verify email is in the allowed list |
| Cloudflare 502 Bad Gateway | Nginx or app container unhealthy | `docker compose ps` to check health; `docker logs shekel-prod-nginx` and `docker logs shekel-prod-app` for errors |
| Cloudflare 522 Connection Timed Out | cloudflared cannot reach Nginx | Verify Nginx is running: `docker compose ps`. Check cloudflared logs: `journalctl -u cloudflared -n 20` |
| 429 on first login attempt | Cloudflare rate limit too aggressive | Check WAF rules in Cloudflare dashboard (Security > WAF > Rate limiting rules); increase threshold |
| 429 after a few login attempts | Flask-Limiter rate limit (5/15min) | Wait 15 minutes. To clear immediately: `docker restart shekel-prod-valkey` -- counters are stored in Valkey's memory alone (persistence off), so restarting it clears them, while restarting the app does NOT. No Valkey user may flush: the app's ACL user runs only the limiter's commands (`deploy/valkey/users.acl`). Limited requests fail closed for the seconds it is down |
| 500 on every login attempt | Valkey container down or unreachable, the app's Valkey password wrong, or a grant missing from Valkey's ACL | App is configured fail-closed: rate-limit storage outage rejects every limited request. `docker compose ps valkey` to check status; `docker logs shekel-prod-valkey --tail 50` for errors (start.sh names an empty password or a missing `user shekel` rule); `docker compose up -d valkey` to restart. A healthy Valkey with 500s still is a password the two do not share (production's override must mount `secrets/valkey_password` into both, and blank both `VALKEY_PASSWORD` variables) or a socket the app cannot reach: `docker compose exec valkey stat -c '%a %u:%g %n' /run/valkey /run/valkey/valkey.sock` must print `2710 999:1000` and `660 999:1000`, and the app must mount the `valkey-socket` volume as `docker-compose.yml` declares. A grant missing from the host's copy of `deploy/valkey/users.acl` shows in the app's log as `ACL failure in script: User shekel has no permissions to run the '<command>' command` for a command the limiter's scripts call (`lindex`, `lpush`, `ltrim`, `expire`), or as `NoPermissionError: User shekel has no permissions to run the '<command>' command` for one it sends itself (`evalsha`, `script|load`): correct the copy, then `cd /opt/docker/shekel && docker compose up -d --wait --force-recreate valkey`. Login resumes immediately once Valkey accepts the app's login |
| Wrong IP in logs (127.0.0.1) | Nginx real IP config issue | Verify `set_real_ip_from` and `real_ip_header CF-Connecting-IP` in the active Nginx config: `deploy/nginx-bundled/nginx.conf` (bundled mode) or `deploy/nginx-shared/nginx.conf` (shared mode -- the runtime copy is `/opt/docker/nginx/nginx.conf`) |
| No logs in Grafana | Promtail not scraping | `docker logs promtail`. Verify `monitoring` network exists. Verify app is on the network: `docker network inspect monitoring` |
| CSS/JS not loading | Static files volume issue | `docker exec shekel-prod-nginx ls /var/www/static/` to verify files exist. The entrypoint copies them on every start, so restart the app: `docker compose restart app` |
| Health check returns 500 | Database connection issue | `docker exec shekel-prod-db pg_isready -U shekel_user -d shekel`. Check `docker logs shekel-prod-app --tail 20` |
| `docker compose up` ends `service "migrate" didn't complete successfully: exit N`, the app container `Created` and not running | The deploy failed in the migrate service, so compose did not start the app | `docker logs shekel-prod-migrate` names the deploy step that failed. In production `shekel-deploy` saved that log beside its dump and decided the rollback (2.3). Repair what it names, then `docker compose up -d` runs the deploy again and starts the app |
| The deploy stops naming `SECRET_KEY` (`SECRET_KEY environment variable is not set, so a guess record's email digest cannot be computed`) | A database from before 2026-11-09 holding a failed sign-in count or a lock: its migration needs the web secret key, which the migrate service does not hold | Run that one deploy with the key supplied by hand: 7.3, "A deploy stops naming SECRET_KEY" |
| Database backup failed | Container down or disk full | `docker ps` to check shekel-prod-db. `df -h` to check disk space |
| NAS backup failed | NAS not mounted | `mount \| grep nas`. Remount: `sudo mount -a` |
| App fails with `connection requires SSL` | Shared-mode TLS not staged | Run `sudo ./scripts/generate_pg_cert.sh` (Commit C-37). See §4.12 Postgres TLS for the full procedure. |
| Postgres logs `could not load private key file` | `server.key` mode not 0600 or wrong owner | Re-run `sudo ./scripts/generate_pg_cert.sh --force`; the chown step is what closes this. |
| Postgres logs `private key file has group or world access` | Same as above | Same fix as above. |
| `SHOW ssl;` returns `off` after override applied | db service did not pick up the override | `docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --force-recreate db` |
| Cloudflare Access never prompts | Missing `originRequest.access` block | See §6.4a; check that `<TEAM_NAME>` and `<AUD_TAG>` placeholders are replaced and `cloudflared --config ... ingress validate` passes. |
| Metrics endpoint reachable from app container | Missing `metrics: 127.0.0.1:2000` directive | See §6.4b. Restart cloudflared after editing `/etc/cloudflared/config.yml`. |

### 7.2 Log Locations

| Log | Command | Contents |
|-----|---------|----------|
| Flask app (JSON) | `docker logs shekel-prod-app` | Request logs, auth events, business events, errors |
| The deploy | `docker logs shekel-prod-migrate --tail 100` | The deploy's steps, in the migrate service: the schemas, the grants, the migrations and the seeds, and the step that failed. An `up` that changes nothing in the service starts the same container again, so its log holds every deploy since compose last made it, the newest last |
| Audit cleanup | `journalctl -u shekel-audit-cleanup.service` | The nightly cleanup's count of deleted rows, or its error |
| Nginx (JSON) | `docker logs shekel-prod-nginx` | HTTP access logs, upstream errors |
| PostgreSQL | `docker logs shekel-prod-db` | Database server logs, connection errors |
| Cloudflared | `journalctl -u cloudflared` | Tunnel connection logs, reconnects |
| Backups | `cat /var/log/shekel_backup.log` | Cron job output for backup, retention, verify, integrity |
| Grafana | `docker logs grafana` | Grafana server logs |
| Promtail | `docker logs promtail` | Log scraper status, discovery errors |

### 7.3 Emergency Procedures

#### Application is down -- restore service quickly

```bash
# 1. Identify which container is unhealthy.
docker compose ps

# 2. Check container logs for the failing service.  An app container
#    left `Created` means the deploy failed: the migrate log says why.
docker logs shekel-prod-app --tail 50
docker logs shekel-prod-migrate --tail 50
docker logs shekel-prod-nginx --tail 20
docker logs shekel-prod-db --tail 20

# 3. Restart the failing service.
docker compose restart app    # or: nginx, db

# 4. If the app container won't start, restart the full stack.
docker compose down && docker compose up -d

# 5. Verify recovery.
curl -s http://localhost/health
```

#### Database is corrupted -- restore from backup

```bash
# 1. Identify the latest good backup.
ls -lht /var/backups/shekel/

# 2. Restore (will prompt for confirmation).
./scripts/restore.sh /var/backups/shekel/shekel_backup_20260315_020000.sql.gz

# 3. Verify the restore.
curl -s http://localhost/health
cd /opt/docker/shekel && docker compose run --rm --no-deps --pull never app \
    python scripts/integrity_check.py --verbose
```

If local backups are corrupted, restore from NAS:

```bash
cp /mnt/nas/backups/shekel/shekel_backup_20260315_020000.sql.gz /tmp/
./scripts/restore.sh /tmp/shekel_backup_20260315_020000.sql.gz
rm /tmp/shekel_backup_20260315_020000.sql.gz
```

#### Locked out of the app -- MFA device lost

```bash
# SSH to the Proxmox host.  The reset asks y/N before it changes anything.
cd /opt/docker/shekel && docker compose run --rm --no-deps --pull never app \
    python scripts/reset_mfa.py your-email@example.com
# Output: MFA has been disabled for your-email@example.com; their sessions
# are ended and their trusted devices forgotten.

# Log in with email + password (MFA is now disabled).
# Re-enable MFA in Settings > Security after logging in.
```

#### A deploy stops naming SECRET_KEY

The deploy runs in the migrate service, which holds the owner's password and no web secret (ruling
plan#3450). One migration reads one: `3748145b83e3` (2026-11-09) names each failed sign-in count's
record by a digest keyed with `SECRET_KEY`, and needs the key only on a database older than it that
holds a count or a lock. Such a deploy stops with
`SECRET_KEY environment variable is not set, so a guess record's email digest cannot be computed`
and rolls back, changing nothing (`app/services/guess_record_service.py`, `_digest_key`). Production
is past it; an install or a restored backup from before 2026-11-10 may not be.

Run that one deploy with the key supplied by hand, then start the stack as usual. `-e SECRET_KEY`
names no value, so the key never appears on a command line: compose takes it from the shell, else
from `.env`.

```bash
cd /opt/docker/shekel

# Production, whose key is the file secrets/secret_key:
SECRET_KEY="$(cat secrets/secret_key)" \
    docker compose run --rm --no-deps --pull never -e SECRET_KEY migrate

# A self-hosted install, whose .env holds the key:
docker compose run --rm --no-deps --pull never -e SECRET_KEY migrate

# Then the stack: its deploy has nothing left to name, and the app starts.
docker compose up -d
```

`run ... migrate` with no command runs the deploy, `scripts/deploy.sh`, the service's own command.
The key is in that container's environment only while it runs: `--rm` removes it.

Measured 2026-11-12 on a throwaway stack built from card plan#2166's branch, with production's
secret files mounted. Downgraded to `b7e4c19a2d60`, the revision before the migration, with one
user's failed sign-in count at 3, the deploy stopped with the message above and exit 1, leaving the
stamp at `b7e4c19a2d60` and the count on the user's row. The production form above then deployed to
the head, `Database initialised: ONE transaction, committed.`, carrying the count of 3 into one
guess record, and `docker compose up -d` deployed again and started the app healthy. Run with the
shell variable set, the run's `SECRET_KEY` held the file's 64 characters; with none, `.env`'s value;
without `-e`, nothing.

#### Locked out of Cloudflare Access

1. Log into the Cloudflare dashboard directly at `https://dash.cloudflare.com` (this is independent
   of the tunnel and Access policy)
2. Navigate to **Zero Trust** > **Access** > **Applications**
3. Either:
   - Add your current email to the **Allowed Users** policy, or
   - Temporarily set the policy action to **Bypass** (allows everyone)
4. Access the app and verify the fix
5. Restore the original policy

#### Complete disaster recovery -- host is lost

1. Provision a new Proxmox host with Docker and Docker Compose
2. Clone the repository: `git clone <repo-url> /opt/shekel`
3. Reconstruct `.env` from `.env.example`:
   - `SECRET_KEY`: generate new (users must re-login)
   - `TOTP_ENCRYPTION_KEY`: use the backed-up key from password manager, or generate new (users must
     re-enroll MFA)
   - `POSTGRES_PASSWORD`, `OWNER_ROLE_PASSWORD`, `APP_ROLE_PASSWORD` and `SHEKEL_VALKEY_PASSWORD`:
     generate new. A backup holds no login or password (it is taken `--no-owner --no-privileges`),
     and the new cluster's first boot makes the superuser from the first and both logins from the
     next two (`deploy/postgres/provision_roles.sh`)
4. Create the monitoring network: `docker network create monitoring`
5. Start the stack: `docker compose up -d`
6. Restore from NAS backup: `./scripts/restore.sh /mnt/nas/backups/shekel/<latest>.sql.gz`
7. Re-install cloudflared and configure the tunnel (see `cloudflared/config.yml`)
8. Re-configure cron jobs (see §1 Cron Schedule)
9. Verify: `curl -s http://localhost/health` and `curl -s https://<domain>/health`

See `docs/runbook_secrets.md` for detailed secret reconstruction procedures.
