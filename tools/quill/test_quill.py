"""The ``quill`` command's reads and decisions -- next, claim, release, show, block, move,
drop, sync, spec-history and spec-revert -- over :class:`_fake.FakeTracker` and a
throwaway git repository with a real bare ``origin`` (the ``code`` fixture,
``conftest.py``), where every ship marker and claim (ruling ``balance:R-BAL359``) is
written and read as git writes and reads it.  ``file`` has its own module,
``test_quill_file.py``, and so do ``show``'s own cases, ``test_quill_show.py``.  Nothing
here calls GitHub.
"""
from __future__ import annotations

import re
from functools import partial

import pytest
import requests

from tools.ci.gitcmd import GitError
from tools.ci.scratch import commit, commit_files, elsewhere, point_dev
from tools.ci.scratch import run as _run
from tools.ci.trailers import DEV, tracking
from tools.quill import _git, _github, _tracker
from tools.quill import quill
from tools.quill._fake import CLAIMED, FakeTracker, claim, run, ship, unship
from tools.quill._github import GitHubError
from tools.quill._tracker import Child, Edit, OutsideLink


# -- next ----------------------------------------------------------------------------------

def test_next_names_the_first_workable_step_and_what_the_order_cannot_place(code, capsys,
                                                                            monkeypatch):
    """Board order; a step not on the board and a stale claim are named, never skipped."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    tracker.add(3, on_board=False)
    tracker.add(4)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-09-01T00:00:00+00:00")
    claim(code, 1)
    ship(code, 2)
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "next: plan#4 [step, balance] card 4"
    assert "NOT ON THE BOARD, so in no order: plan#3" in out
    assert "STALE CLAIM: plan#1 on card/1 " in out
    assert not tracker.writes


def test_next_names_a_claim_idle_three_days_stale_and_no_fresh_one(code, capsys, monkeypatch):
    """Stale is the claim branch's LAST COMMIT, not its claim: plan#2's marker says it was
    claimed 2026-10-04, but its tip was committed now, so it is not named."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-09-01T00:00:00-04:00")
    claim(code, 1)
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    claim(code, 2)
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "next: plan#3 [step, balance] card 3"
    assert ("STALE CLAIM: plan#1 on card/1 (claimed 2026-10-04T12:00:00Z; last commit "
            "2026-09-01T00:00:00-04:00): no commit for more than 3 days -- `quill release "
            "plan#1` keeps its work and frees it") in out
    assert "plan#2" not in out



