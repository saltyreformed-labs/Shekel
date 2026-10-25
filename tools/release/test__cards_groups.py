"""A release after the cutover whose window touches a deploy-together group, judged by
the tracker's cards (rulings ``balance:R-BAL287`` - ``R-BAL289``, and the coordinator's
rulings under ``R-BAL207``): over throwaway repositories under tmp_path and quill's own
in-memory tracker.  No network, no docker, never a real checkout."""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill.door import FakeTracker, OutsideLink
from tools.quill.setup_tracker import DEPLOY_TOGETHER_LABEL, FILING
from tools.release import release
from tools.release._plan import CLEAN, REFUSED
from tools.release.conftest import (
    MONEY,
    after_cutover,
    deploy_group,
    judge,
    pull_request,
    released_group,
    replan,
    replanned_and_dropped,
    ship,
    undo,
)


# -- the deploy-together group (R-BAL288: wholly in production or wholly out) ----------------

def test_a_group_shipped_in_part_is_refused_naming_the_missing_leaf(repo):
    """#51 without #52: production would hold part of the group."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(51))
    tracker = FakeTracker()
    deploy_group(tracker)
    verdict = judge(repo, base, cut, tracker).verdict
    (group,) = verdict.carried.groups
    assert (group.key, group.carried, group.released, group.missing, group.undone) == (
        "plan#50", ("plan#51",), (), ("plan#52",), ())
    assert verdict.refusals == (
        "it carries part of the deploy-together group plan#50 (plan#51) without plan#52; "
        "the group ships whole",)
    assert verdict.carried.steps[0].groups == ("plan#50",)


def test_a_group_shipped_whole_with_a_released_leaf_is_clean(repo):
    """#51 already in production, #52 shipped now: the group is whole."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51))
    cut = pull_request(repo, base, ship(52))
    tracker = FakeTracker()
    deploy_group(tracker)
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.carried.groups[0].released == ("plan#51",)
    assert verdict.status == CLEAN


def test_undoing_one_leaf_of_a_released_group_is_refused(repo):
    """Both leaves in production; reverting #51 alone leaves #52 without it."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    cut = pull_request(repo, base, undo(51))
    tracker = FakeTracker()
    deploy_group(tracker)
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        "it leaves plan#51 of the deploy-together group plan#50 out of production (its "
        "marker deleted) and keeps plan#52; the group ships whole",)


def test_undoing_a_whole_group_is_clean(repo):
    """Reverting every leaf takes the group out whole."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    cut = pull_request(repo, base, undo(51), undo(52))
    tracker = FakeTracker()
    deploy_group(tracker)
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.carried.groups[0].undone == ("plan#51", "plan#52")
    assert verdict.status == CLEAN


def test_undoing_one_leaf_of_a_released_group_and_dropping_it_is_refused(repo):
    """The review's HIGH-1: #51 reverted, then dropped; production must not keep #52
    without it, so a dropped leaf the window names still counts."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    cut = pull_request(repo, base, undo(51))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.close(51, "not_planned")
    assert judge(repo, base, cut, tracker).verdict.status == REFUSED


def test_a_shipped_leaf_a_person_closed_is_no_dropped_leaf(repo):
    """Real cards are closed by hand too: #52 shipped and closed by a person is a leaf
    production holds, judged by git's answer at the cut, never read as dropped."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    cut = pull_request(repo, base, undo(51))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.cards_by_number[52] = replace(tracker.cards_by_number[52], is_open=False,
                                          state_reason="COMPLETED", touched_by_hand=True)
    assert judge(repo, base, cut, tracker).verdict.status == REFUSED


def test_an_offered_cut_is_judged_by_what_production_holds_at_it(repo):
    """Refused for two money movers, the cut offers the newest earlier cut that passes:
    not the one shipping #51 without #52 (whole only at the cut), but the one before it."""
    base, cutover = after_cutover(repo)
    first = pull_request(repo, cutover, ship(40))
    half = pull_request(repo, first, ship(51))
    cut = pull_request(repo, half, ship(52), ship(41))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    deploy_group(tracker)
    judged = judge(repo, base, cut, tracker)
    assert judged.verdict.status == REFUSED
    assert judged.passing.cut == first


