"""How far a bank line sits from a row the app holds, and whether they may pair.

**The day rules, stated ONCE for both tiers of the matcher** (plan step
``bank_import:X-f6d-1``).  :mod:`._propose` scores an EXACT figure and
:mod:`._near` scores a near one, and both have to answer the same two questions
about a candidate pair: *may these be paired at all*, and *how far apart does
the app believe they are*.  This module owns both answers.

**Splitting them out is this package's own lesson rather than a tidy-up.**  Two
spellings of one bound is what finding **N-322** was, and the two passes over
one bucketing before it; the module docstrings on either side of this one say
in as many words that a bound only one pass applies *is not a bound, it is a
disagreement*.  A second tier that re-derived the day window would be exactly
that, one measurement later.

Services-boundary discipline: plain data in, plain data out.
"""

from __future__ import annotations

from datetime import date, timedelta

from ._offers import BankLine
from ._subjects import CandidateRow, RowKind


#: How far a recorded settle day may sit from the bank's posted day and still
#: be proposed as the same movement.  **Measured rather than chosen**: on the
#: developer's own statement 58 lines pair uniquely at 14 days with 4
#: ambiguous, against 55 / 1 at 7 and 50 / 21 at 30.  The value is the knee of
#: that curve -- far enough to catch a settle recorded a week late, near enough
#: that a monthly commitment cannot reach its neighbour.
DAY_WINDOW: int = 14


def day_distance(row: CandidateRow, posted_on: date) -> "int | None":
    """Return how far *row*'s recorded day sits from *posted_on*.

    Args:
        row: The candidate.
        posted_on: The bank's day.

    Returns:
        The absolute distance in days, or ``None`` for a row carrying no day at
        all -- there is no distance from "never observed to have moved", and
        treating one as zero would rank an unsettled row above a settled one
        that is genuinely a day out.
    """
    if row.settled_on is None:
        return None
    return abs((row.settled_on - posted_on).days)


def within_window(row: CandidateRow, line: BankLine) -> bool:
    """Return whether *row* may be paired with *line* at all.

    Two independent tests, and both are about THIS pair rather than about the
    row: a distance the owner would recognise, and a pairing the write door can
    actually carry out.

    **The distance is measured from the row's WINDOW**
    (:attr:`~._subjects.CandidateRow.expected_window`) -- the days the app
    believes that money moved between -- widened by :data:`DAY_WINDOW` at each
    end.  A row with no window at all is refused rather than admitted: see the
    comment on that branch.  For most settled rows and for an unsettled purchase
    the window is one day, so the test is exactly the
    ``abs(anchor - posted_on) <= DAY_WINDOW`` it has always been; for an
    unsettled TRANSACTION it is that row's whole pay period, which is the whole
    of what the app asserts about when the money moves; and for a PURCHASE the
    reconcile panel ticked it is the span from its purchase day to the day the
    balance was asserted, which is the whole of what the app knows there.

    **An unsettled transaction had NO distance test at all until plan step
    X-f6a-3c, and that was finding N-312.**  The reasoning was that the bank is
    the only evidence such a row has, so there is nothing to be far from -- true
    of the row's CASH clock and false of the row, which is budgeted in a
    paycheck.  Measured on the developer's own clone: removing the settled
    partner from an amount group makes **44 of the statement's own lines** pair
    with a projection budgeted 48 to 148 days later, the worst a 2026-04-01
    line taking a mortgage transfer budgeted 2026-08-27.  The arm the old
    reasoning protected is untouched: every one of the 51 rows a proposal
    settles today is a PURCHASE, and 0 proposals name an unsettled transaction
    on either the first pass or the second.  **That census predates the NEAR
    tier** (plan step ``bank_import:X-f6d-1``), which is a third pass and does
    propose one: the count is a measurement of the two passes it was taken
    over, not a property of this module.

    **The FLOOR, and forgetting one was a real defect.**  A purchase cannot
    reach the bank before it was made, so ``entry_service.update_entry``'s
    ``_reject_settled_before_purchase`` refuses that pairing at the door -- and
    a proposer blind to ``purchased_on`` renders an Accept button that can
    never succeed.  Measured on the developer's own clone: 23 (line, undated
    purchase) pairs where the line posted BEFORE the purchase was made.  Found
    by adversarial security review 2026-08-17.

    **What plan step X-f6a-3a changed is that the floor is now SATISFIABLE
    rather than merely refusing**, because a match may move the purchase day
    (ruling **R-FW**).  The pairing is legal when the app's own purchase day
    already sits on or before the bank's, OR when the day the match would write
    does -- so a purchase the owner recorded days late is a CORRECTION to offer
    instead of a line the review screen leaves looking unexplained.  It is the
    same measurement that forced the ruling: on the developer's own statement
    14 unexplained lines worth `$1,028.66` are an exact amount at the same
    merchant as an unmatched purchase, refused only because the recorded
    purchase day was 1 to 5 days after the bank posted it -- and the door
    X-f6a-3b builds would have offered to record every one of them a SECOND
    time.

    **The floor is asked of the LINE, not of a bare day, and that is the bug
    fix rather than a signature tidy-up.**  ``_one_to_one`` kept a row that was
    legal against ANY line sharing its amount, and :func:`_assign`'s undated arm
    then paired it with whichever line was free -- so a per-GROUP survival test
    stood in for a per-PAIR legality test.  Measured on the developer's own
    clone at HEAD: 3 offered proposals whose Accept could never succeed, the
    worst pairing a line posted 2026-06-01 with a purchase made 2026-07-27.

    Args:
        row: The candidate.
        line: The bank line it would be paired with.

    Returns:
        Whether to consider the pairing at all.
    """
    # **The second clause is :attr:`~._offers.BankLine.states_impossible_days`,
    # asked rather than respelled** (plan step ``bank_import:X-f6a-3d``): the
    # same fact -- the bank dates this line MADE after it POSTED -- decides two
    # different things, and it was written out here and again in the reader
    # that declines to OFFER such a line as a purchase.  Two spellings of one
    # predicate on a money screen is this arc's own root cause 1, and a
    # docstring one module over claimed they were already one.  Found by two
    # adversarial reviews 2026-08-19.
    if (
        row.kind is RowKind.PURCHASE
        and row.expected_on is not None
        and line.posted_on < row.expected_on
        and line.states_impossible_days
    ):
        return False
    window = row.expected_window
    if window is None:
        # A row the app can date no way at all is not offerable.  It is
        # unconstructible through either candidate arm -- a purchase's day is
        # a NOT NULL column and a transaction's constructor declines a row
        # whose period the calendar lacks -- and the answer is stated
        # rather than left to a default because the OTHER reading, "no window
        # means no bound", is finding N-312 itself.  :func:`_day_buckets`
        # declines the same row for the same reason, so the two passes cannot
        # disagree about what an undatable row is worth.
        return False
    first, last = window
    slack = timedelta(days=DAY_WINDOW)
    return first - slack <= line.posted_on <= last + slack


