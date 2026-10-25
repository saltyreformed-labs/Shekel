"""The card reader's two checks of its read of the tracker (:func:`tools.release._cards.read`):
a deploy-together group's links read down and its cards' lines read up give it the same
steps, and no step hangs below a group under a card that is no step, read for every group
the read holds, any card below it in any state (rulings ``balance:R-BAL287`` -
``R-BAL289``, and the coordinator's rulings under ``R-BAL207``).  Over throwaway
repositories under tmp_path and quill's own in-memory tracker.  No network, no docker,
never a real checkout."""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill.door import OWNED, FakeTracker, above
from tools.release import _cards, release
from tools.release._plan import CLEAN
from tools.release.conftest import deploy_group, judge, release_after, replan, ship, undo


# -- the two walks agree, and no step hangs unread ---------------------------------------------

def _unread(repo, base, cut, tracker, capsys):
    """``release plan`` over ``<base>..<cut>``: it exits 2, nothing judged, and says why."""
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=lambda: tracker) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    return captured.err.strip().removeprefix("release plan: nothing was judged: ")


def test_a_groups_links_read_down_and_up_give_the_same_steps(repo):
    """Under a read whose links agree, the steps below a group read down its links are
    the steps whose line up reaches it, at every depth, a finding's line up included and
    the finding no step of it."""
    base, cut = release_after(repo, (ship(52),), (ship(5111),))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511, 512))
    replan(tracker, 511, (5111, 5112))
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[511])
    cards = tracker.cards_by_number
    up = sorted(number for number, card in cards.items() if card.kind == "step"
                and 50 in [step.number for step in above(card, cards)])
    known = _cards.read(repo.root, base, cut, tracker)
    assert known.groups[50] == tuple(up) == (51, 52, 511, 512, 5111, 5112)


def test_a_finding_linked_under_the_group_card_itself_is_no_member(repo):
    """#57, a finding the group's own card owns (blocked by it, R-BAL395), ships: it
    touches the group and is no step of it, so the group #51 and #52 make is whole."""
    base, cut = release_after(repo, (ship(51), ship(52)), (ship(57),))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(57, kind="finding")
    tracker.block(57, tracker.cards_by_number[50])
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    (group,) = verdict.carried.groups
    assert (group.released, group.through) == (("plan#51", "plan#52"), ("plan#57",))


def test_a_link_seen_only_from_the_groups_side_judges_nothing(repo, capsys):
    """The tracker is read in rounds: #52 is read under #50 in one, and its own parent,
    read in the next, is gone (a person unlinked it between them).  A walk either way
    alone would judge the group with #52 or without it; nothing is judged."""
    base, cut = release_after(repo, (ship(51),), (ship(51),))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.cards_by_number[52] = replace(tracker.cards_by_number[52], parent=None)
    assert _unread(repo, base, cut, tracker, capsys) == (
        "the links under the deploy-together group plan#50 disagree, so its members cannot "
        "be read: plan#52 below it by its links, which its own read does not place under "
        "it (the tracker changed while it was read: judge again)")


def test_a_link_seen_only_from_the_cards_side_judges_nothing(repo, capsys):
    """The reverse tear: #53's parent reads #51, and #51's links, read in a later round,
    no longer list it."""
    base, cut = release_after(repo, (ship(51), ship(52)), (ship(53),))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(53, parent=51)
    assert _unread(repo, base, cut, tracker, capsys) == (
        "the links under the deploy-together group plan#50 disagree, so its members cannot "
        "be read: plan#53 under it by its own read, which its links do not reach (the "
        "tracker changed while it was read: judge again)")


class _Lagging(FakeTracker):
    """quill's fake, each card in ``lagging`` listing its sub-issues as an earlier round of
    the read returned them."""

    def __init__(self) -> None:
        """An empty tracker, no listing lagging."""
        super().__init__()
        self.lagging = {}

    def cards(self, numbers):
        """The cards that exist among ``numbers``, a lagging listing kept."""
        return {number: replace(card, children=self.lagging.get(number, card.children))
                for number, card in super().cards(numbers).items()}


def test_a_card_retyped_while_the_tracker_was_read_judges_nothing(repo, capsys):
    """#50 lists #52 as a step in one round; #52, read in the next, is a finding (a person
    changed its type between them)."""
    base, cut = release_after(repo, (ship(51),), (ship(51),))
    tracker = _Lagging()
    deploy_group(tracker)
    tracker.lagging[50] = tracker.cards([50])[50].children
    tracker.cards_by_number[52] = replace(tracker.cards_by_number[52], kind="finding")
    assert _unread(repo, base, cut, tracker, capsys) == (
        "the links under the deploy-together group plan#50 disagree, so its members cannot "
        "be read: plan#52 below it by its links, which its own read does not place under "
        "it (the tracker changed while it was read: judge again)")


