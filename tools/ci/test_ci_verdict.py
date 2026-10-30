"""Controls for ``ci_verdict``: the ``lint-and-test`` check fails closed.

The ``needs`` shapes below are the real ones.  Captured on the hosted runner
2026-09-22 (run 35813806288, a throwaway probe workflow): a matrix job with
one failing leg of three reports ``"failure"`` for the whole job, a job whose
dependency failed reports ``"skipped"``, and a job skipped by its own ``if:``
reports ``"skipped"`` too -- which is why ``skipped`` is never trusted.  Every
entry carries ``result`` and ``outputs``.  Since ruling ``balance:R-BAL399``
every pull request runs every grader, so the registry-only scope's cases went
with the scope, and the red sentences no longer name a scope.
"""
from __future__ import annotations

import io
import json

import pytest

from tools.ci import ci_verdict


def _needs(**results):
    """A ``needs`` context: every grader succeeded unless overridden.

    Args:
        **results: ``job_id=result`` overrides; an underscore stands for the
            hyphen (``tax_law`` names the ``tax-law`` job).
    """
    needs = {job: {"result": "success", "outputs": {}} for job in
             ("landing", "tools", "tax-law", "lint", "test", "image")}
    for job, result in results.items():
        needs[job.replace("_", "-")]["result"] = result
    return needs


class TestARunIsGreenOnlyWhenEveryGraderPassed:
    """Every job must have succeeded."""

    def test_every_grader_succeeded_is_green(self):
        """The one green shape of a run."""
        assert not ci_verdict.verdict(_needs())

    @pytest.mark.parametrize("job", ["landing", "tools", "tax_law", "lint", "test", "image"])
    @pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
    def test_any_grader_not_succeeding_is_red(self, job, result):
        """A failed, cancelled or SKIPPED grader turns the check red.

        ``test`` skipped is the case this module exists for: a shard's job
        skipped because a dependency failed would otherwise read as a pass.
        """
        reasons = ci_verdict.verdict(_needs(**{job: result}))
        assert reasons == [f"{job.replace('_', '-')}: {result!r}, where it must succeed"]


class TestTheVerdictKnowsExactlyWhatItGrades:
    """An unknown job or a missing one is red: nothing is graded by default."""

    def test_a_job_the_verdict_does_not_name_is_red(self):
        """A grader added to ``needs`` without a rule here is refused."""
        needs = {**_needs(), "benchmarks": {"result": "success", "outputs": {}}}
        assert ci_verdict.verdict(needs) == [
            "benchmarks: a job this verdict does not know how to grade",
        ]

    @pytest.mark.parametrize("job", ["landing", "tools", "tax-law", "lint", "test", "image"])
    def test_a_named_job_missing_from_needs_is_red(self, job):
        """Dropping a grader from ``needs`` cannot quietly stop grading it."""
        needs = _needs()
        del needs[job]
        assert ci_verdict.verdict(needs) == [f"{job}: absent from needs, so it was never graded"]


class TestTheCommandLine:
    """What ``ci.yml``'s aggregate job actually runs."""

    def test_green_exits_zero_and_says_so(self, monkeypatch, capsys):
        """The real JSON shape on stdin; exit 0 and a GREEN line."""
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_needs())))
        assert ci_verdict.main() == 0
        out = capsys.readouterr().out
        assert out.splitlines()[-1] == "lint-and-test: GREEN"
        assert "test: success" in out

    def test_red_exits_one_and_names_the_defect(self, monkeypatch, capsys):
        """A failed shard: exit 1, the job named, RED last."""
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_needs(test="failure"))))
        assert ci_verdict.main() == 1
        out = capsys.readouterr().out.splitlines()
        assert "RED -- test: 'failure', where it must succeed" in out
        assert out[-1] == "lint-and-test: RED"
