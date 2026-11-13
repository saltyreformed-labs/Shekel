"""Controls for ``updates.yml``, the weekly dependency update (rulings plan#2448-plan#2450) and
the weekly image scan and its report (plan#2444): which job holds the update App's key, whose
code each runs, when each runs, and how long each job and step may run (rulings plan#2761,
plan#2762, plan#2765, plan#3721).
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PurePosixPath

import pytest
import yaml

from tools.ci import arcs
from tools.ci.test_build_image_action import USES as BUILD_IMAGE
from tools.ci.test_ci_workflow import _is_a_limit, _run_step, _workflows


#: The update workflow (rulings plan#2448-plan#2450), its jobs that hold the App's key, and the
#: module each of those runs.
UPDATES = "updates.yml"
KEYED = {"publish": "tools.update.publish", "queue": "tools.update.queue"}
#: GitHub's own action that mints an App's installation token.
MINT = "actions/create-github-app-token@"
#: What each update job's limit adds to all its steps' limits, for GitHub's setting the job up
#: (ruling plan#2762).
JOB_LIMIT_MARGIN = 5
#: The one job whose steps carry no limit of their own: the alert, as nothing reports a
#: stalled report (ruling plan#2761).
NO_STEP_LIMITS = "alert"
#: How a ``uses:`` names an action of this repository's own, which GitHub loads from the
#: workflow's commit (``$/``) or the checkout (``./``).
LOCAL_ACTION = ("$/", "./")
#: The scan's schedule's day of the week, as cron spells Tuesday (plan#2444's spec).
TUESDAY = "2"
#: The failed or reported run's page, as the alert and the scan's report each name it.
RUN = "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"


def _runs_an_outside_action(uses: str, root: Path = arcs.REPO) -> bool:
    """Whether a step that ``uses`` this action runs another repository's action, whose post
    step, if it has one, runs under the step's limit (ruling plan#2765): an action that is not
    this repository's, or one of this repository's, read from ``root``, that runs one in any of
    its steps, at any depth (ruling plan#3721).  An action of this repository's must be
    composite, whose steps are all it runs: one of another kind could carry a post step of its
    own, which this does not read."""
    if not uses.startswith(LOCAL_ACTION):
        return True
    folder = root / uses.removeprefix("$/").removeprefix("./")
    runs = yaml.safe_load(next(folder.glob("action.y*ml")).read_text(encoding="utf-8"))["runs"]
    assert runs["using"] == "composite", (uses, runs["using"])
    return any(_runs_an_outside_action(step["uses"], root)
               for step in runs["steps"] if "uses" in step)


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
        alert, the due job and the scan's report widens the workflow's read-only token, and each
        only to issues (rulings plan#2466, plan#2829, plan#3710)."""
        workflow = self._workflow()
        refresh = workflow["jobs"]["refresh"]
        text = yaml.safe_dump(refresh)
        assert "secrets." not in text and "vars." not in text and MINT not in text
        assert refresh["steps"][0]["with"] == {"ref": "dev", "persist-credentials": False}
        assert workflow["permissions"] == {"contents": "read"}
        assert {name: job["permissions"] for name, job in workflow["jobs"].items()
                if "permissions" in job} == dict.fromkeys(("alert", "due", "report"), {
                    "contents": "read", "issues": "write"})

    def test_a_failed_job_opens_an_issue_with_the_workflow_s_own_token(self):
        """The alert runs when the refresh, the publish, the queue, the due job, the scan or its
        report failed, a scan that cannot run or be judged among them (ruling plan#3710),
        holding no secret, variable or mint: main's own code, given the workflow's token and
        each job's result (ruling plan#2466), in two steps, the checkout and the alert, so it
        installs nothing and runs on the standard library alone (``-S``; plan#2481)."""
        alert = self._workflow()["jobs"]["alert"]
        assert alert["needs"] == ["refresh", "publish", "queue", "due", "scan", "report"]
        assert alert["if"] == "failure()"
        text = yaml.safe_dump(alert)
        assert "secrets." not in text and "vars." not in text and MINT not in text
        checkout, step = alert["steps"]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"persist-credentials": False}
        assert step["env"] == {
            "GH_TOKEN": "${{ github.token }}", "RUN": RUN,
            **{job.upper(): f"${{{{ needs.{job}.result }}}}" for job in alert["needs"]}}
        assert step["run"] == ('python3 -S -m tools.update.alert "${RUN}" ' + " ".join(
            f'"{job}=${{{job.upper()}}}"' for job in alert["needs"]))

    def test_the_due_job_holds_only_the_workflow_s_token(self):
        """The due job holds exactly its keys, so no job-level ``env`` or ``defaults`` reaches its
        steps, and no secret, variable, mint or environment (ruling plan#2829): main's own code,
        which fetches the two tips itself with no credential, with the landing group, which
        quill's door imports, and the workflow's token to its one step."""
        due = self._workflow()["jobs"]["due"]
        assert set(due) == {"if", "runs-on", "timeout-minutes", "permissions", "steps"}
        text = yaml.safe_dump(due)
        assert "secrets." not in text and "vars." not in text and MINT not in text
        checkout, install, step = due["steps"]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"persist-credentials": False}
        assert install["with"] == {"install": "landing"} and "env" not in install
        assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
        assert step["run"] == "python -m tools.update.due"

    def test_each_keyed_job_runs_mains_code_and_hands_its_token_to_one_step(self):
        """Each keyed job, and no other, names the environment ``update``, whose secret the
        key is and which GitHub refuses a job on any ref but main (ruling plan#2472); each holds
        exactly its keys, so no job-level ``env`` or ``defaults`` reaches its steps; it checks
        out the workflow's own commit; the key goes to the mint alone, narrowed to the two
        permissions each job was measured to need, Workflows never among them (``updates.yml``,
        THE KEY; rulings plan#2477, plan#2807), no other step naming a secret by attribute or by
        index (``secrets['UPDATE_APP_KEY']``), and the token to the one step after it, which runs
        the job's module."""
        jobs = self._workflow()["jobs"]
        assert {name: job["environment"] for name, job in jobs.items()
                if "environment" in job} == dict.fromkeys(KEYED, "update")
        assert {name: set(jobs[name]) for name in KEYED} == {
            "publish": {"needs", "runs-on", "timeout-minutes", "environment", "steps"},
            "queue": {"if", "runs-on", "timeout-minutes", "environment", "steps"}}
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
        """The refresh on Monday's schedule or a dispatch, the queue and the due job on the
        daily one, the scan on Tuesday's (plan#2444), each only from main, so a dispatch from a
        branch runs nothing (the environment, not these conditions, keeps the key from a
        branch's code); the publish only after the refresh, and the scan's report only after
        the scan, which skips it on any other day; and one run at a time, each waiting
        for the one before, never cancelling it, so a dispatch never races Monday's publish,
        nor the queue a publish."""
        workflow = self._workflow()
        on = workflow.get(True, workflow.get("on"))
        monday, daily, tuesday = (entry["cron"] for entry in on["schedule"])
        assert tuesday.split()[2:] == ["*", "*", TUESDAY], tuesday
        assert "workflow_dispatch" in on
        jobs = workflow["jobs"]
        main = "github.ref == 'refs/heads/main'"
        assert " ".join(jobs["refresh"]["if"].split()) == (
            f"{main} && (github.event_name == 'workflow_dispatch' || "
            f"github.event.schedule == '{monday}')")
        for name in ("queue", "due"):
            assert jobs[name]["if"] == f"{main} && github.event.schedule == '{daily}'", name
        assert jobs["scan"]["if"] == f"{main} && github.event.schedule == '{tuesday}'"
        assert jobs["publish"]["needs"] == "refresh" and "if" not in jobs["publish"]
        assert jobs["report"]["needs"] == "scan" and "if" not in jobs["report"]
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


