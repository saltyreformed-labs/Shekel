"""CI's landing check over a finding's marker and the findings a claim's card owes (rulings
plan#1777, "With its fix, enforced", and plan#1784, "One owner, always a leaf").

A finding fixed inside the pull request of the step that owns it (``R-BAL395``: the one step
it is blocked by) ships in that pull request: the step's branch writes the finding's marker
``plan/ships/F`` (``quill finding-file``), rule 5 lets a claim write it only when the tracker
says the claim's card owns it, and rule 7 refuses the step's pull request while it owns a
finding git does not say shipped once the change and the other branch meet, neither marked
nor handed on.  The backstop: a leaf's pull request is refused while a step above it, split
into smaller steps, owns one (a hand edit on the web).  Every history is built and judged,
and every tracker answered, by the builders the landing tests share
(:mod:`tools.quill._histories`).
"""
from __future__ import annotations

import pytest

from tools.ci.scratch import commit_files, point_dev, point_main, run
from tools.quill._git import marker, shipped
from tools.quill._histories import MON, PERSON, Lookup, card_node, claim, closed, judging
from tools.quill._histories import key_paths, landed, merge, never, reader, start, tracker
from tools.quill._histories import verdict
from tools.quill._queries import blocking
from tools.quill._recorded import recording
from tools.quill._state import sync_plan
from tools.quill._tracker import card_from
from tools.quill.setup_tracker import APP_LOGIN, PLAN


@pytest.fixture(name="code")
def _code(tmp_path):
    """A repository whose ``origin/dev`` and ``origin/main`` hold one base commit; the
    repository and the base (:func:`_histories.start`)."""
    return start(tmp_path)


@pytest.fixture(name="check")
def _check(capsys, code):
    """Judge pull request #600 of branch ``head`` at ``head_sha`` into ``base``, whose tip is
    ``onto`` (:func:`_histories.judging`), reading the tracker through ``github`` (no read
    allowed when None): its exit status, output and error."""
    judged = judging(capsys, code[0])

    def _run(onto, head_sha, head, *, base="dev", github=None):
        """Judge pull request #600 of ``head`` into ``base`` (the fixture)."""
        return judged(onto, head_sha, head, base, reader(github) if github else never)

    return _run


def _fixed(root, on, finding):
    """The commit on ``on`` fixing finding ``finding``: its fix, and its marker as ``quill
    finding-file`` writes it, ``fixed <MON>``; its sha."""
    return commit_files(root, on, f"fix plan#{finding}",
                        {f"f{finding}.txt": "fix\n", marker(finding): f"fixed {MON}\n"})


def _foreign(tail="", head="card/7", number=12):
    """Rule 5's refusal of finding ``number``'s marker, which ``head`` adds and may not, with
    what rule 7 adds to it (``tail``)."""
    return (f"REFUSED -- plan/ships/{number}: {head} adds it, but only card {number}'s own "
            f"claim, card/{number}, may record it{tail}")


def _owes(number=12, card=7, head="card/7"):
    """The owed-finding arm's refusal of ``head``, recording card ``card``, which owns open
    finding ``number`` and holds no marker for it."""
    return (f"REFUSED -- plan/ships/{number}: {head} records card {card}, but holds no marker "
            f"for finding plan#{number}, which it owns (R-BAL395) and which is neither shipped "
            f"nor dropped: write it in the commit that fixes it (quill finding-file "
            f"plan#{number}), or hand it to the step that does (quill hand plan#{number} --to "
            "plan#S)")


#: How the one-owner rule names a finding blocked by card 7 and card 8.
_TWO = ("it is blocked by plan#7, plan#8, so it names no one owner: `quill hand plan#12 --to "
        "plan#S`, S the one of them that owns it, removes the other's link")


# ------------------------------------------------------- the stand-in, held to GitHub's answer