def test_an_offered_cut_is_held_to_a_leaf_a_person_closed_after_it_shipped(repo):
    """#52 ships at the cut and a person closes it as completed: git says it shipped, so it
    is no dropped leaf (R-BAL181), and the offered cut shipping #51 without it is refused
    as any cut shipping part of the group is; the cut before it is offered."""
    base, cutover = after_cutover(repo)
    first = pull_request(repo, cutover, ship(40))
    half = pull_request(repo, first, ship(51))
    cut = pull_request(repo, half, ship(52), ship(41))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    deploy_group(tracker)
    tracker.cards_by_number[52] = replace(tracker.cards_by_number[52], is_open=False,
                                          state_reason="COMPLETED", touched_by_hand=True)
    judged = judge(repo, base, cut, tracker)
    assert judged.verdict.status == REFUSED
    assert judged.passing.cut == first


def test_a_still_marked_leaf_shipped_at_the_cut_is_a_leaf(repo):
    """#53, closed by a person while its filing mark stayed, ships alone: production
    holding it at the cut makes it a member, so holding it without #51 is refused (that it
    is a leaf because git says it shipped AT THE CUT is pinned by
    :func:`test_a_still_marked_leaf_is_a_leaf_once_it_ships_at_the_cut`)."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(53))
    tracker = FakeTracker()
    deploy_group(tracker, leaves=(51, 53))
    tracker.cards_by_number[53] = replace(tracker.cards_by_number[53],
                                          labels=("balance", FILING), is_open=False,
                                          state_reason="COMPLETED", touched_by_hand=True)
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        "it carries part of the deploy-together group plan#50 (plan#53) without plan#51; "
        "the group ships whole",)


def test_a_leaf_of_nested_groups_is_held_to_each(repo):
    """Group #40 holds #41 and group #45 (#46, #47): shipping #46 and #47 leaves #45 whole
    and #40 without #41, so every group above a leaf judges it."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(46), ship(47))
    tracker = FakeTracker()
    deploy_group(tracker, container=40, leaves=(41,))
    tracker.add(45, labels=("balance", DEPLOY_TOGETHER_LABEL))
    tracker.add_child(40, tracker.cards_by_number[45])
    replan(tracker, 45, (46, 47))
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.carried.steps[0].groups == ("plan#45", "plan#40")
    assert [(group.key, group.missing) for group in verdict.carried.groups] == [
        ("plan#40", ("plan#41",)), ("plan#45", ())]
    assert verdict.refusals == (
        "it carries part of the deploy-together group plan#40 (plan#46, plan#47) without "
        "plan#41; the group ships whole",)


def test_an_undo_of_a_marker_production_never_held_names_no_card(repo):
    """What three trailer-era tests (the review's E4 and two of the delta review's cases)
    held for a STRAY ``Reopens:`` naming a card production never held -- a leaf never
    shipped, a leaf dropped long ago, a finding dropped under the group -- is structural
    under the marker (R-BAL359; the ruled report's s.10 moots R-BAL309's stray): a marker
    is deleted only where it is, so a card production never held is undone only after a
    ship in the same window, the net change names it on neither side, and the release is
    held to no group for it.  (The half this replaces deleted markers never written, which
    changes nothing and could not fail: M3's review, its 6.)  #51 and #52 released; #53 a
    leaf and #56 a finding #51 owns, both dropped, each shipped and undone in the window."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    cut = pull_request(repo, pull_request(repo, base, ship(53), ship(56)), undo(53), undo(56))
    fix = pull_request(repo, base, ship(51))
    tracker = FakeTracker()
    deploy_group(tracker, leaves=(51, 52, 53))
    tracker.add(56, kind="finding")
    tracker.block(56, tracker.cards_by_number[51])
    tracker.close(53, "not_planned")
    tracker.close(56, "not_planned")
    verdict = judge(repo, base, cut, tracker).verdict
    assert (verdict.carried.steps, verdict.carried.groups, verdict.status) == ((), (), CLEAN)
    verdict = judge(repo, base, fix, tracker).verdict
    assert {step.key: step.groups for step in verdict.carried.steps} == {
        "plan#51": ("plan#50",)}
    assert verdict.status == CLEAN


def test_an_offered_cut_still_shipping_a_leaf_dropped_since_is_held_to_its_group(repo):
    """#51 shipped at m1, undone at m2, dropped since.  The cut and m2 are refused (money);
    m1 would leave production holding #51 without #52, so it is refused too, and the cut
    offered is the cutover's own, which carries nothing: a dropped leaf counts at each cut
    whose production holds it."""
    base, cutover = after_cutover(repo)
    first = pull_request(repo, cutover, ship(51))
    second = pull_request(repo, first, undo(51), ship(40), ship(42))
    cut = pull_request(repo, second, ship(41))
    tracker = FakeTracker()
    for number in (40, 41, 42):
        tracker.add(number, labels=MONEY)
    deploy_group(tracker)
    tracker.close(51, "not_planned")
    judged = judge(repo, base, cut, tracker)
    assert judged.verdict.status == REFUSED
    assert judged.passing.cut == cutover


def test_a_dropped_leaf_is_no_part_of_the_group(repo):
    """A leaf closed as not planned never ships, so the group is whole without it."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(51), ship(52))
    tracker = FakeTracker()
    deploy_group(tracker, leaves=(51, 52, 53))
    tracker.close(53, "not_planned")
    assert judge(repo, base, cut, tracker).verdict.carried.groups[0].missing == ()


