#!/usr/bin/env bash
# verify_prod_deploy.sh -- read-only checks of production after a deploy.
#
# Usage:
#   verify_prod_deploy.sh [SINCE]    # SINCE: docker logs --since (default 10m)
#
# Exit 0, printing VERIFY OK, only if every check holds; otherwise exit 1,
# printing VERIFY FAILED.  It changes no file, pin or container; its probes
# are anonymous requests, which the app's rate limiter counts like any other.
#   * The app container reports itself healthy.
#   * Anonymous probes from INSIDE it (no port is exposed): /health is 200; a
#     private URL and an unknown URL both 302 to /login (the login gate's
#     default deny, ruling bank_import:R-BI4); /login is 200.
#   * Gunicorn holds no superuser or owner credential (card plan#2166): its
#     environment names none of the variables that carry one, and its secret
#     files hold neither password.  Names only: no value is printed.
#   * Its log since SINCE was read, and holds no Traceback or ERROR line.
#
# Run it from ~/projects/Shekel once that checkout is fast-forwarded to main,
# the checkout shekel-deploy runs from, with the time shekel-deploy started
# as SINCE (card X-cx L9's /release skill is to do both).
#
# A KNOWN FALSE FAILURE (finding BAL-628, owner X-do): after an in-place
# RESTART of the same container, not after a deploy (which creates a fresh
# one), gunicorn's control server logs an ERROR line, `Control server error:
# [Errno 13] Permission denied`, and this reads VERIFY FAILED.  Read the
# ERROR line before treating a failure after a restart as real.
#
# Written outside the repository for the coordinator's deploys (2026-09-12)
# and moved here by card X-cx L9a, so the check that grades production is
# reviewed and linted like the script whose work it grades.
set -u

CONTAINER="${SHEKEL_CONTAINER_NAME:-shekel-prod-app}"
since="${1:-10m}"
ok=0

status=$(docker ps --filter "name=${CONTAINER}" --format '{{.Status}}')
echo "container ${CONTAINER}: ${status}"
case "$status" in
    *"(healthy)"*) ;;
    *)
        echo "FAIL: container not healthy"
        ok=1
        ;;
esac

probe() {
    docker exec "$CONTAINER" python -c "
import urllib.request, urllib.error, sys
url='http://localhost:8000'+sys.argv[1]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
op=urllib.request.build_opener(NoRedirect)
try:
    r=op.open(url, timeout=5); print(r.status, r.headers.get('Location',''))
except urllib.error.HTTPError as e: print(e.code, e.headers.get('Location',''))
" "$1"
}

expect() { # expect <path> <status> [location-prefix]
    local out code loc
    out=$(probe "$1")
    code=${out%% *}
    loc=${out#* }
    if [ "$code" = "$2" ] && { [ -z "${3:-}" ] || [[ "$loc" == "$3"* ]]; }; then
        echo "ok   $1 -> $out"
    else
        echo "FAIL $1 -> $out (expected $2 ${3:-})"
        ok=1
    fi
}

expect /health 200
expect /dashboard 302 /login
expect /no-such-url-xyz 302 /login
expect /login 200

# What gunicorn must not hold: the variables that carry the superuser's or the
# owner's password (one inside a database URL), named whatever their value, a
# blank one included, and the two passwords' secret files.
CREDENTIAL_VARIABLES=(POSTGRES_PASSWORD POSTGRES_PASSWORD_FILE DB_PASSWORD DATABASE_URL
    OWNER_ROLE_PASSWORD PGPASSWORD)
CREDENTIAL_FILES=(postgres_password owner_role_password)

held() { # held <environ|files> <path> <name>...: those of the names held at path
    # Prints them on one line, in the order asked, and exits non-zero when it
    # cannot read the path.  The values are read inside the container and
    # never leave it.  A directory that does not exist holds no file.
    docker exec "$CONTAINER" python -c "
import os, sys
kind, path, names = sys.argv[1], sys.argv[2], sys.argv[3:]
if kind == 'environ':
    with open(path, 'rb') as f:
        present = {entry.partition(b'=')[0].decode('ascii', 'replace')
                   for entry in f.read().split(b'\0')}
else:
    try:
        present = set(os.listdir(path))
    except FileNotFoundError:
        present = set()
print(' '.join(name for name in names if name in present))
" "$@"
}

# Gunicorn's environment is PID 1's, as /proc/1/environ shows it: the
# entrypoint execs gunicorn, so PID 1 holds what the entrypoint exported,
# from the secret files too, where a `docker exec` holds only what the
# container was created with.  The read must name DATABASE_URL_APP, which the
# entrypoint exports just before that exec: one without it is not gunicorn's
# (an init in front of it, say) and proves nothing.
if ! got=$(held environ /proc/1/environ DATABASE_URL_APP "${CREDENTIAL_VARIABLES[@]}"); then
    echo "FAIL: could not read gunicorn's environment, /proc/1/environ"
    ok=1
elif [ "${got%% *}" != DATABASE_URL_APP ]; then
    echo "FAIL: /proc/1/environ is not gunicorn's: it names no DATABASE_URL_APP"
    ok=1
elif [ "$got" != DATABASE_URL_APP ]; then
    echo "FAIL gunicorn's environment names ${got#DATABASE_URL_APP }"
    ok=1
else
    echo "ok   gunicorn's environment names no superuser or owner credential"
fi
if ! got=$(held files /run/secrets "${CREDENTIAL_FILES[@]}"); then
    echo "FAIL: could not read gunicorn's secret files, /run/secrets"
    ok=1
elif [ -n "$got" ]; then
    echo "FAIL gunicorn's secret files hold ${got}"
    ok=1
else
    echo "ok   gunicorn's secret files hold no superuser or owner password"
fi

# Read first, then counted: piped straight into `grep -c`, a `docker logs`
# that failed counted zero error lines and passed this check.
if logs=$(docker logs --since "$since" "$CONTAINER" 2>&1); then
    bad=$(grep -cE 'Traceback|ERROR' <<<"$logs")
    echo "log lines with Traceback/ERROR in the last ${since}: ${bad}"
    [ "$bad" = 0 ] || ok=1
else
    echo "FAIL: could not read the log of ${CONTAINER}: ${logs}"
    ok=1
fi

if [ "$ok" = 0 ]; then
    echo "VERIFY OK"
else
    echo "VERIFY FAILED"
fi
exit "$ok"
