#!/usr/bin/env python3
"""A throwaway PostgreSQL cluster of one caller's own: the ONE home of its life.

``scripts/test.sh`` gives every suite run a cluster of its own (``balance:X-br-4``), and
starts and stops it here; the release rehearsal kit's bench
(``tools/rehearsal/bench.py``, step X-cx's K1) starts its own here too, so there is one
``docker run``, one readiness rule and one teardown (the coordinator's K1 leaf-plan ruling,
2026-10-18, point 1), and one way a client tool reaches the cluster from inside its
container (``balance:R-BAL327``).  It is a container on a rootless daemon this module
chooses, and a start refuses any other (:func:`daemon`), reached over a UNIX SOCKET in a
directory under ``XDG_RUNTIME_DIR``, with no network and no port.  ``client`` judges no
daemon: it runs its tool in the container it is named, on the daemon :func:`_endpoint` names,
and the rehearsal kit names only the id its own start handed over (the K1a-3 review's B-2).

A cluster is OWNED BY A TOKEN its caller makes before starting it (the coordinator's ruling on
K1a commit B, 2026-10-19).  The start labels the container with the token as ``docker run``
creates it, and names the socket directory after it, so whatever a start made carries its
token from the instant it exists, and the daemon itself can say which containers are this
caller's.  ``stop`` removes exactly what carries the token and nothing else, so a caller can
stop by its token in EVERY path -- a normal end, a failed or refused start, a signal, a start
killed outright -- and never remove a container another caller made, whatever that container
is named.  The caller still names the container, for the humans reading ``docker ps``;
correctness never reads the name.

Usage::

    python scripts/private_cluster.py token
    python scripts/private_cluster.py daemon
    python scripts/private_cluster.py start <name> <token> <image>
    python scripts/private_cluster.py stop <token>
    python scripts/private_cluster.py url <socket-dir> <database>
    python scripts/private_cluster.py client <container> <tool> [<argument> ...]

``token`` prints a new token.  ``daemon`` prints the daemon a cluster goes to, as the
``DOCKER_HOST`` docker is handed (an empty line for docker's own default), once it is known to
be one a cluster may go to (:func:`daemon`); a caller that runs docker itself before the start
-- the image builder -- hands it on.  ``url`` prints the URL a caller connects to a database of
a started cluster by, from the socket directory ``start`` handed over (:func:`url`).
``client`` runs a PostgreSQL client tool -- ``pg_restore``, ``pg_dump``, ``psql`` -- INSIDE
a started cluster's container, given its id, at the cluster's socket as the image's user
(:func:`_in_container`).  The process BECOMES that ``docker exec``, so what it reads reaches
the tool, and what the tool writes and its exit status are the caller's.  The tools are the
image's, so a caller needs no PostgreSQL tools of its own, and theirs are always the
cluster's major (``balance:R-BAL327``).

``start`` prints the socket directory, then the container's id, one per line, once the
cluster accepts connections there.  A start that fails stops what it made, naming anything
that stop left behind, and writing those two lines is the last thing it can fail at: a start
that cannot write them -- to a pipe nobody reads any more, its caller killed while it
waited, or anywhere else a write fails -- stops the cluster too, since no caller can have
it (K1a commit B3).  One killed outright (KILL) cannot stop anything, which is why the
caller makes the token before starting and stops by it.  That stop leaves nothing, but for
one window: a start KILLed while its ``docker run`` is in flight leaves its socket
directory, empty.  The create finishes after the caller's stop has removed the directory,
and docker makes it again as the mount's source (16 of 16 on the rootless daemon, K1a's
review, 2026-10-19; the container ends by itself under ``--rm``).  ``stop`` removes the
token's container with its anonymous volume, and its socket directory, and names on stderr
anything that survived or could not be asked about; it is safe to run on a cluster that
never started, or twice, which is what lets a caller's signal traps call it whenever they
fire.

From just before it makes anything until it has handed over a cluster that accepts
connections (on the command line: until those two lines are written), or has stopped what
it made, the start HOLDS its caller's signals (INT, TERM,
HUP): it acts on one between docker commands, never inside one, and finishes its own stop
before ending BY that signal, naming what the stop left, so a calling shell sees the signal
and stops too.  Every docker command runs in a session of its own, so a terminal's Ctrl-C
reaches this process and never a docker command it is waiting on: aborting the CLI of
``docker run`` does not abort the create, which the daemon finished after the stop had
looked (the delta review of 668b53c99 + bb2f1492a, MEDIUM-A).

Exit status: 0 done; 1 ``stop`` left something behind, each named; 2 ``start`` could not give
a cluster that accepts connections, or could not hand one over, or refused before making
anything (each reason printed, and anything its own stop left behind), or ``daemon`` refused
the daemon, or ``client`` could not run docker at all, or the command line was wrong.
``client`` otherwise exits as the tool did, or as ``docker exec`` did when it could not run
the tool there (no such container).  A start that cannot write on stderr either, to say
why, has run its stop all the same, and exits 120, CPython's status for an output it cannot
flush as it exits.  A start ended by a held signal ends BY that signal, as Python ends on an
unhandled Ctrl-C; ``main`` ends the process it runs in that way, so a caller in process wants
:func:`start`, which raises :class:`Interrupted` instead.

Environment variables read:
    XDG_RUNTIME_DIR    Where the socket directory is made, and where the rootless daemon's
                       socket is looked for.  Default ``/run/user/<uid>``.
    READINESS_TIMEOUT_SECONDS  Cap on the wait for the cluster to accept connections.
                       Default 15.
    DOCKER_HOST        The daemon.  When unset, the rootless daemon's socket in
                       ``XDG_RUNTIME_DIR`` is chosen if one is there, and handed to every
                       docker command this runs (:func:`daemon`).
    SHEKEL_ALLOW_HOST_DOCKER  ``1`` accepts a daemon that is not rootless.  Shares its
                       spelling with ``tests/test_deploy/conftest.py`` on purpose.
    CI                 Sanctions a daemon that is not rootless, since a CI daemon is a
                       throwaway nothing observes, and ``on-ci`` answers ``test.sh`` from it.
                       Read through a vocabulary, NOT by presence: ``CI=false`` must not
                       sanction anything (:func:`_on_ci`).

Standard library only: ``test.sh`` runs it before any environment is known to hold more.
"""
from __future__ import annotations

