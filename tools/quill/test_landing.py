"""CI's landing check (X-cx M2): the one write rule over the ship markers a pull request's merge
writes, and the tracker's word on each card a change records; nothing calls GitHub.

Every history is a throwaway repository's, built and judged by the builders this module shares
with rule 8's (:mod:`tools.quill._histories`): its markers written as files and its merges
REAL ones, judged as CI judges one: the merge of the pull request's head into its base's tip
(:func:`_check`).  The
tracker is read through the REAL card-only reader (:class:`_tracker.CardReader`, review O3 of
a1b), so each card it judges is a :class:`_tracker.BareCard` built by ``card_from`` from a node
in the shape GitHub answers a read without the board, and a decision that reached for a board
item would raise.  The nodes are built by :func:`_card`, whose shape is held key for key to
the recorded lookup's (:func:`test_the_nodes_are_shaped_as_github_answers_the_reader`).  The
pull request is read through a stand-in for GitHub's answer (:func:`_pull`), and its own read
from GitHub's answer is graded apart (:func:`landing.pull_request`).
"""
from __future__ import annotations

import copy

import pytest
import requests

from tools.ci.scratch import commit_files, point_dev, point_main, run
from tools.quill import landing
from tools.quill._git import marker
from tools.quill._github import GitHubError
from tools.quill._histories import MON, PERSON, TUE, judging
from tools.quill._histories import Lookup as _Lookup
from tools.quill._histories import card_node as _card
from tools.quill._histories import claim as _claim
from tools.quill._histories import closed as _closed
from tools.quill._histories import landed as _landed
from tools.quill._histories import merge as _merge
from tools.quill._histories import never as _never
from tools.quill._histories import pull as _pull
from tools.quill._histories import reader as _reader
from tools.quill._histories import start as _start
from tools.quill._histories import tracker as _tracker
from tools.quill._histories import undone as _undone
from tools.quill._histories import verdict as _verdict
from tools.quill._recorded import recording
from tools.quill._tracker import CardReader, Tracker
from tools.quill.landing import (TOKEN_VARIABLE, CannotJudge, PullRequest, main, named,
                                 pull_from_environment, pull_request, reader_from_environment)
from tools.quill.setup_tracker import APP_LOGIN, DEPLOY_TOGETHER_LABEL, FILING, PLAN


def _paths(value, prefix=""):
    """Every key path in ``value``: a dict's keys, joined by dots, a list's items as ``[]``."""
    if isinstance(value, dict):
        return set().union(*({prefix + key} | _paths(item, f"{prefix}{key}.")
                             for key, item in value.items()))
    if isinstance(value, list):
        return set().union(set(), *(_paths(item, prefix.rstrip(".") + "[].") for item in value))
    return set()


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository whose ``origin/dev`` and ``origin/main`` hold one base commit, the start
    of every history below (:func:`_histories.start`); the repository and the base."""
    return _start(tmp_path)


@pytest.fixture(name="check")
def _check(capsys, code):
    """The check run over the merge-queue group of pull request #600 in ``code``'s repository
    (:func:`_histories.judging`): called with ``onto``, the tip of ``base``, and branch
    ``head`` at ``head_sha``, it judges GitHub's merge of the two, reading the tracker through
    ``github`` (no read allowed when None), and answers its exit status, its output and its
    error."""
    judged = judging(capsys, code[0])

    def _run(onto, head_sha, head, *, base="dev", github=None):
        """Judge pull request #600 of ``head`` into ``base`` (the fixture)."""
        return judged(onto, head_sha, head, base, _reader(github) if github else _never)

    return _run


def test_the_nodes_are_shaped_as_github_answers_the_reader():
    """Two nodes :func:`_card` builds, between them with a type, a label, a parent, a
    sub-issue and a close, hold exactly the key paths the recorded lookup's nodes hold
    between them, less their board items: the fields the card-only reader asks for
    (``_queries.BARE_CARD_FIELDS``).  No recorded node has a blocker, and :func:`_card`
    builds none (no decision here reads one), so a blocker's own shape is not held here."""
    recorded = next(exchange["answer"]["data"]["repository"]
                    for exchange in recording("tracker")["exchanges"]
                    if "c99999: issue(number: 99999)" in ((exchange["body"] or {}).get("query")
                                                         or ""))
    want = {path for node in recorded.values() if node for path in _paths(node)
            if not path.startswith("projectItems")}
    built = _tracker(_card(1), _closed(_card(2, parent=1), "COMPLETED", PERSON))
    assert _paths(built[1]) | _paths(built[2]) == want


# ---------------------------------------------------- arm 3: m2_probe/f2.sh's and f4.sh's cases


