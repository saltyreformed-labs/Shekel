"""``quill file step --parent``: a leaf is filed in its split step's milestone while the split
step holds one, which it then gives up (the coordinator's code call, 14:4x 2026-10-21, under
ruling ``balance:R-BAL274``'s one milestone per card), else in none, the milestones its
leaves hold named (X-cx S3a's review, HIGH 1); a leaf dropped before its filing finished
gives it back (MEDIUM 2) -- over :class:`_fake.FakeTracker` and a throwaway git repository
(the ``code`` fixture); and the tracker's read and write it takes, against scripted GitHub
answers.  Nothing here calls GitHub.
"""
from __future__ import annotations

import re
from dataclasses import replace

import pytest

from tools.quill._fake import FailOnce, FakeTracker, leaf_filing, run
from tools.quill._github import GitHubError
from tools.quill._tracker import Board, Child, Tracker, TrackerError
from tools.quill.setup_tracker import FILING, PLAN


def _in_milestones(tracker, **held):
    """``tracker`` holding milestones #1 and #2, and each card ``cN`` named in ``held`` in the
    one given."""
    tracker.milestones.update((1, 2))
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
    assert tracker.writes == [("create", 2, "step", "half", ("balance", FILING), "balance", 1),
                              ("add_child", 1, 2), ("board_add", 2), ("board_place", 2, "PVTI_1"),
                              ("board_remove", 1), ("set_milestone", 1, None), ("unmark", 2)]
    out = capsys.readouterr().out
    assert "  in milestone #1, its split step's" in out
    assert "  plan#1 left milestone #1: a split step keeps none" in out


def test_a_leaf_of_a_split_step_holding_none_is_filed_in_none_and_names_its_leaves_one(
        code, tmp_path, capsys):
    """The migration's shape (balance X-bi-6-4d at the cutover): #1 split, holding no
    milestone, its leaf #2 in #2, its OWN outcome's, and #3 in none.  The tracker cannot tell
    #2's from one carried from #1, so the new leaf takes none, and the command names #2 with
    #2 for a person to set; #1 is written nothing more (review HIGH 1: it took #2 before)."""
    tracker = _in_milestones(FakeTracker(), c2=2)
    tracker.add(1)
    _split(tracker, 1, 2, 3)
    assert run(tracker, code, *leaf_filing(tmp_path, "more")) == 0
    assert tracker.milestone_of == {2: 2}
    assert tracker.writes[0] == ("create", 4, "step", "more", ("balance", FILING), "balance")
    assert not [write for write in tracker.writes if write[0] == "set_milestone"]
    out = capsys.readouterr().out
    assert ("  in no milestone: its split step holds none, so the tracker records no outcome "
            "for it; below it, plan#2 is in milestone #2: if this leaf belongs with one of "
            "them, set it on the web") in out
    assert "its split step's" not in out


def test_a_second_leaf_after_the_first_took_the_milestone_is_filed_in_none(code, tmp_path):
    """#1 in #2, split twice by quill: the first leaf takes #2 and #1 gives it up, so the
    second finds #1 holding none and is filed in none."""
    tracker = _in_milestones(FakeTracker(), c1=2)
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "first half")) == 0
    made = len(tracker.writes)
    assert run(tracker, code, *leaf_filing(tmp_path, "second half")) == 0
    assert tracker.milestone_of == {2: 2}
    assert tracker.writes[made] == ("create", 3, "step", "second half", ("balance", FILING),
                                    "balance")


def test_a_leaf_of_a_step_split_twice_names_the_milestone_held_below_it(
        code, tmp_path, capsys):
    """#1 split into #2, itself split into #3 in milestone #1 (salary X-av's shape at the
    cutover): the leaves read below #1 are nested ones too, so #3 is named with #1."""
    tracker = _in_milestones(FakeTracker(), c3=1)
    tracker.add(1)
    _split(tracker, 1, 2)
    _split(tracker, 2, 3)
    assert run(tracker, code, *leaf_filing(tmp_path, "more")) == 0
    assert tracker.milestone_of == {3: 1}
    assert "; below it, plan#3 is in milestone #1: if this leaf" in capsys.readouterr().out


