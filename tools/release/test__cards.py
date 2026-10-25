"""A release after the cutover, judged by the tracker's cards (rulings ``balance:R-BAL287``
- ``R-BAL289``): over throwaway repositories under tmp_path and quill's own in-memory
tracker.  No network, no docker, never a real checkout."""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.ci.scratch import run
from tools.quill.door import FakeTracker, GitHubError
from tools.quill.quill import CONNECT
from tools.release import _cards, release
from tools.release._carried import Declaration
from tools.release._model import STEPS_PATH, ReleaseError
from tools.release._plan import CLEAN, REFUSED, Inputs, plan
from tools.release.conftest import (
    MONEY,
    after_cutover,
    judge,
    pull_request,
    released_group,
    replan,
    ship,
    undo,
)


class Reached:
    """A tracker connection that counts each time it is made."""

    def __init__(self, tracker: FakeTracker) -> None:
        """Connect to ``tracker``."""
        self.tracker = tracker
        self.times = 0

    def __call__(self) -> FakeTracker:
        """The tracker, counted."""
        self.times += 1
        return self.tracker


def _never() -> FakeTracker:
    """A connection a test says is never made."""
    raise AssertionError("the tracker was reached")


# -- the era and the money flag -------------------------------------------------------------

def test_a_cut_after_the_cutover_is_judged_by_its_cards_labels(repo):
    """The placeholder this replaces refused such a cut as unbuilt (L9a-1b); it is now
    judged: each card's flag is its moves-money label as the tracker holds it."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(40), ship(41))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41)
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.money, s.undone) for s in verdict.carried.steps] == [
        ("plan#40", True, False), ("plan#41", False, False)]
    assert verdict.status == CLEAN


def test_a_cut_before_the_cutover_never_reaches_the_tracker(repo):
    """The 4d release is before the cutover: its reader is the registries', offline."""
    base = repo.plan((), parents=())
    cut = repo.commit("work", {"app.py": "1"}, parents=(base,))
    assert plan(repo.root, base, cut, Inputs(connect=_never)).verdict.status == CLEAN


def test_a_cut_after_the_cutover_with_no_tracker_given_judges_nothing(repo):
    """Only the command line reaches the live tracker; a caller giving no connection is
    stopped, never judged as carrying nothing."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(40))
    with pytest.raises(ReleaseError, match="no way to reach the tracker was given"):
        plan(repo.root, base, cut)


def test_two_money_cards_refuse_and_the_cut_with_one_is_offered(repo):
    """Two money movers on dev's line: refused, and the cut after the first offered.  The
    tracker is reached ONCE: the offered cut is judged with the cut's read."""
    base, cutover = after_cutover(repo)
    first = pull_request(repo, cutover, ship(40))
    cut = pull_request(repo, first, ship(41))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    reached = Reached(tracker)
    judged = plan(repo.root, base, cut, Inputs(connect=reached))
    assert judged.verdict.refusals == (
        "it carries 2 steps that move money (plan#40, plan#41); a release carries one at most",)
    assert judged.passing.cut == first
    assert reached.times == 1


def test_an_unrehearsed_money_card_is_refused(repo):
    """R-BAL281's interim names a card the same way it names a step."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(40))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    assert judge(repo, base, cut, tracker, ["plan#40"]).verdict.refusals == (
        "plan#40 moves money and has no rehearsal kit",)


# -- an undo (R-BAL288) ----------------------------------------------------------------------

def test_an_undone_money_card_counts_as_a_money_change(repo):
    """R-BAL288's example: #40 (money) released, then reverted; the release carrying the
    revert and #41 (money) carries two money changes."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(40))
    revert = pull_request(repo, base, undo(40))
    cut = pull_request(repo, revert, ship(41))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.undone) for s in verdict.carried.steps] == [
        ("plan#40", True), ("plan#41", False)]
    assert verdict.status == REFUSED
    assert verdict.refusals[0].startswith("it carries 2 steps that move money (plan#40, ")


