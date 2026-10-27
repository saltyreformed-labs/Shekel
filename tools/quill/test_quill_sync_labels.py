"""``quill sync``'s arc-label pass (ruling ``balance:R-BAL354``, plan#1811): each card's arc
labels put back to display its Arc, and a REPORT for each card whose Arc no label can fix,
over the in-memory tracker (:class:`_fake.FakeTracker`) and the ``code`` fixture's real git.
"""
from __future__ import annotations

from tools.quill._fake import FailOnce, FakeTracker, run, ship

#: How the pass names its correction of a card, before what it removes and adds.
_BACK = "back plan#{} [{}, {}] card {}'s arc labels to display its Arc (R-BAL354): "


def _back(number, kind, arc, remove, add):
    """The line the pass prints for one correction (under a dry run, "would put" for "put")."""
    return "put " + _BACK.format(number, kind, arc, number) + f"removing {remove}, adding {add}\n"


def test_sync_puts_each_cards_arc_labels_back_to_display_its_arc(code, capsys):
    """A label a person swapped, removed or added beside the right one is put back from the
    Arc, and a task's stray arc label removed; every other label is kept, and a card whose arc
    labels display its Arc is not written.  A dry run prints each and writes nothing; the
    second run writes nothing more."""
    tracker = FakeTracker()
    tracker.add(1, labels=("recurrence",))
    tracker.add(2, "question", labels=(), arc="salary")
    tracker.add(3, labels=("balance", "moves-money"))
    tracker.add(4, labels=("balance", "recurrence"))
    tracker.add_task(5, 3, labels=("balance",))
    lines = [_back(1, "step", "balance", ["recurrence"], ["balance"]),
             _back(2, "question", "salary", [], ["salary"]),
             _back(4, "step", "balance", ["recurrence"], []),
             _back(5, "task", "no arc", ["balance"], [])]
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert capsys.readouterr().out == "".join(line.replace("put ", "would put ", 1)
                                              for line in lines)
    assert not tracker.writes
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == "".join(lines)
    assert tracker.writes == [("relabel", 1, ("balance",), ("recurrence",)),
                              ("relabel", 2, ("salary",), ()),
                              ("relabel", 4, (), ("recurrence",)),
                              ("relabel", 5, (), ("balance",))]
    assert {n: tracker.cards_by_number[n].labels for n in range(1, 6)} == {
        1: ("balance",), 2: ("salary",), 3: ("balance", "moves-money"), 4: ("balance",),
        5: ()}
    assert run(tracker, code, "sync") == 0
    assert capsys.readouterr().out == ""
    assert len(tracker.writes) == 4


def test_a_card_whose_arc_no_label_can_fix_is_reported_and_left_as_it_is(code, capsys):
    """A card but a task holding no Arc (views_spec s.3: no label can be derived from
    nothing), and a task holding one (R-BAL386: its arc is its card's), are each a REPORT,
    which makes sync exit 1, and neither card is written: the first keeps the labels that say
    what its Arc should be."""
    tracker = FakeTracker()
    tracker.add(1, labels=("recurrence",), arc=None)
    tracker.add(2, "ruling", labels=(), arc=None, on_board=False, is_open=False,
                state_reason="COMPLETED", closed_by_tool=True)
    tracker.add_task(3, 1, arc="balance")
    assert run(tracker, code, "sync") == 1
    assert capsys.readouterr().out == (
        "REPORT: plan#1 [step, no arc] card 1 holds no Arc, its arc's one home (R-BAL349), so "
        "no arc label can display it: set its Arc on the web (its labels: ['recurrence'])\n"
        "REPORT: plan#2 [ruling, no arc] card 2 holds no Arc, its arc's one home (R-BAL349), so "
        "no arc label can display it: set its Arc on the web (its labels: [])\n"
        "REPORT: plan#3 [task, balance] card 3 is a task, whose arc is its card's (R-BAL386), "
        "and holds the Arc 'balance': clear it on the web\n")
    assert not tracker.writes
    assert run(tracker, code, "sync", "--dry-run") == 1


def test_a_card_holding_an_arc_none_of_quills_is_reported_and_never_relabelled(code, capsys):
    """An Arc none of quill's arcs (an option added to the field on the web before the code
    knows it) is displayed by no arc label, so a put-back would add one on every run and never
    settle (plan#1811's review, L1): it is a REPORT, and the card is left as it is."""
    tracker = FakeTracker()
    tracker.add(1, labels=("balance",), arc="tracker")
    assert run(tracker, code, "sync") == 1
    assert capsys.readouterr().out == (
        "REPORT: plan#1 [step, tracker] card 1 holds the Arc 'tracker', none of quill's arcs "
        "(balance, recurrence, pay_calendar, credit_card, bank_import, salary), so no arc label "
        "can display it: set its Arc on the web to one of them\n")
    assert not tracker.writes


def test_a_card_still_being_filed_is_put_back_too_and_keeps_its_mark(code, capsys):
    """Every card is read, a marked one too (plan#1811 S4, amending its plan's C5): a card is
    born holding its Arc and its arc label, and a filing run again compares its Arc and its
    other labels, never its arc labels, so the correction changes nothing it decides."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "finding", labels=("filing", "recurrence"), blocked_by=(1,), on_board=False)
    run(tracker, code, "sync")
    assert _back(2, "finding", "balance", ["recurrence"], ["balance"]) in capsys.readouterr().out
    assert tracker.writes == [("relabel", 2, ("balance",), ("recurrence",))]
    assert tracker.cards_by_number[2].labels == ("filing", "balance")


def test_the_pass_follows_every_state_write_and_a_failed_one_is_finished_next_run(code):
    """The pass comes after sync's state writes and before its put-on of a task (ruling
    plan#1842): a correction GitHub cut off fails the run after its close landed, and costs
    that run its put-on; the next run makes both, the correction first."""
    tracker = FakeTracker()
    tracker.add(1, labels=("recurrence",))
    tracker.add(9)
    tracker.add_task(2, 1, on_board=False)
    ship(code, 9)
    FailOnce(tracker, "relabel")
    assert run(tracker, code, "sync") == 2
    assert tracker.writes == [("close", 9, "completed")]
    assert run(tracker, code, "sync") == 0
    assert tracker.writes[1:] == [("relabel", 1, ("balance",), ("recurrence",)),
                                  ("board_add", 2)]
