#!/bin/bash
# The migrate service's entrypoint: every command run there runs as the
# database owner, shekel_owner, under the deploy's configuration.
#
#   The deploy        its command, scripts/deploy.sh, run once per deploy
#                     before the web server starts.
#   A maintenance     docker compose run --rm --no-deps migrate \
#   script                python scripts/audit_cleanup.py
#                     runs one script as the owner, with no deploy.
#
# It holds the owner's password and no other credential (rulings plan#3412,
# plan#3413): no superuser password, which never leaves the database
# container, and no web secret (ruling plan#3450).  It loads that password,
# builds DATABASE_URL from it, waits for PostgreSQL and execs its command.
set -euo pipefail

# The secret loader, the wait for PostgreSQL and the database URL, shared
# with the web server's entrypoint.
# shellcheck source=_entrypoint_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_entrypoint_lib.sh"

if [ "$#" -eq 0 ]; then
    echo "ERROR: no command to run as the database owner." >&2
    echo "       The migrate service's command is scripts/deploy.sh; a maintenance" >&2
    echo "       script is named after the service: docker compose run --rm --no-deps" >&2
    echo "       migrate python scripts/<name>.py" >&2
    exit 1
fi

# The deploy's configuration (app/config.py, DeployConfig): this container
# decides it, once, here, so every script run here builds it, and a script
# that needs a web secret refuses here because it belongs in the web
# container (ruling plan#3451).
export FLASK_ENV=deploy

# The owner's password, from /run/secrets/owner_role_password when mounted,
# else OWNER_ROLE_PASSWORD.  deploy/postgres/provision_roles.sh set it in the
# database container, refusing one under 16 characters.
_load_secret OWNER_ROLE_PASSWORD owner_role_password
if [ -z "${OWNER_ROLE_PASSWORD:-}" ]; then
    echo "ERROR: the database owner's password is not set." >&2
    echo "       Mount it as the Docker secret owner_role_password, or set" >&2
    echo "       OWNER_ROLE_PASSWORD: the password deploy/postgres/provision_roles.sh" >&2
    echo "       gave shekel_owner in the database container." >&2
    exit 1
fi

# The owner's URL, the one the deploy's configuration connects by.
DATABASE_URL="$(_database_url shekel_owner "${OWNER_ROLE_PASSWORD}")"
export DATABASE_URL

_wait_for_postgres shekel_owner

exec "$@"
