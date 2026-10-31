"""Controls for ``ci.yml``'s wiring to the CI modules: every grader is judged, nothing fails green.

Every pull request runs every grader: no job carries a condition (ruling
``balance:R-BAL399`` deleted the scope that let a change set touching only
planning documents skip lint and the shards, and these controls' guard
cases with it), and the ``lint-and-test`` aggregate judges every grader
through ``ci_verdict``.  (Re-expressed over jobs when ``bank_import:X-gy``
split the single job, the developer confirming the change under rule 5,
2026-09-22.)  The merge queue's ``merge_group`` event triggers the workflow.
Since L9d the ``landing`` job reads the tracker through a token minted for
that run alone (ruling balance:R-BAL311).  Since M2 it judges the ship
markers the merge it checked out writes, for the one pull request its event
names, which it reads through the job's own token (ruling balance:R-BAL359).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import yaml

from tools.ci import arcs, ci_verdict
from tools.quill import landing
from tools.quill.setup_tracker import PLAN


# ---------------------------------------------------------------- the wiring


#: The tax-law job's one grading step (ruling salary:R-SAL74).
TAX_LAW_STEP = "Is next year's tax law in? (refuses from December 1)"

#: The step conditions a graded job may carry, each for its own reason.  A step
#: skipped by its own ``if:`` leaves its job ``success``, so ``ci_verdict`` would
#: read a skipped GRADER as green: every other step in a graded job runs
#: unconditionally.  The serial benchmarks run on one shard of six, and so do the
#: rehearsal kit's cluster tests (ruling balance:R-BAL327), on another.  The landing
#: job's mint grades nothing: it is skipped on a run handed no key (GitHub
#: documents that a Dependabot run and a fork's pull request get no Actions
#: secrets), and the check after it, which has no condition, then refuses to
#: judge a change that records a card, so the skip can never turn a needed read
#: green (step X-cx's L9d).
#: The test job's step that runs the rehearsal kit's cluster tests, and the shard it runs on.
REHEARSAL_STEP = "Run the rehearsal kit's cluster tests (shard 3)"
REHEARSAL_SHARD = 3

STEP_CONDITIONS = {
    ("test", "Run audit-trigger benchmarks (serial)"): "strategy.job-index == 0",
    ("test", REHEARSAL_STEP): f"strategy.job-index == {REHEARSAL_SHARD}",
    ("landing", "Mint the tracker read token"): "env.PLAN_READER_KEY_PRESENT == 'true'",
}

#: The jobs whose every step grades: the six ``lint-and-test`` needs, and the
#: aggregate itself.
GRADED_JOBS = ("landing", "tools", "tax-law", "lint", "test", "image", "lint-and-test")

#: The tools job's one grading step.
TOOLS_STEP = "Lint and test every tools package"

#: GitHub's ``timeout-minutes`` for a job that names none, and the most a job on a
#: GitHub-hosted runner may run.
GITHUB_DEFAULT_TIMEOUT_MINUTES = 360

#: The install action every job that runs Python calls, by the ``$/`` form GitHub loads from
#: the workflow's own commit (ruling plan#2375), and the file that defines it.
INSTALL_LOCK = "$/.github/actions/install-lock"
INSTALL_LOCK_FILE = arcs.REPO / ".github/actions/install-lock/action.yml"
#: The step that calls it, named alike in every job.
INSTALL_STEP = "Install the lock"

#: What each job that runs Python installs from the lock, and whether it keeps uv's cache:
#: every job of every workflow but the two that build the app image (ci.yml's ``image`` and
#: the release's), which run no Python on the runner.  The landing job holds a token and the
#: release workflow publishes, so neither restores a cache; the scheduled runs keep none, as
#: they kept no pip cache.
INSTALLS = {
    ("ci.yml", "landing"): {"install": "landing", "cache": "false"},
    ("ci.yml", "tools"): {"install": "all", "cache": "true"},
    ("ci.yml", "tax-law"): {"install": "runtime", "cache": "true"},
    ("ci.yml", "lint"): {"install": "all", "cache": "true"},
    ("ci.yml", "test"): {"install": "all", "cache": "true"},
    ("ci.yml", "lint-and-test"): {"install": "python", "cache": "false"},
    ("ci.yml", "polyglot-lint"): {"install": "all", "cache": "true"},
    ("calendar-sweep.yml", "sweep"): {"install": "all", "cache": "false"},
    ("tax-law.yml", "watch"): {"install": "runtime", "cache": "false"},
    ("docker-publish.yml", "tax-law"): {"install": "runtime", "cache": "false"},
}

#: The triggers under which a step's PATH write lets a pull request's code run with the
#: base's authority (zizmor's github-env audit): no workflow calling the install action may
#: run on one (ruling plan#2376).
DANGEROUS_TRIGGERS = frozenset({"pull_request_target", "workflow_run"})


def _jobs() -> dict[str, dict]:
    """Read the jobs out of the live workflow file."""
    text = (arcs.REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["jobs"]


def _steps(job: str) -> dict[str, dict]:
    """One job's steps, keyed by name."""
    return {step["name"]: step for step in _jobs()[job]["steps"]}


def _run_step(run: str, env: dict[str, str], cwd: Path | None = None,
              ) -> subprocess.CompletedProcess:
    """Run a step's ``run`` text as GitHub runs a ``shell: bash`` step, in ``cwd``.

    ``bash --noprofile --norc -eo pipefail`` is GitHub's invocation for an
    explicit ``shell: bash``, and for every ``run`` step of a composite action,
    which must name its shell; a workflow step with NO ``shell:`` gets ``bash -e``
    instead, which is why the wiring tests require ``shell: bash`` on each
    workflow step this runs.
    """
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", run],
        env={"PATH": "/usr/bin:/bin", **env}, cwd=cwd,
        capture_output=True, text=True, check=False,
    )


