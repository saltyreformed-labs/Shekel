"""X-cx's migration plan (:mod:`_migrate_plan`): the writes still to make, and what it refuses,
on the small corpus (:mod:`_migrate_fake`) and the in-memory tracker (:mod:`_migrate_plan_fake`).
Which cards the tracker may hold besides the items' cards, and a card filed already (card #1)
graded a case per check, are :mod:`test__migrate_plan_cards`'s; five of card #1's tests stay
here: its links alone, the tracker lacking it, its milestone, its mark, its board place.

Each test builds a tracker state by hand -- empty, wholly filed, or a run cut short at some
write -- and reads what the plan says of it.  Executing the writes is X-cx's B2b; nothing
here writes.
"""
from __future__ import annotations

import random
from dataclasses import replace

import pytest

from tools.ci.arcs import REPO
from tools.ci.gitcmd import git
from tools.quill import _migrate_source
from tools.quill._fake import FakeTracker
from tools.quill._migrate_bodies import AS_FILED
from tools.quill._migrate_fake import COMMIT
from tools.quill._migrate_plan import (
    BLOCK,
    BOARD_ADD,
    CLOSE_UNMARKED,
    COMMENT,
    CREATE,
    LINK,
    MILESTONE,
    UNMARK,
    Write,
    card_key,
    desired_board,
    milestone_title,
    moves,
    plan,
)
from tools.quill._migrate_plan_fake import (
    MILESTONES,
    OUTCOME,
    corpus_items,
    corpus_plan,
    corpus_texts,
    filed_tracker,
    tracked,
    with_change,
)
from tools.quill._migrate_source import read_registries, read_source
from tools.quill._tracker import Comment, OutsideLink
from tools.quill.check import card_title
from tools.quill.setup_tracker import FILING


# -- what a run writes -----------------------------------------------------------

def test_an_empty_tracker_takes_every_write_in_the_order_draft_4_gives(corpus):
    """HISTORY: each ruling's create, then its close with its unmark.  The FREEZE: the
    milestone; the steps, each after its parent and blockers, the container A-2's unmark
    held to the end of the steps; the findings, each blocked by its owner step and under no
    card (R-BAL395); the questions, each with its As filed comment and its board place."""
    found = corpus_plan(corpus, FakeTracker())
    assert not found.refusals and found.numbers == {}
    assert found.history == (
        Write(CREATE, "balance:R-A"), Write(CLOSE_UNMARKED, "balance:R-A"),
        Write(CREATE, "salary:R-SAL1"), Write(CLOSE_UNMARKED, "salary:R-SAL1"))
    assert found.freeze == (
        Write(MILESTONE, OUTCOME),
        Write(CREATE, "balance:A-2"),
        Write(CREATE, "balance:A-2a"), Write(LINK, "balance:A-2a", "balance:A-2"),
        Write(BOARD_ADD, "balance:A-2a"), Write(UNMARK, "balance:A-2a"),
        Write(CREATE, "balance:A-2b"), Write(LINK, "balance:A-2b", "balance:A-2"),
        Write(BLOCK, "balance:A-2b", "balance:A-2a"), Write(BOARD_ADD, "balance:A-2b"),
        Write(UNMARK, "balance:A-2b"),
        Write(CREATE, "balance:A-3"), Write(BLOCK, "balance:A-3", "balance:A-2a"),
        Write(BOARD_ADD, "balance:A-3"), Write(UNMARK, "balance:A-3"),
        Write(CREATE, "salary:S-1"), Write(BOARD_ADD, "salary:S-1"), Write(UNMARK, "salary:S-1"),
        Write(UNMARK, "balance:A-2"),
        Write(CREATE, "balance:F-1"), Write(BLOCK, "balance:F-1", "balance:A-2a"),
        Write(UNMARK, "balance:F-1"),
        Write(CREATE, "balance:F-2"), Write(BLOCK, "balance:F-2", "balance:A-3"),
        Write(UNMARK, "balance:F-2"),
        Write(CREATE, "salary:N-391"), Write(BLOCK, "salary:N-391", "salary:S-1"),
        Write(UNMARK, "salary:N-391"),
        *(write for key in ("balance:Q-1", "balance:Q-2", "salary:N-391 question")
          for write in (Write(CREATE, key), Write(COMMENT, key), Write(BOARD_ADD, key),
                        Write(UNMARK, key))))


def test_a_wholly_filed_tracker_takes_no_write(corpus):
    """Every card filed, finished, linked and placed: nothing left, nothing refused."""
    tracker, numbers = filed_tracker(corpus)
    found = corpus_plan(corpus, tracker)
    assert (found.history, found.freeze, found.refusals) == ((), (), ())
    assert found.numbers == numbers


