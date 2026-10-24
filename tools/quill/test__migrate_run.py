"""X-cx's migration run (:mod:`_migrate_run`): the plan's writes made on the in-memory
tracker (:mod:`_fake`), on the small corpus (:mod:`_migrate_fake`).  Nothing here reaches
GitHub; a clock that never waits stands in for time.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
import requests

from tools.quill import _migrate_source, migrate
from tools.quill._fake import AnswerLostOnce, FailOnce, FakeTracker
from tools.quill._github import GitHubError
from tools.quill._migrate_fake import complete_input
from tools.quill._migrate_plan import desired_board
from tools.quill._migrate_plan_fake import (
    MILESTONES,
    corpus_items,
    corpus_plan,
    corpus_texts,
    filed_tracker,
    tracked,
)
from tools.quill._migrate_run import (
    PACE_SECONDS,
    RENEW_SECONDS,
    Clock,
    Filing,
    Run,
    Stopped,
)
from tools.quill._tracker import BOARD_WAIT_SECONDS, POLL_SECONDS, OutsideLink, TrackerError
from tools.quill.setup_tracker import FILING


class _Time:
    """A clock that moves only when slept on, keeping every sleep."""

    def __init__(self):
        """At 0, nothing slept."""
        self.at, self.slept = 0.0, []

    def now(self):
        """The time."""
        return self.at

    def sleep(self, seconds):
        """Move the clock on."""
        self.slept.append(seconds)
        self.at += seconds


def _empty():
    """The tracker before the migration: #1 and #2 deleted numbers, as the real tracker's
    gaps are, and nothing else."""
    tracker = FakeTracker()
    tracker.gone.update({1, 2})
    return tracker


def _filed(corpus, **marks):
    """The tracker as a finished migration leaves it (:func:`filed_tracker`, numbered from
    #2), #1 a deleted number."""
    tracker, numbers = filed_tracker(corpus, **marks)
    tracker.gone.add(1)
    return tracker, numbers


def _run(corpus, tracker, time=None):
    """A run filing the corpus on ``tracker``."""
    time = time or _Time()
    return Run(lambda: tracker, Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES),
               Clock(time.now, time.sleep))


def _settled(corpus, tracker):
    """Whether the plan, read now, refuses nothing and has nothing left to write."""
    found = corpus_plan(corpus, tracker)
    return (found.refusals, found.history, found.freeze) == ((), (), ())


def test_history_then_open_files_every_card_and_orders_the_board(corpus):
    """From the empty tracker: ``history`` files both rulings first, each closed with its mark
    removed in one write; ``open`` then files every other card, removes every mark, and
    puts the board in its desired order.  The plan read afterwards refuses nothing and has
    nothing left; the first cards are the rulings', so the number rule's blind case is the
    first rulings' (:mod:`_migrate_plan`)."""
    tracker = _empty()
    assert not _run(corpus, tracker).history()
    assert tracker.writes == [
        ("create", 3, "ruling", corpus_texts(corpus)["balance:R-A"].title, ("balance", FILING)),
        ("close_unmarked", 3, ("balance",)),
        ("create", 4, "ruling", corpus_texts(corpus)["salary:R-SAL1"].title, (FILING, "salary")),
        ("close_unmarked", 4, ("salary",))]
    assert not _run(corpus, tracker).open()
    assert _settled(corpus, tracker)
    assert not [card for card in tracker.cards_by_number.values() if FILING in card.labels]
    numbers = corpus_plan(corpus, tracker).numbers
    assert tracker.board.items == desired_board(corpus_items(corpus), numbers,
                                                tracked(tracker))[0]
    assert min(number for key, number in numbers.items()
               if not key.startswith(("balance:R-", "salary:R-"))) == 5


def test_a_split_steps_leaves_are_filed_in_its_outcomes_milestone_and_it_in_none(corpus):
    """The outcome scoping A-2 as well as A-3: the run files A-2's leaves in its milestone
    and A-2 in none (the coordinator's code call, 14:4x 10-21), and the plan read afterwards,
    which ``migrate verify`` grades by, refuses nothing and has nothing left."""
    split = replace(corpus, outcomes=(("O1 the first", ("balance:A-2", "balance:A-3")),))
    tracker = _empty()
    assert not _run(split, tracker).history()
    assert not _run(split, tracker).open()
    assert _settled(split, tracker)
    numbers = corpus_plan(split, tracker).numbers
    assert {key: tracker.milestone_of.get(numbers[key]) for key in (
        "balance:A-2", "balance:A-2a", "balance:A-2b", "balance:A-3", "salary:S-1")} == {
        "balance:A-2": None, "balance:A-2a": 1, "balance:A-2b": 1, "balance:A-3": 1,
        "salary:S-1": None}