def test_a_card_shipped_again_after_its_undo_is_carried_and_not_undone(repo):
    """A ship, its revert and a ship again inside one window: production holds it, and its
    commits are every one of the window that changed its marker, each pull request's own
    and the merge that landed it."""
    base, cutover = after_cutover(repo)
    first = pull_request(repo, cutover, ship(40))
    revert = pull_request(repo, first, undo(40))
    cut = pull_request(repo, revert, ship(40))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    (step,) = judge(repo, base, cut, tracker).verdict.carried.steps
    assert (step.money, step.undone) == (True, False)
    assert sorted(step.claims) == sorted(
        sha for merge in (first, revert, cut) for sha in (merge, run(repo.root, "rev-parse",
                                                                    f"{merge}^2")))


# -- the window: the NET change of the markers against the real merge (R-BAL359) ------------

def test_a_hotfix_on_main_the_cut_lacks_is_no_undo(repo):
    """The ruled report's finding 4: #41 released, then undone on main by a hotfix the cut
    lacks, and the cut ships #42.  Against the merge the release writes, the hotfix stays and
    the cut carries #42 alone; against the cut alone #41 would read as shipped again."""
    _, cutover = after_cutover(repo)
    dev = pull_request(repo, cutover, ship(41), files={"a.py": "1"})
    base = pull_request(repo, repo.merge(cutover, dev), undo(41), files={"hotfix.py": "1"})
    cut = pull_request(repo, dev, ship(42), files={"b.py": "1"})
    tracker = FakeTracker()
    tracker.add(41, labels=MONEY)
    tracker.add(42, labels=MONEY)
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.undone) for s in verdict.carried.steps] == [("plan#42", False)]
    assert verdict.status == CLEAN


def test_a_hotfix_a_resync_brought_to_dev_is_not_carried_again(repo):
    """#70 shipped on main by a hotfix, and main merged into dev: production already holds
    #70, so the next release carries what dev added beside it, #41 and #42, and not #70."""
    _, cutover = after_cutover(repo)
    hotfix = ship(70)
    base = pull_request(repo, cutover, hotfix, files={"hotfix.py": "1"})
    dev = pull_request(repo, cutover, ship(41), files={"a.py": "1"})
    resync = repo.commit("Merge main into dev", {"hotfix.py": "1", **hotfix},
                         parents=(dev, base))
    cut = pull_request(repo, resync, ship(42), files={"b.py": "1"})
    tracker = FakeTracker()
    for number in (41, 42, 70):
        tracker.add(number)
    verdict = judge(repo, base, cut, tracker).verdict
    assert [s.key for s in verdict.carried.steps] == ["plan#41", "plan#42"]
    assert verdict.status == CLEAN


