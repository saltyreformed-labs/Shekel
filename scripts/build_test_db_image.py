#!/usr/bin/env python3
"""Bake the test template database into a tagged docker image.

The test suite clones a per-worker database from ``shekel_test_template``
for every test.  That template was built by :mod:`scripts.build_test_template`
against a long-lived shared container, which is why the suite carried a slot
lock, a restart flag, a live-backend probe, ``TEST_DB_PREFIX`` and
``TEST_TEMPLATE_DATABASE``: one postmaster, many worktrees.  Each RUN now has
its own container and every one of those has been deleted
(``balance:X-br-4``).

This script builds the artifact that makes that affordable: an image whose
PGDATA already contains the template, so starting a run costs a container
boot rather than a migration replay.

THREE THINGS HERE WERE MEASURED, NOT ASSUMED, and each would have produced a
silently wrong image:

1. ``PGDATA`` MUST SIT OUTSIDE THE IMAGE'S DECLARED VOLUME.  The upstream
   image declares ``VOLUME /var/lib/postgresql`` and defaults ``PGDATA`` to
   ``/var/lib/postgresql/18/docker``, inside it -- and ``docker commit`` does
   not capture volume contents.  Committing a container built the default way
   yields an image with an EMPTY cluster and no error anywhere.  Measured: a
   marker database created before the commit was simply gone afterwards.  So
   the build and every run set ``PGDATA`` to :data:`_BAKED_PGDATA`.

2. THE CLUSTER MUST BE SHUT DOWN CLEANLY BEFORE THE COMMIT.  Relying on
   ``docker stop`` alone lost the template once and kept it once -- a race,
   measured both ways.  ``pg_ctl -m fast -w`` is deterministic and also cuts
   cold start from 5,896 ms to 292 ms, because a cleanly-stopped cluster does
   not replay WAL on every boot.

   Do NOT re-derive the reason as "SIGTERM means a smart shutdown".  An
   earlier version of this paragraph said exactly that and it is wrong for
   this image: ``docker image inspect`` reports ``StopSignal=SIGINT``, which
   is PostgreSQL's FAST shutdown.  The likelier cause of the lost template is
   the initdb window described at :func:`_wait_ready` -- a shutdown issued
   while the entrypoint's temporary server is up stops that server rather
   than the real one.  The remedy is unchanged; the explanation was not.

3. THE CACHE KEY IS AN OPTIMISATION, NOT THE CORRECTNESS ARGUMENT.  The
   template is NOT a function of the migrations alone:
   :func:`scripts.build_test_template._populate_template` re-applies in-code
   trigger definitions AFTER ``alembic upgrade``, deliberately, so the latest
   definition wins over the migration-frozen one.  Editing
   ``app/audit_infrastructure.py``, ``app/posting_infrastructure.py`` or
   ``app/opening_infrastructure/`` changes the template without touching
   ``migrations/`` at all.  A key that hashed only the migrations would go
   stale silently, which is the one failure mode that would corrupt results
   rather than merely slow them.  So the key covers every input that runs
   during the build (:data:`KEY_INPUTS`), and -- more importantly -- the
   image is VERIFIED after it is committed, by starting it and re-running the
   builder's own assertions.  A wrong key then costs a rebuild or a loud
   refusal, never a silent wrong-schema run.

Usage::

    python scripts/build_test_db_image.py            # build if absent
    python scripts/build_test_db_image.py --force    # rebuild regardless
    python scripts/build_test_db_image.py --print-tag  # just resolve the tag

Exit codes: 0 on success (image present and verified), 1 on a build or
verification failure, 2 on a usage or environment problem.

A build, never a cache hit or ``--print-tag``, ends by pruning stale images of earlier keys
(:mod:`scripts.prune_test_db_images`, imported only then). At run time nothing the prune meets
fails the build; a prune module that cannot import fails that one invocation, after the bake.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
# ``scripts`` and ``app`` resolve only from the repo root, which is not on the
# path when this runs as ``python scripts/build_test_db_image.py``.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The exact postgres build the suite's template is made against, which every
# run's cluster then boots as the baked image: a template built on one
# PostgreSQL build and cloned on another is not a supported combination.
_BASE_IMAGE = (
    "postgres:18-alpine@sha256:"
    "6c538e7206ea40ff740ef27883529390a690b6ead6ba96b44c67a9f7c638e8fd"
)

# PGDATA for both the build and every run.  Outside the image's declared
# VOLUME; see point 1 in the module docstring.
_BAKED_PGDATA = "/pgdata-baked"

_IMAGE_REPO = "shekel-test-db"
# The baked template's name.  ONE spelling in this file: ``_verify_image``
# asked for it by literal in five places, so a rename elsewhere would have left
# the verifier reporting "the commit did not capture it" -- a diagnosis naming
# the wrong cause -- and ``main`` discarding a good image on every invocation,
# forever.  ``tests/conftest.py`` and ``scripts/build_test_template.py`` spell
# the same constant; all THREE must agree.
_TEMPLATE_DATABASE = "shekel_test_template"

# Inputs the key covers no matter what the builder imports.  These shape the
# template without being reachable by reading its import block: the migration
# chain itself, alembic's own configuration (``migrations/env.py`` sets
# ``version_table_schema``), the ORM classes ``ref_seeds`` seeds THROUGH, and
# the builder script.
_FIXED_KEY_INPUTS = (
    "migrations/versions",
    "migrations/env.py",
    "alembic.ini",
    "app/models",
    "scripts/build_test_template.py",
)

_TEMPLATE_BUILDER = "scripts/build_test_template.py"

# Modules the builder imports from this repository (``app`` and ``scripts``),
# parsed out of the builder itself.
REPO_IMPORT = re.compile(
    r"^(?:from|import)\s+((?:app|scripts)(?:\.[A-Za-z_][A-Za-z0-9_]*)*)", re.M
)


def key_inputs() -> tuple[str, ...]:
    """Return every path whose content determines the baked template.

    HAND-ENUMERATING THIS WAS WRONG AND WAS MEASURED WRONG.  The first
    version listed four modules by hand -- and missed
    ``app/append_only_infrastructure.py``, which
    ``build_test_template._populate_template`` also re-applies after
    ``alembic upgrade``.  A review changed ``APPEND_ONLY_TABLES`` to attach
    three more triggers to a fourth table and the cache key did not move, so
    the stale image would have been reused and the verifier never run.  That
    is a set defined by recollection, and this repo has paid for that shape
    before.

    So the repository half is DERIVED from the builder's own import block:
    the census comes from the file that does the work, and a future import
    lands in the key without anyone remembering to add it.  It reads
    ``scripts`` imports as well as ``app`` ones because the build's verdict
    is an input too: ``scripts/schema_parity.py`` (finding plan#1858)
    decides whether a template is built at all, and a change to what it
    compares must re-run it rather than reuse an image it never judged.
    The rest is fixed because it shapes the template without appearing
    there -- see :data:`_FIXED_KEY_INPUTS`.

    Deriving still cannot see a TRANSITIVE import, which is why
    ``app/models`` is in the fixed half and why the key is only an
    optimisation: :func:`_verify_image` refuses a stale artifact whatever the
    key said, and it now runs on every invocation rather than only after a
    build.

    ``from app import create_app`` resolves to the WHOLE ``app`` package, so
    any ``app/**/*.py`` edit moves the key and forces a rebuild.  That is
    deliberate: it costs 8.5 s (measured 2026-09-05) and it makes transitive
    imports -- which parsing one file's import block cannot see -- stop
    mattering.  Over-hashing wastes seconds; under-hashing ships a stale
    template, which is the failure this whole script exists to prevent.

    Returns:
        Repo-relative paths, sorted and deduplicated.

    Raises:
        BuildError: When the builder cannot be read.
    """
    builder = _REPO_ROOT / _TEMPLATE_BUILDER
    if not builder.exists():
        raise BuildError(f"{_TEMPLATE_BUILDER} is missing; cannot derive the key")
    found: set[str] = set(_FIXED_KEY_INPUTS)
    for dotted in REPO_IMPORT.findall(builder.read_text(encoding="utf-8")):
        relative = dotted.replace(".", "/")
        if (_REPO_ROOT / f"{relative}.py").exists():
            found.add(f"{relative}.py")
        elif (_REPO_ROOT / relative).is_dir():
            found.add(relative)
    return tuple(sorted(found))


# Kept as a name because the tests and the error messages refer to it; it is
# now computed rather than declared.
KEY_INPUTS = key_inputs()

_READY_TIMEOUT_SECONDS = 60
_BUILD_USER = "shekel_user"
_BUILD_PASSWORD = "shekel_pass"
# How much of the template builder's own log a failure report quotes: enough to
# show the last migration revision it ran and whatever it logged after that.
_FAILED_BUILD_LOG_LINES = 40


class BuildError(RuntimeError):
    """A step of the bake failed, with a message naming which one."""


class DockerError(BuildError):
    """A docker command itself failed, so no verdict about the image exists.

    Separate from its parent because the two demand OPPOSITE responses and
    conflating them cost the cache.  ``main`` rebuilds when verification
    returns a verdict of "stale"; a container that never STARTED returns no
    verdict at all, and rebuilding on it treats an infrastructure fault as
    evidence about the image.  Measured on the rootless daemon, where
    ~30% of container starts failed a host-port bind: every invocation
    reported "cached image rejected", rebuilt for 9 s, and buried the real
    error, so the cache never once served its purpose.

    A subclass, not a sibling, so the outer ``except BuildError`` in
    ``main`` still reports it and exits non-zero.
    """


def _run(
    command: list[str], *, capture: bool = True, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command, raising :class:`BuildError` with its stderr on failure.

    Args:
        command: argv to execute.
        capture: Capture stdout/stderr rather than inheriting them.
        check: Raise when the command exits non-zero.

    Returns:
        The completed process.

    Raises:
        DockerError: When ``check`` and the command exited non-zero.

            This is the DEFAULT classification, not a proof: ``_run`` sees an
            exit status and nothing else, and `docker exec psql` exits
            non-zero both for an unreachable daemon and for a missing
            relation.  A caller that knows it asked a question ABOUT THE
            IMAGE must catch this and re-raise :class:`BuildError` -- see
            ``ask`` inside :func:`_verify_image`.
    """
    result = subprocess.run(
        command, capture_output=capture, text=True, check=False
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise DockerError(
            f"{' '.join(command[:3])}... exited {result.returncode}: {detail}"
        )
    return result


def cache_key() -> str:
    """Return a short hex digest of every input that determines the template.

    Returns:
        Twelve hex characters, stable across machines for identical content.

    Raises:
        BuildError: When a declared input does not exist -- a renamed module
            must update :data:`KEY_INPUTS`, because silently dropping an
            input from the key is exactly the staleness this guards against.
    """
    digest = hashlib.sha256()
    digest.update(_BASE_IMAGE.encode())
    for relative in key_inputs():
        target = _REPO_ROOT / relative
        if not target.exists():
            raise BuildError(
                f"key input {relative!r} does not exist. If it moved, update "
                "KEY_INPUTS -- an input silently dropped from the key is a "
                "silently stale image."
            )
        files = (
            sorted(path for path in target.rglob("*.py") if path.is_file())
            if target.is_dir()
            else [target]
        )
        if target.is_dir() and not files:
            raise BuildError(f"key input {relative!r} contains no .py files")
        for path in files:
            digest.update(str(path.relative_to(_REPO_ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def migration_head() -> str:
    """Return the head revision of the migration chain.

    Computed from the files rather than by importing alembic, so it costs no
    app import and works against a bare checkout; each file is read by
    :mod:`scripts.migration_chain`, the one parser of a revision id, imported
    here so ``--print-tag`` stays stdlib-only.  The head is the revision that
    no other revision names as its ``down_revision``.

    Returns:
        The head revision id.

    Raises:
        BuildError: When the chain has no head or more than one -- both mean
            the schema this image would be verified against is not
            well-defined, and guessing would defeat the check.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred on purpose; see the docstring.
    from scripts import migration_chain  # pylint: disable=import-outside-toplevel
    revisions: set[str] = set()
    parents: set[str] = set()
    versions = _REPO_ROOT / migration_chain.VERSIONS
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = migration_chain.revision(text)
        if found:
            revisions.add(found)
        parents.update(migration_chain.down_revisions(text))
    heads = revisions - parents
    if len(heads) != 1:
        raise BuildError(
            f"migration chain has {len(heads)} heads ({sorted(heads)}); the "
            "schema to verify against is not well-defined"
        )
    return heads.pop()


def image_tag(key: str | None = None) -> str:
    """Return the image reference for a cache key.

    Args:
        key: Cache key; computed when omitted.

    Returns:
        A ``repo:tag`` reference.
    """
    return f"{_IMAGE_REPO}:{key or cache_key()}"


def image_exists(tag: str) -> bool:
    """Return whether the image is present locally.

    Args:
        tag: Image reference.

    Returns:
        True when ``docker image inspect`` succeeds.
    """
    return _run(["docker", "image", "inspect", tag], check=False).returncode == 0


def _wait_ready(container: str) -> None:
    """Block until PostgreSQL accepts connections, or fail loud.

    Args:
        container: Container name.

    Raises:
        BuildError: When the cluster is not ready within the timeout.
    """
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        # ``-h 127.0.0.1`` forces TCP, and that is the whole point.  The
        # upstream entrypoint runs a TEMPORARY server during initdb which
        # listens on the unix socket ONLY, so a socket-based ``pg_isready``
        # succeeds against a cluster that is still initialising.  A shutdown
        # issued in that window stops the TEMP server; the entrypoint then
        # starts the real one and the container never stops.  Measured: a
        # container still running 60 s after a clean shutdown was requested.
        # The template build hid this because building takes long enough for
        # init to finish first -- a race won by accident, not a guarantee.
        probe = _run(
            [
                "docker", "exec", container, "pg_isready", "-q",
                "-h", "127.0.0.1", "-U", _BUILD_USER,
            ],
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.2)
    raise BuildError(
        f"{container} did not accept connections within "
        f"{_READY_TIMEOUT_SECONDS}s"
    )


def _wait_stopped(container: str) -> None:
    """Block until the container is no longer running.

    Args:
        container: Container name.

    Raises:
        BuildError: When it is still running after the timeout, which means
            the cluster never shut down and committing it would capture a
            crash-recovery state.
    """
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        state = _run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container],
            check=False,
        )
        if state.stdout.strip() == "false":
            return
        time.sleep(0.2)
    raise BuildError(
        f"{container} was still running {_READY_TIMEOUT_SECONDS}s after a "
        "shutdown was requested; committing it would capture an unclean "
        "cluster"
    )


# THE BAKE CONTAINER IS THE LAST PUBLISHED PORT IN THIS HARNESS, and the
# number it publishes on is CHOSEN rather than assigned.  `docker run -p
# 127.0.0.1::5432` lets dockerd pick, and on a rootless daemon that is a
# 25-40% failure: dockerd's portallocator picks inside the container's network
# namespace and rootlesskit then binds the same number on the HOST, where a
# different set of sockets lives.  `net.ipv4.ip_local_port_range` is
# 32768-60999, byte-identical to docker's publish band, and this host routinely
# holds thousands of sockets in it.  Measured 2026-09-05, rootless daemon:
#
#     12 containers, docker-assigned, removed between each     5/12 failed
#     12 containers, docker-assigned, none removed             3/12 failed
#     12 containers, caller-chosen port outside the band       0/12 failed
#      8 containers, docker-assigned, on the ROOT daemon       0/8  failed
#
# The third row is the fix and the second is why it is a diagnosis rather than
# a guess: with nothing recycled there is no release race left to blame.  The
# run container solved this by having no port at all (`scripts/test.sh`); this
# one cannot, because the postgres entrypoint's post-`initdb` `psql` hardcodes
# `/var/run/postgresql` and `PGHOST` does not override it (measured: exit 2
# either way), and mounting over that path leaves files this user cannot
# delete.  So it keeps a port and picks a safe one.
#
# 20000-32767 is entirely BELOW the ephemeral range, so nothing allocates into
# it behind our back.  The number is derived from the PID for the same reason
# the container NAME is: two worktrees on one branch derive the same cache key
# and so bake at the same moment, and a single fixed port would make the second
# one fail.  A PID is also what makes the ordinary advice work -- a re-run is a
# new PID and therefore a different port -- which is why this is not wrapped in
# a retry loop.
_BAKE_PORT_BAND_START = 20000
_BAKE_PORT_BAND_SIZE = 12768


def _bake_port() -> int:
    """Return this process's host port for the bake container.

    Returns:
        A port in 20000-32767, derived from the PID so two concurrent bakes
        do not collide and a re-run lands somewhere else.
    """
    return _BAKE_PORT_BAND_START + (os.getpid() % _BAKE_PORT_BAND_SIZE)


def _mapped_port(container: str) -> str:
    """Return the host port docker assigned to the cluster.

    Args:
        container: Container name.

    Returns:
        The host port as a string.

    Raises:
        BuildError: When no port mapping is published.
    """
    mapping = _run(["docker", "port", container, "5432/tcp"]).stdout.strip()
    if not mapping or ":" not in mapping:
        raise BuildError(f"no published port for {container}: {mapping!r}")
    return mapping.rsplit(":", 1)[1]


def template_checks() -> tuple[tuple[str, str, int], ...]:
    """Return the ``(label, sql, expected)`` checks a HEAD-built database is graded by.

    The list is :func:`scripts._template_checks.template_checks`' (split out
    at plan step ``balance:X-bi-6-4d-2``, when this file reached pylint's
    1000-line cap); this is its door here, raising the bake's own
    :class:`BuildError`, and ``tests/test_scripts/test_init_database_one_transaction.py``
    grades a first boot by the same list through it.

    Returns:
        The checks, each family's count from its producer's own constant.

    Raises:
        BuildError: When a producer's constant cannot be read.
    """
    # Pylint: import-outside-toplevel -- ``--print-tag`` imports nothing beyond
    # the standard library (``test_print_tag_imports_nothing_beyond_the_standard_library``),
    # and ``scripts`` is not; only a verification reaches this.
    # pylint: disable=import-outside-toplevel
    from scripts._template_checks import TemplateCheckError
    from scripts._template_checks import template_checks as checks

    try:
        return checks()
    except TemplateCheckError as exc:
        raise BuildError(str(exc)) from exc


def _builder_failure(stdout: str, stderr: str) -> str:
    """Return the report for a failed ``build_test_template.py`` run.

    BOTH streams (ruling **R-BAL121**).  The builder runs the chain through the
    deploy's own runner, so ``migrations/env.py`` leaves its logging to the
    app: each migration revision is logged as the app's JSON on STDOUT, and
    the traceback goes to stderr.  Quoting only stderr would drop the line
    that names the revision that was running when the build failed.

    Args:
        stdout: The builder's standard output.
        stderr: Its standard error.

    Returns:
        The last :data:`_FAILED_BUILD_LOG_LINES` lines of stdout, then stderr.
    """
    log_tail = stdout.strip().splitlines()[-_FAILED_BUILD_LOG_LINES:]
    return (
        "build_test_template.py failed.\n"
        f"Its log (stdout, last {_FAILED_BUILD_LOG_LINES} lines):\n"
        + "\n".join(log_tail)
        + "\nIts error (stderr):\n"
        + stderr.strip()
    )


def _verify_image(tag: str) -> None:
    """Start the committed image and re-run the builder's own assertions.

    This is the correctness argument, and the reason the cache key is only an
    optimisation.  It also catches the class of failure that the clean-
    shutdown requirement exists for: a commit taken over a crash-recovery
    cluster loses its most recent ``CREATE DATABASE``, so the template is
    simply absent and nothing else would notice until a suite run failed
    confusingly.

    Args:
        tag: Image reference to verify.

    Raises:
        BuildError: When the image does not contain a usable template -- a
            verdict, on which ``main`` rebuilds.
        DockerError: When docker itself failed, so no verdict was reached.
    """
    container = f"shekel-verify-{tag.rsplit(':', 1)[1]}-{os.getpid()}"
    _run(["docker", "rm", "-fv", container], check=False)
    # The `docker run` belongs INSIDE the try.  It sat outside, so a start
    # that failed after the container was created left it behind in Created
    # state -- four accumulated on this daemon during one session of probing.
    try:
        _run(
            [
                "docker", "run", "-d", "--name", container,
                "-e", f"POSTGRES_USER={_BUILD_USER}",
                "-e", f"POSTGRES_PASSWORD={_BUILD_PASSWORD}",
                "-e", f"PGDATA={_BAKED_PGDATA}",
                # NO PUBLISHED PORT.  Every question this function asks goes
                # through `docker exec`; the mapped port was never read.  It
                # ran on EVERY per-run invocation, so a dead argument was
                # paying the rootless port-bind collision (5/12) once per run
                # on the hot path.  `build()` still publishes one because
                # build_test_template.py connects from the HOST.
                tag,
            ]
        )
        _wait_ready(container)

        def ask(database: str, sql: str) -> str:
            """Return one scalar from the committed image.

            Re-raises a failed query as :class:`BuildError`.  ``_run`` cannot
            draw this line: it sees an exit status, and `docker exec` exits
            non-zero both when the DAEMON is unreachable (a fault) and when
            psql rejects the SQL because a relation is missing (a verdict --
            the image is wrong and must be rebuilt).  Only the caller knows
            it asked a question about the image's contents.

            Args:
                database: Database to query inside the committed image.
                sql: Statement returning exactly one scalar.

            Returns:
                The scalar, stripped.

            Raises:
                BuildError: When the query fails, which means the image does
                    not have the shape this verification requires.
                DockerError: When `docker exec` could not run at all.
            """
            command = [
                "docker", "exec", container, "psql", "-U", _BUILD_USER,
                "-d", database, "-tAc", sql,
            ]
            probe = _run(command, check=False)
            if probe.returncode != 0:
                detail = (probe.stderr or probe.stdout or "").strip()
                raise BuildError(
                    f"{tag} could not answer {sql!r} against {database!r}: "
                    f"{detail}. The image does not match this tree -- it is "
                    "rebuilt rather than reported."
                )
            return probe.stdout.strip()

        present = ask(
            "postgres",
            "SELECT count(*) FROM pg_database "
            f"WHERE datname = '{_TEMPLATE_DATABASE}'",
        )
        if present != "1":
            raise BuildError(
                f"{tag} has no template database (got {present!r}). The "
                "commit did not capture it -- check that PGDATA is outside "
                "the declared VOLUME and that the cluster was shut down "
                "cleanly before committing."
            )

        # EXISTENCE IS NOT ENOUGH, and an earlier draft of this function
        # stopped there while the module docstring claimed it "re-runs the
        # builder's own assertions".  An empty database called
        # shekel_test_template would have passed.  These four are what make
        # the cache key an optimisation rather than the correctness
        # argument: a stale or truncated image is refused here, loudly,
        # whatever the key said.
        head = migration_head()
        # Ask whether the table exists before selecting from it.  An empty
        # database called shekel_test_template otherwise fails with psql's
        # raw `relation "alembic_version" does not exist`, which is true but
        # tells the reader nothing about what to do; measured on a
        # deliberately empty probe image.
        if ask(_TEMPLATE_DATABASE, "SELECT to_regclass('alembic_version')") == "":
            raise BuildError(
                f"{tag} has a template database with no alembic_version "
                "table, so it was never migrated. The template is empty -- "
                "check that build_test_template.py actually ran, and rebuild "
                "with --force."
            )
        stamped = ask(_TEMPLATE_DATABASE, "SELECT version_num FROM alembic_version")
        if stamped != head:
            raise BuildError(
                f"{tag} is stamped {stamped!r} but the migration chain's head "
                f"is {head!r}. The image is STALE -- it was baked against a "
                "different schema. Rebuild with --force."
            )
        # EXACT counts, from the producer's own constants -- not ``> 0``.
        # An earlier version accepted any positive count, and a probe image
        # carrying 1 of 19 account types and 50 of 52 audit triggers passed.
        # This function is the design's safety net for a wrong cache key, so
        # a threshold weaker than the builder's own assertions defeats it:
        # a template missing an entire trigger family would sail through.
        # The counts are IMPORTED rather than restated, so there is one home
        # for each of them, and the list itself has one home too
        # (:func:`template_checks`), which a first boot is graded by as well.
        for label, sql, expected in template_checks():
            answer = ask(_TEMPLATE_DATABASE, sql)
            if answer != str(expected):
                raise BuildError(
                    f"{tag} failed verification ({label}): got {answer!r}, "
                    f"expected {expected}. The image does not match this "
                    "tree -- rebuild with --force."
                )
        log_rows = ask(_TEMPLATE_DATABASE, "SELECT count(*) FROM system.audit_log")
        if log_rows != "0":
            raise BuildError(
                f"{tag} ships {log_rows} audit_log rows; the template must "
                "start from a known zero or every clone inherits them."
            )
        recovered = _run(["docker", "logs", container], check=False)
        if "was not properly shut down" in (recovered.stdout + recovered.stderr):
            raise BuildError(
                f"{tag} boots into crash recovery, so it was committed over "
                "an unclean shutdown. The template may be missing writes; "
                "rebuild with --force."
            )
    finally:
        _run(["docker", "rm", "-fv", container], check=False)


def build(tag: str) -> None:
    """Bake the template into ``tag``.

    Args:
        tag: Image reference to create.

    Raises:
        BuildError: When any step fails.
    """
    # The PID suffix is load-bearing in a repo where several checkouts run
    # at once.  The cache key is a CONTENT hash, so two worktrees on the same
    # branch derive the SAME key by construction -- and each of these helpers
    # opens with an unconditional `docker rm -f` on its own name.  Without a
    # per-process suffix the second invocation's first act destroys the
    # first's live bake container mid-migration-replay, and neither takes any
    # lock.
    container = f"shekel-bake-{tag.rsplit(':', 1)[1]}-{os.getpid()}"
    _run(["docker", "rm", "-fv", container], check=False)
    print(f"  starting build container from {_BASE_IMAGE.split('@', maxsplit=1)[0]}")
    host_port = _bake_port()
    try:
        try:
            _run(
                [
                    "docker", "run", "-d", "--name", container,
                    "-e", f"POSTGRES_USER={_BUILD_USER}",
                    "-e", f"POSTGRES_PASSWORD={_BUILD_PASSWORD}",
                    "-e", "POSTGRES_DB=postgres",
                    "-e", f"PGDATA={_BAKED_PGDATA}",
                    "-p", f"127.0.0.1:{host_port}:5432", _BASE_IMAGE,
                ]
            )
        except DockerError as exc:
            # Name the port, because the docker error alone reads as a daemon
            # fault and the remedy is different: something on this host already
            # holds 127.0.0.1:<port>.  A re-run picks a different one.
            raise DockerError(
                f"the bake container could not bind 127.0.0.1:{host_port}, "
                "which this process derived from its PID inside "
                f"{_BAKE_PORT_BAND_START}-"
                f"{_BAKE_PORT_BAND_START + _BAKE_PORT_BAND_SIZE - 1}.  "
                "Something else on this host holds it; re-running picks a "
                f"different port.  docker said: {exc}"
            ) from exc
        _wait_ready(container)
        # Asked back from docker rather than reused from ``host_port``: the
        # point is to confirm the publish actually happened, and a value this
        # process already holds cannot confirm anything.
        port = _mapped_port(container)
        admin = (
            f"postgresql://{_BUILD_USER}:{_BUILD_PASSWORD}"
            f"@127.0.0.1:{port}/postgres"
        )
        print(f"  running build_test_template.py against 127.0.0.1:{port}")
        # ONE producer for the template.  A second migrate-and-seed path
        # written into this file would be rule 14's shape: two spellings of
        # one value, agreeing until they do not.
        builder = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts/build_test_template.py")],
            cwd=str(_REPO_ROOT),
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(Path.home()),
                "TEST_ADMIN_DATABASE_URL": admin,
                # The builder calls ``create_app``, which refuses without the
                # pinned locale (ruling recurrence:R-R92).  The parent's value
                # is passed ONLY when it has one: an empty string would stop
                # the child's ``load_dotenv()`` supplying it from a host
                # ``.env`` (dotenv never overrides a variable already set).
                **({"LC_ALL": os.environ["LC_ALL"]}
                   if "LC_ALL" in os.environ else {}),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        if builder.returncode != 0:
            raise BuildError(_builder_failure(builder.stdout, builder.stderr))
        # Quote the builder's own verification line rather than inferring
        # success from a zero exit.  It prints the counts it checked; a
        # build that somehow produced nothing would still exit 0 if its
        # assertions were skipped, and this is the cheapest place to notice.
        for line in builder.stdout.splitlines():
            if line.startswith(("  Step 3/3", "DONE:")):
                print(f"  builder: {line.strip()}")
        print("  template built; shutting the cluster down cleanly")
        # See point 2 in the module docstring: `docker stop` alone is a race.
        #
        # This exec is NOT checked, and that is not laziness.  postgres is
        # PID 1, so a successful shutdown terminates the container -- which
        # kills the very `docker exec` that asked for it.  Measured: exit
        # 137 (SIGKILL) after "waiting for server to shut down....", i.e.
        # the shutdown WORKED and the messenger died with it.  Checking the
        # status here would fail every successful build.
        #
        # What actually establishes a clean shutdown is two things that do
        # not depend on this exit code: the container reaching a stopped
        # state below, and _verify_image refusing an image that boots into
        # crash recovery.  The instrument is the outcome, not the command.
        _run(
            [
                "docker", "exec", "-u", "postgres", container,
                "pg_ctl", "-D", _BAKED_PGDATA, "-m", "fast", "-w", "stop",
            ],
            check=False,
        )
        _wait_stopped(container)
        print(f"  committing {tag}")
        # Untag any predecessor first so a rebuild does not leave a ~489 MB
        # dangling image behind; `docker commit` to an existing tag orphans
        # the old layers silently, and two such were already on this daemon
        # from probe bakes before anyone noticed.
        _run(["docker", "rmi", "-f", tag], check=False)
        _run(["docker", "commit", container, tag])
    finally:
        _run(["docker", "rm", "-fv", container], check=False)

    print("  verifying the committed image")
    try:
        _verify_image(tag)
    except DockerError:
        # KEEP THE IMAGE.  A container that never started says nothing about
        # what was just committed, and untagging here would throw away a good
        # ~9 s bake because a host port bind lost a race -- which is exactly
        # the confusion DockerError exists to prevent.  This arm was missed on
        # the first pass and an adversarial review caught it.
        raise
    except BuildError:
        # DO NOT LEAVE A REFUSED IMAGE TAGGED.  It would be accepted by the
        # next invocation's existence check, and the failure that produced it
        # would never be seen again.
        _run(["docker", "rmi", "-f", tag], check=False)
        raise


def _prune_stale_images() -> None:
    """Remove the stale images of earlier keys; ``main`` calls this only after a build.

    Imported here so ``--print-tag``, which other worktrees' prunes run, is stdlib-only.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred on purpose; see the docstring.
    import scripts.prune_test_db_images as _prune  # pylint: disable=import-outside-toplevel
    _prune.prune_stale_images(_IMAGE_REPO, _REPO_ROOT)


def main(argv: list[str] | None = None) -> int:
    """Ensure the template image exists for the current tree.

    Args:
        argv: Command-line arguments; ``sys.argv[1:]`` when omitted.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force", action="store_true", help="rebuild even if the tag exists"
    )
    parser.add_argument(
        "--print-tag",
        action="store_true",
        help="print the tag for this tree and exit without building",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable summary"
    )
    args = parser.parse_args(argv)

    try:
        tag = image_tag()
        if args.print_tag:
            print(tag)
            return 0
        present = image_exists(tag)
        if present and not args.force:
            # VERIFY A CACHED IMAGE TOO.  An earlier version verified only
            # after a build, so a bake whose verification FAILED left the bad
            # image tagged and every later invocation short-circuited on
            # "already present" and returned 0.  Measured: a crash-recovery
            # image was accepted with exit 0 by the very run that would have
            # refused it seconds earlier.  One failed bake poisoned the tag
            # permanently, and reported success.
            print(f"{tag} already present; verifying")
            try:
                _verify_image(tag)
            except DockerError:
                # No verdict was reached, so there is nothing to conclude
                # about the image.  Report it rather than rebuilding on it.
                raise
            except BuildError as stale:
                print(f"  cached image rejected: {stale}", file=sys.stderr)
                print("  discarding it and rebuilding")
                _run(["docker", "rmi", "-f", tag], check=False)
                build(tag)
                _prune_stale_images()
            print(f"DONE: {tag} ready.")
        else:
            print(f"Building {tag}")
            build(tag)
            _prune_stale_images()
            print(f"DONE: {tag} ready.")
        if args.json:
            print(json.dumps({"tag": tag, "rebuilt": not present or args.force}))
        return 0
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
