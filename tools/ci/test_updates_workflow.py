"""Controls for ``updates.yml``, the weekly dependency update (rulings plan#2448-plan#2450):
which job holds the update App's key, whose code each runs, and when each runs.
"""
from __future__ import annotations

import re

import yaml

from tools.ci.test_ci_workflow import _workflows


#: The update workflow (rulings plan#2448-plan#2450), its jobs that hold the App's key, and the
#: module each of those runs.
UPDATES = "updates.yml"
KEYED = {"publish": "tools.update.publish", "queue": "tools.update.queue"}
#: GitHub's own action that mints an App's installation token.
MINT = "actions/create-github-app-token@"


class TestTheUpdateWorkflowKeepsItsKeyFromPackageCode:
    """``updates.yml``: the job that runs package code sees no secret, each job holding the
    App's key names the environment only main may use (ruling plan#2472), runs main's own code
    with the landing group alone and keeps no credential, and each job runs only on the
    schedule its condition names, from main."""

    @staticmethod
    def _workflow() -> dict:
        """The update workflow, read."""
        return _workflows()[UPDATES]

    def test_the_refresh_reads_dev_and_sees_no_secret(self):
        """The refresh checks out ``dev`` and names no secret, variable or mint; no job but the
        alert widens the workflow's read-only token, and the alert only to issues (ruling
        plan#2466)."""
        workflow = self._workflow()
        refresh = workflow["jobs"]["refresh"]
        text = yaml.safe_dump(refresh)
        assert "secrets." not in text and "vars." not in text and MINT not in text
        assert refresh["steps"][0]["with"] == {"ref": "dev", "persist-credentials": False}
        assert workflow["permissions"] == {"contents": "read"}
        assert {name: job["permissions"] for name, job in workflow["jobs"].items()
                if "permissions" in job} == {"alert": {"contents": "read", "issues": "write"}}

    def test_a_failed_job_opens_an_issue_with_the_workflow_s_own_token(self):
        """The alert runs when the refresh, the publish or the queue failed, holding no secret,
        variable or mint: main's own code, given the workflow's token and each job's result
        (ruling plan#2466), in two steps, the checkout and the alert, so it installs nothing
        and runs on the standard library alone (``-S``; plan#2481)."""
        alert = self._workflow()["jobs"]["alert"]
        assert alert["needs"] == ["refresh", "publish", "queue"]
        assert alert["if"] == "failure()"
        text = yaml.safe_dump(alert)
        assert "secrets." not in text and "vars." not in text and MINT not in text
        checkout, step = alert["steps"]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"persist-credentials": False}
        assert step["env"] == {
            "GH_TOKEN": "${{ github.token }}",
            "RUN": "${{ github.server_url }}/${{ github.repository }}/actions/runs/"
                   "${{ github.run_id }}",
            **{job.upper(): f"${{{{ needs.{job}.result }}}}" for job in alert["needs"]}}
        assert step["run"] == ('python3 -S -m tools.update.alert "${RUN}" ' + " ".join(
            f'"{job}=${{{job.upper()}}}"' for job in alert["needs"]))

    def test_each_keyed_job_runs_mains_code_and_hands_its_token_to_one_step(self):
        """Each keyed job, and no other, names the environment ``update``, whose secret the
        key is and which GitHub refuses a job on any ref but main (ruling plan#2472); each holds
        exactly its keys, so no job-level ``env`` or ``defaults`` reaches its steps; it checks
        out the workflow's own commit; the key goes to the mint alone, narrowed to the two
        permissions the App holds, no other step naming a secret by attribute or by index
        (``secrets['UPDATE_APP_KEY']``), and the token to the one step after it, which runs the
        job's module."""
        jobs = self._workflow()["jobs"]
        assert {name: job["environment"] for name, job in jobs.items()
                if "environment" in job} == dict.fromkeys(KEYED, "update")
        assert {name: set(jobs[name]) for name in KEYED} == {
            "publish": {"needs", "runs-on", "environment", "steps"},
            "queue": {"if", "runs-on", "environment", "steps"}}
        for name, module in KEYED.items():
            steps = jobs[name]["steps"]
            assert steps[0]["uses"].startswith("actions/checkout@")
            assert steps[0]["with"] == {"persist-credentials": False}, name
            mint, use = steps[-2], steps[-1]
            assert mint["uses"].startswith(MINT) and mint["id"] == "token", name
            assert mint["with"] == {
                "client-id": "${{ vars.UPDATE_APP_CLIENT_ID }}",
                "private-key": "${{ secrets.UPDATE_APP_KEY }}",
                "permission-contents": "write", "permission-pull-requests": "write"}, name
            assert use["env"]["UPDATE_TOKEN"] == "${{ steps.token.outputs.token }}", name
            assert use["run"].startswith(f"python -m {module}"), name
            for step in steps:
                if step is not mint:
                    assert not re.search(r"\bsecrets\b", yaml.safe_dump(step)), (name, step)
            for step in steps[:-2]:
                assert "steps.token" not in yaml.safe_dump(step), (name, step)

    def test_each_job_runs_only_from_main_on_the_schedule_its_condition_names(self):
        """The refresh on Monday's schedule or a dispatch, the queue on the daily one, each
        only from main, so a dispatch from a branch runs nothing (the environment, not these
        conditions, keeps the key from a branch's code); the publish only after the refresh;
        and one run at a time, each waiting for the one before, never cancelling it, so a
        dispatch never races Monday's publish, nor the queue a publish."""
        workflow = self._workflow()
        on = workflow.get(True, workflow.get("on"))
        monday, daily = (entry["cron"] for entry in on["schedule"])
        assert "workflow_dispatch" in on
        jobs = workflow["jobs"]
        main = "github.ref == 'refs/heads/main'"
        assert " ".join(jobs["refresh"]["if"].split()) == (
            f"{main} && (github.event_name == 'workflow_dispatch' || "
            f"github.event.schedule == '{monday}')")
        assert jobs["queue"]["if"] == f"{main} && github.event.schedule == '{daily}'"
        assert jobs["publish"]["needs"] == "refresh" and "if" not in jobs["publish"]
        assert workflow["concurrency"] == {"group": "updates", "cancel-in-progress": False}

    def test_the_publish_reads_what_the_refresh_wrote_from_the_commit_it_read(self):
        """The artifact the refresh uploads, which must hold a file, is the one the publish
        downloads, to the folder each command names; the commit it is made on is the one the
        refresh checked out."""
        jobs = self._workflow()["jobs"]
        refresh = {step["name"]: step for step in jobs["refresh"]["steps"]}
        publish = {step["name"]: step for step in jobs["publish"]["steps"]}
        upload = refresh["Upload what the refresh prepared"]["with"]
        download = publish["Download what the refresh prepared"]["with"]
        assert (upload["name"], upload["path"], upload["if-no-files-found"]) == (
            download["name"], download["path"], "error")
        assert upload["path"] == "${{ runner.temp }}/update"
        assert refresh["Refresh the lock"]["run"].startswith(
            'python -m tools.update.refresh "${RUNNER_TEMP}/update"\n')
        assert publish["Publish each change"]["run"] == (
            'python -m tools.update.publish "${RUNNER_TEMP}/update" "${BASE}"')
        assert publish["Publish each change"]["env"]["BASE"] == (
            "${{ needs.refresh.outputs.base }}")
        assert jobs["refresh"]["outputs"] == {"base": "${{ steps.checkout.outputs.commit }}"}
        assert jobs["refresh"]["steps"][0]["id"] == "checkout"
