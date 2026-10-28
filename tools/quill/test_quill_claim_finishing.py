"""``quill claim`` files its card's two finishing tasks (ruling plan#1875): last in the card's
order, each body naming the card, decided before the claim's push and written after it; a
re-claim finds them filed and files none; a claim a failure cut short is finished by the same
command run again where its branch is checked out (R-BAL186).  Over the in-memory tracker
(:class:`_fake.FakeTracker`) and the ``code`` fixture's real git.
"""
from __future__ import annotations

import pytest

from tools.ci.scratch import run as _run
from tools.quill import _git
from tools.quill._fake import AnswerLostOnce, FailOnce, FakeTracker, claim, leaf_filing, run
from tools.quill._state import SHIPPABLE
from tools.quill._tasks import finishing
from tools.quill.check import Draft, Owner, violations
from tools.quill.setup_tracker import FILING, TASK

(REVIEW, REVIEW_TEXT), (LAND, LAND_TEXT) = finishing(1)

#: What a claim of step 1, on the board, writes when it files both finishing tasks as plan#2
#: and plan#3: each created marked, linked under it, put on the board under it, unmarked.
FILES_BOTH = [("create", 2, "task", REVIEW, ("filing",)), ("add_child", 1, 2),
              ("board_add", 2), ("unmark", 2),
              ("create", 3, "task", LAND, ("filing",)), ("add_child", 1, 3),
              ("board_add", 3), ("unmark", 3)]


def _order(tracker, card=1):
    """plan#``card``'s sub-issues in its order."""
    return [child.number for child in tracker.cards([card])[card].children]


def _released(tracker, code):
    """plan#1's claim released, and this clone's branch ``card/1`` deleted, so the clone can
    claim it again."""
    assert run(tracker, code, "release", "plan#1") == 0
    _run(code, "switch", "--quiet", "feat/work")
    _run(code, "branch", "--quiet", "-D", "card/1")


def test_a_claim_files_the_two_finishing_tasks_last_each_naming_its_card(code, capsys):
    """Below the card's tasks already filed, the review then the suite and pull request, each
    body the constant's with the card's number; the claim's lines first, then each write."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "claim", "plan#1") == 0
    assert tracker.writes == FILES_BOTH
    assert _order(tracker) == [2, 3]
    assert tracker.bodies[2] == REVIEW_TEXT and tracker.bodies[3] == LAND_TEXT
    assert "`quill spec-history plan#1 --since card/1`" in REVIEW_TEXT
    assert LAND_TEXT.startswith("Begun once the full suite is green (ruling plan#1981): ")
    assert "and plan#1's pull request into dev, queued; " in LAND_TEXT
    out = capsys.readouterr().out
    assert ("\n  switched to card/1\n  its finishing tasks, last in its order (ruling "
            "plan#1875); cut short, `quill claim plan#1` run again on card/1 files the rest:\n"
            "filed plan#2\n") in out
    assert out.count("  in plan#1's order: last, no finishing task after it still to "
                     "begin\n") == 2
    other = FakeTracker()
    other.add(5)
    other.add_task(6, 5, title="piece a")
    assert run(other, code, "claim", "plan#5") == 0
    assert _order(other, 5) == [6, 7, 8]


def test_two_cards_claimed_each_file_their_own(code):
    """Each body names its card, so a card's finishing task is never taken for another's, a
    filing's identity being its kind, title and text (``_filing._same_filing``)."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(5)
    assert run(tracker, code, "claim", "plan#1") == 0
    assert run(tracker, code, "claim", "plan#5") == 0
    assert _order(tracker) == [6, 7] and _order(tracker, 5) == [8, 9]
    assert tracker.bodies[8] == finishing(5)[0][1] != tracker.bodies[6] == REVIEW_TEXT