def test_every_group_whose_links_disagree_is_named(repo, capsys):
    """Two groups torn in one read, #52 under #50 and #62 under #60: both are named, so
    one judging again is not followed by a second refusal for the other."""
    base, cut = release_after(
        repo, (ship(51), ship(61)),
        (ship(51), ship(61)))
    tracker = FakeTracker()
    deploy_group(tracker)
    deploy_group(tracker, container=60, leaves=(61, 62))
    for torn in (52, 62):
        tracker.cards_by_number[torn] = replace(tracker.cards_by_number[torn], parent=None)
    assert _unread(repo, base, cut, tracker, capsys) == (
        "the links under the deploy-together group plan#50 disagree, so its members cannot "
        "be read: plan#52 below it by its links, which its own read does not place under "
        "it (the tracker changed while it was read: judge again); and the links under the "
        "deploy-together group plan#60 disagree, so its members cannot be read: plan#62 "
        "below it by its links, which its own read does not place under it (the tracker "
        "changed while it was read: judge again)")


def test_a_step_linked_under_a_finding_judges_nothing_until_it_is_moved(repo, capsys):
    """A person links #56, a step, under #55, a finding #51 owns (blocked by it, R-BAL395,
    as quill files one), on the web (quill links a step only under the step it splits):
    nothing is judged, naming the link.  Moved under #51, the step it splits, the same
    release is judged."""
    base, cut = release_after(repo, (ship(51), ship(52)), (ship(56),))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding", blocked_by=(51,))
    tracker.add(56)
    tracker.add_child(55, tracker.cards_by_number[56])
    assert _unread(repo, base, cut, tracker, capsys) == (
        "a step hangs under a card that is no step, so the members of a group above it "
        "cannot be read: plan#56 under plan#55, a finding, below the deploy-together group "
        "plan#50 (move each under the step it splits, or give plan#55 the step type if a "
        "person changed it)")
    tracker.remove_child(55, tracker.cards_by_number[56])
    tracker.add_child(51, tracker.cards_by_number[56])
    assert judge(repo, base, cut, tracker).verdict.status == CLEAN


def test_a_held_step_hung_under_a_finding_no_marker_names_judges_nothing(repo, capsys):
    """The full-leaf review's H-1: #56 and #57, steps production holds, hang under #55, a
    finding #51 owns (blocked by it, R-BAL395, as quill files one: below #51 by #51's
    blocking read, R395's review, F1), and the window changes no marker of #55, #56 or #57,
    so quill's closure never holds them.  A release taking #51 and #52 out would leave #56
    and #57 in production without their group: every card below the group is read, so
    nothing is judged."""
    base, cut = release_after(
        repo, (ship(51), ship(52), ship(56), ship(57)),
        (undo(51), undo(52)))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding", blocked_by=(51,))
    for step in (56, 57):
        tracker.add(step)
        tracker.add_child(55, tracker.cards_by_number[step])
    assert _unread(repo, base, cut, tracker, capsys) == (
        "a step hangs under a card that is no step, so the members of a group above it "
        "cannot be read: plan#56, plan#57 under plan#55, a finding, below the "
        "deploy-together group plan#50 (move each under the step it splits, or give "
        "plan#55 the step type if a person changed it)")


def test_a_split_step_whose_type_a_person_changed_judges_nothing(repo, capsys):
    """The full-leaf review's H-1 and L-2: #52, split into #521 and #522, retyped a finding
    on the web.  quill's leaf rule no longer reaches #521 or #522; the remedy names #52's
    type."""
    base, cut = release_after(repo, (ship(51),), (ship(51),))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 52, (521, 522))
    tracker.cards_by_number[52] = replace(tracker.cards_by_number[52], kind="finding")
    assert _unread(repo, base, cut, tracker, capsys) == (
        "a step hangs under a card that is no step, so the members of a group above it "
        "cannot be read: plan#521, plan#522 under plan#52, a finding, below the "
        "deploy-together group plan#50 (move each under the step it splits, or give "
        "plan#52 the step type if a person changed it)")


