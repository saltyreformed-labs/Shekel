# Shekel Operations Runbook: Deployment

Section 2 of the runbook, whose contents and quick reference are
[`docs/runbook.md`](../runbook.md).

## 2. Deployment

### 2.1 Deploying a New Version

Production deploys with `shekel-deploy`, which pins `ghcr.io/saltyreformed-labs/shekel@sha256:...`,
cosign-verified, on the hardened stack at `/opt/docker/shekel`. A self-hosted install runs the image
CI builds and signs, and updates by 2.2. Development is `docker-compose.dev.yml`.

```bash
shekel-deploy                  # pull :latest, deploy if the digest is new
shekel-deploy sha256:abc...    # deploy one specific digest
shekel-deploy --dry-run        # resolve, verify, report the pre-flight, change nothing
shekel-deploy --dump-only      # step 4's dump alone, its path printed last; nothing else
```

`--dump-only` takes no other argument (`-h` aside). Beyond the daemon check, the database's
running-check, the dump and its readback it makes no call: no pull, no pre-flight, no pin written,
and no container started, stopped or recreated. It names its dump
`shekel_prod_dump_<current pin>_<ts>.dump`, never `predeploy`, so a release rehearsal's same-day
dump is never mistaken for a deploy's restore point.

**What it does (in order):**

1. Resolves the target digest (pulls `:latest`, or takes an explicit `sha256:...`). No-ops if it
   already matches the current pin.
2. `cosign verify` against the keyless OIDC identity of `.github/workflows/docker-publish.yml`.
   Aborts if `cosign` is missing; `--no-verify` is for emergencies only.
3. **Migration pre-flight.** Lists `migrations/versions` in *both* images, reads the database's
   Alembic stamp, and refuses up front a target that cannot resolve it (an image older than the
   database). It reports the revisions the target adds: a **migration-bearing** release. It also
   asks the previous image for `scripts/owner_entrypoint.sh`, which the migrate service runs: an
   image built before card plan#2166 has none, so it cannot run under these compose files, and a
   failure is then not rolled back by a re-pin (2.3). `--dry-run` reports all of this without
   deploying.
4. **`pg_dump -Fc` of the whole database** into `~/shekel-backups/` (override with
   `SHEKEL_BACKUP_DIR`), written to a `.part` file and renamed only after `pg_restore -f /dev/null`
   decodes every data block of it (a table-of-contents read passes a truncated archive).
   **No dump, no deploy** -- a dump failure aborts *before* the pin is touched.
5. Rewrites `SHEKEL_IMAGE_DIGEST` in `/opt/docker/shekel/.env` and `docker compose up -d app`.
   Compose runs the one-shot migrate service first, from the same image, and starts the app only
   once it exits 0. The deploy runs there: its step 3 runs the migrations, the reference seed, the
   three ledger hooks and the audit-trigger check in ONE transaction, committed together or not at
   all. A deploy that fails fails the `up`, with the app's new container created and not started.
6. Polls the app container's healthcheck for up to 4 minutes; ntfy ping either way.
7. **On failure**, stops the migrate container if it is still running the deploy, and the new app
   container, re-reads the stamp, and either re-pins the previous digest or refuses (2.3).

After a deploy, `deploy/verify_prod_deploy.sh <the deploy's start time>`, run from this checkout,
checks production read-only: health, the login gate's redirects, and the log since the deploy. Its
header names its checks, and the one false failure it knows (BAL-628, after an in-place restart).

**The canonical copy is `deploy/shekel-deploy.sh` in this repo**, and
`/opt/docker/scripts/shekel-deploy.sh` is a symlink to it. That means
**the checked-out working tree is what rolls production** -- check out the revision you intend to
deploy from before running it. Install or repair the link with:

```bash
sudo ln -sfn /home/josh/projects/Shekel/deploy/shekel-deploy.sh \
             /opt/docker/scripts/shekel-deploy.sh
```

### 2.2 Updating a Self-Hosted Install

A self-hosted install is the README's Quick Start: the image CI builds and signs on `main`, and the
configuration files of the commit that image was built from. A release changes both, so an update
takes both, by the README's Updating section. Migrations run in the migrate service, which
`docker compose up` runs from the new image before it starts the app. The README's steps check no
signature; `deploy/README.md`'s `cosign verify` command does.

### 2.3 Rolling Back

**In production, `shekel-deploy`'s re-read stamp decides.**

When the new image fails (compose rejects it, as it does when the deploy fails in the migrate
service; its app container reports unhealthy; or it is not healthy within 4 minutes),
`shekel-deploy` first stops the migrate container if it is still running the deploy, so step 3 can
commit nothing more, and stops the new app container, so a failed release is not left serving. Then
it re-reads the database's Alembic stamp. That stamp, not the release, decides what happens:

- **The previous image can resolve the stamp.** The release migrated nothing, or the deploy's step 3
  failed and rolled back: since plan step `X-cv` the migrations, the reference seed, the ledger
  hooks and the audit-trigger check are ONE transaction, so a failure there leaves the stamp unmoved
  even for a migration-bearing release. The script saves the failed containers' logs beside the
  dump, the migrate container's then the app's, in one file (the dump's name with
  `.failed-container.log` for `.dump`), and re-pins the previous digest. After a health failure it
  then waits for that image to be healthy; after a compose failure it does not wait. Nothing is
  restored. When the previous image does not come up either, see below.
- **The previous image cannot resolve the stamp.** Step 3 COMMITTED and something later failed (the
  deploy's step 4, the first user's seed; or the app's start, its static copy, or the app never
  became healthy). Re-pinning would give a second failed deploy -- reproduced as
  `CommandError: Can't locate revision identified by ...` (finding F-8) -- so the script
  **refuses**, leaves the pin at the new digest, saves the logs, and names the ways out:
  1. **Start the new pin again**, offered when the new image resolves the stamp (it wrote it):
     `cd /opt/docker/shekel && docker compose up -d app`, which runs the deploy again first and then
     starts the app. Nothing is restored and nothing is lost. The right first move when the log
     shows a transient failure, such as a release that was only slower than the health window.
  2. **Restore the pre-deploy dump** and re-pin the previous digest (below). This discards
     everything written since the dump.