def _workflows() -> dict[str, dict]:
    """Every workflow file, read, by its file name."""
    folder = arcs.REPO / ".github/workflows"
    return {path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
            for path in sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")])}


def _action_steps() -> dict[str, dict]:
    """The install action's steps, keyed by name."""
    action = yaml.safe_load(INSTALL_LOCK_FILE.read_text(encoding="utf-8"))
    return {step["name"]: step for step in action["runs"]["steps"]}


def _calls_the_install(job: dict) -> bool:
    """Whether a job has a step naming the install action, in any form."""
    return any("install-lock" in step.get("uses", "") for step in job.get("steps", []))


def _dangerous_callers(workflows: dict[str, dict]) -> list[str]:
    """The workflows that call the install action and run on a :data:`DANGEROUS_TRIGGERS`.

    PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1); a trigger written
    alone, as a list or as a mapping is a trigger all the same.
    """
    found = []
    for name, workflow in workflows.items():
        on = workflow.get(True, workflow.get("on"))
        triggers = {on} if isinstance(on, str) else set(on or ())
        if triggers & DANGEROUS_TRIGGERS and any(
                _calls_the_install(job) for job in workflow["jobs"].values()):
            found.append(name)
    return found


class TestTheWorkflowIsWiredToTheAnswer:
    """Claim 4: read ``ci.yml`` and hold every job to what it must run."""

    def test_no_job_runs_on_a_condition_but_the_aggregate(self):
        """Every grader runs on every change set (R-BAL399): no job carries an ``if:`` or
        waits on another job, but the aggregate, which runs ``if: always()`` after them all."""
        for name, job in _jobs().items():
            if name == "lint-and-test":
                continue
            assert "if" not in job, name
            assert "needs" not in job, name

    def test_every_job_carries_a_time_limit(self):
        """Every job carries a whole ``timeout-minutes`` under GitHub's default (ruling
        plan#2328; its size, twice the job's longest recorded run, is plan#2330's, which
        this test does not grade).

        Every job here holds a merge: the six graders through the aggregate, and the
        aggregate and the polyglot linters as the required checks.  One with no limit waits
        out GitHub's 360 minutes behind a stalled runner, and a limit of 360 or more is no
        limit.  ``True`` is an ``int`` to Python but no number of minutes to GitHub.
        """
        for name, job in _jobs().items():
            limit = job.get("timeout-minutes")
            assert isinstance(limit, int) and not isinstance(limit, bool), (name, limit)
            assert 0 < limit < GITHUB_DEFAULT_TIMEOUT_MINUTES, (name, limit)

    def test_the_code_graders_keep_their_steps(self):
        """The lint job and the test job each still hold every grading step they run."""
        graders = {
            "lint": [
                "Lint with pylint",
                "Lint scripts with pylint",
                "Lint checker package with pylint",
                "Lint verification harnesses with pylint",
                "Test and apply custom pylint checkers",
                "Cross-tree duplicate-code (pylint app/ + scripts/)",
            ],
            "test": [
                "Run tests",
                "Run audit-trigger benchmarks (serial)",
                REHEARSAL_STEP,
            ],
        }
        for job, names in graders.items():
            steps = _steps(job)
            for name in names:
                assert name in steps, f"{name!r} is no longer in the {job!r} job"

    def test_this_package_is_linted_and_tested(self):
        """``tools/ci`` holds the verdict this workflow's required check runs, so its own
        floor and tests run in the tools job."""
        run = _steps("tools")[TOOLS_STEP]["run"]
        assert "pylint tools/ci/ --fail-under=10 --fail-on=E,F" in run
        assert "pytest tools/ci -c /dev/null -q" in run

    def test_the_release_tool_is_linted_and_tested(self):
        """``tools/release`` (step X-cx's L9a) holds the same floor in the same step."""
        run = _steps("tools")[TOOLS_STEP]["run"]
        assert "pylint tools/release/ --fail-under=10 --fail-on=E,F" in run
        assert "pytest tools/release -c /dev/null -q" in run

    def test_the_split_tool_is_linted_and_tested(self):
        """``tools/split`` (step balance:X-er) holds the tools packages' one floor in their
        one step: its pylint 10.00 and its tests run beside quill, ci and release."""
        run = _steps("tools")[TOOLS_STEP]["run"]
        assert "pylint tools/split/ --fail-under=10 --fail-on=E,F" in run
        assert "pytest tools/split -c /dev/null -q" in run

    def test_the_rehearsal_kit_is_linted_and_tested(self):
        """``tools/rehearsal`` (step X-cx's K1) holds the same floor in the same step, and its
        tests run there with the ``cluster`` ones deselected (the K1 leaf-plan ruling, item 3)."""
        run = _steps("tools")[TOOLS_STEP]["run"]
        assert "pylint tools/rehearsal/ --fail-under=10 --fail-on=E,F" in run
        assert 'pytest tools/rehearsal -c /dev/null -q -m "not cluster"' in run

    def test_the_update_tool_is_linted_and_tested(self):
        """``tools/update`` (the Monday lock refresh, ruling plan#2351) holds the same floor
        in the same step."""
        run = _steps("tools")[TOOLS_STEP]["run"]
        assert "pylint tools/update/ --fail-under=10 --fail-on=E,F" in run
        assert "pytest tools/update -c /dev/null -q" in run

    def test_the_rehearsal_kit_s_cluster_tests_run_on_one_shard(self):
        """The ``cluster`` tests the tools job deselects run in the test job, on ONE
        shard, which must be one the matrix still has: a shard index past its end would skip
        the step on every run, and a skipped step reads green (ruling balance:R-BAL327).  The
        job's checkout is the default depth of 1: the bench case's second state is a commit
        it makes of HEAD's own tree, never an older one."""
        job = _jobs()["test"]
        step = _steps("test")[REHEARSAL_STEP]
        assert step["run"] == "pytest tools/rehearsal -c /dev/null -q -m cluster"
        assert step["if"] == f"strategy.job-index == {REHEARSAL_SHARD}"
        assert REHEARSAL_SHARD in job["strategy"]["matrix"]["shard"]
        assert _steps("test")["Checkout code"]["with"] == {"persist-credentials": False}

    def test_no_step_in_a_graded_job_carries_a_condition_of_its_own(self):
        """Every step of a graded job runs, bar the serial-benchmark leg and the landing mint.

        A step skipped by its own ``if:`` leaves its job ``success`` -- the
        skipped-reads-as-passing hole ``lint-and-test`` exists to close, one
        level down.  The single job held this over EVERY step condition; this
        is that claim over the jobs.
        """
        for name in GRADED_JOBS:
            for step in _jobs()[name]["steps"]:
                assert step.get("if") == STEP_CONDITIONS.get((name, step["name"])), (
                    name, step["name"], step.get("if"),
                )

    def test_no_graded_step_or_job_may_fail_without_failing(self):
        """``continue-on-error`` would turn a red grader green before the verdict sees it."""
        for name in GRADED_JOBS:
            job = _jobs()[name]
            assert "continue-on-error" not in job, name
            for step in job["steps"]:
                assert "continue-on-error" not in step, (name, step["name"])

    def test_the_tax_law_check_runs_at_the_refuse_stage(self):
        """From December 1 every pull request waits for next year's tax law (R-SAL74).

        Unconditional and waiting on nothing, because the ruling refuses EVERY
        release; and at the REFUSE stage, never the notice stage, which would
        start refusing a month early.  The weekly watch is the NOTICE stage, on a
        schedule.
        """
        job = _jobs()["tax-law"]
        assert "if" not in job and "needs" not in job
        assert _steps("tax-law")[TAX_LAW_STEP]["run"].strip() == (
            "python scripts/check_tax_law.py refuse"
        )
        text = (arcs.REPO / ".github/workflows/tax-law.yml").read_text(encoding="utf-8")
        watch = yaml.safe_load(text)
        # PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1).
        assert "schedule" in watch[True]
        job = watch["jobs"]["watch"]
        runs = [step.get("run", "") for step in job["steps"]]
        assert "python scripts/check_tax_law.py notice" in runs
        # A watch that may fail without failing emails nobody.
        assert "continue-on-error" not in job and "if" not in job
        for step in job["steps"]:
            assert "continue-on-error" not in step and "if" not in step, step.get("name")

    def test_the_release_image_waits_for_the_tax_law_check(self):
        """No image is built from December 1 without next year (ruling salary:R-SAL88).

        The pull-request check is judged when a PR is pushed, not when it
        merges, and a tag skips PRs; the publish workflow's own check is judged
        when the image is built.  It is a separate job holding read-only
        permissions (its install runs third-party code, and the build job can
        sign and push), the build NEEDS it, and nothing lets it skip or fail
        green.
        """
        text = (arcs.REPO / ".github/workflows/docker-publish.yml").read_text(
            encoding="utf-8",
        )
        jobs = yaml.safe_load(text)["jobs"]
        check = jobs["tax-law"]
        assert check["permissions"] == {"contents": "read"}
        assert "if" not in check and "needs" not in check and "continue-on-error" not in check
        runs = [step.get("run", "") for step in check["steps"]]
        assert "python scripts/check_tax_law.py refuse" in runs
        for step in check["steps"]:
            assert "continue-on-error" not in step and "if" not in step, step.get("name")
        # EVERY other job waits for the check: a second publishing job added
        # beside the build would otherwise escape it.
        for name, job in jobs.items():
            if name != "tax-law":
                assert job.get("needs") == "tax-law", name
                assert "if" not in job, f"an if: could run {name!r} after the check failed"
                assert "continue-on-error" not in job, f"{name!r} could fail green"
        # A re-run of only the failed build reuses a check judged earlier, so
        # the check hands over the instant its refusal starts and the build's
        # FIRST step -- before any checkout or login -- refuses once it has.
        assert check["outputs"] == {"refuse_from": "${{ steps.refuse_from.outputs.epoch }}"}
        emit = next(step for step in check["steps"] if step.get("id") == "refuse_from")
        assert "python scripts/check_tax_law.py refuse --starts-epoch" in emit["run"]
        gate = jobs["build-and-push"]["steps"][0]
        assert gate["env"] == {"REFUSE_FROM": "${{ needs.tax-law.outputs.refuse_from }}"}
        # No step of the build may run past a failed gate or fail green: an
        # ``if: always()`` on the build step would build after the refusal.
        for step in jobs["build-and-push"]["steps"]:
            assert "if" not in step and "continue-on-error" not in step, step.get("name")
        # Both executed steps name ``bash``, so GitHub runs them the way
        # ``_run_step`` does (pipefail included).
        assert gate["shell"] == "bash" and emit["shell"] == "bash"

    @staticmethod
    def _publish_jobs() -> dict[str, dict]:
        """Read the jobs out of the live release-image workflow."""
        text = (arcs.REPO / ".github/workflows/docker-publish.yml").read_text(
            encoding="utf-8",
        )
        return yaml.safe_load(text)["jobs"]

    def test_the_build_gate_lets_only_a_future_instant_through(self):
        """The build's first step, RUN: through only while the refusal instant is ahead.

        A re-run of only the failed build reuses a check judged earlier
        (ruling R-SAL88), so this step is what refuses it.  Every value that
        is not a well-formed future instant must refuse -- an empty output, a
        malformed one, one bash cannot compare -- because a check that handed
        nothing has vouched for nothing.
        """
        run = self._publish_jobs()["build-and-push"]["steps"][0]["run"]
        now = int(time.time())
        future = str(now + 3600)
        malformed = "no usable refusal instant"
        missing = "Next year's tax law is missing"
        # value -> (exit status, the reason it must give)
        cases = {
            future: (0, "Before the tax-law refusal"),
            str(now - 1): (1, missing),
            str(now): (1, missing),
            "0": (1, missing),
            "": (1, malformed),
            "abc": (1, malformed),
            # Malformed FUTURE instants: each is what only the digits-only
            # check refuses, and each stays in the future on every run.
            " " + future: (1, malformed),
            future + " ": (1, malformed),
            "+" + future: (1, malformed),
            "-5": (1, malformed),
            "0x10": (1, malformed),
            "\uff11" + future[1:]: (1, malformed),
            "9" * 19: (1, malformed),
            "9" * 23: (1, malformed),
        }
        for value, (expected, reason) in cases.items():
            result = _run_step(run, {"REFUSE_FROM": value})
            assert result.returncode == expected, (value, result.stdout, result.stderr)
            assert reason in result.stdout + result.stderr, (value, result.stdout, result.stderr)

    def test_the_check_hands_its_instant_to_the_build(self, tmp_path):
        """The check job's output step, RUN: it writes the script's answer, or fails.

        A stand-in ``python`` prints what ``check_tax_law.py refuse
        --starts-epoch`` would; a failing one must fail the step and write
        nothing, so the build is handed no instant and refuses.
        """
        jobs = self._publish_jobs()
        emit = next(step for step in jobs["tax-law"]["steps"] if step.get("id") == "refuse_from")
        stand_in = tmp_path / "python"
        output = tmp_path / "github_output"
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "GITHUB_OUTPUT": str(output)}

        # A value the real law never yields, so an emit step that hard-codes
        # today's answer cannot pass.
        stand_in.write_text(
            '#!/bin/sh\n'
            'test "$*" = "scripts/check_tax_law.py refuse --starts-epoch" || exit 9\n'
            'echo 4242424242\n',
        )
        stand_in.chmod(0o755)
        output.write_text("")
        result = _run_step(emit["run"], env)
        assert result.returncode == 0, result.stderr
        assert output.read_text() == "epoch=4242424242\n"

        # A failing script writes NOTHING, silent or not: a value printed
        # before a failure is no answer.
        for failing in ("exit 1\n", "echo 4242424242\nexit 1\n"):
            stand_in.write_text("#!/bin/sh\n" + failing)
            output.write_text("")
            result = _run_step(emit["run"], env)
            assert result.returncode != 0, failing
            assert output.read_text() == "", failing