@pytest.mark.parametrize("head", ["revert-601", "card/212"])
def test_a_revert_of_the_revert_never_re_ships_a_card_from_any_branch(check, code, head):
    """Case 1: card 212 lands on dev, a release takes it to main, dev reverts it, and a revert
    of that revert, carrying more code, comes back.  Its marker's line is main's, so a later
    release would show no marker change while the code moved, and count none of it (the
    report's finding 1).  dev's own line held that line once, so it is refused from any
    branch, the card's own claim included: a card undone ships again from a fresh claim."""
    root, base = code
    landed = _landed(root, base, 212)
    point_main(root, _merge(root, base, landed, "release"))
    reverted = _undone(root, landed, 212)
    again = commit_files(root, reverted, 'Revert "Revert plan#212"',
                         {marker(212): f"claimed {MON}\n", "w212.txt": "work\n",
                          "app.py": "a money fix\n"})
    status, out, _ = check(reverted, again, head)
    assert status == 1
    assert out.splitlines() == [
        "REFUSED -- plan/ships/212: origin/dev held this very line once and undid it, so it "
        "would re-ship card 212 with no change to its record, and a release would count none "
        "of the code it brings back: a card undone ships again from a fresh claim (quill claim "
        "212), which writes a new line",
        _verdict(head, 1, 1)]


def test_a_resync_brings_mains_hotfix_without_reading_the_tracker(check, code):
    """Case 2: hotfix 700 lands on main; main, brought back into dev, carries the merge
    through which main took its marker in, judged where it landed: it passes, and the tracker
    is not read again (R-BAL184)."""
    root, base = code
    hotfix = _landed(root, base, 700)
    point_main(root, hotfix)
    dev = commit_files(root, base, "dev's own work", {"app.py": "dev\n"})
    assert check(dev, hotfix, "main")[:2] == (0, _verdict("main", 1, 0) + "\n")


def test_a_branch_that_merged_main_in_brings_its_hotfix(check, code):
    """Case 3: a branch that merged main in after hotfix 700 lands carries main's marker into
    dev, through the same merge: it passes."""
    root, base = code
    point_main(root, hotfix := _landed(root, base, 700))
    feature = commit_files(root, _merge(root, base, hotfix, "merge main in"), "x", {"x.py": "x\n"})
    assert check(base, feature, "feat/x")[0] == 0


def test_a_release_carries_the_claim_dev_held_at_its_cut_or_at_its_tip(check, code):
    """Cases 4 and 5: card 300 lands on dev (its first claim's line), is reverted, and lands
    again from a fresh claim (a new line).  A release cut before the revert carries the first
    line, and one of dev's tip the second: each holds the merge through which dev took its
    line in, so each passes into main, its tracker never read."""
    root, base = code
    cut = _landed(root, base, 300)
    tip = _landed(root, _undone(root, cut, 300), 300, TUE)
    point_dev(root, tip)
    assert check(base, cut, "release/1", base="main")[0] == 0
    assert check(base, tip, "dev", base="main")[0] == 0


def test_a_branch_carrying_another_cards_claim_is_refused(check, code):
    """Case 6: a branch merges card 400's in-flight claim and lands it into dev, which would
    ship card 400 half built (the report's finding 2): only card/400 may record it."""
    root, base = code
    carried = _merge(root, base, _claim(root, base, 400)[1], "carry card/400 in")
    status, out, _ = check(base, carried, "feat/cleanup")
    assert status == 1
    assert out.splitlines()[0] == (
        "REFUSED -- plan/ships/400: feat/cleanup adds it, but only card 400's own claim, "
        "card/400, may record it")


@pytest.mark.parametrize("how", ["a copy of its line", "its claim commit alone"])
def test_devs_line_reaches_main_only_with_the_merge_that_landed_it(check, code, how):
    """Cases 7 and 8 (``m2_probe/f4.sh``): card 500 lands on dev.  A change into main that
    copies its marker's line, or merges its claim commit alone, holds the line dev holds but
    not the merge that landed the card's code with it: main would record card 500 shipped
    with none of its code, and the release that later brought the code would count none of
    it.  Refused as a line dev took in that the change does not bring; the release itself
    (case 9) passes."""
    root, base = code
    claimed, work = _claim(root, base, 500)
    point_dev(root, dev := _merge(root, base, work, "Merge card/500"))
    head = (commit_files(root, base, "copy", {marker(500): f"claimed {MON}\n"})
            if how == "a copy of its line" else _merge(root, base, claimed, "claim alone"))
    status, out, _ = check(base, head, "copy", base="main")
    assert status == 1
    assert out.splitlines()[0] == _not_brought(500, "origin/dev")
    assert check(base, dev, "dev", base="main")[0] == 0


def _not_brought(number, other):
    """The refusal of card ``number``'s line, which the branch ``other`` took in, written by a
    change that does not bring it from there."""
    return (f"REFUSED -- plan/ships/{number}: {other} took this very line in, but this change "
            "does not bring the commit through which it did, or holds the one through which it "
            f"undid it, so it would record card {number} where its code is not, or re-ship it "
            f"with no change to its record: the line comes from {other} only through the merge "
            "that took it in, and a card undone ships again from a fresh claim (quill claim "
            f"{number}), which writes a new line")


