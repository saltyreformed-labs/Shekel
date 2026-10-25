"""X-cx's migration plan (:mod:`_migrate_plan`): which cards the tracker may hold besides
the items' cards -- the cards filed already, and the tracker's own below them, told by their
NUMBER -- and a card filed already (card #1) graded a case per check, on the small corpus
(:mod:`_migrate_fake`) and the in-memory tracker (:mod:`_migrate_plan_fake`); five more of
card #1's tests are :mod:`test__migrate_plan`'s.  Nothing here writes.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tools.quill import _migrate_source
from tools.quill._fake import FakeTracker
from tools.quill._migrate_plan import CREATE, LINK, Write, plan
from tools.quill._migrate_plan_fake import (
    MILESTONES,
    corpus_items,
    corpus_plan,
    corpus_texts,
    filed_tracker,
    tracked,
    with_change,
)
from tools.quill._tracker import Comment, Milestone, OutsideLink
from tools.quill.check import card_title
from tools.quill.setup_tracker import FILING


def _rekeyed(number, title, first=2):
    """How the plan refuses card ``number``, titled ``title``, that no item claims, numbered
    after ``first``, the migration's first card (A-2's, #2 in
    :func:`_migrate_plan_fake.filed_tracker`'s numbering)."""
    return (f"plan#{number} ({title!r}) is no item's card, and it is numbered above "
            f"plan#{first}, the lowest card an item's key names (a card filed already apart), so "
            f"it reads as the migration's.  FIRST, if plan#{first} is a card of the tracker's own "
            "given an item's key by hand (refused besides), settle that one and run again: this "
            "card is not to be deleted for it.  Else it is the migration's card with its title or "
            "arc label changed by hand, or its item left the registries, or it was filed natively "
            "inside the freeze; settle it before the run")


def _resent(key, *states):
    """How the plan refuses ``key`` on cards in the state a re-sent create leaves, each named
    with what it holds (``states``, lowest first, each opening with its card's number): the
    last card, the one the run went on with, named to keep."""
    keep = states[-1].split(" ", 1)[0]
    return (f"{key} is the key of {len(states)} cards ({'; '.join(states)}): the state a "
            "re-sent create leaves (GitHub refused a create for a rate limit though it had "
            "performed it, and the tool sent it again), copies of one create, each but the last "
            f"made as its create left it.  Keep the last made ({keep}), the one the run went on "
            "with; delete the others on the web, and run again: the next run grades it as the "
            "item's card")


def _by_hand(key, *states):
    """How the plan refuses ``key`` on cards in a state no re-sent create leaves, each named
    with what it holds (``states``, lowest first)."""
    return (f"{key} is the key of {len(states)} cards ({'; '.join(states)}), not the state a "
            "re-sent create leaves (copies of one create, each but the last made as its create "
            "left it, and the last refused nothing as the item's card): a person gave a card "
            "this key by hand (retitled it, or changed its arc label), edited one of them, or "
            "changed what the last is graded against (such as a card it is linked to, that "
            "card's key given to another card, or its milestone), or this read lags the "
            "migration's own writes.  Run again later, and if it is refused still, settle them "
            "by hand and run again: retitle or relabel a card given this key to its own title "
            "and arc label, never deleting it (above all one of the tracker's own, filed before "
            "the migration), and delete no card holding a later write")


def _filed_already(key, mapped, *states):
    """How the plan refuses ``key``, a card filed already's, on card ``mapped`` and others,
    each named with what it holds (``states``, lowest first)."""
    return (f"{key} is the key of {len(states)} cards ({'; '.join(states)}): the migration "
            f"files no card for a card filed already (plan#{mapped}), so every other card "
            "holding its key was given it by hand (retitled, or its arc label changed); retitle "
            "or relabel each to its own title and arc label, delete none, and run again")


def _outside(number, title):
    """How the plan refuses card ``number``, titled ``title``, that no item claims, numbered
    lower than every card an item's key names and under no card filed already."""
    return (f"plan#{number} ({title!r}) is no item's card, numbered lower than every card an "
            "item's key names (a card filed already apart) and under no card filed already: a "
            "card filed outside X-cx before the migration, an X-cx leaf unlinked from a card "
            "filed already by hand (a finding or a ruling: its owner link removed; block it by "
            "its owner again, R-BAL395), or one of the migration's first cards with its title "
            "or arc label changed by hand or its item gone from the registries; settle it "
            "before the run")


# -- a card whose key was changed by hand (review of B2a, M3; its delta review) ----

@pytest.mark.parametrize("key, change", [
    ("balance:Q-1", {"labels": ("balance", "salary")}),
    ("balance:R-A", {"title": "Here"}),
    ("balance:A-2b", {"labels": ("balance", "moves-money", "recurrence")}),
], ids=["question-two-arcs", "ruling-retitled", "step-third-arc"])
def test_a_migrated_card_whose_key_was_changed_by_hand_is_refused_never_filed_again(
        corpus, key, change):
    """Its key lost: numbered after the migration's first card, it is refused, and its item
    is not filed again beside it (review of B2a, M3)."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers[key], **change)
    found = corpus_plan(corpus, tracker)
    title = tracker.cards_by_number[numbers[key]].title
    assert found.refusals == (_rekeyed(numbers[key], title),)
    assert (found.history, found.freeze) == ((), ())


def test_a_marked_card_no_item_claims_is_refused(corpus):
    """Q-2 created, still marked, before its As filed comment, then retitled by hand: it
    carries nothing of the migration's but its mark, and it sits below no card filed
    already, so it is refused."""
    tracker, numbers = filed_tracker(corpus, marked=("balance:Q-2",))
    tracker.notes[numbers["balance:Q-2"]] = []
    with_change(tracker, numbers["balance:Q-2"], title="Unwritten fork")
    assert any(refusal.startswith(f"plan#{numbers['balance:Q-2']} ('Unwritten fork')")
               for refusal in corpus_plan(corpus, tracker).refusals)


@pytest.mark.parametrize("key, change", [
    ("balance:A-3", {"labels": ("recurrence",)}),
    ("balance:A-3", {"title": "[A-3x] Last part"}),
    ("balance:A-2a", {"title": "[A-2z] First half"}),
], ids=["another-arc", "another-alias", "a-blocker-and-owner"])
def test_a_card_whose_key_now_names_no_item_is_refused(corpus, key, change):
    """Its key read whole but no item's -- its one arc label changed, or its alias (delta
    review of B2a, MEDIUM-2: every earlier case read no key at all) -- refused ONCE: what
    follows from it is not refused again (the finding A-3 owns; for A-2a, the finding F-1 it
    owns, the steps A-2b and A-3 it blocks, and its board place)."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers[key], **change)
    found = corpus_plan(corpus, tracker)
    title = tracker.cards_by_number[numbers[key]].title
    assert found.refusals == (_rekeyed(numbers[key], title),)
    assert not found.freeze


