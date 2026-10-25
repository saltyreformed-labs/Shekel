"""The processes the proof runs: the suite through ``./scripts/test.sh`` (never bare
pytest: the wrapper gives each run a database cluster of its own), pylint, and ``git grep``
for the old module's name.  Each returns what it observed; judging it is
:mod:`tools.split._dynamic`'s.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from tools.ci import gitcmd
from tools.split._model import RESERVED, Plan
from tools.split._observe import OBSERVE_ENV

#: pylint's exit-status bits for a fatal message and a usage error: either means it did
#: not lint, so its message list is not an observation.
_PYLINT_BROKEN = 1 | 32

#: Where the census looks for the old module's name.
CENSUS_PATHSPECS = ("tests", "tools", "scripts", "docs", ".claude", "app")


class RunError(RuntimeError):
    """A run did not produce the observation it exists to produce."""


def observe(root: Path, targets: list[str], out: Path, collect_only: bool) -> dict:
    """Run ``targets`` through the suite wrapper with the observer loaded and return its
    observation (collect-only runs single-process, so the collection is the run's own)."""
    out.unlink(missing_ok=True)
    env = dict(os.environ)
    env[OBSERVE_ENV] = str(out)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(root), env.get("PYTHONPATH")) if p)
    args = ["./scripts/test.sh", *targets, "-p", "tools.split._observe",
            "-p", "no:cacheprovider", "-q"]
    if collect_only:
        args += ["-n", "0", "--collect-only"]
    proc = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True,
                          check=False)
    if not out.exists():
        raise RunError(f"{' '.join(args)} wrote no observation (exit {proc.returncode}):\n"
                       + "\n".join((proc.stdout + proc.stderr).splitlines()[-30:]))
    observed = json.loads(out.read_text(encoding="utf-8"))
    observed["returncode"] = proc.returncode
    observed["summary"] = (proc.stdout.strip().splitlines() or [""])[-1]
    return observed


def pylint_messages(root: Path, paths: list[str]) -> list[dict]:
    """pylint's messages for ``paths``, linted in ONE run so duplicate-code between them
    is seen."""
    proc = subprocess.run(["pylint", "--output-format=json", "--score=n", *paths], cwd=root,
                          capture_output=True, text=True, check=False)
    if proc.returncode & _PYLINT_BROKEN:
        raise RunError(f"pylint did not lint {paths} (exit {proc.returncode}): "
                       f"{proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout or "[]")


def mentions(plan: Plan) -> re.Pattern[str]:
    """A mention of the source module's NAME, in any spelling, that does not continue INTO
    the new package.

    The unit is the name, never a list of spellings (the coordinator's ruling on the split
    session's fork over the fourth split, 2026-10-15): ``m.py``, ``tests.x.m``, ``m.Member``,
    a module-level ``m.test_fn`` and a bare ``m`` all name the old module, and a census that
    matched only the first two missed a live ``m.Member`` pointer.  A mention is dropped only
    where it continues into the package: followed by ``/`` (its directory) or by ``.`` and
    one of the package's own module names, read from the plan.  Whatever this over-lists is
    the safe side, because each line is classified by the pointer rule."""
    own = "|".join(sorted(RESERVED | {part.module for part in plan.parts}))
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(plan.stem)}(?![A-Za-z0-9_])"
                      rf"(?!/|\.(?:{own})(?![A-Za-z0-9_]))")


def census(root: Path, plan: Plan) -> list[str]:
    """``git grep -n`` lines naming the old module (:func:`mentions`), in tracked files under
    the census roots.  git finds every line holding the name as a FIXED string, so the one
    judge of what a mention is stays :func:`mentions`.  It starts through
    :func:`tools.ci.gitcmd.run`, so ``root`` alone is the repository read, whatever a hook
    has bound this process to."""
    proc = gitcmd.run(root, "grep", "-n", "-F", plan.stem, "--", *CENSUS_PATHSPECS)
    if proc.returncode not in (0, 1):
        raise RunError(f"git grep failed: {proc.stderr.strip()}")
    mention = mentions(plan)
    return [entry for entry in proc.stdout.splitlines()
            if mention.search(entry.split(":", 2)[2])]