def test_leaves_in_differing_milestones_file_the_leaf_in_none_and_say_which(
        code, tmp_path, capsys):
    """#1's leaves #2 and #3 sit in milestones #1 and #2: the leaf is filed in none and the
    command names each leaf with its milestone, for a person to set."""
    tracker = _in_milestones(FakeTracker(), c2=1, c3=2)
    tracker.add(1)
    _split(tracker, 1, 2, 3)
    assert run(tracker, code, *leaf_filing(tmp_path, "third")) == 0
    assert 4 not in tracker.milestone_of
    assert tracker.writes[0] == ("create", 4, "step", "third", ("balance", FILING), "balance")
    assert ("; below it, plan#2 is in milestone #1, plan#3 is in milestone #2: if this leaf "
            "belongs with one of them, set it on the web") in capsys.readouterr().out


def test_a_leaf_with_no_milestone_held_anywhere_is_filed_as_before(code, tmp_path, capsys):
    """No milestone on the split step or below it: the create is the one quill always sent,
    with no milestone, the split step is written nothing more, and no milestone is named."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 0
    assert tracker.writes[0] == ("create", 2, "step", "half", ("balance", FILING), "balance")
    assert not tracker.milestone_of
    assert not [write for write in tracker.writes if write[0] == "set_milestone"]
    assert "milestone" not in capsys.readouterr().out


def test_a_run_cut_before_the_clear_is_finished_by_the_same_command_filing_no_second_leaf(
        code, tmp_path):
    """The clear fails after the leaf is filed in #1: the split step still holds #1 and the
    leaf is still marked.  Run again, the command finds the leaf (R-BAL186), files no
    second one, and clears #1."""
    tracker = _in_milestones(FakeTracker(), c1=1)
    tracker.add(1)
    FailOnce(tracker, "set_milestone")
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 2
    assert tracker.milestone_of == {1: 1, 2: 1}
    assert FILING in tracker.cards_by_number[2].labels
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 0
    assert [write for write in tracker.writes if write[0] == "create"] == [
        ("create", 2, "step", "half", ("balance", FILING), "balance", 1)]
    assert tracker.milestone_of == {2: 1}
    assert FILING not in tracker.cards_by_number[2].labels


def _cut_at(code, tmp_path, write):
    """#1 in milestone #1, split into #2 by a run whose ``write`` failed: the create carried
    #1 onto #2, which is still marked."""
    tracker = _in_milestones(FakeTracker(), c1=1)
    tracker.add(1)
    FailOnce(tracker, write)
    assert run(tracker, code, *leaf_filing(tmp_path, "half")) == 2
    assert FILING in tracker.cards_by_number[2].labels
    return tracker


def _milestone_writes(tracker, made):
    """The milestone writes after the first ``made``, in order."""
    return [write for write in tracker.writes[made:] if write[0] == "set_milestone"]


def test_a_drop_of_a_leaf_whose_filing_never_finished_gives_its_milestone_back(
        code, tmp_path, capsys):
    """Cut at the unmark, after #1 gave #1 up: dropping #2 (R-BAL205: never part of the split)
    puts #1, a plain step again, back in #1 and takes #2 out, both before #2's unlink,
    which is last (review MEDIUM 2: #1 stayed in none, #2 in #1)."""
    tracker = _cut_at(code, tmp_path, "unmark")
    assert tracker.milestone_of == {2: 1}
    made = len(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "drop", "plan#2", "--why", "kept whole") == 0
    assert tracker.milestone_of == {1: 1}
    assert _milestone_writes(tracker, made) == [("set_milestone", 1, 1),
                                                ("set_milestone", 2, None)]
    names = [write[0] for write in tracker.writes[made:]]
    assert names.index("set_milestone") < names.index("remove_child")
    out = capsys.readouterr().out
    assert "  plan#1 put back in milestone #1, from plan#2" in out
    assert "  plan#2 taken out of milestone #1" in out