@pytest.mark.parametrize("into", ["main", "dev"])
def test_a_copy_after_a_landing_and_its_revert_arrived_together_is_refused(check, code, into):
    """M2's review HIGH-1: a card lands on one branch and is reverted there, and a release
    (or resync) takes both to the other, whose history then holds the landing with no marker
    and no code.  A copy of the line into it does not BRING the landing (its first parent
    already holds it): refused, from any branch, card/500 included, as the card would read
    shipped with its code reverted everywhere."""
    root, base = code
    undone = _undone(root, _landed(root, base, 500), 500)
    (point_dev if into == "main" else point_main)(root, undone)
    onto = _merge(root, base, undone, "release" if into == "main" else "resync")
    other = "origin/dev" if into == "main" else "origin/main"
    for head in ("copy", "card/500"):
        copied = commit_files(root, onto, "copy", {marker(500): f"claimed {MON}\n"})
        status, out, _ = check(onto, copied, head, base=into)
        assert status == 1 and out.splitlines()[0] == _not_brought(500, other)


def test_a_line_whose_landing_the_target_holds_already_is_not_brought_again(check, code):
    """Card 5 lands on dev with one line while main holds another (its own hotfix), and the
    release's merge resolves the conflict to main's: main then holds dev's landing without
    its line, and nothing undid it.  A copy of dev's line into main does not BRING that
    landing (main's tip holds it), so it is refused: what was settled is not reopened by a
    copy."""
    root, base = code
    point_dev(root, landed := _landed(root, base, 5))
    hotfix = _landed(root, base, 5, TUE)
    kept = run(root, "commit-tree", f"{hotfix}^{{tree}}", "-p", hotfix, "-p", landed, "-m",
               "release, keeping main's line")
    copied = commit_files(root, kept, "copy", {marker(5): f"claimed {MON}\n"})
    status, out, _ = check(kept, copied, "copy", base="main")
    assert status == 1 and out.splitlines()[0] == _not_brought(5, "origin/dev")


def test_a_revert_of_a_revert_made_on_the_other_branch_is_refused(check, code):
    """M2's review MEDIUM-3: hotfix 700 lands on main and main reverts it; a branch off main
    reverts that revert, with a money fix, into dev.  It brings main's landing AND main's
    undo of it, so it would re-ship 700 with no change to its record, and rule 3 would then
    refuse every release: refused here, from any branch."""
    root, base = code
    reverted = _undone(root, _landed(root, base, 700), 700)
    point_main(root, reverted)
    for head in ("revert-700", "card/700"):
        again = commit_files(root, reverted, 'Revert "Revert plan#700"',
                             {marker(700): f"claimed {MON}\n", "w700.txt": "work\n",
                              "app.py": "a money fix\n"})
        status, out, _ = check(base, again, head)
        assert status == 1 and out.splitlines()[0] == _not_brought(700, "origin/main")


def test_a_queue_groups_later_entry_is_judged_on_the_merge_the_earlier_one_makes(capsys,
                                                                                   code):
    """M2's review: a group of several pull requests into dev, whose tip has not moved yet.
    A later entry's merge stands on the earlier one's, and is judged on it: card/8 after
    card/7's entry records card 8 alone; and after entries landing card 9 and reverting it,
    card/9 writing its old line again is refused as dev's own line's (rule 3, read at M's
    first parent, never at dev's tip), the tracker never read."""
    root, base = code
    github = _Lookup(_tracker(_card(8)))
    first = _merge(root, base, _claim(root, base, 7)[1], "Merge pull request #601")
    tip = _claim(root, base, 8)[1]
    later = _merge(root, first, tip, "Merge pull request #602")
    queue = f"refs/heads/gh-readonly-queue/dev/pr-602-{first}"
    assert main(["merge_group", later, queue], _reader(github), _pull("card/8", tip), root) == 0
    assert github.asked == [[8]]
    capsys.readouterr()
    undone = _undone(root, _landed(root, base, 9), 9)
    again = commit_files(root, undone, "claim 9 by hand", {marker(9): f"claimed {MON}\n"})
    status = main(["merge_group", _merge(root, undone, again), queue], _never,
                  _pull("card/9", again), root)
    assert status == 1
    assert "plan/ships/9: origin/dev held this very line once and undid it" in (
        capsys.readouterr().out)


# ----------------------------------------------------------------- arms 1, 2 and 5: the claim


def test_a_card_branch_records_its_card_read_as_work(check, code):
    """card/7 adds its marker beside its work: card 7 is a step, so it lands."""
    root, base = code
    github = _Lookup(_tracker(_card(7)))
    status, out, _ = check(base, _claim(root, base, 7)[1], "card/7", github=github)
    assert (status, out) == (0, _verdict("card/7", 1, 0) + "\n")
    assert github.asked == [[7]]


def test_a_card_branch_may_undo_another_card_but_record_only_its_own(check, code):
    """card/7 also deletes card 5's marker (an undo passes from any branch) and adds card 8's
    fresh one, which only card/8 may: card 8 is refused, card 7 alone is read."""
    root, base = code
    point_dev(root, dev := _landed(root, base, 5))
    tip = commit_files(root, _claim(root, dev, 7)[1], "more",
                       {marker(5): None, marker(8): f"claimed {TUE}\n"})
    github = _Lookup(_tracker(_card(7), _card(8)))
    status, out, _ = check(dev, tip, "card/7", github=github)
    assert status == 1
    assert out.splitlines() == [
        "REFUSED -- plan/ships/8: card/7 adds it, but only card 8's own claim, card/8, may "
        "record it", _verdict("card/7", 3, 1)]
    assert github.asked == [[7]]


