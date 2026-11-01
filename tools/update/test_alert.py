"""A failed run of the weekly update, told to the developer (:mod:`tools.update.alert`, ruling
plan#2466): one issue that mentions him, links the run and names each job's result.  Quill's
client is faked as a recorder of each REST call; its own sending is quill's tests'."""
from __future__ import annotations

from datetime import date

import pytest

from tools.quill.door import GitHubError
from tools.update import alert
from tools.update.app import DEVELOPER

_RUN = "https://github.com/owner/shekel/actions/runs/7"
_DAY = date(2026, 11, 2)


class _GitHub:
    """Quill's client as the alert calls it: each REST call recorded, answered ``answer`` or
    refused with ``refusal``."""

    def __init__(self, answer=None, refusal=None):
        """Hold the answer, or the refusal, to give each call."""
        self.answer, self.refusal = answer, refusal
        self.sent = []

    def rest(self, method, path, body=None):
        """Record the call; answer or refuse it."""
        self.sent.append((method, path, body))
        if self.refusal is not None:
            raise self.refusal
        return self.answer


def test_the_issue_mentions_the_developer_links_the_run_and_names_each_job():
    """Its title is the day; its body opens with the mention and the run, then each job."""
    assert alert.issue(_DAY, _RUN, [("refresh", "success"), ("publish", "failure")]) == {
        "title": "The weekly update failed 2026-11-02",
        "body": f"@{DEVELOPER} the weekly update's run failed: {_RUN}\n\n"
                "- refresh: success\n- publish: failure\n\n"
                "Close this issue once what failed is fixed (ruling plan#2466)."}


def test_one_issue_is_opened_in_the_repository_and_its_link_printed(capsys):
    """One POST to the repository's issues, and the link GitHub answers printed."""
    github = _GitHub(answer={"html_url": "https://github.com/owner/shekel/issues/9"})
    assert alert.opened(github, "owner/shekel", _DAY, _RUN, [("queue", "failure")]) == 0
    assert github.sent == [("POST", "/repos/owner/shekel/issues",
                            alert.issue(_DAY, _RUN, [("queue", "failure")]))]
    assert capsys.readouterr().out == (
        "update alert: opened https://github.com/owner/shekel/issues/9\n")


def test_a_refused_issue_exits_1_saying_so(capsys):
    """GitHub refusing the write (a token not widened to issues) is a failed job."""
    github = _GitHub(refusal=GitHubError(403, "POST /repos/owner/shekel/issues -> 403: "
                                              "Resource not accessible by integration"))
    assert alert.opened(github, "owner/shekel", _DAY, _RUN, [("refresh", "failure")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("update alert: no issue was opened: ")
    assert "Resource not accessible by integration" in err


def test_main_exits_1_naming_each_variable_unset(monkeypatch, capsys):
    """No token or no repository opens nothing, naming each missing."""
    monkeypatch.delenv(alert.ISSUE_TOKEN_VARIABLE, raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert alert.main([_RUN, "refresh=failure"]) == 1
    assert capsys.readouterr().err == (
        "update alert: GITHUB_TOKEN, GITHUB_REPOSITORY unset, so no issue was opened\n")


def test_main_opens_the_issue_for_the_day_and_results_the_command_line_names(monkeypatch):
    """The token and repository from the environment, the day from its clock, each result as
    the workflow passes it."""
    monkeypatch.setenv(alert.ISSUE_TOKEN_VARIABLE, "t0k")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/shekel")
    calls = []
    monkeypatch.setattr(alert, "opened", lambda *args: calls.append(args[1:]) or 0)
    assert alert.main([_RUN, "refresh=success", "publish=failure", "queue=skipped"],
                      today=lambda: _DAY) == 0
    assert calls == [("owner/shekel", _DAY, _RUN,
                      [("refresh", "success"), ("publish", "failure"), ("queue", "skipped")])]


@pytest.mark.parametrize("given", ["refresh", "=failure", "refresh="])
def test_a_result_that_is_no_job_and_result_is_refused(given, capsys):
    """Each result is ``<job>=<result>``, both named."""
    with pytest.raises(SystemExit) as raised:
        alert.main([_RUN, given])
    assert raised.value.code == 2
    assert "is no <job>=<result>" in capsys.readouterr().err