import argparse
import errno
import math
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType, TracebackType

# The superuser the image was baked with (``scripts/build_test_db_image.py``'s
# ``_BUILD_USER``); ``test.sh`` builds its connection URLs with the same name and password.
_USER = "shekel_user"
_PASSWORD = "shekel_pass"

# The label ``docker run`` puts on the container, its value the caller's token: how the
# daemon says which containers a caller made.  A ``label=<key>=<value>`` filter matches the
# value EXACTLY (measured 2026-10-19 on the rootless daemon: ``abcd1234`` did not list a
# container labelled ``abcd12345``, and ``abcd`` listed neither).
_TOKEN_LABEL = "shekel.private_cluster.token"

# A token is 128 random bits in lowercase hex: never guessed, never reused, and safe as a
# label value, in a container name and as a path component.
_TOKEN_BYTES = 16
_TOKEN_SHAPE = re.compile(rf"[0-9a-f]{{{2 * _TOKEN_BYTES}}}")

# The socket directory is named after the token, so a stop that holds only the token finds
# it, and a token's directory can belong to no other cluster.
_SOCKET_DIR_PREFIX = "shekel-cluster-"

# Where the image keeps its cluster: outside the base image's declared VOLUME, which
# ``docker commit`` does not capture (``scripts/build_test_db_image.py``'s ``_BAKED_PGDATA``).
_PGDATA = "/pgdata-baked"

# Inside the container, the socket directory is mounted HERE and not over the default
# ``/var/run/postgresql``: the entrypoint chowns that path to a subuid-mapped user and
# sticky-bits it, after which this user cannot unlink the socket that user made --
# measured 5 of 5.  At ``/sockets`` a plain ``rm`` removes it.
_SOCKETS = "/sockets"

# PostgreSQL's socket file, which it makes in the socket directory.
_SOCKET_FILE = ".s.PGSQL.5432"

# A unix socket path is capped by the kernel: ``sun_path`` is 108 bytes with its terminator,
# so 107 usable, and postgres appends ``/`` and the socket file to the directory.  BYTES,
# not characters: a multibyte ``XDG_RUNTIME_DIR`` counted in characters would pass a path
# the kernel then rejects (a review measured the boundary: 93 binds, 94 fails).
_SUN_PATH_BYTES = 107
_SOCKET_DIR_BYTES = _SUN_PATH_BYTES - len(f"/{_SOCKET_FILE}".encode())

# The cluster's settings.  A CENSUS of the deleted shared cluster's, not a selection from
# it: the compose service ``balance:X-br-4`` removed ran with TEN ``-c`` flags and the
# per-run branch was first written with THREE, which was survivable while this path was
# opt-in and is not now that it is the only one.  Each of the ten is carried here or named
# below as not carried, with why.  Three flags here are not from it: the two socket flags
# are this cluster's own mechanics, and the lock table's size has its own paragraph below.
#
# TWO TIMEOUTS are carried because the suite QUOTES one of them as a mechanism.  Docstrings
# and comments across ``tests/`` (``git grep lock_timeout tests`` finds them) state that a
# conflicting DDL dies on, or waits out, the cluster's 10-second ``lock_timeout`` --
# PostgreSQL's default is 0, wait forever, so without the flag that becomes the per-test
# pytest-timeout naming a timeout instead of a lock, with each of them telling the author
# to expect the other signature.  ``statement_timeout`` comes along for the same reason: a
# runaway statement is bounded by the cluster, loudly, rather than by the test runner.
#
# ``idle_in_transaction_session_timeout`` is DELIBERATELY NOT carried (ruling plan#3027,
# 2026-11-06), though the shared cluster set it at 30 seconds.  It bounded no runaway: it
# bounded a connection that was only idle.  A test using the seed fixtures holds a
# transaction open on their session, since their reads leave it open and any later read of
# a fixture object opens it again, so that connection sits idle in it whenever the test
# works elsewhere.  A test 30 seconds past that session's last statement passed and then
# errored at teardown, on a database error that said nothing of time (the merge queue's CI
# run 42235790947 refused PR #673 so), under ``pytest.ini``'s 50-second per-test timeout,
# sized on purpose (ruling R-BI41) and now the one limit on a test's idle time.  A wait on
# a lock an idle connection holds is still bounded, by ``lock_timeout``.  Neither
# production compose file sets it, so as far as this repository says production runs
# PostgreSQL's default, 0.  The rehearsal bench starts its cluster here too, so it loses
# the limit with the suite.
#
# The three ``tcp_keepalives_*`` flags are DELIBERATELY NOT carried, and this is the one
# place that says so: they configure TCP sockets, and this cluster has none, so they would
# be inert rather than wrong.  So would the tenth, ``file_copy_method=clone``, which is not
# carried either: it chooses how ``CREATE DATABASE ... STRATEGY FILE_COPY`` copies a
# template's files (the deleted service paired it with a btrfs PGDATA for reflinks), and
# every clone the suite makes is ``STRATEGY WAL_LOG`` (tests/conftest.py).
#
# THE NON-DURABLE KNOBS ARE WHAT MAKE A CLUSTER PER RUN AFFORDABLE.  Measured: the full
# suite took 753 s in a per-run container against 356 s on the shared cluster -- 2.1x --
# purely because the baked image runs with docker's default durability while the shared
# test-db had ``fsync=off``, ``synchronous_commit=off`` and ``full_page_writes=off``.
# Per-test drop-and-reclone is thousands of DDL cycles, and docs/testing-standards.md
# prices one at 1618 ms with fsync on against 31 ms with it off.  The cluster lives for one
# run, so a crash losing its last transactions costs a re-run and nothing else.
#
# THE LOCK TABLE IS SIZED FOR THE SUITE rather than left at PostgreSQL's default of 64
# (finding BAL-570).  The table is shared by the whole cluster and a lock keeps its slot
# until its transaction ends, so what has to fit is every worker's open transaction at the
# same moment.  Two transactions in the suite lock the whole schema at once, and both grow
# with every migration: ``_empty_the_database`` in
# tests/test_scripts/test_init_database_one_transaction.py drops the five application
# schemas, and the first boot it then runs (``init_fresh_database``) creates them again.
# Five tests run that pair side by side in collection order, so ``-n 12`` can hand all
# five to five workers at once.  Measured 2026-09-30 on dev at 337341152 (image
# shekel-test-db:0aa2eb4287c5, PostgreSQL 18.6), from ``pg_locks`` inside each transaction
# and from a sampler reading the whole cluster's ``pg_locks`` through a run (a sampled peak
# is a floor, not the peak):
#
#   the DROP's transaction              2,829 locks
#   the first boot, at its commit       1,519 locks
#   heaviest single backend, full run   2,827 (that DROP; none heavier seen)
#   cluster peak, a run of that file   13,052 (four DROPs, one boot)
#   cluster peak, full run             10,869 (three DROPs, one boot)
#   one transaction can hold, at 64    14,912 before "out of shared memory"
#   one transaction can hold, at 256   55,760
#
# Five of those DROPs in flight are 14,145 of the 14,912 before any other worker holds a
# lock.  That is what the full run that surfaced BAL-570 hit: four of the five failed on
# the DROP with "out of shared memory ... increase max_locks_per_transaction".  At 256, all
# twelve of pytest.ini's workers inside a 2,829-lock transaction at the same moment would
# hold 33,948, so the heaviest transaction can grow to ~4,600 locks (64% more) before even
# that schedule overflows.  It costs 11 MB of shared memory (161 MB against 150).  On
# PostgreSQL 18 the same setting also sizes each backend's fast-path slots (64 to 256),
# which only moves weak locks off the shared table; the DROP's AccessExclusive locks never
# take that path.
#
# This sizes the suite's CONCURRENCY, not the application's need.  Neither production
# compose file sets it, so as far as this repository says production runs 64, where one
# transaction tops out near the 14,912 above (the first boot holds 1,519).  At 256 the
# suite no longer fails when one transaction outgrows that ceiling, so it is no alarm for
# one.
_SETTINGS = (
    "lock_timeout=10000",
    "statement_timeout=30000",
    "fsync=off",
    "synchronous_commit=off",
    "full_page_writes=off",
    "max_locks_per_transaction=256",
    # No TCP listener at all, even inside the container's own namespace.
    "listen_addresses=",
    f"unix_socket_directories={_SOCKETS}",
)

