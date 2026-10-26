"""``quill finding-file`` and ``quill hand`` (step plan#1772): a finding fixed inside its owner
step's pull request ships in it (ruling plan#1777), its marker written on the owner's branch;
and an open finding is handed from its one owner to another step, a step a branch builds
(ruling plan#1784).  Each refusal forced to fire, over :class:`_fake.FakeTracker` and the
``code`` fixture's real git (``conftest.py``).
"""
from __future__ import annotations

import re

from tools.ci.scratch import run as _run
from tools.quill._fake import FailOnce, FakeTracker, claim, finding_filing, run, ship
from tools.quill._tracker import Child, OutsideLink

_FIXED = re.compile(r"fixed \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n")
#: What a refusal says of a finding git says shipped.
_SHIPPED = "shipped in git (plan/ships/2 is on origin/dev or origin/main)"


def _owned(tracker, finding=2, owners=(1,), **fields):
    """Step 1 and the open finding ``finding`` blocked by ``owners``."""
    tracker.add(1)
    return tracker.add(finding, "finding", blocked_by=owners, **fields)


# -- finding-file ----------------------------------------------------------------------------

def test_finding_file_writes_the_marker_on_its_owners_branch_alone(code, capsys):
    """The marker is written into the working tree only while ``card/<its owner>`` is checked
    out, ``fixed <instant>``, for the session to commit with the fix; nothing is written to the
    tracker."""
    tracker = FakeTracker()
    _owned(tracker)
    assert run(tracker, code, "finding-file", "plan#2") == 1
    assert ("is owned by plan#1, so its marker is written on card/1, and feat/work is checked "
            "out: switch to card/1") in capsys.readouterr().err
    assert not (code / "plan/ships/2").exists()
    assert run(tracker, code, "claim", "plan#1") == 0
    writes = list(tracker.writes)
    assert run(tracker, code, "finding-file", "plan#2") == 0
    assert _FIXED.fullmatch((code / "plan/ships/2").read_text())
    assert "wrote plan/ships/2: commit it with the fix and push card/1" in capsys.readouterr().out
    assert tracker.writes == writes
    _run(code, "checkout", "--quiet", "--detach")
    assert run(tracker, code, "finding-file", "plan#2") == 1
    assert "and no branch is checked out" in capsys.readouterr().err


def test_finding_file_refuses_a_card_that_is_no_finding_shipped_or_dropped(code, capsys):
    """A step's number names no finding; a finding git says shipped, or one dropped, leaves
    nothing to mark fixed."""
    tracker = FakeTracker()
    _owned(tracker)
    tracker.add(3, "finding", blocked_by=(1,), is_open=False, state_reason="NOT_PLANNED")
    assert run(tracker, code, "finding-file", "plan#1") == 1
    assert "plan#1 [step, balance] card 1 is no finding" in capsys.readouterr().err
    assert run(tracker, code, "finding-file", "plan#3") == 1
    assert "was dropped (closed as NOT_PLANNED)" in capsys.readouterr().err
    ship(code, 2)
    assert run(tracker, code, "finding-file", "plan#2") == 1
    assert _SHIPPED in capsys.readouterr().err
    assert not (code / "plan/ships").exists()


def test_finding_file_refuses_a_finding_claimed_as_work_of_its_own(code, capsys):
    """``card/F``'s own marker records a finding claimed as its own work, so no other branch
    writes one."""
    tracker = FakeTracker()
    _owned(tracker)
    claim(code, 2)
    assert run(tracker, code, "finding-file", "plan#2") == 1
    assert "is claimed as work of its own, card/2 (claimed" in capsys.readouterr().err