def test_a_released_card_undone_and_claimed_again_is_carried_as_shipped_again(repo):
    """#40 (money) in production; dev reverts it and ships it again from a fresh claim,
    which writes a new line: the marker CHANGED against production's, so the release
    carries #40 once, shipped again and not undone, its money counted."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(40))
    cut = pull_request(repo, pull_request(repo, base, undo(40)), ship(40))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    verdict = judge(repo, base, cut, tracker, ["plan#40"]).verdict
    assert [(s.key, s.money, s.undone) for s in verdict.carried.steps] == [
        ("plan#40", True, False)]
    assert verdict.refusals == ("plan#40 moves money and has no rehearsal kit",)


def test_an_offered_cut_carries_a_card_the_cut_shipped_and_undid(repo):
    """#40 (money) shipped at the first cut and undone at the cut, #41 and #42 (money)
    shipped between: the cut carries #41 and #42 (#40 is in production on neither side, the
    ruled net change) and is refused.  The cut offered is the first, carrying #40 alone,
    judged with the cut's ONE read, which holds #40 because a commit of the window changed
    its marker."""
    base, cutover = after_cutover(repo)
    first = pull_request(repo, cutover, ship(40))
    second = pull_request(repo, first, ship(41))
    cut = pull_request(repo, second, undo(40), ship(42))
    tracker = FakeTracker()
    for number in (40, 41, 42):
        tracker.add(number, labels=MONEY)
    reached = Reached(tracker)
    judged = plan(repo.root, base, cut, Inputs(connect=reached))
    assert [s.key for s in judged.verdict.carried.steps] == ["plan#41", "plan#42"]
    assert judged.verdict.status == REFUSED
    assert judged.passing.cut == first
    assert [(s.key, s.undone) for s in judged.passing.carried.steps] == [("plan#40", False)]
    assert reached.times == 1


def test_an_entry_under_the_markers_that_is_no_card_is_refused(repo):
    """CI's landing check refuses one before it lands (its rule 1); one that reaches a
    release names no card, so what the release changes there cannot be read."""
    base, cutover = after_cutover(repo)
    cut = repo.commit("work", {"app.py": "1", "plan/ships/notes": "x\n",
                               "plan/ships/7/x": "y\n"}, parents=(cutover,))
    assert judge(repo, base, cut, FakeTracker()).verdict.refusals == tuple(
        f"{path} is no card's marker (a marker is the file plan/ships/<card number>), so "
        "what this release changes there cannot be read"
        for path in ("plan/ships/7/x", "plan/ships/notes"))


def test_an_entry_under_the_markers_that_is_no_file_is_refused(repo):
    """M3's review, its 7: a gitlink at a card's marker path is no marker by quill's one
    reading of a tree (``markers_in``), so a release changing one is refused as a stray,
    never carried as the card shipped."""
    base, cutover = after_cutover(repo)
    run(repo.root, "read-tree", cutover)
    run(repo.root, "update-index", "--add", "--cacheinfo", f"160000,{cutover},plan/ships/5")
    cut = run(repo.root, "commit-tree", run(repo.root, "write-tree"), "-p", cutover, "-m",
              "a gitlink")
    tracker = FakeTracker()
    tracker.add(5)
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        "plan/ships/5 is no card's marker (a marker is the file plan/ships/<card number>), so "
        "what this release changes there cannot be read",)


def test_an_earlier_cut_conflicting_on_a_marker_is_refused_and_the_release_judged(repo):
    """M3's review, its 1: #40 shipped on dev and undone there, claimed afresh and landed as
    a hotfix on main, and main merged back into dev, so dev and main merge cleanly; but each
    cut from #40's ship to its undo merges with main with a conflict on #40's marker.  The
    cut carries three money movers, the next two cuts two each, and each conflicting cut is
    refused for its conflict, so the cut offered is the first, never the whole release
    judged on nothing."""
    _, cutover = after_cutover(repo)
    first = pull_request(repo, cutover, ship(42), files={"a.py": "1"})
    shipped = pull_request(repo, first, ship(40), files={"b.py": "1"})
    both = pull_request(repo, shipped, ship(41), ship(44), files={"c.py": "1"})
    undone = pull_request(repo, both, undo(40), files={"d.py": "1"})
    hotfix = ship(40)
    base = pull_request(repo, cutover, hotfix, files={"hotfix.py": "1"})
    resync = repo.commit("Merge main into dev", {"hotfix.py": "1", **hotfix},
                         parents=(undone, base))
    cut = pull_request(repo, resync, ship(43), files={"e.py": "1"})
    tracker = FakeTracker()
    for number in (40, 41, 42, 43, 44):
        tracker.add(number, labels=MONEY)
    judged = plan(repo.root, base, cut, Inputs(connect=lambda: tracker))
    assert judged.verdict.status == REFUSED
    assert judged.passing.cut == first
    assert [s.key for s in judged.passing.carried.steps] == ["plan#42"]
    known = _cards.read(repo.root, base, cut, tracker)
    for conflicting in (both, shipped):
        assert _cards.carried(repo.root, base, conflicting, known).unreadable == (
            f"{conflicting[:12]} and {base[:12]} merge with a conflict on plan/ships/40, so "
            "what releasing this cut would leave production holding of that card cannot be "
            "read: it is not offered",)


def test_a_marker_conflicting_in_the_merge_of_base_and_cut_judges_nothing(repo, capsys):
    """#40 claimed on main and, apart, on dev: the merge the release would write conflicts
    on its marker, so whether production holds it after is unknown and nothing is judged."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(40), files={"hotfix.py": "1"})
    cut = pull_request(repo, cutover, ship(40), files={"a.py": "1"})
    tracker = FakeTracker()
    tracker.add(40)
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=lambda: tracker) == 2
    assert "merge with a conflict on plan/ships/40" in capsys.readouterr().err