_DEFAULT_READINESS_SECONDS = 15
# How long one docker command may take before the daemon is taken to be wedged.
_DOCKER_TIMEOUT_SECONDS = 60
# The signals by which a caller asks a start to end: Ctrl-C, and what supervisors and a
# closing terminal send.  KILL cannot be held, which is why the caller holds the token.
_CALLERS_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

# The rootless daemon's socket, in ``XDG_RUNTIME_DIR``, where ``dockerd-rootless.sh`` makes it.
_ROOTLESS_SOCKET = "docker.sock"
# The values of ``CI`` that mean this run is on CI (:func:`_on_ci`).
_CI_VALUES = frozenset({"1", "true", "TRUE", "True", "yes", "on"})
# The operator's own sanction, spelled as ``tests/test_deploy/conftest.py`` spells it.
_HOST_DOCKER_OVERRIDE = "SHEKEL_ALLOW_HOST_DOCKER"


class ClusterError(RuntimeError):
    """The cluster could not be started, or is not usable; the message says which and why."""


class Interrupted(KeyboardInterrupt):
    """A caller's signal ended a start after it began making the cluster.  The start has
    stopped what it made; :attr:`signum` is the signal, and the arguments are what the stop
    could not remove, one sentence each.

    A KeyboardInterrupt, and so not an ``Exception``, for the caller that runs :func:`start`
    in process: this module's own cases, under a pytest that stops its session on a Ctrl-C
    and records an ``Exception`` as one failed case before going on to the next.  As an
    ``Exception`` it would turn that caller's Ctrl-C into a failure it carries on past, which
    is the defect :func:`main` ends by the signal to prevent in a calling shell.  :func:`main`
    itself catches it by name and needs neither base.
    """

    def __init__(self, signum: int, survived: list[str]) -> None:
        super().__init__(*survived)
        self.signum = signum