def test_a_leaf_split_again_counts_as_its_own_leaves(repo):
    """#52 split into #521 and #522: a release without #522 names #522 alone."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(51), ship(521))
    tracker = FakeTracker()
    deploy_group(tracker)
    for leaf in (521, 522):
        tracker.add(leaf)
        tracker.add_child(52, tracker.cards_by_number[leaf])
    assert judge(repo, base, cut, tracker).verdict.carried.groups[0].missing == ("plan#522",)


def test_a_group_leaf_reverted_and_split_since_is_held_to_its_group(repo):
    """The second delta review's HIGH (the third's P1): #51 undone, then re-planned as #511
    and #512, neither shipped.  #51 is under group #50, so the undo touches it, and the
    group's leaves as they are now are #52, #511 and #512: production keeping #52 without
    the other two is refused."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    cut = pull_request(repo, base, undo(51))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511, 512))
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.carried.steps[0].groups == ("plan#50",)
    assert verdict.status == REFUSED
    assert verdict.refusals == (
        "it leaves production holding plan#52 of the deploy-together group plan#50 without "
        "plan#511, plan#512 (it names plan#51, under the group but no leaf of it); the group "
        "ships whole",)


def test_a_group_leaf_reverted_and_split_since_is_whole_once_its_new_leaves_ship(repo):
    """The third delta review's P5, k1's own workflow: #51 undone and re-planned as #511
    and #512, both shipped in the same window.  Production holds every leaf the group has
    now, so the release is clean; a stand-in judging #51 as a member refused every cut."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    reverted = pull_request(repo, base, undo(51))
    cut = pull_request(repo, reverted, ship(511), ship(512))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511, 512))
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    (group,) = verdict.carried.groups
    assert (group.carried, group.released, group.missing, group.undone, group.through) == (
        ("plan#511", "plan#512"), ("plan#52",), (), (), ("plan#51",))


def test_a_group_card_reverted_and_split_since_is_judged_by_its_new_leaves(repo):
    """The third delta review's P6: #70, the deploy-together card itself, released,
    reverted, then split into #701 and #702, both shipped.  #70 touches its own group,
    which is whole: it is no member of itself."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(70))
    reverted = pull_request(repo, base, undo(70))
    cut = pull_request(repo, reverted, ship(701), ship(702))
    tracker = FakeTracker()
    tracker.add(70, labels=("balance", DEPLOY_TOGETHER_LABEL))
    replan(tracker, 70, (701, 702))
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    (group,) = verdict.carried.groups
    assert (group.key, group.carried, group.through) == (
        "plan#70", ("plan#701", "plan#702"), ("plan#70",))


def test_a_card_split_since_into_a_group_below_it_touches_no_group_itself(repo):
    """The third delta review's P7: #80 released, reverted, then split into #81, labelled
    deploy-together, whose leaves #811 and #812 shipped.  #81 is BELOW #80, so #80 is under
    no group; the group #81 is whole."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(80))
    reverted = pull_request(repo, base, undo(80))
    cut = pull_request(repo, reverted, ship(811), ship(812))
    tracker = FakeTracker()
    tracker.add(80)
    tracker.add(81, labels=("balance", DEPLOY_TOGETHER_LABEL))
    tracker.add_child(80, tracker.cards_by_number[81])
    replan(tracker, 81, (811, 812))
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    assert {step.key: step.groups for step in verdict.carried.steps} == {
        "plan#80": (), "plan#811": ("plan#81",), "plan#812": ("plan#81",)}


def test_leaves_under_a_split_step_a_person_closed_are_no_members(repo):
    """A person closed #52, split into #521 and #522, on the web: a drop its leaves inherit
    and never carry (R-BAL185), so quill never offers either.  A follow-up fix to #51 is
    judged without them; counting them refused every release touching the group until a
    person closed both by hand."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51))
    cut = pull_request(repo, base, ship(51))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 52, (521, 522))
    tracker.cards_by_number[52] = replace(tracker.cards_by_number[52], is_open=False,
                                          state_reason="NOT_PLANNED", touched_by_hand=True)
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    assert verdict.carried.groups[0].carried == ("plan#51",)
    assert verdict.carried.groups[0].missing == ()


