"""A failed run of the weekly update, told to the developer (:mod:`tools.update.alert`, ruling
plan#2466): one issue that mentions him, links the run and names each job's result, sent by the
runner's ``gh`` from a module that imports the standard library alone (plan#2481).  ``gh`` is
faked as a recorder of what it is handed; what the real one does with it is measured in the
module's notes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date

import pytest

from tools.ci.arcs import REPO
from tools.update import alert
from tools.update.app import ACTIONS, DEVELOPER

_RUN = "https://github.com/owner/shekel/actions/runs/7"
_DAY = date(2026, 11, 2)
_LINK = "https://github.com/owner/shekel/issues/9"


class _Send:
    """The alert's sender as :func:`alert.opened` calls it: each issue recorded, answered as
    ``gh`` exits ``status`` with ``stdout`` and ``stderr``."""

    def __init__(self, status=0, stdout="", stderr=""):
        """Hold the answer to give each send."""
        self.answer = (status, stdout, stderr)
        self.sent = []

    def __call__(self, repository, body):
        """Record the send; answer it."""
        self.sent.append((repository, body))
        status, stdout, stderr = self.answer
        return subprocess.CompletedProcess(["gh"], status, stdout, stderr)


def test_the_issue_mentions_the_developer_links_the_run_and_names_each_job():
    """Its title is the day; its body opens with the mention and the run, then each job."""
    assert alert.issue(_DAY, _RUN, [("refresh", "success"), ("publish", "failure")]) == {
        "title": "The weekly update failed 2026-11-02",
        "body": f"@{DEVELOPER} the weekly update's run failed: {_RUN}\n\n"
                "- refresh: success\n- publish: failure\n\n"
                "Close this issue once what failed is fixed (ruling plan#2466)."}


def test_one_issue_is_opened_in_the_repository_and_its_link_printed(capsys):
    """One send of the issue to the repository, and the link ``gh`` prints printed."""
    send = _Send(stdout=_LINK + "\n")
    assert alert.opened(send, "owner/shekel", _DAY, _RUN, [("queue", "failure")]) == 0
    assert send.sent == [("owner/shekel", alert.issue(_DAY, _RUN, [("queue", "failure")]))]
    assert capsys.readouterr().out == f"update alert: opened {_LINK}\n"


def test_a_refused_issue_exits_1_saying_so(capsys):
    """GitHub refusing the write (a token not widened to issues) is a failed job, its reason
    the one ``gh`` gives."""
    send = _Send(status=1, stdout='{"message":"Resource not accessible by integration"}',
                 stderr="gh: Resource not accessible by integration (HTTP 403)\n")
    assert alert.opened(send, "owner/shekel", _DAY, _RUN, [("refresh", "failure")]) == 1
    assert capsys.readouterr() == ("", "update alert: no issue was opened: gh: Resource not "
                                       "accessible by integration (HTTP 403)\n")


def test_gh_is_handed_the_issue_on_its_input_and_prints_the_link(tmp_path, monkeypatch):
    """The real sender runs the ``gh`` first on PATH as one POST to the repository's issues,
    the issue's JSON on its input, and answers what it printed."""
    gh = tmp_path / "gh"
    gh.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "$0.args"\ncat > "$0.input"\necho {_LINK}\n',
                  encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    body = alert.issue(_DAY, _RUN, [("queue", "failure")])
    done = alert.sent("owner/shekel", body)
    assert (done.returncode, done.stdout, done.stderr) == (0, _LINK + "\n", "")
    assert (tmp_path / "gh.args").read_text(encoding="utf-8").splitlines() == [
        "api", "repos/owner/shekel/issues", "--method", "POST", "--input", "-",
        "--jq", ".html_url"]
    assert json.loads((tmp_path / "gh.input").read_text(encoding="utf-8")) == body