@dataclass
class _HeldSignals:
    """The caller's signals, HELD while a start makes its cluster and while it stops it.

    A handler that raises would raise inside whatever docker command the start is waiting
    on, and ``subprocess.run`` answers an exception by killing that command: for ``docker
    run`` the daemon finishes the create anyway, after the start's stop has looked (MEDIUM-A).
    So each signal is only RECORDED, and the start acts on it between docker commands
    (:meth:`act`).  A recording handler holds in any caller, where blocking the signals
    would not: a signal mask is per thread, and a caller with a second thread (xdist's) has a
    process-directed signal delivered there instead.  Handlers run in the main thread only,
    so :func:`start` must run there (``signal.signal`` refuses any other).

    On the way out the caller's own handlers are restored, and a signal recorded after the
    last :meth:`act` is raised again under them, so none is lost.
    """

    signals: list[int] = field(default_factory=list)
    _before: dict[int, signal.Handlers | object] = field(default_factory=dict)
    _acted: bool = False

    def __enter__(self) -> _HeldSignals:
        """Hold the caller's signals, keeping its handlers to restore."""
        for signum in _CALLERS_SIGNALS:
            self._before[signum] = signal.signal(signum, self._record)
        return self

    def _record(self, signum: int, _frame: FrameType | None) -> None:
        """The handler: note the signal, and carry on with what was interrupted."""
        self.signals.append(signum)

    def act(self) -> None:
        """End the start here if its caller has sent a signal: raise KeyboardInterrupt, which
        :func:`start` answers by stopping what it made.

        Raises:
            KeyboardInterrupt: when a signal is held.
        """
        if self.signals:
            self._acted = True
            raise KeyboardInterrupt

    def __exit__(self, kind: type[BaseException] | None, value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        """Restore the caller's handlers, and raise again under them a signal recorded after
        the last :meth:`act` of a start that is handing its cluster over."""
        for signum, handler in self._before.items():
            signal.signal(signum, handler)
        if self.signals and not self._acted and kind is None:
            signal.raise_signal(self.signals[0])


@dataclass(frozen=True)
class Cluster:
    """A started cluster: its container's id, and the socket directory it is reached by."""

    container: str
    socket_dir: Path


def new_token() -> str:
    """A new token, for a caller to own one cluster by (the module says how)."""
    return secrets.token_hex(_TOKEN_BYTES)


def socket_dir(token: str) -> Path:
    """The socket directory of the cluster owned by ``token``: a directory named after it
    under ``XDG_RUNTIME_DIR`` (``/run/user/<uid>`` when unset or empty), which is 0700, so
    the socket stays private though its own directory must be open to the container.

    Raises:
        ClusterError: when ``token`` is not one :func:`new_token` makes.  It becomes a path
            component that :func:`stop` removes, so ``..`` or a ``/`` in it would point that
            removal elsewhere.
    """
    if not _TOKEN_SHAPE.fullmatch(token):
        raise ClusterError(f"{token!r} is not a cluster token: one is {2 * _TOKEN_BYTES} "
                           "lowercase hex digits ('private_cluster.py token' makes one)")
    return _runtime_dir() / f"{_SOCKET_DIR_PREFIX}{token}"


def _runtime_dir() -> Path:
    """``XDG_RUNTIME_DIR``, or ``/run/user/<uid>`` when it is unset or empty: where the socket
    directory is made and the rootless daemon's socket is looked for."""
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def _endpoint() -> str:
    """The daemon every docker command here is handed, as ``DOCKER_HOST``: that variable when
    it is set and not empty, else the rootless daemon's socket in :func:`_runtime_dir` when
    one is there, else ``""``, docker's own default.  An absent rootless socket is no fallback
    to anything: :func:`daemon` then refuses what docker's default turns out to be."""
    given = os.environ.get("DOCKER_HOST", "")
    if given:
        return given
    rootless = _runtime_dir() / _ROOTLESS_SOCKET
    return f"unix://{rootless}" if rootless.is_socket() else ""


def _on_ci() -> bool:
    """Whether this run is on CI.

    ``CI`` is read through a VOCABULARY, not by presence.  It sanctions a container per run on
    the daemon that runs PRODUCTION, the heaviest consequence here (:func:`_sanctioned`), and a
    presence test made ``CI=false`` and ``CI=0`` sanction it.  An unrecognised value is not CI:
    it cannot be refused as an operator's typo would be, because CI sets it, not the operator.
    ``test.sh`` asks this too (``on-ci``), whether CI's install has already built the venv,
    rather than spelling the vocabulary a second time.
    """
    return os.environ.get("CI", "") in _CI_VALUES


def _sanctioned() -> bool:
    """Whether a container per run may go to a daemon that is not rootless: on CI, whose daemon
    is a throwaway nothing observes, or when the operator sets ``SHEKEL_ALLOW_HOST_DOCKER=1``
    to accept the churn.
    """
    return _on_ci() or os.environ.get(_HOST_DOCKER_OVERRIDE) == "1"


def daemon() -> str:
    """The daemon a cluster goes to (:func:`_endpoint`, ``""`` for docker's own default), once
    it is known to be rootless or a container per run on it is sanctioned (:func:`_sanctioned`).

    THE DAEMON IS NOT THE SYSTEM ONE.  The system daemon on this host runs the production
    database, and the homelab wud/cadvisor/alloy stack watches every container on it: exactly
    the churn ``docs/test-harness-isolation.md`` exists to stop.  So a daemon that is not
    rootless is REFUSED rather than quietly used, and fail-closed: an absent rootless daemon
    stops the start with instructions, it never falls back.

    The daemon is asked WHAT IT IS rather than judged by its address.  "Rootless" is a property
    of a daemon; a path is a guess about one, and the guess has a hole: a ``tcp://`` endpoint
    aimed at a socket-proxy container reads as isolated while being the production daemon at
    another address.  Whether it could be asked is kept apart from its answer, so a stopped
    daemon is never called one that is not rootless: its socket outlives it, and may well be
    the rootless one's.

    THE RULE LIVES IN THE ACT THAT STARTS A CLUSTER, so no caller can start one without it (the
    coordinator's ruling on K1a-2's review, B1, 2026-10-19).  ``test.sh`` held it alone until
    then, and the rehearsal kit's bench, which chose no daemon, put a cluster holding a
    production dump on the system daemon whenever ``DOCKER_HOST`` was unset.  :func:`start`
    asks it before it makes anything.  A caller that runs docker itself before the start --
    the image builder, which both callers run first -- asks it first too (``private_cluster.py
    daemon``) and hands its answer on, so a refused daemon gets no image either, and the image
    is baked on the daemon the cluster runs on.

    Raises:
        ClusterError: when the daemon cannot be asked what it is, or is not rootless, and no
            sanction applies.
    """
    endpoint = _endpoint()
    try:
        asked = _docker("info", "--format", "{{.SecurityOptions}}")
        answer = None if asked.returncode else asked.stdout
    except ClusterError:
        answer = None
    if (answer is not None and "rootless" in answer) or _sanctioned():
        return endpoint
    where = endpoint or "the default socket"
    if answer is None:
        raise ClusterError(f"could not ask {where} what it is, so whether a cluster would land "
                           "on the production daemon is UNKNOWN, and none is started.  Start "
                           "the rootless daemon with 'systemctl --user start docker.service' "
                           "(see docs/test-harness-isolation.md).")
    raise ClusterError(f"refuses this daemon: {where} is not rootless, so a cluster would land "
                       "on the daemon that runs production.  Start the rootless daemon with "
                       "'systemctl --user start docker.service' (see "
                       f"docs/test-harness-isolation.md), or set {_HOST_DOCKER_OVERRIDE}=1 to "
                       "accept the churn.")


def url(directory: Path | str, database: str) -> str:
    """The URL a caller connects to ``database`` of a started cluster by: the image's user
    and password, and the cluster's socket ``directory`` (what ``start`` hands over) as
    libpq's ``host``, which libpq reads as a socket directory because it begins with ``/``.
    The one home of how a caller reaches the cluster (the coordinator's fork B on K1a-2,
    2026-10-19): ``test.sh`` builds its two URLs here, and the rehearsal kit its own.
    SQLAlchemy passes the query string through to libpq untouched, so one URL serves both.
    """
    return f"postgresql://{_USER}:{_PASSWORD}@/{database}?host={directory}"


def _in_container(container: str, tool: str, *args: str, attach_stdin: bool = False
                  ) -> list[str]:
    """The docker arguments that run the PostgreSQL client ``tool``, given ``args``, inside
    ``container``, at the cluster's socket as the image's user: the ONE home of that spelling
    from inside the container (``balance:R-BAL327``), which the readiness probe and
    ``client`` both run.

    The socket and the user go in as libpq's ``PGHOST`` and ``PGUSER``, so the tool's own
    arguments say only what it is to do, and name a database by its name.  ``attach_stdin``
    hands the tool docker's standard input (``-i``): ``client`` does, for a dump the tool is
    to read; the probe reads nothing, and attached, docker would read whatever its caller's
    standard input holds, a terminal's keystrokes included.
    """
    return ["exec", *(["-i"] if attach_stdin else []), "-e", f"PGHOST={_SOCKETS}",
            "-e", f"PGUSER={_USER}", container, tool, *args]


def _docker_env() -> dict[str, str]:
    """The environment a docker command runs in: this process's, with ``DOCKER_HOST`` the
    daemon :func:`_endpoint` names when it names one."""
    endpoint = _endpoint()
    return {**os.environ, "DOCKER_HOST": endpoint} if endpoint else dict(os.environ)


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    """One docker command, its output captured; a daemon that does not answer within
    :data:`_DOCKER_TIMEOUT_SECONDS` is reported as one, where a shell waited forever.

    It runs in a SESSION OF ITS OWN, so a terminal's Ctrl-C, which goes to the terminal's
    foreground process group, reaches the caller and never the docker command: the CLI of
    ``docker run`` aborted that way leaves the daemon to finish the create after whatever
    stop the caller then runs has looked.  Measured with the delta review of bb2f1492a's race
    probe on the rootless daemon, 12 rounds a shape: a container left in 4 of 12 rounds of a
    bare start and 3 of 12 in test.sh's shape on 731b2aa08, and in 0 of 12 of each once the
    CLI ran apart and the start held its caller's signals (2026-10-19).

    Its output is decoded with ``errors="replace"``: a byte it prints that is not UTF-8
    becomes U+FFFD in the message that quotes it, where a strict decode raised a
    UnicodeDecodeError that is no ClusterError, so a failed start skipped its own stop and
    ended in a traceback.  Nothing else reads the text for more than that: a container's id
    is hex.

    It is handed the daemon :func:`_endpoint` names as it runs, whether or not its caller
    exported one.  The daemon is not judged again mid-start: :func:`daemon` judged the one
    :func:`_endpoint` named at the start's door, so a rootless socket that vanishes between
    the start's ``docker info`` and its ``docker run`` sends the run to docker's default (the
    K1a-3 review's B-1, a noted follow-up).

    Raises:
        ClusterError: when docker cannot be run or does not answer in time.
    """
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True,
                              errors="replace", check=False, start_new_session=True,
                              timeout=_DOCKER_TIMEOUT_SECONDS, env=_docker_env())
    except subprocess.TimeoutExpired as exc:
        raise ClusterError(f"docker {args[0]} did not answer within "
                           f"{_DOCKER_TIMEOUT_SECONDS}s; the daemon may be wedged") from exc
    except OSError as exc:
        raise ClusterError(f"docker cannot be run: {exc}") from exc


