"""X-cx's migration, its RUN: the plan's writes made on a tracker, as quill's App (the L7
design's draft 4 s.4).

:mod:`_migrate_plan` says what is still to write; this makes it, write by write, on the
tracker the run is aimed at (``migrate history|open --target plan|rehearsal``, with no
default).  Nothing here decides what a card says or where it goes.

**Every run starts by reading the whole tracker** (:meth:`Run.read`): every card whole,
the numbering probe (:func:`_tracker.numbering`: a card the listing lacks stops the run,
so a create whose answer was lost is found, never made again), the milestones and the
board.  Then the plan, handed EVERY item, rulings included, in both phases; a plan that
refuses anything writes nothing.

**The two obligations the plan's number rule rests on** (:mod:`_migrate_plan`'s
docstring, "What this cannot see"): HISTORY's writes are made before any of the
freeze's -- :meth:`Run.open` refuses while the plan holds any -- so the migration's
first cards are rulings; and :func:`_migrate_plan.plan` is handed every item, so no
item's card reads as no item's.

**A write is addressed by KEY** (:class:`_migrate_plan.Write`) and takes its card's
number from the run's read or from the create that answered in this run.  A run STOPS at
the first write GitHub refuses or does not answer (the tracker's own error), or that the
run finds it may not make (:class:`Stopped`), and makes nothing after it: what it made
stands, and running the same command again resumes it, its read finding a write that
landed with its answer lost.

**A by-number re-read precedes each write a listing that lags the run before would
send again** (draft 4 s.4), and the write is not sent when the re-read shows it made: a
parent link and a blocked-by link (both cards), a question's As filed comment (its
comments), a board add and a mark's removal (the card).  A card found under another
parent, in the tracker or outside it, stops its parent link at the re-read, and a card gone
stops any write that re-reads it (the comment's through GitHub's NOT_FOUND error,
:meth:`_tracker.Tracker.comments`: reasoned, unmeasured); any other edit since the read is
left to the next plan, which refuses what it grades: the board phase's re-plan, after the
writes, or the next run's.  The rest take none: a create the listing lags is found by the
numbering probe; a milestone the listing lags is sent again, and fails closed, refused by
GitHub (reasoned, unmeasured: the rehearsal's to measure) or by the next read's refusal of
two of one title; and a ruling's close with its mark removed, sent again, leaves it as it
was.

**Paced, renewed.**  Each write after a run's first is made at least :data:`PACE_SECONDS`
after the run's last, GitHub's documented 500 content-creating requests an hour with every
write counted until the rehearsal measures which count (draft 4 s.4; a run started right
after another stopped is not paced against it); its rate-limit refusals are still waited
out by :class:`_github.GitHub`.  The App's installation token lives an hour, so the run
connects afresh :data:`RENEW_SECONDS` after its last connect, before the next read or
write.  One call that runs past the token's life -- a request's rate-limit waits may
take up to an hour (:data:`_github.RATE_LIMIT_BUDGET_SECONDS`) -- is answered 401, and
the run stops; the next run connects afresh.

**The board** is ordered once every card's writes are made (:meth:`Run.open`): the
tracker is read and planned again, and anything still to write or refused stops the run
(a read that lags the run's own writes, a create GitHub performed twice, or a person's
edit; run ``migrate open`` again once GitHub's reads catch up, and settle what it still
refuses as each says, else by hand); then the board is read until it holds every card the
desired order places on that read of the tracker, for :data:`_tracker.BOARD_WAIT_SECONDS` at
most, else the run stops, so a board read that lags one of them is waited on, never ordered
without it.  From that one
board read come the order of the cards below a card filed already, the moves and the
board items' ids, and a card it holds beyond them stops the run; only the cards outside
the longest common subsequence move (:func:`_migrate_plan.moves`), each a write, each read
back until the board shows it; and last the board is read once more, and an order other
than the desired one stops the run.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from tools.quill._migrate_bodies import Built
from tools.quill._migrate_plan import (
    BLOCK,
    BOARD_ADD,
    CLOSE_UNMARKED,
    COMMENT,
    CREATE,
    LINK,
    MILESTONE,
    UNMARK,
    Plan,
    Tracked,
    Write,
    as_filed_comments,
    desired_board,
    milestone_title,
    moves,
    plan,
)
from tools.quill._migrate_source import Item
from tools.quill._tracker import BOARD_WAIT_SECONDS, Card, Tracker, numbering, read_until
from tools.quill.setup_tracker import FILING

#: GitHub's documented cap on content-creating requests, an hour ("no more than 500"; its
#: 80 a minute is met too), counting every write until the rehearsal measures which count.
WRITES_AN_HOUR = 500
#: The least time between two writes.
PACE_SECONDS = 3600 / WRITES_AN_HOUR
#: How long after a connect the run connects again: ten minutes inside the hour an
#: installation token lives.
RENEW_SECONDS = 50 * 60


class Stopped(RuntimeError):
    """A run stopped part way: what it made stands, and the same command run again
    resumes it."""


@dataclass(frozen=True)
class Clock:
    """The run's time: ``now`` in seconds, and ``sleep``; tests pass ones that do not wait."""

    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class Filing:
    """What a run files: every item, as :func:`_migrate_source.read_source` orders them;
    each one's card text as filed, by key (``migrate.prepare``: built, then its bare
    ``#N`` written); and each outcome's milestone, ``(title, description)``, as the input
    file gives it."""

    items: tuple[Item, ...]
    built: Mapping[str, Built]
    milestones: Mapping[str, tuple[str, str]]


