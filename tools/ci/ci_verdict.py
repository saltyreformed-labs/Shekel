"""Decide the ``lint-and-test`` check: green only when every grader passed.

Branch protection on ``main`` requires a status named ``lint-and-test``.  Until
plan step ``bank_import:X-gy`` that was one job running everything in series;
it is now several jobs in parallel -- the landing check, the tools packages'
lint and tests, the tax-law check, lint, the test shards and the app image's
build -- and ``lint-and-test`` is the job that NEEDS all of them and answers
for them.
``ci.yml`` hands it the ``needs`` context as JSON on stdin and this module
prints the verdict and exits non-zero on red.

**Why it cannot simply be a job that depends on the others.**  GitHub skips a
job whose dependency failed, and a SKIPPED required check counts as PASSING.
A ``lint-and-test`` that merely listed the graders in ``needs`` would therefore
be skipped -- green -- exactly when a shard failed: a gate that measures
nothing on the one day it matters.  So the job runs ``if: always()`` and every
result is judged here, in the direction that fails closed:

* A job in :data:`GRADERS` must have succeeded.  Every pull request runs every
  grader (ruling ``balance:R-BAL399`` deleted the scope that let a change set
  touching only planning documents skip lint and the shards), so ``skipped``
  is red like ``failure`` and ``cancelled``.  A combined matrix result is
  ``failure`` when any leg failed (measured on the runner 2026-09-22, run
  cited in ``test_ci_verdict.py``).
* A job this module does not name, or a named job absent from ``needs``, is
  red: a verdict that does not know what it is grading has not graded it.

Usage from ``ci.yml``, at the repository root::

    printf '%s' "${NEEDS}" | python -m tools.ci.ci_verdict

with ``NEEDS: ${{ toJSON(needs) }}``.
"""
from __future__ import annotations

import json
import sys

#: The jobs ``lint-and-test`` answers for: the landing check, which judges
#: which change may write a ship marker (step X-cx's M2); the tools packages'
#: lint and tests; the tax-law check, which refuses EVERY pull request from
#: December 1 until next year's tax law is in the app (plan step
#: salary:X-at-4, ruling salary:R-SAL74); lint; the test shards; and the app
#: image, built and checked before a merge (task plan#2366), folded in here
#: so the rulesets' required checks need no new entry.
GRADERS = ("landing", "tools", "tax-law", "lint", "test", "image")


def verdict(needs: dict) -> list[str]:
    """Judge the ``needs`` context; return why the check is red (empty: green).

    Args:
        needs: The parsed ``toJSON(needs)`` of the aggregate job -- job id to
            ``{"result": ..., "outputs": {...}}``.

    Returns:
        One sentence per defect, in job order; an empty list means green.
    """
    reasons = [f"{job}: a job this verdict does not know how to grade"
               for job in sorted(set(needs) - set(GRADERS))]
    for job in GRADERS:
        if job not in needs:
            reasons.append(f"{job}: absent from needs, so it was never graded")
            continue
        result = needs[job].get("result")
        if result != "success":
            reasons.append(f"{job}: {result!r}, where it must succeed")
    return reasons


def main() -> int:
    """Read ``needs`` as JSON on stdin, print the verdict, exit 1 when red."""
    needs = json.load(sys.stdin)
    for job in sorted(needs):
        print(f"{job}: {needs[job].get('result')}")
    reasons = verdict(needs)
    for reason in reasons:
        print(f"RED -- {reason}")
    print("lint-and-test: " + ("RED" if reasons else "GREEN"))
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
