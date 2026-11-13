# Secret Management -- Operations Runbook

## Secret Inventory

The Shekel application requires five secrets for production operation, plus one optional secret used
only during a key rotation. All other environment variables have safe defaults or are non-sensitive.
Each service is given only the secrets it uses (rulings plan#3412, plan#3413): the web server, the
app service, holds the session key, the TOTP keys and the web login's password; the deploy, the
migrate service, holds the owner's password alone; the database container holds the three database
passwords, the superuser's among them, which no other service is given.

| Secret | Purpose | Generation Command | Rotation Impact |
|--------|---------|-------------------|-----------------|
| `SECRET_KEY` | Flask session cookie encryption. The app's | `python -c "import secrets; print(secrets.token_hex(32))"` | All active sessions are invalidated; users must log in again |
| `TOTP_ENCRYPTION_KEY` | Fernet encryption of TOTP secrets stored in database. The app's | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | Non-destructive when rotated via the documented procedure: place the previous value in `TOTP_ENCRYPTION_KEY_OLD`, then run `scripts/rotate_totp_key.py --confirm` to re-wrap every ciphertext under the new primary |
| `TOTP_ENCRYPTION_KEY_OLD` | Optional comma-separated list of retired Fernet keys used during rotation | -- (existing primary value, moved here at rotation time) | Empty in steady state.  Populated transiently during a key rotation; pruned again after `scripts/rotate_totp_key.py` completes |
| `POSTGRES_PASSWORD` | The database superuser's, `shekel_user` (the image's `POSTGRES_USER`). The database container's alone: no other service is given it, and nothing logs in with it but an operator's break-glass session. The deploy and the web server never use it | `python -c "import secrets; print(secrets.token_urlsafe(32))"` | None on the app or the deploy. Set in the database container by hand ("Rotating POSTGRES_PASSWORD") |
| `OWNER_ROLE_PASSWORD` | The database owner's, `shekel_owner`: it owns every object and runs the deploy and the owner's maintenance scripts, all in the migrate service, and is not a superuser. The database container's provisioning script, `deploy/postgres/provision_roles.sh`, sets it | `python -c "import secrets; print(secrets.token_urlsafe(32))"` | None while nothing deploys: the next deploy, or the next maintenance script in the migrate service, logs in with the new one |
| `APP_ROLE_PASSWORD` | The web login's, `shekel_app`: reads and writes data only, granted by the deploy, so an in-process RCE cannot drop tables or audit triggers (audit finding F-081, Commit C-13). The provisioning script sets it, and the app's entrypoint builds `DATABASE_URL_APP` from it | `python -c "import secrets; print(secrets.token_urlsafe(32))"` | From the moment the provisioning script sets it until the app restarts, the running app's new connections are refused |

The provisioning script refuses a login's password shorter than 16 characters, and the app's
entrypoint the web login's.

## Where Secrets Are Stored

The Shekel deployment has two postures for secret storage, one per deployment mode: bundled mode,
the README Quick Start, keeps its secrets in `.env`; shared-mode production
(`deploy/docker-compose.prod.yml`) keeps them in files.

### Posture 1 -- Env-backed (`.env` file)

Bundled mode's. Secrets live in the `.env` file on the host, in the same directory as the
`docker-compose.yml` file (`/opt/shekel/.env`).

- The `.env` file is excluded from version control (`.gitignore`).
- The `.env` file should be readable only by root: `chmod 600 .env`.
- Docker Compose reads `.env` and gives each service the variables `docker-compose.yml` names for it
  in its stored environment (`Container.Config.Env`), fixed when the container is made. A changed
  value reaches a container only when compose makes it again (`docker compose up -d <service>`);
  `docker compose restart` keeps the old one (measured 2026-11-13).
- Trade-off: anyone with `docker inspect` access on the host sees the real values; the file at rest
  carries the real values too.

### Posture 2 -- File-backed (Docker secrets, audit Commit C-38)

