"""Controls for ``docker-publish.yml``'s weekly rebuild (ruling plan#3724): the schedule, one
build at a time, the build's refusal of a run that is no longer of ``main``'s tip, and the alert
a failed scheduled run opens.

The tax-law check every build waits for is held by ``test_ci_workflow``'s
``test_the_release_image_waits_for_the_tax_law_check``, and the alert's running the runner's own
Python by :data:`tools.ci.test_ci_workflow.RUNNER_PYTHON`.
"""
from __future__ import annotations

import yaml

from tools.ci.test_ci_workflow import _run_step, _workflows

#: The release-image workflow.
PUBLISH = "docker-publish.yml"
#: The build's step that refuses a run of an older commit than ``main``'s.
TIP_STEP = "Is this still main's newest commit?"
#: The repository the step reads ``main``'s tip from, as the workflow hands it.
REMOTE = "${{ github.server_url }}/${{ github.repository }}.git"


def _workflow() -> dict:
    """The release-image workflow, read."""
    return _workflows()[PUBLISH]


def _tip_step() -> dict:
    """The build's step :data:`TIP_STEP`."""
    return next(step for step in _workflow()["jobs"]["build-and-push"]["steps"]
                if step["name"] == TIP_STEP)


class TestTheWeeklyRebuild:
    """``docker-publish.yml`` builds ``main`` weekly as a push to it does, never puts an older
    commit under ``latest``, and tells the developer when a scheduled build fails."""

    def test_main_is_built_on_each_push_and_weekly_one_run_at_a_time(self):
        """A push to ``main``, and Mondays at 08:23 UTC, off the top of the hour when GitHub may
        drop queued scheduled runs; no dispatch, which would build a branch's copy of this file,
        and no git tag (ruling plan#3741), whose build ``shekel-deploy`` refuses to deploy, so
        no tag pattern either.  One run at a time, in one group, as every run is of ``main``
        and pushes ``latest``, and none cancelled: none in progress, and ``queue: max`` keeps
        every run waiting where GitHub's default keeps one and cancels it for the next."""
        workflow = _workflow()
        # PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1).
        on = workflow.get(True, workflow.get("on"))
        assert on == {"push": {"branches": ["main"]}, "schedule": [{"cron": "23 8 * * 1"}]}
        meta = next(step for step in workflow["jobs"]["build-and-push"]["steps"]
                    if step.get("id") == "meta")
        assert meta["with"]["tags"].split() == [
            "type=raw,value=latest,enable={{is_default_branch}}", "type=sha"]
        assert workflow["concurrency"] == {"group": "release-image",
                                           "cancel-in-progress": False, "queue": "max"}

    def test_the_tip_check_runs_second_before_any_checkout_or_login(self):
        """Straight after the tax-law gate and before the checkout, with exactly its keys: no
        ``if:`` that could skip it and no ``continue-on-error`` that could fail it green, and
        ``shell: bash`` so GitHub runs it as :func:`_run_step` does (pipefail included)."""
        steps = _workflow()["jobs"]["build-and-push"]["steps"]
        assert steps[1]["name"] == TIP_STEP
        assert steps[2]["uses"].startswith("actions/checkout@")
        assert set(steps[1]) == {"name", "shell", "env", "run"}
        assert steps[1]["shell"] == "bash"
        assert steps[1]["env"] == {"SHA": "${{ github.sha }}", "REMOTE": REMOTE}

    def test_the_tip_check_passes_main_s_tip_alone(self, tmp_path):
        """The step, RUN with a stand-in ``git`` answering ``ls-remote`` as GitHub would: it
        passes a run of ``main``'s tip, and refuses one of any other commit, and one whose tip
        cannot be read, whether ``git`` fails, prints nothing, or prints a tip and then
        fails."""
        run = _tip_step()["run"]
        remote = "https://github.com/owner/repo.git"
        tip = "a" * 40
        calls = tmp_path / "calls"
        git = tmp_path / "git"
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "REMOTE": remote}
        answer = f"printf '%s\\trefs/heads/main\\n' {tip}\n"
        # stand-in body -> (sha the run is of, exit status, what it must say)
        cases = [
            (answer, tip, 0, f"{tip} is main's newest commit."),
            (answer, "b" * 40, 1, f"main's newest commit is '{tip}'"),
            ("exit 2\n", tip, 2, ""),
            ("exit 0\n", tip, 1, "main's newest commit is ''"),
            (answer + "exit 128\n", tip, 128, ""),
        ]
        for body, sha, expected, said in cases:
            git.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\n{body}')
            git.chmod(0o755)
            calls.write_text("")
            result = _run_step(run, {**env, "SHA": sha})
            assert result.returncode == expected, (body, sha, result.stdout, result.stderr)
            assert said in result.stdout + result.stderr, (body, sha, result.stdout, result.stderr)
            if expected:
                assert "is main's newest commit." not in result.stdout, (body, sha)
            assert calls.read_text() == f"ls-remote --exit-code {remote} refs/heads/main\n"

    def test_a_failed_scheduled_run_opens_the_update_alert_s_issue(self):
        """The alert runs when a scheduled run's build failed, and on no other run: never for
        the tax-law job's refusal, which skips the build (ruling plan#3742), and never for a
        release, which watches its own build.  It holds no secret or variable and only the
        workflow's token, widened to issues, so it can publish nothing; in two steps, the
        checkout and the alert, so it installs nothing and runs on the standard library alone
        (``-S``; plan#2481), handed each job's result (ruling plan#3724)."""
        alert = _workflow()["jobs"]["alert"]
        assert set(alert) == {"needs", "if", "runs-on", "timeout-minutes", "permissions",
                              "steps"}
        assert alert["needs"] == ["tax-law", "build-and-push"]
        assert alert["if"] == ("failure() && github.event_name == 'schedule'"
                               " && needs.build-and-push.result == 'failure'")
        assert alert["permissions"] == {"contents": "read", "issues": "write"}
        text = yaml.safe_dump(alert)
        assert "secrets." not in text and "vars." not in text
        checkout, step = alert["steps"]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"persist-credentials": False}
        variables = {job: job.upper().replace("-", "_") for job in alert["needs"]}
        assert step["env"] == {
            "GH_TOKEN": "${{ github.token }}",
            "RUN": "${{ github.server_url }}/${{ github.repository }}/actions/runs/"
                   "${{ github.run_id }}",
            **{name: f"${{{{ needs.{job}.result }}}}" for job, name in variables.items()}}
        assert step["run"] == ('python3 -S -m tools.update.alert "${RUN}" ' + " ".join(
            f'"{job}=${{{name}}}"' for job, name in variables.items()))
