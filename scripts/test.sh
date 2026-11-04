#!/bin/bash
# Shekel Budget App -- Test Runner Wrapper
#
# Gives the run a PRIVATE POSTGRES CLUSTER, then runs pytest against it
# with whatever arguments were passed, forwarded verbatim:
#
#     ./scripts/test.sh                           # full suite
#     ./scripts/test.sh tests/test_routes/...     # targeted run
#     ./scripts/test.sh -n 0 -x                   # pass-through flags
#
# A cluster of this run's own
#     The container comes from the image ``scripts/build_test_db_image.py``
#     bakes -- the test template already inside it -- and is reached over a
#     UNIX SOCKET in a per-run directory.  It is removed afterwards.  Its
#     whole life -- the socket directory, the ``docker run`` and its
#     settings, the readiness wait and the teardown -- is
#     ``scripts/private_cluster.py``'s, the one home the release rehearsal
#     kit's bench (``tools/rehearsal/bench.py``) starts its own cluster
#     through too; this wrapper makes the token that owns the cluster, arms
#     the traps that stop it by that token, and runs pytest against it.
#
#     NOTHING IS SHARED, SO NOTHING NEEDS COORDINATING.  Until
#     ``balance:X-br-4`` this wrapper also carried a hygiene restart, a
#     live-backend probe, a per-worktree template name and a per-worktree
#     worker-database prefix, and the repo carried a suite-slot lock beside
#     them.  Every one of those existed because ONE postmaster served every
#     worktree; a cluster per run leaves each of them nothing to protect, so
#     they were deleted rather than kept as a second way to do nothing.  It
#     also fixes what none of them ever did: two runs in the SAME worktree.
#
#     WHAT THE DELETION DOES NOT FIX is that concurrent runs still share the
#     host's cores.  That is a resource fact rather than a defect, so this
#     script REPORTS the other runs it can see and proceeds -- see the block
#     above the container name for the measurement and the argument.
#
#     What it costs, measured 2026-09-05 on ``tests/test_utils/`` (385
#     tests), same host, back to back: 12.7 s against the shared cluster,
#     14.1 s here with the image already built and its layers warm, 22.0 s
#     on the first run after a rebuild.  The fixed part is the image
#     verification (1.4 s) plus the container's whole life -- start,
#     readiness and removal -- which is 0.33 s.
#
#     NO PORT IS PUBLISHED.  The cluster serves one process on this machine,
#     and on a rootless daemon a published port is a 25-40% failure: docker
#     allocates it inside the container's network namespace and rootlesskit
#     binds that number on the HOST.  ``scripts/private_cluster.py`` holds
#     the measurements.
#
#     THE DAEMON IS NOT THE SYSTEM ONE.  A container per run on the system
#     daemon is exactly the churn ``docs/test-harness-isolation.md`` was
#     written to stop: that daemon runs the production database and the
#     homelab wud/cadvisor/alloy stack watches every container on it.  So
#     ``scripts/private_cluster.py`` refuses a non-rootless daemon rather
#     than quietly spamming it, and this wrapper inherits the refusal.
#
# Environment variables read:
#     DOCKER_HOST        The daemon this run talks to.  Chosen by
#                        ``scripts/private_cluster.py daemon``, which takes
#                        the rootless socket in ``XDG_RUNTIME_DIR`` when this
#                        is unset and one is there; then EXPORTED so pytest
#                        and ``tests/test_deploy``'s conftest see the same
#                        endpoint the run uses.
#     XDG_RUNTIME_DIR, SHEKEL_ALLOW_HOST_DOCKER, CI  Read by
#                        ``scripts/private_cluster.py``: where the rootless
#                        socket and the per-run socket directory are, and the
#                        two sanctions for a non-rootless daemon (its
#                        docstring says how each is read).  CI also stops
#                        the venv's sync below: CI's install has built it.
#     READINESS_TIMEOUT_SECONDS  Cap on the readiness wait, read by
#                        ``scripts/private_cluster.py``.  Default: 15.
#     TEST_DB_IMAGE      An image to use INSTEAD of building and verifying
#                        one.  Skips build_test_db_image.py entirely, so
#                        whoever sets it owns the image's correctness.  Its
#                        one caller today is
#                        tests/test_scripts/test_test_runner/, which drives
#                        a stub docker that cannot satisfy a real
#                        verification.  It is NOT set by CI: CI's jobs run
#                        this wrapper like any other run and bake the image on
#                        their own runner (``bank_import:X-gy``).  Unset is
#                        the developer's case and every real run, and is
#                        verified on every invocation.
#     PYTEST_MARKER_EXPR Marker expression handed to pytest.
#                        Default: ``not docker`` (see the note above the
#                        pytest invocation at the bottom of this file).
#                        An explicit ``-m`` in the caller's arguments wins,
#                        because pytest keeps the last one.
#
# What it does NOT read, and where those went:
#     TEST_DATABASE_URL / TEST_ADMIN_DATABASE_URL are EXPORTED by this
#     script, not read from ``.env``: they name this run's socket, which no
#     dotenv can know.  ``TEST_TEMPLATE_DATABASE`` and ``TEST_DB_PREFIX``
#     are gone entirely -- a private cluster holds exactly one template and
#     one run's worker databases, so there is nothing to rename them away
#     from.  Their single spellings now live as constants in
#     ``tests/conftest.py``, ``scripts/build_test_template.py`` and
#     ``scripts/build_test_db_image.py``, which must agree.
#     LC_ALL is EXPORTED too, over any value the caller had: ``create_app``
#     refuses to start under anything but production's locale (ruling
#     recurrence:R-R92), so the suite -- and CI, which runs it through here --
#     always runs under that locale.
#     VIRTUAL_ENV and PATH are EXPORTED too, outside CI where the tree holds
#     ``uv.lock``, naming this tree's venv over any value the caller had (the
#     block above ``docker``'s check says why).
#
# Exit codes:
#     Whatever pytest returns, in the ordinary case.  A bootstrap failure
#     exits 2 without running pytest: no python interpreter, no uv or a sync
#     that fails, no docker, a
#     daemon that is not rootless or cannot be reached, a socket path over
#     the kernel's 107-byte limit or not UTF-8, an image that cannot be
#     prepared, a container name docker refuses, a cluster that does not
#     accept connections within READINESS_TIMEOUT_SECONDS, and one that
#     reports ready without leaving a socket (``scripts/private_cluster.py``
#     refuses the socket path, the readiness and the missing socket, and
#     any other start it cannot finish).  NO COUNT IS GIVEN:
#     every attempt to count them in a comment
#     has been wrong.  A run that INT, TERM or HUP ends has no status: it
#     stops its cluster, then dies BY that signal, as a command a terminal's
#     Ctrl-C ended does (the block above the traps says why).  Other
#     non-pytest statuses are docker's or the shell's, not this script's.