def test_a_card_whose_item_left_the_registries_is_refused(corpus):
    """F-2's row gone, its card still there: refused, never left to stand unaccounted."""
    tracker, numbers = filed_tracker(corpus)
    items = [item for item in corpus_items(corpus) if item.key != "balance:F-2"]
    found = corpus_plan(corpus, tracker, items)
    assert found.refusals == (_rekeyed(numbers["balance:F-2"], "[F-2] Double count"),)


@pytest.mark.parametrize("key, edit", [
    ("balance:R-A", "crlf"), ("balance:Q-1", "comment-deleted"),
], ids=["crlf-body", "question-comment-deleted"])
def test_a_retitled_card_with_no_readable_block_is_refused_all_the_same(corpus, key, edit):
    """Retitled by hand, with its body saved through a web form with CRLF, or a question's
    As filed comment deleted (delta review of B2a, MEDIUM-3): numbered after the
    migration's first card, it is refused whatever its body or comments still say."""
    tracker, numbers = filed_tracker(corpus)
    number = numbers[key]
    if edit == "crlf":
        tracker.bodies[number] = tracker.bodies[number].replace("\n", "\r\n")
    else:
        tracker.notes[number] = []
    with_change(tracker, number, title=card_title("Zz", "Retitled"))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_rekeyed(number, "[Zz] Retitled"),)
    assert (found.history, found.freeze) == ((), ())


# -- the tracker's own cards, told by their number (review of PR #549, M1, L2, L3) --

def _ended(number, title):
    """How the plan refuses card ``number``, titled ``title``, that no item claims, whose
    filing ended, numbered lower than every card an item's key names and under no card filed
    already."""
    return (f"plan#{number} ({title!r})'s filing ended (dropped while marked, or a ruling "
            "withdrawn), no item claims it, and it is numbered lower than every card an item's "
            "key names (a card filed already apart) and under no card filed already: a card "
            "filed outside X-cx before the migration, an X-cx leaf quill unlinked when it was "
            "dropped (R-BAL205), an X-cx card a person unlinked from a card filed already, or "
            "one of the migration's first cards with its title or arc label changed by hand or "
            "its item gone from the registries; re-link one a person unlinked under its card "
            "filed already (a finding or a ruling: block it by its owner again, R-BAL395), and "
            "delete any other on the web, before the run")


def _mapped_with_leaf(corpus, monkeypatch, start=2):
    """The wholly filed corpus, numbered from ``start``, with S-1 mapped onto its card (off
    the board, as a container is) and a step of the tracker's own, #1, under it -- filed
    before the migration, as X-cx's own are (#2-#10, #26 and #27 under #1 today, the
    migration's first card #31 at the earliest)."""
    tracker, numbers = filed_tracker(corpus, start=start)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    tracker.add(1, "step", "Its own.", on_board=False, title="Its own leaf", labels=("salary",),
                parent=numbers["salary:S-1"])
    return tracker


@pytest.mark.parametrize("kind, where", [
    ("step", "a-persons-comment"), ("step", "body"), ("step", "tool-comment"),
    ("finding", "nowhere"), ("ruling", "nowhere"), ("question", "nowhere"),
])
def test_a_card_of_the_trackers_own_below_a_card_filed_already_is_let_be(corpus, monkeypatch,
                                                                         kind, where):
    """X-cx's own cards (#2-#10, #26 and #27 under #1 today), filed before the migration:
    no item's, and not refused, whatever their kind (a finding or a ruling as ``quill file``
    filed one before R-BAL395, a sub-issue of its owner, review of PR #549, L3; one filed
    since, blocked by its owner, is the next test's) and whatever they hold -- an As filed
    block in a body (a fenced example quoting one) or in a comment, the tool's or a
    person's.  Only its number tells a card of the tracker's own (review of PR #549, M1)."""
    texts = corpus_texts(corpus)
    block = texts["balance:F-1"].body
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    with_change(tracker, 1, kind=kind)
    if where == "body":
        tracker.bodies[1] = block
    tracker.notes[1] = {"a-persons-comment": [Comment("a-person", "t", block)],
                        "tool-comment": [Comment(FakeTracker.app_login, "t",
                                                 texts["balance:Q-1"].comment)]}.get(where, [])
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert (found.refusals, found.history, found.freeze) == ((), (), ())


@pytest.mark.parametrize("kind, owners, own", [
    ("finding", ("leaf",), True),
    ("finding", ("filed",), True),
    ("ruling", ("leaf",), True),
    ("finding", ("leaf", "filed"), False),
    ("finding", ("leaf", "outside"), False),
    ("finding", ("outside",), False),
    ("finding", ("item",), False),
    ("step", ("leaf",), False),
], ids=["owned-by-its-leaf", "owned-by-the-card-filed-already", "a-ruling-owned-by-its-leaf",
        "blocked-by-two", "an-outside-blocker-beside-its-owner", "blocked-from-outside-alone",
        "owned-by-an-items-card", "a-step-no-card-owns"])
def test_a_finding_or_ruling_owned_below_a_card_filed_already_is_the_trackers_own(
        corpus, monkeypatch, kind, owners, own):
    """R-BAL395: ``quill file finding|ruling --owner`` files a card blocked by its owner and
    under nothing.  One whose owner -- its one blocker, with none outside the tracker
    (:func:`_state.owner`) -- is the card filed already or a card below it is the tracker's
    own (``_migrate_plan._own``), let be while it is numbered below the migration's first
    card, and refused as the migration's once numbered above it, as a sub-issue is.  Blocked
    by two cards, by an issue outside the tracker beside its owner or alone, or by an item's
    card, it has no owner below a card filed already, and a step has no owner at all: each
    is refused as a card under no card filed already.  ``owners`` names what blocks it: X-cx's
    own leaf, the card filed already, an item's card, or an issue outside the tracker."""
    texts = corpus_texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch, start=10)
    by = {"leaf": 1, "filed": _migrate_source.MAPPED["salary:S-1"],
          "item": next(number for number, card in tracker.cards_by_number.items()
                       if card.title.startswith("[A-3]"))}
    state = {"is_open": False, "state_reason": "COMPLETED"} if kind == "ruling" else {}
    below = () if own else (_outside(2, "Its own card"),)
    for number, refused in ((2, below), (90, (*below, _rekeyed(90, "Its own card", first=10)))):
        tracker.add(number, kind, "One sentence.", on_board=False, title="Its own card",
                    labels=("salary",),
                    blocked_by=tuple(by[name] for name in owners if name != "outside"),
                    outside=tuple(OutsideLink("blocker", "o/code#9") for name in owners
                                  if name == "outside"), **state)
        found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
        assert found.refusals == refused
        assert not found.freeze and not found.history