def test_a_second_run_of_each_phase_writes_nothing(corpus):
    """A finished migration run again: neither phase writes, and the board moves nothing
    (draft 4 s.4: a second run moves nothing)."""
    tracker = _empty()
    _run(corpus, tracker).history()
    _run(corpus, tracker).open()
    made = list(tracker.writes)
    assert not _run(corpus, tracker).history() and not _run(corpus, tracker).open()
    assert tracker.writes == made


def test_open_is_refused_while_history_is_unfinished(corpus):
    """``open`` before ``history``: refused, nothing written -- the first cards the migration
    files are rulings (what the number rule cannot see rests on it)."""
    tracker = _empty()
    assert _run(corpus, tracker).open() == [
        "HISTORY is unfinished, 4 write(s) still to make: run `migrate history` first, so "
        "every ruling is filed before any other card (what the number rule cannot see rests "
        "on it)"]
    assert not tracker.writes


def test_a_plan_that_refuses_anything_writes_nothing(corpus):
    """A card no item claims, outside every card filed already: both phases refuse with the
    plan's own refusal, and nothing is written."""
    tracker = _empty()
    tracker.add(3, title="A stray card", on_board=False)
    refusals = list(corpus_plan(corpus, tracker).refusals)
    assert refusals and _run(corpus, tracker).history() == refusals
    assert _run(corpus, tracker).open() == refusals
    assert not tracker.writes


def _to_the_end(corpus, tracker):
    """Run ``history`` then ``open`` again and again, each run stopping at the first write
    that fails, until both finish; how many runs stopped."""
    stopped = 0
    for phase in ("history", "open"):
        while True:
            try:
                assert not getattr(_run(corpus, tracker), phase)()
                break
            except GitHubError:
                stopped += 1
    return stopped


@pytest.mark.parametrize("fault", [FailOnce, AnswerLostOnce])
@pytest.mark.parametrize("owner, name", [
    ("tracker", "create_milestone"), ("tracker", "create"), ("tracker", "close_unmarked"),
    ("tracker", "add_child"), ("tracker", "block"), ("tracker", "comment"),
    ("tracker", "unmark"), ("board", "add")])
def test_a_run_stopped_at_any_write_resumes_and_makes_nothing_twice(corpus, fault, owner,
                                                                   name):
    """A write that fails before it lands, or lands and loses its answer, stops the run; the
    next run finds what landed and makes the rest: the finished tracker is the one a run
    with no fault leaves, write for write but the faulted one's retry."""
    clean = _empty()
    _to_the_end(corpus, clean)
    tracker = _empty()
    fault(tracker if owner == "tracker" else tracker.board, name)
    assert _to_the_end(corpus, tracker) == 1
    assert _settled(corpus, tracker)
    assert {n: replace(c, board_item=None) for n, c in tracker.cards_by_number.items()} == {
        n: replace(c, board_item=None) for n, c in clean.cards_by_number.items()}
    assert (tracker.notes, tracker.board.items) == (clean.notes, clean.board.items)


def test_a_listing_that_lags_a_link_a_comment_or_a_board_add_is_read_again_before_writing(
        corpus):
    """A-2b's card linked under A-2 and blocked by A-2a, and Q-1's As filed comment
    written and its card put on the board, each still marked, by a run that stopped there;
    the next run's listing lags all four.  Each write is re-read by number first and not
    sent again -- a second parent link the in-memory tracker would refuse loudly, a second
    comment the plan refuses after, a second board add GitHub answers with the same item --
    and the marks are removed.  A-3's card, its filing finished, reads marked still: its
    mark's removal is re-read too, and not sent again (GitHub answers it 404)."""
    tracker, numbers = _filed(corpus, marked=("balance:A-2b", "balance:Q-1"))
    real, lagged = tracker.all_cards, []

    def lagging():
        cards = real()
        if not lagged:
            lagged.append(True)
            step, question = numbers["balance:A-2b"], numbers["balance:Q-1"]
            cards[step] = replace(cards[step], card=replace(cards[step].card, parent=None,
                                                            blocked_by=()))
            cards[question] = replace(cards[question], comments=(),
                                      card=replace(cards[question].card, board_item=None))
            done = cards[numbers["balance:A-3"]]
            cards[numbers["balance:A-3"]] = replace(done, card=replace(
                done.card, labels=(*done.card.labels, FILING)))
        return cards

    tracker.all_cards = lagging
    assert not _run(corpus, tracker).open()
    assert tracker.writes[1:] == [("unmark", numbers["balance:A-2b"]),
                                  ("unmark", numbers["balance:Q-1"])]
    assert _settled(corpus, tracker)