class TestTheScanReadsDevsImageWithNoSecret:
    """``updates.yml``'s ``scan`` (plan#2444): dev's image built by the one recipe, saved as a
    file and read by the scanner's compose service, its JSON uploaded for the report, the job
    holding no secret and only the workflow's read-only token, as its build runs dev's package
    code."""

    #: The step that saves the image and runs the scanner on it.
    SCAN_STEP = "Save the image and scan it"

    @staticmethod
    def _scan() -> dict:
        """The scan job, read."""
        return _workflows()[UPDATES]["jobs"]["scan"]

    def test_it_builds_dev_s_image_by_the_one_recipe_and_sees_no_secret(self):
        """The job holds exactly its keys, so no permission, environment or ``needs``, and
        names no secret, variable, mint or token; its four steps, in order, set up no builder
        (ruling plan#2742).  It checks out dev keeping no credential, builds by the one recipe,
        loaded under the job's image name and never pushed (ruling plan#2752), runs the scanner
        in one step, and uploads, as the artifact ``scan``, the file that step's output names,
        failing when there is none."""
        scan = self._scan()
        assert set(scan) == {"if", "runs-on", "timeout-minutes", "env", "steps"}
        assert scan["env"] == {"IMAGE": "shekel:scan"}
        text = yaml.safe_dump(scan)
        assert "secrets." not in text and "vars." not in text and MINT not in text
        assert not re.search(r"\bsecrets\b|\btoken\b", text)
        checkout, build, step, upload = scan["steps"]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"ref": "dev", "persist-credentials": False}
        assert set(build) == {"name", "timeout-minutes", "uses", "with"}
        assert build["uses"] == BUILD_IMAGE
        assert build["with"] == {"push": False, "load": True, "tags": "${{ env.IMAGE }}"}
        assert step["name"] == self.SCAN_STEP and step["id"] == "scan"
        assert upload["uses"].startswith("actions/upload-artifact@")
        assert upload["with"] == {"name": "scan", "path": "${{ steps.scan.outputs.json }}",
                                  "if-no-files-found": "error", "retention-days": 7}

    def test_the_step_hands_the_scanner_the_file_it_reads_and_names_the_json_written(
            self, tmp_path):
        """The step's text, run as GitHub runs it (``shell: bash``), with a stand-in ``docker``
        that writes a saved image readable by its owner alone, as ``docker save`` does: the
        job's image is saved to the file the scanner's compose file binds (its
        ``${VARIABLE:?...}``), made readable by all, as the scanner runs as uid 65532; the
        scanner's service runs from its compose file with no TTY, its standard output alone the
        JSON, whose file the step's output names.  A scanner that exits 1 fails the step, which
        then names no JSON for the upload to find."""
        step = next(step for step in self._scan()["steps"] if step["name"] == self.SCAN_STEP)
        assert set(step) == {"name", "id", "timeout-minutes", "shell", "env", "run"}
        assert step["shell"] == "bash"
        compose = (arcs.REPO / "docker-compose.scan.yml").read_text(encoding="utf-8")
        variable, = re.findall(r"\$\{(\w+):\?", compose)
        assert variable in step["env"], (variable, step["env"])
        files = {name: value.replace("${{ runner.temp }}", str(tmp_path))
                 for name, value in step["env"].items()}
        assert all(value.startswith(f"{tmp_path}/") for value in files.values()), files
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "${CALLS}"\n'
            'case "$1" in\n'
            '  save) : > "$3"; chmod 0600 "$3" ;;\n'
            '  compose) echo "{\\"matches\\": []}"; echo "progress" >&2; exit "${EXIT}" ;;\n'
            "esac\n", encoding="utf-8")
        docker.chmod(0o755)
        for exit_status in (0, 1):
            calls, output = tmp_path / f"calls{exit_status}", tmp_path / f"output{exit_status}"
            output.write_text("", encoding="utf-8")
            ran = _run_step(step["run"], {
                **files, "PATH": f"{bin_dir}:/usr/bin:/bin", "IMAGE": "shekel:scan",
                "GITHUB_OUTPUT": str(output), "CALLS": str(calls), "EXIT": str(exit_status)},
                cwd=tmp_path)
            assert ran.returncode == exit_status, ran
            assert calls.read_text(encoding="utf-8").splitlines() == [
                f"save --output {files[variable]} shekel:scan",
                "compose -f docker-compose.scan.yml run --rm --no-TTY scan"]
            assert stat.S_IMODE(os.stat(files[variable]).st_mode) == 0o644
            json_file, = (value for name, value in files.items() if name != variable)
            assert Path(json_file).read_text(encoding="utf-8") == '{"matches": []}\n'
            assert output.read_text(encoding="utf-8") == (
                "" if exit_status else f"json={json_file}\n")