def test_a_task_under_a_finding_owned_below_a_card_filed_already_is_the_trackers_own(
        corpus, monkeypatch):
    """R-BAL386 and R-BAL395: ``quill file task --parent`` hangs a task under a finding,
    and the finding hangs under nothing, blocked by its owner.  A task under a finding the
    tracker owns is its own too: the walk up takes a finding's owner where it has no parent
    (``_migrate_plan._own``; R395's review, F2)."""
    texts = corpus_texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch, start=10)
    tracker.add(2, "finding", "One sentence.", on_board=False, title="Its own finding",
                labels=("salary",), blocked_by=(1,))
    tracker.add(3, "task", "One sentence.", on_board=False, title="Its own task",
                labels=("salary",), parent=2)
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert (found.refusals, found.history, found.freeze) == ((), (), ())


@pytest.mark.parametrize("kind, where", [
    ("step", "nowhere"), ("step", "body"), ("step", "crlf-body"), ("step", "tool-comment"),
    ("finding", "nowhere"), ("ruling", "nowhere"),
])
def test_a_card_below_a_card_filed_already_filed_after_the_migrations_first_is_refused(
        corpus, monkeypatch, kind, where):
    """The same cards numbered after the migration's first card: none is the tracker's own
    (no card is filed natively inside the freeze), so each is refused, whatever its kind and
    whatever it holds (review of PR #549, M1)."""
    texts = corpus_texts(corpus)
    block = texts["balance:F-1"].body
    body = {"body": block, "crlf-body": block.replace("\n", "\r\n")}.get(where, "Its own.")
    comments = ([Comment(FakeTracker.app_login, "t", texts["balance:Q-1"].comment)]
                if where == "tool-comment" else [])
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    tracker.add(90, kind, body, on_board=False, title="A card of its own", labels=("salary",),
                parent=1)
    tracker.notes[90] = comments
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert found.refusals == (_rekeyed(90, "A card of its own"),)
    assert (found.history, found.freeze) == ((), ())


def test_a_card_dropped_while_marked_outside_x_cx_is_refused_as_what_it_is(corpus):
    """An X-cx leaf dropped while marked, which quill UNLINKS (R-BAL205) and keeps marked for
    good: refused, said to be a dropped card (review of B2a's fixes, L1; none on the tracker
    2026-10-09).  The same card still below the card filed already is X-cx's own."""
    tracker, _ = filed_tracker(corpus)
    tracker.add(1, title="[X-cx L13] A leaf", labels=("balance", FILING), is_open=False,
                state_reason="NOT_PLANNED", on_board=False)
    assert corpus_plan(corpus, tracker).refusals == (
        _ended(1, "[X-cx L13] A leaf"),)


def test_a_card_filed_outside_x_cx_is_refused(corpus):
    """A card of its own outside X-cx, open and unmarked, filed before the migration: the
    cutover starts on X-cx's cards and the migration's only."""
    tracker, _ = filed_tracker(corpus)
    tracker.add(1, title="A card of its own", on_board=False)
    assert corpus_plan(corpus, tracker).refusals == (_outside(1, "A card of its own"),)


def test_x_cx_s_own_leaf_dropped_while_marked_below_it_is_let_be(corpus, monkeypatch):
    """Not yet unlinked: X-cx's own step, its filing quill's business, not the migration's."""
    texts = corpus_texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    with_change(tracker, 1, labels=("salary", FILING), is_open=False, state_reason="NOT_PLANNED")
    assert not plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals


@pytest.mark.parametrize("key", ["salary:N-391", "balance:Q-1", "balance:A-3"])
def test_a_migrated_card_moved_below_a_card_filed_already_and_rekeyed_is_refused(
        corpus, monkeypatch, key):
    """A finding below it retitled and rewritten as ``quill edit`` would (one sentence, no
    block); a question moved there and retitled, its comment deleted; a step moved there and
    retitled: none is X-cx's own, each numbered after the migration's first card, so each is
    refused (third review of B2a, M-A)."""
    tracker, numbers = filed_tracker(corpus)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    number = numbers[key]
    with_change(tracker, number, title="Renamed by hand", parent=numbers["salary:S-1"])
    if key == "salary:N-391":
        tracker.bodies[number] = "The stub disagrees with the profile."
    tracker.notes[number] = []
    if number in tracker.board.items:
        tracker.board.items.remove(number)
    found = plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker))
    assert found.refusals == (_rekeyed(number, "Renamed by hand"),)
    assert not found.freeze and not found.history


def test_cards_two_levels_below_a_card_filed_already_are_its_own_or_refused(corpus,
                                                                            monkeypatch):
    """Below X-cx's own leaf #1, a step and a finding of its own filed before the migration
    are let be, and a card no item claims filed after its first card is refused (third
    review of B2a, L5: M19; review of PR #549, M1 and L3)."""
    texts = corpus_texts(corpus)
    tracker = _mapped_with_leaf(corpus, monkeypatch, start=10)
    tracker.add(2, "step", "Deeper.", title="Its own deeper leaf", labels=("salary",),
                parent=1, on_board=False)
    tracker.add(3, "finding", "One sentence.", title="Its own finding", labels=("salary",),
                parent=2, on_board=False)
    assert not plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals
    tracker.add(92, "step", "One sentence.", title="Deeper step", labels=("salary",),
                parent=3, on_board=False)
    assert plan(corpus_items(corpus), texts, MILESTONES, tracked(tracker)).refusals == (
        _rekeyed(92, "Deeper step", first=10),)


def test_a_card_filed_already_that_is_no_item_is_not_refused_as_unclaimed(corpus, monkeypatch):
    """S-1 mapped but no longer among the items (the source refuses that itself): its card is
    not refused again as a card nothing accounts for (third review of B2a, L5: M1)."""
    tracker, _ = filed_tracker(corpus, skip=("salary:S-1", "salary:N-391"))
    tracker.add(50, "step", title="[S-1] filed", labels=("salary",), on_board=False)
    texts = corpus_texts(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 50)
    items = [item for item in corpus_items(corpus)
             if item.key not in ("salary:S-1", "salary:N-391")]
    assert not plan(items, texts, MILESTONES, tracked(tracker)).refusals