def _labelled(token: str) -> list[str]:
    """The ids of every container carrying ``token``'s label, running or not.  A daemon
    that cannot be asked is an error, never an empty answer, so a teardown reading it never
    calls an unreachable daemon clean.

    Raises:
        ClusterError: when docker cannot be asked.
    """
    asked = _docker("ps", "--all", "--quiet", "--filter", f"label={_TOKEN_LABEL}={token}")
    if asked.returncode:
        raise ClusterError(f"docker could not be asked which containers carry {token}: "
                           f"{(asked.stderr or asked.stdout).strip()}")
    return asked.stdout.split()


def _readiness_seconds() -> float:
    """The cap on the readiness wait, from ``READINESS_TIMEOUT_SECONDS``.

    Raises:
        ClusterError: when the variable is set to anything but a finite, non-negative number
            of seconds (``nan`` and ``inf`` would make the wait endless).
    """
    given = os.environ.get("READINESS_TIMEOUT_SECONDS", "")
    if not given:
        return _DEFAULT_READINESS_SECONDS
    try:
        seconds = float(given)
    except ValueError as exc:
        raise ClusterError(f"READINESS_TIMEOUT_SECONDS={given!r} is not a number of "
                           "seconds") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ClusterError(f"READINESS_TIMEOUT_SECONDS={given!r} is not a finite, "
                           "non-negative number of seconds")
    return seconds