@pytest.mark.parametrize("outside", [False, True], ids=["in-the-tracker", "outside-it"])
def test_a_card_linked_elsewhere_by_the_time_its_link_is_made_stops_the_run(corpus, outside):
    """The re-read before a parent link finds the card under another card, in the tracker or
    outside it (review 3 of B2b-1, LOW-2: one outside was sent the link): a person's edit
    inside the freeze, so the run stops rather than re-home it."""
    tracker, numbers = _filed(corpus, marked=("balance:A-2b",))
    step, real = numbers["balance:A-2b"], tracker.all_cards

    def lagging():
        cards = real()
        cards[step] = replace(cards[step], card=replace(cards[step].card, parent=None,
                                                        outside=()))
        return cards

    tracker.all_cards = lagging
    tracker.cards_by_number[step] = replace(
        tracker.cards_by_number[step], parent=None if outside else numbers["balance:A-3"],
        outside=(OutsideLink("parent", "o/code#5"),) if outside else ())
    with pytest.raises(Stopped, match=(r"is a sub-issue of o/code#5, outside the tracker now"
                                       if outside else r"is a sub-issue of plan#\d+ now")):
        _run(corpus, tracker).open()
    assert not tracker.writes[1:]


def test_a_blocker_outside_the_tracker_added_since_the_read_is_refused_by_the_re_plan(corpus):
    """A-2b's card blocked by an issue outside the tracker after the run's read: its parent
    link's re-read finds no parent, and only a parent stops the link, so the link is made,
    then the mark's removal; the board phase's re-plan refuses the outside link, a person's
    call, and the run stops before the board (review 4 of B2b-1, LOW-3: the filter was
    unpinned, and dropping it stopped the link naming a parent that does not exist)."""
    tracker, numbers = _filed(corpus, marked=("balance:A-2b",))
    step, real, reads = numbers["balance:A-2b"], tracker.all_cards, []

    def lagging():
        cards = real()
        reads.append(True)
        if len(reads) == 1:
            cards[step] = replace(cards[step], card=replace(cards[step].card, parent=None,
                                                            outside=()))
        return cards

    tracker.all_cards = lagging
    tracker.cards_by_number[step] = replace(tracker.cards_by_number[step], parent=None,
                                            outside=(OutsideLink("blocker", "o/code#9"),))
    with pytest.raises(Stopped, match=rf"is refused .*: balance:A-2b \(plan#{step}\): it is "
                                      r"linked outside the tracker \(o/code#9\)"):
        _run(corpus, tracker).open()
    assert tracker.writes[1:] == [("add_child", numbers["balance:A-2"], step), ("unmark", step)]


def test_a_card_gone_by_the_time_its_link_is_made_stops_the_run(corpus):
    """The re-read before a parent link finds no card: deleted by hand inside the freeze
    after the run's read, so the run stops, naming it, and writes nothing."""
    tracker, numbers = _filed(corpus, marked=("balance:A-2b",))
    step, real, by_number = numbers["balance:A-2b"], tracker.all_cards, tracker.cards

    def lagging():
        cards = real()
        cards[step] = replace(cards[step], card=replace(cards[step].card, parent=None))
        return cards

    tracker.all_cards = lagging
    tracker.cards = lambda wanted: {n: c for n, c in by_number(wanted).items() if n != step}
    with pytest.raises(Stopped, match=rf"plan#{step} is not on the tracker"):
        _run(corpus, tracker).open()
    assert not tracker.writes[1:]


@pytest.mark.parametrize("title, change, stop", [
    ("[A-2b]", {"parent": None, "mark": True},
     r"does not show every write yet \(2 to make on this read\)"),
    ("[R-A]", {"is_open": True, "state_reason": None, "mark": True},
     r"does not show every write yet \(1 to make on this read\)"),
    ("[A-2b]", {"parent": None},
     r"read again after every card's writes, is refused .*: balance:A-2b \(plan#\d+\): its "
     r"filing finished, yet it lacks link: a person changed it, or this read lags the "
     r"migration's own writes; run again later"),
], ids=["a-link-and-its-mark", "a-rulings-close", "read-as-a-persons-edit"])
def test_a_read_after_the_freeze_that_lags_its_writes_stops_before_the_board(
        corpus, title, change, stop):
    """The read after every card's writes shows a card as it stood before some of them --
    A-2b before its link and its mark's removal, R-A before its close, or A-2b before its
    link alone, which reads as a person's edit, and the refusal names the lag beside it
    (review 4 of B2b-1, LOW-4): the plan, read again, has writes left or refuses, so the
    run stops before ordering the board, the count or the refusal named, and the next run,
    its read caught up, writes nothing and finishes."""
    tracker = _empty()
    _run(corpus, tracker).history()
    real, reads = tracker.all_cards, []

    def lagging():
        cards = real()
        reads.append(True)
        found = [n for n, whole in cards.items() if whole.card.title.startswith(title)]
        if len(reads) == 2 and found:
            card, fields = cards[found[0]].card, dict(change)
            if fields.pop("mark", False):
                fields["labels"] = (*card.labels, FILING)
            cards[found[0]] = replace(cards[found[0]], card=replace(card, **fields))
        return cards

    tracker.all_cards = lagging
    with pytest.raises(Stopped, match=stop):
        _run(corpus, tracker).open()
    assert not [write for write in tracker.writes if write[0] == "board_place"]
    made = len(tracker.writes)
    assert not _run(corpus, tracker).open() and len(tracker.writes) == made
    assert _settled(corpus, tracker)