Shared-mode production's. Secrets live in individual files under `/opt/docker/shekel/secrets/`, each
mounted at `/run/secrets/<name>` into the services that use it and no other. The `.env` file holds
non-sensitive variables and the placeholder `replaced_by_docker_secret` for each file-backed value,
which the base file's `${VAR:?...}` interpolation needs. No container uses a placeholder: the app's
and the migrate service's entrypoints read each file with `_load_secret`
(`scripts/_entrypoint_lib.sh`) and export its value over the variable, the database image and its
provisioning script and Valkey's `start.sh` read their files themselves, and the override blanks the
variables it can (all three passwords on the database, `VALKEY_PASSWORD` on Valkey and the app,
`OWNER_ROLE_PASSWORD` on the migrate service), so a missing file is refused as unset rather than
tried as a value.

The directory is `0700`, owned by the operating user (the uid that runs `docker compose`). Compose
outside Swarm ignores a secret's `uid`, `gid` and `mode`, so each container reads the host file's
own owner and mode, and each file's mode follows the users that read it:

| File | Read by | Mode |
|------|---------|------|
| `secret_key` | app | `0600` |
| `totp_encryption_key` | app | `0600` |
| `app_role_password` | app (logs in with it), db (sets it) | `0644` |
| `owner_role_password` | migrate (logs in with it), db (sets it) | `0644` |
| `postgres_password` | db alone | `0644` |
| `valkey_password` | app, valkey | `0640` |

The app and the migrate service run as uid 1000, the files' owner; the database container runs as
uid 70 (postgres), which reads through the "other" bits, and the 0700 directory contains them;
Valkey runs as uid 999 gid 1000, the operating user's group. `totp_encryption_key_old` exists only
during a TOTP key rotation ("Rotating TOTP_ENCRYPTION_KEY" below).

Trade-off: `docker inspect` shows only the placeholder values; the real values live only in the
operator-controlled secrets directory.

### Migrating to Docker secrets

`scripts/reconcile_prod_to_canonical.sh` (`docs/runbook/2_deployment.md` 2.5) moves a shared-mode
host to Posture 2 and keeps it there. It writes each file from `.env`'s value when the file is
missing or empty, refusing a value that is absent or a placeholder, since it must be the one in use;
makes `owner_role_password`, a new one when `.env` holds no real value; sets every file's mode from
the table above on every run, a file already there included; and rewrites `.env` with the
placeholders, keeping the old one as `.env.bak.<timestamp>`. Its closing text prints the recreate:
the database first, its provisioning script, then the rest of the stack.

Then confirm the files are what each container reads:

```bash
cd /opt/docker/shekel

# The stored environment holds the placeholder, not the value.
docker inspect shekel-prod-app --format '{{ range .Config.Env }}{{ println . }}{{ end }}' | grep -E '^SECRET_KEY='
# Expect: SECRET_KEY=replaced_by_docker_secret

# Each entrypoint logs each file it loaded, at every start.
docker logs shekel-prod-app 2>&1 | grep '^Loaded '
# Expect, per start:
#   Loaded SECRET_KEY from /run/secrets/secret_key.
#   Loaded APP_ROLE_PASSWORD from /run/secrets/app_role_password.
#   Loaded TOTP_ENCRYPTION_KEY from /run/secrets/totp_encryption_key.
#   Loaded VALKEY_PASSWORD from /run/secrets/valkey_password.
docker logs shekel-prod-migrate 2>&1 | grep '^Loaded '
# Expect, per deploy since compose made the container:
#   Loaded OWNER_ROLE_PASSWORD from /run/secrets/owner_role_password.
```

`docker exec shekel-prod-app` cannot show a loaded value: an exec'd process gets the stored
environment, the placeholders. Back up the secrets directory once it holds the files (Recommendation
below): it is now the only copy of each value.

### Rolling back to Posture 1