def _refuse_a_socket_dir_docker_cannot_bind(directory: Path) -> None:
    """Refuse a socket directory the cluster's socket cannot be bound in and reached at, by
    the one rule on its BYTES (``os.fsencode``, as the kernel and docker are handed them):
    they are UTF-8, and they fit the kernel's budget for a socket path.  Refused at the door,
    where nothing has been made, rather than surfacing later as a connection error from deep
    inside the caller or a readiness wait that runs out its cap.

    UTF-8 first, so the length refusal's message never holds a byte that is not UTF-8, and
    this one quotes each such byte escaped.  A byte that is not UTF-8 reaches the daemon as
    U+FFFD, so it mounts ANOTHER directory and the socket never appears in this one:
    measured twice on the rootless daemon (the reviews of 668b53c99 and bb2f1492a), a real
    start waited out its whole cap, exited 2 and left the replaced directory, which its stop
    could not name.

    Raises:
        ClusterError: when the directory is not UTF-8, or is too long.
    """
    encoded = os.fsencode(directory)
    try:
        encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClusterError(f"socket directory {encoded.decode('utf-8', 'backslashreplace')} "
                           "is not UTF-8, so docker would mount another directory in its "
                           "place.  Set XDG_RUNTIME_DIR to a path that is.") from exc
    if len(encoded) > _SOCKET_DIR_BYTES:
        raise ClusterError(f"socket directory {directory} is too long; a unix socket path "
                           f"cannot exceed {_SUN_PATH_BYTES} bytes.  Set XDG_RUNTIME_DIR to "
                           "something shorter.")


def _make_socket_dir(directory: Path) -> None:
    """Make the socket directory EXCLUSIVELY, open to the container's postgres user.

    Anything already at the path -- a directory, a file, a symlink -- is refused, never
    taken over: the path is the token's, and a token is new, so whatever is there was made
    by no start of this cluster, and opening a symlink's target to the container would hand
    it a directory elsewhere.  0777 because rootless docker maps the container's postgres
    user into the subuid range, so it cannot write a directory owned by this user.  The
    socket is still private: ``XDG_RUNTIME_DIR`` itself is 0700.

    Raises:
        ClusterError: when anything is at the path, or the directory cannot be made.
    """
    try:
        directory.mkdir(parents=True)
        directory.chmod(0o777)
    except OSError as exc:
        raise ClusterError(f"socket directory {directory} cannot be made: {exc}") from exc


def _run_container(name: str, token: str, image: str, directory: Path) -> str:
    """Start the container ``name`` from ``image``, labelled with ``token`` as it is
    created; its id.

    NO PUBLISHED PORT.  The cluster serves one process on this machine, so a port buys
    nothing and costs a whole failure class: on a rootless daemon ``dockerd`` picks the port
    inside the container's network namespace and rootlesskit then binds that number on the
    HOST, where a live outbound connection often already owns it.  ``ip_local_port_range``
    is 32768-60999, byte-identical to docker's publish band, and this host routinely holds
    thousands of sockets in it.  Measured: 5 of 12 container starts failed, and 3 of 12
    still failed with no port ever recycled, so it is not a release race -- it is two
    allocators in two namespaces sharing one number space.  The root daemon does not hit it
    (0 of 8), which is why this only surfaced on moving off it.  A socket has no allocator
    and no shared namespace: the cluster's directory carries its own name, which its caller
    made unique.

    ``--network=none`` follows from having no port, which also takes the cluster off the
    experimental gvisor-tap-vsock driver the rootless daemon falls back to when slirp4netns
    is absent.  ``--rm`` removes a container whose postgres exits; :func:`stop` removes the
    rest.  The label is given here, to the create itself, so no instant exists in which the
    container is this caller's and does not say so.  A name another container already holds
    makes docker refuse the run ("Conflict"), and the start's stop then removes nothing of
    that container, which does not carry this token.

    Raises:
        ClusterError: when docker refuses to start it.
    """
    started = _docker(
        "run", "-d", "--rm", "--name", name, "--label", f"{_TOKEN_LABEL}={token}",
        "--network=none",
        "-e", f"POSTGRES_USER={_USER}", "-e", f"POSTGRES_PASSWORD={_PASSWORD}",
        "-e", "POSTGRES_DB=postgres", "-e", f"PGDATA={_PGDATA}",
        "-v", f"{directory}:{_SOCKETS}",
        image,
        *(arg for setting in _SETTINGS for arg in ("-c", setting)),
    )
    if started.returncode:
        raise ClusterError(f"docker could not start {name} from {image}: "
                           f"{(started.stderr or started.stdout).strip()}")
    return started.stdout.strip()


def _wait_ready(name: str, container: str, directory: Path, seconds: float,
                held: _HeldSignals) -> None:
    """Block until the cluster accepts connections on its socket, and the socket is on this
    side of the mount; a signal its caller sent ends the wait after the probe it landed in.
    The probe asks the container by its id, which is this start's; ``name`` is for the
    messages.

    Asking over the socket is safe for an image that ships a populated PGDATA, as the
    baked test image does: the entrypoint skips initdb, so the window in which its
    temporary server answers on a socket before the real server exists never opens.
    ``scripts/build_test_db_image.py``, whose bake container DOES run initdb, is the one
    that cannot trust a socket.  Readiness and a usable socket are two facts: a container
    can answer ``pg_isready`` and still have left no socket here, and a URL naming a socket
    that does not exist would fail deep inside its caller, pointing at the database.

    Raises:
        ClusterError: when it is not ready within ``seconds``, or left no socket.
        KeyboardInterrupt: when its caller has sent a signal (:meth:`_HeldSignals.act`).
    """
    deadline = time.monotonic() + seconds
    while True:
        probed = _docker(*_in_container(container, "pg_isready", "-q"))
        held.act()
        if not probed.returncode:
            break
        if time.monotonic() >= deadline:
            raise ClusterError(f"{name} did not accept connections within {seconds:g}s")
        time.sleep(0.1)
    socket_file = directory / _SOCKET_FILE
    if not (socket_file.exists() and stat.S_ISSOCK(socket_file.stat().st_mode)):
        raise ClusterError(f"{name} reported ready but left no socket at {socket_file}")


