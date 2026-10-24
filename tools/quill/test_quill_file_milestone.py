"""``quill file step --parent``: a leaf is filed in its split step's milestone, which the split
step gives up (the coordinator's code call, 14:4x 2026-10-21, under ruling
``balance:R-BAL274``'s one milestone per card; a second leaf's reading, R-BAL207, X-cx S3a),
over :class:`_fake.FakeTracker` and a throwaway git repository (the ``code`` fixture); and
the tracker's read and write it takes, against scripted GitHub answers.  Nothing here calls
GitHub.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill._fake import FailOnce, FakeTracker, leaf_filing, run
from tools.quill._tracker import Board, Child, Milestone, Tracker, TrackerError
from tools.quill.setup_tracker import FILING, PLAN


def _in_milestones(tracker, **held):
    """``tracker`` holding milestones #1 and #2, and each card ``cN`` named in ``held`` in the
    one given."""
    for number in (1, 2):
        tracker.milestones_by_number[number] = Milestone(number, f"O{number}", "d")
    tracker.milestone_of.update({int(name[1:]): number for name, number in held.items()})
    return tracker


def _split(tracker, parent, *leaves):
    """``parent`` split into ``leaves``, each a step linked under it and off the board."""
    held = tracker.cards_by_number[parent]
    for number in leaves:
        tracker.add(number, parent=parent, on_board=False)
    tracker.cards_by_number[parent] = replace(
        held, children=(*held.children, *(Child(number, "step", True) for number in leaves)))


def test_the_first_leaf_takes_its_split_steps_milestone_and_the_split_step_gives_it_up(
        code, tmp_path, capsys):
    """Step #1 in milestone #1, split: the leaf is born in #1 (its create carries it), and #1
    leaves it after the leaf is linked and placed, before the leaf's mark comes off."""
    tracker = _in_milestones(FakeTracker(), c1=1)
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 0
    assert tracker.milestone_of == {2: 1}
    assert tracker.writes == [("create", 2, "step", "half", ("balance", FILING), 1),
                              ("add_child", 1, 2), ("board_add", 2), ("board_place", 2, "PVTI_1"),
                              ("board_remove", 1), ("clear_milestone", 1), ("unmark", 2)]
    out = capsys.readouterr().out
    assert "  in milestone #1, its split step's" in out
    assert "  plan#1 left milestone #1: a split step keeps none" in out


def test_a_second_leaf_joins_the_milestone_its_first_leaf_carries(code, tmp_path):
    """Split already, #1 holds no milestone: the next leaf takes the one its leaves hold, so
    it never groups under no milestone, and #1 is written nothing more."""
    tracker = _in_milestones(FakeTracker(), c1=2)
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "first half")) == 0
    made = len(tracker.writes)
    assert run(tracker, code, *leaf_filing(tmp_path, "second half")) == 0
    assert tracker.milestone_of == {2: 2, 3: 2}
    assert tracker.writes[made] == ("create", 3, "step", "second half", ("balance", FILING), 2)
    assert ("clear_milestone", 1) not in tracker.writes[made:]


def test_a_leaf_takes_the_milestone_held_below_a_step_split_twice(code, tmp_path):
    """#1 split into #2, itself split into #3 in milestone #1 (salary X-av's shape at the
    cutover): a new leaf of #1 reads the leaves below it, nested ones included."""
    tracker = _in_milestones(FakeTracker(), c3=1)
    tracker.add(1)
    _split(tracker, 1, 2)
    _split(tracker, 2, 3)
    assert run(tracker, code, *leaf_filing(tmp_path, "more")) == 0
    assert tracker.milestone_of == {3: 1, 4: 1}
    assert ("clear_milestone", 1) not in tracker.writes


def test_leaves_in_differing_milestones_file_the_leaf_in_none_and_say_which(
        code, tmp_path, capsys):
    """#1's leaves #2 and #3 sit in milestones #1 and #2: no one of them is its split step's,
    so the leaf is filed in none and the command names both, for a person to set."""
    tracker = _in_milestones(FakeTracker(), c2=1, c3=2)
    tracker.add(1)
    _split(tracker, 1, 2, 3)
    assert run(tracker, code, *leaf_filing(tmp_path, "third")) == 0
    assert 4 not in tracker.milestone_of
    assert tracker.writes[0] == ("create", 4, "step", "third", ("balance", FILING))
    assert ("  in no milestone: the leaves below its split step are in #1, #2, so which it "
            "joins is a person's call: set it on the web") in capsys.readouterr().out