def test_a_ruling_under_a_group_named_by_a_marker_is_no_work_not_a_torn_read(repo):
    """#58, a ruling #51 owns, named by a mistyped marker: it is refused as no work, and
    no walk counts it as a step of the group."""
    base, cut = release_after(repo, (ship(51), ship(52)), (ship(58),))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(58, kind="ruling")
    tracker.block(58, tracker.cards_by_number[51])
    (refusal,) = judge(repo, base, cut, tracker).verdict.refusals
    assert refusal.startswith("plan#58 (its marker changed at ")
    assert refusal.endswith(") is a ruling, which no commit ships, so it names no work whose "
                            "flag can be read")


# -- every card below every group the read holds (the PR #565 delta review, M-1 and L-A) --------

def _hung(steps, card, what, groups):
    """The refusal of a read in which ``steps`` hang under ``card``, which is ``what``,
    below the deploy-together ``groups``."""
    return ("a step hangs under a card that is no step, so the members of a group above it "
            f"cannot be read: {steps} under plan#{card}, {what}, below the deploy-together "
            f"group {groups} (move each under the step it splits, or give plan#{card} the "
            "step type if a person changed it)")


def _hang(tracker, card, kind, step, under=51):
    """``card``, of ``kind``, below ``under`` -- a finding or a ruling blocked by it, its
    owner, as quill files one (R-BAL395); a card of any other kind linked under it -- and
    ``step``, a step, linked under ``card``, as a person links them on the web."""
    if kind in OWNED:
        tracker.add(card, kind=kind, blocked_by=(under,))
    else:
        tracker.add(card, kind=kind)
        tracker.add_child(under, tracker.cards_by_number[card])
    tracker.add(step)
    tracker.add_child(card, tracker.cards_by_number[step])


def test_a_group_the_release_does_not_touch_is_read_below_it_all_the_same(repo, capsys):
    """M-1, kept strict by the coordinator: the release ships #71 alone, which the group
    #50 blocks, so quill's closure holds the group and the release touches nothing in it.
    #53, a step, hangs under #60, a finding the group's own card owns: nothing is
    judged, naming the link.  Moved under #51, the step it splits, the same release is
    judged, and gives no verdict on the group it does not touch."""
    base, cut = release_after(repo, (), (ship(71),))
    tracker = FakeTracker()
    deploy_group(tracker)
    _hang(tracker, 60, "finding", 53, under=50)
    tracker.add(71, blocked_by=(50,))
    assert _unread(repo, base, cut, tracker, capsys) == _hung("plan#53", 60, "a finding",
                                                              "plan#50")
    tracker.remove_child(60, tracker.cards_by_number[53])
    tracker.add_child(51, tracker.cards_by_number[53])
    verdict = judge(repo, base, cut, tracker).verdict
    assert (verdict.status, verdict.carried.groups) == (CLEAN, ())


def test_a_step_hung_below_a_group_with_no_step_of_its_own_judges_nothing(repo, capsys):
    """The group #50 holds no step, only #60, a finding, and #53, a step, hangs under #60:
    a group with no step to judge as a member is read below all the same."""
    base, cut = release_after(repo, (), (ship(53),))
    tracker = FakeTracker()
    deploy_group(tracker, leaves=())
    _hang(tracker, 60, "finding", 53, under=50)
    assert _unread(repo, base, cut, tracker, capsys) == _hung("plan#53", 60, "a finding",
                                                              "plan#50")


def test_a_torn_read_below_a_group_the_release_does_not_touch_judges_nothing(repo, capsys):
    """M-1's other check: the release ships #71 alone, which the group #50 blocks, and
    #52's own parent, read in a later round than #50's links, is gone.  The walks are
    compared for the group the release does not touch too: nothing is judged."""
    base, cut = release_after(repo, (), (ship(71),))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.cards_by_number[52] = replace(tracker.cards_by_number[52], parent=None)
    tracker.add(71, blocked_by=(50,))
    assert _unread(repo, base, cut, tracker, capsys) == (
        "the links under the deploy-together group plan#50 disagree, so its members cannot "
        "be read: plan#52 below it by its links, which its own read does not place under "
        "it (the tracker changed while it was read: judge again)")


def test_a_torn_read_below_a_group_with_no_step_of_its_own_judges_nothing(repo, capsys):
    """#53's own parent reads the group #50, and #50's links, read in another round, list
    no step: the walks of a group with no step down are compared too, so #53 is not
    dropped unseen."""
    base, cut = release_after(repo, (), (ship(53),))
    tracker = FakeTracker()
    deploy_group(tracker, leaves=())
    tracker.add(53, parent=50)
    assert _unread(repo, base, cut, tracker, capsys) == (
        "the links under the deploy-together group plan#50 disagree, so its members cannot "
        "be read: plan#53 under it by its own read, which its links do not reach (the "
        "tracker changed while it was read: judge again)")


