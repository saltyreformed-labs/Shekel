"""``quill watch`` (plan#1861 item 7, rulings plan#1871, plan#1874): one listing of ``origin``'s
branches against the tips its last finished pass saw, then sync's producer over each card a
moved branch names, and nothing at all when none moved; over the in-memory tracker
(:class:`_fake.FakeTracker`) and the ``code`` fixture's real git."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tools.ci.gitcmd import MAIN, tracking
from tools.ci.scratch import commit_files, elsewhere
from tools.ci.scratch import run as _run
from tools.quill import _git, _watch, quill
from tools.quill._fake import FailOnce, FakeTracker, claim, finish, named, piece, run, ship, unship

#: The line a pass prints for a card shown as ``status`` that showed ``was``.
SHOWN = "show {label} as {status} in the board's Status (it showed {was})\n"


def _saved(code):
    """The tips the watcher keeps, by branch (``refs/quill/watched/<branch>``)."""
    out = _run(code, "for-each-ref", "--format=%(refname) %(objectname)", _watch.WATCHED)
    return dict(line.removeprefix(_watch.WATCHED).split(" ") for line in out.splitlines())


def _watched(code, capsys):
    """A tracker the watcher has made one first pass over, its output read: step 1 claimed, its
    task 2 begun on the claim, its task 3 not begun; step 5, its task 6 not begun; and step 7,
    unclaimed."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add_task(3, 1)
    tracker.add(5)
    tracker.add_task(6, 5)
    tracker.add(7)
    claim(code, 1)
    named(code, 1, 2)
    assert run(tracker, code, "watch") == 0
    capsys.readouterr()
    return tracker


def _never():
    """A ``connect`` that fails the test: the pass had nothing to read or write."""
    raise AssertionError("watch connected to the tracker though no branch moved")


def test_a_first_pass_decides_over_every_card_as_sync_and_saves_the_tips(code, capsys):
    """With no tip saved it saw nothing, so every card moved: the open shipped step is closed
    and every card the board holds shows git's answer, among them step 7, closed by a person,
    which no read of the pass's but the Status pass's reads; then the tips fetched are saved,
    one per branch ``origin`` lists."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1)
    tracker.add(5)
    tracker.add(7, is_open=False, state_reason="NOT_PLANNED", touched_by_hand=True)
    tracker.board.items.append(7)
    claim(code, 1)
    named(code, 1, 2)
    ship(code, 5)
    capsys.readouterr()
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        "no tips saved, so every card moved: a pass over every card, as sync's\n"
        "close plan#5 [step, balance] card 5\n"
        + "".join(SHOWN.format(label=label, status=status, was="none") for label, status in (
            ("plan#1 [step, balance] card 1", "In Progress"),
            ("plan#2 [task, no arc] card 2", "In Progress"),
            ("plan#5 [step, balance] card 5", "Done"),
            ("plan#7 [step, balance] card 7", "Dropped"))))
    assert _saved(code) == _git.origin_tips(code) and set(_saved(code)) == {
        "dev", "main", "card/1"}


def test_nothing_moved_asks_github_nothing_and_writes_nothing(code, capsys):
    """A pass that finds every branch where the last left it ends at the listing: it connects to
    no tracker, prints nothing and saves nothing new."""
    tracker = _watched(code, capsys)
    saved, writes = _saved(code), list(tracker.writes)
    assert quill.main(["watch"], connect=_never, root=code) == 0
    assert capsys.readouterr().out == "" and _saved(code) == saved and tracker.writes == writes


def test_a_pass_lists_origin_once(code, capsys, monkeypatch):
    """ONE ``git ls-remote`` a pass, the listing ``_git.read`` is handed, whether a branch
    moved or not."""
    tracker = _watched(code, capsys)
    listings = []
    real = _git.git

    def counting(root, *args, **kwargs):
        """``_git``'s git, counting each listing of ``origin``."""
        if args[0] == "ls-remote":
            listings.append(args)
        return real(root, *args, **kwargs)

    monkeypatch.setattr(_git, "git", counting)
    named(code, 1, 3)
    assert run(tracker, code, "watch") == 0
    assert run(tracker, code, "watch") == 0
    assert len(listings) == 2


def test_a_moved_branch_shows_its_finished_task_and_writes_no_other_card(code, capsys):
    """A push to ``card/1`` finishing task 2 (its file on the claim) closes it and shows it
    Done within the pass; a card no moved branch names keeps what it shows, a person's edit of
    it included, for the 3-hourly sync to put back."""
    tracker = _watched(code, capsys)
    tracker.board.status[7] = "Done"
    tip = _git.on_origin(code)[1]
    moved = piece(code, 1, 2)
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        f"card/1 moved: {tip[:12]} -> {moved[:12]}\n"
        "close plan#2 [task, no arc] card 2\n"
        + SHOWN.format(label="plan#2 [task, no arc] card 2", status="Done", was="In Progress"))
    assert tracker.board.status[7] == "Done"
    assert _saved(code)["card/1"] == moved