- **The stamp could not be re-read** (the database container did not answer). The script claims
  nothing about a commit and leaves the pin. Once the database answers, roll back through
  `shekel-deploy <old-digest>`: its pre-flight reads the stamp and refuses up front, naming the
  restore, if the previous image cannot resolve it.
- **The migrate container could not be stopped.** It may still be inside step 3, so no stamp read is
  final: the script refuses and leaves the pin. `docker stop shekel-prod-migrate`, then
  `shekel-deploy <old-digest>`. An app container that will not stop is a warning only (ruling
  plan#3539): it runs no migration and cannot move the stamp, so the decision goes on, and the
  output says it may still be serving the new image.
- **The previous image cannot run under the compose files in place.** An image built before card
  plan#2166 has no `scripts/owner_entrypoint.sh`, which the migrate service runs, so re-pinning it
  would bring nothing up; the first release of that card is this case. The pre-flight asks, and
  `--dry-run` says so. On a failure the script refuses and leaves the pin at the new digest, and the
  way back it names is the compose files that image ran under as well as its digest, with what the
  re-read stamp shows: whether that image resolves it or the dump must be restored first.

**Read the saved log first** (the path is in the output and the ntfy alert). The migrate container's
log comes first and names the deploy step that failed; the app's follows. Together they say whether
a restart can clear it.

**When the re-pinned previous image does not come up either.** After a health failure the script
then ends `rollback container also unhealthy; manual intervention required`. It prints the same line
when the re-pin's own `compose up` failed and usually nothing started (finding BAL-540); after a
compose failure it prints `WARN: revert compose up also failed; container state unknown.` instead.
The re-pin's `up` runs the previous image's deploy in the migrate service first, so a fault both
images meet in the deploy fails that `up` as well, with the app created and not started:
`docker compose ps -a` shows `shekel-prod-migrate` exited with a nonzero code, and
`docker logs shekel-prod-migrate` names the step. The previous image knows the stamp's revision, so
the migration level is not the cause; something both images meet is: schema drift the stamp does not
record, the data, or the environment. Whether the dump can help turns on step 3: if the saved log
has no `Database initialised: ONE transaction, committed.`, the failed release committed nothing
past the deploy's steps 1 and 2 (idempotent: the schemas and the web login's grants, which every
deploy re-runs), so the fault predates the deploy and the dump holds it too: restoring cannot help,
and the fault is repaired in place. If step 3 did commit, what it wrote may be the fault, and
restoring the dump (below) is a candidate.

An app whose deploy passed but whose own start fails crash-loops under `restart: unless-stopped`,
each pass restarting gunicorn alone: since card plan#2166 the app's start runs no deploy. Docker
waits between passes, from a tenth of a second up to about a minute, so `docker compose ps` may show
it `Up` for seconds at a time with `(health: starting)` during a pass or `Restarting` during a wait.
The tell is `docker inspect shekel-prod-app --format '{{.State.Status}} {{.RestartCount}}'`: a state
of `restarting`, or a restart count that grows between two readings two minutes apart. Stop it
before repairing and start it after, with an `up` that runs the deploy again first:

```bash
cd /opt/docker/shekel
docker compose stop app      # a crash-looping app retries on its own under `restart: unless-stopped`
# ... repair what the saved log names ...
docker compose up -d app     # the deploy, in the migrate service, then the app
```

A missing audit trigger is the case the X-cv rehearsal measured: every image since C-13 counts them
at every deploy, since card plan#2166 in the migrate service, so the release's deploy refuses and
the re-pinned image's deploy refuses the same database, and no app is left running. Since ruling
`R-BAL129` the release's refusal names each audited table missing its trigger and, for a trigger
dropped by hand, prints the statements that re-create them, each ending in `;`. Each has this form
and runs as the database owner (the app role has DML only), between the stop and the start above:

```sql
CREATE TRIGGER audit_<table> AFTER INSERT OR UPDATE OR DELETE ON <schema>.<table>
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func();
```

```bash
docker exec shekel-prod-db psql -U shekel_user -d shekel -v ON_ERROR_STOP=1 \
    -c '<the statements from the saved log>'
```

If the refusal names a table newly added to `AUDITED_TABLES`, its migration did not attach the
trigger, and the migration is the fix. An image older than `R-BAL129` prints only a count, and its
`flask db upgrade` advice does not re-create a dropped trigger. (Measured 2026-09-23 in the X-cv
rehearsal's second pass, before card plan#2166, when the app's own start ran the deploy, on a copy
of production's data with one audit trigger dropped and the 6-3 release image `876fbf5c5bf7` as the
previous image: the release's saved log named `budget.transactions` and printed one statement;
`docker compose ps` showed the crash-looping previous container as `Up 4 seconds (health: starting)`
51 seconds after the re-pin created it, after 5 restarts: its first pass ran 11 seconds and the next
four about 8 seconds each, and the waits between them grew from 0.1 to 1.6 seconds, where an earlier
rehearsal of the same image and fault ran 11 to 12 second passes with waits of at most a quarter of
a second, so that container was up nearly all the time; the stop, that statement run through `psql`
as printed, and the start above brought it up healthy in about 15 seconds, with the re-created
trigger identical to the dropped one and no table changed.)

**Restoring discards everything written since the dump was taken** -- minutes of real entries in a
budgeting app. Capture the failed state first so that window stays recoverable.