Shared mode has no Posture 1 to roll back to. Its override blanks the database's three password
variables, Valkey's and the migrate service's, and compose refuses to parse the files while a
secret's file is missing, so the files are shared mode's one source. A damaged or lost file is
restored from the secrets backup (Disaster Recovery, below); a value lost with it is rotated (Secret
Rotation Procedures).

## Secret Rotation Procedures

### Rotating SECRET_KEY

1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"` The output is a
   64-character hex string (256 bits of entropy). The production config rejects any value shorter
   than 32 characters or matching a known placeholder.
2. Install the new value at the source of truth for your posture:

   - **Posture 1 (env-backed):** update `SECRET_KEY` in `.env`.
   - **Posture 2 (file-backed):** overwrite the secret file with `printf` (no trailing newline) so
     the file content is exactly the secret bytes:

     ```bash
     sudo printf '%s' '<new-secret-key>' | \
         sudo tee /opt/docker/shekel/secrets/secret_key >/dev/null
     sudo chmod 0600 /opt/docker/shekel/secrets/secret_key
     ```

     Leave the placeholder in `.env` unchanged.

3. Bring the app up on the new key:

   - **Posture 1:** `docker compose up -d app`, which makes the app container again with the new
     environment, after the deploy runs in the migrate service. A `restart` would keep the old key.
   - **Posture 2:** `docker compose restart app`. The entrypoint reads the file at every start.

4. Run the global session-invalidation script (see next section) so that any cookies signed under
   the old key are rejected even if an attacker captured them before the key was rotated.
5. Impact: all active sessions are invalidated; users must re-authenticate.

### Post-rotation session invalidation

Rotating `SECRET_KEY` makes every previously-issued session cookie unverifiable on the new key.
However, any cookie an attacker captured before the rotation can still be reused by anyone who
learns the *old* key (for example, anyone with read access to git history that contains a
previously-leaked key). The defense-in-depth control is to bump `users.session_invalidated_at` for
every row, which causes the `load_user` callback in `app/__init__.py` to reject any session older
than the bump time -- regardless of which key it was signed with.

Run after every `SECRET_KEY` rotation, and after any git history rewrite that excises a
historically-leaked key:

```bash
cd /opt/docker/shekel
docker compose run --rm --no-deps --pull never app python scripts/rotate_sessions.py --confirm
```

It runs in a one-shot container of the app service, as the web login (`docs/runbook.md` 1, Script
Inventory, says why `docker exec` reaches no login).

The script bumps `session_invalidated_at` to `now()` for every row in `auth.users`. It is idempotent
(repeated runs simply move the timestamp forward) and emits a structured log event
`sessions_invalidated_global` at WARNING level so the audit log captures the operation.

The `--confirm` flag is mandatory; running without it prints a usage hint and exits with code 1.

#### Rewriting git history to remove a leaked SECRET_KEY

If a `SECRET_KEY` value was committed to git history (for example, audit finding F-001), rotating
the live key is necessary but not sufficient: anyone with access to the repository's object store
can still extract the historical key from the dangling blob. The full remediation is:

1. **Inventory clones.** Track every clone of the repository (developer machines, CI caches,
   audit-branch snapshots, NAS backups). Each will need to be re-cloned after the rewrite.

2. **Rotate the live key first.** Follow the `Rotating SECRET_KEY` procedure above. This minimises
   the window during which an attacker can use a historical key against current sessions.

3. **Rewrite history.** Use either tool below. Both must run against a fresh, clean local clone --
   not your working copy:

   - `git filter-repo`:

     ```bash
     git clone --mirror <remote-url> shekel.git
     cd shekel.git
     # Replace the literal leaked value with a placeholder marker:
     printf 'OLD_LEAKED_KEY_HERE==>REDACTED\n' > replacements.txt
     git filter-repo --replace-text replacements.txt
     ```

   - BFG Repo-Cleaner:

     ```bash
     git clone --mirror <remote-url> shekel.git
     cd shekel.git
     bfg --replace-text replacements.txt
     git reflog expire --expire=now --all
     git gc --prune=now --aggressive
     ```