def test_a_review_filed_under_a_card_whose_suite_stands_goes_above_it(code):
    """A claim finding only the suite and pull request filed files the review above it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(2, 1, title=LAND)
    assert run(tracker, code, "claim", "plan#1") == 0
    assert tracker.writes == [("create", 3, "task", REVIEW, ("filing",)), ("add_child", 1, 3),
                              ("place_child", 1, 3, 2), ("board_add", 3), ("unmark", 3)]
    assert _order(tracker) == [3, 2]


def test_a_reclaim_finds_them_filed_whatever_their_state(code, capsys):
    """Released and claimed again, plan#1 files none: its review done (closed by the tool)."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "claim", "plan#1") == 0
    tracker.close(2, "completed")
    writes = list(tracker.writes)
    _released(tracker, code)
    capsys.readouterr()
    assert run(tracker, code, "claim", "plan#1") == 0
    assert tracker.writes == writes
    assert "\n  its finishing tasks are filed already (ruling plan#1875)\n" in (
        capsys.readouterr().out)


def test_a_claim_finds_them_filed_by_hand_whatever_their_text_and_state(code):
    """Filed by hand before the claim, as plan#1885 and plan#1886 were, titled as the constant
    but the review dropped and the suite and pull request with another text: the claim files
    none.  A test of its own, on a repository of its own, so plan#1 is released at most once
    per test: ``_git.release`` names the branch it keeps to the second, so a second release
    in the first one's second, of another tip, is refused (plan#1982)."""
    by_hand = FakeTracker()
    by_hand.add(1)
    by_hand.add_task(2, 1, title=REVIEW, is_open=False, state_reason="NOT_PLANNED")
    by_hand.add_task(3, 1, title=LAND, body="Run the suite, then open the pull request.")
    assert run(by_hand, code, "claim", "plan#1") == 0
    assert not by_hand.writes


def test_a_claim_cut_short_is_finished_by_the_same_command_where_its_branch_is(code, capsys):
    """The push lands and the review's create lands, its answer lost: exit 2.  Run again on
    ``card/1``, the claim finds the review's card by its title and text, finishes its filing,
    and files the suite and pull request: no card twice, and no second claim.  Run once more,
    it is refused, writing nothing."""
    tracker = FakeTracker()
    tracker.add(1)
    AnswerLostOnce(tracker, "create")
    assert run(tracker, code, "claim", "plan#1") == 2
    sha = _git.on_origin(code)[1]
    assert tracker.writes == [("create", 2, "task", REVIEW, ("filing",))]
    capsys.readouterr()
    assert run(tracker, code, "claim", "plan#1") == 0
    assert tracker.writes == FILES_BOTH
    assert _git.on_origin(code) == {1: sha}
    out = capsys.readouterr().out
    assert out.startswith("plan#1 is claimed already, card/1 (claimed ")
    assert ("and checked out here; its finishing tasks are not all filed, so this finishes its "
            "claim\n") in out
    assert "\nfinishing plan#2 [task, no arc] Adversarial review: its filing has not" in out
    assert run(tracker, code, "claim", "plan#1") == 1
    assert "plan#1 is already claimed: card/1 (claimed " in capsys.readouterr().err
    assert tracker.writes == FILES_BOTH


def test_a_claim_cut_short_under_an_earlier_text_files_the_task_anew(code):
    """The suite and pull request created and linked under an earlier text of the constant,
    its filing cut short, still marked: the claim does not find it by its text, so it files
    the task anew, last, and the marked one, never offered, is withdrawn with ``quill drop``
    (``_tasks.FINISHING``)."""
    tracker = FakeTracker()
    tracker.add(1)
    earlier = tracker.create("task", LAND, "An earlier text.", ())
    tracker.add_child(1, tracker.cards_by_number[earlier])
    assert run(tracker, code, "claim", "plan#1") == 0
    assert _order(tracker) == [3, earlier, 4]
    assert (tracker.bodies[4], tracker.cards_by_number[4].labels) == (LAND_TEXT, ())
    assert FILING in tracker.cards_by_number[earlier].labels
    assert run(tracker, code, "drop", f"plan#{earlier}", "--why", "an earlier text's") == 0
    assert tracker.cards_by_number[earlier].state_reason == "NOT_PLANNED"


