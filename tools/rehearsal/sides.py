"""How a side runs (step X-cx's K1b): its deploy step and its boot, each a side probe
(``side/``) run by path from the side's worktree under its venv, which :mod:`.rows` runs on
each copy.

THE ENVIRONMENT IS BUILT WHOLE (:func:`side_environment`): nothing of the shell that runs the
kit reaches a side, so an operator's ``DATABASE_URL_APP``, ``VALKEY_PASSWORD`` or
``SHEKEL_FAKE_TODAY`` cannot.  What production gives the app beside its secrets and URLs is read
from the side's own tree (:func:`image_environment`), so a cut that changes it is measured with
it: every ``ENV`` of its ``Dockerfile`` but ``PATH``, the ``TZ`` its production compose file
pins, and the passkey origin that file names, which a side's app refuses to start without from
plan#3488 on, and a side from before it names none and reads none.  The
rate-limit store is not among them: neither probe serves a request, and the app names its store
in its own code (plan#2634), needing only the store's password to make its app, so every side is
given one no store holds (:data:`_UNHELD_PASSWORD`).  A harness that renders pages in this
environment turns the limiter off first (``tests/manual/verify_render_surfaces.py``).  That the
store serves the app is graded elsewhere: on every pull request against the real store, and at a
release by its check after the deploy (ruling plan#2473).

THE CLOCK IS PINNED at ONE instant for every side: noon of the dump's civil day in base's zone,
computed once a run (:func:`pinned_instant`), so a run that crosses midnight deploys every side
on one day.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from tools.rehearsal import bench

_SIDE = Path(__file__).resolve().parent / "side"
#: The side probe that runs a side's deploy step under the pinned instant, run by path.
_DEPLOY_PROBE = _SIDE / "deploy.py"
#: The side probe that boots a side's app under the pinned instant, run by path.
_BOOT_PROBE = _SIDE / "boot.py"
#: The store's password every side is given (``VALKEY_PASSWORD``): the app names its store in
#: its own code and refuses to make its app without the password (plan#2634).  No store holds
#: it, so a deploy, which serves no request, that reached for the store would fail rather than
#: pass on a live one: where nothing listens at the code's address the connection fails naming
#: it, and where a store does, the store refuses the password.
_UNHELD_PASSWORD = "rehearsal.invalid"
#: The production compose file's pin of the app's civil timezone.
_COMPOSE_TZ = re.compile(r"^\s+TZ: (\S+)$", re.MULTILINE)
#: The production compose file's passkey origin (plan#3488).
_COMPOSE_ORIGIN = re.compile(r"^\s+PASSKEY_ORIGIN: (\S+)$", re.MULTILINE)
_COMPOSE = Path("deploy") / "docker-compose.prod.yml"
#: ``PYTHONHASHSEED``, fixed on every side (design s.3 item 5).
_HASH_SEED = "0"
#: Switches python-dotenv off, so ``app/config.py``'s ``load_dotenv()`` reads no ``.env``, as
#: in production, whose image holds none (``.dockerignore``).  Without it a ``.env`` in any
#: directory above the side's worktree (the walk ``find_dotenv()`` makes up to ``/``) would
#: set what this environment leaves unset: ``DATABASE_URL_APP`` first, which the app prefers
#: to ``DATABASE_URL`` and only the deploy step blanks, so the boot would connect where it
#: named.  Honoured by every python-dotenv pinned since 2026-03-21 (1.2.2 on).
_NO_DOTENV = ("PYTHON_DOTENV_DISABLED", "1")
#: How much of a refused deploy's stderr the report quotes.
_QUOTED_LINES = 20


def image_environment(worktree: Path) -> dict[str, str]:
    """What production gives the app beside its secrets and URLs, read from the side's own
    tree: every ``ENV`` of its ``Dockerfile`` but ``PATH`` (which names the image's venv;
    :func:`bench.dockerfile_environment`), the app's ``TZ`` from its production compose file,
    and the ``PASSKEY_ORIGIN`` that file names, where it names one: a tree from before
    plan#3488 names none, and its app reads none.

    Raises:
        bench.BenchError: when either file cannot be read, or the compose file pins no
            single ``TZ`` or names more than one ``PASSKEY_ORIGIN``.
    """
    image = bench.dockerfile_environment(worktree)
    try:
        compose = (worktree / _COMPOSE).read_text(encoding="utf-8")
    except OSError as exc:
        raise bench.BenchError(f"{worktree} cannot give production's environment: {exc}") \
            from exc
    zones = _COMPOSE_TZ.findall(compose)
    if len(zones) != 1:
        raise bench.BenchError(f"{worktree / _COMPOSE} pins {len(zones)} TZ lines, not one")
    origins = _COMPOSE_ORIGIN.findall(compose)
    if len(origins) > 1:
        raise bench.BenchError(
            f"{worktree / _COMPOSE} names {len(origins)} PASSKEY_ORIGIN lines, not one")
    environment = {name: value for name, value in image.items() if name != "PATH"}
    environment["TZ"] = zones[0]
    if origins:
        environment["PASSKEY_ORIGIN"] = origins[0]
    return environment


def side_environment(state: bench.State, url: str, secret_key: str) -> dict[str, str]:
    """The whole environment a side probe runs in, its deploy and its boot, inheriting
    nothing: production's (:func:`image_environment`, its passkey origin among it), its
    configuration, the copy's URL, a
    secret key of the run's, a store password no store holds (:data:`_UNHELD_PASSWORD`), the
    fixed hash seed, python-dotenv switched off (:data:`_NO_DOTENV`), and the side's venv first
    on ``PATH``.

    Raises:
        bench.BenchError: when the side's tree cannot give production's environment.
    """
    return {
        **image_environment(state.worktree),
        "FLASK_ENV": "production",
        "SECRET_KEY": secret_key,
        "DATABASE_URL": url,
        "VALKEY_PASSWORD": _UNHELD_PASSWORD,
        "PYTHONHASHSEED": _HASH_SEED,
        _NO_DOTENV[0]: _NO_DOTENV[1],
        "PATH": os.pathsep.join((str(state.python.parent), "/usr/bin", "/bin")),
    }


def pinned_instant(made: bench.Bench) -> datetime:
    """The one instant every side's clock is pinned at in a run: noon of the dump's civil day
    (:meth:`bench.Listing.noon`) in the zone base's production compose file pins
    (:func:`image_environment`).

    Raises:
        bench.BenchError: when base's tree cannot give production's environment.
    """
    base = made.made["base"]
    return made.listing.noon(image_environment(base.worktree)["TZ"])


def _probe(probe: Path, state: bench.State, url: str, secret_key: str, moment: datetime
           ) -> str | None:
    """Run the side probe ``probe`` for ``state`` on the copy at ``url``, its clock pinned at
    ``moment``, from the side's worktree under its venv in :func:`side_environment`; None when
    it exits 0, else what it said last.

    Raises:
        bench.BenchError: when the side's interpreter cannot be run, or its tree cannot give
            production's environment.
    """
    done = bench.run([str(state.python), str(probe), moment.isoformat()],
                     side_environment(state, url, secret_key), cwd=state.worktree)
    if not done.returncode:
        return None
    said = (done.stderr or done.stdout).strip().splitlines()[-_QUOTED_LINES:]
    return f"exit {done.returncode}:\n" + "\n".join(said)


def deploy(state: bench.State, url: str, secret_key: str, moment: datetime) -> str | None:
    """Run ``state``'s deploy step on the copy at ``url`` with its clock pinned at ``moment``
    (``side/deploy.py``, by :func:`_probe`); None when it exits 0, else what it said last.

    Raises:
        bench.BenchError: as :func:`_probe`.
    """
    return _probe(_DEPLOY_PROBE, state, url, secret_key, moment)


def boot(state: bench.State, url: str, secret_key: str, moment: datetime) -> str | None:
    """Boot ``state``'s app on the copy at ``url`` with its clock pinned at ``moment`` and
    answer one query (``side/boot.py``, by :func:`_probe`); None when it exits 0, else what it
    said last.

    Raises:
        bench.BenchError: as :func:`_probe`.
    """
    return _probe(_BOOT_PROBE, state, url, secret_key, moment)