def read_tracked(tracker: Tracker) -> Tracked:
    """``tracker`` as the migration reads it, a run, ``check --target`` and ``verify`` alike:
    every card whole, checked against the numbering probe; its milestones; its board's
    order.

    Raises:
        TrackerError: When the probe finds a card the listing lacks, or another number it
            refuses (:func:`_tracker.numbering`); or two milestones share a title.
    """
    cards = tracker.all_cards()
    numbering(cards, tracker.number_state)
    return Tracked(cards, tracker.milestones(),
                   tuple(number for number, _ in tracker.board.order()), tracker.app_login)


class _Connection:
    """The tracker a run reaches: connected afresh once its token is :data:`RENEW_SECONDS`
    old, and its writes paced :data:`PACE_SECONDS` apart."""

    def __init__(self, connect: Callable[[], Tracker], clock: Clock) -> None:
        """Connect through ``connect``, timed by ``clock``."""
        self._connect, self.clock = connect, clock
        self._tracker = connect()
        self._connected = clock.now()
        self._last_write: float | None = None

    @property
    def tracker(self) -> Tracker:
        """The tracker, through a token connected afresh once the last is
        :data:`RENEW_SECONDS` old."""
        if self.clock.now() - self._connected >= RENEW_SECONDS:
            self._tracker = self._connect()
            self._connected = self.clock.now()
        return self._tracker

    def paced(self) -> Tracker:
        """The tracker to write on, once :data:`PACE_SECONDS` have passed since the last
        write."""
        if self._last_write is not None:
            wait = self._last_write + PACE_SECONDS - self.clock.now()
            if wait > 0:
                self.clock.sleep(wait)
        tracker = self.tracker
        self._last_write = self.clock.now()
        return tracker