@pytest.mark.parametrize("failing", ["add_child", "unmark"])
def test_a_claim_whose_link_or_unmark_failed_is_finished_and_one_made_before_the_ruling_too(
        code, failing):
    """The review filed but not linked, or linked and still marked: run again, its filing is
    finished, nothing is created twice.  A claim made before ruling plan#1875 (its git alone,
    :func:`_fake.claim`), checked out here, gets its finishing tasks from the same command."""
    tracker = FakeTracker()
    tracker.add(1)
    FailOnce(tracker, failing)
    assert run(tracker, code, "claim", "plan#1") == 2
    assert run(tracker, code, "claim", "plan#1") == 0
    assert tracker.writes == FILES_BOTH and _order(tracker) == [2, 3]
    earlier = FakeTracker()
    earlier.add(1)
    _released(tracker, code)
    claim(code, 1)
    _run(code, "fetch", "--quiet", "origin", "refs/heads/card/1:refs/heads/card/1")
    _run(code, "switch", "--quiet", "card/1")
    assert run(earlier, code, "claim", "plan#1") == 0
    assert earlier.writes == FILES_BOTH


def test_a_claim_held_elsewhere_is_refused_and_files_nothing(code, capsys):
    """Another clone's claim, not checked out here: refused as held, nothing written, though
    its finishing tasks are not filed."""
    tracker = FakeTracker()
    tracker.add(1)
    claim(code, 1)
    assert run(tracker, code, "claim", "plan#1") == 1
    assert "refused: plan#1 is already claimed: card/1 (claimed " in capsys.readouterr().err
    assert not tracker.writes


def test_a_refused_finishing_filing_refuses_the_claim_before_its_push(code, capsys):
    """Decided before the push: the review's title and text on a card at the top level (a
    person unlinked it) is refused as ``quill file task`` refuses it, and nothing is pushed
    or written."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, "task", body=REVIEW_TEXT, labels=(), title=REVIEW)
    assert run(tracker, code, "claim", "plan#1") == 1
    assert ("refused: plan#2 [task, no arc] Adversarial review has this kind, title and text, "
            "and its filing finished at the top level, not under plan#1: re-homing a card is "
            "done by hand") in capsys.readouterr().err
    assert not tracker.writes and not _git.on_origin(code)


@pytest.mark.parametrize("kind", SHIPPABLE)
def test_each_finishing_task_passes_the_check_under_live_work(kind):
    """Each finishing task's title and text, a task under a live step or finding, the one
    owner ``quill claim`` files them under (it refuses any other card first): the check a
    filing makes passes it, so the claim never grades them again
    (``_filing.finishing_filings``)."""
    for title, body in finishing(1):
        assert not violations(Draft(TASK, title, body, (), Owner(kind, True, False)))


def test_a_released_claims_finishing_tasks_are_dropped_before_its_step_splits(code, tmp_path,
                                                                             capsys):
    """A step split into smaller steps is never shipped itself (R-BAL177), so a leaf is refused
    while the finishing tasks a claim filed are open; dropped, the split goes ahead."""
    tracker = FakeTracker()
    tracker.add(1)
    assert run(tracker, code, "claim", "plan#1") == 0
    _released(tracker, code)
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 1
    assert ("refused: plan#1 has tasks still open (plan#2, plan#3): a step split into smaller "
            "steps is never shipped itself") in capsys.readouterr().err
    for task in (2, 3):
        assert run(tracker, code, "drop", f"plan#{task}", "--why", "split instead") == 0
    assert run(tracker, code, *leaf_filing(tmp_path, "Half")) == 0
    assert tracker.cards_by_number[1].step_children == (4,)