4. **Force-push the rewritten history** to the affected branches. Coordinate with any other
   contributors first. The Shekel repository is single-owner, so coordination is trivial.

5. **Run the session-invalidation script** described above so that any cookies signed under the
   historical key are invalidated even if an attacker preserved them.

6. **Re-clone everywhere.** Every existing clone (developer machine, CI runner cache, audit-branch
   snapshot) still has the leaked key in its object store. Delete each clone and re-clone from the
   rewritten remote. Document this in the audit trail.

7. **Install a pre-commit hook** (gitleaks or detect-secrets) so the pattern cannot recur. This is
   tracked separately in the audit remediation plan.

### Rotating TOTP_ENCRYPTION_KEY

This procedure is **non-destructive**: existing MFA enrollments remain valid throughout the
rotation, and users do not need to re-enroll. It relies on the application's `MultiFernet`
configuration, which accepts the new primary key for encryption AND decrypts ciphertexts written
under any retired key listed in `TOTP_ENCRYPTION_KEY_OLD`.

The full rotation has four steps and one optional cleanup deploy:

1. **Generate the new primary key.**

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Save the output -- it is the only chance to capture it.

2. **Promote the existing primary to retired, install the new primary.**

   - **Posture 1 (env-backed):** in `/opt/shekel/.env`:

     ```diff
     -TOTP_ENCRYPTION_KEY=<previous-primary-value>
     +TOTP_ENCRYPTION_KEY=<newly-generated-value>
     +TOTP_ENCRYPTION_KEY_OLD=<previous-primary-value>
     ```

   - **Posture 2 (file-backed):**

     ```bash
     # Move the current primary into the retired-key file.
     sudo mv /opt/docker/shekel/secrets/totp_encryption_key \
             /opt/docker/shekel/secrets/totp_encryption_key_old
     # Install the new primary.
     sudo printf '%s' '<newly-generated-value>' | \
         sudo tee /opt/docker/shekel/secrets/totp_encryption_key >/dev/null
     sudo chmod 0600 /opt/docker/shekel/secrets/totp_encryption_key \
                     /opt/docker/shekel/secrets/totp_encryption_key_old
     ```

     The `totp_encryption_key_old` file is NOT declared in the `secrets:` block of
     `deploy/docker-compose.prod.yml` (because compose requires every declared secret file to
     exist). Edit the host's copy of the override, `/opt/docker/shekel/docker-compose.override.yml`,
     to mount it into the app alone, before bringing the app back up:

     ```diff
        services:
          app:
            secrets:
              - source: secret_key
                target: secret_key
              - source: app_role_password
                target: app_role_password
              - source: totp_encryption_key
                target: totp_encryption_key
              - source: valkey_password
                target: valkey_password
     +        - source: totp_encryption_key_old
     +          target: totp_encryption_key_old
        ...
        secrets:
          ...
          totp_encryption_key:
            file: /opt/docker/shekel/secrets/totp_encryption_key
     +    totp_encryption_key_old:
     +      file: /opt/docker/shekel/secrets/totp_encryption_key_old
     ```

     Step 5 below removes the edit and the retired-key file after rotation. Until then the host copy
     differs from the repository's (the drift 2.5 of `docs/runbook/2_deployment.md` checks for), so
     a release waits for step 5.

   If `TOTP_ENCRYPTION_KEY_OLD` already has a value (e.g. from an earlier in-progress rotation),
   append the new retired value with a comma -- the application reads the value as a comma-separated
   list of Fernet keys, regardless of whether it came from an env var or a secret file. In the
   file-backed posture, write the comma-joined string into the file with `printf` (no trailing
   newline) so the file content is exactly the comma-separated list.