#: What the pass SAYS about a line whose exact counterpart the WINDOW refused.
#:
#: Written beside the bound that produced it, for the reason
#: :data:`~._near._DECLINED_SENTENCES` states: a reader composing its own would
#: be a second statement of what this module decided.
EXACT_BUT_TOO_FAR: str = (
    "one of your own rows is for exactly this figure, and it is dated too far "
    "away for the app to pair them"
)


def exactly_matched_but_outside_the_window(
    lines: "list[BankLine]", rows: "list[CandidateRow]",
) -> "dict[int, str]":
    """Return the lines an EXACT row exists for and the window refused.

    Plan step ``bank_import:X-ge-1``.  **The one bound this module applies that
    nothing published**, and publishing it is finding **N-322**'s own rule --
    which this module's header already states in as many words: *a bound only
    one pass applies is not a bound, it is a disagreement*.

    **An exact figure is the strongest claim the matcher has**, which is why
    the exact tier needs no corroboration for it (:func:`~._near
    .names_the_merchant` states that argument from the other side).  So a line
    whose figure an unclaimed row matches TO THE CENT, refused only because the
    two are dated too far apart, is precisely a line the pass had a reason to
    look harder at -- and until this step it was indistinguishable from a line
    with no candidate at all.  Under a human tick that cost nothing, because
    the person reading the screen was the check; ruling **R-GH**'s automatic
    door removed the person.

    **Measured on the developer's own books 2026-08-26**, over the 80 lines a
    standing rule would file: 5 lines, `$151.77` -- and one of them is real.
    An `Apple` line of `-$21.34` posted 2026-07-29 sits **15 days** from his
    own `Apple Music` row of `$21.34`, one day past :data:`DAY_WINDOW`, so the
    automatic door would have recorded that subscription a second time.

    **It reports rather than pairs, and the two are different jobs.**  Widening
    the window would change what the app PROPOSES, which is a claim about
    evidence that ruling R-GD(b) settled by measurement; this changes only what
    the app SAYS it declined, which is the conservative direction and needs no
    such licence.

    Args:
        lines: The bank lines no proposal explains.
        rows: The candidate rows no proposal claims.

    Returns:
        ``{line_id: sentence}``, one entry per line, empty when the window
        refused no exact pair.  **The FLOOR counts as the window here**:
        :func:`within_window` refuses a purchase the line predates for a
        different reason, and both are this module declining a pair it had a
        figure to consider.
    """
    declined: "dict[int, str]" = {}
    for line in lines:
        for row in rows:
            if row.cash_amount != line.amount:
                continue
            if within_window(row, line):
                continue
            declined[line.line_id] = EXACT_BUT_TOO_FAR
            break
    return declined


def signed_days_outside(window: "tuple[date, date]", day: date) -> int:
    """Return how far *day* falls outside *window*, and on WHICH side.

    Negative when *day* PRECEDES the window, positive when it FOLLOWS it,
    ``0`` inside.  **The one arithmetic under two readers** (plan step
    ``bank_import:X-gz``): the matcher scores a pairing by the magnitude
    (:func:`days_outside`) and the MATCH pane tells the reviewer the direction
    -- *the bank posted it 2 days after that pay period* -- and a second
    subtraction spelled for the sentence could disagree with the one that
    ranked the row.

    **Total over its declared domain**, which is why it takes the window
    rather than the row: a row that has none is refused by
    :func:`within_window` before any distance is asked for, and a helper
    correct only because its caller pre-filters is a contract no reader can
    see.

    Args:
        window: A ``(first, last)`` span of days, both ends inclusive -- a
            row's :attr:`~._subjects.CandidateRow.expected_window`, or the
            budget clock the pane prints.
        day: The day the bank posted the line.

    Returns:
        The signed distance in days, ``0`` inside.
    """
    first, last = window
    if day < first:
        return -(first - day).days
    if day > last:
        return (day - last).days
    return 0


def days_outside(window: "tuple[date, date]", day: date) -> int:
    """Return how far *day* falls OUTSIDE *window*, in days.

    ``0`` when it falls inside, which is what makes this a distance rather
    than a signed offset: a row whose own paycheck covers the bank's day is
    not "near", it is right, and every such row ties.  The magnitude of
    :func:`signed_days_outside`, which holds the arithmetic.

    Args:
        window: The row's :attr:`~._subjects.CandidateRow.expected_window`.
        day: The day the bank posted the line.

    Returns:
        The distance in days, ``0`` inside.
    """
    return abs(signed_days_outside(window, day))
