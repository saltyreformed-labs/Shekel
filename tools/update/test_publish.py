"""The Monday update's publish (:mod:`tools.update.publish`, rulings plan#2351, plan#2450): each
change one commit on the ``dev`` commit the refresh read, its pull request opened or refreshed,
and each branch whose change is gone deleted.

GitHub's API is faked (:class:`_GitHub`) as a small store of commits, trees, branches and pull
requests that answers each endpoint the publish uses as GitHub documents it: a tree's id is
its content's hash, so the same files on the same tree make the same tree, as git's do.
"""
from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.quill.door import LOCK, GitHub
from tools.update import folder, publish
from tools.update.app import DEVELOPER
from tools.update.folder import WEEK, Change
from tools.update.pyproject import PYPROJECT

#: The repository the publish writes.
_REPOSITORY = "owner/shekel"
#: ``dev``'s commit the refresh read, and its tree.
_BASE, _BASE_TREE = "b" * 40, "t" * 40
#: The login a pull request closed by deleting its branch names as its closer: the App's, as
#: GraphQL names a bot, by its slug.
_APP = "shekel-updates"


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
    head, base, title, text and state, and once closed its head commit, closer and whether
    it merged) and ``status``, what the compare of each commit with ``dev`` says; every write
    recorded in ``writes``, and a request ``refuse`` says yes to (given its method, path and
    body) answered 422."""

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
        if method != "GET" and path != "/graphql":
            self.writes.append((method, path, body))
        if self.refuse(method, path, body):
            return _Response(422, {"message": "Validation Failed"})
        return getattr(self, f"_{method.lower()}")(path, parse_qs(parts.query), body)

    def close(self, number, by=DEVELOPER, merged=False, fork=False):
        """Pull request ``number`` closed by the login ``by``, merged or not, from a fork or
        not, at its branch's commit."""
        pull = self.pulls[number]
        pull |= {"state": "closed", "closed_by": by, "merged": merged, "fork": fork,
                 "sha": self.branches[pull["head"]]}

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
        if (answer := self._closed(path)) is not None:
            return answer
        assert path == "/pulls"
        owner, _, head = query["head"][0].partition(":")
        assert (owner, query["base"], query["state"]) == ("owner", ["dev"], ["open"])
        return _Response(200, [{"number": number} for number, pull in sorted(self.pulls.items())
                               if pull["head"] == head and pull["state"] == "open"])

    def _closed(self, path):
        """A read of what a closed pull request left: a commit's changed files, each with
        its blob's id (the hash of its text), or a blob, found among the trees by that hash;
        None for any other path."""
        if path.startswith("/commits/"):
            commit = self.commits[path.removeprefix("/commits/")]
            return _Response(200, {"files": [
                {"filename": name, "status": "modified", "sha": _hash("blob", text)}
                for name, (_, text) in self.trees[commit["tree"]].items()]})
        if path.startswith("/git/blobs/"):
            (text,) = {text for files in self.trees.values() for _, text in files.values()
                       if _hash("blob", text) == path.removeprefix("/git/blobs/")}
            return _Response(200, {"content": base64.b64encode(text.encode()).decode(),
                                   "encoding": "base64"})
        return None

    def _post(self, path, _query, body):
        """A write making a tree, a commit, a branch or a pull request; or GraphQL's one
        page of the pull requests closed unmerged from a branch, newest first, each with its
        head, whether it came from a fork, and who closed it, an actor GitHub no longer knows
        read as none."""
        if path == "/graphql":
            assert body["variables"]["after"] is None
            return _Response(200, {"data": {"repository": {"pullRequests": {
                "nodes": [{"number": number, "isCrossRepository": pull["fork"],
                           "headRefOid": pull["sha"], "timelineItems": {"nodes": [
                               {"actor": None if pull["closed_by"] is None
                                else {"login": pull["closed_by"]}}]}}
                          for number, pull in sorted(self.pulls.items(), reverse=True)
                          if pull["head"] == body["variables"]["branch"]
                          and pull["state"] == "closed" and not pull["merged"]],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}})
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
        """A branch deleted, which closes each pull request open from it, the App its
        closer."""
        name = path.removeprefix("/git/refs/heads/")
        sha = self.branches.pop(name)
        for pull in self.pulls.values():
            if pull["head"] == name and pull["state"] == "open":
                pull |= {"state": "closed", "closed_by": _APP, "merged": False, "fork": False,
                         "sha": sha}
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


def _pyee(version, *, locked=True, certifi="2026.10.19"):
    """pyee prepared alone at ``version``: its requirement moved to it, as the refresh's
    ``moved`` spells one, and its lock where uv locked it, beside certifi at ``certifi`` in
    both, which the week can move under it."""
    files = {PYPROJECT: '[project]\nname = "shekel"\ndependencies = '
                        f'["certifi>={certifi},<2027", "pyee>={version},<15"]\n'}
    if locked:
        files[LOCK] = "".join(f'[[package]]\nname = "{name}"\nversion = "{at}"\n\n'
                              for name, at in (("certifi", certifi), ("pyee", version)))
    return Change(folder.alone("pyee"), files,
                  f"chore(deps): pyee from 13.0.1 to {version}, waiting for a card",
                  "Needs one.\n")


def _closed_at(fake, tmp_path, change, **closed):
    """``change`` published, its pull request #100 then closed as ``closed`` says, and the
    writes forgotten."""
    assert _publish(fake, _folder(tmp_path / "before", change)) == 0
    fake.close(100, **closed)
    fake.writes.clear()


@pytest.mark.parametrize("locked", [True, False], ids=["its lock", "its requirement alone"])
def test_a_pull_request_the_developer_closed_stays_closed_at_the_release_it_proposes(
        tmp_path, capsys, locked):
    """Rulings plan#2774, plan#2776: closed by the developer, a held-back change is neither
    committed nor opened again while it proposes the release its closed head did, read from
    the lock or, where uv could not lock it, from its moved requirement, though the week
    moved another package under it since."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0", locked=locked))
    held = dict(fake.branches)
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.0", locked=locked,
                                                  certifi="2026.10.23"))) == 0
    assert not fake.writes
    assert fake.branches == held
    assert [pull["state"] for pull in fake.pulls.values()] == ["closed"]
    assert (f"update/major/pyee: left, as its pull request #100, closed by {DEVELOPER} at pyee "
            "14.0.0; its next release is published") in capsys.readouterr().out


def test_a_requirement_the_refresh_never_moves_names_no_release(tmp_path):
    """An uncapped requirement of the package, which the refresh moves nowhere, is no
    release proposed: ``dev`` adding one under a closed change keeps it closed."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0", locked=False))
    again = _pyee("14.0.0", locked=False)
    grouped = again.files[PYPROJECT] + '\n[dependency-groups]\ndev = ["pyee>=13"]\n'
    assert _publish(fake, _folder(tmp_path, Change(again.where, {PYPROJECT: grouped},
                                                   again.title, again.body))) == 0
    assert not fake.writes