def test_finding_file_refuses_a_finding_with_no_one_owner_naming_the_mend(code, capsys):
    """No blocker names ``quill block``; two name ``quill hand`` to the one that owns it; three
    name ``--remove``; an outside link is removed on the web."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(3)
    tracker.add(4)
    tracker.add(5, "finding")
    tracker.add(6, "finding", blocked_by=(1, 3))
    tracker.add(7, "finding", blocked_by=(1, 3, 4))
    tracker.add(8, "finding", outside=(OutsideLink("blocker", "saltyreformed-labs/Shekel#6"),))
    expected = {
        5: "has no one owner, so no branch may write its marker (R-BAL395): it is blocked by no "
           "card: `quill block plan#5 --by plan#S` gives it one",
        6: "it is blocked by plan#1, plan#3, so it names no one owner: `quill hand plan#6 --to "
           "plan#S`, S the one of them that owns it, removes the other's link",
        7: "`quill block plan#7 --by plan#X --remove` each of them but its owner",
        8: "it is blocked by saltyreformed-labs/Shekel#6, outside the tracker, which no branch of "
           "this repository builds: remove that link on the web first (R-BAL188)",
    }
    for number, sentence in expected.items():
        assert run(tracker, code, "finding-file", f"plan#{number}") == 1
        assert sentence in capsys.readouterr().err
    assert not (code / "plan/ships").exists()


def test_finding_file_refuses_an_owner_no_branch_builds(code, capsys):
    """A split step, a ruling and a step that shipped are no step a branch builds (plan#1784),
    so none of them writes the marker; each names the hand-off."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(9, "step", True),), on_board=False)
    tracker.add(9, parent=1)
    tracker.add(3, "ruling", is_open=False, state_reason="COMPLETED")
    tracker.add(4)
    for number, owner in ((5, 1), (6, 3), (7, 4)):
        tracker.add(number, "finding", blocked_by=(owner,))
    ship(code, 4)
    for number, owner in ((5, "plan#1 [step"), (6, "plan#3 [ruling"), (7, "plan#4 [step")):
        assert run(tracker, code, "finding-file", f"plan#{number}") == 1
        err = capsys.readouterr().err
        assert f"'s owner is {owner}" in err
        assert ("a finding's owner is a step a branch builds: live work, never a step split "
                f"into smaller steps (R-BAL395, plan#1784), so no branch may write its marker: "
                f"`quill hand plan#{number} --to plan#S` hands it to one") in err


def test_a_finding_the_tool_closed_as_shipped_is_marked_again_once_a_revert_unshipped_it(
        code, capsys):
    """Shown done by the tool, but git no longer says it shipped (a revert deleted its marker):
    it is not dropped, so its owner's branch marks it fixed again with a fresh line."""
    tracker = FakeTracker()
    _owned(tracker, is_open=False, state_reason="COMPLETED", closed_by_tool=True)
    assert run(tracker, code, "claim", "plan#1") == 0
    assert run(tracker, code, "finding-file", "plan#2") == 0
    assert _FIXED.fullmatch((code / "plan/ships/2").read_text())
    capsys.readouterr()


# -- hand ------------------------------------------------------------------------------------

def test_hand_blocks_by_the_new_owner_first_then_removes_the_old_link(code, capsys):
    """Each write printed as it lands, the new link first, so a run cut short fails closed."""
    tracker = FakeTracker()
    _owned(tracker)
    tracker.add(3)
    assert run(tracker, code, "hand", "plan#2", "--to", "plan#3") == 0
    assert tracker.writes == [("block", 2, 3), ("unblock", 2, 1)]
    assert tracker.cards_by_number[2].blocked_by == (3,)
    out = capsys.readouterr().out
    assert ("plan#2 is blocked by plan#3, its new owner (R-BAL395)\n"
            "plan#2 is no longer blocked by plan#1: plan#3 owns") in out


def test_a_hand_off_cut_short_leaves_two_owners_and_the_same_command_finishes_it(code, capsys):
    """The unblock fails: the finding is blocked by both (no one owner); run again, the command
    reads it blocked by the new owner and one other, and removes only that other link."""
    tracker = FakeTracker()
    _owned(tracker)
    tracker.add(3)
    FailOnce(tracker, "unblock")
    assert run(tracker, code, "hand", "plan#2", "--to", "plan#3") == 2
    assert tracker.cards_by_number[2].blocked_by == (1, 3)
    assert run(tracker, code, "hand", "plan#2", "--to", "plan#3") == 0
    assert tracker.writes == [("block", 2, 3), ("unblock", 2, 1)]
    assert tracker.cards_by_number[2].blocked_by == (3,)
    assert ("plan#2 is blocked by plan#3 already: an earlier hand-off was cut short, so this one "
            "finishes it") in capsys.readouterr().out


def test_hand_refuses_a_card_that_is_no_finding_shipped_or_dropped(code, capsys):
    """Nothing is left to hand on; nothing is written."""
    tracker = FakeTracker()
    _owned(tracker)
    tracker.add(3)
    tracker.add(4, "finding", blocked_by=(1,), is_open=False, state_reason="NOT_PLANNED")
    assert run(tracker, code, "hand", "plan#1", "--to", "plan#3") == 1
    assert "plan#1 [step, balance] card 1 is no finding" in capsys.readouterr().err
    assert run(tracker, code, "hand", "plan#4", "--to", "plan#3") == 1
    assert "was dropped (closed as NOT_PLANNED): nothing is left" in capsys.readouterr().err
    ship(code, 2)
    assert run(tracker, code, "hand", "plan#2", "--to", "plan#3") == 1
    assert _SHIPPED in capsys.readouterr().err
    assert not tracker.writes


def test_hand_refuses_a_new_owner_no_branch_builds(code, capsys):
    """A split step, a ruling, a step that shipped and a finding may not own a finding
    (plan#1784); nothing is written."""
    tracker = FakeTracker()
    _owned(tracker)
    tracker.add(3, children=(Child(9, "step", True),), on_board=False)
    tracker.add(9, parent=3)
    tracker.add(4, "ruling", is_open=False, state_reason="COMPLETED")
    tracker.add(5)
    tracker.add(6, "finding", blocked_by=(1,))
    ship(code, 5)
    for to, named in ((3, "plan#3 [step"), (4, "plan#4 [ruling"), (5, "plan#5 [step"),
                      (6, "plan#6 [finding")):
        assert run(tracker, code, "hand", "plan#2", "--to", f"plan#{to}") == 1
        assert (f"{named}, balance] card {to} may not own plan#2 [finding, balance] card 2: a "
                "finding's owner is a step a branch builds") in capsys.readouterr().err
    assert run(tracker, code, "hand", "plan#2", "--to", "plan#9") == 0
    assert tracker.writes == [("block", 2, 9), ("unblock", 2, 1)]


def test_hand_refuses_a_finding_its_new_owner_owns_or_with_no_one_owner(code, capsys):
    """Owned by S already; blocked by no card, or by two that are not S, or by S and two
    others; linked to an issue outside the tracker: each names its mend, nothing written."""
    tracker = FakeTracker()
    for number in (1, 3, 4, 5):
        tracker.add(number)
    tracker.add(6, "finding", blocked_by=(5,))
    tracker.add(7, "finding")
    tracker.add(8, "finding", blocked_by=(1, 3))
    tracker.add(10, "finding", blocked_by=(5, 1, 3))
    tracker.add(11, "finding", blocked_by=(1,),
                outside=(OutsideLink("blocker", "saltyreformed-labs/Shekel#6"),))
    expected = {
        6: "plan#5 owns plan#6 [finding, balance] card 6 already: nothing to hand on",
        7: "is not handed on: it is blocked by no card: `quill block plan#7 --by plan#S`",
        8: "is not handed on: it is blocked by plan#1, plan#3, so it names no one owner",
        10: "`quill block plan#10 --by plan#X --remove` each of them but its owner",
        11: "is not handed on: it is blocked by saltyreformed-labs/Shekel#6, outside the "
            "tracker",
    }
    for number, sentence in expected.items():
        assert run(tracker, code, "hand", f"plan#{number}", "--to", "plan#5") == 1
        assert sentence in capsys.readouterr().err
    assert not tracker.writes


# -- the filing's re-homing refusal ------------------------------------------------------------

def test_the_filing_names_hand_for_a_finding_blocked_by_one_other_card(code, tmp_path,
                                                                       capsys):
    """A finding a filing finds blocked by one other card is re-homed with ``quill hand``, and
    the filing then finds it filed; one blocked by two, and a question converted into a ruling,
    are re-homed by hand, as before."""
    tracker = FakeTracker()
    for number in (1, 3, 4):
        tracker.add(number)
    stale = finding_filing("plan#1", "Stale", "The grid shows a stale balance.")
    for blockers, how in (((3, 4), "re-homing a card is done by hand"),
                          ((3,), "`quill hand plan#2 --to plan#1` re-homes it")):
        tracker.add(2, "finding", body="The grid shows a stale balance.", title="Stale",
                    blocked_by=blockers)
        assert run(tracker, code, *stale) == 1
        assert ("plan#4, not only by plan#1" if len(blockers) == 2 else
                "is blocked by plan#3, not only by plan#1") in (err := capsys.readouterr().err)
        assert f"the owner this filing names (R-BAL395): {how}" in err
    assert run(tracker, code, "hand", "plan#2", "--to", "plan#1") == 0
    assert run(tracker, code, *stale) == 0
    assert "is filed already" in capsys.readouterr().out
    answer = tmp_path / "a"
    answer.write_text("Yes.")
    tracker.add(5, "question", body="Ship it?", title="Ship", blocked_by=(3,))
    assert run(tracker, code, "file", "ruling", "--arc", "balance", "--title", "Ship", "--owner",
               "plan#1", "--from-question", "plan#5", "--answer-file", str(answer)) == 1
    assert ("not only by plan#1, the owner this filing names (R-BAL395): re-homing a card is "
            "done by hand") in capsys.readouterr().err