def test_a_delete_passes_from_any_branch_and_reads_nothing(check, code):
    """A revert of card 5's landing, from a branch of no card: an undo, which lands."""
    root, base = code
    landed = _landed(root, base, 5)
    assert check(landed, _undone(root, landed, 5), "revert-5")[0] == 0


def test_a_card_branch_pushed_again_after_its_merge_records_nothing(check, code):
    """The report's 5c: card/7 landed, then more work was pushed to it and opened again.  Its
    change holds no marker (dev holds card 7's already), so it records nothing a change from
    card/7 must: refused, the tracker never read."""
    root, base = code
    tip = _claim(root, base, 7)[1]
    landed = _merge(root, base, tip, "Merge card/7")
    status, out, _ = check(landed,
                           commit_files(root, tip, "later work", {"app.py": "later\n"}), "card/7")
    assert status == 1
    assert out.splitlines() == [
        "REFUSED -- card/7 adds or changes no plan/ships/7, so it records nothing, though a "
        "change from card/7 records card 7 (pushed again after its merge? later work is a card "
        "of its own)", _verdict("card/7", 0, 1)]


def test_a_branch_of_another_repository_is_no_claim(check, code):
    """A fork's branch named card/7 adds card 7's marker: no claim, so it is refused."""
    root, base = code
    status, out, _ = check(base, _claim(root, base, 7)[1], None)
    assert status == 1
    assert out.splitlines() == [
        "REFUSED -- plan/ships/7: a branch of another repository adds it, but only card 7's "
        "own claim, card/7, may record it",
        _verdict("a branch of another repository", 1, 1)]


@pytest.mark.parametrize("path", ["plan/ships/notes", "plan/ships/07", "plan/ships/7/inner",
                                  "plan/ships"])
def test_an_entry_that_is_no_cards_marker_is_refused_and_its_delete_lands(check, code, path):
    """A name that is no card number, a leading zero, a directory below, and the directory
    itself a file are no card's marker: no change may add one, card/7 included (which then
    records nothing either); a change deleting one lands."""
    root, base = code
    status, out, _ = check(base,
                           commit_files(root, base, "stray", {path: "x\n"}), "card/7")
    assert status == 1
    assert out.splitlines()[1] == (
        f"REFUSED -- {path}: no card's marker is named so (a marker is the file plan/ships/<card "
        "number>), so no change may add or change it")
    point_dev(root, stray := commit_files(root, base, "stray", {path: "x\n"}))
    assert check(stray, commit_files(root, stray, "clean", {path: None}),
                 "clean")[0] == 0


# ------------------------------------------------------------------- arm 6: the records door


def test_the_records_door_adds_markers_into_main_for_cards_never_marked(check, code):
    """R-BAL363: a change into main that only adds markers, from any branch, records cards
    whose work landed before records existed, each read as work."""
    root, base = code
    records = commit_files(root, base, "records", {marker(2): "landed before records\n",
                                                   marker(4): "landed before records\n"})
    github = _Lookup(_tracker(_card(2), _card(4, "finding")))
    status, out, _ = check(base, records, "records", base="main", github=github)
    assert (status, out) == (0, _verdict("records", 2, 0, "main") + "\n")
    assert github.asked == [[2, 4]]


@pytest.mark.parametrize("ref", ["refs/remotes/origin/card/900",
                                 "refs/remotes/origin/released/900/20261019T130000Z"])
def test_the_records_door_never_records_a_card_a_claim_was_made_for(check, code, ref):
    """M2's review HIGH-2: card 900 is claimed (live, or released), its work not landed.  A
    change into main adding its claim's line, or a line of its own, would record it with none
    of its code, so the release that later brought the code would count none of it: refused,
    since a claimed card's work is the records' own, not work landed before them."""
    root, base = code
    claimed, work = _claim(root, base, 900)
    run(root, "update-ref", ref, work)
    for head in (_merge(root, base, claimed, "claim alone"),
                 commit_files(root, base, "records", {marker(900): "landed before records\n"})):
        status, out, _ = check(base, head, "records", base="main")
        assert status == 1
        assert out.splitlines()[0] == (
            "REFUSED -- plan/ships/900: a change that only adds markers records work landed "
            "before records existed, but a claim was made for card 900, so only card/900 may "
            "record it")


@pytest.mark.parametrize(("base", "files"), [
    ("dev", {}), ("main", {"app.py": "code\n"})], ids=["into dev", "beside code"])
def test_the_records_door_opens_only_into_main_for_a_change_of_markers_alone(check, code,
                                                                             base, files):
    """Into dev, or beside any other path, a change adding a marker is no records-only change:
    refused, as from any branch that is not the card's."""
    root, start = code
    records = commit_files(root, start, "records", {marker(2): "landed\n", **files})
    status, out, _ = check(start, records, "records", base=base)
    assert status == 1
    assert out.splitlines()[0].startswith("REFUSED -- plan/ships/2: records adds it, but only "
                                          "card 2's own claim, card/2, may record it")