set -euo pipefail

# Resolved via BASH_SOURCE, not the invoker's cwd, so a run from outside the
# repo root still finds the project (OPS/SH-26).
_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Production's locale (ruling recurrence:R-R92; the Dockerfile sets the same
# value).  Exported HERE, before anything below builds the app: the image
# builder further down runs ``scripts/build_test_template.py``, which calls
# ``create_app``, and passes this value through its scrubbed environment.
export LC_ALL=C.UTF-8

# The image builder imports app.ref_seeds and app.audit_infrastructure to
# read the counts it verifies against, so it needs the project's
# interpreter -- a bare python3 has neither the package nor psycopg.
if [ -x "${_REPO_ROOT}/.venv/bin/python" ]; then
    _PYTHON="${_REPO_ROOT}/.venv/bin/python"
else
    _PYTHON="$(command -v python3 || true)"
fi
if [ -z "$_PYTHON" ]; then
    echo "[test.sh] needs a python interpreter to prepare the test-db image." >&2
    exit 2
fi

# THE SUITE RUNS WHAT THIS TREE'S LOCK NAMES (ruling plan#2828).  A lane has a
# venv of its own, built from its own ``uv.lock`` (``tools/quill/lane.py`` makes
# a new lane's), and this keeps it on that lock through every merge; what that
# costs is in docs/testing-standards.md, Test Run Guidelines.  This block and the
# lock reach a lane in one merge, so a lane before the lock keeps its link because
# its wrapper has no such block; here, a tree with no lock has nothing to build from.
#
# A LINK IS REMOVED, NEVER FOLLOWED.  An older lane's ``.venv`` links the main
# checkout's, and uv syncs through a link into its target (measured 2026-11-04,
# on scratch venvs), which is the venv the edit hooks, the tracker's sync and
# every lane still before the lock read.  ``rm`` of the link leaves that target
# whole (measured with it); a dangling link goes the same way, which uv refuses
# (``File exists``).  ``-f``, as two runs started together can both see the link.
#
# ``VIRTUAL_ENV`` AND ``PATH`` NAME THE TREE'S VENV FROM HERE ON.  A session an
# older lane's start began holds the main checkout's venv first on ``PATH``, and
# the ``pytest`` below is found on ``PATH``, so replacing the link alone would
# still run the main checkout's pytest over that venv's packages: stopping at
# ``import valkey`` (measured 2026-11-04).
#
# CI IS LEFT ALONE: ``.github/actions/install-lock`` is CI's one installer and has
# already built the job's venv, so there is nothing here for this to sync.
if [ -f "${_REPO_ROOT}/uv.lock" ] \
    && ! "$_PYTHON" "${_REPO_ROOT}/scripts/private_cluster.py" on-ci; then
    if ! command -v uv >/dev/null 2>&1; then
        echo "[test.sh] needs uv, which is not on PATH, to build .venv from uv.lock." >&2
        exit 2
    fi
    if [ -L "${_REPO_ROOT}/.venv" ]; then
        rm -f -- "${_REPO_ROOT}/.venv"
        echo "[test.sh] removed the link at ${_REPO_ROOT}/.venv:" \
            "this tree holds uv.lock, so it gets a venv of its own." >&2
    fi
    VIRTUAL_ENV="${_REPO_ROOT}/.venv"
    PATH="${VIRTUAL_ENV}/bin:${PATH}"
    export VIRTUAL_ENV PATH
    if ! uv sync --locked --quiet --directory "$_REPO_ROOT"; then
        echo "[test.sh] uv sync --locked failed in ${_REPO_ROOT} (uv's own words" \
            "are above), so no test ran, and .venv is not usable until it succeeds:" \
            "put right what uv names, then run this again." >&2
        exit 2
    fi
    _PYTHON="${VIRTUAL_ENV}/bin/python"
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "[test.sh] needs docker, which is not on PATH." >&2
    exit 2