```bash
cd /opt/docker/shekel

# 1. STOP THE APP FIRST.  A live app writes during the restore, and under
#    `restart: unless-stopped` a crash-looping one comes back up until it
#    is stopped.
docker compose stop app

# 2. Keep the failed state before overwriting it.
docker exec shekel-prod-db pg_dump -U shekel_user -d shekel -Fc \
    >'/home/josh/shekel-backups/failed-state.dump'

# 3. Restore the dump shekel-deploy named in its output.
#    --single-transaction --exit-on-error are NOT optional: without them a
#    stream that ends early leaves the database EMPTY after --clean has
#    already dropped everything, with no transaction to roll back (OPS/SH-01).
docker exec -i shekel-prod-db pg_restore -U shekel_user \
    -d shekel --clean --if-exists --single-transaction --exit-on-error \
    <'/home/josh/shekel-backups/shekel_prod_predeploy_<digest>_<ts>.dump'

#    Then hand every object to the owner login.  A dump from before the
#    cutover names the superuser as each one's owner, which the owner's
#    deploy cannot change; on a later dump this changes nothing.
docker exec shekel-prod-db /docker-entrypoint-initdb.d/10-provision-roles.sh

# 4. --clean drops only what the archive knows about, so a table the failed
#    migration created survives and will collide when it is retried.
docker exec shekel-prod-db psql -U shekel_user -d shekel -c '\dt budget.*'

# 5. Only now put the pin back.
sed -i 's|^SHEKEL_IMAGE_DIGEST=.*|SHEKEL_IMAGE_DIGEST=<old-digest>|' /opt/docker/shekel/.env
docker compose up -d app
```

### 2.4 First-Time Setup

A new install follows the README's Quick Start. The host-side jobs of 1's Cron Schedule
(`backup.sh`, `backup_retention.sh`, `verify_backup.sh`) are neither among the files it downloads
nor in the image, so they run from a checkout of this repository.

### 2.5 Shared-mode Deployment and Config Sync

The maintainer's homelab runs Shekel in **shared mode**: the bundled `shekel-prod-nginx` service is
parked in the `disabled` profile and a separately-managed Nginx at `/opt/docker/nginx/` proxies
traffic from the dedicated `shekel-frontend` Docker bridge to `shekel-prod-app:8000`. The Shekel app
is NOT on the wider `homelab` network: that network hosts unrelated co-tenants (Jellyfin, Immich,
UniFi) and would expose Gunicorn directly to any of their compromise paths (audit findings
F-020/F-129 closed in Commit C-33). The version-controlled copies of the runtime configs live under
`deploy/`:

| Repo path | Runtime path on the host |
|-----------|--------------------------|
| `docker-compose.yml` | `/opt/docker/shekel/docker-compose.yml` |
| `deploy/docker-compose.prod.yml` | `/opt/docker/shekel/docker-compose.override.yml` |
| `deploy/valkey/` (`users.acl`, `start.sh`) | `/opt/docker/shekel/deploy/valkey/` |
| `deploy/nginx-shared/nginx.conf` | `/opt/docker/nginx/nginx.conf` |
| `deploy/nginx-shared/conf.d/shekel.conf` | `/opt/docker/nginx/conf.d/shekel.conf` |

The repo is the source of truth. The on-host copies must match byte-for-byte; drift between them is
a deployment-integrity bug (security-2026-04-15 finding F-021). `shekel-deploy` copies none of them:
a release that changes one runs its host steps (2.6).

#### Bringing up shared mode (first-time)

```bash
# On the host:
cd /opt/shekel
git checkout dev && git pull --ff-only

# Ensure the dedicated shekel-frontend network exists.  The subnet
# is pinned so Gunicorn's FORWARDED_ALLOW_IPS literal and the shared
# Nginx's set_real_ip_from directive both reference the same CIDR.
# Audit finding F-015 + F-020 (Commit C-33).
docker network ls --filter name=shekel-frontend
# If missing:
docker network create shekel-frontend \
    --driver bridge \
    --subnet 172.32.0.0/24

# Edit /opt/docker/docker-compose.yml so the shared nginx and
# cloudflared services attach to shekel-frontend in addition to the
# homelab bridge they already join.  Apply with:
#   cd /opt/docker && docker compose up -d nginx cloudflared

# Edit /opt/docker/cloudflared/config.yml so the shekel ingress rule
# points to ``http://nginx:80`` instead of straight at
# ``http://shekel-prod-app:8000``.  Restart cloudflared after the
# edit:
#   cd /opt/docker && docker compose up -d cloudflared

# The runtime override location is /opt/docker/shekel/.  Either:
#  (a) keep that directory as a thin wrapper that copies the files
#      from the repo on each pull, or
#  (b) invoke compose against the repo files directly.
docker compose \
  -f /opt/shekel/docker-compose.yml \
  -f /opt/shekel/deploy/docker-compose.prod.yml \
  up -d
```

#### Editing the shared-Nginx configuration

```bash
# 1. Edit and commit on the dev branch.
#    (work happens in the repo, NOT on the runtime files)
$EDITOR deploy/nginx-shared/nginx.conf
$EDITOR deploy/nginx-shared/conf.d/shekel.conf
git add deploy/nginx-shared/
git commit -m "deploy(nginx): <what changed>"
git push

# 2. On the host, pull and copy into place.
cd /opt/shekel
git pull --ff-only
sudo cp deploy/nginx-shared/nginx.conf            /opt/docker/nginx/nginx.conf
sudo cp deploy/nginx-shared/conf.d/shekel.conf    /opt/docker/nginx/conf.d/shekel.conf

# 3. Validate before reload.
sudo docker exec nginx nginx -t

# 4. Reload without dropping connections.
sudo docker exec nginx nginx -s reload

# 5. Confirm the change is live.
curl -sSI https://shekel.saltyreformed.com | head
```

If `nginx -t` reports an error in step 3, do not reload. Restore the previous file with
`git restore` on the host or `git checkout HEAD~1 -- deploy/nginx-shared/...`, copy it back into
place, and rerun `nginx -t`.

#### Editing the shared-mode compose override