# -- what the card reader cannot read --------------------------------------------------------

def test_an_old_form_claim_after_the_cutover_names_the_cut_to_release_first(repo):
    """R-BAL287: a step ticked before the cutover and not released by its freeze has no
    card; only a cut holding steps.md can judge it, and the refusal names the NEWEST such
    cut holding the claim (two hold steps.md after it here)."""
    base = repo.plan((("balance:A-1", "Do it.", "#1", "--"),), parents=())
    tick = repo.plan((("balance:A-1", "Did it.", "SHIPPED", "`0123abcd`"),), parents=(base,),
                     ships=("balance:A-1",))
    later = repo.plan((("balance:A-1", "Did it.", "SHIPPED", "`0123abcd`"),
                       ("balance:A-2", "Do more.", "#1", "--")), parents=(tick,))
    cutover = repo.commit("cutover", {STEPS_PATH: None}, parents=(later,))
    cut = pull_request(repo, cutover, ship(40))
    tracker = FakeTracker()
    tracker.add(40)
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        f"{tick[:12]} claims `balance:A-1` in the old form, which only a cut holding steps.md "
        "can judge (R-BAL287: the cutover's freeze ends with a release of every one): "
        f"release {later[:12]} first, with the release tool as that cut holds it",)


def test_an_old_form_claim_merged_after_the_cutover_is_prose(repo):
    """A claim on a branch merged after the cutover: the cut before the cutover holds
    steps.md but not the claim, so no release could judge it, and a marker is the record:
    it is prose (X-cx's marker_leaf_plan 2(c)), never a refusal wedging every release."""
    base = repo.plan((("balance:A-1", "Do it.", "#1", "--"),), parents=())
    before = repo.plan((("balance:A-1", "Do it.", "#1", "--"),
                        ("balance:A-2", "Do more.", "#2", "--")), parents=(base,))
    cutover = repo.commit("cutover", {STEPS_PATH: None}, parents=(before,))
    late = repo.commit("late", {"app.py": "1"}, ships=("balance:A-1",), parents=(base,))
    # The merge keeps the cutover's deletion, as git's does (the branch never touched it).
    cut = repo.commit("Merge pull request", {"app.py": "1"}, parents=(cutover, late))
    verdict = judge(repo, base, cut, FakeTracker()).verdict
    assert (verdict.carried.steps, verdict.refusals, verdict.status) == ((), (), CLEAN)


def test_an_old_form_reopens_before_the_cutover_is_refused_as_judged_by_no_reader(repo):
    """An undo of a step with no card, merged before the cutover and missed by its freeze:
    no reader judges what it takes out of production, the release tool as a cut holding
    steps.md holds it included (it reads no ``Reopens:``), so no cut is named to release
    first (M3's review, its 2)."""
    base = repo.plan((("balance:A-1", "Did it.", "SHIPPED", "`0123abcd`"),), parents=())
    revert = repo.commit("revert", {"app.py": "0"}, trailers=("Reopens: balance:A-1",),
                         parents=(base,))
    cutover = repo.commit("cutover", {STEPS_PATH: None}, parents=(revert,))
    cut = pull_request(repo, cutover, ship(40))
    tracker = FakeTracker()
    tracker.add(40)
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        f"{revert[:12]} carries `Reopens: balance:A-1` in the old form: it undoes a step "
        "that has no card, so no reader judges what it takes out of production (R-BAL287, "
        "R-BAL288)",)


