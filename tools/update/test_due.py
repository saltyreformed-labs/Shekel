"""A release due, told to the developer (:mod:`tools.update.due`, ruling plan#2829): one issue,
found by its exact title each run, opened mentioning him, following the list, closed once
nothing waits.  ``gh`` is faked, as a recorder of what it is handed or a program on PATH; what
the real one does with a PATCH is not measured until the job runs on ``main``.  The versions
are read from throwaway repositories by the one walk."""
from __future__ import annotations

import json

import pytest

from tools.ci.scratch import commit, commit_files, point_main, repository, with_origin
from tools.ci.scratch import run as _scratch
from tools.quill.door import Moved
from tools.update import due, fake_gh
from tools.update.alert import BOT
from tools.update.app import DEVELOPER
from tools.update.fake_gh import LINK as _LINK
from tools.update.fake_gh import REPOSITORY as _REPOSITORY

_MOVED = (Moved("docker-compose.yml", "postgres", (f"18-alpine@sha256:{'a' * 64}",),
                (f"18.7-alpine@sha256:{'b' * 64}",)),
          Moved("uv.lock", "flask", ("3.1.0",), ("3.1.1",)))
_LATER = (*_MOVED, Moved("uv.lock", "zeta", ("0.6.3",), ("0.6.4",)))
#: The issue's text naming :data:`_MOVED`, spelled out once here, so a change to what the
#: developer reads fails this file.
_TEXT = ("A release of `dev` would move these versions, from `main`'s to `dev`'s:\n\n"
         f"- `docker-compose.yml: postgres 18-alpine@sha256:{'a' * 64} -> "
         f"18.7-alpine@sha256:{'b' * 64}`\n"
         "- `uv.lock: flask 3.1.0 -> 3.1.1`\n\n"
         "This list follows `dev` each day, and this issue closes once a release carries them "
         "(ruling plan#2829).")
_OPENED = f"@{DEVELOPER} {_TEXT}"


def _issue(number=9, title=due.TITLE, body=_OPENED, by=BOT):
    """One line of the listing, as :func:`tools.update.alert.listed`'s ``--jq`` prints it, any
    character but a
    newline as itself."""
    return json.dumps({"number": number, "title": title, "body": body, "by": by},
                      ensure_ascii=False)


class _Gh(fake_gh.Issues):
    """The module's two calls of ``gh`` faked (:class:`tools.update.fake_gh.Issues`), as
    :func:`due.run` makes them."""

    def run(self, versions):
        """:func:`due.run` over this fake, its exit status."""
        return due.run(_REPOSITORY, versions, listing=self.listing, send=self.send)


def test_versions_waiting_and_none_open_opens_one_issue_mentioning_him(capsys):
    """The issue's title is the exact one read back, and its text mentions him, then lists
    each version, each in a code span, so an image's ``@sha256`` reads as no mention."""
    gh = _Gh()
    assert gh.run(_MOVED) == 0
    assert gh.written == [(None, {"title": due.TITLE, "body": _OPENED})]
    assert capsys.readouterr().out == (f"update due: wrote {_LINK}\n"
                                       "update due: versions waiting for a release: 2\n")


def test_versions_waiting_and_one_open_follows_the_list_with_no_new_mention():
    """The list moved: the open issue's text follows it, and that text mentions no one, so the
    edit cannot be a new mention.  No second issue is opened."""
    gh = _Gh(_issue())
    assert gh.run(_LATER) == 0
    assert len(gh.written) == 1
    number, body = gh.written[0]
    assert number == 9 and set(body) == {"body"}
    assert "- `uv.lock: zeta 0.6.3 -> 0.6.4`\n" in body["body"]
    assert body["body"].startswith("A release of") and f"@{DEVELOPER}" not in body["body"]


@pytest.mark.parametrize("body", [_OPENED, _TEXT], ids=["as_opened", "as_edited"])
def test_a_list_that_has_not_moved_writes_nothing(body):
    """Each later day, with the list as the issue holds it, whether it holds the text it opened
    with or one an edit wrote: no write, so nothing a daily run could tell him."""
    gh = _Gh(_issue(body=body))
    assert gh.run(_MOVED) == 0
    assert not gh.written


