# Secret Management -- Operations Runbook

## Secret Inventory

The Shekel application requires four required secrets for production operation, plus one optional
secret used only during a key rotation. All other environment variables have safe defaults or are
non-sensitive.

| Secret | Purpose | Generation Command | Rotation Impact |
|--------|---------|-------------------|-----------------|
| `SECRET_KEY` | Flask session cookie encryption | `python -c "import secrets; print(secrets.token_hex(32))"` | All active sessions are invalidated; users must log in again |
| `TOTP_ENCRYPTION_KEY` | Fernet encryption of TOTP secrets stored in database | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | Non-destructive when rotated via the documented procedure: place the previous value in `TOTP_ENCRYPTION_KEY_OLD`, then run `scripts/rotate_totp_key.py --confirm` to re-wrap every ciphertext under the new primary |
| `TOTP_ENCRYPTION_KEY_OLD` | Optional comma-separated list of retired Fernet keys used during rotation | -- (existing primary value, moved here at rotation time) | Empty in steady state.  Populated transiently during a key rotation; pruned again after `scripts/rotate_totp_key.py` completes |
| `POSTGRES_PASSWORD` | PostgreSQL superuser password (owner role `shekel_user`).  Used by `entrypoint.sh` for schema creation, migrations, seed scripts | `python -c "import secrets; print(secrets.token_urlsafe(32))"` | Requires updating both the db service and app service configs simultaneously; restart both containers |
| `APP_ROLE_PASSWORD` | Least-privilege PostgreSQL DML-only role password (`shekel_app`).  Constructed into `DATABASE_URL_APP` by `entrypoint.sh` and used by Gunicorn at runtime so an in-process RCE cannot drop tables or audit triggers (audit finding F-081, Commit C-13) | `python -c "import secrets; print(secrets.token_urlsafe(32))"` | `entrypoint.sh` reprovisions the `shekel_app` role with the new password on every restart; rotate by updating the source-of-truth secret and recreating the app container |

## Where Secrets Are Stored

The Shekel deployment supports two postures for secret storage. Both are operator-grade and either
is acceptable for production; the file-backed posture is recommended for the shared-mode
(`deploy/docker-compose.prod.yml`) deployment.

### Posture 1 -- Env-backed (`.env` file)

The default posture, used by the bundled-mode quickstart. Secrets live in the `.env` file on the
host, in the same directory as the `docker-compose.yml` file (`/opt/docker/shekel/.env` for shared
mode, `/opt/shekel/.env` for bundled mode).

- The `.env` file is excluded from version control (`.gitignore`).
- The `.env` file should be readable only by root: `chmod 600 .env`.
- Docker Compose reads `.env` automatically and injects values into container environment variables
  (`Container.Config.Env`).
- Trade-off: anyone with `docker inspect` access on the host sees the real values; the file at rest
  carries the real values too.

### Posture 2 -- File-backed (Docker secrets, audit Commit C-38)

Available in shared-mode production (`deploy/docker-compose.prod.yml`). Secrets live in individual
files under `/opt/docker/shekel/secrets/`, bind-mounted into the container as `/run/secrets/<name>`.
The `.env` file holds non-sensitive variables and a placeholder for each docker-secret-managed value
(any non-empty string -- the placeholder satisfies the base file's `${VAR:?...}` interpolation but
is overwritten at runtime by `entrypoint.sh::_load_secret`).