def test_a_cards_labels_read_in_another_order_are_the_same_labels(corpus):
    """A-2b's labels read back out of order (the in-memory tracker keeps them sorted; the
    order GitHub answers in is not the plan's to assume): the same labels, so nothing is
    refused or written (review 4 of B2b-1, LOW-3: the sort was unpinned)."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers["balance:A-2b"], labels=("moves-money", "balance"))
    found = corpus_plan(corpus, tracker)
    assert (found.history, found.freeze, found.refusals) == ((), (), ())


def test_a_steps_filing_cut_short_after_its_create_resumes_at_its_links(corpus):
    """A-2b created, still marked, with no link yet (a run stopped after the create's
    answer): its parent link, its blocker link, its board place and its unmark remain."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:A-2b",))
    with_change(tracker, numbers["balance:A-2b"], parent=None, blocked_by=())
    tracker.board.items.remove(numbers["balance:A-2b"])
    assert corpus_plan(corpus, tracker).freeze == (
        Write(LINK, "balance:A-2b", "balance:A-2"), Write(BLOCK, "balance:A-2b", "balance:A-2a"),
        Write(BOARD_ADD, "balance:A-2b"), Write(UNMARK, "balance:A-2b"))


def test_a_ruling_created_and_not_yet_closed_takes_its_one_last_write(corpus):
    """Open and marked, or closed and still marked: the one PATCH closing and unmarking it."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:R-A", "salary:R-SAL1"))
    with_change(tracker, numbers["balance:R-A"], is_open=True, state_reason=None)
    found = corpus_plan(corpus, tracker)
    assert found.history == (Write(CLOSE_UNMARKED, "balance:R-A"),
                             Write(CLOSE_UNMARKED, "salary:R-SAL1"))


def test_a_questions_as_filed_comment_is_found_by_reading_never_made_twice(corpus):
    """Q-1 marked: with the tool's As filed comment on it, only its unmark remains; with the
    same text from a person, or the tool's other comment, the comment is still to make."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:Q-1",))
    assert corpus_plan(corpus, tracker).freeze == (
        Write(UNMARK, "balance:Q-1"),)
    for comment in (Comment("a-person", "t", f"{AS_FILED}\n\nblock"),
                    Comment(tracker.app_login, "t", "another note")):
        tracker.notes[numbers["balance:Q-1"]] = [comment]
        assert corpus_plan(corpus, tracker).freeze == (
            Write(COMMENT, "balance:Q-1"), Write(UNMARK, "balance:Q-1"))