class TestTheEventIsReadWhereItIsUsed:
    """The merge queue's trigger, and the one pull request the landing check judges."""

    #: The landing check's ``PULL``: a pull request's number on its own event, a queue
    #: group's branch on its, and nothing on a push, read from the event in the step that
    #: uses it (the scope job that handed it over went with R-BAL399).
    PULL = "${{ github.event.pull_request.number || github.event.merge_group.head_ref || '' }}"

    def test_the_merge_queue_triggers_the_workflow(self):
        """``merge_group`` beside ``pull_request``, neither filtered; ``push`` still only to
        ``main``.  A branch filter on ``pull_request`` (``branches: [main]``) would stop the
        landing check on every pull request into ``dev`` (the L9d delta review's LOW-5)."""
        text = (arcs.REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        # PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1), and a bare
        # trigger, one with no filter, as None.
        triggers = yaml.safe_load(text)[True]
        assert triggers == {"push": {"branches": ["main"]}, "pull_request": None,
                            "merge_group": None}

    def test_the_landing_check_reads_its_pull_request_from_its_own_event(self):
        """Each event carries only its own field, so the first non-empty one is the event's;
        and the two forms it hands over are the ones the landing check reads as that pull
        request (a queue group's branch ends with the number of the one it merges)."""
        check = _steps("landing")["Ship markers against the write rule"]
        assert check["env"]["PULL"] == self.PULL
        number = "594"
        queued = f"refs/heads/gh-readonly-queue/dev/pr-{number}-{'b' * 40}"
        for event, pull in (("pull_request", number), ("merge_group", queued)):
            assert landing.named([event, "a" * 40, pull]) == int(number), event


class TestTheLandingCheckReadsTheTrackerWithATokenOfItsOwn:
    """The ``landing`` job: the tracker read through a token minted for this run alone.

    Step X-cx's L9d, ruling balance:R-BAL311: GitHub's own action mints an
    installation token of the read-only App "Shekel plan reader" from the key
    in the ``PLAN_READER_KEY`` secret, narrowed to reading issues of the
    tracker, and only the check step is handed it.  The tracker's owner and
    name come from quill's ``PLAN``, their one home, never from this file.
    Since M2 the check also reads the pull request it judges through the job's
    own token, which the job widens by ``pull-requests: read`` alone.
    """

    MINT = "Mint the tracker read token"
    CHECK = "Ship markers against the write rule"
    PLACE = "Read the tracker's place"
    #: Every key each step may hold.  A ``shell:`` could run something other than the
    #: check and exit 0, an ``env:`` could hand a step the key or move uv's index, and a
    #: ``continue-on-error`` could fail green: none may be added unseen.
    SHAPES = {
        "Checkout code": {"name", "uses", "with"},
        INSTALL_STEP: {"name", "uses", "with"},
        PLACE: {"name", "id", "run"},
        MINT: {"name", "id", "if", "uses", "with"},
        CHECK: {"name", "env", "run"},
    }

    def test_the_job_judges_the_merge_checked_out_over_full_history_in_order(self):
        """Unconditional, waiting on nothing, a full checkout, the install before the mint,
        and the check handed the event, the commit checked out and the pull request the event
        names, with each token under the name the check reads it by."""
        job = _jobs()["landing"]
        assert "needs" not in job and "if" not in job
        assert [step["name"] for step in job["steps"]] == [
            "Checkout code", INSTALL_STEP, self.PLACE, self.MINT, self.CHECK,
        ]
        assert job["steps"][0]["with"] == {"fetch-depth": 0, "persist-credentials": False}
        check = _steps("landing")[self.CHECK]
        assert check["env"] == {"EVENT_NAME": "${{ github.event_name }}",
                                "MERGE_SHA": "${{ github.sha }}",
                                "PULL": TestTheEventIsReadWhereItIsUsed.PULL,
                                landing.TOKEN_VARIABLE: "${{ steps.token.outputs.token }}",
                                landing.PULL_TOKEN_VARIABLE: "${{ github.token }}"}
        # EXACTLY this, with no condition: the check decides whether a read is
        # needed, and a ``|| true`` would let a refusal fall on the floor.
        assert "if" not in check
        assert check["run"].strip() == (
            'python -m tools.quill.landing "${EVENT_NAME}" "${MERGE_SHA}" "${PULL}"'
        )

    def test_the_job_and_every_step_hold_exactly_their_keys(self):
        """Nothing beyond these keys on the job or any step, and every action pinned by sha
        but this repository's own install action, which GitHub loads from the workflow's own
        commit (``$/``, ruling plan#2375).

        A job-level ``defaults`` (a ``shell:`` for every step) could make the check
        exit 0 unrun, and a wider ``permissions:`` widen the job's GitHub token, as each
        step's extra key could (:attr:`SHAPES`): the job's token reads the code and the
        pull request, and nothing else.  Its ``timeout-minutes`` is every job's
        (:meth:`TestTheWorkflowIsWiredToTheAnswer.test_every_job_carries_a_time_limit`).
        """
        job = _jobs()["landing"]
        assert set(job) == {"runs-on", "timeout-minutes", "permissions", "env", "steps"}
        assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
        assert {step["name"]: set(step) for step in job["steps"]} == self.SHAPES
        for step in job["steps"]:
            if "uses" in step and step["uses"] != INSTALL_LOCK:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["name"]

    def test_the_token_is_minted_for_reading_the_trackers_issues_alone(self):
        """The pinned action, its inputs exactly these, and its only condition the key's presence.

        Exactly these inputs: no ``skip-token-revoke`` (the action revokes the
        token when the job ends; GitHub expires it within the hour regardless) and
        no permission beyond reading issues.  The key reaches the job's env only as
        a boolean, and the mint reads it from the secret itself.
        """
        job = _jobs()["landing"]
        mint = _steps("landing")[self.MINT]
        assert re.fullmatch(r"actions/create-github-app-token@[0-9a-f]{40}", mint["uses"])
        assert mint["id"] == "token"
        assert mint["with"] == {
            "client-id": "${{ vars.PLAN_READER_CLIENT_ID }}",
            "private-key": "${{ secrets.PLAN_READER_KEY }}",
            "owner": "${{ steps.plan.outputs.owner }}",
            "repositories": "${{ steps.plan.outputs.name }}",
            "permission-issues": "read",
        }
        assert job["env"] == {"PLAN_READER_KEY_PRESENT": "${{ secrets.PLAN_READER_KEY != '' }}"}
        assert mint["if"] == "env.PLAN_READER_KEY_PRESENT == 'true'"

    def test_no_step_but_the_check_is_handed_the_token_and_none_but_the_mint_the_key(self):
        """Of the steps, the check alone is handed the token and the mint alone the key.

        (The mint's own post step keeps the token, to revoke it.)  The job's env
        holds the key only as the boolean the mint's condition reads, and no step
        writes GITHUB_ENV or GITHUB_PATH, through which a value set once would
        reach every later step, but the install action's one line putting the
        workspace's own venv first on PATH, which holds no value of any input
        (:class:`TestTheInstallActionInstallsTheLock`).  Either context, by
        attribute or by index (``secrets['PLAN_READER_KEY']``), counts.
        """
        token = re.compile(r"\bsteps\s*(\.\s*token\b|\[\s*['\"]token['\"])")
        for step in _jobs()["landing"]["steps"]:
            text = yaml.safe_dump(step)
            assert "GITHUB_ENV" not in text and "GITHUB_PATH" not in text, step["name"]
            if step["name"] != self.CHECK:
                assert not token.search(text), step["name"]
            if step["name"] != self.MINT:
                assert not re.search(r"\bsecrets\b", text), step["name"]

    def test_the_place_step_hands_the_mint_plans_owner_and_name(self, tmp_path):
        """The step's own shell, RUN: it writes PLAN's owner and name -- the real PLAN's, and a
        stand-in PLAN's made-up ones, so a step spelling the place itself cannot pass -- or
        fails."""
        place = _steps("landing")[self.PLACE]
        assert place["id"] == "plan"
        output = tmp_path / "github_output"
        bindir = os.path.dirname(sys.executable)
        env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
               "GITHUB_OUTPUT": str(output), "PYTHONDONTWRITEBYTECODE": "1"}

        def _run(cwd, run_env):
            output.write_text("")
            return subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", place["run"]],
                                  cwd=cwd, env=run_env, capture_output=True, text=True,
                                  check=False)

        done = _run(arcs.REPO, env)
        assert done.returncode == 0, done.stderr
        assert output.read_text() == f"owner={PLAN.owner}\nname={PLAN.name}\n"

        # Run from a directory whose ``tools.quill.setup_tracker`` holds a made-up PLAN.
        fake = tmp_path / "fake"
        (fake / "tools" / "quill").mkdir(parents=True)
        (fake / "tools" / "__init__.py").write_text("")
        (fake / "tools" / "quill" / "__init__.py").write_text("")
        (fake / "tools" / "quill" / "setup_tracker.py").write_text(
            "class PLAN:\n    owner = 'made-up-owner'\n    name = 'made-up-tracker'\n")
        done = _run(fake, {**env, "PYTHONPATH": ""})
        assert done.returncode == 0, done.stderr
        assert output.read_text() == "owner=made-up-owner\nname=made-up-tracker\n"

        # A python that fails part-way fails the step.
        stand_in = tmp_path / "python"
        stand_in.write_text("#!/bin/sh\ncat >/dev/null\necho owner=someone\nexit 1\n")
        stand_in.chmod(0o755)
        assert _run(arcs.REPO, {**env, "PATH": f"{tmp_path}:/usr/bin:/bin"}).returncode != 0

    def test_the_install_takes_the_landing_group_alone_and_no_cache(self):
        """The install step syncs pyproject.toml's ``landing`` group alone, with no cache
        another run wrote: exactly the two packages the check imports (``requests``, and
        ``cryptography`` through quill's GitHub client), with the indirect packages they need,
        at the lock's versions and hashes.  Each is held to its range by its entry in the
        runtime ``dependencies``, so the group names which packages and never a version."""
        assert _steps("landing")[INSTALL_STEP] == {
            "name": INSTALL_STEP, "uses": INSTALL_LOCK,
            "with": {"install": "landing", "cache": "false"},
        }
        project = tomllib.loads((arcs.REPO / "pyproject.toml").read_text(encoding="utf-8"))
        group = project["dependency-groups"]["landing"]
        assert group == ["requests", "cryptography"]
        runtime = {re.match(r"[\w.-]+", entry)[0] for entry in project["project"]["dependencies"]}
        assert set(group) <= runtime, group