def test_a_re_plan_whose_milestone_listing_lags_its_create_stops_naming_the_lag(corpus):
    """The board phase's re-plan reads a milestone listing that lags the milestone this run
    made: A-3's card, filed in it, is refused for a milestone not on the tracker, and both
    the refusal and the wrapper name the lag; the next run, its listing caught up, writes
    nothing and finishes, the milestone made once (review 6 of B2b-1, LOW-1: the wrapper's
    "settle the rest as each says" could not be followed here)."""
    tracker = _empty()
    _run(corpus, tracker).history()
    real, reads = tracker.milestones, []

    def lagging():
        reads.append(True)
        return {} if len(reads) == 2 else real()

    tracker.milestones = lagging
    with pytest.raises(Stopped) as raised:
        _run(corpus, tracker).open()
    card, = [number for number, held in tracker.cards_by_number.items()
             if held.title.startswith("[A-3]")]
    assert str(raised.value) == (
        "the tracker, read again after every card's writes, is refused (a read that lags the "
        "run's writes, a create GitHub performed twice, or a person's edit; run `migrate open` "
        "again once GitHub's reads catch up, and settle what it still refuses as each says, "
        f"else by hand): balance:A-3 (plan#{card}): its milestone 'The first' is not on the "
        "tracker, though the migration makes a milestone before any card in it: deleted or "
        "renamed on the web, or this read lags its create? One made again would hold none of "
        "its cards (a card's milestone is set only by its create); run again later, and if it "
        "is refused still, settle it by hand")
    made = len(tracker.writes)
    tracker.milestones = real
    assert not _run(corpus, tracker).open() and len(tracker.writes) == made
    assert _settled(corpus, tracker)
    assert len(tracker.milestones()) == 1


def test_the_numbering_probe_stops_a_run_whose_listing_lags_a_card(corpus):
    """A card the listing lacks answers a read by number: the run stops before any write
    (a create whose answer was lost is found, never made twice)."""
    tracker = _empty()
    _run(corpus, tracker).history()
    made = list(tracker.writes)
    real = tracker.all_cards
    tracker.all_cards = lambda: {n: c for n, c in real().items() if n != 3}
    with pytest.raises(TrackerError, match="#3 is an issue the listing of every card lacks"):
        _run(corpus, tracker).open()
    assert tracker.writes == made


#: How a refusal names a card as its create left it.
_BARE = ("still marked and open, linked to nothing the tool reads, off the board, with no "
         "comment")


def _resent(key, keep, states):
    """How the plan refuses ``key`` on two cards in the state a re-sent create leaves,
    ``states`` naming each, card ``keep`` the one to keep."""
    return (f"{key} is the key of 2 cards ({states}): the state a re-sent create leaves (GitHub "
            "refused a create for a rate limit though it had performed it, and the tool sent it "
            "again), copies of one create, each but the last made as its create left it.  Keep "
            f"the last made (plan#{keep}), the one the run went on with; delete the others on "
            "the web, and run again: the next run grades it as the item's card")


def _delete(tracker, number):
    """Card ``number`` deleted on the web: gone from ``tracker``, its number answering 410."""
    del tracker.cards_by_number[number], tracker.bodies[number]
    tracker.gone.add(number)


@pytest.mark.parametrize("stop", [False, True], ids=["went-on", "stopped-after-it"])
def test_a_re_sent_create_is_refused_offering_its_other_card_and_settled_by_deleting_it(
        corpus, stop):
    """F-1's create performed by GitHub, refused for a rate limit and sent again (as
    :mod:`_github` re-sends it): two cards of F-1, and the run goes on with the last made --
    to the board's re-plan, which refuses the key, or to its owner's link, blocked by its
    owner step (R-BAL395), which fails.  The next plan names each card's state and offers
    the others for deletion (review 2 of B2b-1, MEDIUM-A: the one machine state), and the
    board phase's stop names a create GitHub performed twice among its causes (review 5 of
    B2b-1, LOW-C); deleting the first, as it says, the next run finishes with nothing
    refused."""
    tracker = _empty()
    _run(corpus, tracker).history()
    create, block, sent = tracker.create, tracker.block, []

    def re_sent(kind, title, body, labels, milestone=None):
        if title.startswith("[F-1]") and not sent:
            sent.append(create(kind, title, body, labels, milestone=milestone))
        return create(kind, title, body, labels, milestone=milestone)

    def owned(number, blocker):
        if stop and sent and number == sent[0] + 1:
            raise GitHubError(502, "bad gateway")
        return block(number, blocker)

    tracker.create, tracker.block = re_sent, owned
    with pytest.raises((Stopped, GitHubError)) as raised:
        _run(corpus, tracker).open()
    assert isinstance(raised.value, GitHubError if stop else Stopped)
    first = sent[0]
    later = (f"plan#{first + 1} {_BARE}" if stop else
             f"plan#{first + 1} unmarked, blocked by "
             f"plan#{tracker.cards_by_number[first + 1].blocked_by[0]}")
    refusal = _resent("balance:F-1", first + 1, f"plan#{first} {_BARE}; {later}")
    assert corpus_plan(corpus, tracker).refusals == (refusal,)
    if not stop:
        assert str(raised.value) == (
            "the tracker, read again after every card's writes, is refused (a read that lags "
            "the run's writes, a create GitHub performed twice, or a person's edit; run "
            "`migrate open` again once GitHub's reads catch up, and settle what it still refuses "
            f"as each says, else by hand): {refusal}")
    _delete(tracker, first)
    tracker.create, tracker.block = create, block
    assert not _run(corpus, tracker).open()
    assert _settled(corpus, tracker)