```bash
# 1. Edit and commit on the dev branch.
$EDITOR deploy/docker-compose.prod.yml
git add deploy/docker-compose.prod.yml
git commit -m "deploy(compose): <what changed>"
git push

# 2. On the host, pull and copy into place.
cd /opt/shekel
git pull --ff-only
sudo cp deploy/docker-compose.prod.yml /opt/docker/shekel/docker-compose.override.yml

# 3. Validate the merged compose.
cd /opt/docker/shekel
docker compose config >/dev/null

# 4. Recreate affected services.
docker compose up -d
```

#### Verifying the host matches the repo

A drift-check helper is reserved at `scripts/config_audit.py` (implemented in remediation commit
C-49). Until that lands, verify manually:

```bash
diff -u /opt/shekel/deploy/nginx-shared/nginx.conf            /opt/docker/nginx/nginx.conf
diff -u /opt/shekel/deploy/nginx-shared/conf.d/shekel.conf    /opt/docker/nginx/conf.d/shekel.conf
diff -u /opt/shekel/docker-compose.yml                        /opt/docker/shekel/docker-compose.yml
diff -u /opt/shekel/deploy/docker-compose.prod.yml            /opt/docker/shekel/docker-compose.override.yml
diff -ru /opt/shekel/deploy/valkey                            /opt/docker/shekel/deploy/valkey
```

Any non-empty diff is an incident: investigate before re-syncing, because the host change may carry
an undocumented production fix that must be brought into the repo first.

#### One-shot reconciliation script

`scripts/reconcile_prod_to_canonical.sh` bundles the seven host-side steps that bring a drifted
`/opt/docker/shekel/` runtime back to the repo's canonical state. Use this when the host has
diverged substantially (multiple files, secrets directory missing, network attachment wrong) or to
bootstrap a fresh shared-mode deployment from a host that previously ran bundled-mode. It reads
`SHEKEL_VALKEY_PASSWORD` from `.env` and stops at step 3 without it, so on a host from before the
Valkey switch, whose `.env` names `SHEKEL_REDIS_PASSWORD`, rename that variable first.

```bash
# On the host, from the repo root:
cd /opt/shekel
git pull --ff-only
bash scripts/reconcile_prod_to_canonical.sh
# sudo will prompt once -- the script needs root only for the cert
# chown to uid 70 (postgres) on deploy/postgres/server.key.
```

The script:

1. Generates the Postgres TLS cert if not present (`deploy/postgres/server.{crt,key}`)
2. Creates `/opt/docker/shekel/{secrets,deploy/postgres}` directories
3. Writes each secret from the current `.env` to its file under `secrets/` when the file is missing
   or empty: `secret_key`, `totp_encryption_key`, `postgres_password`, `app_role_password` and
   `valkey_password` (from `SHEKEL_VALKEY_PASSWORD`). It refuses one whose `.env` value is absent,
   empty or the placeholder a previous run left, since it must be the value in use. It also makes
   `owner_role_password`, the database owner's: `.env`'s `OWNER_ROLE_PASSWORD` when that holds a
   real value, else a new URL-safe password, never printed. On every run, a file already there
   included, it sets each file's mode for the container user that reads it (the gotcha below)
4. Rewrites `/opt/docker/shekel/.env` with `replaced_by_docker_secret` placeholders for the five
   secrets, `OWNER_ROLE_PASSWORD` among them, and `SHEKEL_VALKEY_PASSWORD`, plus
   `SHEKEL_IMAGE_DIGEST` and `REGISTRATION_ENABLED=false`. Backs up the old `.env` to
   `.env.bak.<timestamp>`.
5. Creates the `shekel-frontend` bridge with the pinned subnet (172.32.0.0/24) if missing
6. Snapshots `/opt/docker/nginx/nginx.conf` and prints critical- directive presence (limit_req_zone,
   set_real_ip_from, timeouts) before overwriting from `deploy/nginx-shared/`. The pre-overwrite
   diff is saved to `/opt/docker/nginx/.reconcile-snapshot-<ts>/`.
7. Copies the repo's `docker-compose.yml` and `deploy/docker-compose.prod.yml` to
   `/opt/docker/shekel/`, `deploy/valkey/` to `/opt/docker/shekel/deploy/valkey/`, and the database
   container's provisioning script `deploy/postgres/provision_roles.sh` to
   `/opt/docker/shekel/deploy/postgres/` at 0755 (the postgres image sources a non-executable hook
   instead of running it). Snapshots the old files for rollback, of `deploy/postgres/` the script
   alone: the TLS key beside it is uid 70's and unreadable to the operating user.

The script STOPS short of recreating the stack so you can review the merged config before pulling
the trigger. Its trailing output prints the exact post-script commands: the database comes up first
and runs its provisioning script, which makes the owner's login on a database made before the script
existed and changes nothing on one it already ran on, and only then the rest of the stack, whose
migrate service logs in as the owner.

##### Gotchas that triggered hotfixes in this session

- **Docker secrets `uid`/`gid`/`mode` are ignored in non-Swarm Compose v2.** The default secret
  mount inherits the host file's ownership and mode. The db container reads three files as uid 70
  (postgres): `postgres_password`, and the two logins' passwords its provisioning script sets,
  `app_role_password` and `owner_role_password`. Since the host files are josh:josh, the script
  makes those three 0644, so the postgres user reads them through the "other" bits. Directory
  containment (`/opt/docker/shekel/secrets/` is mode 0700 josh-only) keeps the values protected from
  host-side enumeration. `secret_key` and `totp_encryption_key` are read by uid 1000 (shekel) inside
  the app container alone, which matches the host file owner, so they are 0600, and
  `valkey_password` is 0640 for the valkey container's gid 1000. The script sets each mode on every
  run, so a file left at another mode is put right: an `app_role_password` an earlier run wrote
  0600, when the app alone read it, becomes 0644.

