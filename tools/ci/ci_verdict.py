"""Decide the ``lint-and-test`` check: green only when every grader that had to run passed.

Branch protection on ``main`` requires a status named ``lint-and-test``.  Until
plan step ``bank_import:X-gy`` that was one job running everything in series;
it is now several jobs in parallel -- the change-set classifier, the card-trailer
check, the landing check, the plan gate, the tax-law check, lint and the test
shards -- and ``lint-and-test`` is the job that NEEDS all of them and answers
for them.  ``ci.yml`` hands it the ``needs`` context as JSON on stdin and this
module prints the verdict and exits non-zero on red.

**Why it cannot simply be a job that depends on the others.**  GitHub skips a
job whose dependency failed, and a SKIPPED required check counts as PASSING.
A ``lint-and-test`` that merely listed the graders in ``needs`` would therefore
be skipped -- green -- exactly when a shard failed: a gate that measures
nothing on the one day it matters.  So the job runs ``if: always()`` and every
result is judged here, in the direction that fails closed:

* A job in :data:`EVERY_SCOPE` must have succeeded, in every scope.
* A job in :data:`FULL_SCOPE_ONLY` must have succeeded -- unless the scope is
  exactly ``registry-only`` (``ci_scope``'s answer for a change set only the
  plan gate grades), when it may have been skipped.  A missing, empty or
  misspelt scope is NOT ``registry-only``, so it demands success.
* ``failure`` and ``cancelled`` are red in every scope.  A combined matrix
  result is ``failure`` when any leg failed (measured on the runner
  2026-09-22, run cited in ``test_ci_verdict.py``).
* A job this module does not name, or a named job absent from ``needs``, is
  red: a verdict that does not know what it is grading has not graded it.

Usage from ``ci.yml``, at the repository root::

    printf '%s' "${NEEDS}" | python -m tools.ci.ci_verdict

with ``NEEDS: ${{ toJSON(needs) }}``.
"""
from __future__ import annotations

import json
import sys

from tools.ci.ci_scope import REGISTRY_ONLY

#: Jobs that grade in every scope: the classifier itself, the card-trailer
#: check (a registry-only pass carries commits too; step X-cx's L4), the
#: landing check, which judges which change may write a ship marker (step
#: X-cx's M2), the plan gate, and the tax-law check, which refuses EVERY pull
#: request from December 1 until next year's tax law is in the app (plan step
#: salary:X-at-4, ruling salary:R-SAL74) -- a registry-only pass included.
EVERY_SCOPE = ("scope", "commit-trailers", "landing", "plan-gate", "tax-law")

#: Jobs guarded by ``needs.scope.outputs.scope != 'registry-only'``: they grade
#: code, and a registry-only change set skips them.
FULL_SCOPE_ONLY = ("lint", "test")


def scope_of_needs(needs: dict) -> str:
    """Return the classifier's answer as the aggregate received it, or ``""``."""
    return str(needs.get("scope", {}).get("outputs", {}).get("scope", ""))


def verdict(needs: dict) -> list[str]:
    """Judge the ``needs`` context; return why the check is red (empty: green).

    Args:
        needs: The parsed ``toJSON(needs)`` of the aggregate job -- job id to
            ``{"result": ..., "outputs": {...}}``.

    Returns:
        One sentence per defect, in job order; an empty list means green.
    """
    registry_only = scope_of_needs(needs) == REGISTRY_ONLY
    reasons = []
    for job in sorted(set(needs) - set(EVERY_SCOPE) - set(FULL_SCOPE_ONLY)):
        reasons.append(f"{job}: a job this verdict does not know how to grade")
    for job in EVERY_SCOPE + FULL_SCOPE_ONLY:
        if job not in needs:
            reasons.append(f"{job}: absent from needs, so it was never graded")
            continue
        result = needs[job].get("result")
        allowed = (
            {"success", "skipped"}
            if job in FULL_SCOPE_ONLY and registry_only
            else {"success"}
        )
        if result not in allowed:
            reasons.append(
                f"{job}: {result!r}, where this scope requires "
                f"{' or '.join(sorted(allowed))}"
            )
    return reasons


def main() -> int:
    """Read ``needs`` as JSON on stdin, print the verdict, exit 1 when red."""
    needs = json.load(sys.stdin)
    scope = scope_of_needs(needs) or "(none)"
    for job in sorted(needs):
        print(f"{job}: {needs[job].get('result')}")
    print(f"scope: {scope}")
    reasons = verdict(needs)
    for reason in reasons:
        print(f"RED -- {reason}")
    print("lint-and-test: " + ("RED" if reasons else "GREEN"))
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