fi

# THE DAEMON THIS RUN TALKS TO is ``scripts/private_cluster.py``'s to choose,
# and to REFUSE when it is not rootless: the system daemon on this host runs the
# production database, and the homelab wud/cadvisor/alloy stack watches every
# container on it -- exactly the churn ``docs/test-harness-isolation.md``
# exists to stop.  The rule lives in the act that starts a cluster, so no
# caller can start one without it (the coordinator's ruling on K1a-2's review,
# 2026-10-19); this wrapper held its only copy until then.  It is asked HERE,
# before the image is prepared, so a refused daemon gets no image either and
# the image is baked on the daemon the cluster will run on; the start asks it
# again, as the act.  A refusal names itself and the way past it, and this
# exits 2.
#
# Its answer is EXPORTED when it names an endpoint -- ``DOCKER_HOST`` as given,
# or the rootless socket it found -- so pytest and ``tests/test_deploy``'s
# conftest see the daemon this run uses.  That conftest treats any non-default
# endpoint as isolated, which only takes effect for a caller who ALSO asks for
# those tests with ``PYTEST_MARKER_EXPR=docker``, because this wrapper defaults
# to ``not docker`` further down and the marker deselects them before the
# conftest is consulted.  An earlier draft of this comment claimed the export
# alone un-skipped them; a review measured otherwise.
_daemon="$("$_PYTHON" "${_REPO_ROOT}/scripts/private_cluster.py" daemon)" || exit 2
if [ -n "$_daemon" ]; then
    DOCKER_HOST="$_daemon"
    export DOCKER_HOST