def test_a_step_hung_under_a_closed_finding_judges_nothing(repo, capsys):
    """#55, a finding #51 owns, closed as fixed, still holds #56, a step production holds:
    a closed card the group lists is read as an open one is, so a release taking #51 and
    #52 out judges nothing."""
    base, cut = release_after(
        repo, (ship(51), ship(52), ship(56)),
        (undo(51), undo(52)))
    tracker = FakeTracker()
    deploy_group(tracker)
    _hang(tracker, 55, "finding", 56)
    tracker.close(55, "completed")
    assert _unread(repo, base, cut, tracker, capsys) == _hung("plan#56", 55, "a finding",
                                                              "plan#50")


def test_a_closed_step_hung_under_a_finding_judges_nothing(repo, capsys):
    """#56, a step that shipped and was closed, hangs under #55, a finding #51 owns: a
    closed step a card below the group lists is read as an open one is, so a release
    taking #51 and #52 out judges nothing."""
    base, cut = release_after(
        repo, (ship(51), ship(52), ship(56)),
        (undo(51), undo(52)))
    tracker = FakeTracker()
    deploy_group(tracker)
    _hang(tracker, 55, "finding", 56)
    tracker.close(56, "completed")
    assert _unread(repo, base, cut, tracker, capsys) == _hung("plan#56", 55, "a finding",
                                                              "plan#50")


@pytest.mark.parametrize(("kind", "what"), [("ruling", "a ruling"), ("question", "a question"),
                                            (None, "a card of no type")])
def test_a_step_hung_under_a_card_of_any_kind_that_is_no_step_judges_nothing(
        repo, capsys, kind, what):
    """#56, a step, hangs under #55, a card of a kind no step is, below #51 (a ruling #51
    owns, or a card of another kind linked under it): nothing is judged, naming what #55
    is."""
    base, cut = release_after(repo, (ship(51), ship(52)), (ship(56),))
    tracker = FakeTracker()
    deploy_group(tracker)
    _hang(tracker, 55, kind, 56)
    assert _unread(repo, base, cut, tracker, capsys) == _hung("plan#56", 55, what, "plan#50")


def test_a_step_hung_below_nested_groups_names_each_group(repo, capsys):
    """The group #50 is a step of the group #40, and #56, a step, hangs under #55, a finding
    #51 owns: the refusal names both groups the hang is below."""
    base, cut = release_after(repo, (ship(51), ship(52)), (ship(56),))
    tracker = FakeTracker()
    deploy_group(tracker, container=40, leaves=())
    deploy_group(tracker)
    tracker.add_child(40, tracker.cards_by_number[50])
    _hang(tracker, 55, "finding", 56)
    assert _unread(repo, base, cut, tracker, capsys) == _hung("plan#56", 55, "a finding",
                                                              "plan#40, plan#50")


def test_a_card_below_two_groups_is_read_for_each(repo, capsys):
    """#50, a deploy-together group whose card a person retyped a finding, holds #51 and
    #52, and hangs under #45, a finding under the group #40.  For #50's own group the two
    steps are its own; for #40 they hang under a card that is no step.  #50 is read for
    each group, though the read reached it for #50 two rounds earlier, so the refusal
    names the hang and its mend, not a torn read."""
    base, cut = release_after(repo, (), (ship(51), ship(52)))
    tracker = FakeTracker()
    deploy_group(tracker, container=40, leaves=())
    deploy_group(tracker)
    tracker.cards_by_number[50] = replace(tracker.cards_by_number[50], kind="finding")
    tracker.add(45, kind="finding")
    tracker.add_child(40, tracker.cards_by_number[45])
    tracker.add_child(45, tracker.cards_by_number[50])
    assert _unread(repo, base, cut, tracker, capsys) == _hung("plan#51, plan#52", 50,
                                                              "a finding", "plan#40")


def test_a_card_listed_below_a_group_and_gone_when_read_judges_nothing(repo, capsys):
    """#51 lists #55, a finding, as its sub-issue, and #55 is gone when the read asks for it
    (deleted between the tracker's rounds): what hangs below it cannot be read, so nothing
    is judged."""
    base, cut = release_after(repo, (ship(51),), (ship(52),))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding")
    tracker.add_child(51, tracker.cards_by_number[55])
    del tracker.cards_by_number[55]
    tracker.gone.add(55)
    assert _unread(repo, base, cut, tracker, capsys) == (
        "plan#55 is listed below the deploy-together group plan#50 and the tracker does not "
        "hold it (the tracker changed while it was read: judge again)")