def test_a_new_branch_shows_its_card_in_progress(code, capsys):
    """A claim made since the last pass: its branch appeared, so its card shows In Progress."""
    tracker = _watched(code, capsys)
    made = claim(code, 7)
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        f"card/7 appeared at {made[:12]}\n"
        + SHOWN.format(label="plan#7 [step, balance] card 7", status="In Progress", was="Todo"))
    assert _saved(code)["card/7"] == made


def test_a_deleted_branch_shows_its_card_and_its_tasks_as_git_says_again(code, capsys):
    """A claim released since the last pass: its branch went, so its card shows Todo again, the
    task begun on it Todo, and the task done on it is reopened, Todo; the tip saved for it is
    deleted."""
    tracker = _watched(code, capsys)
    piece(code, 1, 3)
    assert run(tracker, code, "watch") == 0
    capsys.readouterr()
    tip = _git.on_origin(code)[1]
    _git.release(elsewhere(code), 1, tip, datetime(2026, 10, 27, 12, 0, tzinfo=UTC))
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        f"card/1 went (it was at {tip[:12]})\n"
        "reopen plan#3 [task, no arc] card 3\n"
        + "".join(SHOWN.format(label=f"plan#{number} [{kind}, {arc}] card {number}",
                               status="Todo", was="In Progress" if number < 3 else "Done")
                  for number, kind, arc in ((1, "step", "balance"), (2, "task", "no arc"),
                                            (3, "task", "no arc"))))
    assert "card/1" not in _saved(code)


def test_a_dev_move_syncs_each_card_whose_marker_or_task_file_it_changed(code, capsys):
    """``dev`` moved: each card whose ship marker, and each task whose file, the change from the
    tip saved to the tip fetched adds or deletes is decided, and no other card; a revert of a
    marker reopens its card."""
    tracker = _watched(code, capsys)
    tracker.board.status[7] = "Done"
    old = _git.origin_tips(code)["dev"]
    shipped = ship(code, 5)
    done = finish(code, 6)
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        f"dev moved: {old[:12]} -> {done[:12]}, changing plan/ships/5, plan/tasks/6\n"
        "close plan#5 [step, balance] card 5\n"
        "close plan#6 [task, no arc] card 6\n"
        + SHOWN.format(label="plan#5 [step, balance] card 5", status="Done", was="Todo")
        + SHOWN.format(label="plan#6 [task, no arc] card 6", status="Done", was="Todo"))
    assert shipped != done and tracker.board.status[7] == "Done"
    reverted = unship(code, 5)
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        f"dev moved: {done[:12]} -> {reverted[:12]}, changing plan/ships/5\n"
        "reopen plan#5 [step, balance] card 5\n"
        + SHOWN.format(label="plan#5 [step, balance] card 5", status="Todo", was="Done"))


def test_a_main_move_syncs_each_card_whose_marker_it_changed(code, capsys):
    """A hotfix on ``main`` ships before ``main`` is merged into ``dev`` (R-BAL359), so a move
    of ``main`` is read as ``dev``'s is; one changing no marker nor task file names no card."""
    tracker = _watched(code, capsys)
    other = elsewhere(code)
    old = _run(other, "rev-parse", tracking(MAIN))
    hotfix = commit_files(other, old, "hotfix", {_git.marker(7): "claimed x\n"})
    _run(other, "push", "--quiet", "origin", f"{hotfix}:refs/heads/main")
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        f"main moved: {old[:12]} -> {hotfix[:12]}, changing plan/ships/7\n"
        "close plan#7 [step, balance] card 7\n"
        + SHOWN.format(label="plan#7 [step, balance] card 7", status="Done", was="Todo"))
    plain = commit_files(other, hotfix, "docs", {"README": "x\n"})
    _run(other, "push", "--quiet", "origin", f"{plain}:refs/heads/main")
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        f"main moved: {hotfix[:12]} -> {plain[:12]}, changing no marker or task file\n")


def test_a_card_a_decision_reads_is_decided_with_its_own_claim(code, capsys):
    """Step 1 moved, and is blocked by step 20, claimed, whose task 21 is done on its claim
    and was closed by the tool: the pass reads 20 to decide 1, so it reads 20's claim and its
    tasks too, and leaves 21 done -- never reopened because 20's claim went unread."""
    tracker = _watched(code, capsys)
    tracker.add(20)
    tracker.add_task(21, 20)
    claim(code, 20)
    piece(code, 20, 21)
    assert run(tracker, code, "watch") == 0
    assert tracker.cards_by_number[21].closed_by_tool
    tracker.cards_by_number[1] = replace(tracker.cards_by_number[1], blocked_by=(20,))
    writes = len(tracker.writes)
    named(code, 1, 3)
    capsys.readouterr()
    assert run(tracker, code, "watch") == 0
    assert not tracker.writes[writes:] and not tracker.cards_by_number[21].is_open
    assert "plan#21" not in capsys.readouterr().out