- **Repo `deploy/nginx-shared/nginx.conf` drift.** The audit B7 hardening (`limit_req_zone`,
  `client_*_timeout`, `client_max_body_size`, narrowed `set_real_ip_from`) was applied to the
  on-host file but never back-ported. A blind repo->host sync clobbers them and crashes nginx with
  `zero size shared memory zone "public"`. The script's Step 6 snapshots the host file first and
  prints critical- directive presence before overwriting. After running the script the operator
  should diff the snapshot against the new file and confirm no audit-fix directive was lost.

- **Backend network subnet pin.** The repo file pins `backend` to 172.31.0.0/24. The on-host file
  under prior versions used the auto-assigned 172.25.0.0/16. `docker compose up -d` after the
  override copy tries to recreate the backend network and fails with `Resource still in use` because
  `shekel-postgres-exporter` in the monitoring stack is attached. Stop the exporter first:
  `cd /opt/docker/monitoring && docker compose stop shekel-postgres-exporter`, let the Shekel stack
  come up under the new subnet, then
  `docker compose up -d --force-recreate shekel-postgres-exporter` to rejoin.

- **Image entrypoint must support `_load_secret`.** The C-38 docker secrets pre-date the entrypoint
  code that reads them. Before flipping `.env` to placeholders, confirm the deployed image's
  entrypoint contains the `_load_secret` function:

  ```bash
  docker run --rm --entrypoint sh ghcr.io/saltyreformed-labs/shekel@sha256:<pinned> \
      -c "grep -cE '^_load_secret (SECRET_KEY|POSTGRES_PASSWORD|APP_ROLE_PASSWORD|TOTP_ENCRYPTION_KEY|VALKEY_PASSWORD) ' /home/shekel/app/entrypoint.sh"
  # Output must be 5: one call for each secret the script turns into a placeholder.
  ```

  An image without `_load_secret` reads the placeholders as the literal SECRET_KEY value and fails
  the 32-char minimum check at every boot. Rebuild via CI (push to `main`) and update
  `SHEKEL_IMAGE_DIGEST` in `/opt/docker/shekel/.env` before the flip.

##### Post-script verification

```bash
# Re-read the merged config (the script also writes it to
# /opt/docker/shekel/.reconcile-snapshot-<ts>/merged-config.yml).
cd /opt/docker/shekel
docker compose config | less

# When ready to recreate:
cd /opt/docker && docker compose up -d nginx cloudflared  # attach to shekel-frontend
docker exec nginx nginx -t && docker exec nginx nginx -s reload
cd /opt/docker/shekel && docker compose down && docker compose up -d --wait db
docker exec shekel-prod-db /docker-entrypoint-initdb.d/10-provision-roles.sh
docker compose up -d

# Health checks:
docker compose ps                                            # all four healthy
docker inspect shekel-prod-app --format '{{.HostConfig.ReadonlyRootfs}}'    # → true
docker logs shekel-prod-app 2>&1 | grep '^Loaded '            # → 5 lines, one per secret
curl -sI https://shekel.saltyreformed.com/login | head -5     # → 200 + sec headers
docker exec jellyfin bash -c 'getent hosts shekel-prod-app'   # → fails (isolation confirmed)
```

### 2.6 Host Steps a Release Carries

`shekel-deploy` changes one thing in production, the image pin, and its automatic rollback re-pins
the previous image onto whatever host files are in place. It copies none of 2.5's host copies and
writes nothing under `/opt/docker/shekel/secrets/`. A card whose change needs the host changed too
writes the steps here, one subsection naming the card, each with its rollback and what was measured;
`/release` runs them at its step 11 for each card the release carries, after the developer's "Logged
out, deploy now", from `~/projects/Shekel` at `main`. A step marked as coming before the merge into
`main` runs at `/release` step 5 instead. Once production runs a card, its subsection is history,
and the next change to this section deletes it.

#### The update App's key, before the merge into `main` (plan#1183)