def test_a_leaf_with_no_milestone_held_anywhere_is_filed_as_before(code, tmp_path):
    """No milestone on the split step or below it: the create is the one quill always sent,
    with no milestone, and the split step is written nothing more."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 0
    assert tracker.writes[0] == ("create", 2, "step", "half", ("balance", FILING))
    assert not tracker.milestone_of
    assert ("clear_milestone", 1) not in tracker.writes


def test_a_run_cut_before_the_clear_is_finished_by_the_same_command_filing_no_second_leaf(
        code, tmp_path):
    """The clear fails after the leaf is filed in #1: the split step still holds #1 and the
    leaf is still marked.  Run again, the command finds the leaf (R-BAL186), files no
    second one, and clears #1."""
    tracker = _in_milestones(FakeTracker(), c1=1)
    tracker.add(1)
    FailOnce(tracker, "clear_milestone")
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 2
    assert tracker.milestone_of == {1: 1, 2: 1}
    assert FILING in tracker.cards_by_number[2].labels
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 0
    assert [write for write in tracker.writes if write[0] == "create"] == [
        ("create", 2, "step", "half", ("balance", FILING), 1)]
    assert tracker.milestone_of == {2: 1}
    assert FILING not in tracker.cards_by_number[2].labels


@pytest.mark.parametrize("argv", [
    ("file", "step", "--arc", "balance", "--title", "Top"),
    ("file", "finding", "--arc", "balance", "--title", "Twice", "--owner", "plan#1",
     "--text", "The report counts a refund twice."),
])
def test_a_filing_that_is_no_leaf_reads_no_milestone(code, tmp_path, argv):
    """Only a leaf's filing reads milestones: a top-level step's and a finding's never ask."""
    def refused(_numbers):
        raise AssertionError("a filing that is no leaf read milestones")

    spec = tmp_path / "spec.md"
    spec.write_text("Build it.")
    tracker = FakeTracker()
    tracker.add(1)
    tracker.milestones_of = refused
    body = ("--body-file", str(spec)) if argv[1] == "step" else ()
    assert run(tracker, code, *argv, *body) == 0


# -- the tracker's read and write ------------------------------------------------------------

class _Scripted:
    """A GitHub answering a lookup by number with ``issues`` (``cN`` keys) and a REST call
    with ``answer``; it keeps every call."""

    def __init__(self, issues=None, answer=None):
        """Hold the answers."""
        self.issues, self.answer, self.sent = issues or {}, answer, []

    def graphql_lookup(self, query, **_variables):
        """Keep the query; answer the lookup."""
        self.sent.append(("graphql", query))
        return {"repository": self.issues}

    def rest(self, method, path, body=None):
        """Keep the call; answer it."""
        self.sent.append((method, path, body))
        return self.answer


def _tracker(github):
    """The plan's tracker speaking through ``github``."""
    return Tracker(github, Board(github, PLAN, "B"), "shekel-quill")


def test_milestones_of_reads_each_cards_milestone_by_number_and_none_for_none():
    """One lookup through the read by number, asking each card's milestone alone; a card in
    none reads None, a number nobody filed is absent."""
    github = _Scripted({"c3": {"milestone": {"number": 7}}, "c5": {"milestone": None},
                        "c9": None})
    assert _tracker(github).milestones_of([3, 5, 9]) == {3: 7, 5: None}
    assert [what for what, _ in github.sent] == ["graphql"]
    query = github.sent[0][1]
    assert "c3: issue(number: 3) { milestone { number } }" in query


def test_clear_milestone_sends_none_and_refuses_an_answer_still_in_one():
    """The split step's milestone is cleared by one PATCH, read back."""
    github = _Scripted(answer={"number": 1, "milestone": None})
    _tracker(github).clear_milestone(1)
    assert github.sent == [("PATCH", f"{PLAN.path}/issues/1", {"milestone": None})]
    with pytest.raises(TrackerError, match="plan#1 is still in milestone 4: fix it by hand"):
        _tracker(_Scripted(answer={"number": 1, "milestone": {"number": 4}})).clear_milestone(1)