def test_a_re_sent_create_read_lagging_its_writes_names_the_card_holding_them_to_keep(corpus):
    """A-2's create re-sent, and the run goes on with the last made: A-2a and A-2b linked
    under it, its mark removed.  The board's re-plan reads it before those writes, so every
    card of A-2 reads as its create left it; the refusal still names the last made to keep,
    and following it -- the other deleted -- the next run finishes with nothing refused and
    no link lost (review 3 of B2b-1, MEDIUM-1: "any one" deleted the card holding them)."""
    tracker = _empty()
    _run(corpus, tracker).history()
    create, sent = tracker.create, []

    def re_sent(kind, title, body, labels, milestone=None):
        if title.startswith("[A-2]") and not sent:
            sent.append(create(kind, title, body, labels, milestone=milestone))
        return create(kind, title, body, labels, milestone=milestone)

    real, reads = tracker.all_cards, []

    def lagging():
        cards = real()
        reads.append(True)
        if len(reads) == 2:
            last = sent[0] + 1
            for number, whole in list(cards.items()):
                card = whole.card
                if last in (number, card.parent):
                    card = replace(card, labels=tuple(sorted({*card.labels, FILING})),
                                   parent=None if card.parent == last else card.parent)
                cards[number] = replace(whole, card=card)
        return cards

    tracker.create, tracker.all_cards = re_sent, lagging
    with pytest.raises(Stopped) as raised:
        _run(corpus, tracker).open()
    first = sent[0]
    assert str(raised.value).endswith(_resent("balance:A-2", first + 1, (
        f"plan#{first} {_BARE}; plan#{first + 1} {_BARE}")))
    children = sorted(n for n, c in tracker.cards_by_number.items() if c.parent == first + 1)
    assert len(children) == 2
    tracker.create, tracker.all_cards = create, real
    _delete(tracker, first)
    assert not _run(corpus, tracker).open()
    assert _settled(corpus, tracker)
    assert sorted(n for n, c in tracker.cards_by_number.items()
                  if c.parent == first + 1) == children


def _stamped(tracker, time, events):
    """``tracker``'s and its board's writes each logged into ``events`` with the time it was
    sent; the second write moves the clock on by :data:`RENEW_SECONDS` once sent, as a write
    that waited long on GitHub would."""
    def stamp(name, real):
        def write(*args, **kwargs):
            events.append((name, time.at))
            answer = real(*args, **kwargs)
            if len([event for event in events if event[0] != "connect"]) == 2:
                time.at += RENEW_SECONDS
            return answer
        return write
    for owner, names in ((tracker, ("create_milestone", "create", "close_unmarked", "add_child",
                                    "block", "comment", "unmark")),
                         (tracker.board, ("add", "place"))):
        for name in names:
            setattr(owner, name, stamp(name, getattr(owner, name)))


@pytest.mark.parametrize("start", ["empty", "a-move-left"])
def test_every_write_is_paced_and_the_token_renewed_before_a_write(corpus, start):
    """One run of both phases from an empty tracker (every kind of write but a board move),
    and one finishing a marked question and moving a card: each write after the run's
    first waits :data:`PACE_SECONDS` after the one before it, and once a write leaves the
    token :data:`RENEW_SECONDS` old the run connects afresh before its next write, once."""
    if start == "empty":
        tracker = _empty()
    else:
        tracker, _ = _filed(corpus, marked=("balance:Q-1",))
        tracker.board.items[:2] = tracker.board.items[1::-1]
    time, events = _Time(), []

    def connect():
        events.append(("connect", time.at))
        return tracker

    _stamped(tracker, time, events)
    run = Run(connect, Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES),
              Clock(time.now, time.sleep))
    if start == "empty":
        assert not run.history()
    assert not run.open()
    names = [name for name, _ in events]
    writes = [at for name, at in events if name != "connect"]
    assert names.count("connect") == 2 and names[0] == "connect" and names[3] == "connect"
    assert len(writes) == run.made
    assert ({"place", "unmark"} if start != "empty" else
            {"create_milestone", "create", "close_unmarked", "add_child", "block", "comment",
             "unmark", "add"}) <= set(names)
    assert all(later - earlier >= PACE_SECONDS - 1e-9
               for earlier, later in zip(writes, writes[1:]))