def test_a_container_is_unmarked_after_every_steps_writes(corpus):
    """A-2 still marked while its leaf A-2b is still to link: A-2's unmark comes after
    A-2b's writes and S-1's, never before its last leaf is linked (draft 4 s.4, R9)."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:A-2", "balance:A-2b", "salary:S-1"))
    with_change(tracker, numbers["balance:A-2b"], parent=None)
    assert corpus_plan(corpus, tracker).freeze == (
        Write(LINK, "balance:A-2b", "balance:A-2"), Write(UNMARK, "balance:A-2b"),
        Write(UNMARK, "salary:S-1"), Write(UNMARK, "balance:A-2"))


def test_a_milestone_is_found_by_its_title_and_made_only_when_missing(corpus):
    """With no milestone the freeze opens by making it; the scope step A-3 then has no card
    either, so it is filed in it by its create."""
    tracker, _ = filed_tracker(corpus, milestone=False, skip=("balance:A-3", "balance:F-2"))
    freeze = corpus_plan(corpus, tracker).freeze
    assert freeze[0] == Write(MILESTONE, OUTCOME) and Write(CREATE, "balance:A-3") in freeze


def test_a_card_filed_already_takes_its_links_alone(corpus, monkeypatch):
    """S-1 mapped onto its card (as X-cx is card #1), off the board as a container is, not yet
    blocked by A-2a: its block, and no board place, comment or unmark -- its title and text
    are not graded."""
    tracker, numbers = filed_tracker(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    items = [item if item.key != "salary:S-1" else _blocked(item, "balance:A-2a")
             for item in corpus_items(corpus)]
    with_change(tracker, numbers["salary:S-1"], title="Paycheck, filed long ago")
    tracker.board.items.remove(numbers["salary:S-1"])
    found = corpus_plan(corpus, tracker, items)
    assert (found.freeze, found.refusals) == ((Write(BLOCK, "salary:S-1", "balance:A-2a"),), ())


def test_a_card_filed_already_writes_exactly_its_missing_links(corpus, monkeypatch):
    """A-2b mapped onto its card, which holds neither its parent link nor its one blocker
    link, and is off the board as a container is: exactly those two writes, the parent
    link first -- no create, board place, comment or unmark (moved here with B2b-2, from
    ``test_migrate``'s test of ``migrate.writes``, the estimate's second producer, deleted;
    review of B1's fixes, L2a)."""
    tracker, numbers = filed_tracker(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:A-2b", numbers["balance:A-2b"])
    with_change(tracker, numbers["balance:A-2b"], parent=None, blocked_by=())
    tracker.board.items.remove(numbers["balance:A-2b"])
    found = corpus_plan(corpus, tracker)
    assert (found.history, found.freeze, found.refusals) == (
        (), (Write(LINK, "balance:A-2b", "balance:A-2"),
             Write(BLOCK, "balance:A-2b", "balance:A-2a")), ())


def _blocked(item, key):
    """``item`` blocked by ``key`` too."""
    return _migrate_source.Item(item.arc, item.alias, item.kind, item.row, item.labels,
                                _migrate_source.Links(item.links.parent,
                                                      (*item.links.blockers, key),
                                                      item.links.place), item.outcome)


# -- what a run refuses ----------------------------------------------------------

def test_a_card_filed_already_that_the_tracker_lacks_is_refused_never_filed_again(
        corpus, monkeypatch):
    """S-1 mapped onto #99, which the tracker does not hold: nothing is filed for it."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 99)
    found = corpus_plan(corpus, FakeTracker())
    assert ("salary:S-1 is plan#99, which the tracker does not hold: nothing is filed in its "
            "place") in found.refusals
    assert not [write for write in found.freeze if write.key == "salary:S-1"]


@pytest.mark.parametrize("key, change, reason", [
    ("balance:F-1", {"kind": "step"}, "it is a step, not a finding"),
    ("balance:F-1", {"labels": ("balance", "moves-money")},
     "its labels are ['balance', 'moves-money'], not ['balance']"),
    ("balance:F-1", {"is_open": False, "labels": ("balance", FILING)},
     "its filing ended (dropped while marked, or a ruling withdrawn)"),
    ("balance:R-A", {"state_reason": "NOT_PLANNED"}, "its filing ended"),
    ("balance:F-1", {"outside": (OutsideLink("parent", "o/code#5"),)},
     "it is linked outside the tracker (o/code#5)"),
    ("balance:R-A", {"is_open": True}, "an open ruling whose filing finished"),
    ("balance:F-2", {"is_open": False}, "it is closed, and a finding moves open"),
    ("balance:F-1", {"parent": 5}, "it is a sub-issue of plan#5, not none"),
    ("balance:F-1", {"blocked_by": (3, 5)},
     "it is blocked by plan#5, which the registries do not record"),
    ("balance:A-3", {"blocked_by": (3, 6)},
     "it is blocked by plan#6, which the registries do not record"),
], ids=["kind", "labels", "dropped-marked", "ruling-withdrawn", "outside", "ruling-open",
        "closed", "rehomed", "re-owned", "extra-blocker"])
def test_an_items_card_unlike_it_is_refused_and_left_alone(corpus, key, change, reason):
    """Each way an item's card differs from the item that no write repairs: refused, naming
    the card, and nothing written to it.  A finding is blocked by its owner and under no card
    (R-BAL395): any parent, and any blocker but its owner, is refused."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers[key], **change)
    found = corpus_plan(corpus, tracker)
    assert len(found.refusals) == 1 and found.refusals[0].startswith(
        f"{key} (plan#{numbers[key]}): {reason}"), found.refusals
    assert not [write for write in (*found.history, *found.freeze) if write.key == key]


def test_an_outcome_the_milestones_lack_fails_loud(corpus):
    """An item whose outcome the input file's milestones lack: :func:`milestone_title`
    raises, never files its card in no milestone (review 2 of B2b-1, LOW-C).  ``grade``
    refuses such a file, so only a caller that skips it reaches this."""
    scoped = next(item for item in corpus_items(corpus) if item.outcome is not None)
    with pytest.raises(KeyError):
        milestone_title(scoped, {})


def test_a_card_in_another_milestone_or_none_is_refused(corpus):
    """A-3's card out of its outcome's milestone, or in another: a card's milestone is set
    only by its create, so no write repairs it."""
    tracker, numbers = filed_tracker(corpus)
    for held in (None, 9):
        tracker.milestone_of.pop(numbers["balance:A-3"], None)
        if held is not None:
            tracker.milestone_of[numbers["balance:A-3"]] = held
        assert corpus_plan(corpus, tracker).refusals == (
            f"balance:A-3 (plan#{numbers['balance:A-3']}): it is in milestone {held}, not 1; a "
            "card's milestone is set only by its create",)


def test_a_finished_filing_lacking_a_write_is_refused(corpus):
    """A-3 unmarked, but off the board and unblocked: a person changed it after its filing
    finished, so the migration does not redo it over them -- or a read lags the run's own
    writes, showing the mark's removal and not the writes before it, which the refusal names
    too (review 4 of B2b-1, LOW-4: the executor opens that window)."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers["balance:A-3"], blocked_by=())
    tracker.board.items.remove(numbers["balance:A-3"])
    assert corpus_plan(corpus, tracker).refusals == (
        f"balance:A-3 (plan#{numbers['balance:A-3']}): its filing finished, yet it lacks "
        "block, board-add: a person changed it, or this read lags the migration's own writes; "
        "run again later, and if it is refused still, settle it by hand",)


def test_steps_waiting_in_a_cycle_are_refused(corpus):
    """A-2a blocked by A-3, which is blocked by A-2a: neither can be filed first, nor A-2b,
    which waits on A-2a (review of B2a, L7: the refusal names the cycle and what waits on
    it)."""
    items = [_blocked(item, "balance:A-3") if item.key == "balance:A-2a" else item
             for item in corpus_items(corpus)]
    found = corpus_plan(corpus, FakeTracker(), items)
    assert found.refusals == (
        "steps wait in a cycle, so these can be filed in no order (the cycle and every step "
        "waiting on it): ['balance:A-2a', 'balance:A-2b', 'balance:A-3']",)


# -- a card's key -----------------------------------------------------------------

def test_a_cards_key_is_its_arc_label_and_title_alias_or_its_mapped_number(monkeypatch):
    """A migrated card by its one arc label and alias; a card filed already by its number,
    whatever its title; none for a title with no alias or a card with two arc labels."""
    tracker = FakeTracker()
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:X-cx", 1)
    tracker.add(1, title="Move the plan", labels=("balance",))
    tracker.add(2, title="[N-391 question] Re-read the stub", labels=("salary", FILING))
    tracker.add(3, title="Move the plan", labels=("balance",))
    tracker.add(4, title="[A-1] t", labels=("balance", "salary"))
    cards = tracker.all_cards()
    assert [card_key(number, cards[number]) for number in (1, 2, 3, 4)] == [
        "balance:X-cx", "salary:N-391 question", None, None]


# -- the board ----------------------------------------------------------------------

def test_the_desired_board_is_the_steps_by_rank_then_the_questions_in_ledger_order(corpus):
    """A-2a, A-2b, A-3, S-1, then Q-1, Q-2 and N-391's question; nothing refused."""
    tracker, numbers = filed_tracker(corpus)
    desired, refusals = desired_board(corpus_items(corpus), numbers, tracked(tracker))
    assert desired == [numbers[key] for key in (
        "balance:A-2a", "balance:A-2b", "balance:A-3", "salary:S-1", "balance:Q-1",
        "balance:Q-2", "salary:N-391 question")] and refusals == []


def test_a_card_filed_already_holds_its_rank_with_the_cards_below_it_in_their_order(
        corpus, monkeypatch):
    """S-1 (rank 4) mapped onto #50, a container: at its rank stand the board cards below it
    (#52 under #51 under #50, and #53), in the order the board holds them, never #50."""
    tracker, numbers = filed_tracker(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 50)
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.add(50, on_board=False)
    tracker.add(51, parent=50, on_board=False)
    tracker.add(52, parent=51)
    tracker.add(53, parent=50)
    tracker.board.items[:0] = [tracker.board.items.pop()]  # #53 first, #52 after the others
    numbers["salary:S-1"] = 50
    desired, refusals = desired_board(corpus_items(corpus), numbers, tracked(tracker))
    assert desired[3:5] == [53, 52] and 50 not in desired and refusals == []


def test_a_board_card_with_no_place_and_an_item_with_no_card_are_refused(corpus):
    """A finding put on the board by hand has no place in the desired order; a question with
    no card yet cannot be placed."""
    tracker, numbers = filed_tracker(corpus, skip=("balance:Q-2",))
    tracker.board.items.append(numbers["balance:F-1"])
    refusals = desired_board(corpus_items(corpus), numbers, tracked(tracker))[1]
    assert refusals == [
        f"plan#{numbers['balance:F-1']} is on the board, and the desired order has no place "
        "for it: settle it before the run",
        "balance:Q-2 has a place on the board and no card yet: file it before the board is "
        "ordered"]


def _apply(order, planned):
    """``order`` after each move of ``planned``, as the board makes it."""
    order = list(order)
    for number, after in planned:
        order.remove(number)
        order.insert(0 if after is None else order.index(after) + 1, number)
    return order


def test_moves_take_the_board_to_its_desired_order_and_a_second_run_moves_nothing():
    """Two cards swapped and one dragged to the bottom: only the cards outside the longest
    common subsequence move; once there, nothing moves."""
    current, desired = [1, 3, 2, 4, 5, 6, 0], [0, 1, 2, 3, 4, 5, 6]
    planned = moves(current, desired)
    assert len(planned) == 2 and _apply(current, planned) == desired
    assert moves(desired, desired) == []


def test_moves_reach_any_desired_order_with_the_fewest_cards_moved():
    """Over 500 random orders of 30 cards (seed 20261009): each reached, each moving exactly
    the cards outside a longest common subsequence (counted independently)."""
    shuffle = random.Random(20261009)
    for _ in range(500):
        desired = list(range(30))
        current = desired[:]
        shuffle.shuffle(current)
        planned = moves(current, desired)
        assert _apply(current, planned) == desired
        assert len(planned) == 30 - _longest_increasing(current)


def _longest_increasing(order):
    """The length of the longest increasing subsequence of ``order``: the longest common
    subsequence with the sorted order, counted another way (patience sorting)."""
    tops = []
    for value in order:
        index = next((i for i, top in enumerate(tops) if top >= value), len(tops))
        tops[index:index + 1] = [value]
    return len(tops)


def test_moves_refuse_orders_of_different_cards():
    """The board read must show every card the desired order holds, and no other."""
    with pytest.raises(ValueError):
        moves([1, 2], [1, 2, 3])


# -- review of B2a: the order on the registries' real shapes (M1) -----------------

def _early(writes):
    """Each LINK and BLOCK that names a card whose CREATE has not come before it (a card
    filed already needs none)."""
    created, early = set(), []
    for write in writes:
        if write.what == CREATE:
            created.add(write.key)
        elif write.what in (LINK, BLOCK) and not all(
                key in created or key in _migrate_source.MAPPED
                for key in (write.key, write.other)):
            early.append(write)
    return early


def test_a_container_listed_after_its_leaves_and_a_blocker_of_another_arc_come_first(corpus):
    """``steps.md`` lists its containers after their leaves, and a step may wait on a container
    of another arc listed later (review of B2a, M1: 63 and 17 such edges live).  S-1 blocked
    by A-2, with A-2 listed last: A-2 is filed first, and every LINK and BLOCK follows the
    CREATEs it names; the board still stands by rank."""
    items = {item.key: item for item in corpus_items(corpus)}
    items["salary:S-1"] = _blocked(items["salary:S-1"], "balance:A-2")
    steps = [items[key] for key in ("salary:S-1", "balance:A-2a", "balance:A-2b", "balance:A-3",
                                    "balance:A-2")]
    reordered = steps + [item for key, item in items.items() if item.kind != "step"]
    freeze = corpus_plan(corpus, FakeTracker(), reordered).freeze
    assert freeze[1] == Write(CREATE, "balance:A-2") and not _early(freeze)
    tracker, numbers = filed_tracker(corpus)
    assert desired_board(reordered, numbers, tracked(tracker))[0] == [numbers[key] for key in (
        "balance:A-2a", "balance:A-2b", "balance:A-3", "salary:S-1", "balance:Q-1",
        "balance:Q-2", "salary:N-391 question")]


def test_on_the_live_registries_every_link_follows_the_creates_it_names():
    """The registries as this checkout's HEAD holds them, on a tracker holding only card #1
    (X-cx): nothing refused, and every LINK and BLOCK follows the CREATE of each card it
    names (review of B2a, M1)."""
    source = read_source(read_registries(REPO, git(REPO, "rev-parse", "HEAD").strip()))
    tracker = FakeTracker()
    tracker.add(1, title="[X-cx] Move the plan", labels=("balance",), on_board=False)
    found = plan(source.items, {}, {}, tracked(tracker))
    assert not found.refusals and found.freeze and not _early(found.freeze)


# -- review of B2a: a plan that refuses anything writes nothing (M2) ---------------

def test_a_finished_leaf_left_unlinked_keeps_its_marked_container_from_being_unmarked(corpus):
    """A-2b's filing finished yet unlinked (refused), A-2 still marked: no unmark of A-2,
    nor any other write (review of B2a, M2, probe C)."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:A-2",))
    with_change(tracker, numbers["balance:A-2b"], parent=None)
    found = corpus_plan(corpus, tracker)
    assert found.refusals and (found.history, found.freeze) == ((), ())


def test_a_cycle_files_no_container_and_links_no_finding(corpus):
    """A-2a and A-2b blocked by each other: no create of A-2 left unmarked over no leaf, and
    no F-1 blocked by a card nobody files (probe D)."""
    items = [_blocked(item, "balance:A-2b") if item.key == "balance:A-2a" else item
             for item in corpus_items(corpus)]
    found = corpus_plan(corpus, FakeTracker(), items)
    assert found.refusals and (found.history, found.freeze) == ((), ())


def test_a_refused_step_takes_no_finding_under_it(corpus):
    """A-2a's card of another kind (refused), F-1 not yet filed: F-1 is not blocked by it,
    its owner (probe G; R-BAL395)."""
    tracker, numbers = filed_tracker(corpus, skip=("balance:F-1",))
    with_change(tracker, numbers["balance:A-2a"], kind="question")
    found = corpus_plan(corpus, tracker)
    assert found.refusals and (found.history, found.freeze) == ((), ())


# -- review of B2a: a card that says something else (M4) ---------------------------

@pytest.mark.parametrize("key, part", [
    ("balance:A-3", "title"), ("balance:A-3", "body"), ("balance:R-A", "body"),
    ("balance:Q-1", "comment"),
], ids=["step-title", "step-body", "ruling-body", "question-comment"])
def test_a_stale_card_is_refused_never_finished(corpus, key, part):
    """Each card still to finish whose title, body or As filed comment is not what the
    registries make it now: refused, so no unmark (or a ruling's close) finishes it (review
    of B2a, M4)."""
    tracker, numbers = filed_tracker(corpus, marked=(key,))
    number = numbers[key]
    if part == "title":
        with_change(tracker, number, title=card_title("A-3", "An old name"))
    elif part == "body":
        tracker.bodies[number] = tracker.bodies[number].replace(".", ";", 1)
    else:
        held = tracker.notes[number][0]
        tracker.notes[number] = [replace(held, body=held.body + "\nan old cell")]
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (f"{key} (plan#{number}): it differs in its {part} from what the "
                              "registries make it at this commit: a stale card is never "
                              "finished; settle it by hand, giving it that text, its As filed "
                              f"block naming this commit ({COMMIT})",)


def test_a_card_written_at_another_commit_is_not_stale(corpus):
    """The As filed block's commit is the one the card was written at; a run reading the
    same text at a later commit finishes it (draft 4 s.5: the commit line is not compared)."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:A-3",))
    number = numbers["balance:A-3"]
    tracker.bodies[number] = tracker.bodies[number].replace(COMMIT, "f" * 40)
    assert corpus_plan(corpus, tracker).freeze == (Write(UNMARK, "balance:A-3"),)


def test_a_doubled_as_filed_comment_is_refused(corpus):
    """Two of the tool's As filed comments on Q-1: the tool comments one."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:Q-1",))
    tracker.notes[numbers["balance:Q-1"]] *= 2
    assert corpus_plan(corpus, tracker).refusals == (
        f"balance:Q-1 (plan#{numbers['balance:Q-1']}): it holds 2 As filed comments; the tool "
        "comments one",)


def test_a_tool_comment_quoting_a_block_is_not_an_as_filed_comment(corpus):
    """``quill comment`` posts as the App: a comment of the tool's on Q-1 that QUOTES a card's
    text but does not open as an As filed comment is not a second As filed comment (review
    of B2a's fixes, L2)."""
    texts = corpus_texts(corpus)
    tracker, numbers = filed_tracker(corpus)
    tracker.notes[numbers["balance:Q-1"]].append(
        Comment(FakeTracker.app_login, "t", "A note quoting:\n\n" + texts["balance:Q-1"].comment))
    found = corpus_plan(corpus, tracker)
    assert (found.refusals, found.history, found.freeze) == ((), (), ())


# -- review of B2a: the lows ------------------------------------------------------

def test_a_milestone_renamed_on_the_web_is_refused_not_made_again(corpus):
    """The input's milestone renamed: refused, and the input's title not made beside it
    (review of B2a, L2)."""
    tracker, _ = filed_tracker(corpus, skip=("balance:A-3", "balance:F-2"))
    tracker.milestones_by_number[1] = replace(tracker.milestones_by_number[1], title="Renamed")
    found = corpus_plan(corpus, tracker)
    assert found.refusals == ("milestone #1 'Renamed' is none the input file names (renamed on "
                              "the web?): settle it before the run",) and not found.freeze


def _milestone_gone(number):
    """How the plan refuses A-3's card ``number`` when its outcome's milestone, 'The first',
    is not on the tracker by its title."""
    return (f"balance:A-3 (plan#{number}): its milestone 'The first' is not on the tracker, "
            "though the migration makes a milestone before any card in it: deleted or renamed "
            "on the web, or this read lags its create? One made again would hold none of its "
            "cards (a card's milestone is set only by its create); run again later, and if it "
            "is refused still, settle it by hand")


def test_a_milestone_deleted_on_the_web_is_refused_never_made_again(corpus):
    """The outcome's milestone deleted after A-3 was filed in it (GitHub clears it from the
    card): refused, never made again holding none of its cards, which the next run would
    refuse after the write (review of PR #549, L1)."""
    tracker, numbers = filed_tracker(corpus)
    tracker.milestones_by_number.pop(1)
    tracker.milestone_of.pop(numbers["balance:A-3"])
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        _milestone_gone(numbers["balance:A-3"]),)
    assert (found.history, found.freeze) == ((), ())


def test_a_milestone_renamed_on_the_web_holding_a_card_is_refused_once(corpus):
    """The input's milestone renamed with A-3's card in it: refused once, as renamed -- A-3
    is not refused again for it, nor called deleted (review of the #549 fix, LOW-3)."""
    tracker, _ = filed_tracker(corpus)
    tracker.milestones_by_number[1] = replace(tracker.milestones_by_number[1], title="Renamed")
    found = corpus_plan(corpus, tracker)
    assert found.refusals == ("milestone #1 'Renamed' is none the input file names (renamed on "
                              "the web?): settle it before the run",)
    assert (found.history, found.freeze) == ((), ())


def test_a_card_in_another_milestone_whose_own_is_gone_is_refused_for_the_one_gone(corpus):
    """A-3's card in the second outcome's milestone, its own outcome's milestone deleted:
    refused for its own being gone, which is true whatever milestone the card is in (review
    of the #549 fix, LOW-3; its second review, LOW-1)."""
    tracker, numbers = filed_tracker(corpus)
    second = tracker.create_milestone("The second", "The second outcome.")
    tracker.milestones_by_number.pop(1)
    tracker.milestone_of[numbers["balance:A-3"]] = second
    found = plan(corpus_items(corpus), corpus_texts(corpus),
                 {**MILESTONES, "O2 the second": ("The second", "The second outcome.")},
                 tracked(tracker))
    assert found.refusals == (
        _milestone_gone(numbers["balance:A-3"]),)


@pytest.mark.parametrize("moved, held", [(True, 2), (False, None)],
                         ids=["card-in-it", "card-in-none"])
def test_a_stray_milestone_refuses_no_card_that_does_not_follow_from_it(corpus, moved, held):
    """A milestone the input names none of, beside the outcome's own: refused, and A-3's
    card, moved into it or out of every milestone, is refused besides -- its own milestone
    is there, so its refusal does not follow from the stray one (second review of the #549
    fix, LOW-2)."""
    tracker, numbers = filed_tracker(corpus)
    stray = tracker.create_milestone("Stray", "Made by hand.")
    tracker.milestone_of.pop(numbers["balance:A-3"])
    if moved:
        tracker.milestone_of[numbers["balance:A-3"]] = stray
    assert corpus_plan(corpus, tracker).refusals == (
        f"milestone #{stray} 'Stray' is none the input file names (renamed on the web?): "
        "settle it before the run",
        f"balance:A-3 (plan#{numbers['balance:A-3']}): it is in milestone {held}, not 1; a "
        "card's milestone is set only by its create")


def test_a_card_of_no_outcome_in_a_stray_milestone_is_refused_besides_it(corpus):
    """A-2b, in no outcome's scope, moved into a milestone the input names none of: refused
    besides that milestone, as in a milestone at all -- nothing of its follows from the stray
    one (third review of the #549 fix, LOW-3)."""
    tracker, numbers = filed_tracker(corpus)
    stray = tracker.create_milestone("Stray", "Made by hand.")
    tracker.milestone_of[numbers["balance:A-2b"]] = stray
    assert corpus_plan(corpus, tracker).refusals == (
        f"milestone #{stray} 'Stray' is none the input file names (renamed on the web?): "
        "settle it before the run",
        f"balance:A-2b (plan#{numbers['balance:A-2b']}): it is in milestone {stray}, not None; "
        "a card's milestone is set only by its create")


def test_a_card_moved_below_a_card_filed_already_is_refused_once(corpus, monkeypatch):
    """A-3's card moved below the card S-1 is mapped onto, where the board would hold it
    twice (review of B2a, L3): refused once, as re-homed, and never ordered."""
    tracker, numbers = filed_tracker(corpus)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    with_change(tracker, numbers["balance:A-3"], parent=numbers["salary:S-1"])
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert found.refusals == (
        f"balance:A-3 (plan#{numbers['balance:A-3']}): it is a sub-issue of "
        f"plan#{numbers['salary:S-1']}, not none: re-homing is a person's call",)
    with pytest.raises(ValueError):
        moves([1, 2], [1, 2, 2])


def test_an_alias_no_title_gives_back_is_refused(corpus):
    """An alias holding ``] ``, which a title would cut short (review of B2a, L4)."""
    items = [replace(item, alias="Z] z") if item.key == "salary:S-1" else item
             for item in corpus_items(corpus)]
    found = plan(items, corpus_texts(corpus), MILESTONES, tracked(FakeTracker()))
    assert ("salary:Z] z: its alias does not read back from the title it would carry, so no "
            "run could find its card") in found.refusals


def test_a_card_filed_already_is_not_graded_on_its_milestone(corpus, monkeypatch):
    """A-3, in the outcome's scope, mapped onto its card, which holds no milestone: not
    refused, and nothing to write (review of B2a, L4)."""
    tracker, numbers = filed_tracker(corpus)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "balance:A-3", numbers["balance:A-3"])
    tracker.milestone_of.pop(numbers["balance:A-3"])
    tracker.board.items.remove(numbers["balance:A-3"])
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert (found.refusals, found.freeze) == ((), ())


