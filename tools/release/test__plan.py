"""A release judged over throwaway repositories under tmp_path and quill's own in-memory
tracker: each rule that refuses it, what the approval question must name, and the cut it
offers.  No network, no docker, never a real checkout."""
from __future__ import annotations

import pytest

from tools.quill.door import FakeTracker
from tools.release._model import ReleaseError
from tools.release._plan import CLEAN, NEEDS_APPROVAL, REFUSED, Inputs, Judge
from tools.release.conftest import MONEY, judge, migration, pull_request, ship


def _tracker(*movers: int, plain: tuple[int, ...] = ()) -> FakeTracker:
    """quill's fake holding each of ``movers`` labelled as moving money and each of
    ``plain`` with no label."""
    tracker = FakeTracker()
    for number in movers:
        tracker.add(number, labels=MONEY)
    for number in plain:
        tracker.add(number)
    return tracker


# -- two money movers --------------------------------------------------------------------

def test_two_money_movers_are_refused_and_the_newest_earlier_cut_that_passes_is_offered(repo):
    """R-BAL280's example: dev took #40 (moves money), then #41 (moves money), then #42.
    Cut at #42, two movers refuse it; the cut after #41 refuses too; the cut after #40
    passes and is the one offered."""
    base = repo.commit("base")
    first = pull_request(repo, base, ship(40))
    second = pull_request(repo, first, ship(41))
    third = pull_request(repo, second, ship(42))
    judged = judge(repo, base, third, _tracker(40, 41, plain=(42,)))
    assert judged.verdict.status == REFUSED
    assert judged.verdict.refusals == (
        "it carries 2 steps that move money (plan#40, plan#41); a release carries "
        "one at most",)
    assert judged.passing is not None and judged.passing.cut == first
    assert [s.key for s in judged.passing.carried.steps] == ["plan#40"]
    assert judged.one_migration is None


def test_one_money_mover_is_clean_and_no_cut_is_offered(repo):
    """The control: one mover alone passes, and nothing earlier is judged or offered."""
    base = repo.commit("base")
    cut = pull_request(repo, base, ship(40))
    judged = judge(repo, base, cut, _tracker(40))
    assert (judged.verdict.status, judged.passing, judged.one_migration) == (CLEAN, None, None)


def test_a_refused_cut_with_no_earlier_cut_that_passes_offers_none(repo):
    """Both movers in one pull request: no earlier state of dev passes."""
    base = repo.commit("base")
    cut = pull_request(repo, base, ship(40), ship(41))
    judged = judge(repo, base, cut, _tracker(40, 41))
    assert judged.verdict.status == REFUSED and judged.passing is None


# -- migrations -------------------------------------------------------------------------------

VERSIONS = "migrations/versions"


def _migration_history(repo):
    """A base holding two migrations; dev then takes one adding b2, then one adding c3,
    editing a1 and deleting z0, and touching two files no revision lives in."""
    base = repo.commit("base", {f"{VERSIONS}/a1_first.py": migration("a1", None),
                                f"{VERSIONS}/z0_old.py": migration("z0", None)})
    first = repo.merge(base, repo.commit("add b2", {f"{VERSIONS}/b2_second.py":
                                                    migration("b2", "a1")}, parents=(base,)))
    second = repo.merge(first, repo.commit("add c3", {
        f"{VERSIONS}/c3_third.py": migration("c3", "b2"),
        f"{VERSIONS}/a1_first.py": migration("a1", None) + "# edited\n",
        f"{VERSIONS}/z0_old.py": None,
        f"{VERSIONS}/sub/not_a_revision.py": migration("x9", None),
        f"{VERSIONS}/README": "notes\n"}, parents=(first,)))
    return base, first, second


def test_each_migration_is_named_by_its_revision_and_two_added_need_approval(repo):
    """ADDED, EDITED and DELETED, each with its revision; a file outside the chain's own
    directory names none.  Two added, one edited and one deleted are named for approval,
    and the cut before the second added one is offered."""
    base, first, second = _migration_history(repo)
    judged = judge(repo, base, second, FakeTracker())
    assert [(m.change, m.revision, m.path) for m in judged.verdict.migrations] == [
        ("EDITED", "a1", f"{VERSIONS}/a1_first.py"),
        ("ADDED", "b2", f"{VERSIONS}/b2_second.py"),
        ("ADDED", "c3", f"{VERSIONS}/c3_third.py"),
        ("DELETED", "z0", f"{VERSIONS}/z0_old.py"),
    ]
    assert judged.verdict.status == NEEDS_APPROVAL
    assert judged.verdict.approvals == (
        f"it adds 2 migrations: b2 ({VERSIONS}/b2_second.py), c3 ({VERSIONS}/c3_third.py)",
        f"it edited migration a1 ({VERSIONS}/a1_first.py), which is already on main and "
        "which production never re-runs",
        f"it deleted migration z0 ({VERSIONS}/z0_old.py), which is already on main and "
        "which production never re-runs",
    )
    assert judged.passing is None
    assert judged.one_migration is not None and judged.one_migration.cut == first


def test_one_added_migration_is_clean(repo):
    """The control: one added migration alone needs no extra approval line."""
    base, first, _ = _migration_history(repo)
    assert judge(repo, base, first, FakeTracker()).verdict.status == CLEAN