def test_the_same_release_locked_now_is_the_release_closed(tmp_path):
    """A close of a change uv could not lock holds against the same release once uv locks
    it: the release is what is compared, not the form the change takes."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0", locked=False))
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.0"))) == 0
    assert not fake.writes


def test_the_package_s_next_release_is_published_though_the_last_was_closed(tmp_path, capsys):
    """Closing means "not this release": pyee 14.0.1 is committed on its branch and its pull
    request opened, which the queue tells of once; the closed one stays closed."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0"))
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.1"))) == 0
    assert fake.files("update/major/pyee") == dict(_pyee("14.0.1").files)
    assert [(pull["title"], pull["state"]) for pull in fake.pulls.values()] == [
        (_pyee("14.0.0").title, "closed"), (_pyee("14.0.1").title, "open")]
    assert "; pull request #101 opened" in capsys.readouterr().out


@pytest.mark.parametrize("closed", [
    {"by": _APP}, {"by": "someone-else"}, {"by": None}, {"merged": True}, {"fork": True},
], ids=["closed by the App", "closed by another person", "closed by a deleted account",
        "merged", "from a fork"])
def test_only_the_developer_s_close_keeps_a_release_closed(tmp_path, capsys, closed):
    """A pull request the publish closed by deleting its branch, one anyone else closed, one
    merged, and one from a fork's branch of the same name leave the same release to be
    published again."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0"), **closed)
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.0"))) == 0
    assert [pull["state"] for pull in fake.pulls.values()] == ["closed", "open"]
    assert "; pull request #101 opened" in capsys.readouterr().out


def test_an_open_pull_request_is_refreshed_whatever_an_older_one_closed(tmp_path, capsys):
    """While one is open from the branch, it is refreshed: 14.0.0 closed, 14.0.1 opened, then
    14.0.1 yanked so the refresh proposes 14.0.0 again, which the open one is moved to."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0"))
    assert _publish(fake, _folder(tmp_path / "next", _pyee("14.0.1"))) == 0
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.0"))) == 0
    assert fake.files("update/major/pyee") == dict(_pyee("14.0.0").files)
    assert [(pull["title"], pull["state"]) for pull in fake.pulls.values()] == [
        (_pyee("14.0.0").title, "closed"), (_pyee("14.0.0").title, "open")]
    assert "; pull request #101 refreshed" in capsys.readouterr().out