def test_the_token_is_renewed_on_the_hour_before_a_read_and_not_before(corpus):
    """A run connects again before a read once :data:`RENEW_SECONDS` have passed since it
    last did, and not before; then not again until as long again has passed."""
    tracker, connects, time = _empty(), [], _Time()

    def connect():
        connects.append(time.at)
        return tracker

    run = Run(connect, Filing(corpus_items(corpus), corpus_texts(corpus), MILESTONES),
              Clock(time.now, time.sleep))
    time.at = RENEW_SECONDS - 1
    run.read()
    assert connects == [0.0]
    time.at = RENEW_SECONDS
    run.read()
    time.at = 2 * RENEW_SECONDS - 1
    run.read()
    assert connects == [0.0, RENEW_SECONDS]
    time.at = 2 * RENEW_SECONDS
    run.read()
    assert connects == [0.0, RENEW_SECONDS, 2 * RENEW_SECONDS]


def _shuffled(corpus):
    """The finished tracker with its board's first two cards swapped."""
    tracker, _ = _filed(corpus)
    tracker.board.items[:2] = tracker.board.items[1::-1]
    return tracker


def test_the_board_is_put_back_in_its_desired_order(corpus):
    """Two board cards out of order: one moves, and the board reads the desired order."""
    tracker = _shuffled(corpus)
    desired = desired_board(corpus_items(corpus), corpus_plan(corpus, tracker).numbers,
                            tracked(tracker))[0]
    assert not _run(corpus, tracker).open()
    assert tracker.board.items == desired
    assert [write[0] for write in tracker.writes] == ["create_milestone", "board_place"]


def test_a_move_the_board_does_not_show_stops_the_run(corpus):
    """The board lags a move past the wait: the run stops (resumable)."""
    tracker = _shuffled(corpus)
    tracker.board.lagging = True
    with pytest.raises(Stopped, match="the board does not show plan#"):
        _run(corpus, tracker).open()


def test_a_board_read_that_lags_an_add_is_waited_on_then_stops_the_run(corpus):
    """The board's read lacks a card the desired order places: read again every few
    seconds for :data:`_tracker.BOARD_WAIT_SECONDS`, then the run stops."""
    tracker, numbers = _filed(corpus)
    real = tracker.board.order
    tracker.board.order = lambda: [pair for pair in real() if pair[0] != numbers["balance:A-3"]]
    time = _Time()
    with pytest.raises(Stopped, match=rf"the board does not show \[{numbers['balance:A-3']}\]"):
        _run(corpus, tracker, time).open()
    assert sum(time.slept) == BOARD_WAIT_SECONDS


def test_a_board_read_that_lags_an_add_then_catches_up_is_waited_out_and_ordered(corpus):
    """The board's read lacks its last card for the run's first ten seconds, then shows it:
    the run waits :data:`_tracker.POLL_SECONDS` a time, reads again, and orders the board."""
    tracker, time = _shuffled(corpus), _Time()
    real, last = tracker.board.order, tracker.board.items[-1]
    tracker.board.order = lambda: (real() if time.at >= 2 * POLL_SECONDS
                                   else [pair for pair in real() if pair[0] != last])
    assert not _run(corpus, tracker, time).open()
    assert time.slept[:2] == [POLL_SECONDS, POLL_SECONDS]
    found = corpus_plan(corpus, tracker)
    assert [n for n, _ in real()] == desired_board(corpus_items(corpus), found.numbers,
                                                    tracked(tracker))[0]


def _mapped_block(corpus, monkeypatch):
    """The finished tracker numbered from #5 (#1 a deleted number), S-1 mapped onto its card
    and off the board, as a container is, and X-cx's own steps #2, #3 and #4 under it, on the
    board at its rank in that order: the block the desired order keeps in the order the
    board holds it."""
    tracker, numbers = filed_tracker(corpus, start=5)
    tracker.gone.add(1)
    mapped = numbers["salary:S-1"]
    monkeypatch.setitem(_migrate_source.MAPPED, "salary:S-1", mapped)
    at = tracker.board.items.index(mapped)
    for number in (2, 3, 4):
        tracker.add(number, title=f"[X-cx L{number}] A leaf", labels=("salary",),
                    parent=mapped, on_board=False)
    tracker.board.items[at:at + 1] = [2, 3, 4]
    return tracker