fi
unset _daemon

# TEST_DB_IMAGE lets a caller supply an image this run should use and skip
# the build-and-verify entirely.  It exists for a caller that knows more than
# this script does: today that is this wrapper's own tests, which drive a stub
# docker that cannot satisfy a real verification.  CI is NOT such a caller --
# its jobs bake and verify the image here like every other run.  When the
# variable is unset -- the developer's case, and every real run -- the
# builder runs and checks the
# cached image on EVERY invocation rather than trusting the tag, so a stale or
# damaged image is rebuilt here instead of being cloned from for the whole run.
if [ -n "${TEST_DB_IMAGE:-}" ]; then
    _image="$TEST_DB_IMAGE"
    echo "[test.sh] using the image given in TEST_DB_IMAGE ($_image);" \
        "it is NOT verified here, so whoever set it owns that" >&2
else
    _image="$("$_PYTHON" "${_REPO_ROOT}/scripts/build_test_db_image.py" \
        --print-tag 2>/dev/null || true)"
    if [ -z "$_image" ]; then
        echo "[test.sh] could not resolve the test-db image tag; run" \
            "scripts/build_test_db_image.py by hand to see why." >&2
        exit 2
    fi
    if ! "$_PYTHON" "${_REPO_ROOT}/scripts/build_test_db_image.py" >&2; then
        echo "[test.sh] could not prepare $_image" >&2
        exit 2
    fi
fi

# WHO ELSE IS RUNNING, AND WHY THIS IS A NOTE RATHER THAN A LOCK.
#
# ``scripts/suite_slot.sh`` was a real mkdir-based mutex every gating run had
# to take, and ``balance:X-br-4`` deleted it.  Its header named TWO hazards and
# a cluster per run only kills one of them:
#
#   * CORRECTNESS is now structural.  Nothing can restart another run's
#     postmaster and nothing can collide on a database name, because there is
#     no shared postmaster and no shared name space.  A lock bought exactly
#     this and no longer buys it.
#   * CONTENTION SURVIVES, because the cores do not multiply.  Measured
#     2026-09-05 on this 24-core host: one suite alone finished in 349 s; with
#     THREE running (two private clusters plus a peer's gating run) two of them
#     reached only ~38% in 13 minutes, at a run-queue of 32, ~950,000 context
#     switches/sec and 28% iowait.  No test FAILED in either -- the slowest
#     single test is 2.58 s against pytest.ini's per-test timeout (30 s then;
#     90 s from 2026-09-13 and 50 s since 2026-09-22, sized to CI's clock --
#     docs/testing-standards.md, Test Run Guidelines), so there was roughly
#     11x of headroom and that measurement was sitting on it.
#
# So what survives is a resource fact, not a defect, and the right instrument
# for a resource fact is information rather than a mutex: this prints what else
# is live and proceeds.  Nothing to acquire, nothing to release, nothing to go
# stale, and no run that cannot start because somebody forgot to release.
#
# **The CWD locates a peer; the ARGV does not.**  A lane still before the lock
# links the main checkout's venv, so its pytest reads
# ``/home/josh/projects/Shekel/.venv/bin/pytest`` in its command line whatever
# tree it is testing.  Reading ``/proc/<pid>/cwd`` was already the doctrine's
# rule for killing by PID; it is the same rule for naming a peer, and an
# argv-based version of this block would have named the main checkout for
# every session on the box -- as one did, misattributing a peer's suite during
# the session that wrote this.
#
# **What cwd CANNOT do is tell two sessions apart inside ONE checkout**, and
# that case is real rather than theoretical: the shared main checkout is where
# a release lane runs.  Two such peers print two lines with the same directory.
# That is a weaker label than it looks and a strong enough one for the job --
# what this note is FOR is how many suites are competing for the cores, and the
# PID count answers that whatever the directories say.  Do not upgrade the
# claim: nothing in ``/proc`` names the session, so a fuller identity would
# have to come from the sessions themselves.
#
# Fails soft in every direction: no ``pgrep``, an unreadable ``/proc`` entry, a
# process that exits mid-scan -- all just mean a quieter note.  A status
# report may never stop a run.
if command -v pgrep >/dev/null 2>&1; then
    _peers=""
    # ``--collect-only`` writes no database and takes no worker, so it is not
    # a peer for this purpose; ``suite_slot.sh`` exempted it too.
    for _peer_pid in $(
        {
            pgrep -x python3 2>/dev/null || true
            pgrep -x python 2>/dev/null || true
            pgrep -f 'bin/pytest' 2>/dev/null || true
        } | sort -u
    ); do
        if [ -r "/proc/${_peer_pid}/cmdline" ]; then
            _peer_argv="$(tr '\0' '\n' <"/proc/${_peer_pid}/cmdline" 2>/dev/null || true)"
            # One pass over the arguments in bash, with no pipe (plan step
            # balance:X-dm): `printf ... | grep -q` under pipefail could read
            # a present argument as absent once grep left the pipe early,
            # dropping a running pytest from this note or naming one that
            # only collects.
            _peer_is_pytest=""
            _peer_collects_only=""
            while IFS= read -r _peer_arg; do
                case "$_peer_arg" in
                    pytest | */bin/pytest) _peer_is_pytest=yes ;;
                    --collect-only) _peer_collects_only=yes ;;
                    *) ;;
                esac
            done <<<"$_peer_argv"
            if [ -n "$_peer_is_pytest" ] && [ -z "$_peer_collects_only" ]; then
                _peers="${_peers}[test.sh]   pid ${_peer_pid}  $(readlink "/proc/${_peer_pid}/cwd" 2>/dev/null || echo '(cwd unreadable)')