class TestTheRequiredCheckJudgesEveryGrader:
    """``lint-and-test`` -- the check branch protection requires -- answers for every job.

    GitHub counts a SKIPPED required check as passing, so the aggregate must
    run whatever happened and hand every result to ``ci_verdict``.
    """

    def test_the_aggregate_runs_always_and_needs_every_grader(self):
        """``if: always()``, and ``needs`` is exactly the jobs ``ci_verdict`` grades.

        And EVERY job in the workflow but the aggregate and the separately
        required polyglot linters: a grading job left out of ``needs`` is
        judged by no required check at all.  In the single-job era any new
        step was inside the required check by construction; this is what
        keeps a new JOB there.
        """
        jobs = _jobs()
        job = jobs["lint-and-test"]
        assert job.get("if") == "always()"
        assert set(job["needs"]) == set(ci_verdict.GRADERS)
        assert set(jobs) - {"lint-and-test", "polyglot-lint"} == set(job["needs"])

    def test_the_aggregate_hands_the_whole_needs_context_to_the_verdict(self):
        """The verdict step pipes ``toJSON(needs)`` into ``ci_verdict.py``."""
        step = _steps("lint-and-test")["Verdict"]
        assert step["env"] == {"NEEDS": "${{ toJSON(needs) }}"}
        # EXACTLY this: a ``|| true`` or a second command after the pipe would
        # let the verdict's exit 1 fall on the floor.
        assert step["run"].strip() == (
            "printf '%s' \"${NEEDS}\" | python -m tools.ci.ci_verdict"
        )