3. **Make the application container again so the new key list takes effect.**

   ```bash
   cd /opt/docker/shekel   # or /opt/shekel, Posture 1
   docker compose up -d app
   ```

   Compose makes the app container again, after the deploy runs in the migrate service: in Posture 1
   for its new environment, in Posture 2 for its new secret mount. A `restart` would keep the
   container as it was made, without either (measured 2026-11-13 for an environment change).

   At this point the application can:

     - decrypt every existing ciphertext (via the retired key listed in `TOTP_ENCRYPTION_KEY_OLD`),
       and
     - encrypt every new ciphertext under the new primary.

   Existing MFA users continue to log in successfully. This is the safe state to validate end-to-end
   before continuing.

4. **Re-wrap every existing ciphertext under the new primary.**

   ```bash
   docker compose run --rm --no-deps --pull never app python scripts/rotate_totp_key.py --confirm
   ```

   A one-shot container of the app service, which holds both keys (`docs/runbook.md` 1, Script
   Inventory); in the migrate service the script exits 1, naming the key that service lacks.

   The script prints a summary like

   ```text
   Rotated 3; already current 0; skipped 0.
   ```

   - `Rotated` -- rows successfully migrated to the new primary.
   - `already current` -- rows that were already under the new primary (idempotent re-runs are
     safe).
   - `skipped` -- rows that could not be decrypted under any configured key.
     **A non-zero `skipped` count means the script exits with code 2.** Do not proceed to step 5;
     instead inspect the application log for the row id(s) and reconcile manually (typically by
     resetting MFA for the affected user via `scripts/reset_mfa.py`).

5. **Prune `TOTP_ENCRYPTION_KEY_OLD` at the next deploy** (optional but recommended). Once
   `scripts/rotate_totp_key.py` reports zero skipped rows, the retired key is no longer needed.

   - **Posture 1 (env-backed):** clear the entry in `.env`:

     ```diff
     -TOTP_ENCRYPTION_KEY_OLD=<previous-primary-value>
     +TOTP_ENCRYPTION_KEY_OLD=
     ```

   - **Posture 2 (file-backed):** revert the inline edit to `deploy/docker-compose.prod.yml` from
     step 2 (remove the `totp_encryption_key_old` entries from the app's `secrets:` list and the
     top-level `secrets:` block) and delete the file:

     ```bash
     sudo rm /opt/docker/shekel/secrets/totp_encryption_key_old
     ```

   Run `docker compose up -d app`, which makes the app container again without the retired key,
   after a deploy. The retired key is now permanently retired -- if it was leaked, the leak no
   longer confers access to the MFA secrets.

   You may leave `TOTP_ENCRYPTION_KEY_OLD` populated longer than necessary if you want a rollback
   window; the only cost is that the retired key continues to be a valid decryption key during that
   window.

#### Rollback

If something goes wrong before step 4 completes:

- Restore the previous primary value to `TOTP_ENCRYPTION_KEY` (and clear `TOTP_ENCRYPTION_KEY_OLD`
  if you set it) and restart. No ciphertexts have been mutated yet, so the application returns to
  its previous state.

If something goes wrong DURING step 4 (e.g. the script crashes mid-run):

- The script commits once at the end, so a crash leaves the table in its previous state. Re-run the
  script after fixing the underlying issue. Any rows it had not yet processed are still encrypted
  under the retired key; the next run picks up where it left off, and rows it had already migrated
  are detected as `already current` and skipped.

If something goes wrong after step 4 completes:

- The retired key is still in `TOTP_ENCRYPTION_KEY_OLD`, so the application can still decrypt under
  either key. Decide whether to roll back to the previous primary (move the retired key back to
  `TOTP_ENCRYPTION_KEY` and re-run the script in reverse -- in this case, the previously-current
  rows will be detected as "needing rotation" and re-wrapped under the old key) or accept the new
  primary as the steady state.

### Rotating APP_ROLE_PASSWORD and OWNER_ROLE_PASSWORD