def test_nothing_waiting_and_one_open_closes_it():
    """A release carried them: the open issue closes, its reason completed."""
    gh = _Gh(_issue())
    assert gh.run(()) == 0
    assert gh.written == [(9, {"state": "closed", "state_reason": "completed"})]


def test_nothing_waiting_and_none_open_writes_nothing(capsys):
    """No version waits and no issue is open: nothing is written."""
    gh = _Gh()
    assert gh.run(()) == 0
    assert not gh.written
    assert capsys.readouterr().out == "update due: versions waiting for a release: 0\n"


@pytest.mark.parametrize("other", [
    {"by": "a-stranger"},
    {"title": f"{due.TITLE}."},
    {"title": "The weekly update failed 2026-11-02"},
], ids=["a_stranger_s_issue_of_the_title", "a_longer_title", "the_alert_s_issue"])
def test_an_issue_of_any_other_title_or_author_is_not_its_own(other):
    """The repository is public: a stranger's issue of the exact title stops no mention and is
    neither edited nor closed, and the alert's own issue, by the same token, is left."""
    gh = _Gh(_issue(number=5, **other))
    assert gh.run(_MOVED) == 0
    assert gh.written == [(None, {"title": due.TITLE, "body": _OPENED})]
    closing = _Gh(_issue(number=5, **other))
    assert closing.run(()) == 0
    assert not closing.written


def test_its_own_issue_with_no_text_follows_the_list():
    """GitHub gives an issue whose text a person cleared a null body: it reads as stale, and the
    listing's control characters split no line, since only a newline ends one."""
    gh = _Gh(_issue(body=None), _issue(number=4, title="another\u2028title\x85"))
    assert gh.run(_MOVED) == 0
    assert gh.written == [(9, {"body": _TEXT})]


def test_two_of_its_own_open_are_each_followed_or_closed():
    """One a person reopened beside one the job opened: neither is left stale."""
    followed = _Gh(_issue(number=3, body="an old list"), _issue(number=9))
    assert followed.run(_MOVED) == 0
    assert followed.written == [(3, {"body": _TEXT})]
    closed = _Gh(_issue(number=3, body="an old list"), _issue(number=9))
    assert closed.run(()) == 0
    assert [number for number, _ in closed.written] == [3, 9]


def test_a_listing_gh_refuses_writes_nothing_and_exits_1(capsys):
    """No listing, no knowing what is open, so a write could be a second mention."""
    gh = _Gh(listing_status=1)
    assert gh.run(_MOVED) == 1
    assert not gh.written
    assert capsys.readouterr().err == (
        "update due: no issue was read: gh: Bad credentials (HTTP 401)\n")


def test_a_write_gh_refuses_exits_1_each_other_still_tried(capsys):
    """A write GitHub refuses fails the job, which the alert reports; the next is still tried."""
    gh = _Gh(_issue(number=3), _issue(number=9), refused=(3,))
    assert gh.run(()) == 1
    assert [number for number, _ in gh.written] == [3, 9]
    assert capsys.readouterr().err == (
        "update due: an issue was not written: gh: refused (HTTP 403)\n")


@pytest.fixture(name="code")
def _code(tmp_path):
    """A throwaway repository, in a folder of its own beside what the fake ``gh`` records."""
    return repository(tmp_path, "git")


def _branches(code, main, dev):
    """A bare ``origin`` beside ``code`` whose ``main`` holds the files ``main`` and whose ``dev``
    holds them with the changes ``dev``, both fetched; ``main``'s commit and ``dev``'s."""
    released = commit_files(code, commit(code, "start"), "main", main)
    with_origin(code, released)
    moved = commit_files(code, released, "dev", dev)
    _scratch(code, "push", "--quiet", "origin", f"{moved}:refs/heads/dev")
    return released, moved


def _lock(version):
    """A lock holding flask at ``version``."""
    return (f'version = 1\n\n[[package]]\nname = "flask"\nversion = "{version}"\n'
            'source = { registry = "https://pypi.org/simple" }\n')