class TestEveryJobInstallsTheLock:
    """Every job that runs Python installs it through ``.github/actions/install-lock``.

    Ruling balance:R-BAL294 (plan#1027) as X-em's plan#2327 builds it: the job's Python is
    ``.python-version``'s and its packages ``uv.lock``'s, installed by one recipe, so no
    job states a version of its own.  The action is called by the ``$/`` form (ruling
    plan#2375), and its PATH write is safe only while no caller runs on a trigger that
    hands a pull request's code the base's authority (ruling plan#2376).
    """

    def test_each_job_that_runs_python_installs_its_set_right_after_its_checkout(self):
        """Exactly the jobs :data:`INSTALLS` names call the action, each once, as the step
        straight after its checkout (the action reads the checkout's Dockerfile and lock),
        with its install set and its cache; no other job calls it."""
        calls = {}
        for name, workflow in _workflows().items():
            for job_name, job in workflow["jobs"].items():
                steps = job["steps"]
                at = [i for i, step in enumerate(steps) if "install-lock" in step.get("uses", "")]
                if not at:
                    continue
                assert len(at) == 1, (name, job_name, at)
                assert at[0] >= 1, (name, job_name)
                assert steps[at[0] - 1]["uses"].startswith("actions/checkout@"), (name, job_name)
                assert steps[at[0]]["name"] == INSTALL_STEP, (name, job_name)
                calls[(name, job_name)] = steps[at[0]]["with"]
        assert calls == INSTALLS

    def test_the_action_is_called_by_the_self_repository_form(self):
        """``$/``, which GitHub loads from the workflow's own commit, never ``./``, which
        reads whatever the checkout holds when the step runs (ruling plan#2375)."""
        uses = [step["uses"] for workflow in _workflows().values()
                for job in workflow["jobs"].values() for step in job["steps"]
                if "install-lock" in step.get("uses", "")]
        assert len(uses) == len(INSTALLS)
        assert set(uses) == {INSTALL_LOCK}

    def test_no_workflow_installs_python_or_packages_any_other_way(self):
        """No ``setup-python``, no ``python-version``, no pip and no cache action of a job's
        own: a second recipe would be a second home of what it installs."""
        for name, workflow in _workflows().items():
            for job_name, job in workflow["jobs"].items():
                for step in job["steps"]:
                    where = (name, job_name, step.get("name"))
                    uses = step.get("uses", "")
                    assert not uses.startswith(("actions/setup-python@", "actions/cache@")), where
                    assert "python-version" not in step.get("with", {}), where
                    assert not re.search(r"\bpip3? install\b|-m pip\b", step.get("run", "")), where

    def test_no_caller_runs_on_a_trigger_that_makes_the_path_write_dangerous(self):
        """The reason zizmor's github-env flag is ignored on the action's PATH line (ruling
        plan#2376), checked: no workflow calling the action runs on ``pull_request_target``
        or ``workflow_run``.  The check is shown to refuse each spelling of such a trigger,
        and to pass one that calls no install."""
        assert not _dangerous_callers(_workflows())
        calling = {"j": {"steps": [{"uses": INSTALL_LOCK}]}}
        for on in ("workflow_run", ["push", "pull_request_target"],
                   {"pull_request_target": {"branches": ["dev"]}}):
            assert _dangerous_callers({"x.yml": {True: on, "jobs": calling}}) == ["x.yml"], on
        assert _dangerous_callers({"x.yml": {"on": "workflow_run", "jobs": calling}}) == ["x.yml"]
        other = {"j": {"steps": [{"uses": "actions/checkout@" + "a" * 40}]}}
        assert not _dangerous_callers({"x.yml": {True: "workflow_run", "jobs": other}})