def test_a_card_filed_already_still_marked_is_refused(corpus, monkeypatch):
    """Its own filing unfinished: the migration does not write links onto it (delta review
    of B2a, LOW-4)."""
    tracker, numbers = filed_tracker(corpus)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    with_change(tracker, numbers["salary:S-1"], labels=("salary", FILING))
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert found.refusals == (
        f"salary:S-1 (plan#{numbers['salary:S-1']}): a card filed already still carrying the "
        "filing mark: its own filing is unfinished; finish it with `quill file` before the run",)


# -- delta review of B2a: the board's shape is refused before any write ------------

def test_a_board_card_with_no_place_refuses_the_plan_before_any_write(corpus):
    """The container A-2's card put on the board by hand, the corpus half filed: the plan
    refuses, and writes nothing (delta review of B2a, MEDIUM-1)."""
    tracker, numbers = filed_tracker(corpus, skip=("balance:A-3", "balance:F-2"))
    tracker.board.items.append(numbers["balance:A-2"])
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        f"plan#{numbers['balance:A-2']} is on the board, and the desired order has no place "
        "for it: settle it before the run",) and not found.freeze


def test_a_step_the_registries_put_below_a_card_filed_already_is_refused(corpus, monkeypatch):
    """A-3 under S-1 in the registries, S-1 filed already: X-cx's leaves are its sub-issues,
    never steps.md rows (its row says so), so no step is filed below it -- which also keeps a
    ranked one from standing twice on the board (delta review of B2a, MEDIUM-1)."""
    tracker, numbers = filed_tracker(corpus, skip=("balance:A-3", "balance:F-2"))
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    items = [replace(item, links=replace(item.links, parent="salary:S-1"))
             if item.key == "balance:A-3" else item for item in corpus_items(corpus)]
    found = plan(items, texts, MILESTONES, tracked(tracker))
    assert found.refusals == (
        "balance:A-3 is a step below a card filed already in the registries: that card's "
        "leaves are its own sub-issues, never steps.md rows, so no step is filed below it; "
        "re-point the row before the migration",) and not found.freeze