"
            fi
        fi
    done
    if [ -n "$_peers" ]; then
        echo "[test.sh] NOTE: another pytest is running on this host:" >&2
        printf '%s' "$_peers" >&2
        echo "[test.sh]   Your run is ISOLATED and cannot corrupt it -- but you" \
            "share this host's cores. Measured 2026-09-05: 349 s alone," \
            "against ~38% in 13 minutes with three suites running. Consider" \
            "waiting if either run is a gate." >&2
    fi
    unset _peers _peer_pid _peer_argv _peer_arg _peer_is_pytest _peer_collects_only
fi

# THE CLUSTER IS OWNED BY A TOKEN this run makes HERE, before anything
# starts, so a trap that fires at any moment -- the start killed outright
# included -- stops by it what this run's start made and nothing else: the
# start labels its container with the token as docker creates it and names
# the socket directory after it, and the stop removes only what carries it
# (``scripts/private_cluster.py``; the coordinator's ruling on K1a commit B,
# 2026-10-19).  So every path below stops by the token, with no decision
# about whether the start made anything: a container another run made is
# never this run's to remove, whatever it is named.  One thing outlives that
# stop: a start KILLed while its ``docker run`` is in flight leaves its
# socket directory, empty, which docker makes again as the mount's source
# after the stop has removed it (16 of 16, K1a's review, 2026-10-19).
#
# The container's NAME carries the PID and the token's first eight digits:
# the PID for the humans reading ``docker ps``, and the token so no other
# run's container can already hold the name.  Two runs can coexist --
# including two in the SAME worktree, which no per-worktree database prefix
# ever fixed.  Nothing reads the name to decide anything.
_token="$("$_PYTHON" "${_REPO_ROOT}/scripts/private_cluster.py" token)"
_run_container="shekel-testrun-$$-${_token:0:8}"