- Files are stored in `/opt/docker/shekel/secrets/`.
- Recommended permissions: directory `0700` root-owned, files `0600` root-owned.
- Per-file secrets: `secret_key`, `postgres_password`, `app_role_password`, `totp_encryption_key`
  (and `totp_encryption_key_old` only during a TOTP key rotation -- see the "Rotating
  TOTP_ENCRYPTION_KEY" section below).
- Trade-off: `docker inspect` shows only the placeholder values; the real values live only in the
  operator-controlled secrets directory.

### Migrating to Docker secrets

One-time procedure to move from Posture 1 to Posture 2 on an existing shared-mode deployment. Plan a
maintenance window: the final step recreates both the `app` and `db` containers.

1. Confirm you are running shared-mode production (the override `deploy/docker-compose.prod.yml` is
   active):

   ```bash
   docker compose ps
   # Expect: shekel-prod-app, shekel-prod-db, shekel-prod-valkey
   ```

   If the bundled `shekel-prod-nginx` is also present, you are in bundled mode -- docker secrets are
   not wired in for that path, continue with Posture 1.

2. Create the secrets directory with restrictive permissions. Run on the Proxmox host:

   ```bash
   sudo install -d -m 0700 -o root -g root /opt/docker/shekel/secrets
   ```

3. Write the four required secret values from `.env` into individual files. `printf '%s'` (no
   trailing newline) is preferred over `echo` so the file contents are exactly the secret bytes; the
   entrypoint loader strips a trailing newline if `echo` was used by mistake but `printf` is the
   documented form:

   ```bash
   # Read each value out of the existing .env into a host shell
   # variable, then write it through to the secrets directory.  The
   # awk extracts the value (everything after the first =) so embedded
   # = signs in the value (rare but legal in random hex/base64) are
   # preserved.
   for var in SECRET_KEY POSTGRES_PASSWORD APP_ROLE_PASSWORD TOTP_ENCRYPTION_KEY; do
       value="$(awk -F= -v k="${var}" '$1==k { sub(/^[^=]*=/, ""); print; exit }' /opt/docker/shekel/.env)"
       file_name="$(echo "${var}" | tr '[:upper:]' '[:lower:]')"
       sudo printf '%s' "${value}" | sudo tee /opt/docker/shekel/secrets/${file_name} >/dev/null
   done
   sudo chmod 0600 /opt/docker/shekel/secrets/*
   ```

   Verify each file has the right content:

   ```bash
   sudo wc -c /opt/docker/shekel/secrets/*
   # Expect: each file's byte count matches the original env var's length.
   ```

4. Replace the real values in `/opt/docker/shekel/.env` with placeholder strings. Any non-empty
   string satisfies the base file's `${VAR:?...}` interpolation; `replaced_by_docker_secret` is the
   documented form because a forensic reader sees the intent immediately:

   ```diff
   -SECRET_KEY=<the-real-secret-key>
   -POSTGRES_PASSWORD=<the-real-postgres-password>
   -APP_ROLE_PASSWORD=<the-real-app-role-password>
   -TOTP_ENCRYPTION_KEY=<the-real-totp-encryption-key>
   +SECRET_KEY=replaced_by_docker_secret
   +POSTGRES_PASSWORD=replaced_by_docker_secret
   +APP_ROLE_PASSWORD=replaced_by_docker_secret
   +TOTP_ENCRYPTION_KEY=replaced_by_docker_secret
   ```

   `TOTP_ENCRYPTION_KEY_OLD` stays as-is (empty in steady state).

5. Recreate the `db` and `app` containers so the new compose merge takes effect:

   ```bash
   cd /opt/docker/shekel
   docker compose up -d --force-recreate db app
   ```

6. Verify the migration succeeded:

   ```bash
   # The app's runtime env should show the REAL SECRET_KEY (loaded
   # from /run/secrets/secret_key by entrypoint.sh::_load_secret).
   # We do not print the value -- only its length -- so the secret
   # never lands in shell history or terminal scrollback.
   docker exec shekel-prod-app sh -c 'echo SECRET_KEY length=${#SECRET_KEY}'
   # Expect: SECRET_KEY length=64 (or whatever the real key length is).

   # docker inspect should show the placeholder, not the real value.
   docker inspect shekel-prod-app --format '{{ range .Config.Env }}{{ println . }}{{ end }}' | grep -E '^SECRET_KEY='
   # Expect: SECRET_KEY=replaced_by_docker_secret

   # entrypoint.sh log lines confirm the file-backed load.
   docker logs shekel-prod-app 2>&1 | grep '^Loaded '
   # Expect:
   #   Loaded SECRET_KEY from /run/secrets/secret_key.
   #   Loaded POSTGRES_PASSWORD from /run/secrets/postgres_password.
   #   Loaded APP_ROLE_PASSWORD from /run/secrets/app_role_password.
   #   Loaded TOTP_ENCRYPTION_KEY from /run/secrets/totp_encryption_key.
   ```

7. Backup the secrets directory. The host-side files are now the only source of truth for these
   values; losing them means a full secret rotation. Add to the backup cron job:

   ```bash
   tar -czf /mnt/nas/backups/shekel/secrets-$(date +%Y%m%d).tar.gz \
       -C /opt/docker/shekel secrets
   ```

   Encrypt the backup tarball at rest (gpg, age, or sops) -- the tarball is the equivalent of a
   master credential bundle.

### Rolling back to Posture 1

If file-backed secrets cause an issue and you need to revert:

1. Replace each placeholder in `.env` with its real value (recover from the host secrets directory
   or from your password-manager backup):

   ```bash
   for var in SECRET_KEY POSTGRES_PASSWORD APP_ROLE_PASSWORD TOTP_ENCRYPTION_KEY; do
       file_name="$(echo "${var}" | tr '[:upper:]' '[:lower:]')"
       value="$(sudo cat /opt/docker/shekel/secrets/${file_name})"
       sudo sed -i "s|^${var}=.*|${var}=${value}|" /opt/docker/shekel/.env
   done
   ```

2. Move the secrets directory aside (do NOT delete -- losing the files is irreversible without the
   password-manager backup):

   ```bash
   sudo mv /opt/docker/shekel/secrets /opt/docker/shekel/secrets.disabled
   ```

3. Recreate the affected containers:

   ```bash
   cd /opt/docker/shekel
   docker compose up -d --force-recreate db app
   ```

   `docker compose up` will fail at parse time if any secret referenced in
   `deploy/docker-compose.prod.yml` is missing its backing file -- that is the trade-off for
   explicitness. If you want to disable file-backed secrets entirely (rather than just roll them
   back temporarily), check out a previous git revision of `deploy/docker-compose.prod.yml` from
   before Commit C-38.

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

3. Restart the app container: `docker compose restart app`. The entrypoint reloads the secret on
   every start, so the rotation is in effect after the restart.
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
docker exec shekel-prod-app python scripts/rotate_sessions.py --confirm
```

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
     exist). Inline- edit the override to add it before bringing the stack back up:

     ```diff
        services:
          app:
            secrets:
              - secret_key
              - postgres_password
              - app_role_password
              - totp_encryption_key
     +        - totp_encryption_key_old
        ...
        secrets:
          ...
          totp_encryption_key:
            file: /opt/docker/shekel/secrets/totp_encryption_key
     +    totp_encryption_key_old:
     +      file: /opt/docker/shekel/secrets/totp_encryption_key_old
     ```

     Step 5 below removes the inline edit and the retired-key file after rotation.

   If `TOTP_ENCRYPTION_KEY_OLD` already has a value (e.g. from an earlier in-progress rotation),
   append the new retired value with a comma -- the application reads the value as a comma-separated
   list of Fernet keys, regardless of whether it came from an env var or a secret file. In the
   file-backed posture, write the comma-joined string into the file with `printf` (no trailing
   newline) so the file content is exactly the comma-separated list.

3. **Restart the application container so the new key list takes effect.**

   ```bash
   docker compose restart app
   ```

   At this point the application can:

     - decrypt every existing ciphertext (via the retired key listed in `TOTP_ENCRYPTION_KEY_OLD`),
       and
     - encrypt every new ciphertext under the new primary.

   Existing MFA users continue to log in successfully. This is the safe state to validate end-to-end
   before continuing.

4. **Re-wrap every existing ciphertext under the new primary.**

   ```bash
   docker exec shekel-prod-app python scripts/rotate_totp_key.py --confirm
   ```

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

   Run `docker compose up -d --force-recreate app`. The retired key is now permanently retired -- if
   it was leaked, the leak no longer confers access to the MFA secrets.

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

### Rotating POSTGRES_PASSWORD

1. Generate a new password. No specific format is required, but a long random string is recommended:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Postgres validates the new password BEFORE the rotation can complete -- the old password must
   still be in effect when you issue the `ALTER ROLE` command. Update both the running database and
   the source-of-truth secret in lockstep:

   ```bash
   # Run while the old password is still active.  Use ${OLD_PASSWORD}
   # from .env or the secret file -- the docker exec env passthrough
   # below avoids printing it to terminal scrollback.
   sudo PGPASSWORD="$(sudo cat /opt/docker/shekel/secrets/postgres_password)" \
       docker exec -e PGPASSWORD shekel-prod-db \
       psql -U shekel_user -d shekel \
       -c "ALTER ROLE shekel_user WITH PASSWORD '<new-password>';"
   ```

   (Posture 1 operators replace the `cat` above with the value from `.env` directly, e.g. via
   `grep '^POSTGRES_PASSWORD=' .env`.)

3. Install the new value at the source of truth for your posture:

   - **Posture 1 (env-backed):** update `POSTGRES_PASSWORD` in `.env`. `DATABASE_URL` and
     `DB_PASSWORD` reference `${POSTGRES_PASSWORD}` and are reconstructed by compose at the next
     `up`.
   - **Posture 2 (file-backed):** overwrite the secret file:

     ```bash
     sudo printf '%s' '<new-password>' | \
         sudo tee /opt/docker/shekel/secrets/postgres_password >/dev/null
     sudo chmod 0600 /opt/docker/shekel/secrets/postgres_password
     ```

     Leave the placeholder in `.env` unchanged. The entrypoint reloads the secret on every start and
     rebuilds `DATABASE_URL` and `DB_PASSWORD` from the new value.

4. Recreate both containers so the new password takes effect:

   ```bash
   cd /opt/docker/shekel  # or wherever your compose files live
   docker compose up -d --force-recreate db app
   ```

5. Verify the app can connect under the new password:

   ```bash
   docker exec shekel-prod-app python -c "from app import create_app; create_app().app_context().push(); from app.extensions import db; db.session.execute(db.text('SELECT 1')); print('OK')"
   ```

## Disaster Recovery: Reconstructing Secrets

If the Proxmox host is lost and must be rebuilt from scratch:

1. **Restore the database** from NAS backups using `scripts/restore.sh` (see backup/restore
   runbook).

2. **Reconstruct the secrets store** for your posture:

   - **Posture 1 (env-backed):** rebuild `.env` using `.env.example` as a template:
     - `SECRET_KEY`: generate a new one. Users will need to log in again.
     - `TOTP_ENCRYPTION_KEY`: if you have the original key backed up (see recommendation below), use
       it. If not, generate a new one and all users must re-enroll MFA.
     - `POSTGRES_PASSWORD`: use the password from the restored backup, or set a new one and update
       the PostgreSQL user password.
     - `APP_ROLE_PASSWORD`: any sufficiently random secret; `entrypoint.sh` reprovisions the
       `shekel_app` role with this password on every start.
     - All other variables have defaults or are non-sensitive.

   - **Posture 2 (file-backed):** restore `/opt/docker/shekel/secrets/` from your encrypted backup
     tarball. If the backup is lost too, fall back to Posture 1 above and follow the "Migrating to
     Docker secrets" procedure after the host is up.

3. **Start the application**:

   ```bash
   docker compose up -d
   ```

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
- `APP_ROLE_PASSWORD` value
- Date each secret was last rotated
- Posture in use (env-backed vs. file-backed)

This is the fastest disaster recovery path: copy values from the password manager into either a
fresh `.env` file or fresh secret files under `/opt/docker/shekel/secrets/`, depending on the
posture you are restoring to.