# -- delta review of B2a: the stale card, every kind and state (LOW-2) ---------------

@pytest.mark.parametrize("key", ["balance:A-3", "balance:F-1", "balance:R-A", "balance:Q-1"])
def test_a_finished_card_gone_stale_is_refused_whatever_its_kind(corpus, key):
    """Its filing finished, its body changed since (for a question, the developer's own
    question, which ``quill file ruling --from-question`` takes word for word)."""
    tracker, numbers = filed_tracker(corpus)
    number = numbers[key]
    tracker.bodies[number] = tracker.bodies[number] + " (amended)"
    assert corpus_plan(corpus, tracker).refusals == (
        f"{key} (plan#{number}): it differs in its body from what the registries make it at "
        "this commit: a stale card is never finished; settle it by hand, giving it that text, "
        f"its As filed block naming this commit ({COMMIT})",)


def test_a_body_github_stored_with_crlf_and_a_trailing_newline_is_not_stale(corpus):
    """What a web form or GitHub's storage may add -- CRLF, a trailing newline -- is no
    difference (:func:`check.normalized`)."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:A-3",))
    number = numbers["balance:A-3"]
    tracker.bodies[number] = tracker.bodies[number].replace("\n", "\r\n") + "\r\n"
    assert corpus_plan(corpus, tracker).freeze == (Write(UNMARK, "balance:A-3"),)


# -- third review of B2a ---------------------------------------------------------


def test_a_card_refused_on_the_board_is_refused_once(corpus):
    """Q-1 retitled while on the board, and F-1 on two cards both on the board: one refusal
    each, never also "no place" for what follows from it (third review of B2a, L3)."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers["balance:Q-1"], title="Retitled")
    tracker.add(40, "finding", "dup", title=card_title("F-1", "Stale row"),
                labels=("balance",), on_board=False)
    tracker.board.items += [numbers["balance:F-1"], 40]
    found = corpus_plan(corpus, tracker)
    assert len(found.refusals) == 2 and not [refusal for refusal in found.refusals
                                             if "no place" in refusal], found.refusals