# Stopping is the one home's: it removes what carries the token -- the
# container with its anonymous volume, and the socket directory -- and NAMES
# on stderr anything that survived.  The wrapper still exits with pytest's
# status, as it always has; ``|| true`` because a survivor is reported
# there, not by aborting the teardown under ``set -e``.
_teardown_run_container() {
    "$_PYTHON" "${_REPO_ROOT}/scripts/private_cluster.py" stop "$_token" || true
}

# THE RUN'S TWO LONG CHILDREN -- the start, then pytest -- RUN IN THE
# BACKGROUND AND ARE ``wait``ED, so a trap runs the moment a signal lands
# rather than once the child has ended: bash services no trap while a
# FOREGROUND child runs, and ``wait`` is interruptible.  ``_child`` is the
# running one's PID, empty while none runs.  Each is launched with SIGINT
# back at its default (``trap - INT``): bash starts a background child of a
# non-interactive shell with SIGINT IGNORED, so pytest sat out a terminal's
# Ctrl-C and the wrapper waited for its whole run (12.1 s for a stub pytest
# sleeping 12, measured 2026-10-19).  Both stay in the wrapper's process
# group, so what reaches the group reaches them as it reaches a foreground
# command: a terminal's Ctrl-C, a supervisor's KILL to the group, Ctrl-Z.
#
# Each trap passes TERM and HUP on to the running child, and NOT INT.  A
# supervisor sends TERM or HUP to the wrapper's PID alone; a terminal sends
# Ctrl-C to the whole group, so the child already has that one, and a second
# SIGINT 10-50 ms after the first cut pytest-xdist's own shutdown short --
# death by SIGINT with a traceback, where one gives its summary and exit 2
# (measured 2026-10-19).  Then it waits for the child, stops by the token, and
# ends the wrapper BY that signal rather than by an exit status, so a shell
# that ran it sees the Ctrl-C and stops too: an ``exit 130`` told that shell
# the wrapper had dealt with the signal, and it carried on with its next
# command (measured).
#
# What is NOT passed on: an INT sent to the wrapper's PID ALONE, not to its
# group.  The wrapper then waits for the running child's own end and stops by
# the token.  The one way to pass INT on exactly once from every source --
# each child in a process group of its own, every signal passed on to that
# group -- puts the child out of reach of anything sent to the wrapper's
# group: a KILL to the group would orphan a twelve-worker suite with nobody
# to stop it, and Ctrl-Z would stop the wrapper and not the suite.
#
# The bounds this gives.  A signal during the start ends the wrapper within
# the docker command the start is waiting on (the start HOLDS the signal
# until that command returns; ``scripts/private_cluster.py``), then the
# start's own stop and this one.  A Ctrl-C during pytest ends it as a
# Ctrl-C'd pytest ends in a terminal, then one stop; a TERM or HUP during
# pytest, once pytest has died of it, then one stop.  A TERM and then a KILL
# to the wrapper's PID during the start leave nothing behind: the start,
# passed the TERM, stops what it made whether or not its wrapper is alive to
# wait for it.  ``_child`` is set by the command after each launch and emptied
# by the one after each wait, so a signal landing between the two misses the
# child or names one already reaped; that window is the length of one
# assignment.
_child=""
# shellcheck disable=SC2329 # invoked only through the INT, TERM and HUP traps below, which shellcheck cannot follow
_end_by() {
    if [ -n "$_child" ]; then
        if [ "$1" != INT ]; then
            kill -"$1" "$_child" 2>/dev/null || true
        fi
        wait "$_child" 2>/dev/null || true
    fi
    _teardown_run_container
    trap - EXIT "$1"
    kill -"$1" "$$"
}
# EXIT alone is not enough: an untrapped fatal signal terminates bash WITHOUT
# running the EXIT trap, so the container outlives the run and the next one
# collides with nothing but a leaked cluster.  Ctrl-C (INT, delivered to the
# whole process group) is the common way to strand one; HUP is the same class
# and was missing -- closing the terminal on a running suite leaked both the
# container and the socket directory, while this file's own prose claimed
# every exit path was covered.  Adding the arm was the way to make that
# sentence true.  The traps are armed BEFORE the start, and every one of
# them stops by the token whatever the start did: a start that stopped what
# it made leaves its token nothing to find, and one killed outright leaves
# the trap's stop everything it made, save the socket directory of one
# KILLed during its ``docker run`` (the block above the token says why).
trap '_teardown_run_container' EXIT
trap '_end_by INT' INT
trap '_end_by TERM' TERM
trap '_end_by HUP' HUP