def test_a_finding_under_a_group_touches_it_and_is_held_to_its_leaves(repo):
    """Group #50 released whole as #51; #52 filed under it since.  #55, a finding filed
    under #51, ships: it touches the group (never a member: a fix the group cannot ship
    without is a leaf), and production would keep #51 without #52."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51))
    cut = pull_request(repo, base, ship(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[51])
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.carried.steps[0].groups == ("plan#50",)
    assert verdict.refusals == (
        "it leaves production holding plan#51 of the deploy-together group plan#50 without "
        "plan#52 (it names plan#55 owned by plan#51, under the group but no leaf of it); the "
        "group ships whole",)


def test_a_deploy_together_step_with_no_leaf_is_no_group(repo):
    """A step labelled deploy-together before it is split has no leaf to hold together:
    shipping it touches no group."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(70))
    tracker = FakeTracker()
    tracker.add(70, labels=("balance", DEPLOY_TOGETHER_LABEL))
    verdict = judge(repo, base, cut, tracker).verdict
    assert (verdict.carried.steps[0].groups, verdict.carried.groups) == ((), ())


def test_a_finding_linked_under_a_group_leaf_but_owned_by_none_touches_no_group(repo):
    """R-BAL395: a finding's owner is the one step it is blocked by, never a parent.  #55
    linked under #51 by hand and blocked by nothing has no owner (quill's sync reports it),
    so it is under no group, and the release shipping it touches none."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding")
    tracker.add_child(51, tracker.cards_by_number[55])
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    assert (verdict.carried.steps[0].groups, verdict.carried.groups) == ((), ())


@pytest.mark.parametrize("second", ["a-step-it-waits-on", "an-outside-issue"])
def test_a_finding_blocked_by_more_than_its_owner_refuses_the_release(repo, second):
    """R-BAL395: a finding's owner is the ONE card it is blocked by.  #55, owned by #51, a
    leaf of group #50 production holds without #52, is blocked by a second card too -- #70,
    a step it waits on (``quill block``, or the web's "Mark as blocked by"), or an issue
    outside the tracker -- so it names no one owner and which groups it is under cannot be
    read: the release shipping it is refused, never judged clean beside the group (R395's
    review, F3).  Blocked by #51 alone, the same release is held to the group."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51))
    cut = pull_request(repo, base, ship(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(70)
    waits = second == "a-step-it-waits-on"
    tracker.add(55, kind="finding", blocked_by=(51, 70) if waits else (51,),
                outside=() if waits else (OutsideLink("blocker", "o/code#9"),))
    (refusal,) = judge(repo, base, cut, tracker).verdict.refusals
    assert refusal.startswith("plan#55 (its marker changed at ")
    assert refusal.endswith(
        f") is a finding blocked by plan#51, {'plan#70' if waits else 'o/code#9'}, so it "
        "names no one owner (the one step it is blocked by, R-BAL395) and which groups it is "
        "under cannot be read: leave it blocked by its owner alone")
    tracker.cards_by_number[55] = replace(tracker.cards_by_number[55], blocked_by=(51,),
                                          outside=())
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        "it leaves production holding plan#51 of the deploy-together group plan#50 without "
        "plan#52 (it names plan#55 owned by plan#51, under the group but no leaf of it); the "
        "group ships whole",)


def test_a_finding_owned_by_a_step_below_a_leaf_names_its_owner_then_each_step(repo):
    """#55, a finding owned by #511, a leaf #51 was split into, ships before any leaf of the
    group #50: the report names its way up, its owner first and then each step it is under
    (R-BAL395: a finding is blocked by its owner, and under no card; R395's review, F4)."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511, 512))
    tracker.add(55, kind="finding", blocked_by=(511,))
    (group,) = judge(repo, base, cut, tracker).verdict.carried.groups
    assert group.through == ("plan#55 owned by plan#511 under plan#51",)


def test_a_finding_shipped_into_a_group_production_holds_none_of_is_clean(repo):
    """#55, a finding #51 owns, ships before any leaf of group #50: production holds the
    group wholly out, and a finding is no member of it."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[51])
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    (group,) = verdict.carried.groups
    assert (group.carried, group.missing, group.through) == (
        (), ("plan#51", "plan#52"), ("plan#55 owned by plan#51",))


# -- a step that will never ship, while production holds it ----------------------------------

def test_a_group_leaf_undone_replanned_and_dropped_is_still_a_member(repo):
    """The full-leaf review's E1q: #51 undone, re-planned, then dropped by quill.  #51 is
    no leaf now, but production held it at the base, so it is a member: the release
    keeping #52 without it is refused, naming the way out."""
    tracker, base = released_group(repo)
    cut = pull_request(repo, base, undo(51))
    replanned_and_dropped(tracker)
    verdict = judge(repo, base, cut, tracker).verdict
    (group,) = verdict.carried.groups
    assert (group.released, group.undone, group.missing, group.through, group.dead) == (
        ("plan#52",), ("plan#51",), (), (), ("plan#51",))
    assert verdict.refusals == (
        "it leaves plan#51 of the deploy-together group plan#50 out of production (its "
        "marker deleted) and keeps plan#52; a step the plan will never ship (plan#51) is "
        "a member while production holds it, at the base or the cut: a release that leaves "
        "production holding none of the group is whole (reverting the merge that landed each of "
        "plan#52); the group ships whole",)


def test_a_group_with_a_step_that_will_never_ship_is_whole_once_taken_out(repo):
    """E1q's way out: the same release takes #52 out too, and is whole.  After it,
    production holds #51 on neither side, so it is no member of a later release: #52
    ships back alone, clean."""
    tracker, base = released_group(repo)
    out = pull_request(repo, base, undo(51), undo(52))
    replanned_and_dropped(tracker)
    assert judge(repo, base, out, tracker).verdict.status == CLEAN
    back = pull_request(repo, out, ship(52))
    verdict = judge(repo, out, back, tracker).verdict
    assert verdict.status == CLEAN
    (group,) = verdict.carried.groups
    assert (group.carried, group.dead) == (("plan#52",), ())


def test_a_still_marked_leaf_shipped_and_undone_is_still_a_member(repo):
    """The full-leaf review's E3: #53, closed by a person while its filing mark stayed,
    shipped with #51 and is reverted.  Undone, quill leaves it out of the group's leaves
    (closed while still being filed), but production held it: keeping #51 without it is
    refused, and taking #51 out in the same release is whole."""
    tracker, base = released_group(repo, leaves=(51, 53))
    tracker.cards_by_number[53] = replace(tracker.cards_by_number[53],
                                          labels=("balance", FILING), is_open=False,
                                          state_reason="COMPLETED", touched_by_hand=True)
    cut = pull_request(repo, base, undo(53))
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        "it leaves plan#53 of the deploy-together group plan#50 out of production (its "
        "marker deleted) and keeps plan#51; a step the plan will never ship (plan#53) is "
        "a member while production holds it, at the base or the cut: a release that leaves "
        "production holding none of the group is whole (reverting the merge that landed each of "
        "plan#51); the group ships whole",)
    out = pull_request(repo, base, undo(53), undo(51))
    assert judge(repo, base, out, tracker).verdict.status == CLEAN


@pytest.mark.parametrize("abandon", ["its leaf dropped", "the split step closed"])
def test_a_shipped_step_split_on_the_web_and_abandoned_stays_a_member(repo, abandon):
    """The full-leaf review's W: a person splits SHIPPED #51 on the web (quill refuses to)
    into #511 and abandons the re-plan, by dropping #511 or by closing #51.  #51 will
    never ship again and its code stays in production, so a release taking #52 out alone
    is refused; taking #51 out as well is whole."""
    tracker, base = released_group(repo)
    replan(tracker, 51, (511,))
    closed = 511 if abandon == "its leaf dropped" else 51
    tracker.cards_by_number[closed] = replace(tracker.cards_by_number[closed], is_open=False,
                                              state_reason="NOT_PLANNED",
                                              touched_by_hand=True)
    cut = pull_request(repo, base, undo(52))
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        "it leaves plan#52 of the deploy-together group plan#50 out of production (its "
        "marker deleted) and keeps plan#51; a step the plan will never ship (plan#51) is "
        "a member while production holds it, at the base or the cut: a release that leaves "
        "production holding none of the group is whole (reverting the merge that landed each of "
        "plan#51); the group ships whole",)
    out = pull_request(repo, base, undo(52), undo(51))
    assert judge(repo, base, out, tracker).verdict.status == CLEAN