def test_a_version_dev_moved_opens_the_issue_end_to_end(code, tmp_path, monkeypatch, capsys):
    """Over a real repository, the real walk, the real senders and a ``gh`` on PATH: ``dev``
    moved flask, so one POST of the issue, listing it, and the job's exit is 0."""
    fake_gh.on_path(tmp_path, monkeypatch)
    _branches(code, {"uv.lock": _lock("3.1.0")}, {"uv.lock": _lock("3.1.1")})
    assert due.main([], root=code) == 0
    calls = fake_gh.calls(tmp_path)
    assert len(calls) == 2
    post = next(body for args, body in calls if args[1] == "repos/owner/shekel/issues")
    assert post["title"] == due.TITLE
    assert post["body"].startswith(f"@{DEVELOPER} A release of `dev` would move these versions")
    assert "- `uv.lock: flask 3.1.0 -> 3.1.1`\n" in post["body"]
    assert capsys.readouterr().out.endswith("update due: versions waiting for a release: 1\n")


def test_nothing_moved_closes_an_open_issue_end_to_end(code, tmp_path, monkeypatch):
    """``dev`` changed only the app: nothing waits, so the issue the listing holds is closed,
    by a PATCH of its number."""
    fake_gh.on_path(tmp_path, monkeypatch, listing=_issue() + "\n")
    _branches(code, {"uv.lock": _lock("3.1.0")}, {"app.py": "1"})
    assert due.main([], root=code) == 0
    assert [(args[1:4], body) for args, body in fake_gh.calls(tmp_path) if body is not None] == [
        (["repos/owner/shekel/issues/9", "--method", "PATCH"],
         {"state": "closed", "state_reason": "completed"})]


def test_the_tips_are_read_from_origin_not_as_the_checkout_left_them(
        code, tmp_path, monkeypatch):
    """A re-run after a release: the checkout wrote ``origin/main`` as the run's old commit and
    no ``origin/dev`` at all, but ``main`` on ``origin`` holds what ``dev`` holds.  Nothing waits,
    so no false mention opens."""
    fake_gh.on_path(tmp_path, monkeypatch)
    released, moved = _branches(code, {"uv.lock": _lock("3.1.0")}, {"uv.lock": _lock("3.1.1")})
    _scratch(code, "push", "--quiet", "origin", f"{moved}:refs/heads/main")
    point_main(code, released)
    _scratch(code, "update-ref", "-d", "refs/remotes/origin/dev")
    assert due.main([], root=code) == 0
    assert all(body is None for _, body in fake_gh.calls(tmp_path))


def test_a_read_git_cannot_make_exits_1_saying_so(code, monkeypatch, capsys):
    """A checkout with no ``origin`` reads nothing: the job fails, which the alert reports, and
    it never reads an empty list as nothing waiting."""
    monkeypatch.setenv("GH_TOKEN", "t0k")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPOSITORY)
    monkeypatch.setattr(due, "run", lambda *args: pytest.fail("ran on nothing read"))
    assert due.main([], root=code) == 1
    assert capsys.readouterr().err.startswith("update due: nothing was read: git fetch ")


def test_a_pin_it_cannot_read_exits_1_saying_so(code, monkeypatch, capsys):
    """A lock on ``dev`` that is no TOML is no list of nothing."""
    monkeypatch.setenv("GH_TOKEN", "t0k")
    monkeypatch.setenv("GITHUB_REPOSITORY", _REPOSITORY)
    _branches(code, {"uv.lock": _lock("3.1.0")}, {"uv.lock": "[[package\n"})
    assert due.main([], root=code) == 1
    assert capsys.readouterr().err.startswith("update due: nothing was read: uv.lock is no TOML")


def test_main_exits_1_naming_each_variable_unset(monkeypatch, capsys):
    """No token or no repository reads nothing, naming each missing."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert due.main([]) == 1
    assert capsys.readouterr().err == (
        "update due: GH_TOKEN, GITHUB_REPOSITORY unset, so nothing was read\n")