def _mapped_s1(corpus, monkeypatch):
    """The wholly filed corpus with S-1 mapped onto its card, off the board as a container
    is: card #1's place in the corpus."""
    tracker, numbers = filed_tracker(corpus)
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", numbers["salary:S-1"])
    tracker.board.items.remove(numbers["salary:S-1"])
    return tracker, numbers


@pytest.mark.parametrize("change", [{"title": "Promoted to a step"}, {"labels": ("balance",)}],
                         ids=["retitled", "relabelled"])
def test_a_finding_below_a_card_filed_already_turned_into_a_step_is_refused(
        corpus, monkeypatch, change):
    """N-391's finding card, where the migration itself links it below S-1, made a step, its
    title or its arc label changed, its body rewritten without its block -- never moved: it
    is the migration's card by its number, so it is refused and N-391 is not filed again
    beside it (review of PR #549, M1, case 1)."""
    tracker, numbers = _mapped_s1(corpus, monkeypatch)
    number = numbers["salary:N-391"]
    with_change(tracker, number, kind="step", **change)
    tracker.bodies[number] = "Re-read the stub."
    found = plan(corpus_items(corpus), corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == (_rekeyed(number, tracker.cards_by_number[number].title),)
    assert (found.history, found.freeze) == ((), ())


def test_a_step_moved_below_a_card_filed_already_and_relabelled_is_refused(corpus, monkeypatch):
    """A-2b moved below S-1, its arc label changed (its title untouched), its body rewritten
    without its block: refused, and A-2b not filed again (review of PR #549, M1, case 2)."""
    tracker, numbers = _mapped_s1(corpus, monkeypatch)
    number = numbers["balance:A-2b"]
    with_change(tracker, number, parent=numbers["salary:S-1"], labels=("moves-money", "salary"))
    tracker.bodies[number] = "Do the second half."
    found = plan(corpus_items(corpus), corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == (_rekeyed(number, tracker.cards_by_number[number].title),)
    assert (found.history, found.freeze) == ((), ())


def _with_own_cards(tracker):
    """``tracker`` with card #1 as the tracker holds it -- filed already, here S-1's (X-cx's
    in the registries) -- and X-cx's own steps #2-#10, #26 and #27 under it (measured
    read-only 2026-10-09)."""
    tracker.add(1, title="[S-1] Filed long ago", labels=("salary",), on_board=False)
    for number in (*range(2, 11), 26, 27):
        tracker.add(number, title=f"[X-cx L{number}] A leaf", labels=("salary",), parent=1,
                    on_board=False)
    return tracker


def test_the_live_trackers_own_cards_refuse_nothing_before_or_after_the_first_card(
        corpus, monkeypatch):
    """Card #1, filed already and an item's card as X-cx's row makes it, is numbered below
    every card of the tracker's own, which it is not the migration's first card for: with no
    card of the migration's filed, every other item is filed, and with its cards filed from
    #31, the first number free, nothing is left and nothing refused."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    tracker = _with_own_cards(filed_tracker(
        corpus, skip=tuple(item.key for item in corpus_items(corpus)))[0])
    empty = corpus_plan(corpus, tracker)
    assert not empty.refusals and {
        write.key for write in (*empty.history, *empty.freeze) if write.what == CREATE} == {
        item.key for item in corpus_items(corpus) if item.key != "salary:S-1"}
    filed, numbers = filed_tracker(corpus, skip=("salary:S-1",), start=31)
    with_change(_with_own_cards(filed), numbers["salary:N-391"], blocked_by=(1,))
    found = corpus_plan(corpus, filed)
    assert (found.refusals, found.history, found.freeze) == ((), (), ())


def test_a_card_of_the_trackers_own_on_the_board_below_an_unranked_card_filed_already_is_refused(
        corpus, monkeypatch):
    """S-1 mapped with no place, so no rank for the cards below it to stand at, and its own
    leaf #1 on the board: refused by the plan, before any write, never handed to
    :func:`moves` (review of PR #549, L2)."""
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    tracker.board.items.append(1)
    items = [replace(item, links=replace(item.links, place=None))
             if item.key == "salary:S-1" else item for item in corpus_items(corpus)]
    found = plan(items, corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == ("plan#1 is on the board, and the desired order has no place for "
                              "it: settle it before the run",)
    assert (found.history, found.freeze) == ((), ())


@pytest.mark.parametrize("change, reason", [
    ({"kind": "finding"}, "it is a finding, not a step"),
    ({"labels": ("moves-money", "salary")},
     "its labels are ['moves-money', 'salary'], not ['salary']"),
    ({"outside": (OutsideLink("parent", "o/code#5"),)},
     "it is linked outside the tracker (o/code#5): a person's call"),
    ({"parent": 3}, "it is a sub-issue of plan#3, not none: re-homing is a person's call"),
    ({"blocked_by": (3,)},
     "it is blocked by plan#3, which the registries do not record: a person's call"),
    ({"is_open": False, "state_reason": "COMPLETED"}, "it is closed, and a step moves open"),
    ({"labels": ("salary", FILING), "is_open": False, "state_reason": "NOT_PLANNED"},
     "its filing ended (dropped while marked, or a ruling withdrawn): nothing is filed over "
     "it; settle it by hand"),
], ids=["kind", "labels", "outside", "parent", "extra-blocker", "closed", "dropped-marked"])
def test_a_card_filed_already_is_graded_on_its_kind_labels_links_and_state(
        corpus, monkeypatch, change, reason):
    """Card #1's grading (draft 4 s.5's key, kind, labels and links, and its state): each way
    it differs is refused, and nothing is written onto it (review of PR #549, M2)."""
    tracker, numbers = _mapped_s1(corpus, monkeypatch)
    with_change(tracker, numbers["salary:S-1"], **change)
    found = plan(corpus_items(corpus), corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == (f"salary:S-1 (plan#{numbers['salary:S-1']}): {reason}",)
    assert (found.history, found.freeze) == ((), ())


def test_a_card_filed_already_takes_its_missing_parent_link(corpus, monkeypatch):
    """S-1 mapped, under A-2 in the registries and under nothing on the tracker: its parent
    link is written (review of PR #549, M2; the block is
    :func:`test_a_card_filed_already_takes_its_links_alone`)."""
    tracker, _ = _mapped_s1(corpus, monkeypatch)
    items = [replace(item, links=replace(item.links, parent="balance:A-2"))
             if item.key == "salary:S-1" else item for item in corpus_items(corpus)]
    found = plan(items, corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert (found.refusals, found.freeze) == ((), (Write(LINK, "salary:S-1", "balance:A-2"),))


# -- review of the #549 fix: what a refusal names, and the rule's premises ----------

def test_the_migrations_lowest_card_rekeyed_is_refused_naming_both_causes(corpus):
    """A-2's card, #2, the lowest the migration filed, retitled by hand: the first card an
    item's key names is #3 now, so #2 reads as numbered below it -- refused, naming the
    migration's first cards rekeyed beside a card filed outside X-cx (review of the #549
    fix, LOW-1) and an X-cx leaf unlinked by hand (review of the fix itself, LOW-2)."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers["balance:A-2"], title="Fixed a typo by hand")
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_outside(numbers["balance:A-2"], "Fixed a typo by hand"),)
    assert (found.history, found.freeze) == ((), ())


def test_a_card_of_the_trackers_own_carrying_an_items_key_fails_closed(corpus, monkeypatch):
    """The rule's first premise broken: X-cx's own leaf #3 of four, below S-1 mapped onto
    #1, retitled to F-1's key before any card of the migration's is filed.  It is refused as
    F-1's card, and the own leaves numbered above it as the migration's: refused, never
    filed twice (review of the #549 fix, LOW-2)."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    tracker, _ = filed_tracker(corpus, skip=tuple(item.key for item in corpus_items(corpus)),
                               milestone=False)
    tracker.add(1, title="[S-1] Filed long ago", labels=("salary",), on_board=False)
    for number in range(2, 6):
        tracker.add(number, title=f"[X-cx L{number}] A leaf", labels=("salary",), parent=1,
                    on_board=False)
    with_change(tracker, 3, title=card_title("F-1", "A leaf"), labels=("balance",))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        _rekeyed(4, "[X-cx L4] A leaf", first=3), _rekeyed(5, "[X-cx L5] A leaf", first=3),
        "balance:F-1 (plan#3): it is a step, not a finding")
    assert (found.history, found.freeze) == ((), ())


def test_a_withdrawn_ruling_no_item_claims_is_refused_as_a_filing_that_ended(corpus):
    """A ruling filed outside X-cx before the migration and withdrawn (closed as not planned,
    its mark removed): its filing ended, mark or none, and it is refused as such (third review
    of the #549 fix, LOW-2 and LOW-3)."""
    tracker, _ = filed_tracker(corpus)
    tracker.add(1, "ruling", title="A ruling of its own", is_open=False,
                state_reason="NOT_PLANNED", on_board=False)
    assert corpus_plan(corpus, tracker).refusals == (_ended(1, "A ruling of its own"),)


def test_a_card_of_the_trackers_own_given_a_filed_items_key_is_never_to_be_deleted(
        corpus, monkeypatch):
    """The rule's first premise broken after the item's card is filed: X-cx's own leaf #3,
    below card #1, retitled to F-1's key once the migration filed F-1's card from #31.  F-1's
    key is on two cards, neither as its create left it: the refusal names what each holds,
    says to settle them by hand, retitling the tracker's own and deleting neither; the own
    leaves above #3 read as the migration's (review of the #549 fix's LOWs, LOW-A: the
    reviewer's P8; review 2 of B2b-1, MEDIUM-A)."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    filed, numbers = filed_tracker(corpus, skip=("salary:S-1",), start=31)
    tracker = _with_own_cards(filed)
    with_change(tracker, numbers["salary:N-391"], blocked_by=(1,))
    with_change(tracker, 3, title=card_title("F-1", "A leaf"), labels=("balance",))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        _by_hand("balance:F-1", "plan#3 unmarked, under plan#1",
                 f"plan#{numbers['balance:F-1']} unmarked, blocked by "
                 f"plan#{numbers['balance:A-2a']}"),
        *(_rekeyed(number, f"[X-cx L{number}] A leaf", first=3)
          for number in (*range(4, 11), 26, 27)))
    assert (found.history, found.freeze) == ((), ())


@pytest.mark.parametrize("kind, change, refusal", [
    ("step", {}, _outside),
    ("ruling", {"is_open": False, "state_reason": "NOT_PLANNED"}, _ended),
], ids=["open-step", "withdrawn-ruling"])
def test_x_cx_s_own_card_a_person_unlinked_is_refused_naming_that_cause(
        corpus, monkeypatch, kind, change, refusal):
    """X-cx's own card below the card filed already, unlinked by a person -- an open step, or
    a ruling withdrawn (quill never unlinks one): numbered below the migration's first card and
    under no card filed already, it is refused, and its text names a person's unlink and, for
    the ruling, re-linking it as the remedy (review of the #549 fix's LOWs, LOW-D and LOW-E:
    the cause was pinned as text, never as a state)."""
    tracker = _mapped_with_leaf(corpus, monkeypatch)
    with_change(tracker, 1, kind=kind, parent=None, title="[X-cx L13] A leaf", **change)
    found = plan(corpus_items(corpus), corpus_texts(corpus), MILESTONES, tracked(tracker))
    assert found.refusals == (refusal(1, "[X-cx L13] A leaf"),)
    assert (found.history, found.freeze) == ((), ())


# -- a key on two cards (review 2 of B2b-1, MEDIUM-A): each card named by its state -----

#: How a refusal names a card as its create left it.
_BARE = ("still marked and open, linked to nothing the tool reads, off the board, with no "
         "comment")
#: How a refusal names F-1's card as a finished filing leaves it: blocked by its owner, A-2a's
#: card (R-BAL395).
_F1 = "unmarked, blocked by plan#3"


def _copy(tracker, number, of):
    """Card ``number`` put in ``tracker`` as the create that made card ``of`` made it: its
    kind, title, body, labels and milestone, marked, and nothing more."""
    card = tracker.cards_by_number[of]
    tracker.add(number, card.kind, tracker.bodies[of], on_board=False, title=card.title,
                labels=tuple(sorted({*card.labels, FILING})))
    if of in tracker.milestone_of:
        tracker.milestone_of[number] = tracker.milestone_of[of]


@pytest.mark.parametrize("lagging", [False, True], ids=["as-written", "a-read-lagging-its-writes"])
def test_a_re_sent_creates_other_cards_are_offered_for_deletion(corpus, lagging):
    """F-1's create re-sent after a rate limit GitHub had performed it under: #1 a copy of
    F-1's card as its create left it, and #7, the last made, the one the run went on with,
    holding its later writes -- or read as its create left it too, by a read that lags them.
    Each card is named with its state, the others are offered for deletion, and the card to
    keep is the last made, by its number, either way: a keeper named by its state was any
    one under that lag (review 3 of B2b-1, MEDIUM-1)."""
    tracker, numbers = filed_tracker(corpus)
    _copy(tracker, 1, numbers["balance:F-1"])
    if lagging:
        with_change(tracker, 7, labels=("balance", FILING), blocked_by=())
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_resent("balance:F-1", f"plan#1 {_BARE}",
                                      f"plan#7 {_BARE if lagging else _F1}"),)
    assert (found.history, found.freeze) == ((), ())


def test_two_re_sent_creates_are_each_refused_by_key_with_its_own_card_to_keep(corpus):
    """S-1's create and F-1's each re-sent: two keys on two cards each, S-1 first in the
    source and F-1 first by key.  Each key's last card is graded as its item's card, offers
    its own copy for deletion and names its own last card to keep; the refusals stand in
    key order, as every refusal of a key does."""
    tracker, numbers = filed_tracker(corpus, start=3)
    _copy(tracker, 1, numbers["salary:S-1"])
    _copy(tracker, 2, numbers["balance:F-1"])
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        _resent("balance:F-1", f"plan#2 {_BARE}", "plan#8 unmarked, blocked by plan#4"),
        _resent("salary:S-1", f"plan#1 {_BARE}",
                "plan#7 unmarked, blocking plan#12, on the board"))
    assert (found.history, found.freeze) == ((), ())


_A2A = "blocking plan#5, plan#6, plan#8, on the board"


@pytest.mark.parametrize("change, refusals", [
    (None, (_resent("balance:A-2", f"plan#1 {_BARE}",
                    "plan#3 unmarked, holding 2 sub-issue(s)"),
            _resent("balance:A-2a", f"plan#2 {_BARE}",
                    f"plan#4 unmarked, under plan#3, {_A2A}"))),
    ("moved", (_by_hand("balance:A-2", "plan#1 marked, holding 1 sub-issue(s)",
                        "plan#3 unmarked, holding 1 sub-issue(s)"),
               _by_hand("balance:A-2a", f"plan#2 {_BARE}",
                        f"plan#4 unmarked, under plan#1, {_A2A}"))),
    ("closed", (_by_hand("balance:A-2", f"plan#1 {_BARE}",
                         "plan#3 unmarked, closed, holding 2 sub-issue(s)"),
                _resent("balance:A-2a", f"plan#2 {_BARE}",
                        f"plan#4 unmarked, under plan#3, {_A2A}"))),
], ids=["as-the-run-left-them", "the-last-leaf-card-under-the-containers-copy",
        "the-containers-last-card-closed"])
def test_two_re_sent_creates_one_the_others_parent_are_graded_on_each_others_kept_card(
        corpus, change, refusals):
    """A-2's create and A-2a's each re-sent, the run going on with each last made, so A-2a's
    last card is under A-2's: each last card is graded with every such key's card the one
    it keeps, so both keys offer their copies (review 4 of B2b-1, LOW-3: no test held two
    keys re-sent, one the other's parent).  A-2a's last card moved under A-2's copy by hand
    is under a card no run keeps: both keys are settled by hand.  A-2's last card closed by
    a person is settled by hand, yet A-2's cards are still in the state a re-sent create
    leaves, so A-2a's last card is graded under A-2's last and offered: that one holds a
    later write, which no refusal lets be deleted (review 5 of B2b-1, LOW-A)."""
    tracker, numbers = filed_tracker(corpus, start=3)
    _copy(tracker, 1, numbers["balance:A-2"])
    _copy(tracker, 2, numbers["balance:A-2a"])
    if change == "moved":
        with_change(tracker, numbers["balance:A-2a"], parent=1)
    elif change == "closed":
        with_change(tracker, numbers["balance:A-2"], is_open=False, state_reason="COMPLETED")
    found = corpus_plan(corpus, tracker)
    assert found.refusals == refusals
    assert (found.history, found.freeze) == ((), ())


def test_a_re_sent_last_card_under_a_card_refused_already_is_settled_by_hand(corpus):
    """F-1's create re-sent, #1 a bare copy; a person retitles A-3's card (#5) away from its
    key and makes it #7's owner, F-1's last card blocked by it in place of A-2a's (review 4 of
    B2b-1, LOW-1, its probe; R-BAL395).  #5 is refused as no item's card, and the read
    excuses a link to a card refused already; but once #5 is retitled back, #7 is owned by
    A-3's card, not A-2a's, so the last card is graded as it is and neither card is offered
    for deletion."""
    tracker, numbers = filed_tracker(corpus)
    _copy(tracker, 1, numbers["balance:F-1"])
    with_change(tracker, numbers["balance:A-3"], title="Last part, renamed by hand")
    with_change(tracker, numbers["balance:F-1"], blocked_by=(numbers["balance:A-3"],))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        _by_hand("balance:F-1", f"plan#1 {_BARE}", "plan#7 unmarked, blocked by plan#5"),
        _rekeyed(5, "Last part, renamed by hand", first=1))
    assert (found.history, found.freeze) == ((), ())


def test_a_re_sent_last_card_under_a_card_given_its_parents_key_by_hand_is_settled_by_hand(
        corpus):
    """F-1's create re-sent, #1 a bare copy; a person retitles A-3's card (#5) to A-2a's
    key, F-1's owner's, and makes it #7's owner, F-1's last card blocked by it (review 5 of
    B2b-1, LOW-A, its P1; R-BAL395).  A-2a's key is on two cards, neither as its create left
    it, so a person keeps either: graded with no card of A-2a's, #7 is owned by a card no
    grading names, and neither F-1 card is offered for deletion.  Graded under A-2a's last
    card, #5, #7 was offered, and once #5 was retitled back as A-2a's refusal says, the next
    run refused it."""
    tracker, numbers = filed_tracker(corpus)
    _copy(tracker, 1, numbers["balance:F-1"])
    with_change(tracker, numbers["balance:A-3"], title=corpus_texts(corpus)["balance:A-2a"].title)
    with_change(tracker, numbers["balance:F-1"], blocked_by=(numbers["balance:A-3"],))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        _by_hand("balance:A-2a", "plan#3 unmarked, under plan#2, blocking plan#4, plan#5, on the "
                 "board", "plan#5 unmarked, blocked by plan#3, blocking plan#7, plan#8, on the "
                 "board"),
        _by_hand("balance:F-1", f"plan#1 {_BARE}", "plan#7 unmarked, blocked by plan#5"))
    assert (found.history, found.freeze) == ((), ())


def test_a_re_sent_last_card_in_a_milestone_made_by_hand_is_settled_by_hand(corpus):
    """A-3's create re-sent, #1 a bare copy; its outcome's milestone deleted on the web and
    every card in it moved into one a person made (review 5 of B2b-1, LOW-A, its P1b).  The
    read refuses that milestone and excuses a card in it, but once it is settled -- deleted,
    say -- A-3's last card is in no milestone of its outcome's title: graded with no
    milestone excused, neither card is offered for deletion."""
    tracker, numbers = filed_tracker(corpus)
    _copy(tracker, 1, numbers["balance:A-3"])
    tracker.milestones_by_number.pop(1)
    tracker.milestones_by_number[2] = Milestone(2, "Made by hand", "by hand")
    tracker.milestone_of = {number: 2 for number in tracker.milestone_of}
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (
        _by_hand("balance:A-3", f"plan#1 {_BARE}",
                 "plan#5 unmarked, blocked by plan#3, blocking plan#8, on the board"),
        "milestone #2 'Made by hand' is none the input file names (renamed on the web?): settle "
        "it before the run")
    assert (found.history, found.freeze) == ((), ())


@pytest.mark.parametrize("owner, refusal, states", [
    (1, _resent, ("plan#1 unmarked, holding 11 sub-issue(s), blocking plan#39",
                  "plan#26 unmarked, under plan#1")),
    (26, _by_hand, ("plan#1 unmarked, holding 11 sub-issue(s)",
                    "plan#26 unmarked, under plan#1, blocking plan#39")),
], ids=["under-the-card-filed-already", "under-the-card-given-its-key"])
def test_a_re_sent_last_card_is_graded_under_a_card_filed_alreadys_own_card(
        corpus, monkeypatch, owner, refusal, states):
    """N-391's create re-sent, its last card owned by card #1 (blocked by it, R-BAL395),
    filed already as S-1's, while X-cx's own leaf #26 carries S-1's key by hand.  S-1's
    refusal keeps card #1 (retitle #26), so N-391's last card is graded as #1's, not as
    #26's, the last card of S-1's key: owned by #1 its copy is offered; owned by #26, which
    a run refuses once #26 is retitled as S-1's refusal says, it is settled by hand."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    filed, numbers = filed_tracker(corpus, skip=("salary:S-1",), start=31)
    tracker = _with_own_cards(filed)
    _copy(tracker, 30, numbers["salary:N-391"])
    with_change(tracker, numbers["salary:N-391"], blocked_by=(owner,))
    with_change(tracker, 26, title=card_title("S-1", "A leaf"))
    found = corpus_plan(corpus, tracker)
    assert numbers["salary:N-391"] == 39
    assert found.refusals == (
        refusal("salary:N-391", f"plan#30 {_BARE}", f"plan#39 unmarked, blocked by plan#{owner}"),
        _filed_already("salary:S-1", 1, *states),
        _rekeyed(27, "[X-cx L27] A leaf", first=26))
    assert (found.history, found.freeze) == ((), ())


@pytest.mark.parametrize("change, state", [
    ({"blocked_by": ()}, "unmarked"),
    ({"is_open": False, "state_reason": "COMPLETED"}, "unmarked, closed, blocked by plan#3"),
    ({"outside": (OutsideLink("parent", "o/code#5"),)},
     "unmarked, blocked by plan#3, linked outside the tracker"),
    ({"kind": "step"}, _F1),
], ids=["unmarked-unlinked", "closed", "linked-outside", "both-retyped"])
def test_a_re_sent_create_whose_last_card_is_refused_is_settled_by_hand(corpus, change, state):
    """F-1's create re-sent, #1 a bare copy, and #7, the last made, one the next run refuses
    as F-1's card: unmarked without its owner link, blocked by A-2a (R-BAL395; a person's
    unlink, or a read that lags the link's write, both of which the refusal names: review 5
    of B2b-1, LOW-B), closed, or
    linked outside the tracker by a person, or both cards retyped (copies still).  Keeping
    #7 would be refused, so neither card is offered for deletion (review 3 of B2b-1, LOW-1,
    a condition of MEDIUM-1's keeper)."""
    tracker, numbers = filed_tracker(corpus)
    _copy(tracker, 1, numbers["balance:F-1"])
    with_change(tracker, 7, **change)
    if "kind" in change:
        with_change(tracker, 1, **change)
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_by_hand("balance:F-1", f"plan#1 {_BARE}", f"plan#7 {state}"),)
    assert (found.history, found.freeze) == ((), ())


def _held_by_hand(tracker, edit):
    """``tracker`` with card #1, a re-sent copy, holding the later write ``edit`` names."""
    if edit == "board":
        tracker.board.items.append(1)
    elif edit == "comment":
        tracker.notes[1] = [Comment("a-person", "t", "Seen.")]
    elif edit in ("sub-issue", "blocking"):
        with_change(tracker, 8, **({"parent": 1} if edit == "sub-issue" else
                                   {"blocked_by": (*tracker.cards_by_number[8].blocked_by, 1)}))
    else:
        with_change(tracker, 1, **{
            "unmarked": {"labels": ("balance",)},
            "closed": {"is_open": False, "state_reason": "NOT_PLANNED"},
            "parent": {"parent": 3}, "blocker": {"blocked_by": (3,)},
            "outside": {"outside": (OutsideLink("parent", "saltyreformed-labs/x#1"),)},
        }[edit])


@pytest.mark.parametrize("edit, state", [
    ("unmarked", "unmarked"), ("closed", "marked, closed"), ("parent", "marked, under plan#3"),
    ("sub-issue", "marked, holding 1 sub-issue(s)"), ("blocker", "marked, blocked by plan#3"),
    ("blocking", "marked, blocking plan#8"), ("outside", "marked, linked outside the tracker"),
    ("board", "marked, on the board"), ("comment", "marked, 1 comment(s)"),
])
def test_a_re_sent_copy_holding_any_later_write_is_settled_by_hand(corpus, edit, state):
    """A re-sent create's copy of F-1's card (#1) holding a write its create did not make --
    its mark removed, a close, a link either way, a board place, a comment: not the state a
    re-sent create leaves, so settled by hand, never offered for deletion, the write named in
    its state (review 2 of B2b-1, MEDIUM-A: the keeper by its state)."""
    tracker, numbers = filed_tracker(corpus)
    _copy(tracker, 1, numbers["balance:F-1"])
    _held_by_hand(tracker, edit)
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_by_hand("balance:F-1", f"plan#1 {state}", f"plan#7 {_F1}"),)
    assert (found.history, found.freeze) == ((), ())


@pytest.mark.parametrize("held, refusal", [
    (False, _resent), (True, _by_hand),
], ids=["each-but-the-last-as-created", "the-middle-one-holding-a-write"])
def test_three_cards_of_one_create_are_each_graded(corpus, held, refusal):
    """F-1's create re-sent twice (:mod:`_github` re-sends one up to ``RATE_LIMIT_RETRIES``
    times): two copies, #1 and #2, before the card the run went on with.  Each card but the
    last is graded, so a middle copy holding a write is settled by hand, never offered for
    deletion."""
    tracker, numbers = filed_tracker(corpus, start=4)
    for number in (1, 2):
        _copy(tracker, number, numbers["balance:F-1"])
    if held:
        tracker.notes[2] = [Comment("a-person", "t", "Seen.")]
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (refusal(
        "balance:F-1", f"plan#1 {_BARE}",
        f"plan#2 {'marked, 1 comment(s)' if held else _BARE}",
        "plan#9 unmarked, blocked by plan#5"),)
    assert (found.history, found.freeze) == ((), ())


@pytest.mark.parametrize("retitled, key, states", [
    ("balance:A-3", "A-2", ("plan#2 unmarked, holding 2 sub-issue(s)",
                            "plan#5 unmarked, blocked by plan#3, blocking plan#8, on the board")),
    ("balance:A-2b", "A-3", ("plan#4 unmarked, under plan#2, blocked by plan#3, on the board",
                             "plan#5 unmarked, blocked by plan#3, blocking plan#8, on the board")),
], ids=["onto-a-lower-card", "onto-a-higher-card"])
def test_a_card_retitled_to_another_items_key_is_settled_by_hand(corpus, retitled, key,
                                                                  states):
    """A-3's card retitled by hand to A-2's key, and A-2b's to A-3's (review 2 of B2b-1,
    MEDIUM-A, P1 and P1b): "keep the highest" deleted A-2's card, holding A-2a and A-2b,
    and A-2b's own.  Neither card is as its create left it, so the refusal names each
    card's state, settles them by hand and offers neither for deletion."""
    tracker, numbers = filed_tracker(corpus)
    with_change(tracker, numbers[retitled], title=card_title(key, "Retitled"))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_by_hand(f"balance:{key}", *states),)
    assert (found.history, found.freeze) == ((), ())


def test_a_card_given_a_card_filed_alreadys_key_offers_no_card_for_deletion(corpus,
                                                                             monkeypatch):
    """X-cx's own leaf #26 retitled by hand to S-1's key, S-1 mapped onto card #1 (review 2 of
    B2b-1, MEDIUM-A, P2): "keep the highest" offered card #1 for deletion.  The migration files
    no card for a card filed already, so every other card holding its key was given it by
    hand: retitle, delete none.  #27, numbered above #26, reads as the migration's, its
    refusal naming #26 to settle first."""
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", 1)
    filed, numbers = filed_tracker(corpus, skip=("salary:S-1",), start=31)
    tracker = _with_own_cards(filed)
    with_change(tracker, numbers["salary:N-391"], blocked_by=(1,))
    with_change(tracker, 26, title=card_title("S-1", "A leaf"))
    found = corpus_plan(corpus, tracker)
    assert numbers["salary:N-391"] == 39
    assert found.refusals == (
        _filed_already("salary:S-1", 1, "plan#1 unmarked, holding 11 sub-issue(s), blocking "
                       "plan#39", "plan#26 unmarked, under plan#1"),
        _rekeyed(27, "[X-cx L27] A leaf", first=26))
    assert (found.history, found.freeze) == ((), ())


@pytest.mark.parametrize("state", [
    "a-copy-body-edited", "a-copy-title-edited", "a-copy-kind-changed",
    "a-copy-milestone-changed", "a-copy-label-added", "another-items-card-retitled"])
def test_cards_each_as_a_create_left_it_but_no_copies_of_one_are_settled_by_hand(corpus,
                                                                                  state):
    """Every card but the last made as its create left it, as a re-sent create leaves them,
    yet not copies of one create: a re-sent copy's body, title (its key kept), kind,
    milestone or labels (an arc's apart) edited by hand, a case per part the copies are
    compared on (review 3 of B2b-1, LOW-3); or a run stopped after A-2a's create, before its
    link, and A-2's card, holding nothing yet, retitled to A-2a's key.  A person's edit, so
    settled by hand: neither is offered for deletion."""
    if state.startswith("a-copy-"):
        tracker, numbers = filed_tracker(corpus)
        _copy(tracker, 1, numbers["balance:F-1"])
        if state == "a-copy-body-edited":
            tracker.bodies[1] = "Edited by hand."
        elif state == "a-copy-milestone-changed":
            assert 1 not in tracker.milestone_of
            tracker.milestone_of[1] = 1
        else:
            with_change(tracker, 1, **{
                "a-copy-title-edited": {"title": card_title("F-1", "Edited by hand")},
                "a-copy-kind-changed": {"kind": "step"},
                "a-copy-label-added": {"labels": ("balance", FILING, "moves-money")},
            }[state])
        key, states = "balance:F-1", (f"plan#1 {_BARE}", f"plan#7 {_F1}")
    else:
        kept = ("balance:A-2", "balance:A-2a")
        tracker, numbers = filed_tracker(
            corpus, marked=kept,
            skip=tuple(item.key for item in corpus_items(corpus) if item.key not in kept))
        tracker.board.items.remove(numbers["balance:A-2a"])
        with_change(tracker, numbers["balance:A-2a"], parent=None)
        with_change(tracker, numbers["balance:A-2"],
                    title=tracker.cards_by_number[numbers["balance:A-2a"]].title)
        key, states = "balance:A-2a", (f"plan#2 {_BARE}", f"plan#3 {_BARE}")
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_by_hand(key, *states),)
    assert (found.history, found.freeze) == ((), ())


def test_a_key_on_two_cards_is_refused_and_nothing_written_for_it(corpus):
    """A second card of F-1, marked and holding nothing, numbered ABOVE F-1's filed card: not
    the state a re-sent create leaves (the run goes on with the last made), so it is settled
    by hand; and no third card is planned for F-1."""
    tracker, _ = filed_tracker(corpus)
    tracker.add(40, "finding", "dup", on_board=False,
                title=card_title("F-1", "Stale row"), labels=("balance", FILING))
    found = corpus_plan(corpus, tracker)
    assert found.refusals == (_by_hand("balance:F-1", f"plan#7 {_F1}", f"plan#40 {_BARE}"),)
    assert not found.freeze and "balance:F-1" not in found.numbers