class Run:
    """One run of a migration phase on the tracker ``connect`` reaches."""

    def __init__(self, connect: Callable[[], Tracker], filing: Filing,
                 clock: Clock = Clock()) -> None:
        """Connect through ``connect`` (again each :data:`RENEW_SECONDS`), to file
        ``filing``, timed by ``clock``; :attr:`made` counts the writes sent."""
        self._at = _Connection(connect, clock)
        self.filing = filing
        self._items = {item.key: item for item in filing.items}
        self.made = 0

    @property
    def tracker(self) -> Tracker:
        """The tracker this run reaches, before it reads or writes anything: what GitHub's
        renderer grades the cards' numbers through (``migrate.run``)."""
        return self._at.tracker

    def read(self) -> Tracked:
        """The tracker as this run reads it (:func:`read_tracked`)."""
        return read_tracked(self._at.tracker)

    def _plan(self, tracked: Tracked) -> Plan:
        """The plan for EVERY item on ``tracked``, rulings included."""
        return plan(self.filing.items, self.filing.built, self.filing.milestones, tracked)

    def history(self) -> list[str]:
        """File every ruling without a card (draft 4 s.4's HISTORY, first); the plan's
        refusals, with nothing written, or none."""
        tracked = self.read()
        found = self._plan(tracked)
        if found.refusals:
            return list(found.refusals)
        self._make("history", found.history, found.numbers, tracked)
        return []

    def open(self) -> list[str]:
        """File every other card, then order the board (draft 4 s.4's FREEZE); the
        refusals, with nothing written, or none.  Refused while HISTORY is unfinished."""
        tracked = self.read()
        found = self._plan(tracked)
        if found.refusals:
            return list(found.refusals)
        if found.history:
            return [f"HISTORY is unfinished, {len(found.history)} write(s) still to make: run "
                    "`migrate history` first, so every ruling is filed before any other card "
                    "(what the number rule cannot see rests on it)"]
        self._make("open", found.freeze, found.numbers, tracked)
        self._order_board()
        return []

    # -- the writes ------------------------------------------------------------

    def _make(self, phase: str, writes: Sequence[Write], numbers: Mapping[str, int],
              tracked: Tracked) -> None:
        """Make ``writes`` in order, each printed as it is made."""
        numbers = dict(numbers)
        milestones = {title: held.number for title, held in tracked.milestones.items()}
        makers = {MILESTONE: self._milestone, CREATE: self._create,
                  CLOSE_UNMARKED: self._close_unmarked, LINK: self._link, BLOCK: self._block,
                  COMMENT: self._comment, BOARD_ADD: self._board_add, UNMARK: self._unmark}
        for write in writes:
            said = makers[write.what](write, numbers, milestones)
            print(f"{phase}: {write}" + (f": {said}" if said else ""))

    def _milestone(self, write: Write, _numbers: dict[str, int],
                   milestones: dict[str, int]) -> str:
        """Make an outcome's milestone."""
        title, description = self.filing.milestones[write.key]
        milestones[title] = self._at.paced().create_milestone(title, description)
        self.made += 1
        return f"milestone #{milestones[title]}"

    def _create(self, write: Write, numbers: dict[str, int], milestones: dict[str, int]) -> str:
        """File an item's card, born marked, in its outcome's milestone."""
        item, built = self._items[write.key], self.filing.built[write.key]
        title = milestone_title(item, self.filing.milestones)
        milestone = None if title is None else milestones[title]
        numbers[write.key] = self._at.paced().create(item.kind, built.title, built.body,
                                                  item.labels, milestone=milestone)
        self.made += 1
        return f"plan#{numbers[write.key]}"

    def _close_unmarked(self, write: Write, numbers: dict[str, int],
                        _milestones: dict[str, int]) -> str:
        """Close a ruling's card with its mark removed, in one write: its labels are its
        item's, which the plan refuses a card's labels to differ from."""
        self._at.paced().close_unmarked(numbers[write.key], self._items[write.key].labels)
        self.made += 1
        return ""

    def _link(self, write: Write, numbers: dict[str, int], _milestones: dict[str, int]) -> str:
        """Link a card under its parent, unless the re-read shows it linked.

        Raises:
            Stopped: When the re-read shows it under another parent, in the tracker or
                outside it.
        """
        child, parent = self._both(numbers[write.key], numbers[write.other])
        if child.parent == parent.number:
            return "linked already"
        under = [f"plan#{child.parent}"] if child.parent is not None else []
        under += [f"{link.issue}, outside the tracker" for link in child.outside
                  if link.what == "parent"]
        if under:
            raise Stopped(f"plan#{child.number} is a sub-issue of {under[0]} now, not "
                          f"plan#{parent.number}: a person's edit inside the freeze")
        self._at.paced().add_child(parent.number, child)
        self.made += 1
        return ""

    def _block(self, write: Write, numbers: dict[str, int], _milestones: dict[str, int]) -> str:
        """Record a card blocked by another, unless the re-read shows it recorded."""
        card, blocker = self._both(numbers[write.key], numbers[write.other])
        if blocker.number in card.blocked_by:
            return "blocked already"
        self._at.paced().block(card.number, blocker)
        self.made += 1
        return ""

    def _comment(self, write: Write, numbers: dict[str, int],
                 _milestones: dict[str, int]) -> str:
        """Write a question's As filed comment, unless the re-read finds it written."""
        number, tracker = numbers[write.key], self._at.tracker
        if as_filed_comments(tracker.comments(number), tracker.app_login):
            return "commented already"
        self._at.paced().comment(number, self.filing.built[write.key].comment)
        self.made += 1
        return ""

    def _board_add(self, write: Write, numbers: dict[str, int],
                   _milestones: dict[str, int]) -> str:
        """Put a card on the board, unless the re-read shows it there."""
        card, = self._both(numbers[write.key])
        if card.board_item is not None:
            return "on the board already"
        self._at.paced().board.add(card)
        self.made += 1
        return ""

    def _unmark(self, write: Write, numbers: dict[str, int], _milestones: dict[str, int]) -> str:
        """Remove a card's filing mark, its filing's last write, unless the re-read shows it
        removed."""
        card, = self._both(numbers[write.key])
        if FILING not in card.labels:
            return "unmarked already"
        self._at.paced().unmark(card.number)
        self.made += 1
        return ""

    def _both(self, *numbers: int) -> list[Card]:
        """The cards ``numbers``, re-read by number now.

        Raises:
            Stopped: When one is not on the tracker.
        """
        found = self._at.tracker.cards(numbers)
        gone = [number for number in numbers if number not in found]
        if gone:
            raise Stopped(f"{', '.join(f'plan#{n}' for n in gone)} is not on the tracker: "
                          "deleted by hand inside the freeze?")
        return [found[number] for number in numbers]

    # -- the board -------------------------------------------------------------

    def _order_board(self) -> None:
        """Put the board in its desired order once the tracker shows every card's writes.

        Raises:
            Stopped: When the tracker read again has writes left or refuses anything; the
                board does not hold every card the desired order places on that read within
                :data:`_tracker.BOARD_WAIT_SECONDS`; the board cannot be ordered, holding a
                card that read's desired order does not; a move is not shown where it was
                put; or the board read after the moves is not in the desired order.
        """
        tracked = self.read()
        found = self._plan(tracked)
        if found.refusals:
            raise Stopped("the tracker, read again after every card's writes, is refused (a "
                          "read that lags the run's writes, a create GitHub performed twice, or "
                          "a person's edit; run `migrate open` again once GitHub's reads catch "
                          "up, and settle what it still refuses as each says, else by hand): "
                          f"{'; '.join(found.refusals)}")
        if found.history or found.freeze:
            raise Stopped("the tracker does not show every write yet "
                          f"({len(found.history) + len(found.freeze)} to make on this read): "
                          "run `migrate open` again")
        placed = set(desired_board(self.filing.items, found.numbers, tracked)[0])

        def board() -> list[tuple[int, str]]:
            """The board's order, each read through a token renewed once it is due."""
            return self._at.tracker.board.order()

        order, shown = read_until(board, lambda read: placed <= {number for number, _ in read},
                                  self._at.clock.sleep)
        if not shown:
            missing = sorted(placed - {number for number, _ in order})
            raise Stopped(f"the board does not show {missing} after {BOARD_WAIT_SECONDS} s: "
                          "run `migrate open` again")
        desired = [number for number in self._desired(found.numbers, tracked, order)
                   if number in placed]
        try:
            steps = moves([number for number, _ in order], desired)
        except ValueError as error:
            raise Stopped(f"the board cannot be ordered: {error}") from error
        items = dict(order)
        for number, after in steps:
            if not self._at.paced().board.place(items[number],
                                             None if after is None else items[after]):
                raise Stopped(f"the board does not show plan#{number} moved after "
                              f"{'the top' if after is None else f'plan#{after}'}: run "
                              "`migrate open` again")
            self.made += 1
            print(f"open: board move plan#{number} -> "
                  f"{'the top' if after is None else f'after plan#{after}'}")
        shown = [number for number, _ in self._at.tracker.board.order()]
        if shown != desired:
            raise Stopped(f"the board reads {shown} after its moves, not {desired}: run "
                          "`migrate open` again")

    def _desired(self, numbers: Mapping[str, int], tracked: Tracked,
                 order: Sequence[tuple[int, str]]) -> list[int]:
        """The board's desired order on ``order``, a read of it (:func:`desired_board`): the
        cards below a card filed already in the order that read holds them.  Its refusals
        cannot stand here: an item with a place and no card yet is a create still to make,
        which stopped the run already, and a board card the order has no place for is
        absent from it, so :func:`moves` refuses the two orders' differing cards."""
        board = tuple(number for number, _ in order)
        return desired_board(self.filing.items, numbers, replace(tracked, board=board))[0]
