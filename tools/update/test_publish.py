"""The Monday update's publish (:mod:`tools.update.publish`, rulings plan#2351, plan#2450): each
change one commit on the ``dev`` commit the refresh read, its pull request opened or refreshed,
and each branch whose change is gone deleted.

GitHub's API is faked (:class:`_GitHub`) as a small store of commits, trees, branches and pull
requests that answers each endpoint the publish uses as GitHub documents it: a tree's id is
its content's hash, so the same files on the same tree make the same tree, as git's do.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.quill.door import LOCK, GitHub
from tools.update import folder, publish
from tools.update.folder import WEEK, Change
from tools.update.pyproject import PYPROJECT

#: The repository the publish writes.
_REPOSITORY = "owner/shekel"
#: ``dev``'s commit the refresh read, and its tree.
_BASE, _BASE_TREE = "b" * 40, "t" * 40


class _Response:
    """The slice of ``requests.Response`` quill's client reads."""

    def __init__(self, status_code, body=None):
        """One answer: its status and JSON body (none for a 204)."""
        self.status_code = status_code
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()
        self.headers = {}
        self._body = body

    def json(self):
        """The body."""
        return self._body


def _hash(*parts):
    """A git-like object id for ``parts``."""
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()


class _GitHub:
    """GitHub's REST API over a store: ``commits`` (id to its tree and parents), ``trees`` (id
    to its files over the base's), ``branches`` (name to commit), ``pulls`` (number to its
    head, base, title, text and state) and ``status``, what the compare of each commit with
    ``dev`` says; every write recorded in ``writes``, and a write ``refuse`` says yes to (given
    its method, path and body) answered 422."""

    def __init__(self, *, status="ahead", refuse=lambda method, path, body: False):
        """A store holding ``dev``'s commit alone."""
        self.commits = {_BASE: {"tree": _BASE_TREE, "parents": ["a" * 40]}}
        self.trees = {_BASE_TREE: {}}
        self.branches = {}
        self.pulls = {}
        self.status = status
        self.refuse = refuse
        self.writes = []

    def commit(self, tree_files, parent):
        """A commit holding ``tree_files`` over ``dev``'s tree, on ``parent``; its id."""
        tree = _hash(_BASE_TREE, tree_files)
        self.trees[tree] = tree_files
        sha = _hash("commit", tree, parent)
        self.commits[sha] = {"tree": tree, "parents": [parent]}
        return sha

    def request(self, method, url, **kwargs):
        """Answer one request as GitHub would."""
        parts = urlsplit(url)
        path = parts.path.removeprefix(f"/repos/{_REPOSITORY}")
        body = kwargs["json"]
        if method != "GET":
            self.writes.append((method, path, body))
            if self.refuse(method, path, body):
                return _Response(422, {"message": "Validation Failed"})
        return getattr(self, f"_{method.lower()}")(path, parse_qs(parts.query), body)

    def _get(self, path, query, _body):
        """A read."""
        if path.startswith("/compare/"):
            base, _, into = path.removeprefix("/compare/").partition("...")
            assert into == "dev"
            if base not in self.commits:
                return _Response(404, {"message": "Not Found"})
            return _Response(200, {"status": self.status})
        if path.startswith("/git/commits/"):
            sha = path.removeprefix("/git/commits/")
            commit = self.commits[sha]
            return _Response(200, {"sha": sha, "tree": {"sha": commit["tree"]},
                                   "parents": [{"sha": p} for p in commit["parents"]]})
        if path == f"/git/matching-refs/heads/{folder.PREFIX}":
            return _Response(200, [{"ref": f"refs/heads/{name}", "object": {"sha": sha}}
                                   for name, sha in sorted(self.branches.items())
                                   if name.startswith(folder.PREFIX)])
        assert path == "/pulls"
        owner, _, head = query["head"][0].partition(":")
        assert (owner, query["base"], query["state"]) == ("owner", ["dev"], ["open"])
        return _Response(200, [{"number": number} for number, pull in sorted(self.pulls.items())
                               if pull["head"] == head and pull["state"] == "open"])

    def _post(self, path, _query, body):
        """A write making a tree, a commit, a branch or a pull request."""
        if path == "/git/trees":
            files = dict(self.trees[body["base_tree"]])
            files |= {entry["path"]: (entry["mode"], entry["content"]) for entry in body["tree"]}
            tree = _hash(body["base_tree"], files)
            self.trees[tree] = files
            return _Response(201, {"sha": tree})
        if path == "/git/commits":
            sha = _hash("commit", body["tree"], *body["parents"])
            self.commits[sha] = {"tree": body["tree"], "parents": body["parents"]}
            return _Response(201, {"sha": sha})
        if path == "/git/refs":
            name = body["ref"].removeprefix("refs/heads/")
            assert name not in self.branches
            self.branches[name] = body["sha"]
            return _Response(201, {"ref": body["ref"]})
        assert path == "/pulls"
        number = 100 + len(self.pulls)
        self.pulls[number] = {"head": body["head"], "base": body["base"], "title": body["title"],
                              "body": body["body"], "state": "open"}
        return _Response(201, {"number": number})

    def _patch(self, path, _query, body):
        """A write moving a branch, or refreshing a pull request."""
        if path.startswith("/git/refs/heads/"):
            assert body["force"] is True
            self.branches[path.removeprefix("/git/refs/heads/")] = body["sha"]
            return _Response(200, {"object": {"sha": body["sha"]}})
        number = int(path.removeprefix("/pulls/"))
        self.pulls[number] |= {"title": body["title"], "body": body["body"]}
        return _Response(200, {"number": number})

    def _delete(self, path, _query, _body):
        """A branch deleted, which closes each pull request from it."""
        name = path.removeprefix("/git/refs/heads/")
        del self.branches[name]
        for pull in self.pulls.values():
            if pull["head"] == name:
                pull["state"] = "closed"
        return _Response(204)

    def files(self, branch):
        """What ``branch``'s commit holds over ``dev``'s tree, each file's text."""
        commit = self.commits[self.branches[branch]]
        return {name: text for name, (_, text) in self.trees[commit["tree"]].items()}