def test_the_records_door_reads_each_card_against_the_tracker(check, code):
    """A card the door records is held to what a claim's is: a question is no work."""
    root, base = code
    records = commit_files(root, base, "records", {marker(2): "landed\n"})
    status, out, _ = check(base, records, "records", base="main",
                           github=_Lookup(_tracker(_card(2, "question"))))
    assert status == 1 and "plan/ships/2: plan#2 is a question, which no change ships" in out


# ---------------------------------------------------------- arm 7: the tracker's word on a card


def test_a_card_the_tracker_lacks_is_refused_and_one_past_graphqls_int_is_never_asked(check,
                                                                                    code):
    """card/999 records a number the tracker never filed; card/2147483648 one past GraphQL's
    32-bit Int, which is never asked (asked, it would cost the whole read): each refused as a
    card the tracker lacks."""
    root, base = code
    for number, asked in ((999, [[999]]), (2147483648, [])):
        github = _Lookup(_tracker())
        status, out, _ = check(base, _claim(root, base, number)[1],
                               f"card/{number}", github=github)
        assert status == 1 and github.asked == asked
        assert out.splitlines()[0] == (
            f"REFUSED -- plan/ships/{number}: the tracker holds no plan#{number} (a mistyped "
            "number?): a marker records a card the tracker holds")


@pytest.mark.parametrize(("cards", "what"), [
    ((_card(5), _card(6, parent=5)), "a container"),
    ((_closed(_card(5, "ruling"), "COMPLETED", APP_LOGIN),), "a ruling"),
    ((_card(5, "question"),), "a question"),
    ((_card(5, None),), "a card with no type"),
])
def test_a_card_that_is_no_work_is_refused_saying_what_it_is(check, code, cards, what):
    """A split step's work is its smaller steps (R-BAL177); a ruling is a record, a question
    the developer's, and a card with no type no work: each named as such."""
    root, base = code
    status, out, _ = check(base, _claim(root, base, 5)[1], "card/5",
                           github=_Lookup(_tracker(*cards)))
    assert status == 1
    assert out.splitlines()[0] == (
        f"REFUSED -- plan/ships/5: plan#5 is {what}, which no change ships: a marker records a "
        "step or a finding (a step split into smaller steps ships as them)")


def test_a_card_still_being_filed_is_refused(check, code):
    """R-BAL202: a card still carrying the filing mark, open, is still being filed."""
    root, base = code
    status, out, _ = check(base, _claim(root, base, 6)[1], "card/6",
                           github=_Lookup(_tracker(_card(6, labels=("balance", FILING)))))
    assert status == 1
    assert out.splitlines()[0] == ("REFUSED -- plan/ships/6: plan#6 is still being filed: "
                                   "finish it with quill file, then land")


@pytest.mark.parametrize("closed", [False, True], ids=["still being filed", "dropped while filed"])
def test_no_work_is_named_before_the_filing_mark(check, code, closed):
    """A question carrying the filing mark, open or closed, is refused as a question:
    finishing or reviving its filing would not make it work."""
    root, base = code
    question = _card(6, "question", labels=("balance", FILING))
    card = _closed(question, "NOT_PLANNED", PERSON) if closed else question
    status, out, _ = check(base, _claim(root, base, 6)[1], "card/6",
                           github=_Lookup(_tracker(card)))
    assert status == 1
    assert "plan#6 is a question" in out and "being filed" not in out


@pytest.mark.parametrize("parent", [50, None], ids=["still linked", "unlinked by sync"])
def test_a_step_dropped_while_still_being_filed_is_refused(check, code, parent):
    """The release tool's card reader rests on this refusal (``tools/release/_cards.py``):
    ``sync`` unlinks a step closed while still being filed from the group it was filed
    under, so one that then shipped would be in production under no group, and a release
    carrying it without its siblings would pass.  Group #50 splits into live #51 and #52,
    which a person closed while still marked: card/52's marker is refused, linked or not
    (the coordinator's re-ruling (B), 2026-10-17)."""
    root, base = code
    group = _card(50, labels=("balance", DEPLOY_TOGETHER_LABEL))
    dropped_leaf = _closed(_card(52, parent=parent, labels=("balance", FILING)), "NOT_PLANNED",
                           PERSON)
    status, out, _ = check(base, _claim(root, base, 52)[1], "card/52",
                           github=_Lookup(_tracker(group, _card(51, parent=50), dropped_leaf)))
    assert status == 1
    assert out.splitlines()[0] == (
        "REFUSED -- plan/ships/52: plan#52 was dropped while still being filed: if it is this "
        "change's work, reopen it and finish it with quill file, then land")