def test_gh_is_handed_a_change_to_an_issue_by_its_number(tmp_path, monkeypatch):
    """Given an issue's number, the real sender runs one PATCH of that issue, the JSON on its
    input, and answers what it printed (the due job's write, ruling plan#2829)."""
    gh = tmp_path / "gh"
    gh.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "$0.args"\ncat > "$0.input"\necho {_LINK}\n',
                  encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    done = alert.sent("owner/shekel", {"body": "a list"}, 9)
    assert (done.returncode, done.stdout, done.stderr) == (0, _LINK + "\n", "")
    assert (tmp_path / "gh.args").read_text(encoding="utf-8").splitlines() == [
        "api", "repos/owner/shekel/issues/9", "--method", "PATCH", "--input", "-",
        "--jq", ".html_url"]
    assert json.loads((tmp_path / "gh.input").read_text(encoding="utf-8")) == {"body": "a list"}


def test_gh_is_asked_for_every_page_of_open_issues(tmp_path, monkeypatch):
    """The real listing is one ``gh api`` read of the repository's open issues, every page, each
    a line, answered as ``gh`` printed it (the due job's read and the scan's report's)."""
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$0.args"\necho \'{"number": 9}\'\n',
                  encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    done = alert.listed("owner/shekel")
    assert (done.returncode, done.stdout, done.stderr) == (0, '{"number": 9}\n', "")
    assert (tmp_path / "gh.args").read_text(encoding="utf-8").splitlines() == [
        "api", "repos/owner/shekel/issues?state=open&per_page=100", "--paginate", "--jq",
        ".[] | {number, title, body, by: .user.login}"]


def test_the_workflow_s_issues_are_those_its_own_token_opened():
    """Of a listing, each issue the workflow's bot opened, as REST names it, in its order, and
    none a person opened, whatever the title or a login that spells the bot's but its
    ``[bot]``; only a newline ends a line, so a title's other line breaks split none."""
    issues = [{"number": 3, "title": "a\u2028b\x85", "body": None, "by": alert.BOT},
              {"number": 4, "title": "The weekly update failed 2026-11-02", "body": "",
               "by": ACTIONS},
              {"number": 5, "title": "x", "body": "", "by": "a-stranger"},
              {"number": 9, "title": "y", "body": "z", "by": alert.BOT}]
    listing = "".join(json.dumps(issue, ensure_ascii=False) + "\n" for issue in issues)
    assert alert.BOT == f"{ACTIONS}[bot]"
    assert alert.the_workflows(listing) == [issues[0], issues[3]]
    assert not alert.the_workflows("")


def test_the_module_runs_with_no_package_installed():
    """Run as its job runs it, by an interpreter given no installed package (``-S``; ``-E``
    so no variable of this run's puts one back), the module imports and answers: a failed
    install of the lock cannot also stop the alert (plan#2481)."""
    done = subprocess.run([sys.executable, "-E", "-S", "-m", alert.__name__, "--help"],
                          cwd=REPO, capture_output=True, text=True, check=False)
    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout.startswith("usage: python3 -S -m tools.update.alert ")


def test_main_exits_1_naming_each_variable_unset(monkeypatch, capsys):
    """No token or no repository opens nothing, naming each missing."""
    monkeypatch.delenv(alert.ISSUE_TOKEN_VARIABLE, raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert alert.main([_RUN, "refresh=failure"]) == 1
    assert capsys.readouterr().err == (
        "update alert: GH_TOKEN, GITHUB_REPOSITORY unset, so no issue was opened\n")


def test_main_opens_the_issue_for_the_day_and_results_the_command_line_names(monkeypatch):
    """The repository from the environment, sent by ``gh``, the day from its clock, each result
    as the workflow passes it."""
    monkeypatch.setenv(alert.ISSUE_TOKEN_VARIABLE, "t0k")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/shekel")
    calls = []
    monkeypatch.setattr(alert, "opened", lambda *args: calls.append(args) or 0)
    assert alert.main([_RUN, "refresh=success", "publish=failure", "queue=skipped"],
                      today=lambda: _DAY) == 0
    assert calls == [(alert.sent, "owner/shekel", _DAY, _RUN,
                      [("refresh", "success"), ("publish", "failure"), ("queue", "skipped")])]


@pytest.mark.parametrize("given", ["refresh", "=failure", "refresh="])
def test_a_result_that_is_no_job_and_result_is_refused(given, capsys):
    """Each result is ``<job>=<result>``, both named."""
    with pytest.raises(SystemExit) as raised:
        alert.main([_RUN, given])
    assert raised.value.code == 2
    assert "is no <job>=<result>" in capsys.readouterr().err