def test_a_dry_run_writes_nothing_and_leaves_the_move_to_the_next_pass(code, capsys):
    """Under ``--dry-run`` each write is printed as what would be written, none is made, and
    no tip is saved: the next pass finds the same move and makes it."""
    tracker = _watched(code, capsys)
    saved, writes, shown = _saved(code), list(tracker.writes), dict(tracker.board.status)
    piece(code, 1, 2)
    assert run(tracker, code, "watch", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "would close plan#2 [task, no arc] card 2\n" in out
    assert "would show plan#2 [task, no arc] card 2 as Done" in out
    assert (_saved(code), tracker.writes, tracker.board.status) == (saved, writes, shown)
    assert run(tracker, code, "watch") == 0
    assert tracker.board.status[2] == "Done" and not tracker.cards_by_number[2].is_open


def test_a_pass_a_failure_cut_short_saves_no_tip_and_the_next_finishes_it(code, capsys):
    """A failed write ends the pass with exit 2 and saves no tip, so the next pass decides the
    same cards again, writing only what is still owed."""
    tracker = _watched(code, capsys)
    saved = _saved(code)
    piece(code, 1, 2)
    FailOnce(tracker.board, "set_status")
    assert run(tracker, code, "watch") == 2
    assert _saved(code) == saved and not tracker.cards_by_number[2].is_open
    capsys.readouterr()
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out.endswith(
        SHOWN.format(label="plan#2 [task, no arc] card 2", status="Done", was="In Progress"))
    assert _saved(code) == _git.origin_tips(code)


def test_a_report_about_a_card_it_decides_is_printed_and_exits_zero(code, capsys):
    """A REPORT about a card the pass decides -- a task done in git that a person reopened --
    is printed as sync prints it, and the pass exits 0, the tips saved: a REPORT alerts from
    the 3-hourly sync only (ruling plan#1907), so the watcher's unit reads every other exit
    status as a failure."""
    tracker = _watched(code, capsys)
    piece(code, 1, 2)
    assert run(tracker, code, "watch") == 0
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=True,
                                         closed_by_tool=False, touched_by_hand=True)
    named(code, 1, 3)
    capsys.readouterr()
    assert run(tracker, code, "watch") == 0
    assert "REPORT: task plan#2 is done in git" in capsys.readouterr().out
    assert _saved(code) == _git.origin_tips(code)


@pytest.mark.parametrize("missing", ["dev", "main"])
def test_a_tip_of_dev_or_main_not_saved_is_a_pass_over_every_card(code, capsys, missing):
    """With ``dev``'s or ``main``'s tip not saved there is no tip to compare it with, so the
    pass is a first pass, over every card."""
    tracker = _watched(code, capsys)
    _run(code, "update-ref", "-d", f"{_watch.WATCHED}{missing}")
    tracker.board.status[7] = "Done"
    assert run(tracker, code, "watch") == 0
    out = capsys.readouterr().out
    assert out.startswith("no tips saved, so every card moved")
    assert tracker.board.status[7] == "Todo" and missing in _saved(code)


def test_a_pass_names_nothing_of_a_task_file_outside_the_cards_it_decides(code, capsys):
    """Task 6's file is on ``dev``, and a later pass decides only card 1, whose branch moved:
    it reads no line of task 6's, nor names its file as one marking nothing done."""
    tracker = _watched(code, capsys)
    finish(code, 6)
    assert run(tracker, code, "watch") == 0
    capsys.readouterr()
    tip = _git.on_origin(code)[1]
    moved = named(code, 1, 3)
    assert run(tracker, code, "watch") == 0
    assert capsys.readouterr().out == (
        f"card/1 moved: {tip[:12]} -> {moved[:12]}\n"
        + SHOWN.format(label="plan#3 [task, no arc] card 3", status="In Progress", was="Todo"))


def test_a_task_file_a_claim_wrote_is_read_by_the_task_it_names(code, capsys):
    """``card/1``'s claim writes the file of task 6, a task under step 5: the pass reads task 6
    by that file, which no task under card 1 names, so it reports the file as on the wrong
    branch -- not as naming a card that does not exist."""
    tracker = _watched(code, capsys)
    piece(code, 1, 6)
    assert run(tracker, code, "watch") == 0
    assert ("REPORT: card/1's tip holds plan/tasks/6, but task plan#6 is under plan#5, not "
            "plan#1, so it marks nothing done") in capsys.readouterr().out


def test_a_push_between_the_listing_and_the_fetch_is_read_as_a_move(code, capsys, monkeypatch):
    """The tips compared with those saved are the ones the fetch brought, never the listing's:
    a push to ``card/1`` landing after the listing (which shows only ``card/7`` new) is read in
    this pass, and its tip saved, never passed over and saved as seen."""
    tracker = _watched(code, capsys)
    claim(code, 7)
    listed = _git.origin_tips(code)
    finished = piece(code, 1, 2)
    monkeypatch.setattr(_git, "origin_tips", lambda _root: listed)
    assert run(tracker, code, "watch") == 0
    assert f"-> {finished[:12]}\n" in capsys.readouterr().out
    assert not tracker.cards_by_number[2].is_open and _saved(code)["card/1"] == finished