def test_a_step_shipped_and_closed_carrying_no_mark_lands_again_from_a_fresh_claim(check,
                                                                                    code):
    """Card 7 landed, was undone, and is re-shipped from a fresh claim before sync reopens
    it: closed by the tool as completed and carrying no mark, it is not dropped while still
    being filed, and its new line lands."""
    root, base = code
    undone = _undone(root, _landed(root, base, 7), 7)
    status, _, _ = check(undone, _claim(root, undone, 7, TUE)[1], "card/7",
                         github=_Lookup(_tracker(_closed(_card(7), "COMPLETED", APP_LOGIN))))
    assert status == 0


def _dropped_leaf():
    """Step #5 with one step linked under it, #6, closed by a person while still being
    filed: #6 is a leaf of #5 only once git says it shipped (``_state.leaves``)."""
    return _tracker(_card(5), _closed(_card(6, parent=5, labels=("balance", FILING)),
                                      "NOT_PLANNED", PERSON))


@pytest.mark.parametrize(("where", "split"), [
    ("nowhere", False), ("on main", True), ("on dev", True), ("on an unlanded branch", False),
    ("on dev, undone by the change", False)])
def test_a_step_is_split_by_the_leaves_shipped_once_the_change_and_the_other_branch_meet(
        check, code, where, split):
    """Whether #5 is split reads which leaves git says shipped once the merge and the other
    branch meet, as sync reads it once they have: #6 shipped on main (a hotfix dev lacks
    yet) or on dev splits #5, and card/5's marker is refused as a container's; #6 nowhere,
    only on a branch nothing says lands, or on dev but undone by card/5's own change, splits
    nothing, and #5 lands."""
    root, base = code
    onto = base
    if where == "on main":
        point_main(root, _landed(root, base, 6))
    elif where.startswith("on dev"):
        onto = _landed(root, base, 6)
    elif where == "on an unlanded branch":
        run(root, "update-ref", "refs/remotes/origin/feat-x", _claim(root, base, 6)[1])
    tip = _claim(root, onto, 5)[1]
    if where.endswith("undone by the change"):
        tip = commit_files(root, tip, "undo 6", {marker(6): None})
    status, out, _ = check(onto, tip, "card/5", github=_Lookup(_dropped_leaf()))
    assert status == (1 if split else 0)
    assert ("plan#5 is a container" in out) is split


def test_a_marker_carried_from_the_other_branch_is_never_read_again(check, code):
    """R-BAL184: card 5 landed on dev as a plain step, and has since been split into smaller
    steps, a container now.  The release carrying it to main is not judged against the
    tracker again, so permanent history cannot refuse a release for good."""
    root, base = code
    point_dev(root, dev := _landed(root, base, 5))
    assert check(base, dev, "dev", base="main")[0] == 0


def test_refusals_come_by_card(check, code):
    """Two records the tracker lacks, judged by card number, the smaller first."""
    root, base = code
    records = commit_files(root, base, "records", {marker(10): "x\n", marker(9): "y\n"})
    status, out, _ = check(base, records, "records", base="main",
                           github=_Lookup(_tracker()))
    assert status == 1
    assert [line[:28] for line in out.splitlines()[:2]] == ["REFUSED -- plan/ships/9: the",
                                                            "REFUSED -- plan/ships/10: th"]


# ------------------------------------------------------------- what cannot be read refuses


def test_a_change_writing_no_marker_lands_without_reading_the_tracker(check, code):
    """No marker, no read, and no token needed."""
    root, base = code
    head = commit_files(root, base, "plain", {"app.py": "x\n"})
    assert check(base, head, "feat/x")[:2] == (0, _verdict("feat/x", 0, 0) + "\n")


@pytest.mark.parametrize("fails", [
    GitHubError(200, "graphql: [{\"type\": \"FORBIDDEN\"}]"),
    requests.ConnectionError("the tracker cannot be reached"),
    requests.ReadTimeout("the tracker did not answer in time"),
])
def test_a_tracker_that_cannot_be_read_refuses_to_judge(check, code, fails):
    """An error GitHub answers, no answer at all, or none in time: exit 2, nothing checked."""
    root, base = code
    status, out, err = check(base, _claim(root, base, 7)[1], "card/7",
                             github=_Lookup({}, fails))
    assert (status, out) == (2, "")
    assert err.startswith("landing check: the tracker could not be read, so nothing was "
                          f"checked: {fails}")


def test_a_card_linked_to_one_the_tracker_lacks_refuses_to_judge(check, code):
    """#5 lists #6 under it and the tracker has no #6: whether #5 is split cannot be read."""
    root, base = code
    nodes = _tracker(_card(5), _card(6, parent=5))
    del nodes[6]
    status, _, err = check(base, _claim(root, base, 5)[1], "card/5",
                           github=_Lookup(nodes))
    assert status == 2 and "cards [6] are named by others but do not exist" in err