def test_next_reads_a_closed_blocker_and_a_containers_leaves(code, capsys):
    """A blocker a person closed is dropped; one the tool closed counts only if git shipped it."""
    tracker = FakeTracker()
    tracker.add(5, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.add(6, is_open=False, state_reason="COMPLETED", touched_by_hand=True)
    tracker.add(1, blocked_by=(5,))
    tracker.add(2, blocked_by=(6,))
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#2 ")
    ship(code, 5)
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#1 ")



# -- claim, release ------------------------------------------------------------------------

def test_claim_pushes_card_n_checks_it_out_and_refuses_a_held_card(code, capsys):
    """R-BAL359: the claim is ``card/1`` on origin, a commit on origin/dev adding
    plan/ships/1, checked out here with no upstream (no ``branch.card/1`` section in the
    shared configuration).  Exactly one session wins: the second claim is refused with the
    holder named."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "claim", "plan#1") == 0
    sha = _git.on_origin(code)[1]
    held = _git.read(code).claims[1]
    assert (held.sha, held.branch) == (sha, "card/1") and held.claimed is not None
    assert _run(code, "diff", "--name-status", f"{sha}^", sha) == "A\tplan/ships/1"
    assert _run(code, "rev-parse", f"{sha}^") == _run(code, "rev-parse", tracking(DEV))
    assert _run(code, "symbolic-ref", "--short", "HEAD") == "card/1"
    assert "branch.card/1" not in _run(code, "config", "--list")
    assert run(tracker, code, "claim", "1") == 1
    assert "plan#1 is already claimed: card/1 (claimed " in capsys.readouterr().err
    assert _git.on_origin(code) == {1: sha}
    assert not tracker.writes


def test_claim_here_builds_on_the_commit_checked_out(code):
    """``--here``, for work already started: the claim's commit sits on HEAD, not on
    origin/dev."""
    tracker = FakeTracker()
    tracker.add(1)
    started = commit(code, "work started", parents=[_run(code, "rev-parse", "HEAD")])
    _run(code, "update-ref", "refs/heads/feat/work", started)
    assert run(tracker, code, "claim", "plan#1", "--here") == 0
    sha = _git.on_origin(code)[1]
    assert _run(code, "rev-parse", f"{sha}^") == started
    assert _run(code, "symbolic-ref", "--short", "HEAD") == "card/1"


def test_claim_refuses_a_card_branch_this_clone_has_already(code, capsys):
    """The check-out would collide with it, so nothing is pushed."""
    tracker = FakeTracker()
    tracker.add(1)
    _run(code, "branch", "card/1")
    assert run(tracker, code, "claim", "plan#1") == 1
    assert "this clone already has a branch 'card/1'" in capsys.readouterr().err
    assert not _git.on_origin(code)


@pytest.mark.parametrize("fields", [
    {"kind": "ruling"}, {"kind": "question"}, {"is_open": False},
    {"children": (Child(9, "step", True),)},
])
def test_claim_refuses_what_no_branch_ships(code, fields):
    """A ruling, a question, a closed card and a container are not built by a branch."""
    tracker = FakeTracker()
    tracker.add(1, **fields)
    for child in fields.get("children", ()):
        tracker.add(child.number, parent=1)
    assert run(tracker, code, "claim", "plan#1") == 1
    assert not tracker.writes
    assert not _git.on_origin(code)


def test_claim_builds_on_dev_where_dev_and_main_differ(code):
    """M1's review L3: the claim's commit sits on the dev origin holds, not on main."""
    tracker = FakeTracker()
    tracker.add(1)
    on_dev = ship(code, 5)
    assert run(tracker, code, "claim", "plan#1") == 0
    assert _run(code, "rev-parse", f"{_git.on_origin(code)[1]}^") == on_dev


def test_claim_lost_to_a_claim_made_since_its_read_is_refused(code, capsys, monkeypatch):
    """M1's review L2: another clone's claim lands between this command's read and its
    create-only push; the lease refuses it, exit 1, and nothing here moves."""
    tracker = FakeTracker()
    tracker.add(1)
    made = _git.claim

    def raced(root, number, base, when):
        made(elsewhere(root), number, tracking(DEV), CLAIMED)
        return made(root, number, base, when)

    monkeypatch.setattr(_git, "claim", raced)
    assert run(tracker, code, "claim", "plan#1") == 1
    assert "another session claimed plan#1 since" in capsys.readouterr().err
    assert _run(code, "symbolic-ref", "--short", "HEAD") == "feat/work"
    assert list(_git.on_origin(code)) == [1] and not _git.has_branch(code, "card/1")


def test_a_claim_whose_switch_fails_stands_and_says_what_to_run(code, capsys):
    """M1's review L2: the claim is printed once its push lands, so a check-out git refuses
    (here an untracked file the claim's commit would overwrite) leaves it standing, exit 0,
    with the command to finish it."""
    tracker = FakeTracker()
    tracker.add(1)
    (code / "plan" / "ships").mkdir(parents=True)
    (code / "plan" / "ships" / "1").write_text("mine\n")
    assert run(tracker, code, "claim", "plan#1") == 0
    out = capsys.readouterr().out
    assert "\n  not switched to card/1 (" in out and "run `git switch card/1`" in out
    assert list(_git.on_origin(code)) == [1]
    assert _run(code, "symbolic-ref", "--short", "HEAD") == "feat/work"


def test_claim_refuses_a_card_branch_that_outlived_its_merge_naming_the_delete(code, capsys):
    """M1's review L4: ``card/1`` whose tip dev holds is no claim, and a create-only push
    cannot replace it; the refusal names the leased delete, and once it runs the claim is
    made."""
    tracker = FakeTracker()
    tracker.add(1)
    merged = ship(code, 7)
    _run(elsewhere(code), "push", "--quiet", "origin", f"{merged}:refs/heads/card/1")
    assert run(tracker, code, "claim", "plan#1") == 1
    err = capsys.readouterr().err
    assert f"origin holds card/1 at {merged[:12]}, whose work origin/dev or origin/main" in err
    delete = re.search(r"`git (push [^`]+)`", err)[1]
    assert delete == (f"push --force-with-lease=refs/heads/card/1:{merged} origin "
                      ":refs/heads/card/1")
    assert _git.on_origin(code) == {1: merged}
    _run(code, *delete.split())
    assert run(tracker, code, "claim", "plan#1") == 0


def test_claim_of_work_a_revert_unshipped_says_to_sync_first(code, capsys):
    """M1's review L5: the tool closed plan#1 as shipped and a revert took its marker back;
    claim refuses until sync reopens it, and says so."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    ship(code, 1)
    unship(code, 1)
    assert run(tracker, code, "claim", "plan#1") == 1
    assert ("git no longer says it shipped (a revert deleted plan/ships/1): `quill sync` "
            "reopens it, then claim it") in capsys.readouterr().err
    assert run(tracker, code, "sync") == 0
    assert run(tracker, code, "claim", "plan#1") == 0


def test_release_keeps_the_work_and_deletes_the_claim(code, capsys):
    """Whoever made the claim: its tip is kept on ``released/1/<instant>``, ``card/1`` is
    deleted from origin; a card with no claim is refused."""
    tracker = FakeTracker()
    tracker.add(1)
    sha = claim(code, 1)
    assert run(tracker, code, "release", "plan#1") == 0
    assert "released plan#1, card/1 (claimed 2026-10-04T12:00:00Z; last commit " in (
        capsys.readouterr().out)
    assert not _git.on_origin(code)
    [kept] = _run(code, "ls-remote", "origin", "refs/heads/released/*").splitlines()
    assert kept.split("\t")[0] == sha
    assert re.fullmatch(r"refs/heads/released/1/[0-9]{8}T[0-9]{6}Z", kept.split("\t")[1])
    assert run(tracker, code, "release", "plan#1") == 1
    assert "plan#1 is not claimed" in capsys.readouterr().err
    assert not tracker.writes


def test_release_leaves_this_clones_card_branch_and_names_it(code, capsys):
    """The claim this clone made and checked out: its branch here keeps the work, and the
    release says so."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "claim", "plan#1") == 0
    assert run(tracker, code, "release", "plan#1") == 0
    assert "\n  this clone's branch card/1 is left as it is\n" in capsys.readouterr().out
    assert _git.has_branch(code, "card/1") and not _git.on_origin(code)



# -- block, move, drop ---------------------------------------------------------------------

def test_block_and_unblock(code):
    """One edge each way; removing an edge that is not there is refused."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    assert run(tracker, code, "block", "plan#1", "--by", "plan#2") == 0
    assert tracker.cards_by_number[1].blocked_by == (2,)
    assert run(tracker, code, "block", "plan#1", "--by", "plan#2", "--remove") == 0
    assert run(tracker, code, "block", "plan#1", "--by", "plan#2", "--remove") == 1



def test_move_places_steps_and_questions_only(code):
    """R-BAL177: a finding is not in the order."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    tracker.add(4, "finding", parent=1)
    assert run(tracker, code, "move", "plan#3", "--top") == 0
    assert tracker.board.items == [3, 1, 2]
    assert run(tracker, code, "move", "plan#3", "--after", "plan#1") == 0
    assert tracker.board.items == [1, 3, 2]
    assert run(tracker, code, "move", "plan#1", "--bottom") == 0
    assert tracker.board.items == [3, 2, 1]
    assert run(tracker, code, "move", "plan#4", "--top") == 1



def test_drop_says_why_and_closes_as_not_planned(code):
    """No trailer, no code commit: a comment and a close."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded by plan#2") == 0
    assert tracker.writes == [("comment", 1, "Dropped: superseded by plan#2"),
                              ("close", 1, "not_planned")]



# -- sync ----------------------------------------------------------------------------------

def test_sync_closes_shipped_work_with_no_claim_and_reports_a_persons_reopen(code, capsys):
    """R-BAL359: the marker is git's answer, so open work it ships is closed with no claim
    behind it; one a person reopened is reported and left as it is; and a revert deleting
    the marker reopens the tool's close."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, touched_by_hand=True)
    ship(code, 1, 2)
    assert run(tracker, code, "sync") == 1
    assert tracker.writes == [("close", 1, "completed")]
    assert ("REPORT: plan#2 shipped in git (plan/ships/2 is on origin/dev or origin/main) but "
            "a person reopened it: close it by hand, or revert the change that shipped it"
            ) in capsys.readouterr().out
    unship(code, 1)
    assert run(tracker, code, "sync") == 1
    assert tracker.writes[1:] == [("reopen", 1)]



def test_sync_dry_run_writes_nothing(code, capsys):
    """``--dry-run`` says what it would do."""
    tracker = FakeTracker()
    tracker.add(1)
    ship(code, 1)
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert not tracker.writes
    assert "would close plan#1" in capsys.readouterr().out



def test_sync_reopens_what_a_revert_of_its_marker_takes_back(code):
    """The tool's own COMPLETED close follows git."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    ship(code, 1)
    unship(code, 1)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == [("reopen", 1)]



def test_sync_prints_a_marker_naming_no_card_and_a_stray_entry_as_history(code, capsys):
    """BAL-544's class: a number nobody filed is reported, never silently passed -- and, as
    history no tracker write can change, it never fails sync (R-BAL184); so is an entry
    under plan/ships/ that is no card's marker."""
    tracker = FakeTracker()
    ship(code, 77)
    assert run(tracker, code, "sync") == 0
    assert ("HISTORY: the marker plan/ships/77 on origin/dev or origin/main names plan#77, "
            "which does not exist" in capsys.readouterr().out)
    stray = commit_files(code, _run(code, "rev-parse", tracking(DEV)), "notes",
                         {"plan/ships/notes": "x\n"})
    _run(code, "push", "--quiet", "origin", f"{stray}:refs/heads/dev")
    assert run(tracker, code, "sync") == 0
    out = capsys.readouterr().out
    assert ("HISTORY: plan/ships/notes on origin/dev or origin/main is no card's marker"
            in out)
    assert "REPORT" not in out



# -- spec-history, spec-revert -------------------------------------------------------------

def _versions(tracker):
    """Card 1 filed 10-01 by the tool, edited 10-02 by the tool and 10-03 by the developer."""
    tracker.add(1, body="v3")
    tracker.versions[1] = [
        Edit("E1", "2026-10-01T00:00:00Z", FakeTracker.app_login, "v1\n"),
        Edit("E2", "2026-10-02T00:00:00Z", FakeTracker.app_login, "v2\n"),
        Edit("E3", "2026-10-03T00:00:00Z", "SaltyReformed", "v3"),
    ]



def test_spec_history_shows_each_change_in_the_window_against_the_version_before(code, capsys):
    """R-BAL174: a review grades every spec change since its branch started."""
    tracker = FakeTracker()
    _versions(tracker)
    assert run(tracker, code, "spec-history", "plan#1", "--since", "2026-10-02") == 0
    out = capsys.readouterr().out
    assert f"edit E2, 2026-10-02T00:00:00Z by {FakeTracker.app_login}" in out
    assert "-v1" in out and "+v2" in out
    assert "edit E3" in out and "by SaltyReformed" in out
    assert "filed" not in out



def test_spec_history_since_a_git_ref_reads_its_commit_date(code, capsys):
    """``--since <branch>`` for a branch with no commit off dev yet: it starts at its tip,
    the commit it was cut from."""
    tracker = FakeTracker()
    _versions(tracker)
    assert run(tracker, code, "spec-history", "plan#1", "--since", "feat/work") == 0
    assert "has not changed since feat/work" in capsys.readouterr().out



def test_spec_revert_restores_a_version_and_refuses_a_change_since_its_read(code, capsys,
                                                                            monkeypatch):
    """By hand: the version named, and only if nobody saved the card between the read of
    its history and the write (the body is read again just before the write: review M3)."""
    tracker = FakeTracker()
    _versions(tracker)
    assert run(tracker, code, "spec-revert", "plan#1", "--to", "E2") == 0
    assert tracker.bodies[1] == "v2\n"
    read = tracker.edits

    def saved_meanwhile(number):
        """The history as read; then someone saves the card."""
        answer = read(number)
        tracker.bodies[number] = "changed meanwhile"
        return answer

    monkeypatch.setattr(tracker, "edits", saved_meanwhile)
    assert run(tracker, code, "spec-revert", "plan#1", "--to", "E1") == 1
    assert "changed while its history was read" in capsys.readouterr().err
    assert tracker.bodies[1] == "changed meanwhile"


# -- the exit statuses ---------------------------------------------------------------------

def test_a_github_failure_exits_2(code, capsys, monkeypatch):
    """Refused is 1; a failed call is 2, with its message."""
    tracker = FakeTracker()

    def broken(_numbers):
        raise GitError("boom")

    monkeypatch.setattr(tracker, "cards", broken)
    assert run(tracker, code, "drop", "plan#1", "--why", "x") == 2
    assert "failed: boom" in capsys.readouterr().err



# -- the review of checkpoint 2 ------------------------------------------------------------

def test_a_marker_naming_a_container_closes_nothing_and_unblocks_nothing(code, capsys):
    """Review H2: a mistyped number closed the container with no claim and unblocked the
    step waiting on it while its leaf was open; a marker names a container when the step
    was split after it was claimed."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, blocked_by=(1,))
    claim(code, 2)
    ship(code, 1)
    assert run(tracker, code, "sync") == 0
    assert not tracker.writes
    assert ("HISTORY: the marker plan/ships/1 names plan#1, a container, which no change "
            "ships") in capsys.readouterr().out
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")



def test_dropping_a_split_step_takes_its_leaves_out_of_the_order(code, capsys):
    """R-BAL190 (narrowing R-BAL185 for ``quill drop``; review L2 first): ``quill drop``
    on a split step notes it there, then drops each open leaf, each with the reason, and the
    split step shows them at the next sync."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert tracker.writes == [
        ("comment", 1, "Dropped: superseded (its leaves still work: plan#2)"),
                              ("comment", 2, "Dropped: superseded"), ("close", 2, "not_planned")]
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[-1] == ("close", 1, "not_planned")