def test_an_old_form_reopens_merged_after_the_cutover_is_prose(repo):
    """An undo of a step with no card, merged after the cutover: no cut holding steps.md
    holds it, so no release could judge it, and a marker is the record: prose.  The
    release carries the marker the same commit writes, judged as any is."""
    base, cutover = after_cutover(repo)
    revert = repo.commit("revert", {"app.py": "1", **ship(41)},
                         trailers=("Reopens: balance:A-1",), parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(41, labels=MONEY)
    assert judge(repo, base, revert, tracker, ["plan#41"]).verdict.refusals == (
        "plan#41 moves money and has no rehearsal kit",)


@pytest.mark.parametrize(("kind", "what"), [("ruling", "a ruling"), ("question", "a question")])
def test_a_marker_naming_a_card_that_is_no_work_is_refused(repo, kind, what):
    """A ruling or a question is never shipped; read as work it would pass as moving no
    money."""
    base, cutover = after_cutover(repo)
    typo = repo.commit("work", {"app.py": "1", **ship(12)}, parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(12, kind=kind)
    verdict = judge(repo, base, typo, tracker).verdict
    assert not verdict.carried.steps
    assert verdict.refusals == (
        f"plan#12 (its marker changed at {typo[:12]}) is {what}, which no commit ships, so "
        "it names no work whose flag can be read",)


def test_a_step_reverted_and_split_since_is_judged_by_its_own_label(repo):
    """The delta review's case: #60 (money) released, reverted, then split into #61 and
    #62.  Its marker was valid when it landed; the code it names was #60's own, so #60's
    own label judges it: an undone money change, never a block on every later release."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(60))
    cut = pull_request(repo, base, undo(60))
    tracker = FakeTracker()
    tracker.add(60, labels=MONEY)
    for leaf in (61, 62):
        tracker.add(leaf)
        tracker.add_child(60, tracker.cards_by_number[leaf])
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.money, s.undone) for s in verdict.carried.steps] == [
        ("plan#60", True, True)]
    assert verdict.status == CLEAN


# -- a card's own money label (the coordinator, under R-BAL207, 2026-10-14) ------------------

def test_a_step_undone_and_replanned_into_a_money_leaf_is_one_money_change(repo):
    """The full-leaf review's E2: #51 (no money label) undone, then re-planned as #511
    (moves money) and #512, both shipped.  #51 is judged by its own label, so the release
    carries one money change, #511's; reading #511's label onto #51 too counted two and
    refused every release from then on."""
    tracker, base = released_group(repo)
    reverted = pull_request(repo, base, undo(51))
    cut = pull_request(repo, reverted, ship(511), ship(512))
    replan(tracker, 51, (511, 512))
    tracker.cards_by_number[511] = replace(tracker.cards_by_number[511], labels=MONEY)
    verdict = judge(repo, base, cut, tracker).verdict
    assert [(s.key, s.money, s.undone) for s in verdict.carried.steps] == [
        ("plan#51", False, True), ("plan#511", True, False), ("plan#512", False, False)]
    assert verdict.status == CLEAN


def test_a_container_of_no_type_is_refused(repo):
    """A step split into leaves whose type a person cleared: its kind unread, it is
    refused like any card of no type, never read as moving no money."""
    base, cutover = after_cutover(repo)
    cut = repo.commit("work", {"app.py": "1", **ship(12)}, parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(12, kind=None)
    replan(tracker, 12, (121, 122))
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        f"plan#12 (its marker changed at {cut[:12]}) is a card of no type, which no commit "
        "ships, so it names no work whose flag can be read",)


def test_a_card_of_no_type_is_refused(repo):
    """quill never clears a card's type (its one retype makes a question a ruling), so a
    card of no type is a person's doing; its kind unread, it is refused, never read as
    moving no money."""
    base, cutover = after_cutover(repo)
    cut = repo.commit("work", {"app.py": "1", **ship(12)}, parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(12, kind=None)
    assert judge(repo, base, cut, tracker).verdict.refusals == (
        f"plan#12 (its marker changed at {cut[:12]}) is a card of no type, which no commit "
        "ships, so it names no work whose flag can be read",)


def test_a_trailer_after_the_cutover_is_prose(repo):
    """A marker is the record (R-BAL359): a ``plan#N`` trailer ships and undoes nothing,
    and a value of neither form, which no check refuses once CI's trailer check is gone,
    refuses nothing either (X-cx's m2_plan.md s.3: it would wedge every release over it)."""
    base, cutover = after_cutover(repo)
    cut = repo.commit("work", {"app.py": "1"}, trailers=(
        "Ships: plan#4a", "Ships: plan#40", "Reopens: plan#41"), parents=(cutover,))
    tracker = FakeTracker()
    tracker.add(40, labels=MONEY)
    tracker.add(41, labels=MONEY)
    verdict = judge(repo, base, cut, tracker).verdict
    assert (verdict.carried.steps, verdict.refusals, verdict.status) == ((), (), CLEAN)


def test_a_marker_naming_a_card_the_tracker_does_not_hold_is_refused(repo):
    """R-BAL289: no declaration fills it after the cutover."""
    base, cutover = after_cutover(repo)
    typo = repo.commit("work", {"app.py": "1", **ship(999)}, parents=(cutover,))
    verdict = judge(repo, base, typo, FakeTracker()).verdict
    assert not verdict.carried.steps
    assert verdict.refusals == (
        f"plan#999 (its marker changed at {typo[:12]}) is a card the tracker does not hold, "
        "so whether it moves money cannot be read (R-BAL289)",)


def test_a_declaration_on_a_cut_after_the_cutover_judges_nothing(repo):
    """Every card's flag is its label, so a declaration fills nothing (R-BAL287)."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(40))
    tracker = FakeTracker()
    tracker.add(40)
    with pytest.raises(ReleaseError, match="declare plan#40: names no step"):
        plan(repo.root, base, cut,
             Inputs((), [Declaration("plan#40", False, "x")], lambda: tracker))


# -- the command line ------------------------------------------------------------------------

def test_plan_prints_an_undone_card(repo, capsys):
    """The report names an undo beside its flag."""
    _, cutover = after_cutover(repo)
    base = pull_request(repo, cutover, ship(40))
    cut = pull_request(repo, base, undo(40))
    tracker = FakeTracker()
    tracker.add(40)
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=lambda: tracker) == CLEAN
    out = capsys.readouterr().out.splitlines()
    assert out[3].startswith("  plan#40  moves money: no  UNDONE: its marker deleted  "
                             "(deleted at ")


def test_plan_given_no_connection_exits_2_on_a_cut_after_the_cutover(repo, capsys):
    """The command line's caller reaches the live tracker only by passing it (``python -m``
    does); a caller passing none judges nothing."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(40))
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root) == 2
    assert "no way to reach the tracker was given" in capsys.readouterr().err


def test_plan_exits_2_when_the_tracker_cannot_be_read(repo, capsys):
    """A failed read of the tracker judges nothing; it never reads as no money."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(40))

    def refused():
        raise GitHubError(401, "GET /app -> 401")

    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=refused) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release plan: nothing was judged: ")


def test_plan_exits_2_when_the_apps_key_cannot_be_used(repo, capsys, monkeypatch):
    """L9d: a corrupt app.pem, read by the command line's own connection, escaped ``main``
    as a traceback with exit 1, which reads as refused; it judges nothing."""
    base, cutover = after_cutover(repo)
    cut = pull_request(repo, cutover, ship(40))
    monkeypatch.setattr("tools.quill._tracker.app_credentials",
                        lambda: ("Iv23client", b"not a pem"))
    assert release.main(["plan", "--base", base, "--cut", cut], root=repo.root,
                        connect=CONNECT) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("release plan: nothing was judged: the App's private key "
                                   "cannot be loaded")