def test_a_blocker_and_the_read_of_what_a_card_blocks_are_shaped_as_github_answers_them():
    """The recorded read of what step #220 blocks (``recorded/blocking.json``) and the
    recorded node of finding #221, blocked by it: a node :func:`card_node` builds with a
    blocker and, as #221 was recorded, no label holds exactly #221's key paths, less its
    board items, and the stand-in's answer
    to the read of what a card blocks holds exactly the recorded answer's."""
    exchanges = recording("blocking")["exchanges"]
    node = next(exchange["answer"]["data"]["repository"]["c221"] for exchange in exchanges
                if "c221: issue(number: 221)" in ((exchange["body"] or {}).get("query") or ""))
    read = next(exchange["answer"]["data"] for exchange in exchanges
                if "blocking(first" in ((exchange["body"] or {}).get("query") or ""))
    assert key_paths(card_node(221, "finding", labels=(), blocked_by=(220,))) == {
        path for path in key_paths(node) if not path.startswith("projectItems")}
    github = Lookup(tracker(card_node(220), card_node(221, "finding", blocked_by=(220,))))
    assert key_paths(github.graphql(blocking(PLAN), number=220)) == key_paths(read)


# ------------------------------------------------------------- rule 5: whose claim may write it


def test_a_findings_marker_lands_from_its_owners_claim(check, code):
    """Finding 12 is blocked by step 7 alone, its owner: card/7 lands card 7 with the commit
    fixing it and its marker, both read in the one read, and what card 7 blocks read once,
    which reads no card again."""
    root, base = code
    github = Lookup(tracker(card_node(7), card_node(12, "finding", blocked_by=(7,))))
    status, out, _ = check(base, _fixed(root, claim(root, base, 7)[1], 12), "card/7",
                           github=github)
    assert (status, out) == (0, verdict("card/7", 2, 0) + "\n")
    assert (github.asked, github.blocks_asked) == ([[7, 12]], [7])


@pytest.mark.parametrize(("cards", "tail"), [
    ((card_node(8), card_node(12, "finding", blocked_by=(8,))),
     ", or its owner's, card/8 (a finding's marker is written on its owner's branch by `quill "
     "finding-file plan#12`, R-BAL395)"),
    ((card_node(8), card_node(12, "finding", blocked_by=(7, 8))),
     f", and it names no one owner whose claim may (R-BAL395): {_TWO}"),
    ((card_node(12, "finding"),),
     ", and it names no one owner whose claim may (R-BAL395): it is blocked by no card: `quill "
     "block plan#12 --by plan#S` gives it one, S a step a branch builds"),
], ids=["owned by another step", "blocked by two cards", "blocked by none"])
def test_a_findings_marker_from_a_claim_that_is_not_its_one_owner_is_refused(check, code, cards,
                                                                            tail):
    """card/7 writes finding 12's marker, but 12 is owned by step 8, blocked by 7 and 8 (no
    one owner), or blocked by none: refused, naming the owner's branch beside 12's own claim,
    or how to give 12 the one owner it lacks."""
    root, base = code
    github = Lookup(tracker(card_node(7), *cards))
    status, out, _ = check(base, _fixed(root, claim(root, base, 7)[1], 12), "card/7",
                           github=github)
    assert (status, out.splitlines()) == (1, [_foreign(tail), verdict("card/7", 2, 1)])


def test_a_finding_blocked_by_a_finding_is_owned_by_no_step(check, code):
    """card/7 is the claim of finding 7, which finding 12 is blocked by alone: a finding's
    owner is a step a branch builds, so card/7 may not write 12's marker."""
    root, base = code
    github = Lookup(tracker(card_node(7, "finding"), card_node(12, "finding", blocked_by=(7,))))
    status, out, _ = check(base, _fixed(root, claim(root, base, 7)[1], 12), "card/7",
                           github=github)
    assert (status, out.splitlines()) == (1, [_foreign(
        ", and its one owner, plan#7, is no step: a finding's owner is a step a branch builds: "
        "live work, never a step split into smaller steps (R-BAL395, plan#1784)"),
        verdict("card/7", 2, 1)])