def test_a_pull_request_the_developer_reopened_is_refreshed(tmp_path, capsys):
    """Reopening is the way back: an open pull request is refreshed, never a second one
    opened."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0"))
    fake.pulls[100]["state"] = "open"
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.0"))) == 0
    assert len(fake.pulls) == 1
    assert "; pull request #100 refreshed" in capsys.readouterr().out


def test_every_release_the_developer_closed_stays_closed(tmp_path, capsys):
    """Not only the newest close holds: 14.0.0 closed, 14.0.1 opened and closed, then 14.0.1
    yanked so the refresh proposes 14.0.0 again, which stays closed on its own close."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0"))
    assert _publish(fake, _folder(tmp_path / "next", _pyee("14.0.1"))) == 0
    fake.close(101)
    fake.writes.clear()
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.0"))) == 0
    assert not fake.writes
    assert "left, as its pull request #100, closed by " in capsys.readouterr().out


def test_a_close_holds_once_the_developer_deleted_its_branch(tmp_path):
    """The release is read at the closed pull request's own head, which outlives its
    branch: with the branch gone, the same release is still neither committed nor
    opened."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0"))
    del fake.branches["update/major/pyee"]
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.0"))) == 0
    assert not fake.writes
    assert "update/major/pyee" not in fake.branches


@pytest.mark.parametrize("name", [LOCK, PYPROJECT])
def test_a_closed_head_that_cannot_be_read_proposes_no_release(tmp_path, capsys, name):
    """A head the developer closed whose lock or ``pyproject.toml`` is no TOML proposes no
    release, so the change is published again, never failing that release and every later
    one."""
    fake = _GitHub()
    fake.branches["update/major/pyee"] = fake.commit({name: ("100644", "no = [\n")}, _BASE)
    fake.pulls[7] = {"head": "update/major/pyee", "base": "dev", "title": "pyee",
                     "body": "pyee", "state": "open"}
    fake.close(7)
    assert _publish(fake, _folder(tmp_path, _pyee("14.0.0"))) == 0
    assert "; pull request #101 opened" in capsys.readouterr().out


@pytest.mark.parametrize("refuse", [
    lambda method, path, body: (method, path) == ("POST", "/graphql"),
    lambda method, path, body: method == "GET" and path.startswith("/commits/"),
    lambda method, path, body: method == "GET" and path.startswith("/git/blobs/"),
], ids=["the pull requests closed", "its head's files", "a file's text"])
def test_a_closed_pull_request_github_will_not_read_fails_its_change_alone(
        tmp_path, capsys, refuse):
    """A read GitHub refuses fails the change it is read for, leaving its branch where it
    was, and the run; the week's lock is still published."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0"))
    held = fake.branches["update/major/pyee"]
    fake.refuse = refuse
    assert _publish(fake, _folder(tmp_path, _WEEK, _pyee("14.0.0"))) == 1
    assert fake.branches["update/major/pyee"] == held
    out, err = capsys.readouterr()
    assert "update/major/pyee: failed: " in err
    assert "Validation Failed" in err
    assert "update/lock: made at " in out


def test_a_change_whose_own_lock_cannot_be_read_fails_alone(tmp_path, capsys):
    """Once the developer has closed one, the change's own release is read: a lock that is
    no TOML fails that change, and the run, leaving its branch where it was; the week's lock
    is still published."""
    fake = _GitHub()
    _closed_at(fake, tmp_path, _pyee("14.0.0"))
    held = fake.branches["update/major/pyee"]
    broken = Change(folder.alone("pyee"), {LOCK: "no = [\n"}, _pyee("14.0.0").title, "Text.\n")
    assert _publish(fake, _folder(tmp_path, _WEEK, broken)) == 1
    assert fake.branches["update/major/pyee"] == held
    out, err = capsys.readouterr()
    assert "update/major/pyee: failed: uv.lock is no TOML" in err
    assert "update/lock: made at " in out