@pytest.mark.parametrize(("null", "what", "message"), [
    (lambda node: node.update(labels=None), "labels", "as null, so the read holds none of them"),
    (lambda node: node["subIssues"].update(nodes=None), "sub-issues",
     "as null, so the read holds none of them"),
    (lambda node: node["blockedBy"].update(nodes=None), "blockers",
     "as null, so the read holds none of them"),
    (lambda node: node["timelineItems"].update(nodes=None), "close events",
     "as null, so the read holds none of them"),
    (lambda node: node["labels"].update(nodes=[{"name": "balance"}, None], totalCount=2),
     "labels", "as null, so the read cannot say what it is"),
    (lambda node: node["subIssues"].update(nodes=[None], totalCount=1), "sub-issues",
     "as null, so the read cannot say what it is"),
    (lambda node: node["blockedBy"].update(nodes=[None], totalCount=1), "blockers",
     "as null, so the read cannot say what it is"),
    (lambda node: node["timelineItems"].update(nodes=[None]), "close events",
     "as null, so the read cannot say what it is"),
], ids=["labels", "sub-issue nodes", "blocker nodes", "close-event nodes", "a label",
        "a sub-issue", "a blocker", "a close event"])
def test_a_list_or_a_node_github_answers_as_null_refuses_to_judge(check, code, null, what,
                                                                  message):
    """A card's list, or one node of it, answered as null (GitHub's schema allows both) exits
    2 naming the card and the list, never a traceback and never read as none (a null list of
    close events read as none would say no one ever closed the card)."""
    root, base = code
    nodes = _tracker(_card(5))
    null(nodes[5])
    status, out, err = check(base, _claim(root, base, 5)[1], "card/5",
                             github=_Lookup(nodes))
    assert (status, out) == (2, "")
    one = "" if message.endswith("none of them") else "one of "
    assert err == ("landing check: what the tracker answered cannot be judged, so nothing was "
                   f"checked: GitHub answered {one}plan#5's {what} {message}\n")


def test_without_a_token_a_change_recording_a_card_is_not_judged(capsys, code, monkeypatch):
    """CI's default connection reads the token from its variable: unset or empty, a change
    recording a card exits 2 saying so, and one recording none is judged without it."""
    root, base = code
    tip = _claim(root, base, 7)[1]
    merge = _merge(root, base, tip)
    for value in (None, ""):
        if value is None:
            monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
        else:
            monkeypatch.setenv(TOKEN_VARIABLE, value)
        assert main(["pull_request", merge, "600"], pulls=_pull("card/7", tip), root=root) == 2
        assert f"{TOKEN_VARIABLE} is not set, so the tracker cannot be read" in (
            capsys.readouterr().err)
    plain = commit_files(root, base, "plain", {"app.py": "x\n"})
    assert main(["pull_request", _merge(root, base, plain), "600"], pulls=_pull("feat", plain),
                root=root) == 0


def test_the_token_reaches_a_reader_of_cards_without_the_board(monkeypatch):
    """The token in the variable makes a card-only reader of the plan's tracker, reading
    closes as the declared App's: never quill's App and never the board (R-BAL289)."""
    monkeypatch.setenv(TOKEN_VARIABLE, "a-read-only-token")
    monkeypatch.setattr(landing, "GitHub", lambda token: ("github", token))
    reader = reader_from_environment()
    assert isinstance(reader, CardReader) and not isinstance(reader, Tracker)
    assert (reader.github, reader.place, reader.app_login) == (
        ("github", "a-read-only-token"), PLAN, APP_LOGIN)


def test_a_marker_conflicting_with_the_other_branch_refuses_to_judge(check, code):
    """card/7 records a line for card 7 while main holds another: whether it shipped, and
    so whether a step is split, cannot be read until the two are settled."""
    root, base = code
    point_main(root, commit_files(root, base, "main's", {marker(7): f"claimed {TUE}\n"}))
    status, out, err = check(base, _claim(root, base, 7)[1], "card/7",
                             github=_Lookup(_tracker(_card(7))))
    assert (status, out) == (2, "")
    assert "merge with a conflict on plan/ships/7" in err


def test_a_merge_that_is_not_the_pull_requests_refuses_to_judge(capsys, code):
    """A commit of one parent, a merge whose second parent is another branch's than the head
    GitHub names, and a commit this repository lacks: exit 2, nothing judged."""
    root, base = code
    tip = _claim(root, base, 7)[1]
    elsewhere = _merge(root, base, _claim(root, base, 8)[1])
    for merge, said in ((tip, "is not a merge of pull request #600's head"),
                        (elsewhere, "is not a merge of pull request #600's head"),
                        ("f" * 40, "git rev-list")):
        assert main(["pull_request", merge, "600"], _never, _pull("card/7", tip), root) == 2
        err = capsys.readouterr().err
        assert err.startswith(f"landing check: {merge[:12]} could not be judged, so nothing "
                              "was checked: ") and said in err


def test_a_push_and_a_pull_request_into_another_branch_judge_nothing(capsys, code):
    """A push to main was judged in main's queue; a pull request into a branch stacked on
    another lands nothing on dev or main yet: each exits 0, saying so."""
    root, base = code
    assert main(["push", base, ""], _never, _pull("x", base), root) == 0
    assert "judged in main's merge queue" in capsys.readouterr().out
    assert main(["pull_request", base, "7"], _never, _pull("x", base, "feat/a"), root) == 0
    assert capsys.readouterr().out == (
        "landing check: pull request #7 merges into feat/a, neither dev nor main: what it "
        "carries is judged when feat/a's own pull request lands it\n")