def test_a_finding_below_a_card_filed_already_put_on_the_board_is_refused(corpus,
                                                                           monkeypatch):
    """N-391's finding card, owned by S-1 mapped (blocked by it, R-BAL395), put on the board
    by hand: it is an item's card with no place, never one of X-cx's own to stand at S-1's
    rank (third review of B2a, L4)."""
    tracker, numbers = filed_tracker(corpus)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.board.items.append(numbers["salary:N-391"])
    assert plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals == (
        f"plan#{numbers['salary:N-391']} is on the board, and the desired order has no place "
        "for it: settle it before the run",)


def test_a_card_filed_already_on_the_board_is_refused(corpus, monkeypatch):
    """A container is never on the board (R-BAL179); S-1 mapped and left on it is refused
    rather than handed to :func:`moves` (third review of B2a, L5: M5)."""
    tracker, numbers = filed_tracker(corpus)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    assert plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals == (
        f"plan#{numbers['salary:S-1']} is on the board, and the desired order has no place "
        "for it: settle it before the run",)


def test_a_step_two_levels_below_a_card_filed_already_in_the_registries_is_refused(
        corpus, monkeypatch):
    """A-2 under S-1 mapped: A-2a and A-2b sit two levels below it, each refused too (third
    review of B2a, L5: M11)."""
    tracker = FakeTracker()
    tracker.create_milestone(*MILESTONES[OUTCOME])
    tracker.add(1, "step", title="[S-1] filed", labels=("salary",), on_board=False)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    items = [replace(item, links=replace(item.links, parent="salary:S-1"))
             if item.key == "balance:A-2" else item for item in corpus_items(corpus)]
    refused = [refusal.split(" ")[0] for refusal in
               plan(items, texts, MILESTONES, tracked(tracker)).refusals]
    assert refused == ["balance:A-2", "balance:A-2a", "balance:A-2b"]