def _read_through(tracker, views):
    """``tracker``'s board read through ``views``: its n-th read (from 1) is
    ``views[n](order)`` where one is given, and every read after a move the board's own."""
    real, place, reads, moved = tracker.board.order, tracker.board.place, [], []

    def order():
        reads.append(True)
        view = views.get(len(reads))
        return real() if view is None or moved else view(real())

    def placed(item, after):
        moved.append(item)
        return place(item, after)

    tracker.board.order, tracker.board.place = order, placed


def _block(tracker):
    """The board's cards of the tracker's own, #2-#4, in the board's order."""
    return [number for number in tracker.board.items if number < 5]


def test_a_board_holding_cards_below_a_card_filed_already_is_ordered_once(corpus,
                                                                          monkeypatch):
    """The whole board reversed, the block of X-cx's own cards below S-1 mapped with it: the
    run orders the board, the block standing at S-1's rank in the order the board held it,
    and a second run moves nothing (review 2 of B2b-1, LOW-A: no run test set MAPPED; its
    P6)."""
    tracker = _mapped_block(corpus, monkeypatch)
    tracker.board.items.reverse()
    assert not _run(corpus, tracker).open()
    assert _block(tracker) == [4, 3, 2] and _settled(corpus, tracker)
    made = list(tracker.writes)
    assert not _run(corpus, tracker).open() and tracker.writes == made


@pytest.mark.parametrize("views, slept", [
    ({3: lambda order: [pair for pair in order if pair[0] != 3]}, [POLL_SECONDS]),
    ({2: lambda order: sorted(order, key=lambda pair: pair[0] == 3)}, []),
    ({4: lambda order: [pair for pair in order if pair[0] not in (7, 8)]}, []),
], ids=["the-wait-read-lacks-a-block-card", "the-re-plans-read-holds-another-block-order",
        "a-read-after-the-wait-read-lacks-a-moved-card"])
def test_the_board_is_ordered_on_the_one_read_that_holds_every_card(corpus, monkeypatch,
                                                                    views, slept):
    """The block below S-1 mapped held as (3, 2, 4), and A-2b's and A-3's cards (#7, #8)
    swapped; one board read lags (review 2 of B2b-1, LOW-A).  The read the board phase waits
    on lacking a block card is waited out, never ordered without it (its P8, which left the
    block reordered for good); the re-plan's read holding the block in an older order does
    not reorder it; and a read after the one waited on, lacking the cards it moves, is never
    read for the moves or the ids.  Each way the block keeps its order and the board ends
    desired."""
    tracker = _mapped_block(corpus, monkeypatch)
    assert tracker.board.items == [6, 7, 8, 2, 3, 4, 12, 13, 15]
    tracker.board.items[:] = [6, 8, 7, 3, 2, 4, 12, 13, 15]
    _read_through(tracker, views)
    time = _Time()
    assert not _run(corpus, tracker, time).open()
    assert time.slept == slept and _block(tracker) == [3, 2, 4] and _settled(corpus, tracker)


def test_a_card_below_a_card_filed_already_the_re_plans_read_lacks_stops_the_run(
        corpus, monkeypatch):
    """The re-plan's read of the board lacks a block card the later read holds: the desired
    order places only the cards that read showed, so the board cannot be ordered and the
    run stops before any move (the next run reads it whole)."""
    tracker = _mapped_block(corpus, monkeypatch)
    _read_through(tracker, {2: lambda order: [pair for pair in order if pair[0] != 3]})
    with pytest.raises(Stopped, match="the board cannot be ordered: the board's present and "
                                      "desired orders hold different cards"):
        _run(corpus, tracker).open()
    assert not [write for write in tracker.writes if write[0] == "board_place"]


def test_a_board_read_after_the_moves_out_of_order_stops_the_run(corpus):
    """Every move shown where it was put, but the board read last is not in the desired
    order (a read that lags a move, or a person's drag): the run stops, "run again"."""
    tracker = _shuffled(corpus)
    real, before = tracker.board.order, tracker.board.order()
    tracker.board.order = lambda: before if any(
        write[0] == "board_place" for write in tracker.writes) else real()
    with pytest.raises(Stopped, match="after its moves, not"):
        _run(corpus, tracker).open()


def _main(corpus, code, tmp_path, argv, tracker):
    """``migrate`` run with ``argv`` and the corpus's complete input file, connecting to
    ``tracker``: its exit status, and each place it was asked to connect to."""
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    time, places = _Time(), []
    status = migrate.main([*argv, "--input", str(path)], read=lambda _root, _commit: corpus,
                          root=code, connect=lambda place: places.append(place) or tracker,
                          clock=Clock(time.now, time.sleep))
    return status, places


@pytest.mark.parametrize("target", [("plan", "saltyreformed-labs", "shekel-plan"),
                                    ("rehearsal", "saltyreformed-labs", "shekel-plan-rehearsal")])