@pytest.mark.parametrize("fails", [
    GitHubError(404, "GET /repos/o/r/pulls/7 -> 404"), requests.ConnectionError("unreachable"),
    CannotJudge("GitHub's answer for pull request #7 lacks 'base'")])
def test_a_pull_request_that_cannot_be_read_refuses_to_judge(capsys, fails):
    """GitHub's error, no answer, or an answer lacking a field: exit 2, nothing judged."""
    def _fails(number):
        raise fails
    assert main(["pull_request", "a" * 40, "7"], _never, _fails) == 2
    assert capsys.readouterr().err == (
        f"landing check: pull request #7 could not be read, so nothing was checked: {fails}\n")


@pytest.mark.parametrize("missing", ["GITHUB_TOKEN", "GITHUB_REPOSITORY"])
def test_the_pull_request_is_read_with_the_jobs_token_from_its_repository(monkeypatch, missing):
    """The job's own token reads the run's repository's pull request; either unset, nothing
    is read."""
    asked = []

    class _GitHub:
        """GitHub answering one pull request's read."""

        def __init__(self, token):
            """Keep the token."""
            asked.append(token)

        @staticmethod
        def rest(method, path):
            """Answer the read."""
            asked.append((method, path))
            return {"base": {"ref": "dev"},
                    "head": {"ref": "card/7", "sha": "a" * 40, "repo": {"full_name": "o/r"}}}

    monkeypatch.setattr(landing, "GitHub", _GitHub)
    monkeypatch.setenv("GITHUB_TOKEN", "the-jobs-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    assert pull_from_environment(7) == PullRequest(7, "dev", "card/7", "a" * 40)
    assert asked == ["the-jobs-token", ("GET", "/repos/o/r/pulls/7")]
    monkeypatch.delenv(missing)
    with pytest.raises(landing.NotSet, match=f"{missing} is not set"):
        pull_from_environment(7)


_ANSWER = {"base": {"ref": "dev"},
           "head": {"ref": "card/7", "sha": "a" * 40, "repo": {"full_name": "o/r"}}}


@pytest.mark.parametrize(("change", "head"), [
    (lambda answer: None, "card/7"),
    (lambda answer: answer["head"].update(repo=None), None),
    (lambda answer: answer["head"].update(repo={"full_name": "fork/r"}), None),
], ids=["this repository's", "a deleted repository's", "a fork's"])
def test_a_head_is_a_claim_only_as_a_branch_of_this_repository(change, head):
    """GitHub's answer read: the head branch is named only when it is this repository's."""
    answer = copy.deepcopy(_ANSWER)
    change(answer)
    assert pull_request(7, answer, "o/r") == PullRequest(7, "dev", head, "a" * 40)


@pytest.mark.parametrize("change", [
    lambda answer: answer.pop("base"), lambda answer: answer["head"].pop("sha"),
    lambda answer: answer["head"]["repo"].pop("full_name"),
    lambda answer: answer["base"].update(ref=None), lambda answer: answer.update(head=None),
], ids=["no base", "no head sha", "no head repository name", "a base that is no text",
        "a null head"])
def test_an_answer_lacking_what_the_rule_reads_cannot_be_judged(change):
    """A field missing, null where an object belongs, or no text where a name belongs: never
    read as a pull request into no branch, which would judge nothing and pass."""
    answer = copy.deepcopy(_ANSWER)
    change(answer)
    with pytest.raises(CannotJudge, match="pull request #7"):
        pull_request(7, answer, "o/r")


@pytest.mark.parametrize(("args", "number"), [
    (["pull_request", "a" * 40, "7"], 7),
    (["merge_group", "a" * 40, f"refs/heads/gh-readonly-queue/dev/pr-594-{'b' * 40}"], 594),
    (["merge_group", "a" * 64, f"gh-readonly-queue/main/pr-12-{'b' * 64}"], 12),
    (["pull_request", "a" * 40, "07"], None), (["pull_request", "a" * 40, ""], None),
    (["merge_group", "a" * 40, "7"], None),
    (["merge_group", "a" * 40, f"gh-readonly-queue/dev/pr-594-{'b' * 12}"], None),
    (["pull_request", "a" * 40, f"gh-readonly-queue/dev/pr-594-{'b' * 40}"], None),
    (["workflow_dispatch", "a" * 40, "7"], None), (["pull_request", "a" * 12, "7"], None),
    (["pull_request", "a" * 40], None), ([], None)])
def test_a_command_line_names_its_pull_request_by_its_event(args, number):
    """A pull request's own number; the number a queue group's branch ends with; each beside
    a full commit id.  Anything else names none."""
    assert named(args) == number


def test_a_command_line_naming_no_pull_request_is_refused(capsys):
    """Exit 2, with the usage."""
    assert main(["pull_request", "dev", "7"], _never) == 2
    assert capsys.readouterr().err.startswith("usage: python -m tools.quill.landing ")