def test_a_step_that_will_never_ship_outside_a_group_is_no_member_of_it(repo):
    """#60, outside group #50, was released and is undone and dropped beside a follow-up
    fix to #51: it is under no group, so it is a member of none, and the group is whole."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52), ship(60))
    cut = pull_request(repo, base, ship(51), undo(60))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(60)
    tracker.close(60, "not_planned")
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    assert verdict.carried.groups[0].dead == ()


def test_a_finding_under_a_group_undone_and_dropped_is_no_member(repo):
    """#55, a finding #51 owns, shipped with the group and is reverted and dropped: it
    touches the group and is never a member (a fix the group cannot ship without is a
    leaf), so production keeping #51 and #52 is whole."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52), ship(55))
    cut = pull_request(repo, base, undo(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[51])
    tracker.close(55, "not_planned")
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    assert verdict.carried.steps[0].groups == ("plan#50",)
    assert verdict.carried.groups[0].through == ("plan#55 owned by plan#51",)


def test_production_after_a_release_is_the_merge_not_the_cut(repo):
    """M3's review, its 3: #51 and #52, the group #50, released; a hotfix on main undoes
    #52; the cut, built on dev before it, ships finding #55 under #51.  Production after the
    release is the merge, which keeps the hotfix's undo, so it holds #51 without #52 and the
    release is refused; read off the cut alone it would hold both and pass."""
    _, cutover = after_cutover(repo)
    dev = pull_request(repo, cutover, ship(51), ship(52))
    base = pull_request(repo, repo.merge(cutover, dev, "release"), undo(52),
                        files={"hotfix.py": "1"})
    cut = pull_request(repo, dev, ship(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[51])
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        "it leaves production holding plan#51 of the deploy-together group plan#50 without "
        "plan#52 (it names plan#55 owned by plan#51, under the group but no leaf of it); the "
        "group ships whole",)


def test_production_after_a_release_holds_a_member_a_hotfix_shipped(repo):
    """The twin: #51 released alone, a hotfix on main ships #52, and the cut, built on dev
    before it, ships finding #55 under #51.  The merge holds #51 and #52, the group whole,
    so the release passes; read off the cut alone it would lack #52 and be refused."""
    _, cutover = after_cutover(repo)
    dev = pull_request(repo, cutover, ship(51))
    base = pull_request(repo, repo.merge(cutover, dev, "release"), ship(52),
                        files={"hotfix.py": "1"})
    cut = pull_request(repo, dev, ship(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[51])
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(group.key, group.released, group.missing) for group in verdict.carried.groups] == [
        ("plan#50", ("plan#51", "plan#52"), ())]
    assert verdict.status == CLEAN


# -- every step below a group that production holds at the cut (C', 2026-10-14) -------------

def _way_out(verdict):
    """What reverting the merges the way out ``verdict``'s one refusal names writes to the
    markers (:func:`undo`): one delete per member it keeps, for the same release to make."""
    (refusal,) = verdict.refusals
    named = refusal.split("(reverting the merge that landed each of ", 1)[1].split(")", 1)[0]
    return tuple(undo(int(key.removeprefix("plan#"))) for key in named.split(", "))


def _person_closed(tracker, number, reason):
    """``number`` closed by a person on the web, as ``reason`` (``COMPLETED`` or
    ``NOT_PLANNED``)."""
    tracker.cards_by_number[number] = replace(tracker.cards_by_number[number], is_open=False,
                                              state_reason=reason, touched_by_hand=True)


def test_a_shipped_step_split_on_the_web_into_a_live_leaf_is_a_member_while_held(repo):
    """The full-leaf review's H1: a person splits SHIPPED #51 on the web into #511, still
    open.  #51's code stays in production, so a release taking #52 out alone leaves part of
    the group there: refused.  Taking #51 out too is whole."""
    tracker, base = released_group(repo)
    replan(tracker, 51, (511,))
    cut = pull_request(repo, base, undo(52))
    verdict = judge(repo, base, cut, tracker).verdict
    (group,) = verdict.carried.groups
    assert (group.released, group.missing, group.undone, group.dead) == (
        ("plan#51",), ("plan#511",), ("plan#52",), ())
    assert verdict.refusals == (
        "it leaves plan#52 of the deploy-together group plan#50 out of production (its "
        "marker deleted) and keeps plan#51, without plan#511; the group ships whole",)
    out = pull_request(repo, base, undo(52), undo(51))
    assert judge(repo, base, out, tracker).verdict.status == CLEAN


def test_the_way_out_a_refusal_names_is_whole_in_one_round(repo):
    """The full-leaf review's H2: #51 shipped, then split on the web into #511 (shipped,
    then closed by a person) and #513 (dropped); #52 undone and dropped.  The refusal names
    every member production keeps, #51 among them, and the release deleting those markers
    is whole, though taking #511 out makes it a dropped leaf and #51 a dropped step."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, pull_request(repo, cutover, ship(51), ship(52)),
                  ship(511))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511, 513))
    _person_closed(tracker, 511, "COMPLETED")
    tracker.close(513, "not_planned")
    tracker.close(52, "not_planned")
    cut = pull_request(repo, base, undo(52))
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.refusals == (
        "it leaves plan#52 of the deploy-together group plan#50 out of production (its "
        "marker deleted) and keeps plan#51, plan#511; a step the plan will never ship "
        "(plan#52) is a member while production holds it, at the base or the cut: a release "
        "that leaves production holding none of the group is whole (reverting the merge "
        "that landed each of plan#51, plan#511); the group ships whole",)
    out = pull_request(repo, cut, *_way_out(verdict))
    verdict = judge(repo, base, out, tracker).verdict
    assert verdict.status == CLEAN
    assert verdict.carried.groups[0].dead == ("plan#51", "plan#52", "plan#511")


def test_the_way_out_is_whole_when_it_takes_out_a_leaf_closed_while_still_marked(repo):
    """The full-leaf review's H2b: #521, filed under #52 and closed by a person while still
    marked, shipped; #51 undone and dropped.  Taken out, #521 is no leaf of #52 (closed
    while still being filed, and no longer shipped); the way out is still whole."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52), ship(521))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 52, (521,))
    tracker.cards_by_number[521] = replace(tracker.cards_by_number[521],
                                           labels=("balance", FILING))
    _person_closed(tracker, 521, "COMPLETED")
    tracker.close(51, "not_planned")
    cut = pull_request(repo, base, undo(51))
    verdict = judge(repo, base, cut, tracker).verdict
    assert _way_out(verdict) == (undo(52), undo(521))
    assert judge(repo, base, pull_request(repo, cut, *_way_out(verdict)),
                  tracker).verdict.status == CLEAN


