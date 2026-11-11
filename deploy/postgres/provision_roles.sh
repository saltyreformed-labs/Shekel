#!/usr/bin/env bash
# provision_roles.sh -- the database's two logins, and who owns its objects.
#
# The ONE place a Shekel login is made or its password set (rulings
# plan#3412, plan#3413).  It runs inside the database container as the
# bootstrap superuser, over the container's own socket, which needs no
# password, so the superuser's password never leaves that container:
#
#   shekel_owner  owns the database, its schemas and every object in them,
#                 and runs the deploy (the migrations, the seeds, the grants).
#                 Not a superuser.
#   shekel_app    the web server's login: data reads and writes only, granted
#                 by the deploy (scripts/init_db_role.sql).
#
# Two callers, and only two:
#   * The postgres image's first boot.  The compose files mount this file at
#     /docker-entrypoint-initdb.d/10-provision-roles.sh, which the image runs
#     once, when it creates the cluster, against the server it starts for
#     that (socket only).  It must stay executable: the image RUNS an
#     executable hook and SOURCES any other into its own shell.
#   * An operator, by hand, in the running container: after a password is
#     changed, once for a database made before this script existed, and
#     after a backup is restored without its owners.
#       docker exec shekel-prod-db /docker-entrypoint-initdb.d/10-provision-roles.sh
#
# Each password is read as the app's entrypoint reads a secret: the file
# /run/secrets/<name> when one is mounted, else the environment variable.
#   shekel_owner  /run/secrets/owner_role_password, else OWNER_ROLE_PASSWORD
#   shekel_app    /run/secrets/app_role_password,   else APP_ROLE_PASSWORD
# A password that is empty or shorter than 16 characters is refused before
# the database is touched.  The passwords reach psql through its own
# environment and its \getenv, never its argument list, which any process in
# the container could read in /proc/<pid>/cmdline.
#
# Why each object is handed over one at a time: PostgreSQL refuses REASSIGN
# OWNED for objects of the bootstrap superuser ("required by the database
# system"), and the bootstrap superuser is the image's POSTGRES_USER,
# shekel_user, which also cannot be made a non-superuser (both measured
# 2026-11-10 on the postgres:18-alpine image the compose files pin).  ALTER
# TABLE ... OWNER carries a table's indexes, its column sequences and its row
# type with it.  Anything left outside the system catalogs that the owner
# does not own after that is refused by name, so a kind of object this script
# does not hand over fails here, not at the next migration.
#
# Everything runs in ONE transaction: a refusal leaves every role, password
# and owner as it found them.  Run again with the same passwords, it changes
# no login, password or owner.
set -euo pipefail

# _password VAR SECRET: set and export VAR to the password in
# /run/secrets/SECRET when that file exists, else to VAR's own value; refuse
# an empty or short one, naming where it came from but never what it is.
_password() {
    local var="$1" file="/run/secrets/$2" value origin
    if [ -f "$file" ]; then
        if [ ! -r "$file" ]; then
            echo "ERROR: ${file} exists but this container's user cannot read it." >&2
            echo "       Make the host file readable by uid ${EUID} (see docs/runbook_secrets.md)." >&2
            exit 1
        fi
        # $(< file) drops a trailing newline an editor or echo left, as the
        # app's _load_secret does, so the password matches the one it sends.
        value="$(<"$file")"
        origin="$file"
    else
        value="${!var:-}"
        origin="$var"
    fi
    if [ -z "$value" ]; then
        echo "ERROR: no password for ${var}: ${origin} is empty or unset." >&2
        exit 1
    fi
    if [ "${#value}" -lt 16 ]; then
        echo "ERROR: the password in ${origin} is shorter than 16 characters (got ${#value})." >&2
        echo "       Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"" >&2
        exit 1
    fi
    printf -v "$var" '%s' "$value"
    export "${var?}"
}

_password OWNER_ROLE_PASSWORD owner_role_password
_password APP_ROLE_PASSWORD app_role_password