# The start prints the socket directory, then the container's id, once the
# cluster accepts connections on a socket it has checked is there; a start
# that cannot finish names why, and what its own stop left behind, and this
# exits 2, its EXIT trap stopping by the token.  ``--`` ends the start's
# options, so an image beginning with ``-`` is handed over as the image:
# without it ``--help`` printed the usage and exited 0, and this read the
# usage as the socket directory.  It runs as a PROCESS SUBSTITUTION, so it can
# be waited for (the block above the traps says why) while its output still
# comes back, through a pipe read once it has ended: no temporary file for a
# KILL to leave behind.
# shellcheck disable=SC2312 # the start's status is not masked: the wait on its PID ($!) below reads it
exec {_start_out}< <(
    trap - INT
    exec "$_PYTHON" "${_REPO_ROOT}/scripts/private_cluster.py" \
        start -- "$_run_container" "$_token" "$_image"
)
_child=$!
_start_status=0
wait "$_child" || _start_status=$?
_child=""
if [ "$_start_status" -ne 0 ]; then
    exit 2
fi
read -r _run_sockdir <&"$_start_out"
exec {_start_out}<&-
unset _start_out _start_status

# The URLs are the one home's (``private_cluster.py url``): the image's user
# and password, the database, and the socket directory as libpq's host.
# tests/conftest.py rewrites only the PATH of these URLs to reach a
# per-worker database, and urlparse/urlunparse preserve the query, so the
# socket survives that rewrite.
TEST_ADMIN_DATABASE_URL="$("$_PYTHON" "${_REPO_ROOT}/scripts/private_cluster.py" \
    url "$_run_sockdir" postgres)"
TEST_DATABASE_URL="$("$_PYTHON" "${_REPO_ROOT}/scripts/private_cluster.py" \
    url "$_run_sockdir" shekel_test)"
export TEST_ADMIN_DATABASE_URL TEST_DATABASE_URL

echo "[test.sh] private cluster $_run_container on ${_run_sockdir}" \
    "from $_image" >&2

# Local default: skip container-spawning tests (marked ``docker``) so a
# routine run never touches the production Docker daemon that the homelab
# stack + wud/cadvisor/alloy share (see docs/test-harness-isolation.md).
# Opt back in by selecting the marker; DOCKER_HOST is already exported above,
# so on this host the tests/test_deploy conftest guard sees an isolated
# endpoint and the full local opt-in is just:
#   PYTEST_MARKER_EXPR=docker ./scripts/test.sh tests/test_deploy/...
# CI's shards pass ``-m ""`` (``.github/workflows/ci.yml``), so CI still runs
# the full set: an explicit ``-m`` in the caller's arguments takes precedence
# (pytest keeps the last ``-m`` on the command line), and an empty expression
# selects everything.
PYTEST_MARKER_EXPR="${PYTEST_MARKER_EXPR:-not docker}"

# NOT `exec`: the container has to be removed after pytest returns, and an
# exec'd process has no after.  pytest's status is preserved and re-raised so
# the wrapper stays transparent to callers and to CI.
#
# BACKGROUNDED, then waited, with SIGINT at its default: the block above the
# traps says why.  With pytest in the foreground a SIGTERM to the wrapper did
# nothing until pytest finished -- measured 20 s of silence -- which broke
# `timeout` and left the SIGKILL escalation leaking a container AND orphaning
# pytest onto PID 1.
(
    trap - INT
    exec pytest -m "$PYTEST_MARKER_EXPR" "$@"
) &
_child=$!
_pytest_status=0
wait "$_child" || _pytest_status=$?
_child=""
_teardown_run_container
trap - EXIT INT TERM HUP
exit "$_pytest_status"