#: The week's lock and pyee prepared alone, as the refresh writes them.
_WEEK = Change(WEEK, {LOCK: "the week's lock\n"}, "chore(deps): the lock moves 6 packages",
               "Six moved.\n")
_PYEE = Change(folder.alone("pyee"), {LOCK: "pyee's lock\n", PYPROJECT: "pyee>=14\n"},
               "chore(deps): pyee from 13.0.1 to 14.0.0, waiting for a card", "Needs one.\n")


def _folder(tmp_path, *changes):
    """The folder the refresh's command leaves holding ``changes``; its path."""
    out = tmp_path / "out"
    for change in changes:
        folder.write(out, change)
    folder.write_summary(out, "The report.\n")
    return out


def _publish(fake, out, base=_BASE):
    """The publish of ``out`` from ``base`` against ``fake``; its exit status."""
    return publish.run(GitHub("token", session=fake), _REPOSITORY, out, base)


def test_each_change_is_one_commit_on_the_commit_the_refresh_read_with_its_pull_request(
        tmp_path, capsys):
    """The first Monday: each change made one commit whose one parent is the ``dev`` commit
    the refresh read, holding its files over that commit's tree, on its branch, with its pull
    request into ``dev`` opened under its title and text."""
    fake = _GitHub()
    assert _publish(fake, _folder(tmp_path, _WEEK, _PYEE)) == 0
    for change in (_WEEK, _PYEE):
        commit = fake.commits[fake.branches[change.branch]]
        assert commit["parents"] == [_BASE]
        assert fake.files(change.branch) == dict(change.files)
    assert [(pull["head"], pull["base"], pull["title"], pull["body"])
            for pull in fake.pulls.values()] == [
        ("update/lock", "dev", _WEEK.title, _WEEK.body),
        ("update/major/pyee", "dev", _PYEE.title, _PYEE.body)]
    assert [body["message"] for _, path, body in fake.writes if path == "/git/commits"] == [
        f"{_WEEK.title}\n", f"{_PYEE.title}\n"]
    out = capsys.readouterr().out
    assert "update/lock: made at " in out
    assert "; pull request #100 opened" in out
    assert "update publish: 2 changes from bbbbbbbbbbbb; 0 failed" in out


def test_publishing_the_same_change_again_moves_no_branch(tmp_path, capsys):
    """A branch already holding the change on that commit is left, so no CI run restarts and
    no queued pull request is dropped; its pull request's title and text are refreshed."""
    fake = _GitHub()
    out = _folder(tmp_path, _WEEK)
    assert _publish(fake, out) == 0
    held = dict(fake.branches)
    fake.writes.clear()
    assert _publish(fake, out) == 0
    assert fake.branches == held
    assert [(method, path) for method, path, _ in fake.writes] == [
        ("POST", "/git/trees"), ("PATCH", "/pulls/100")]
    assert f"update/lock: left at {held['update/lock'][:12]}" in capsys.readouterr().out


def test_a_new_week_moves_each_branch_and_deletes_each_whose_change_is_gone(tmp_path, capsys):
    """Last week's lock moved to this week's commit by force, its pull request refreshed, not
    reopened; a package no longer prepared has its branch deleted, closing its pull request;
    a branch the layout could not name is never touched."""
    fake = _GitHub()
    last_lock = fake.commit({LOCK: ("100644", "last week's lock\n")}, "d" * 40)
    last_rumdl = fake.commit({LOCK: ("100644", "rumdl's lock\n")}, "d" * 40)
    fake.branches = {"update/lock": last_lock, "update/major/rumdl": last_rumdl,
                     "update/other": last_lock}
    fake.pulls = {7: {"head": "update/lock", "base": "dev", "title": "old", "body": "old",
                      "state": "open"},
                  8: {"head": "update/major/rumdl", "base": "dev", "title": "rumdl",
                      "body": "rumdl", "state": "open"}}
    assert _publish(fake, _folder(tmp_path, _WEEK)) == 0
    assert fake.commits[fake.branches["update/lock"]]["parents"] == [_BASE]
    assert fake.files("update/lock") == dict(_WEEK.files)
    assert sorted(fake.branches) == ["update/lock", "update/other"]
    assert (fake.pulls[7]["title"], fake.pulls[7]["state"], fake.pulls[8]["state"]) == (
        _WEEK.title, "open", "closed")
    assert len(fake.pulls) == 2
    out = capsys.readouterr().out
    assert f"update/lock: moved from {last_lock[:12]} to " in out
    assert "; pull request #7 refreshed" in out
    assert "update/major/rumdl: deleted, its change gone, which closes its pull request" in out