class TestTheScansReportReadsItsJsonAsData:
    """``updates.yml``'s ``report`` (rulings plan#3710, plan#3720): after the scan, main's own
    code judges the JSON the scan uploaded, read as data, on the lock's interpreter and the
    standard library alone, with only the workflow's token, widened to issues."""

    @staticmethod
    def _jobs() -> dict:
        """The update workflow's jobs, read."""
        return _workflows()[UPDATES]["jobs"]

    def test_it_runs_mains_code_on_the_standard_library_with_the_workflow_s_token(self):
        """The job holds exactly its keys, so no job-level ``env`` or ``defaults`` reaches its
        steps, and names no secret, variable or mint; in four steps it checks out the
        workflow's own commit keeping no credential, installs the lock's interpreter and no
        package, downloads the scan's artifact and runs the report, handed the token and the
        run's page."""
        report = self._jobs()["report"]
        assert set(report) == {"needs", "runs-on", "timeout-minutes", "permissions", "steps"}
        text = yaml.safe_dump(report)
        assert "secrets." not in text and "vars." not in text and MINT not in text
        checkout, install, download, step = report["steps"]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"persist-credentials": False}
        assert install["with"] == {"install": "python"} and "env" not in install
        assert download["uses"].startswith("actions/download-artifact@")
        assert set(step) == {"name", "timeout-minutes", "env", "run"}
        assert step["env"] == {"GH_TOKEN": "${{ github.token }}", "RUN": RUN}
        assert step["run"].startswith("python -m tools.update.scan ")

    def test_it_reads_the_one_file_the_scan_uploaded(self):
        """The artifact the scan uploads is the one the report downloads, and the file it reads
        is the one the scan wrote: its one file, which the upload puts at the artifact's root by
        its name, in the folder the download names."""
        jobs = self._jobs()
        scan = {step["name"]: step for step in jobs["scan"]["steps"]}
        report = {step["name"]: step for step in jobs["report"]["steps"]}
        upload = scan["Upload the scanner's JSON"]["with"]
        download = report["Download the scanner's JSON"]["with"]
        assert upload["path"] == "${{ steps.scan.outputs.json }}"
        assert download == {"name": upload["name"], "path": "${{ runner.temp }}/scan"}
        written = PurePosixPath(scan[TestTheScanReadsDevsImageWithNoSecret.SCAN_STEP]["env"][
            "SCAN_JSON"])
        assert written.parent == PurePosixPath("${{ runner.temp }}")
        folder = download["path"].replace("${{ runner.temp }}", "${RUNNER_TEMP}")
        assert report["Open or close the scan's issue"]["run"] == (
            f'python -m tools.update.scan "{folder}/{written.name}" "${{RUN}}"')