@pytest.mark.parametrize("depth", [2, 3])
def test_a_step_that_will_never_ship_deep_below_the_group_is_a_member(repo, depth):
    """The full-leaf review's H3 and H8: E1q with the undone step two or three levels below
    the group.  #50 splits into #51 and #52, #51 into #511 (and #512 at depth 2), #511 into
    #5111 and #5112 at depth 3; the undone step is re-planned into leaves all dropped."""
    undone, kept = (511, 512) if depth == 2 else (5111, 5112)
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(undone), ship(kept),
                  ship(52))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511, 512) if depth == 2 else (511,))
    if depth == 3:
        replan(tracker, 511, (5111, 5112))
    replan(tracker, undone, (undone * 10 + 1,))
    tracker.close(undone * 10 + 1, "not_planned")
    cut = pull_request(repo, base, undo(undone))
    verdict = judge(repo, base, cut, tracker).verdict
    (group,) = verdict.carried.groups
    assert (group.released, group.undone, group.dead) == (
        ("plan#52", f"plan#{kept}"), (f"plan#{undone}",), (f"plan#{undone}",))
    assert _way_out(verdict) == (undo(52), undo(kept))


def test_a_dead_member_production_keeps_is_named_beside_a_missing_one(repo):
    """The full-leaf review's H4: #51 shipped, split on the web into #511, and #511
    dropped; #52 never shipped.  A fix to #55, a finding #51 owns, keeps #51 without #52,
    and the refusal names #51's way out too."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511,))
    tracker.close(511, "not_planned")
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[51])
    cut = pull_request(repo, base, ship(55))
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.refusals == (
        "it leaves production holding plan#51 of the deploy-together group plan#50 without "
        "plan#52 (it names plan#55 owned by plan#51, under the group but no leaf of it); a step "
        "the plan will never ship (plan#51) is a member while production holds it, at the "
        "base or the cut: a release that leaves production holding none of the group is "
        "whole (reverting the merge that landed each of plan#51); the group ships whole",)
    assert judge(repo, base, pull_request(repo, cut, *_way_out(verdict)),
                  tracker).verdict.status == CLEAN


def test_a_step_that_will_never_ship_carried_in_by_the_release_is_a_member(repo):
    """The full-leaf review's H5: #51 split before its ship merged, the split abandoned
    (#511 dropped), then its ship lands: production would hold #51 without #52."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(51))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511,))
    tracker.close(511, "not_planned")
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.refusals == (
        "it carries part of the deploy-together group plan#50 (plan#51) without plan#52; a "
        "step the plan will never ship (plan#51) is a member while production holds it, at "
        "the base or the cut: a release that leaves production holding none of the group is "
        "whole (reverting the merge that landed each of plan#51); the group ships whole",)
    assert judge(repo, base, pull_request(repo, cut, *_way_out(verdict)),
                  tracker).verdict.status == CLEAN


def test_an_offered_cut_is_held_to_the_leaves_the_cut_reads(repo):
    """The full-leaf review's H6, as the next review's T2 sharpened it: #51 and #52 in
    production; #521, filed under #52 and closed by a person while still marked, ships at
    the cut beside a second money mover.  Read at the cut, #521 is a leaf of #52, so the
    earlier cut shipping a fix under #52 keeps #51 and #52 without #521: it is refused,
    never offered.  Read with its own shipped set, #521 is no leaf and it would pass."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    earlier = pull_request(repo, base, ship(55), ship(40))
    cut = pull_request(repo, earlier, ship(521), ship(41))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 52, (521,))
    tracker.cards_by_number[521] = replace(tracker.cards_by_number[521],
                                           labels=("balance", FILING))
    _person_closed(tracker, 521, "COMPLETED")
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[52])
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    judged = judge(repo, base, cut, tracker)
    assert judged.verdict.status == REFUSED
    assert judged.passing is None


def test_a_still_marked_leaf_is_a_leaf_once_it_ships_at_the_cut(repo):
    """The next review's T1: #51 in production; #521, filed under #52 and closed by a person
    while still marked, ships.  Read with the cut's shipped set, #521 is #52's leaf and #52
    a split step no release holds, so production holds every member: clean.  Read with the
    base's, #521 would be no leaf and #52 a missing one."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51))
    cut = pull_request(repo, base, ship(521))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 52, (521,))
    tracker.cards_by_number[521] = replace(tracker.cards_by_number[521],
                                           labels=("balance", FILING))
    _person_closed(tracker, 521, "COMPLETED")
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.status == CLEAN
    (group,) = verdict.carried.groups
    assert (group.carried, group.released, group.missing) == (("plan#521",), ("plan#51",), ())