A login's password is set in one place, the database container's provisioning script,
`deploy/postgres/provision_roles.sh` (ruling plan#3413), which reads it where that container holds
it: the file in Posture 2, the container's environment in Posture 1. So a rotation writes the new
value there, runs the script, and then brings up the service that logs in with it. Run from the
directory that holds the compose files, `/opt/docker/shekel` (Posture 2) or `/opt/shekel` (Posture
1).

1. **Write the new value where the database container reads it.**

   - **Posture 2:** write the file in place, the value never printed:

     ```bash
     python3 -I -c 'import secrets; print(secrets.token_urlsafe(32), end="")' \
         > secrets/app_role_password        # or secrets/owner_role_password
     ```

     `>` rewrites the file that is there, so it keeps its mode (0644) and the database container,
     which mounted that file, reads the new value. A file replaced by a rename (`mv`, or an editor
     that saves that way) stays unseen there until the container restarts.

   - **Posture 1:** generate a value
     (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), put it in `.env`'s
     `APP_ROLE_PASSWORD` (or `OWNER_ROLE_PASSWORD`), and make the database container again so its
     environment holds it: `docker compose up -d --wait db`. That restarts the database; the app
     reconnects.

   The script refuses a password shorter than 16 characters; a URL-safe one is needed because each
   entrypoint puts it in a database URL unencoded.

2. **Set it in the database:**

   ```bash
   docker exec shekel-prod-db /docker-entrypoint-initdb.d/10-provision-roles.sh
   ```

   It sets both logins' passwords from what the container holds and, on a database it already ran
   on, changes nothing else: it ends
   `NOTICE: shekel_owner and shekel_app provisioned; 0 object(s) handed to shekel_owner.`

3. **Bring up the service that logs in with it.**

   - **The web login's:** in Posture 2, `docker compose restart app`, which runs no deploy; the
     entrypoint reads the file at every start. In Posture 1, `docker compose up -d`, which makes the
     migrate and app containers again with their new environment, the deploy first. From step 2
     until then the running app's new connections are refused: its `/health` answered 500 in that
     window (measured 2026-11-13, Posture 1).
   - **The owner's:** nothing holds it between deploys. In Posture 2 the next deploy, or the next
     maintenance script in the migrate service, reads the file. In Posture 1 a
     `docker compose run ... migrate` makes its container from `.env` as it is now, and the next
     `docker compose up -d` makes the deploy's again.

4. **Verify** each login, each in a one-shot container of its own service:

   ```bash
   docker compose run --rm --no-deps --pull never app sh -c \
       'psql "$DATABASE_URL_APP" -Atc "SELECT current_user"'     # → shekel_app
   docker compose run --rm --no-deps --pull never migrate sh -c \
       'psql "$DATABASE_URL" -Atc "SELECT current_user"'         # → shekel_owner
   ```

Measured 2026-11-13 in Posture 1 on a throwaway stack from card plan#2166's branch, both passwords
at once: step 1 made the database container again and left the app's, step 2 ended as above, the
app's `/health` answered 500 until step 3, and step 4 printed each login. Posture 2 was measured
2026-11-12, the file written in place and step 3's restart.

### Rotating POSTGRES_PASSWORD

Nothing logs in with the superuser's password. Every command that acts as the superuser (the
backups, the dump `shekel-deploy` takes, a restore, the provisioning script) runs in the database
container over its own socket, which asks no password, and no other service is given it (ruling
plan#3412). It matters only to a login over the network, an operator's break-glass session. So a
rotation changes it in the database and where it is kept, and restarts no service. The image reads
`POSTGRES_PASSWORD` only when it makes a cluster on an empty volume, so the kept value and the
database's can part unseen: change both together.

- **Posture 2:** write the file in place, then set the database from it:

  ```bash
  cd /opt/docker/shekel
  python3 -I -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > secrets/postgres_password
  docker exec -i shekel-prod-db psql -U shekel_user -d shekel -v ON_ERROR_STOP=1 --no-psqlrc <<'SQL'
  \set password `cat /run/secrets/postgres_password`
  ALTER ROLE shekel_user PASSWORD :'password';
  SQL
  ```

- **Posture 1:** generate a value, put it in `.env`'s `POSTGRES_PASSWORD`, make the database
  container again so its environment holds it (`docker compose up -d --wait db`, which restarts the
  database), then:

  ```bash
  docker exec -i shekel-prod-db psql -U shekel_user -d shekel -v ON_ERROR_STOP=1 --no-psqlrc <<'SQL'
  \getenv password POSTGRES_PASSWORD
  ALTER ROLE shekel_user PASSWORD :'password';
  SQL
  ```

Each prints `ALTER ROLE`. The value crosses into psql from the file or the container's environment,
never a command line.

Measured 2026-11-13 on the throwaway stack above: after the Posture 1 form, and after the Posture 2
form read from a file in the database container, a network login as `shekel_user` with the new
password connected, and one with another password was refused (`password authentication failed`).

## Disaster Recovery: Reconstructing Secrets

If the Proxmox host is lost and must be rebuilt from scratch (the whole sequence is
`docs/runbook/7_troubleshooting.md` 7.3, "Complete disaster recovery"):

1. **Reconstruct the secrets store** for your posture:

   - **Posture 1 (env-backed):** rebuild `.env` using `.env.example` as a template:
     - `SECRET_KEY`: generate a new one. Users will need to log in again.
     - `TOTP_ENCRYPTION_KEY`: if you have the original key backed up (see recommendation below), use
       it. If not, generate a new one and all users must re-enroll MFA.
     - `POSTGRES_PASSWORD`, `OWNER_ROLE_PASSWORD` and `APP_ROLE_PASSWORD`: generate new. A backup
       holds no login or password (it is taken `--no-owner --no-privileges`), and the new cluster's
       first boot makes the superuser from the first and both logins from the next two
       (`deploy/postgres/provision_roles.sh`).
     - All other variables have defaults or are non-sensitive.

   - **Posture 2 (file-backed):** restore `/opt/docker/shekel/secrets/` from your encrypted backup
     tarball. If the backup is lost too, put new values in `.env` as for Posture 1, and the
     reconcile script ("Migrating to Docker secrets") writes each to its file.

2. **Start the application** on its empty database volume, whose first boot makes the three logins:

   ```bash
   docker compose up -d
   ```

3. **Restore the database** from NAS backups using `scripts/restore.sh` (see backup/restore
   runbook), which hands the restored objects to the owner and runs the deploy before the app serves
   again.

### Recommendation: Back Up the secrets store

The shape of the backup depends on your posture:

```bash
# Posture 1 (env-backed) -- back up the .env file.  Add to the
# backup cron job (after the database backup):
cp /opt/docker/shekel/.env /mnt/nas/backups/shekel/env_backup

# Posture 2 (file-backed) -- back up the secrets directory.  Encrypt
# at rest because the tarball is the master credential bundle.
sudo tar -czf - -C /opt/docker/shekel secrets | \
    gpg --encrypt --recipient backup-key \
    > /mnt/nas/backups/shekel/secrets-$(date +%Y%m%d).tar.gz.gpg
```

Either backup serves as the recovery source for step 2 above.

### Recommendation: Document Secrets in a Password Manager

Create a secure note in your password manager with:

- `SECRET_KEY` value
- `TOTP_ENCRYPTION_KEY` value
- `POSTGRES_PASSWORD` value
- `OWNER_ROLE_PASSWORD` value
- `APP_ROLE_PASSWORD` value
- Date each secret was last rotated
- Posture in use (env-backed vs. file-backed)

This is the fastest disaster recovery path: copy values from the password manager into either a
fresh `.env` file or fresh secret files under `/opt/docker/shekel/secrets/`, depending on the
posture you are restoring to.