def test_a_merge_main_holds_and_dev_lacks_is_no_deletion(repo):
    """Trap (j): main took a commit dev never did.  What the release brings is the cut's
    side since the two met, so main's own migration is not read as deleted."""
    base, first, _ = _migration_history(repo)
    main = repo.commit("main only", {f"{VERSIONS}/q9_main.py": migration("q9", "a1")},
                       parents=(base,))
    assert [(m.change, m.revision)
            for m in judge(repo, main, first, FakeTracker()).verdict.migrations] == [
        ("ADDED", "b2")]


def test_the_cut_before_the_second_migration_skips_a_cut_still_adding_two(repo):
    """dev took b2, then c3, then d4: cut at d4, the cut after c3 still adds two, so the
    one offered is the cut after b2."""
    base, first, second = _migration_history(repo)
    third = repo.merge(second, repo.commit("add d4", {f"{VERSIONS}/d4_fourth.py":
                                                      migration("d4", "c3")}, parents=(second,)))
    judged = judge(repo, base, third, FakeTracker())
    assert [m.revision for m in judged.verdict.added] == ["b2", "c3", "d4"]
    assert judged.one_migration is not None and judged.one_migration.cut == first


def test_a_migration_named_with_a_non_ascii_character_is_read(repo):
    """git quotes such a path in its plain output; read NUL-separated, it is one more added
    migration, so two added need the approval question."""
    base, first, _ = _migration_history(repo)
    cut = repo.merge(first, repo.commit("add c3", {f"{VERSIONS}/c3_é.py":
                                                   migration("c3", "b2")}, parents=(first,)))
    assert [m.revision for m in judge(repo, base, cut, FakeTracker()).verdict.added] == [
        "b2", "c3"]


def test_an_empty_range_judges_nothing(repo):
    """A cut ``base`` already holds releases nothing."""
    base = repo.commit("base")
    with pytest.raises(ReleaseError, match="holds no commit"):
        judge(repo, base, base, FakeTracker())


# -- what the skill says has no rehearsal kit -----------------------------------------------

def test_an_unrehearsed_money_mover_is_refused_and_the_cut_before_it_offered(repo):
    """R-BAL281's interim: a money mover with no kit refuses the release, in R-BAL280's
    shape, and the offered cut does not carry it."""
    base = repo.commit("base")
    first = pull_request(repo, base, ship(41))
    second = pull_request(repo, first, ship(40))
    judged = judge(repo, base, second, _tracker(40, plain=(41,)), ["plan#40"])
    assert judged.verdict.refusals == ("plan#40 moves money and has no rehearsal kit",)
    assert judged.passing is not None and judged.passing.cut == first


def test_an_unrehearsed_added_migration_is_refused(repo):
    """An added migration with no kit refuses the release too; no earlier cut exists."""
    base, first, _ = _migration_history(repo)
    judged = judge(repo, base, first, FakeTracker(), ["b2"])
    assert judged.verdict.refusals == (
        f"migration b2 ({VERSIONS}/b2_second.py) has no rehearsal kit",)
    assert judged.passing is None


@pytest.mark.parametrize("name", ["plan#41", "a1", "zz"])
def test_an_unrehearsed_name_naming_no_mover_and_no_added_migration_judges_nothing(repo, name):
    """A card moving no money, an edited migration and a typo: each would refuse nothing,
    so the command refuses to judge rather than read as rehearsed."""
    base = repo.commit("base", {f"{VERSIONS}/a1_first.py": migration("a1", None)})
    cut = repo.commit("ship #41", {**ship(41),
                                   f"{VERSIONS}/a1_first.py": migration("a1", None) + "#\n"},
                      parents=(base,))
    with pytest.raises(ReleaseError, match=f"unrehearsed {name}"):
        judge(repo, base, cut, _tracker(plain=(41,)), [name])


# -- an offered cut is judged with what the release's cut knows (the delta review) -----------

def test_an_unrehearsed_mover_refuses_every_earlier_cut_carrying_it(repo):
    """The skill found no kit for #40 at the cut; the earlier cut carries #40 too.  It is
    refused all the same: no kit was ever judged."""
    base = repo.commit("base")
    first = pull_request(repo, base, ship(40))
    cut = pull_request(repo, first, ship(41))
    judged = judge(repo, base, cut, _tracker(40, plain=(41,)), ["plan#40"])
    assert judged.verdict.refusals == ("plan#40 moves money and has no rehearsal kit",)
    assert judged.passing is None


def test_an_earlier_cut_adding_a_migration_the_cut_does_not_is_refused(repo):
    """dev took aa, then deleted it and took bb under a new id; bb has no kit.  The earlier
    cut adds aa, which the skill never saw, so it is no cut to offer."""
    base = repo.commit("base")
    first = repo.merge(base, repo.commit("add aa", {f"{VERSIONS}/aa_first.py":
                                                    migration("aa", None)}, parents=(base,)))
    cut = repo.merge(first, repo.commit("redo as bb", {f"{VERSIONS}/aa_first.py": None,
                                                       f"{VERSIONS}/bb_first.py":
                                                       migration("bb", None)},
                                        parents=(first,)))
    tracker = FakeTracker()
    judged = judge(repo, base, cut, tracker, ["bb"])
    assert judged.verdict.refusals == (f"migration bb ({VERSIONS}/bb_first.py) has no "
                                       "rehearsal kit",)
    assert judged.passing is None
    candidate = Judge(repo.root, base, cut, Inputs(connect=lambda: tracker)).verdict(
        first, "add aa")
    assert candidate.refusals == (
        f"it adds migration aa ({VERSIONS}/aa_first.py), which the release's cut does not "
        "add, so no rehearsal of it was judged",)