psql -v ON_ERROR_STOP=1 --no-psqlrc --single-transaction --quiet \
    --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB:-${POSTGRES_USER}}" <<'SQL'
\getenv owner_password OWNER_ROLE_PASSWORD
\getenv app_password APP_ROLE_PASSWORD
-- psql substitutes :'name' outside dollar quotes only, so each password
-- crosses into the DO block through a setting local to this transaction.
SET LOCAL shekel.owner_password TO :'owner_password';
SET LOCAL shekel.app_password TO :'app_password';

-- The two logins.  Every attribute that could reach past the database's own
-- rows is pinned off on each run, so a role altered by hand is put back.
DO $$
DECLARE
    login record;
BEGIN
    FOR login IN
        SELECT * FROM (VALUES ('shekel_owner', 'shekel.owner_password'),
                              ('shekel_app', 'shekel.app_password')) AS l(role, setting)
    LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = login.role) THEN
            EXECUTE format('CREATE ROLE %I', login.role);
        END IF;
        EXECUTE format(
            'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
            'NOREPLICATION NOBYPASSRLS PASSWORD %L',
            login.role, current_setting(login.setting));
    END LOOP;
END
$$;

-- The owner owns the database and everything in it outside the system
-- catalogs.  "public" stays pg_database_owner's, which makes the database's
-- owner its owner without a change.
DO $$
DECLARE
    owner_oid CONSTANT oid := 'shekel_owner'::regrole;
    item record;
    handed integer := 0;
BEGIN
    IF (SELECT datdba FROM pg_database WHERE datname = current_database()) <> owner_oid THEN
        EXECUTE format('ALTER DATABASE %I OWNER TO shekel_owner', current_database());
        handed := handed + 1;
    END IF;

    FOR item IN
        SELECT nspname FROM pg_namespace
        WHERE nspname !~ '^pg_' AND nspname NOT IN ('information_schema', 'public')
          AND nspowner <> owner_oid
    LOOP
        EXECUTE format('ALTER SCHEMA %I OWNER TO shekel_owner', item.nspname);
        handed := handed + 1;
    END LOOP;

    FOR item IN
        SELECT c.oid::regclass AS relation FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r', 'p') AND c.relowner <> owner_oid
    LOOP
        EXECUTE format('ALTER TABLE %s OWNER TO shekel_owner', item.relation);
        handed := handed + 1;
    END LOOP;

    FOR item IN
        SELECT p.oid::regprocedure AS routine FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND p.proowner <> owner_oid
    LOOP
        EXECUTE format('ALTER ROUTINE %s OWNER TO shekel_owner', item.routine);
        handed := handed + 1;
    END LOOP;

    -- Whatever is still someone else's is a kind this script does not hand
    -- over (a view, a free-standing sequence, a type of its own): refused by
    -- name, so the gap is closed here and not met by a migration.
    FOR item IN
        SELECT format('%s %s', CASE c.relkind WHEN 'S' THEN 'sequence' WHEN 'v' THEN 'view'
                                              WHEN 'm' THEN 'materialized view'
                                              WHEN 'f' THEN 'foreign table'
                                              WHEN 'c' THEN 'composite type'
                                              ELSE 'relation (relkind ' || c.relkind::text || ')'
                                         END,
                      c.oid::regclass) AS what,
               pg_get_userbyid(c.relowner) AS owner
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND c.relowner <> owner_oid
        UNION ALL
        SELECT format('type %s', t.oid::regtype), pg_get_userbyid(t.typowner)
        FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND t.typowner <> owner_oid
        LIMIT 1
    LOOP
        RAISE EXCEPTION 'the % is owned by %, not shekel_owner, and this script hands over '
                        'only schemas, tables and routines', item.what, item.owner
            USING HINT = 'Hand it over by hand (ALTER ... OWNER TO shekel_owner) or teach '
                         'deploy/postgres/provision_roles.sh its kind, then run it again.';
    END LOOP;

    RAISE NOTICE 'shekel_owner and shekel_app provisioned; % object(s) handed to shekel_owner.',
        handed;
END
$$;
SQL