def test_a_drop_cut_before_the_split_step_gave_it_up_only_clears_the_leaf(code, tmp_path):
    """Cut at #1's clear: both hold #1.  Dropping #2 leaves #1's alone and takes #2's off, so
    #1's progress never counts a card that was no part of it."""
    tracker = _cut_at(code, tmp_path, "set_milestone")
    assert tracker.milestone_of == {1: 1, 2: 1}
    made = len(tracker.writes)
    assert run(tracker, code, "drop", "plan#2", "--why", "kept whole") == 0
    assert tracker.milestone_of == {1: 1}
    assert _milestone_writes(tracker, made) == [("set_milestone", 2, None)]


def test_a_drop_cut_after_the_give_back_is_finished_by_the_same_command(code, tmp_path):
    """The drop's own run cut at #2's take-off, after #1 got #1 back: run again, it finds #1
    holding one, writes it nothing, and takes #2's off before the unlink."""
    tracker = _cut_at(code, tmp_path, "unmark")
    real = tracker.set_milestone

    def failing_take_off(number, milestone):
        """#2's take-off fails once; every other milestone write lands."""
        if number == 2 and milestone is None and not failing_take_off.failed:
            failing_take_off.failed = True
            raise GitHubError(502, "bad gateway")
        real(number, milestone)

    failing_take_off.failed = False
    tracker.set_milestone = failing_take_off
    assert run(tracker, code, "drop", "plan#2", "--why", "kept whole") == 2
    assert tracker.milestone_of == {1: 1, 2: 1}
    assert tracker.cards_by_number[2].parent == 1
    made = len(tracker.writes)
    assert run(tracker, code, "drop", "plan#2", "--why", "kept whole") == 0
    assert tracker.milestone_of == {1: 1}
    assert _milestone_writes(tracker, made) == [("set_milestone", 2, None)]
    assert tracker.cards_by_number[2].parent is None


def test_sync_after_a_person_closes_a_leaf_still_being_filed_gives_its_milestone_back(
        code, tmp_path, capsys):
    """The same undo through ``sync`` (R-BAL205): a dry run prints it and writes nothing, and
    the run itself puts #1 back in #1 and takes #2 out."""
    tracker = _cut_at(code, tmp_path, "unmark")
    tracker.cards_by_number[2] = replace(tracker.cards_by_number[2], is_open=False,
                                         state_reason="NOT_PLANNED", touched_by_hand=True)
    made = len(tracker.writes)
    capsys.readouterr()
    assert run(tracker, code, "sync", "--dry-run") == 0
    assert not tracker.writes[made:]
    out = capsys.readouterr().out
    assert "  plan#1 would be put back in milestone #1, from plan#2" in out
    assert "  plan#2 would be taken out of milestone #1" in out
    assert run(tracker, code, "sync") == 0
    assert tracker.milestone_of == {1: 1}
    assert _milestone_writes(tracker, made) == [("set_milestone", 1, 1),
                                                ("set_milestone", 2, None)]


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


@pytest.mark.parametrize("milestone, answer", [(None, None), (3, {"number": 3})])
def test_set_milestone_sends_one_patch_read_back(milestone, answer):
    """A card is put in a milestone, or with None taken out of its one, by one PATCH."""
    github = _Scripted(answer={"number": 1, "milestone": answer})
    _tracker(github).set_milestone(1, milestone)
    assert github.sent == [("PATCH", f"{PLAN.path}/issues/1", {"milestone": milestone})]


@pytest.mark.parametrize("milestone, answer, says", [
    (None, {"number": 4}, "plan#1 is in milestone 4, not None: fix it by hand"),
    (3, None, "plan#1 is in milestone None, not 3: fix it by hand"),
    (3, {"number": 4}, "plan#1 is in milestone 4, not 3: fix it by hand"),
])
def test_set_milestone_refuses_an_answer_in_another_milestone(milestone, answer, says):
    """GitHub's answer showing the card anywhere else is a failed write."""
    tracker = _tracker(_Scripted(answer={"number": 1, "milestone": answer}))
    with pytest.raises(TrackerError, match=re.escape(says)):
        tracker.set_milestone(1, milestone)