def start(name: str, token: str, image: str, *,
          hand_over: Callable[[Cluster], None] | None = None) -> Cluster:
    """Start a cluster owned by ``token``, its container named ``name``, from ``image``, and
    wait until it accepts connections.

    ``image`` ships a populated PGDATA at :data:`_PGDATA`, as the baked test image does
    (:func:`_wait_ready` says why readiness depends on it).  Nothing is made until the
    readiness cap, the token and the socket path's bytes are known good and the daemon is one
    a cluster may go to (:func:`daemon`).  From just before
    the socket directory is made -- the first thing a start makes, the ``docker run`` the
    second -- until the cluster is handed over or stopped, the caller's signals are HELD
    (:class:`_HeldSignals`).  A start that fails after that stops what carries its token,
    and its error names anything that stop left behind.  One ended by a signal stops it too,
    and raises :class:`Interrupted` carrying the signal and what the stop could not remove;
    the signal wins over a failure it met, since the caller asked the start to end.  One
    killed outright (KILL) stops nothing, so its caller, which holds the token, stops by it.

    ``hand_over``, when given, is the start's LAST held step: it is called with the cluster
    once it accepts connections, and a :class:`ClusterError` it raises fails the start like
    any other failure, so the start stops what it made with the caller's signals still held.
    :func:`main` hands the cluster over by writing it out (:func:`_hand_over`), the one step
    that can fail with nobody left to stop the cluster; a caller in process is handed it by
    this returning, and passes none.

    Once it is handed over, the cluster is its caller's: a signal recorded after the start's
    last look is raised again under the caller's own handlers, so it reaches the caller,
    which holds the token.  A signal that lands inside the stop of a start that FAILED
    ends it BY that signal once the stop has finished, as one that landed before the
    failure would: a TERM there turns exit 2 into death by TERM.  One that lands inside
    the stop of a start a signal already ended changes nothing; the first signal ends it.

    Raises:
        ClusterError: when the start is refused or fails, naming why and what its stop left.
        Interrupted: when a signal ends the start once it has begun making the cluster.
    """
    seconds = _readiness_seconds()
    directory = socket_dir(token)
    _refuse_a_socket_dir_docker_cannot_bind(directory)
    daemon()
    with _HeldSignals() as held:
        try:
            _make_socket_dir(directory)
            held.act()
            container = _run_container(name, token, image, directory)
            held.act()
            _wait_ready(name, container, directory, seconds, held)
            cluster = Cluster(container, directory)
            if hand_over is not None:
                hand_over(cluster)
        except KeyboardInterrupt as interrupted:
            # Raised by ``held.act()``, or in process by the caller's own Ctrl-C handler.
            raise Interrupted(held.signals[0] if held.signals else signal.SIGINT,
                              stop(token)) from interrupted
        except ClusterError as failed:
            survived = stop(token)
            if held.signals:
                raise Interrupted(held.signals[0], survived) from failed
            if survived:
                raise ClusterError(f"{failed}.  Stopping it left something behind: "
                                   + " ".join(survived)) from failed
            raise
    return cluster


def stop(token: str) -> list[str]:
    """Remove what carries ``token``: its container, with its anonymous volume, and its
    socket directory; one sentence per thing that survived, or that could not be asked about.

    It NEVER removes what does not carry the token: the container is found by its LABEL and
    removed by its id, never by a name, so a container another caller made is left alone
    whatever it is named -- the guarantee the coordinator's MEDIUM-1 asked for, now held by
    the daemon's own record of who made what rather than by a caller deciding when not to
    stop (the ruling on K1a commit B, 2026-10-19).

    ``-v`` removes the ANONYMOUS VOLUME too: the baked image inherits the base's ``VOLUME
    /var/lib/postgresql`` though PGDATA lives elsewhere, and plain ``docker rm -f`` left one
    behind per run -- measured 101 of 112 volumes on the development daemon dangling, the
    newest in one-second pairs matching a run's own verify and start.  Each holds ~0 B, so
    the cost is unbounded metadata on a daemon the homelab stack watches, not disk.  A
    survivor is NAMED, never discarded: a teardown that cannot say it failed left a wedged
    or unreachable daemon's container running while its caller exited clean, which is why
    the containers are asked for again after the removal, by :func:`_labelled`, which tells
    "none" from "cannot be asked".  The socket directory sits on a tmpfs, so a leak is
    bounded only by the next reboot; a plain removal suffices because it is mounted at
    ``/sockets`` (see :data:`_SOCKETS`).  A symlink at its path is named, never followed.

    Raises:
        ClusterError: when ``token`` is not a token (:func:`socket_dir`).
    """
    directory = socket_dir(token)
    survived = []
    try:
        made = _labelled(token)
        if made:
            _docker("rm", "-fv", *made)
            survived.extend(f"container {container} of cluster {token} survived teardown "
                            f"-- remove it by hand ('docker rm -fv {container}')."
                            for container in _labelled(token))
    except ClusterError as exc:
        survived.append(f"the container of cluster {token} may have survived teardown: "
                        f"{exc}")
    shutil.rmtree(directory, ignore_errors=True)
    if directory.exists(follow_symlinks=False):
        survived.append(f"{directory} survived teardown.")
    return survived


def _warn(survived: list[str]) -> None:
    """Name on stderr each thing a stop left behind."""
    for line in survived:
        print(f"[private_cluster] WARNING: {line}", file=sys.stderr)