def test_a_claim_recording_no_card_of_its_own_writes_no_findings_marker(check, code):
    """card/7 writes finding 12's marker, which its card owns, but not its own: it records
    nothing, and 12's marker is refused as another card's, since only a change recording the
    owner's card ships the owner's fix: 12 is read, with the card it is blocked by, as every
    card a change records is, and nothing card 7 blocks is."""
    root, base = code
    github = Lookup(tracker(card_node(7), card_node(12, "finding", blocked_by=(7,))))
    status, out, _ = check(base, _fixed(root, base, 12), "card/7", github=github)
    assert status == 1
    assert out.splitlines()[1:] == [_foreign(), verdict("card/7", 1, 2)]
    assert github.asked == [[12], [7]] and not github.blocks_asked


@pytest.mark.parametrize("head", ["dependabot/uv/requests-2.33.0", None],
                         ids=["dependabot", "a fork"])
def test_a_pull_request_that_is_no_claims_reads_nothing_and_writes_no_findings_marker(
        check, code, head):
    """A Dependabot pull request and a fork's get no key, so no token: neither is a claim, so
    neither reads the tracker.  A change of its code alone lands; one writing a finding's
    marker is refused by rule 5, the tracker never read."""
    root, base = code
    bump = commit_files(root, base, "bump",
                        {"uv.lock": '[[package]]\nname = "requests"\nversion = "2.33.0"\n'})
    who = head or "a branch of another repository"
    assert check(base, bump, head)[:2] == (0, verdict(who, 0, 0) + "\n")
    status, out, _ = check(base, _fixed(root, base, 12), head)
    assert (status, out.splitlines()) == (1, [_foreign(head=who), verdict(who, 1, 1)])


# ----------------------------------------------------------- rule 7: the findings a card owes


@pytest.mark.parametrize(("close", "owed"), [
    (None, True), (("COMPLETED", APP_LOGIN), True), (("COMPLETED", PERSON), False),
    (("NOT_PLANNED", APP_LOGIN), False)],
    ids=["open", "closed by the tool as shipped", "closed by a person", "dropped by quill drop"])
def test_a_claim_is_refused_while_its_card_owns_an_unmarked_finding(check, code, close, owed):
    """card/7 records card 7, which owns finding 12, with no marker for it.  An open finding
    is owed, and so is one the TOOL closed as shipped, as that close only shows git (a revert
    since deleted its marker): refused, naming the marker and the hand-off.  A person's close
    is a drop, and so is ``quill drop``'s: it lands."""
    root, base = code
    finding = card_node(12, "finding", blocked_by=(7,))
    github = Lookup(tracker(card_node(7), closed(finding, *close) if close else finding))
    status, out, _ = check(base, claim(root, base, 7)[1], "card/7", github=github)
    assert (status, out.splitlines()) == ((1, [_owes(), verdict("card/7", 1, 1)]) if owed
                                          else (0, [verdict("card/7", 1, 0)]))
    assert (github.asked, github.blocks_asked) == ([[7], [12]], [7])


def test_a_finding_claimed_as_work_of_its_own_is_still_owed_by_its_owner(check, code):
    """Finding 12, owned by step 7, is claimed as work of its own (``card/12``, whose own
    marker records it): card 7 still owns it, so card/7 is refused until card/12 lands or 12
    is handed on (Josh's "neither marks fixed nor hands to another step")."""
    root, base = code
    run(root, "update-ref", "refs/remotes/origin/card/12", claim(root, base, 12)[1])
    github = Lookup(tracker(card_node(7), card_node(12, "finding", blocked_by=(7,))))
    status, out, _ = check(base, claim(root, base, 7)[1], "card/7", github=github)
    assert (status, out.splitlines()) == (1, [_owes(), verdict("card/7", 1, 1)])