class TestAStalledUpdateRunFailsLoudly:
    """``updates.yml``'s time limits (rulings plan#2761, plan#2762, plan#2765, plan#3721): a
    stalled step fails its job, which the alert reports, before its job's own limit could
    cancel it in silence."""

    def test_each_step_s_limit_cuts_it_off_before_its_job_s_limit_could(self):
        """Every job carries a limit (:func:`tools.ci.test_ci_workflow._is_a_limit`), and every
        step of each job but :data:`NO_STEP_LIMITS` one of its own, whose steps carry none; the
        job's limit is its steps' total, a step that runs another repository's action
        (:func:`_runs_an_outside_action`) counted twice, plus :data:`JOB_LIMIT_MARGIN`.

        A step cut off by its own limit fails, so its job fails and the alert, which runs on
        ``failure()``, opens its issue; a job cut off by its own limit is cancelled, which
        ``failure()`` never sees.  An action's post step, if it has one, runs under its step's
        limit (actions/runner's ActionRunner.cs), so a job's limit no longer than all of these
        could cut a stalled step off before the step's own did, ending the run in silence.  How
        long each step's limit is, re-sized from the workflow's own runs by plan#2763, is not
        graded here.
        """
        jobs = _workflows()[UPDATES]["jobs"]
        assert NO_STEP_LIMITS in jobs
        for name, job in jobs.items():
            limit = job.get("timeout-minutes")
            assert _is_a_limit(limit), (name, limit)
            limits = [step.get("timeout-minutes") for step in job["steps"]]
            if name == NO_STEP_LIMITS:
                assert limits == [None] * len(limits), (name, limits)
                continue
            assert all(_is_a_limit(step) for step in limits), (name, limits)
            posts = [step["timeout-minutes"] for step in job["steps"]
                     if "uses" in step and _runs_an_outside_action(step["uses"])]
            assert limit == sum(limits) + sum(posts) + JOB_LIMIT_MARGIN, (name, limit, limits)

    def test_a_step_counts_twice_when_it_runs_another_repository_s_action_at_any_depth(
            self, tmp_path):
        """:func:`_runs_an_outside_action`, the rule the limits above are read by (rulings
        plan#2765, plan#3721): an action of another repository's counts twice, and one of this
        repository's counts twice when any of its steps runs one, by either local form, at any
        depth, and once when none does.  Read on this repository's actions as they stand
        (``build-image`` runs ``docker/build-push-action``; ``install-lock`` runs only
        ``dockerfile-uv``, which runs none) and on a tree of actions three deep; an action of
        this repository's that is not composite is refused, not read."""
        assert _runs_an_outside_action("actions/checkout@v9")
        assert _runs_an_outside_action("$/.github/actions/build-image")
        assert not _runs_an_outside_action("$/.github/actions/install-lock")
        assert not _runs_an_outside_action("$/.github/actions/dockerfile-uv")

        def action(name: str, runs: dict) -> None:
            folder = tmp_path / ".github/actions" / name
            folder.mkdir(parents=True)
            (folder / "action.yml").write_text(yaml.safe_dump({"runs": runs}),
                                               encoding="utf-8")

        def composite(*uses: str) -> dict:
            return {"using": "composite",
                    "steps": [{"run": "true", "shell": "bash"}, *({"uses": u} for u in uses)]}

        action("leaf", composite())
        action("outside", composite("docker/build-push-action@v9"))
        action("middle", composite("./.github/actions/leaf", "$/.github/actions/outside"))
        action("top", composite("$/.github/actions/middle"))
        action("quiet", composite("$/.github/actions/leaf", "./.github/actions/leaf"))
        action("node", {"using": "node24", "main": "main.js", "post": "post.js"})
        action("calls-node", composite("$/.github/actions/node"))
        for name, twice in (("leaf", False), ("outside", True), ("middle", True),
                            ("top", True), ("quiet", False)):
            for form in LOCAL_ACTION:
                uses = f"{form}.github/actions/{name}"
                assert _runs_an_outside_action(uses, tmp_path) is twice, uses
        for name in ("node", "calls-node"):
            with pytest.raises(AssertionError, match="node24"):
                _runs_an_outside_action(f"$/.github/actions/{name}", tmp_path)

    def test_no_update_job_or_step_may_fail_without_failing(self):
        """``continue-on-error`` would leave a job green after a step cut off by its own limit,
        so the alert would never run (ruling plan#2761)."""
        for name, job in _workflows()[UPDATES]["jobs"].items():
            assert "continue-on-error" not in job, name
            for step in job["steps"]:
                assert "continue-on-error" not in step, (name, step["name"])