def test_the_same_change_on_an_older_dev_commit_is_moved_onto_the_one_read(tmp_path, capsys):
    """A branch holding this week's files on an older ``dev`` commit is no branch holding the
    change on the commit the refresh read: it is moved onto that one."""
    fake = _GitHub()
    older = fake.commit({LOCK: ("100644", _WEEK.files[LOCK])}, "d" * 40)
    fake.branches = {"update/lock": older}
    assert _publish(fake, _folder(tmp_path, _WEEK)) == 0
    assert fake.commits[fake.branches["update/lock"]]["parents"] == [_BASE]
    assert f"update/lock: moved from {older[:12]} to " in capsys.readouterr().out


def test_a_week_with_nothing_to_publish_deletes_every_branch_left(tmp_path):
    """The report alone: nothing to publish, so each branch of the layout is deleted."""
    fake = _GitHub()
    fake.branches = {"update/lock": fake.commit({}, _BASE)}
    assert _publish(fake, _folder(tmp_path)) == 0
    assert not fake.branches


@pytest.mark.parametrize(("status", "base", "said"), [
    ("behind", _BASE, "bbbbbbbbbbbb is not dev's, nor in its history (behind)"),
    ("diverged", _BASE, "bbbbbbbbbbbb is not dev's, nor in its history (diverged)"),
    ("ahead", "main", "'main' is no full commit id"),
], ids=["dev behind it", "off dev", "no commit id"])
def test_a_commit_not_in_devs_history_publishes_nothing(tmp_path, capsys, status, base, said):
    """The commit the refresh read must be ``dev``'s or in its history: else no write."""
    fake = _GitHub(status=status)
    fake.branches = {"update/major/rumdl": fake.commit({}, _BASE)}
    assert _publish(fake, _folder(tmp_path, _WEEK), base) == 1
    assert not fake.writes
    assert said in capsys.readouterr().err


def test_a_folder_outside_its_layout_publishes_nothing_and_reads_nothing(tmp_path, capsys):
    """The folder is refused before GitHub is read or written."""
    out = _folder(tmp_path, _WEEK)
    (out / folder.SUMMARY).unlink()

    class _Untouched:  # pylint: disable=too-few-public-methods
        """A session no request may reach.

        Pylint: too-few-public-methods (1/2) -- **one because a session is one method**.
        """

        def request(self, method, url, **_kwargs):
            """Fail the test."""
            raise AssertionError(f"{method} {url}")

    assert publish.run(GitHub("token", session=_Untouched()), _REPOSITORY, out, _BASE) == 1
    assert "nothing was published: " in capsys.readouterr().err


def test_a_refused_write_fails_the_run_once_every_other_branch_is_tried(tmp_path, capsys):
    """GitHub refuses the week's pull request: its line says so, pyee is still published and
    a gone branch still deleted, and the run fails."""
    fake = _GitHub(refuse=lambda method, path, body: (method, path) == ("POST", "/pulls")
                   and body["head"] == "update/lock")
    fake.branches = {"update/major/rumdl": fake.commit({}, _BASE)}
    assert _publish(fake, _folder(tmp_path, _WEEK, _PYEE)) == 1
    assert sorted(fake.branches) == ["update/lock", "update/major/pyee"]
    assert [pull["head"] for pull in fake.pulls.values()] == ["update/major/pyee"]
    out, err = capsys.readouterr()
    assert "update/lock: failed: " in err
    assert "Validation Failed" in err
    assert "update/major/pyee: made at " in out
    assert "update/major/rumdl: deleted" in out
    assert "update publish: 2 changes from bbbbbbbbbbbb; 1 failed" in out


def test_the_publish_starts_only_with_each_of_its_variables(monkeypatch, capsys, tmp_path):
    """An unset or empty variable stops the run before any read, naming each one."""
    monkeypatch.setenv(publish.TOKEN_VARIABLE, "")
    monkeypatch.delenv(publish.REPOSITORY_VARIABLE, raising=False)
    assert publish.main([str(_folder(tmp_path, _WEEK)), _BASE]) == 1
    assert capsys.readouterr().err == (
        "update publish: UPDATE_TOKEN, GITHUB_REPOSITORY unset, so nothing was published\n")