def test_a_finding_shipped_from_its_own_branch_or_handed_on_is_owed_no_more(check, code):
    """Finding 12 landed from its own claim on main (a hotfix dev lacks yet): git says it
    shipped once card/7's change and main meet, so card/7 lands.  Handed on to step 8, it is
    no longer what card 7 blocks: card/7 lands, nothing of 12 read."""
    root, base = code
    point_main(root, landed(root, base, 12))
    github = Lookup(tracker(card_node(7), card_node(12, "finding", blocked_by=(7,))))
    assert check(base, claim(root, base, 7)[1], "card/7", github=github)[0] == 0
    handed = Lookup(tracker(card_node(7), card_node(8), card_node(12, "finding",
                                                                  blocked_by=(8,))))
    assert check(base, claim(root, base, 7)[1], "card/7", github=handed)[0] == 0
    assert handed.asked == [[7]]


def test_a_finding_its_card_and_another_block_names_no_one_owner_and_is_refused(check, code):
    """Finding 12 is blocked by card 7 and by step 8, so it names no one owner and no pull
    request owes it: card/7 is refused (fail closed), with how to give it one."""
    root, base = code
    github = Lookup(tracker(card_node(7), card_node(8),
                            card_node(12, "finding", blocked_by=(7, 8))))
    status, out, _ = check(base, claim(root, base, 7)[1], "card/7", github=github)
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/ships/12: card/7 records card 7, but open finding plan#12, which it "
        f"blocks, has no one owner, so no pull request owes it (R-BAL395, plan#1784): {_TWO}",
        verdict("card/7", 1, 1)])


def test_the_rulings_and_steps_a_card_blocks_are_owed_by_no_one(check, code):
    """Card 7 blocks open ruling 13 and step 14 (waiting on it): neither is a finding, so
    card/7 lands, and neither is read by its number."""
    root, base = code
    github = Lookup(tracker(card_node(7), card_node(13, "ruling", blocked_by=(7,)),
                            card_node(14, blocked_by=(7,))))
    assert check(base, claim(root, base, 7)[1], "card/7", github=github)[0] == 0
    assert github.asked == [[7]]


# --------------------------------------------- the backstop: a finding a split step above owns


@pytest.mark.parametrize("owner", [5, 4], ids=["its parent", "its parent's parent"])
def test_a_leaf_claim_is_refused_while_a_step_above_it_owns_an_open_finding(check, code,
                                                                           owner):
    """Step 4 splits into 5, which splits into 6; finding 12 is linked by hand to 5, or to 4,
    a step split into smaller steps, which no branch builds: card/6 is refused, naming the
    hand-off down to card 6."""
    root, base = code
    github = Lookup(tracker(card_node(4), card_node(5, parent=4), card_node(6, parent=5),
                            card_node(12, "finding", blocked_by=(owner,))))
    status, out, _ = check(base, claim(root, base, 6)[1], "card/6", github=github)
    assert (status, out.splitlines()) == (1, [
        f"REFUSED -- plan/ships/12: card/6 records card 6, but plan#{owner}, a step above it "
        "split into smaller steps, which no branch builds, owns open finding plan#12 (R-BAL395, "
        "plan#1784): hand it down first (quill hand plan#12 --to plan#6), or to the leaf that "
        "fixes it", verdict("card/6", 1, 1)])
    assert github.blocks_asked == [6, 5, 4]


def test_a_finding_handed_down_to_the_leaf_lands_with_its_fix(check, code):
    """Finding 12, handed down from split step 5 to its leaf 6, is owned by 6: card/6 lands
    with its marker beside the fix."""
    root, base = code
    github = Lookup(tracker(card_node(5), card_node(6, parent=5),
                            card_node(12, "finding", blocked_by=(6,))))
    status, out, _ = check(base, _fixed(root, claim(root, base, 6)[1], 12), "card/6",
                           github=github)
    assert (status, out) == (0, verdict("card/6", 2, 0) + "\n")


# ------------------------------------------------------------------ a revert reopens the finding