def test_a_writing_phase_writes_the_tracker_its_target_names(corpus, code, tmp_path, capsys,
                                                             target):
    """``--target`` picks the place, graded on its exact owner and repository-name tokens
    (``shekel-plan`` is a prefix of the rehearsal's), and each phase prints its writes and
    the count."""
    status, places = _main(corpus, code, tmp_path, ["history", "--target", target[0]],
                           _empty())
    assert (status, [(each.owner, each.name) for each in places]) == (0, [target[1:]])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "history: create balance:R-A: plan#3"
    assert out[-1] == "history: done, 4 write(s) made"


@pytest.mark.parametrize("command", ["history", "open"])
def test_a_writing_phase_has_no_default_target(corpus, code, tmp_path, command):
    """Without ``--target`` the command line is refused (exit 2), and nothing connects."""
    connected = []
    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    with pytest.raises(SystemExit) as stop:
        migrate.main([command, "--input", str(path)], read=lambda _root, _commit: corpus,
                     root=code, connect=connected.append)
    assert (stop.value.code, connected) == (2, [])


def test_a_refusal_of_the_input_file_connects_to_nothing(corpus, code, tmp_path, capsys):
    """An input file missing a name: refused before any tracker is reached (exit 1)."""
    path = tmp_path / "input.json"
    data = complete_input(corpus)
    del data["names"]["balance:A-3"]
    path.write_text(json.dumps(data))
    places = []
    status = migrate.main(["open", "--target", "rehearsal", "--input", str(path)],
                          read=lambda _root, _commit: corpus, root=code,
                          connect=places.append)
    assert (status, places) == (1, [])
    assert capsys.readouterr().out.splitlines()[-1] == "1 refusal(s); nothing written"


def test_a_stopped_run_exits_3_and_the_next_run_finishes(corpus, code, tmp_path, capsys):
    """A write that fails stops the run with exit 3 and a ``stopped:`` line; the same
    command run again finishes (exit 0)."""
    tracker = _empty()
    FailOnce(tracker, "close_unmarked")
    assert _main(corpus, code, tmp_path, ["history", "--target", "rehearsal"], tracker)[0] == 3
    assert capsys.readouterr().err.startswith("stopped: bad gateway")
    assert _main(corpus, code, tmp_path, ["history", "--target", "rehearsal"], tracker)[0] == 0
    found = corpus_plan(corpus, tracker)
    assert (found.refusals, found.history) == ((), ())


@pytest.mark.parametrize("fault", ["numbering", "connection-reset", "key-unreadable",
                                   "stopped"])
def test_a_run_that_cannot_read_or_reach_the_tracker_exits_3(corpus, code, tmp_path, capsys,
                                                             fault):
    """The numbering probe refusing the read (:class:`_tracker.TrackerError`), a connection
    reset mid-write (:class:`requests.RequestException`), or the App's key unreadable
    (:class:`OSError`), or the run's own stop (:class:`Stopped`: here the board read lacks a
    card for :data:`_tracker.BOARD_WAIT_SECONDS`): each stops the run with exit 3 and a
    ``stopped:`` line."""
    tracker = _empty()
    if fault == "stopped":
        tracker, _ = _filed(corpus)
        real = tracker.board.order
        tracker.board.order = lambda: real()[1:]
    if fault == "numbering":
        _run(corpus, tracker).history()
        real = tracker.all_cards
        tracker.all_cards = lambda: {n: c for n, c in real().items() if n != 3}
    elif fault == "connection-reset":
        def reset(*_args, **_kwargs):
            raise requests.ConnectionError("connection reset")
        tracker.create = reset

    def connect(_place):
        if fault == "key-unreadable":
            raise OSError("no such file: the App's key")
        return tracker

    path = tmp_path / "input.json"
    path.write_text(json.dumps(complete_input(corpus)))
    time = _Time()
    status = migrate.main(["open" if fault in ("numbering", "stopped") else "history", "--target",
                           "rehearsal", "--input", str(path)],
                          read=lambda _root, _commit: corpus, root=code, connect=connect,
                          clock=Clock(time.now, time.sleep))
    assert status == 3
    assert capsys.readouterr().err.startswith("stopped: ")


def test_a_plan_that_refuses_anything_exits_1_and_writes_nothing(corpus, code, tmp_path, capsys):
    """A card no item claims, numbered above the migration's first: ``open`` reads, refuses,
    writes nothing and exits 1 with the plan's refusal on a ``REFUSED:`` line."""
    tracker, numbers = _filed(corpus)
    stray = max(numbers.values()) + 1
    tracker.add(stray, title="A card of its own", on_board=False)
    assert _main(corpus, code, tmp_path, ["open", "--target", "rehearsal"], tracker)[0] == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"REFUSED: plan#{stray} ('A card of its own') is no item's card")
    assert out[-1] == "1 refusal(s); nothing written" and not tracker.writes[1:]