def test_the_way_out_names_every_member_kept_carried_or_released(repo):
    """A member that will never ship (#51) beside one carried in (#53), one released
    before (#52) and one missing (#54): the way out names #53, #51 and #52, and is whole."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(51), ship(52))
    cut = pull_request(repo, base, ship(53))
    tracker = FakeTracker()
    deploy_group(tracker, leaves=(51, 52, 53, 54))
    replan(tracker, 51, (511,))
    tracker.close(511, "not_planned")
    verdict = judge(repo, base, cut, tracker).verdict
    assert verdict.refusals == (
        "it carries part of the deploy-together group plan#50 (plan#53) without plan#54; a "
        "step the plan will never ship (plan#51) is a member while production holds it, at "
        "the base or the cut: a release that leaves production holding none of the group is "
        "whole (reverting the merge that landed each of plan#53, plan#51, plan#52); the "
        "group ships whole",)
    assert judge(repo, base, pull_request(repo, cut, *_way_out(verdict)),
                 tracker).verdict.status == CLEAN


def test_a_held_split_step_under_a_dropped_step_is_a_member(repo):
    """The full-leaf review's H7: #51, split into #511, closed by a person (a drop #511
    inherits); #511 shipped, then split on the web into #5111.  #511 is held and will
    never ship: a release taking #52 out alone is refused."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(511), ship(52))
    tracker = FakeTracker()
    deploy_group(tracker)
    replan(tracker, 51, (511,))
    replan(tracker, 511, (5111,))
    _person_closed(tracker, 51, "NOT_PLANNED")
    cut = pull_request(repo, base, undo(52))
    verdict = judge(repo, base, cut, tracker).verdict
    (group,) = verdict.carried.groups
    assert (group.released, group.undone, group.dead) == (
        ("plan#511",), ("plan#52",), ("plan#511",))
    assert _way_out(verdict) == (undo(511),)


# -- the command line ------------------------------------------------------------------------

def test_plan_prints_each_card_named_under_a_group_that_is_no_leaf_of_it(repo, capsys):
    """The group's line says why a release touching it through no leaf lists it."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(55))
    tracker = FakeTracker()
    deploy_group(tracker)
    tracker.add(55, kind="finding")
    tracker.block(55, tracker.cards_by_number[51])
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=lambda: tracker) == CLEAN
    assert ("group plan#50: carried none; released none; missing plan#51, plan#52; named "
            "under it plan#55 owned by plan#51") in capsys.readouterr().out.splitlines()


def test_plan_prints_each_member_the_plan_will_never_ship(repo, capsys):
    """The group's line names each member that will never ship, the one a refusal's way
    out is for (E1q)."""
    tracker, base = released_group(repo)
    cut = pull_request(repo, base, undo(51))
    replanned_and_dropped(tracker)
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=lambda: tracker) == REFUSED
    assert ("group plan#50: carried none; released plan#52; missing none; undone plan#51; "
            "the plan will never ship plan#51") in capsys.readouterr().out.splitlines()