def test_a_revert_of_the_steps_merge_reopens_each_finding_it_marked(check, code):
    """Josh's "A revert of the step reopens them": card/7 lands card 7 with finding 12's
    marker, and sync would close both; a revert of its merge deletes both markers, which
    passes from any branch (rule 2) with no tracker read, and git then says neither shipped,
    so sync reopens both, each closed by the tool."""
    root, base = code
    github = Lookup(tracker(card_node(7), card_node(12, "finding", blocked_by=(7,))))
    tip = _fixed(root, claim(root, base, 7)[1], 12)
    assert check(base, tip, "card/7", github=github)[0] == 0
    point_dev(root, dev := merge(root, base, tip, "Merge card/7"))
    assert {7, 12} <= shipped(root).cards
    reverted = commit_files(root, dev, "Revert card/7", {
        marker(7): None, marker(12): None, "w7.txt": None, "f12.txt": None})
    assert check(dev, reverted, "revert-7")[:2] == (0, verdict("revert-7", 2, 0) + "\n")
    point_dev(root, merge(root, dev, reverted, "Merge revert-7"))
    assert not {7, 12} & shipped(root).cards
    cards = {node["number"]: card_from({**node, "projectItems": {"totalCount": 0, "nodes": []}},
                                       PLAN, "board", APP_LOGIN)
             for node in tracker(closed(card_node(7), "COMPLETED", APP_LOGIN),
                                 closed(card_node(12, "finding", blocked_by=(7,)), "COMPLETED",
                                        APP_LOGIN)).values()}
    assert sync_plan(cards, shipped(root).cards, {}).reopen == [7, 12]


@pytest.mark.parametrize("kind", ["step", "question"])
def test_another_cards_marker_that_is_no_finding_is_refused_as_before(check, code, kind):
    """card/7 writes the marker of card 8, a step or a question, no finding: refused by rule
    5's own sentence alone, as before rule 7 read it, and never again for what card 8 is."""
    root, base = code
    tip = commit_files(root, claim(root, base, 7)[1], "more", {marker(8): f"claimed {MON}\n"})
    status, out, _ = check(base, tip, "card/7",
                           github=Lookup(tracker(card_node(7), card_node(8, kind))))
    assert (status, out.splitlines()) == (1, [_foreign(number=8), verdict("card/7", 2, 1)])


def test_the_records_door_passes_another_cards_marker_from_a_claim_as_from_any_branch(check,
                                                                                     code):
    """card/7's change into main adds only markers: its card's and step 2's, which no commit
    of dev or main ever marked and no claim was made for.  Step 2 passes through the
    records-only door (R-BAL363) as from any branch, never read as a finding card 7 owns."""
    root, base = code
    records = commit_files(root, base, "records", {marker(7): "landed before records\n",
                                                   marker(2): "landed before records\n"})
    github = Lookup(tracker(card_node(2), card_node(7)))
    assert check(base, records, "card/7", base="main", github=github)[:2] == (
        0, verdict("card/7", 2, 0, "main") + "\n")


def test_a_step_the_records_door_records_is_refused_while_it_owns_an_open_finding(check, code):
    """A change into main through the records-only door (R-BAL363) adds the marker of step 2,
    which owns open finding 5: refused as a claim's card would be (plan#1784, every door keeps
    the rule), naming the marker the same change may add and the hand-off.  The same change
    adding finding 5's marker too, through the same door, lands."""
    root, base = code
    github = Lookup(tracker(card_node(2), card_node(5, "finding", blocked_by=(2,))))
    records = commit_files(root, base, "records", {marker(2): "landed before records\n"})
    status, out, _ = check(base, records, "records", base="main", github=github)
    assert (status, out.splitlines()) == (1, [
        "REFUSED -- plan/ships/5: records records card 2, but holds no marker for finding "
        "plan#5, which it owns (R-BAL395) and which is neither shipped nor dropped: write its "
        "marker in this change too, through the same door (R-BAL363), if the work it records "
        "fixed it, or hand it to the step that does (quill hand plan#5 --to plan#S)",
        verdict("records", 1, 1, "main")])
    both = commit_files(root, base, "both", {marker(2): "landed before records\n",
                                             marker(5): "landed before records\n"})
    assert check(base, both, "records", base="main", github=github)[:2] == (
        0, verdict("records", 2, 0, "main") + "\n")