**These steps are the developer's, in GitHub's settings, and they come before the merge into
`main`**, which brings `updates.yml` there (ruling plan#2472, built by plan#2728). `updates.yml`
signs in as the update App with a private key only a run on `main` may reach: the secret of an
environment named `update`, whose deployment branches are `main` alone.

1. Create the environment `update`, in
   [Shekel's environments](https://github.com/saltyreformed-labs/Shekel/settings/environments).
2. Set its deployment branches to `main` alone: **Selected branches and tags**, with one rule, a
   branch rule and not a tag rule, named `main`.
3. Add the `UPDATE_APP_KEY` secret to it. This needs the App's private key file, or a new key from
   the App's page, as GitHub never shows a stored secret again.
4. Delete the repository secret `UPDATE_APP_KEY`, in
   [Shekel's Actions secrets](https://github.com/saltyreformed-labs/Shekel/settings/secrets/actions).

`UPDATE_APP_CLIENT_ID` stays a repository variable, and `PLAN_READER_KEY` a repository secret: the
landing job runs on every pull request and queue group, which a `main`-only environment would
refuse.

**The order is the point, and nothing but the read-back checks it.** Were the release to land first,
the first keyed run would have GitHub create `update` with no rules and no secrets,
`secrets.UPDATE_APP_KEY` would resolve to the repository secret, the mint would succeed, and every
branch could still name the key, with no red run and no alert. Likewise with every step done but the
last. (plan#2728's review, read in GitHub's documentation; not measured.)

**The read-back**, which reads only, before the approval:

```bash
REPO=repos/saltyreformed-labs/Shekel
gh api "$REPO/environments/update" --jq .deployment_branch_policy; echo "exit=$?"
gh api "$REPO/environments/update/deployment-branch-policies" --jq '[.branch_policies[] | {name, type}]'; echo "exit=$?"
gh api "$REPO/environments/update/secrets" --jq '[.secrets[].name]'; echo "exit=$?"
gh api "$REPO/actions/secrets" --jq '[.secrets[].name]'; echo "exit=$?"
```

Each must exit 0. The first must say `custom_branch_policies` is true, as a rule listed but not
enforced restricts nothing; the second must list exactly one policy, `name` `main` and `type`
`branch`; the third must list `UPDATE_APP_KEY`; the fourth must not. Anything else is a STOP:
nothing merges.

**Rollback:** none. Until the merge, no workflow on `main` names the key, so the steps can stay
whether or not the release goes on, and `shekel-deploy`'s rollback leaves them as they are.

**Measured** 2026-11-05, before the steps: the first three reads answered 404 and exit 1, and the
fourth printed `["PLAN_READER_KEY","UPDATE_APP_KEY"]`, so the read-back refuses until they are done.
No environment exists to read yet, so what it reads of an environment and a policy comes from
GitHub's REST reference, not a run.

#### The Valkey switch (plan#1183)

**This release is cut at or after plan#2718's merge**, which landed after plan#1183's and deleted
the source-build deploy, as that could not cross this switch. The switch has no rehearsal of its own
(ruling plan#2473): CI's real-store test, the rehearsal measured below and `verify_prod_deploy.sh`'s
`GET /login` answer for it.

**Until question plan#2944 is ruled, it stops at `/release` step 3.** `dev` also holds plan#1192,
whose pull request #655 landed just after the last release reached `main`. It moves money and has no
rehearsal kit, so `release plan` refuses the cut (measured 2026-11-05 at `84bae6be8`:
`newest earlier cut that passes: none`). A kit cannot ride with this release: its bench refuses a
side with no `uv.lock`, which `main` is until this release, so any other carried card that needs a
kit is the same STOP.

The release carrying plan#1183 moves the rate limiter's store from Redis to Valkey (R-BAL298). The
image and the host files must move together, and only the steps below move them back:

- The new image names its store in its own code and needs the store's password in `VALKEY_PASSWORD`,
  which the host's old files never hand the app, so on them it refuses to boot
  (`ValueError: VALKEY_PASSWORD is not set`), and `shekel-deploy`'s rollback restores the old image,
  healthy.
- The old image takes its store from `RATELIMIT_STORAGE_URI`, which the new files no longer set, so
  on them it falls back to its own default, `redis://redis:6379/0`, with no password: it boots and
  passes `/health`, and every rate-limited route answers 500, as the orphan `shekel-prod-redis`
  refuses a client with no password (`redis.exceptions.AuthenticationError`). After step 1, then,
  `shekel-deploy`'s own rollback cannot recover. When the new image fails its health wait, the
  rollback prints `rollback healthy` on an app that cannot sign anyone in. When compose cannot start
  the `valkey` service at all, it ends `revert compose up also failed`, its alert still saying it
  reverted, and leaves the app container created but not running: production is down. Either way,
  step 3 restores files and image together.
- `/health` is exempt from the limiter, so a store fault only limited routes meet (a missing grant,
  a password the store does not hold) passes `shekel-deploy`'s health wait:
  `verify_prod_deploy.sh`'s `GET /login` is this release's gate.

**1. Host steps**, before `shekel-deploy`, in one call. The rename comes first: everything after it
reads `SHEKEL_VALKEY_PASSWORD`.

```bash
P=/opt/docker/shekel; Q=~/projects/Shekel
S="$P/.valkey-cutover-$(date +%Y%m%d-%H%M%S)"
[ "$(grep -c '^SHEKEL_REDIS_PASSWORD=' "$P/.env")" = 1 ] && [ ! -e "$P/secrets/valkey_password" ] \
    && install -d -m 0700 "$S" \
    && cp -p "$P/.env" "$P/docker-compose.yml" "$P/docker-compose.override.yml" "$P/secrets/redis_users.acl" "$S/" \
    && echo "snapshot=$S" \
    && sed -i 's/^SHEKEL_REDIS_PASSWORD=/SHEKEL_VALKEY_PASSWORD=/' "$P/.env" \
    && (umask 027 && sed -n 's/^SHEKEL_VALKEY_PASSWORD=//p' "$P/.env" | tr -d '\n' > "$P/secrets/valkey_password") \
    && [ -s "$P/secrets/valkey_password" ] \
    && sed -i 's/^SHEKEL_VALKEY_PASSWORD=.*/SHEKEL_VALKEY_PASSWORD=replaced_by_docker_secret/' "$P/.env" \
    && install -d -m 0755 "$P/deploy/valkey" \
    && install -m 0644 "$Q/deploy/valkey/users.acl" "$Q/deploy/valkey/start.sh" "$P/deploy/valkey/" \
    && install -m 0644 "$Q/docker-compose.yml" "$P/docker-compose.yml" \
    && install -m 0644 "$Q/deploy/docker-compose.prod.yml" "$P/docker-compose.override.yml" \
    && (cd "$P" && docker compose config -q); echo "exit=$?"
```

It writes nothing unless `.env` names `SHEKEL_REDIS_PASSWORD` once and no password file exists yet.
It snapshots what it replaces into `$S` (0700) and prints `snapshot=$S`: `.env`, holding the old
pin, both compose files, and the Redis ACL file step 4 deletes. Then it renames the variable, writes
`secrets/valkey_password` from the bytes `.env` holds (0640: `deploy/docker-compose.prod.yml` says
why), leaves `.env` the placeholder `replaced_by_docker_secret` in its place, since the valkey
service and the app both read the file (ruling plan#2633), copies `deploy/valkey/` and both compose
files in from `main`, and has compose parse the result. No container changes until compose recreates
one. Exit 0 goes on to step 2. Any other exit is a STOP: with no `snapshot=` line nothing was
written; with one, step 3 puts the files back first.

**2. Deploy and verify**, as every release does (`/release` step 11). `shekel-deploy` creates the
`valkey` service as the app's dependency and recreates the app; `shekel-prod-redis` stays up, unused
(compose warns `Found orphan containers`), for step 3. Exit 0 and `VERIFY OK` go on to step 4.
Anything else goes to step 3 only where step 3 applies, and is otherwise a STOP for the developer.

**3. Rollback**, only while the old image can read the database's schema: `shekel-deploy` failed
without printing `rollback REFUSED`, or it printed `deploy OK` for a release whose dry run read
`the target adds no migrations`. After `deploy OK` on a release that adds migrations, entrypoint
step 3 has committed them, and the old image would crash-loop on `Can't locate revision`; that, and
`rollback REFUSED`, are a STOP for the developer, with two ways out: forward, by correcting what
`verify_prod_deploy.sh` found, or back, by 2.3's restore through its step 4 and then this step in
place of 2.3's step 5.

Forward, a grant missing from the host's ACL copy is corrected in that file,
`/opt/docker/shekel/deploy/valkey/users.acl`, and Valkey, which reads its rules when it starts, is
recreated; the app keeps running and reconnects:

```bash
cd /opt/docker/shekel && docker compose up -d --wait --force-recreate valkey; echo "exit=$?"
```

Then read each Traceback or ERROR line the deploy's check counted: each must be the ACL failure.
Only then does `verify_prod_deploy.sh` run again, with SINCE the new Valkey's start
(`docker inspect shekel-prod-valkey --format '{{.State.StartedAt}}'`), since a window opening
earlier counts those lines again. The copy came from `main`, so `main` holds the same fault, and the
host copy now differs from the repository, which 2.5 calls a deployment-integrity bug: a hotfix card
corrects `main`.

Back, the snapshot serves until the next deploy rewrites the pin. With step 1's snapshot path:

```bash
P=/opt/docker/shekel; S=<step 1's snapshot path>
[ -s "$S/.env" ] \
    && cp -p "$S/.env" "$S/docker-compose.yml" "$S/docker-compose.override.yml" "$P/" \
    && { [ -e "$P/secrets/redis_users.acl" ] || cp -p "$S/redis_users.acl" "$P/secrets/"; } \
    && (cd "$P" && docker compose config -q && docker compose up -d --wait --remove-orphans); echo "exit=$?"
```

It puts back the old pin, the old variable and both compose files, recreates the app on the old
image against Redis (recreating Redis from the snapshot's ACL file when step 4 already removed it),
and removes the Valkey container; the database container keeps running. Then `verify_prod_deploy.sh`
must print `VERIFY OK`, and what the switch added goes: the files step 1 wrote, and the socket
volume compose made for the `valkey` service, which the old files do not name (a later step 2 would
reuse it unasked). Each goes only if it is there, as a step 1 or 2 that stopped early made fewer:

```bash
P=/opt/docker/shekel
rm -rf "${P:?}/deploy/valkey" "${P:?}/secrets/valkey_password" \
    && { ! docker volume inspect shekel-prod_valkey-socket >/dev/null 2>&1 || docker volume rm shekel-prod_valkey-socket; }; echo "exit=$?"
```

Exit 1 with `volume is in use` means a container still mounts the volume: the Valkey container step
3 should have removed. Production is then behind `main`, as after any rolled-back release.

**4. After `VERIFY OK`**, remove Redis and its ACL file:

```bash
P=/opt/docker/shekel
(cd "$P" && docker compose up -d --wait --remove-orphans) && rm "$P/secrets/redis_users.acl"; echo "exit=$?"
```

Compose recreates nothing here, as every service already matches the files; it removes
`shekel-prod-redis`. Step 3 still works after this step. The snapshot holds a copy of `.env`, the
store's password with it, so the release's last act removes it (`rm -r "$S"`); an undo after that is
a revert of plan#1183, released with host steps of its own.

**5. The dev stack.** `~/projects/Shekel` holds the switch once `/release` step 8 brings it to
`main`, and there the dev compose file names `valkey` while `shekel-dev-redis` still runs. This step
starts Valkey and removes Redis, and nothing else:

```bash
cd ~/projects/Shekel && docker compose -f docker-compose.dev.yml up -d --wait --remove-orphans valkey; echo "exit=$?"
```

It leaves a running dev app as it was, neither recreated nor restarted, on the environment and
mounts it was created with: `FLASK_ENV=development`, which the new code reads as host-run flask's
configuration, the limiter in memory, and no socket. The app is not recreated here because its
entrypoint migrates the dev database on every start, to the head of whichever checkout it runs from,
and that is the developer's call, made apart from a release. Its next start must build, as its image
holds `redis` but not the `valkey` client the new store needs:
`docker compose -f docker-compose.dev.yml up -d --build --remove-orphans`.

Then delete the `RATELIMIT_STORAGE_URI` line from `~/projects/Shekel/.flaskenv`: it names
`redis://...@localhost:6379`, and nothing reads the variable once the release is in `main` (ruling
plan#2633). Host-run `flask run` runs the limiter in memory, always, since dev's Valkey listens only
on a socket the dev app shares (ruling plan#2619); the containerized dev app runs its own
configuration, `development-container`, on that socket (ruling plan#2651).

**The main checkout's venv, at `/release` step 8.** This release is the first whose `main` holds a
lock to move that venv to, and this block runs in place of that step's plain sync, because a lane
still before the lock links the venv and needs `redis`, which the lock does not name:

```bash
. ~/projects/shekel-handoffs/releases/current.sh && cd "${W:?}" || exit 1
uv sync --locked --inexact --directory ~/projects/Shekel; echo "exit=$?"
```

The next release's plain sync removes `redis`, a week on, and by then a lane has merged `dev`.
Production reads nothing in this venv, so step 3 has nothing to put back here.

Measured 2026-11-04 on a scratch copy of it, with `dev` at `7dc2ece23` as the tree before the lock.
A plain `uv sync --locked` added `valkey`, moved 15 versions (pillow 12.3.0 to 13.0.0 the one major,
jactionlint 2.4.0 back to 2.3.0) and removed `redis`, and it kept pip. That tree's suite
(`not docker`) then gave 1 failed, 17,883 passed:
`test_a_missing_ref_row_is_seeded_back_and_committed` stopped at
`'redis' prerequisite not available`. With `--inexact` that test passed, `tools/quill`, `tools/ci`,
`tools/release` and `tools/split` passed, and `pylint app/` held 10.00 under the lock's pylint
4.1.3. `./scripts/test.sh` run from `~/projects/Shekel` syncs without `--inexact` and drops `redis`;
a lane before the lock then meets that one failure, which merging `dev` ends.

Between plan#1183's landing in `dev` and its release, the dev stack runs `main`'s Redis, and a dev
app started from a `dev` worktree names a Valkey socket that nothing serves: its rate-limited routes
fail until the release.

In the same window each lane moves onto a venv of its own as it merges `dev` (ruling plan#2828):
`./scripts/test.sh` replaces its `.venv` link once its tree holds `uv.lock`, and its `.venv/bin`
commands run the link until then. `tools.quill.lane` run from `~/projects/Shekel` is `main`'s, which
still links, so a lane started then is replaced the same way. The edit hooks stay on the main
checkout's venv until `/release` step 8, its versions behind the lock's (pylint 4.1.2 against 4.1.3
on 2026-11-04).

**Measured 2026-11-02**, of the blocks as they stand, on a throwaway stack on the rootless daemon:
`main`'s two compose files (`fc9dcfba7`, the files production holds) with production's image
`506313c73408` and its Redis build `8da19c7399dc`, then this card's files and an image built from it
at `a4e95b28e`, both app images by digest from a local registry; this card's `shekel-deploy.sh`,
unchanged, aimed at the stack by its own overrides; blocks 1 to 4 verbatim but for `P` and `Q`, run
in zsh as `/release` runs them; and a third compose file pointing the secrets and the app's registry
at the rehearsal.

- The new image on the old files died at entrypoint step 3
  (`ValueError: VALKEY_PASSWORD is not set`), and `shekel-deploy` printed `rollback healthy`.
- Step 1 exited 0 each of three times. Refused twice, once by an `.env` already renamed and once by
  a password file already there, it exited 1 each time with no `snapshot=` line and wrote nothing.
- With the password file unreadable to the app alone, step 2's new image died at its secret, and
  `shekel-deploy` re-pinned the old image on the new files: `rollback healthy`, then
  `verify_prod_deploy.sh` `FAIL /dashboard -> 500` and `FAIL /login -> 500`
  (`redis.exceptions.AuthenticationError: Authentication required.`). Step 3 then gave `VERIFY OK`
  on the old image with Redis.
- With it unreadable to both containers, Valkey could not start, compose refused
  (`dependency failed to start`), and `shekel-deploy` ended `revert compose up also failed`, the app
  container `Created` and not running. Step 3 then gave `VERIFY OK`.
- Those two rollbacks ran step 3's cleanup as it stood then, which removed no volume, and a later
  step 2 reused the `shekel-prod_valkey-socket` volume they left without a warning.
- Step 2 then gave `deploy OK` and `VERIFY OK`, and `GET /login` counted down through the app
  container (uid 1000, read-only root, `/run/valkey` mounted read-only, which it could neither list
  nor write; its stored `VALKEY_PASSWORD` blank), the count starting over whenever Valkey was
  recreated. 7.1's socket check printed `2710 999:1000` and `660 999:1000`.
- With `+lpush` taken out of the host's ACL copy and Valkey recreated
  (`up -d --wait --force-recreate valkey`, which returned once Valkey was healthy), `/dashboard` and
  `/login` answered 500
  (`ACL failure in script: User shekel has no permissions to run the 'lpush' command`). The forward
  fix's recreate, run with the old Valkey up, kept `2710 999:1000` and `660 999:1000` and left the
  app neither recreated nor restarted. Run again after a clean `docker compose stop valkey`, it kept
  both, and `verify_prod_deploy.sh` printed `VERIFY OK` with SINCE the new Valkey's start and
  `VERIFY FAILED` with a `1m` window, which reached the fault's five tracebacks. A one-off Valkey
  refusing `evalsha` itself answered
  `NOPERM User shekel has no permissions to run the 'evalsha' command`, which the client reports as
  `NoPermissionError` (7.1).
- Step 4 recreated none of the three containers it kept and removed Redis. Step 3 after it gave
  `VERIFY OK` on the old image with Redis recreated, and the cleanup line above, run then, removed
  the volume. Run on a scratch directory, the line exited 0 with everything there and with nothing
  there, and 1 (`volume is in use`) while a container mounted the volume.
- Step 5 was not run on a dev stack. In a two-service project, `up -d --remove-orphans` naming the
  new service removed the orphan and left the running dependent neither recreated nor restarted,
  with its old environment and no mount; a whole `up` recreated it with both. `main`'s image holds
  the `redis` client and not `valkey`; this card's holds `valkey`.

Where the rehearsal differs: its secrets were 0644, because the rootless daemon maps uids (host uid
1000 is the containers' root there), but for the Valkey password file, which kept step 1's 0640 and
was given the containers' uid and gid 1000 there, so the app read it as owner and Valkey (uid 999
gid 1000) as group, as both do on production. `shekel-deploy` ran with `--no-verify`, as no local
image carries a signature, a 150 s health wait where production's is 240 s, and its alerts off, so
the compose-failure alert's words above are read from `deploy/shekel-deploy.sh`, not seen; its
compose files were named by `COMPOSE_FILE`; the image it built adds no migration over `main`
(`shekel-deploy`'s pre-flight: `the target adds no migrations`), so step 3's migration case was not
run. That `/health` passes the health wait while a grant is missing was measured on 2026-11-01 on
the files before the socket (`deploy OK`, then `FAIL /login -> 500`), not again here.