class TestTheInstallActionInstallsTheLock:
    """The action's own steps, read and RUN with stand-ins for ``docker`` and ``uv``."""

    COPY = "Copy uv out of the Dockerfile's uv image"
    CACHE = "Restore uv's cache"
    INSTALL = "Install from the lock"

    #: A stand-in ``docker``: it records each call, hands ``create`` a container id, and
    #: writes a runnable ``uv`` where ``cp`` is told to; ``FAIL_PULL`` fails the pull.
    DOCKER = (
        "#!/bin/sh\n"
        'echo "$*" >> "${LOG}"\n'
        'case "$1" in\n'
        '    pull) [ -z "${FAIL_PULL:-}" ] || exit 1 ;;\n'
        "    create) echo c0ffee ;;\n"
        '    cp) printf \'#!/bin/sh\\necho "uv 9.9.9"\\n\' > "$3" && chmod +x "$3" ;;\n'
        'esac\n'
    )

    def test_its_inputs_steps_and_pins(self):
        """Two required inputs; its three steps in order; every third-party action pinned by
        sha; GitHub's own cache action, run only when the caller asks for exactly
        ``true``, keyed on the install set, uv's digest, the lock and ``.python-version``;
        and the install step's interpreter uv's own build, never the runner's."""
        action = yaml.safe_load(INSTALL_LOCK_FILE.read_text(encoding="utf-8"))
        assert action["inputs"].keys() == {"install", "cache"}
        assert all(spec["required"] is True for spec in action["inputs"].values())
        assert action["runs"]["using"] == "composite"
        steps = _action_steps()
        assert list(steps) == [self.COPY, self.CACHE, self.INSTALL]
        for step in steps.values():
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["name"]
            else:
                assert step["shell"] == "bash", step["name"]
        cache = steps[self.CACHE]
        assert cache["uses"].startswith("actions/cache@")
        assert cache["if"] == "inputs.cache == 'true'"
        key = "uv-${{ runner.os }}-${{ inputs.install }}-${{ steps.uv.outputs.digest }}-"
        assert cache["with"] == {
            "path": "${{ steps.uv.outputs.cache }}",
            "key": key + "${{ hashFiles('uv.lock', '.python-version') }}",
            "restore-keys": key + "\n",
        }
        assert "if" not in steps[self.COPY] and "if" not in steps[self.INSTALL]
        assert steps[self.INSTALL]["env"] == {
            "UV": "${{ steps.uv.outputs.bin }}",
            "INSTALL": "${{ inputs.install }}",
            "UV_CACHE_DIR": "${{ steps.uv.outputs.cache }}",
            "UV_PYTHON_PREFERENCE": "only-managed",
        }

    def test_only_the_install_step_writes_the_path_and_zizmor_is_ignored_there_alone(self):
        """No step writes GITHUB_ENV, only the install step writes GITHUB_PATH, and the
        file's one zizmor ignore is the github-env one on that step's ``run`` line (ruling
        plan#2376).  No step reads a secret or a token."""
        for name, step in _action_steps().items():
            text = yaml.safe_dump(step)
            assert "GITHUB_ENV" not in text, name
            assert ("GITHUB_PATH" in text) is (name == self.INSTALL), name
            assert not re.search(r"\bsecrets\b|\btoken\b", text), name
        lines = INSTALL_LOCK_FILE.read_text(encoding="utf-8").splitlines()
        ignores = [i for i, line in enumerate(lines) if "zizmor:" in line]
        assert len(ignores) == 1
        assert lines[ignores[0]] == "      run: | # zizmor: ignore[github-env]"
        names = [line for line in lines[:ignores[0]] if line.startswith("    - name: ")]
        assert names[-1] == f"    - name: {self.INSTALL}"

    def _copy(self, tmp_path: Path, dockerfile: str, **env: str):
        """RUN the copy step in a directory holding ``dockerfile``: the result, what
        ``docker`` was asked, and what the step wrote to its outputs."""
        work, bindir, temp = tmp_path / "work", tmp_path / "bin", tmp_path / "temp"
        for folder in (work, bindir, temp):
            folder.mkdir(exist_ok=True)
        (work / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        (bindir / "docker").write_text(self.DOCKER, encoding="utf-8")
        (bindir / "docker").chmod(0o755)
        log, output = tmp_path / "docker.log", tmp_path / "github_output"
        for file in (log, output):
            file.write_text("", encoding="utf-8")
        result = _run_step(_action_steps()[self.COPY]["run"], {
            "PATH": f"{bindir}:/usr/bin:/bin", "LOG": str(log), "RUNNER_TEMP": str(temp),
            "GITHUB_OUTPUT": str(output), **env}, cwd=work)
        return result, log.read_text(encoding="utf-8").splitlines(), output.read_text(
            encoding="utf-8")

    def test_uv_is_copied_from_the_dockerfiles_own_uv_image_by_its_digest(self, tmp_path):
        """With the real Dockerfile the step pulls its uv image by tag and digest, copies
        ``/uv`` out of it, and hands the binary, the digest and the cache directory on."""
        dockerfile = (arcs.REPO / "Dockerfile").read_text(encoding="utf-8")
        # Read here by splitting the line, not by the step's expression.
        line, = (row for row in dockerfile.splitlines()
                 if row.startswith("FROM ghcr.io/astral-sh/uv:"))
        image = line.split()[1]
        digest = image.split("@sha256:")[1]
        result, calls, output = self._copy(tmp_path, dockerfile)
        assert result.returncode == 0, result.stderr
        uv = tmp_path / "temp" / "uv" / "uv"
        assert calls == [f"pull --quiet {image}", f"create {image} /uv",
                         f"cp c0ffee:/uv {uv}", "rm c0ffee"]
        assert result.stdout.splitlines()[-1] == "uv 9.9.9"
        assert output == (f"bin={uv}\ndigest={digest}\n"
                          f"cache={tmp_path / 'temp' / 'uv-cache'}\n")

    def test_a_dockerfile_without_exactly_one_pinned_uv_line_is_refused(self, tmp_path):
        """No uv line, two, a tag with no digest, a short digest or a line that names no
        ``uv`` stage: the step refuses before calling ``docker`` and hands nothing on; and
        a pull that fails fails the step, handing nothing on."""
        pinned = "FROM ghcr.io/astral-sh/uv:0.1.0@sha256:" + "a" * 64 + " AS uv\n"
        refused = {
            "FROM python:3.14-slim\n": "it holds 0",
            pinned + pinned: "it holds 2",
            "FROM ghcr.io/astral-sh/uv:0.1.0 AS uv\n": "it holds 0",
            pinned.replace("a" * 64, "a" * 63): "it holds 0",
            pinned.replace(" AS uv", ""): "it holds 0",
            pinned.replace(" AS uv", " AS tools"): "it holds 0",
        }
        for dockerfile, reason in refused.items():
            result, calls, output = self._copy(tmp_path, dockerfile)
            assert result.returncode == 1, dockerfile
            assert reason in result.stderr, (dockerfile, result.stderr)
            assert calls == [] and output == "", dockerfile
        result, calls, output = self._copy(tmp_path, pinned, FAIL_PULL="1")
        assert result.returncode != 0
        assert calls == ["pull --quiet " + pinned.split()[1]] and output == ""

    def test_each_install_set_runs_its_one_uv_command_then_puts_the_venv_first(self, tmp_path):
        """Each set runs its one uv command, every sync ``--locked --require-build-hashes``,
        then writes the workspace's ``.venv/bin`` and uv's own directory to GITHUB_PATH.  A
        set the step does not name, and a uv that fails, run nothing more and write no
        PATH."""
        uv, log, path = tmp_path / "uv", tmp_path / "uv.log", tmp_path / "github_path"
        run = _action_steps()[self.INSTALL]["run"]
        sync = "sync --locked --require-build-hashes"
        cases = {
            "all": (0, [sync]),
            "runtime": (0, [f"{sync} --no-dev"]),
            "landing": (0, [f"{sync} --only-group landing"]),
            "build": (0, [f"{sync} --only-group build"]),
            "python": (0, ["venv"]),
            "dev": (1, []), "": (1, []), "all ": (1, []), "ALL": (1, []),
        }
        for status in ("0", "3"):
            uv.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexit {status}\n', encoding="utf-8")
            uv.chmod(0o755)
            for install, (expected, calls) in cases.items():
                log.write_text("", encoding="utf-8")
                path.write_text("", encoding="utf-8")
                result = _run_step(run, {"UV": str(uv), "INSTALL": install,
                                         "GITHUB_PATH": str(path),
                                         "GITHUB_WORKSPACE": str(tmp_path / "ws")})
                assert log.read_text(encoding="utf-8").splitlines() == calls, install
                if expected == 0 and status == "0":
                    assert result.returncode == 0, (install, result.stderr)
                    assert path.read_text(encoding="utf-8") == (
                        f"{tmp_path / 'ws'}/.venv/bin\n{tmp_path}\n")
                else:
                    assert result.returncode != 0, (install, status)
                    assert path.read_text(encoding="utf-8") == "", (install, status)
                if expected == 1:
                    assert "No install set" in result.stderr, install
