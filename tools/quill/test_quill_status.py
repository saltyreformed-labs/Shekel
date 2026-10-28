"""``quill sync``'s Status pass (rulings plan#1873, plan#1905, plan#1906): it writes the board's
Status of every card it holds from git, puts back a person's edit, prints each write, writes
none under ``--dry-run``, and none at all while the field lacks an option it writes; over the
in-memory tracker (:class:`_fake.FakeTracker`) and the ``code`` fixture's real git."""
from __future__ import annotations

from tools.quill._fake import FakeTracker, begun, claim, piece, run, ship

#: The line sync prints for a card shown as ``status`` that showed ``was``.
SHOWN = "{verb}show {label} as {status} in the board's Status (it showed {was})\n"


def _plan(code):
    """A board as sync finds it on its first run: step 1 claimed, its task 2 begun on the claim
    and its task 3 finished there, its task 4 not begun; step 5 shipped; question 6; and step 7
    closed by a person, which the board still holds and which no read of sync's but the
    Status pass's reads (closed, never marked, never claimed)."""
    tracker = FakeTracker()
    tracker.add(1)
    for task in (2, 3, 4):
        tracker.add_task(task, 1)
    tracker.add(5)
    tracker.add(6, "question")
    tracker.add(7, is_open=False, state_reason="NOT_PLANNED", touched_by_hand=True)
    tracker.board.items.append(7)
    claim(code, 1)
    begun(code, 1, 2)
    piece(code, 1, 3)
    ship(code, 5)
    return tracker


def test_sync_writes_each_board_cards_status_from_git_and_a_rerun_writes_none(code, capsys):
    """Each card the board holds shows git's answer (``_status.wanted``): the claimed step and
    its begun task In Progress, its finished task and the shipped step Done, the task not begun
    Todo, the step a person closed Dropped, the question none; each write printed, in card
    order, after the state writes.  Run again, it finds the board right and writes nothing."""
    tracker = _plan(code)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 0
    assert tracker.board.status == {1: "In Progress", 2: "In Progress", 3: "Done", 4: "Todo",
                                    5: "Done", 7: "Dropped"}
    lines = capsys.readouterr().out.splitlines(keepends=True)
    assert lines[:2] == ["close plan#3 [task, no arc] card 3\n",
                         "close plan#5 [step, balance] card 5\n"]
    assert lines[2:] == [
        SHOWN.format(verb="", label=label, status=status, was="none") for label, status in (
            ("plan#1 [step, balance] card 1", "In Progress"),
            ("plan#2 [task, no arc] card 2", "In Progress"),
            ("plan#3 [task, no arc] card 3", "Done"),
            ("plan#4 [task, no arc] card 4", "Todo"),
            ("plan#5 [step, balance] card 5", "Done"),
            ("plan#7 [step, balance] card 7", "Dropped"))]
    written = list(tracker.board.status_writes)
    assert run(tracker, code, "sync") == 0
    assert tracker.board.status_writes == written and capsys.readouterr().out == ""


def test_a_persons_edit_is_put_back_and_a_question_holding_one_is_cleared(code, capsys):
    """Only quill writes Status (ruling plan#1905): a value a person set on a card is written
    back to git's answer, and a question, which holds none, is cleared."""
    tracker = _plan(code)
    assert run(tracker, code, "sync") == 0
    tracker.board.status.update({4: "Done", 6: "Todo"})
    del tracker.board.status[7]
    written = len(tracker.board.status_writes)
    capsys.readouterr()
    assert run(tracker, code, "sync") == 0
    assert tracker.board.status_writes[written:] == [(4, "Todo"), (6, None), (7, "Dropped")]
    assert capsys.readouterr().out == (
        SHOWN.format(verb="", label="plan#4 [task, no arc] card 4", status="Todo", was="Done")
        + "clear the board's Status of plan#6 [question, balance] card 6, which shows none (it "
          "showed Todo)\n"
        + SHOWN.format(verb="", label="plan#7 [step, balance] card 7", status="Dropped",
                       was="none"))


def test_a_dry_run_prints_each_status_write_and_makes_none(code, capsys):
    """Under ``--dry-run`` each Status write is printed as what would be written, and none is
    sent."""
    tracker = _plan(code)
    capsys.readouterr()
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert not tracker.board.status_writes and not tracker.board.status
    out = capsys.readouterr().out
    assert SHOWN.format(verb="would ", label="plan#7 [step, balance] card 7", status="Dropped",
                        was="none") in out
    assert out.count("in the board's Status") == 6


def test_while_status_lacks_an_option_sync_reports_it_and_writes_no_status(code, capsys):
    """Ruling plan#1906: until the developer adds Dropped on the web, no card's Status is
    written, not even the answers the field could hold, and sync exits 1 on the REPORT naming
    it, its state writes made; a field renamed is reported the same way.  Once it holds every
    option, the next run writes them."""
    tracker = _plan(code)
    field, options = tracker.board.status_field
    tracker.board.status_field = (field, {name: option for name, option in options.items()
                                          if name != "Dropped"})
    capsys.readouterr()
    assert run(tracker, code, "sync") == 1
    assert not tracker.board.status_writes and ("close", 5, "completed") in tracker.writes
    assert capsys.readouterr().out.endswith(
        "REPORT: the board's Status lacks Dropped, so no card's Status is written until it "
        "holds Todo, In Progress, Done, Dropped (rulings plan#1873, plan#1906): add Dropped on "
        "the web, in the board's settings under Status\n")
    tracker.board.status_field = (None, {})
    assert run(tracker, code, "sync") == 1
    assert "REPORT: the board has no single-select field named 'Status'" in (
        capsys.readouterr().out)
    tracker.board.status_field = (field, options)
    assert run(tracker, code, "sync") == 0
    assert len(tracker.board.status_writes) == 6