def test_a_split_step_a_person_closes_by_hand_takes_its_leaves_out_of_the_order(code, capsys):
    """R-BAL185 as ruled for a person's close: recorded once, on the split step; its leaves
    inherit it, and sync lists those left open under it."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False, is_open=False,
                state_reason="NOT_PLANNED", touched_by_hand=True)
    tracker.add(2, parent=1)
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: nothing")
    assert run(tracker, code, "sync") == 1
    assert "REPORT: plan#2 is open under plan#1, which was dropped" in capsys.readouterr().out
    assert not tracker.writes


def test_a_split_step_with_no_open_leaf_is_not_dropped_again(code, capsys):
    """Its own state shows its leaves; with none open there is nothing to drop."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", False),), on_board=False)
    tracker.add(2, parent=1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    assert run(tracker, code, "drop", "plan#1", "--why", "again") == 1
    assert "no leaf below it that is still work" in capsys.readouterr().err
    assert not tracker.writes



def test_a_network_error_or_missing_credentials_exit_2_not_a_traceback(code, capsys,
                                                                        monkeypatch):
    """Review M5: both escaped ``main`` as a traceback with exit 1, which means refused."""
    tracker = FakeTracker()
    tracker.add(1)

    def unreachable(_numbers):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(tracker, "cards", unreachable)
    assert run(tracker, code, "drop", "plan#1", "--why", "x") == 2
    assert "failed: connection reset" in capsys.readouterr().err

    def no_credentials():
        raise FileNotFoundError(2, "No such file or directory",
                                "/home/x/.config/shekel-quill/app.json")

    assert quill.main(["next"], connect=no_credentials, root=code) == 2
    assert "app.json" in capsys.readouterr().err



@pytest.mark.parametrize("corrupt", [
    pytest.param(({"app.json": b"{not json"}, "is not JSON"), id="app-json"),
    pytest.param(({"app.json": b'{"client_id": "Iv23client"}', "app.pem": b"not a pem"},
                  "the App's private key cannot be loaded"), id="app-pem"),
])
def test_corrupt_app_credentials_exit_2_not_a_traceback(code, capsys, monkeypatch, tmp_path,
                                                        corrupt):
    """L9d: a corrupt app.json or app.pem escaped ``main`` as a traceback with exit 1,
    which means refused; read by the real connection, it is a tracker quill cannot reach.
    ``corrupt`` is the files written and what the refusal says."""
    files, said = corrupt
    keys = tmp_path / "keys"
    keys.mkdir()
    for name, data in files.items():
        (keys / name).write_bytes(data)
    monkeypatch.setattr(_tracker, "app_credentials", partial(_github.app_credentials, keys))
    assert quill.main(["next"], connect=quill.CONNECT, root=code) == 2
    assert said in capsys.readouterr().err


def test_a_claim_refused_by_a_branch_pushed_by_hand_names_it(code, capsys, monkeypatch):
    """Leaf C3, on the ship marker: a ``card/N`` someone pushed by hand holds no marker, so
    the refusal says when it was claimed is unknown, and names its last commit, as every
    other line names a claim."""
    tracker = FakeTracker()
    tracker.add(1)
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-05T08:00:00-04:00")
    by_hand = commit(code, "by hand", parents=[_run(code, "rev-parse", tracking(DEV))])
    _run(code, "push", "--quiet", "origin", f"{by_hand}:refs/heads/card/1")
    assert run(tracker, code, "claim", "plan#1") == 1
    assert capsys.readouterr().err == (
        "refused: plan#1 is already claimed: card/1 (its tip holds no plan/ships/1, so when it "
        "was claimed is unknown; last commit 2026-10-05T08:00:00-04:00)\n")
    assert _git.on_origin(code) == {1: by_hand}



# -- the review of checkpoint 3 ------------------------------------------------------------

def test_a_container_is_never_moved_back_into_the_order(code, capsys):
    """Review cp3 L-f: ``move`` put a split step back on the board, where its leaves stand."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    assert run(tracker, code, "move", "plan#1", "--top") == 1
    assert "no step split into leaves" in capsys.readouterr().err
    assert not tracker.writes



def test_a_command_line_of_no_form_exits_2_before_anything_is_read(code):
    """Review cp3 L-i: argparse's usage error exits 2, which the docstring now says."""
    with pytest.raises(SystemExit) as stopped:
        quill.main(["file", "finding", "--arc", "balance"], connect=FakeTracker, root=code)
    assert stopped.value.code == 2
    assert "argparse's usage error" in quill.__doc__



def test_spec_history_since_a_branch_starts_where_the_branch_grew_from(code, monkeypatch,
                                                                      capsys):
    """Review cp3 M-2: ``--since <branch>`` read its LAST commit, hiding every spec edit made
    after the work started; it starts at the commit the branch grew from."""
    tree = _run(code, "hash-object", "-t", "tree", "/dev/null")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-09-30T12:00:00+00:00")
    fork = _run(code, "commit-tree", tree, "-m", "dev")
    point_dev(code, fork)
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-01T09:00:00+00:00")
    first = _run(code, "commit-tree", tree, "-p", fork, "-m", "work starts")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-10-03T09:00:00+00:00")
    _run(code, "update-ref", "refs/heads/feat/work",
         _run(code, "commit-tree", tree, "-p", first, "-m", "work continues"))
    tracker = FakeTracker()
    tracker.add(1, body="v3")
    tracker.versions[1] = [Edit("E1", "2026-09-29T00:00:00Z", FakeTracker.app_login, "v1"),
                           Edit("E2", "2026-09-30T18:00:00Z", "SaltyReformed", "v2"),
                           Edit("E3", "2026-10-02T00:00:00Z", "SaltyReformed", "v3")]
    assert run(tracker, code, "spec-history", "plan#1", "--since", "feat/work") == 0
    out = capsys.readouterr().out
    assert "edit E2" in out and "edit E3" in out and "edit E1" not in out



def test_a_split_step_whose_leaves_were_all_dropped_counts_as_dropped(code, tmp_path, capsys):
    """R-BAL187 (review cp3 L-e): what waits on it is released, sync closes it as NOT
    planned, never completed, and no new leaf is filed under it."""
    tracker = FakeTracker()
    tracker.add(7, children=(Child(8, "step", True),), on_board=False)
    tracker.add(8, parent=7)
    tracker.add(9, blocked_by=(7,))
    assert run(tracker, code, "drop", "plan#8", "--why", "re-planned") == 0
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    assert capsys.readouterr().out.startswith("next: plan#9 ")
    assert run(tracker, code, "sync") == 0
    assert "every leaf dropped (R-BAL187)" in capsys.readouterr().out
    assert tracker.writes[-1] == ("close", 7, "not_planned")
    spec = tmp_path / "s"
    spec.write_text("Redo.")
    assert run(tracker, code, "file", "step", "--arc", "balance", "--title", "L7c",
               "--parent", "plan#7", "--body-file", str(spec)) == 1
    assert "a step that is done" in capsys.readouterr().err
    writes = list(tracker.writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.writes == writes, "the drop is shown once"



def test_a_card_linked_outside_the_tracker_is_never_offered_and_is_reported(code, capsys):
    """R-BAL188: the card carrying the link waits, as does every leaf of a step blocked by an
    outside issue; next and sync report the link; every other card works as usual."""
    tracker = FakeTracker()
    outside = (OutsideLink("blocker", "saltyreformed-labs/Shekel#12"),)
    tracker.add(1, outside=outside, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, outside=(OutsideLink("sub-issue", "saltyreformed-labs/Shekel#13"),))
    tracker.add(4)
    assert run(tracker, code, "next") == 0
    out = capsys.readouterr().out
    assert out.startswith("next: plan#4 ")
    assert ("OUTSIDE LINK: plan#1's blocker is saltyreformed-labs/Shekel#12, outside the "
            "tracker") in out and "OUTSIDE LINK: plan#3's sub-issue" in out
    assert run(tracker, code, "sync") == 1
    out = capsys.readouterr().out
    assert "REPORT: plan#1's blocker" in out and "REPORT: plan#3's sub-issue" in out



def test_dropping_a_step_names_every_open_leaf_below_it_and_no_container(code, capsys):
    """Review cp3: the note named only the dropped step's own children, and called a step
    split again a leaf; R-BAL190: every open leaf below is dropped, and no step split
    again, whose state shows its own leaves; review cp4e I: the line printed of the note."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1, children=(Child(4, "step", True),), on_board=False)
    tracker.add(3, parent=1)
    tracker.add(4, parent=2)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    out = capsys.readouterr().out
    assert "dropped plan#3 " in out and "dropped plan#4 " in out and "dropped plan#2 " not in out
    assert "commented on plan#1: dropping its leaves still work, plan#3, plan#4;" in out
    assert ("close", 2, "not_planned") not in tracker.writes
    assert ("comment", 1, "Dropped: superseded (its leaves still work: plan#3, plan#4)"
            ) in tracker.writes



def test_sync_reports_an_open_ruling(code, capsys):
    """Review cp3: a ruling whose close failed kept what waits on it blocked, unreported."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "ruling", True),))
    tracker.add(2, "ruling", parent=1)
    assert run(tracker, code, "sync") == 1
    assert "REPORT: ruling plan#2 is open" in capsys.readouterr().out
    assert not tracker.writes


def test_the_board_add_a_move_makes_is_printed_as_it_lands(code, capsys):
    """Every write is printed as it lands: a step not on the board is added, then placed."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, on_board=False)
    assert run(tracker, code, "move", "plan#2", "--top") == 0
    assert capsys.readouterr().out.startswith("  board: added plan#2 ")
    assert tracker.writes == [("board_add", 2), ("board_place", 2, None)]


def test_show_names_a_cards_links_outside_the_tracker(code, capsys):
    """Review cp4 L-8: ``show`` said "parent: none" for a card whose parent is outside, and
    named none of the links that keep it from being offered."""
    tracker = FakeTracker()
    tracker.add(1, outside=(OutsideLink("parent", "saltyreformed-labs/Shekel#5"),
                            OutsideLink("blocker", "saltyreformed-labs/Shekel#6")))
    assert run(tracker, code, "show", "plan#1") == 0
    out = capsys.readouterr().out
    assert "  parent: saltyreformed-labs/Shekel#5 (outside the tracker)" in out
    assert "  blocker outside the tracker: saltyreformed-labs/Shekel#6" in out
    assert "parent: none" not in out


def test_a_card_linked_outside_the_tracker_cannot_be_claimed(code, capsys):
    """Review cp4 L-11: R-BAL188 never offers it as work, yet ``claim`` took it."""
    tracker = FakeTracker()
    tracker.add(1, outside=(OutsideLink("blocker", "saltyreformed-labs/Shekel#6"),))
    assert run(tracker, code, "claim", "plan#1") == 1
    assert "outside the tracker" in capsys.readouterr().err
    assert not tracker.writes
    assert not _git.on_origin(code)


def test_sync_dry_run_writes_no_close_as_not_planned(code, capsys):
    """Review cp4 M54: the dry run must not write R-BAL187's close either."""
    tracker = FakeTracker()
    tracker.add(7, children=(Child(8, "step", False),), on_board=False)
    tracker.add(8, parent=7, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert "would close as not planned" in capsys.readouterr().out
    assert not tracker.writes


def test_dropping_a_claimed_card_names_the_claim_it_leaves(code, capsys):
    """A drop writes no claim; the claim it leaves is named, with the command that releases it."""
    tracker = FakeTracker()
    tracker.add(1)
    sha = claim(code, 1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    out = capsys.readouterr().out
    assert "  its claim stays, card/1 (claimed 2026-10-04T12:00:00Z; last commit " in out
    assert "): `quill release plan#1` keeps its work and frees it" in out
    assert _git.on_origin(code) == {1: sha}


def test_dropping_a_split_step_never_drops_a_leaf_git_shipped(code, capsys):
    """Review cp4b L6: a leaf whose marker merged before sync closed it was dropped, and
    no sync rule shows a shipped leaf the tool closed as not planned; git's answer wins, and
    undoing shipped work is a revert deleting its marker."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True), Child(3, "step", True)), on_board=False)
    tracker.add(2, parent=1)
    tracker.add(3, parent=1)
    ship(code, 2)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert not [write for write in tracker.writes if write[1] == 2]
    assert ("close", 3, "not_planned") in tracker.writes
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#2", "--why", "superseded") == 1
    assert ("undo it by reverting the change that shipped it, which deletes plan/ships/2, not "
            "by a drop") in capsys.readouterr().err


class _FailOnComment:
    """``tracker.comment`` failing once, on the card numbered ``number``."""

    def __init__(self, tracker, number):
        """Stand in for ``tracker.comment``."""
        self.real, self.number, self.failed = tracker.comment, number, False
        tracker.comment = self

    def __call__(self, number, text):
        """Fail the first comment on ``self.number``."""
        if number == self.number and not self.failed:
            self.failed = True
            raise GitHubError(502, "bad gateway")
        return self.real(number, text)


def test_a_split_step_drop_cut_short_at_its_note_is_finished_by_the_same_command(code):
    """Review cp4b L7: with the note written last, a retry after every leaf landed found no
    open leaf and never wrote it; the note goes first."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False)
    tracker.add(2, parent=1)
    _FailOnComment(tracker, 1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 2
    assert not tracker.writes
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert tracker.writes == [
        ("comment", 1, "Dropped: superseded (its leaves still work: plan#2)"),
                              ("comment", 2, "Dropped: superseded"), ("close", 2, "not_planned")]


def test_a_leaf_held_back_from_above_cannot_be_claimed(code, capsys):
    """Review cp4b L10: ``claim`` refused only the card's own outside link; a leaf under a
    split step blocked from outside the tracker (R-BAL188), or closed by a person
    (R-BAL185), is never offered either."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", True),), on_board=False,
                outside=(OutsideLink("blocker", "saltyreformed-labs/Shekel#6"),))
    tracker.add(2, parent=1)
    tracker.add(3, children=(Child(4, "step", True),), on_board=False, is_open=False,
                state_reason="NOT_PLANNED", touched_by_hand=True)
    tracker.add(4, parent=3)
    assert run(tracker, code, "claim", "plan#2") == 1
    assert "plan#1 above it waits on saltyreformed-labs/Shekel#6" in capsys.readouterr().err
    assert run(tracker, code, "claim", "plan#4") == 1
    assert "plan#3 above it was dropped" in capsys.readouterr().err
    assert not tracker.writes
    assert not _git.on_origin(code)


def test_show_names_an_outside_parent_once(code, capsys):
    """Review cp4b P11: the parent line names it; the outside-link lines name the rest."""
    tracker = FakeTracker()
    tracker.add(1, outside=(OutsideLink("parent", "saltyreformed-labs/Shekel#5"),))
    assert run(tracker, code, "show", "plan#1") == 0
    assert capsys.readouterr().out.count("Shekel#5") == 1


def test_a_closed_card_is_not_dropped_again(code, capsys):
    """Review cp4b P14."""
    tracker = FakeTracker()
    tracker.add(1, is_open=False, state_reason="NOT_PLANNED", closed_by_tool=True)
    assert run(tracker, code, "drop", "plan#1", "--why", "again") == 1
    assert "already closed" in capsys.readouterr().err
    assert not tracker.writes


def test_dropping_a_step_that_owns_findings_drops_the_step(code):
    """Review cp4b P15: a step with findings under it and no step (its sub-issues, as quill
    filed a finding before R-BAL395 and a person may still link one) is no split step."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "finding", True),))
    tracker.add(2, "finding", parent=1)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert ("close", 1, "not_planned") in tracker.writes


def test_open_work_git_says_shipped_cannot_be_claimed(code, capsys):
    """Review cp4c L1: ``next`` never offers it; ``claim`` took it."""
    tracker = FakeTracker()
    tracker.add(1)
    ship(code, 1)
    assert run(tracker, code, "claim", "plan#1") == 1
    assert "shipped in git" in capsys.readouterr().err
    assert not tracker.writes
    assert not _git.on_origin(code)


def test_dropping_a_split_step_drops_a_leaf_git_revived_whatever_its_card_shows(code, capsys):
    """Review cp4c L2 and L6: the leaves were picked by their display, so a leaf the tool
    shows completed whose marker a revert deleted was skipped, then reopened by sync and
    offered under the dropped split step; and a shipped leaf left was not named."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", False), Child(3, "step", True),
                             Child(4, "step", False)), on_board=False)
    tracker.add(2, parent=1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    tracker.add(3, parent=1)
    tracker.add(4, parent=1, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    ship(code, 2, 4)
    unship(code, 2)
    assert run(tracker, code, "drop", "plan#1", "--why", "superseded") == 0
    assert ("close", 2, "not_planned") in tracker.writes
    assert "plan#4 shipped in git, so it is not dropped" in capsys.readouterr().out
    assert not [write for write in tracker.writes if write[1] == 4]


def test_a_card_that_is_not_work_named_by_a_marker_can_still_be_dropped(code):
    """Review cp4c P11: git's answer counts only for work; a question a marker names is
    still the developer's to withdraw."""
    tracker = FakeTracker()
    tracker.add(1, "question")
    ship(code, 1)
    assert run(tracker, code, "drop", "plan#1", "--why", "withdrawn") == 0
    assert ("close", 1, "not_planned") in tracker.writes