def _hand_over(cluster: Cluster) -> None:
    """Hand ``cluster`` over on file descriptor 1: its socket directory, then its container's
    id, one per line, as the start's last held step (:func:`start`).

    The lines go out as BYTES, straight to the descriptor and never through ``sys.stdout``,
    so that every way the write can fail is an OSError raised HERE, where the start can still
    stop the cluster (the coordinator's ruling (a') on commit B3).  Through Python's text
    layer, three ways were not.  The lines sat in its buffer until the interpreter's exit, so
    a write to a caller gone by then -- one KILLed while it waited -- failed after the start
    had returned: a KILL to ``test.sh`` alone during ``docker run`` left the container and
    its socket directory, three of three on 184a0fc67 (T10, measured 2026-10-19).
    ``sys.stdout`` is None when descriptor 1 was closed at launch, and flushing it raised an
    AttributeError (measured on this commit's first draft: a traceback, exit 1, the cluster
    left).  And a text stream must spell the path in ITS encoding, which need not hold it.
    Here the path is encoded by ``os.fsencode``, as it came from the filesystem, and a
    buffered binary writer writes every byte or raises, flushed and closed here (closing it
    leaves the descriptor open).  So a pipe nobody reads is EPIPE, a full disk ENOSPC, and no
    descriptor 1 at all EBADF, each measured, and nothing is left for the exit to flush.  ANY
    failed write means the caller cannot have the cluster, so the start fails and stops it.

    Raises:
        ClusterError: when the lines cannot be written, naming the write's own error.
    """
    lines = b"".join(os.fsencode(line) + b"\n" for line in (cluster.socket_dir, cluster.container))
    try:
        with open(1, "wb", closefd=False) as out:
            out.write(lines)
            out.flush()
    except OSError as exc:
        raise ClusterError(f"container {cluster.container} accepts connections but could not "
                           f"be handed over: {errno.errorcode.get(exc.errno, exc.errno)} "
                           f"({exc.strerror})") from exc


def _end_by(signum: int) -> int:
    """End this process BY ``signum``, as Python ends on a Ctrl-C nothing handled, so a
    calling shell sees the signal and stops too: an exit status of 130 tells a shell the
    child dealt with the Ctrl-C itself, and the shell carries on with its next command.

    The default disposition skips the interpreter's own flush, and nothing needs it here: no
    arm that ends this way has printed to ``sys.stdout`` (the handover writes and flushes its
    own bytes, :func:`_hand_over`), and stderr is line-buffered, so each warning is written as
    it is printed.  The status is returned only
    should the signal not end the process, which is CPython's own fallback in
    ``exit_sigint`` (128 + the signal).
    """
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    return 128 + signum


def _start(args: argparse.Namespace) -> int:
    """``start``, as the module says; its exit status."""
    try:
        start(args.name, args.token, args.image, hand_over=_hand_over)
    except ClusterError as exc:
        print(f"[private_cluster] {exc}", file=sys.stderr)
        return 2
    except Interrupted as interrupted:
        _warn(list(interrupted.args))
        return _end_by(interrupted.signum)
    return 0


def _stop(args: argparse.Namespace) -> int:
    """``stop``, as the module says; its exit status."""
    try:
        survived = stop(args.token)
    except ClusterError as exc:
        print(f"[private_cluster] {exc}", file=sys.stderr)
        return 2
    _warn(survived)
    return 1 if survived else 0


def _client(args: argparse.Namespace) -> int:
    """``client``, as the module says: this process BECOMES the ``docker exec`` that runs the
    tool, handed the daemon :func:`_endpoint` names, so a dump streams to the tool and its
    output back with no copy in between, and the tool's exit status is this process's.  It
    returns only when docker cannot be run at all, naming why: exit 2."""
    argv = ["docker", *_in_container(args.container, args.tool, *args.arguments,
                                     attach_stdin=True)]
    try:
        os.execvpe("docker", argv, _docker_env())
    except OSError as exc:
        # The path ``execvpe`` tried is BYTES, which its own message would quote as ``b'...'``.
        print(f"[private_cluster] docker cannot be run: {exc.strerror}: "
              f"{os.fsdecode(exc.filename)}", file=sys.stderr)
    return 2


def _daemon_command() -> int:
    """Print the daemon a cluster goes to, or name its refusal; the exit status."""
    try:
        print(daemon())
    except ClusterError as exc:
        print(f"[private_cluster] {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """Make a token, start or stop a cluster, or run a client tool in one, as the module says;
    its exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("token", help="print a new token, to own a cluster by")
    commands.add_parser("daemon", help="print the daemon a cluster goes to, refusing one "
                        "that is not rootless")
    commands.add_parser("on-ci", help="exit 0 on CI, 1 anywhere else")
    starting = commands.add_parser("start", help="start a cluster owned by a token; print "
                                   "its socket directory, then its container id")
    starting.add_argument("name")
    starting.add_argument("token")
    starting.add_argument("image")
    stopping = commands.add_parser("stop", help="stop what carries a token, naming what "
                                   "survived")
    stopping.add_argument("token")
    connecting = commands.add_parser("url", help="print the URL of a database on a started "
                                     "cluster, from its socket directory")
    connecting.add_argument("socket_dir")
    connecting.add_argument("database")
    entering = commands.add_parser("client", help="run a PostgreSQL client tool inside a "
                                   "started cluster's container, at its socket")
    entering.add_argument("container")
    entering.add_argument("tool")
    entering.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "token":
        print(new_token())
        return 0
    if args.command == "url":
        print(url(args.socket_dir, args.database))
        return 0
    if args.command == "daemon":
        return _daemon_command()
    if args.command == "on-ci":
        return 0 if _on_ci() else 1
    if args.command == "client":
        return _client(args)
    return _start(args) if args.command == "start" else _stop(args)


if __name__ == "__main__":
    sys.exit(main())
