"""
Shekel Budget App -- Calendar Date Utilities

Pure date-arithmetic helpers shared across the projection services, plus
the presentation-layer timezone conversion used to render stored UTC
instants in the user's wall clock.  No Flask, no SQLAlchemy: these
operate on :class:`datetime.date` / :class:`datetime.datetime` values
only, so they import cleanly into any service or test without the app
stack.
"""
import calendar
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

# Single source of truth for the timezone the UI presents instants in.
# Storage and every backend computation stay UTC (each ``timestamptz``
# column is stored UTC by ``CreatedAtMixin``/``TimestampMixin``); this
# constant governs DISPLAY only, converting a stored UTC instant to the
# user's wall clock at the presentation boundary.  ``America/New_York``
# is a DST-aware zone, so the rendered clock is EDT (UTC-4) in summer and
# EST (UTC-5) in winter rather than a wrong fixed offset.
#
# Note: the anchor-history dedupe index keys the STORED ``observed_on``
# column (``app/models/account.py``) since plan step 2, so it needs no
# timezone expression at all and buckets the same civil day this constant
# defines.  It bucketed ``created_at`` by UTC day until then -- an
# independent zone, which is what made two assertions on two Eastern days
# sharing a UTC day collide (finding N-133 / F12).
DISPLAY_TIMEZONE = ZoneInfo("America/New_York")

# How far this application's calendar reaches: the ONE opinion every layer
# states about which dates a user may put on record.
#
# **Here rather than in the validation layer, since plan step R7c-b.**  They
# were declared in ``app/schemas/validation/_helpers.py``, which is the right
# home while only a SCHEMA enforces them -- and it stopped being the only one:
# ``budget.recurrence_rules.starts_on`` is authored, ``NOT NULL`` and reachable
# through a door no schema stands in front of (the recurrence preview reads it
# from ``request.args``), so ``services/recurrence/_resolution.py`` mirrors the
# bound too.  A service may not import from the validation layer, and the
# alternative was a second pair of numbers that could drift from these; this
# module is what both layers already depend on.  The schema keeps its names as
# re-exports, so nothing that imported them from there had to move.
#
# FOUR tables mirror them for writers that never see a schema:
# ``ck_template_amount_versions_effective_date_range``,
# ``ck_recurrence_rules_starts_on_range``, since plan step balance:X-bh-2
# ``ck_pay_schedule_history_opens_range``, and since plan#2331
# ``ck_credit_card_params_effective_date_range`` (a card's dated terms, which
# hold flat backwards as a template's amounts do).  The count is restated
# rather than left at "two", which is what it read until X-bh-2 added the
# third: a number in prose about a set that grows is the copy nobody re-reads.
#
# An HTML date input accepts a four- or five-digit-year typo, and both columns
# STORE what they are given: a stray ``0202`` becomes a template's earliest
# amount version -- anchoring every date before the series, which the
# withdrawal door refuses to remove -- and a stray ``9999`` overflows the pay
# calendar's forward projection with an ``OverflowError`` no handler catches.
CALENDAR_DATE_MIN: date = date(2000, 1, 1)
CALENDAR_DATE_MAX: date = date(2100, 12, 31)


def to_display_tz(value: datetime) -> datetime:
    """Convert a stored UTC instant to the UI display timezone.

    Presentation-only (E-16 sibling of ``to_percent``): the database and
    all backend logic operate in UTC; this is the boundary that expresses
    a stored instant in the user's wall clock (:data:`DISPLAY_TIMEZONE`).

    A naive ``value`` is assumed to be UTC -- every ``timestamptz`` in
    this app is stored UTC, but a value that has lost its tzinfo (e.g. a
    naive test fixture) would otherwise be interpreted in the server's
    local zone by ``astimezone``, silently shifting the rendered day.

    Args:
        value: A timezone-aware (or naive-assumed-UTC) datetime.

    Returns:
        The same instant expressed in :data:`DISPLAY_TIMEZONE`.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIMEZONE)


def to_display_date(value: datetime | None) -> date | None:
    """Return the calendar date of a stored UTC instant in display tz.

    The day-of-record the user sees: the instant converted to
    :data:`DISPLAY_TIMEZONE` first, then truncated to a date, so a
    late-evening Eastern event does not roll onto the next UTC day.
    ``None``-safe so callers can pass an absent timestamp (e.g. an anchor
    that has never been set) straight through.

    Args:
        value: A stored UTC instant, or ``None``.

    Returns:
        The display-timezone calendar date, or ``None`` when ``value`` is
        ``None``.
    """
    if value is None:
        return None
    return to_display_tz(value).date()


def display_today() -> date:
    """Return the user's wall-clock 'today' in :data:`DISPLAY_TIMEZONE`.

    The calendar date it is right now on the user's clock, for surfaces that
    select a civil WINDOW (a tax/calendar year, a "this month") by the user's
    timezone rather than the server's UTC day.  Storage and the resolver's
    replay boundary stay UTC (``date.today()``); this is the presentation-layer
    "now".

    It keeps a display-tz-attributed figure in the SAME civil year as the
    analytics Taxes tab -- which already derives its year this way
    (``app/routes/analytics.py``) -- around the New Year boundary, when UTC has
    ticked to the next year but the user's Eastern clock has not.  The loan
    page's YTD interest / principal chips are exactly such a figure: their
    producer sums each payment by its display-tz civil paid date (the L9 rule),
    so the year they are summed IN must be the display-tz year to match.

    Returns:
        The current calendar date in :data:`DISPLAY_TIMEZONE`.
    """
    return to_display_tz(datetime.now(timezone.utc)).date()


def days_paid_before_due(
    due_date: date | None, settled_on: date | None,
) -> int | None:
    """Return how many days before its due date a settled item's money moved.

    Positive means paid early, negative paid late, zero paid on the day;
    ``None`` when either day is missing -- and for *settled_on* a missing day
    means the item has not settled, so its timeliness is not yet a question.
    **Both operands are civil dates, and no timezone enters this** (ruling
    **R-EC**, plan step X-f1).

    **ONE arithmetic for two shapes** (plan step ``balance:X-bi-6-1b``).  It
    was the body of ``Transaction.days_paid_before_due`` alone; a transfer's
    LEG answers the same question off its covering movement's day
    (``TransferLeg.days_paid_before_due``), and two properties each spelling
    ``(due_date - settled_on).days`` would be rule 14's two spellings, so
    both call this.  It lives in this stdlib-only module because it is two
    ``date`` values and a subtraction: no status, no row, no reference cache.

    Args:
        due_date: The item's due date, or ``None`` for an undated one.
        settled_on: The civil day its money moved, or ``None``.

    Returns:
        ``(due_date - settled_on).days``, or ``None``.
    """
    if due_date is None or settled_on is None:
        return None
    return (due_date - settled_on).days


class DatedAssertion(Protocol):
    """The three fields a loan anchor's chronological position is built from.

    A structural type, not a base class: it names what
    :func:`anchor_chronology_key` reads without forcing its callers onto a
    concrete class.  The production value is
    :class:`app.services.loan_loaders.LoanAnchorFact`, which this module must
    not import (it would give a stdlib-only module a runtime edge to the model
    layer, and this module is stdlib-only).

    Attributes:
        anchor_date: The civil day the balance was asserted FOR -- the business
            date, and the first term.
        created_at: The instant the assertion was RECORDED, aware-UTC.
        event_id: The stored ``budget.loan_anchor_events.id``.
    """

    anchor_date: date
    created_at: datetime
    event_id: int


def anchor_chronology_key(
    anchor: DatedAssertion,
) -> tuple[date, datetime, int]:
    """Return a loan anchor's position in its loan's ONE chronology.

    **The single definition of "which of a loan's balance assertions is later",
    written once and called by every consumer that must agree on it** (plan step
    X-an-b, closing finding **N-196**):

    * :func:`app.services.loan_loaders.load_loan_anchor_facts` sorts its facts by
      this, so the list every reader receives is already in chronological order;
    * :func:`app.services.loan_resolver.select_latest_anchor` takes the ``max()``
      of it, so the resolver seeds from the greatest whatever order it is handed;
    * :func:`app.services.loan_loaders.load_standing_loan_assertions` (plan
      step recurrence:R23) sorts the stored statements by it, and both the
      loader above and the anchor write door's duplicate rule (ruling
      **R-EQ**) read that list.

    They must name the SAME row -- the walk resets the running balance at each
    anchor in turn, so the last one it sees decides the posted balance, while the
    resolver's seeds the replayed one.  Before this function they were two inline
    tuples, and the tuples had drifted: neither carried the row id, and
    ``max()`` returns the FIRST maximal element where the walk applies the LAST,
    so a tie named opposite rows.

    **It lives HERE because its two callers cannot share a module**: the
    resolver is a runtime model-free leaf (``loan_resolver/_periods.py`` takes a
    ``TYPE_CHECKING``-only import to stay one) and ``loan_loaders`` imports
    models and ``db`` while declaring itself a leaf that imports "never another
    loan service".  So neither can import the other, and a key spelled in both
    would be two statements kept in step by hope.  This module is stdlib-only
    and both already depend on it.

    **All three terms, and why none is optional.**  ``anchor_date`` is the
    business day the assertion is ABOUT.  ``created_at`` orders two assertions
    about one business day, so the last one recorded is that day's closing
    balance -- and the synthesized origination fact carries the earliest possible
    instant, so a true-up asserted ON the origination date still outranks it.
    ``event_id`` makes the key TOTAL: ``created_at`` is
    ``server_default=func.now()``, which PostgreSQL evaluates at TRANSACTION
    START, so every row written in one transaction shares an instant --
    ``shekel-prod-db`` carries four such rows today, and they escape a tie only
    because no two of them share an ``anchor_date``.  The higher id wins, which
    is the rule the write door
    (:func:`app.services.loan_anchor_service._governing_loan_anchor`) and both cash
    orderings already apply.

    Pure: three attribute reads and a tuple, no I/O and no clock.

    Args:
        anchor: Any :class:`DatedAssertion` -- an object exposing
            ``anchor_date``, ``created_at`` and ``event_id``.  Takes the OBJECT
            rather than the three values because its callers pass it as a
            ``key=`` function, which is the point: a caller that unpacked the
            fields itself would be free to reassemble them differently.

    Returns:
        The ``(anchor_date, created_at, event_id)`` tuple, ascending-comparable.
    """
    return (anchor.anchor_date, anchor.created_at, anchor.event_id)


def utc_instant(instant: datetime) -> datetime:
    """Return *instant* as an aware-UTC ``datetime``.

    The storage convention, applied once: an aware value converts to UTC, a
    naive value is assumed UTC (every ``timestamptz`` in this app is stored
    UTC).  Normalizing every stored instant through this one helper is what
    makes an instant-vs-instant comparison well-defined -- Python refuses to
    compare a naive datetime with an aware one, so a single un-normalized value
    is a ``TypeError`` at read time rather than a wrong answer.

    **It no longer answers "was this settle inside the asserted balance", and
    the paragraph that said it did was the deleted rule's argument** (ruling
    R-DH, 2026-07-31).  That paragraph cited one production pair -- an anchor at
    12:57:08 UTC and two expenses at 13:07 the same day, $108.15 -- as proof
    that the partition must turn on ORDER WITHIN A DAY.  Scored over the whole
    account rather than that one pair, the instant partition cost ``$4,001.42``
    on a single day and ``$40,554.34`` gross across four months, and the day
    rule books a SMALLER correction at that very assertion ($39.27 against
    $68.88).  The question is now answered by comparing two civil days --
    ``CashSourceFact.settled_on`` against ``CashAnchorFact.observed_on``, both
    stored or resolved once -- and the instants this helper normalizes only
    break a tie between two assertions about the SAME day.  Leaving the old
    argument here is how the old rule gets reintroduced, which is why the
    helpers that implemented it were deleted rather than kept.

    Args:
        instant: A stored ``created_at`` / ``asserted_at`` instant.  Naive
            values are assumed UTC.  (``paid_at`` was a third such caller
            until plan step X-f1 replaced it with a stored civil day.)

    Returns:
        The aware-UTC equivalent of *instant*.
    """
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def days_in_range(first_day: date, last_day: date) -> "list[date]":
    """Return every civil day in an inclusive range, ascending.

    Args:
        first_day: The first day.
        last_day: The last day.

    Returns:
        The days, or ``[]`` when *last_day* precedes *first_day* -- an inverted
        range is an empty range rather than an error, because callers that
        clamp one end (a report bounded at the reader's NOW) reach it
        legitimately.

    **One statement of a five-line loop three callers had written separately**
    -- twice in ``balance_at._cash_flow`` sixty lines apart, where cross-module
    ``duplicate-code`` cannot see them, and once in ``services.bank_agreement``.
    Found by adversarial review 2026-08-24.
    """
    days: "list[date]" = []
    day = first_day
    while day <= last_day:
        days.append(day)
        day += timedelta(days=1)
    return days


#: Months in a year, as the ``int`` calendar arithmetic counts in.
#:
#: The one spelling for the month-ORDINAL arithmetic below and for
#: :mod:`app.services.recurrence._months`, which imports it from here since
#: plan step ``pay_calendar:C17-d-2`` moved the two ordinal primitives down
#: to this leaf.  :data:`app.utils.money.MONTHS_PER_YEAR` is the same twelve
#: as a ``Decimal`` -- the MONEY denominator, deliberately a separate name
#: because every consumer of that one divides money by it.
MONTHS_PER_YEAR = 12

#: The fewest days any calendar month holds -- February's, outside a leap
#: year -- and so the floor every day-of-month gap is measured against and
#: the length a meant day of 29..31 can be lost past.  The ONE spelling
#: (plan step ``pay_calendar:C17-d-2``): the cadence kinds' shortest gaps
#: read it, the era table's bounds derive from it (a semi-monthly pair's
#: lower day is at most one less, so the pair cannot collapse onto one day
#: in February; ``nominal_day``'s domain starts one above it), and
#: :func:`clamped_day` is where the clamp bottoms out at it.
SHORTEST_MONTH_DAYS = 28


def month_ordinal(day: date) -> int:
    """Return *day*'s absolute month ordinal.

    Months numbered continuously from year 0, so "three months later" is
    ``+ 3`` with no year-boundary special case and a residue class over
    ordinals is the same set as a residue class over month NUMBERS whenever
    the step divides 12 -- which it does for every calendar pattern
    (1, 3, 6, 12).

    **Declared HERE, below both packages that walk months** (plan step
    ``pay_calendar:C17-d-2``).  It was born in
    :mod:`app.services.recurrence._months` for the recurrence walk; the pay
    grid's day-of-month kinds step the same ordinals and clamp the same day
    (:mod:`app.services.pay_calendar._grid`), and that package can reach
    neither a private module of ``recurrence`` (the package-privacy gate) nor
    the package itself (``recurrence`` imports ``pay_calendar``, so the edge
    back is a cycle).  Rule 14's remedy for a shared leaf a layer puts out of
    reach is to MOVE THE LEAF, not to spell it twice; ``_months`` imports it
    back and its callers are unchanged.

    Args:
        day: Any date.

    Returns:
        ``year * 12 + (month - 1)``.
    """
    return day.year * MONTHS_PER_YEAR + (day.month - 1)


def clamped_day(ordinal: int, nominal_day: int) -> date:
    """Return the date *nominal_day* names in the month *ordinal* numbers.

    :func:`month_ordinal`'s companion, moved here with it for the reason
    that function gives: the ONE clamp the recurrence walk, the pay grid's
    day-of-month kinds, the card statement and the loan calendar land a meant
    day with.  A day-31 rule -- or a day-31 payday -- is the 31st in January
    and the 30th in April, rather than decaying to the 30th forever
    (recurrence ruling **R-R3**; pay_calendar ruling **R-PC79**).  The
    questions each of them asks of the grid it makes are the set below it.

    Args:
        ordinal: An absolute month ordinal, from :func:`month_ordinal`.
        nominal_day: The day of the month the rule MEANS, 1-31, before
            clamping.

    Returns:
        That month's *nominal_day*, or its last day when the month is shorter.

    Raises:
        ValueError: *ordinal* names a month outside ``datetime.date``'s own
            domain (year 1..9999), from the ``date`` constructor itself.  The
            bound is the caller's, as it is for every grid producer.
    """
    year, month = month_of_ordinal(ordinal)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(nominal_day, last_day))


def month_of_ordinal(ordinal: int) -> tuple[int, int]:
    """Return the ``(year, month)`` an absolute month ordinal numbers.

    :func:`month_ordinal`'s inverse, and its ONE spelling: :func:`clamped_day`
    opened with it inline, and the weekday grids below land their day from
    the same ordinal (plan step ``recurrence:R8-c``), so a second ``divmod``
    would be a second statement of which month an ordinal names.

    Args:
        ordinal: An absolute month ordinal, from :func:`month_ordinal`.

    Returns:
        The calendar year and the month number, 1-12.
    """
    year, month_index = divmod(ordinal, MONTHS_PER_YEAR)
    return year, month_index + 1


#: Days in a week, for every stride and weekday count measured in them.
DAYS_PER_WEEK = 7


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """Return the *nth* *weekday* of a month (the third Monday, the fourth Thursday).

    **The ONE producer of a weekday counted from a month's start**, moved here
    from :mod:`app.utils.business_days` at plan step ``recurrence:R8-c``.  The
    federal holiday set counted Martin Luther King, Jr. Day and Thanksgiving
    with it there, and a recurrence firing on "the third Tuesday" lands every
    month on the same answer; this leaf is below both, so neither spells it
    again (``CLAUDE.md`` rule 14: move the leaf).

    Args:
        year: Calendar year.
        month: Calendar month, 1-12.
        weekday: A :meth:`datetime.date.weekday` index, 0 = Monday.
        nth: 1-based, so ``1`` is the month's first such weekday.  Every month
            holds a 1st through 4th of every weekday; a 5th exists in some
            months only, and asking for one a month lacks answers a day in
            the FOLLOWING month, so a caller asks it only where it knows the
            month holds one.

    Returns:
        The day itself.
    """
    opens = date(year, month, 1)
    lead = (weekday - opens.weekday()) % DAYS_PER_WEEK
    return opens + timedelta(days=lead + DAYS_PER_WEEK * (nth - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    """Return the LAST *weekday* of a month (Memorial Day, "the last Friday").

    Moved here from :mod:`app.utils.business_days` beside :func:`nth_weekday`,
    for the same reason (plan step ``recurrence:R8-c``).

    Counted backward from the month's final day rather than forward at a
    fixed occurrence, because "the last Monday of May" is the fourth in
    some years and the fifth in others -- the distinction the fixed-count
    spelling gets wrong roughly one May in three.

    **It must start from the month's REAL last day**, which is what
    ``calendar.monthrange`` supplies.  A first draft of this function (in
    ``business_days``) started at the 28th -- the last day every month is
    guaranteed to have -- and stepped forward a week at a time while the
    month held: in a 31-day May that stops ON the 28th, because the 28th
    plus seven is already June, and the last Monday of May 2027 (the 31st)
    was answered as the 24th.  The test that caught it is
    ``test_business_days``' ``test_matches_the_transcribed_calendar_exactly
    [2027]``.

    Args:
        year: Calendar year.
        month: Calendar month, 1-12.
        weekday: A :meth:`datetime.date.weekday` index, 0 = Monday.

    Returns:
        The day itself.
    """
    closes = date(year, month, calendar.monthrange(year, month)[1])
    return closes - timedelta(
        days=(closes.weekday() - weekday) % DAYS_PER_WEEK,
    )


# A nominal day's MONTHLY GRID is that day clamped into every month -- one date
# per month, :func:`clamped_day`'s image.  A loan's installments, a card's close
# and due dates, a once-a-month payday and a monthly recurrence each fall on
# one; a twice-a-month payday falls on the union of two.  The four functions
# below are the ONE set of questions every one of them asks of its grid (plan
# step recurrence:R25, ruling **R-R122**, closing ledger row **REC-547**): the
# latest grid day on or before a date, the first on or after it, the first
# strictly after it, and the grid days between two dates.  Until that step the
# loan calendar (:mod:`app.services.installment_calendar`), the card statement
# (:mod:`app.services.card_statement`) and the pay grid
# (:mod:`app.services.pay_calendar._grid`) each wrote the comparisons out over
# the one clamp, and the recurrence walk
# (:mod:`app.services.recurrence._months`) bounded itself by a month constant
# of its own; its stop is the last grid day on or before the calendar's last
# day now.  Each answers a month ORDINAL rather than a date, because
# every caller steps or bounds by the ordinal (an installment's number, a
# statement's month, a payday's step count) and lands the day with
# :func:`clamped_day` itself.


def grid_month_on_or_before(day: date, nominal_day: int) -> int:
    """Return the month whose *nominal_day* is the LATEST grid day on or before *day*.

    *day*'s own month when its clamped *nominal_day* has fallen by *day*, the
    month before otherwise -- the installment a payment due on the 10th of a
    loan due on the 22nd pays (ruling ``recurrence:R-R89``), the last payday a
    day sits after on a monthly grid, the last statement close a day is on or
    after.

    Args:
        day: The date to place.
        nominal_day: The day of the month the grid MEANS, 1-31, before
            clamping.

    Returns:
        A month ordinal (:func:`month_ordinal`) whose ``clamped_day`` is on or
        before *day*, and whose next month's is after it.
    """
    return month_on_or_before(
        day, lambda ordinal: clamped_day(ordinal, nominal_day),
    )


def month_on_or_before(day: date, day_in: Callable[[int], date]) -> int:
    """Return the month whose grid day is the LATEST one on or before *day*.

    :func:`grid_month_on_or_before`'s comparison over ANY grid of one day a
    month, which *day_in* lands in a month ordinal.  The clamped meant day is
    one such grid; the recurrence walk's "third Tuesday" and "last Friday"
    (plan step ``recurrence:R8-c``) are two more, and its stop is the last
    month whose day is on or before the calendar's last day whichever grid it
    walks, so the comparison is stated here once rather than per grid.

    Args:
        day: The date to place.
        day_in: The grid's day in a month ordinal.  Its day must fall inside
            that month, so the answer is *day*'s own month or the one before.

    Returns:
        A month ordinal whose grid day is on or before *day*, and whose next
        month's is after it.
    """
    ordinal = month_ordinal(day)
    if day_in(ordinal) <= day:
        return ordinal
    return ordinal - 1


def grid_month_on_or_after(day: date, nominal_day: int) -> int:
    """Return the month whose *nominal_day* is the FIRST grid day on or after *day*.

    *day*'s own month when its clamped *nominal_day* has not yet passed, the
    month after otherwise -- the due date a pay period opening on *day*
    contains, the first statement close a span reaches.

    Args:
        day: The date to place.
        nominal_day: The day of the month the grid MEANS, 1-31, before
            clamping.

    Returns:
        A month ordinal whose ``clamped_day`` is on or after *day*, and whose
        previous month's is before it.
    """
    ordinal = month_ordinal(day)
    if clamped_day(ordinal, nominal_day) >= day:
        return ordinal
    return ordinal + 1


def grid_month_after(day: date, nominal_day: int) -> int:
    """Return the month whose *nominal_day* is the FIRST grid day strictly after *day*.

    The grid day following :func:`grid_month_on_or_before`'s, so a *day* ON
    the grid answers the NEXT month -- a statement closing on the 20th is in
    the cycle that closes next month (closed-open), and its payment is due on
    the first due day strictly after the close (ruling ``credit_card:R-CC26``).

    Args:
        day: The date to place.
        nominal_day: The day of the month the grid MEANS, 1-31, before
            clamping.

    Returns:
        A month ordinal whose ``clamped_day`` is after *day*, and whose
        previous month's is on or before it.
    """
    return grid_month_on_or_before(day, nominal_day) + 1


def grid_months_within(nominal_day: int, first: date, last: date) -> range:
    """Return the months whose *nominal_day* grid day falls in ``[first, last]``.

    The grid enumerated between two dates: from :func:`grid_month_on_or_after`
    *first* through :func:`grid_month_on_or_before` *last*, ascending, one per
    month.  Empty when no grid day lies in the span, including when *last*
    precedes *first*.  A caller lands each day with :func:`clamped_day`, so the
    MEANT day is re-clamped in every month and a 31st never decays to the 28th
    after a February.

    Args:
        nominal_day: The day of the month the grid MEANS, 1-31, before
            clamping.
        first: The earliest grid day to include.
        last: The latest grid day to include.

    Returns:
        The month ordinals, as a ``range``.
    """
    return range(
        grid_month_on_or_after(first, nominal_day),
        grid_month_on_or_before(last, nominal_day) + 1,
    )


def add_months(start: date, months: int) -> date:
    """Add ``months`` calendar months to ``start``, day-clamped.

    The result's day is clamped to the target month's last day, so
    ``add_months(date(2026, 1, 31), 1)`` yields ``date(2026, 2, 28)``
    rather than raising for the nonexistent February 31st.  **The clamp is
    :func:`clamped_day`'s and the step is :func:`month_ordinal`'s** (plan
    step ``pay_calendar:C17-d-3``, closing ledger row **PC-513**): this
    function spelled both again, one function below them, from before
    ``C17-d-2`` moved the two primitives into this module.  It clamps to
    *start*'s own day, so stepping AGAIN from the result decays a 31st to
    the 28th for good -- the caller's spelling, kept as it was; a rule or a
    payday that means the 31st carries the meant day itself
    (``recurrence:R-R3``, **R-PC79**) and does not step through here.

    Overflow guard: returns :attr:`datetime.date.max` when the result
    would exceed year 9999 (Python's maximum representable year) instead
    of raising, so a long projection horizon degrades gracefully to a
    sentinel far-future date.

    Args:
        start: The starting date.
        months: Number of months to add (non-negative).

    Returns:
        A new :class:`datetime.date` ``months`` months after ``start``,
        or :attr:`datetime.date.max` on year-9999 overflow.
    """
    ordinal = month_ordinal(start) + months
    if ordinal >= (date.max.year + 1) * MONTHS_PER_YEAR:
        return date.max
    return clamped_day(ordinal, start.day)


def months_between(start: date, end: date) -> int:
    """Return whole calendar months from ``start`` to ``end`` (day ignored).

    Computes ``(end.year - start.year) * 12 + (end.month - start.month)``:
    the number of month boundaries between the two dates, with the
    day-of-month disregarded.  The delta from 2026-01-15 to 2027-01-01 is
    12, and from 2026-01-31 to 2026-02-01 is 1.

    The result is signed and unclamped -- an ``end`` before ``start``
    yields a negative count.  Callers that need a floor (e.g. "months
    remaining cannot drop below zero") or an inclusive ``+ 1`` clamp or
    adjust at the call site, because the bound differs per caller.

    Args:
        start: The earlier reference date.
        end: The later reference date.

    Returns:
        The signed whole-month delta as an ``int``.
    """
    return (end.year - start.year) * MONTHS_PER_YEAR + (end.month - start.month)


#: English month names, indexed by ``month_number - 1``.
#:
#: The source of truth for the ``month_name`` Jinja filter and for
#: :func:`app.services.recurrence.describe`.  They lived in
#: :mod:`app.jinja_filters` until plan step R7a, which needed the same names in
#: a SERVICE -- the recurrence describer builds a cadence phrase like
#: ``"Quarterly (Apr 21)"`` -- and a service must not import the template
#: filter module, which reaches back into ``app.services``.  Copying the table
#: instead would put two spellings of one list in the codebase, so it moved
#: down here and both readers take it from one place.
#:
#: **It is not the only month-name producer in the application**: sites across
#: ``app/`` and the templates still name months through :mod:`calendar` and
#: ``strftime``, and they are NOT owed a conversion.  Ruling
#: ``recurrence:R-R54`` answered finding F-15 with the process locale pinned in
#: the image and asserted at startup (:data:`app.PINNED_LOCALE`, ruling
#: ``R-R92``), which covers every such site, present and future, where
#: converting them one at a time covers only the ones someone remembers.
#:
#: Spelled out rather than read from :mod:`calendar` all the same, because a
#: table needs no pin to read English.  The sites that do read the locale read
#: English too: CPython never calls ``setlocale`` for ``LC_TIME``, and a
#: ``setlocale(LC_ALL, "")`` would adopt the pinned ``C.UTF-8``.
_MONTH_NAMES_FULL: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTH_NAMES_ABBR: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


#: English weekday names, indexed by :meth:`datetime.date.weekday` (0 = Monday).
#:
#: Spelled out for the same reason as the month names above:
#: ``f"{a_date:%A}"`` delegates to the platform ``strftime`` and follows
#: ``LC_TIME``.  That is a HAZARD rather than a live failure, and this note once
#: claimed a container started with ``LANG=de_DE.UTF-8`` would render
#: ``Weekly (Donnerstags)``.  It would not, twice over: CPython never calls
#: ``setlocale`` for ``LC_TIME``, and the production image holds no locale but
#: ``C``, ``C.utf8`` and ``POSIX`` (measured in the running container
#: 2026-09-23).  Since ruling ``recurrence:R-R92`` the
#: process locale is also pinned in the image and asserted at startup, so no
#: environment the app can start under names a German weekday.  Public since
#: plan step recurrence:R8-c: a weekday COORDINATE carries its index, not a date.
WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)


def weekday_name(value: date) -> str:
    """Return *value*'s English weekday name (``"Thursday"``).

    Locale-independent by construction: indexed off
    :meth:`datetime.date.weekday` rather than formatted with ``%A``, so the
    name a surface renders does not depend on the process ``LC_TIME``.

    Args:
        value: Any date.

    Returns:
        The English weekday name.
    """
    return WEEKDAY_NAMES[value.weekday()]


def month_name(value: int | None, abbr: bool = False) -> str:
    """Map a 1-12 month number to its English name (``1`` -> ``"January"``).

    Single source for the month-name lookups that used to be duplicated as
    per-template ``month_names`` dicts (polyglot audit TPLB/TPL-07).  Pass
    ``abbr=True`` for the three-letter form (``"Jan"``) used by the list
    views; the default full name (``"January"``) matches the form selects.

    Registered as the ``month_name`` Jinja filter by
    :func:`app.jinja_filters.register_template_filters`, and called directly by
    :func:`app.services.recurrence.describe`, which builds a cadence phrase in
    a service rather than in a template.  Defined HERE so those two readers
    cannot come to name March two different things.

    Args:
        value: A month number in ``1..12``, or ``None``.
        abbr: ``True`` for the abbreviated name, ``False`` for the full
            name.

    Returns:
        The month name, or ``""`` for ``None`` or an out-of-range number.
    """
    if value is None:
        return ""
    index = int(value)
    if not 1 <= index <= len(_MONTH_NAMES_FULL):
        return ""
    names = _MONTH_NAMES_ABBR if abbr else _MONTH_NAMES_FULL
    return names[index - 1]


def day_label(value: date) -> str:
    """Return one day's NARROW label (``"07/13"``), the register a paycheck is named in.

    :func:`pay_period_label`'s one-ended sibling, stated here so a surface
    printing a day BESIDE a paycheck label can speak the same register without
    spelling it again.  Plan step ``bank_import:X-gz`` is the first caller: the
    Reconcile MATCH pane prints a purchase's day beside the paycheck it is
    budgeted in, and a pane reading ``07/13`` against ``07/16 - 07/29`` is one
    register where ``2026-07-13`` against ``07/16 - 07/29`` would be two.
    ``grid/_transaction_entries.html`` and ``accounts/_reconcile_panel.html``
    (three sites) render the same ``%m/%d`` inline and could reach this
    instead; collapsing them is a change to screens that step did not touch
    (``CLAUDE.md`` rule 6), and ledger row **P47**'s census of range
    spellings does not yet name a single-day site, so it is reported rather
    than taken.

    **No year, by the rule the paycheck label states one function down**: the
    year is shown only where it disambiguates, and a single day beside its
    paycheck is disambiguated by the paycheck.

    Args:
        value: Any date.

    Returns:
        ``MM/DD``, zero-padded.
    """
    return value.strftime("%m/%d")


def pay_period_label(start_date: date, end_date: date) -> str:
    """Return a pay period's human label (``"02/21 - 03/06"``).

    **ONE accessor reaches it, since plan step ``pay_calendar:C4-a-5``**, and
    the argument that used to stand here was "one rule, TWO accessors" -- so
    it is restated rather than trimmed, because the reason a function lives
    where it lives is exactly the kind of premise nobody re-checks.  A pay
    period was answered by two types: the ORM row
    (``app.models.pay_period.PayPeriod``) and the derived value
    (:class:`app.services.pay_calendar.DerivedPeriod`).  Neither could import
    the other -- the model closes a cycle through ``pay_calendar._loader``,
    and the calendar package is deliberately model-free -- so the shared rule
    lived here, which both already depended on.  **Stating the format once did
    NOT make the two labels equal**: the row fed it the STORED ``end_date``
    and the derived value the DERIVED one, so under the P12 / P28 shape two
    screens still rendered one paycheck two ways.  C4-a-5 closed that by
    deleting the ROW's accessor, and :attr:`DerivedPeriod.label
    <app.services.pay_calendar.DerivedPeriod.label>` is now the only caller.

    **Why it stays here with one caller, rather than moving into that
    property.**  Its sibling one function down,
    :func:`pay_period_range_label`, is the WIDE register of the same value,
    has two callers of its own, and states its year rule as "this one's".  The
    two registers are ONE decision -- ledger row **P47** counts what happens
    when spellings of a period's date range are written apart, and the answer
    it reached is that the registers live together.  Moving the narrow one
    onto a class would split the pair across modules and leave the wide one
    citing a rule it cannot see, which is how a fourth spelling appears.

    The year is shown only when the period STRADDLES one, because that is
    the only time it disambiguates: ``"12/26/26 - 01/08/27"`` says which
    January, while ``"02/21/26 - 03/06/26"`` says nothing ``"02/21 -
    03/06"`` did not.

    Args:
        start_date: The payday that opens the period.
        end_date: The last day the period covers.

    Returns:
        The label, with a two-digit year on both halves when the period
        crosses a year boundary and no year at all when it does not.
    """
    if start_date.year != end_date.year:
        return (
            f"{start_date.strftime('%m/%d/%y')} - "
            f"{end_date.strftime('%m/%d/%y')}"
        )
    return f"{day_label(start_date)} - {day_label(end_date)}"


def pay_period_range_label(start_date: date, end_date: date) -> str:
    """Return a pay period's WIDE label (``"Feb 21 - Mar 06, 2026"``).

    The second register, for the surfaces with room for month names: the
    Income Statement's window selector and heading, and the Spending report's
    window heading.  :func:`pay_period_label` above is the narrow one
    (``"02/21 - 03/06"``), for a grid column head and a ``<select>`` that has
    to fit beside other controls.  **Two registers is the deliberate part;
    three COPIES of one register was not**, and collapsing that is what this
    function is (plan step C2-f3a, ledger row **P47**'s duplicate half).

    It was written three times -- character for character the same output from
    three separate expressions of it:
    ``ledger_report_service._income_statement._window_label``,
    ``spending_report_service._window._window_label``, and inline Jinja in
    ``analytics/_income_statement.html``.  Two of the three render this label
    beside each other on ONE screen -- the statement's heading and the
    ``<option>`` the reader picked it from -- so editing either alone would put
    one paycheck on one page under two spellings.

    **What is left is FOUR more spellings, and P47's census of six did not have
    two of them** (re-censused 2026-08-18 by C2-f3a's adversarial reviews, over
    every `` - `` / `` -- `` join of two ``strftime`` calls in ``app/``).  The
    row's own status says its count is a floor and this is the measurement that
    proves it: ``companion/index.html`` (``%b %-d`` -- ``%b %-d, %Y``),
    ``grid/_mobile_plan.html`` (``%b %-d`` -- ``%b %-d``),
    ``_recurrence_preview`` (``%b %d, %Y`` - ``%b %d, %Y``), and a pair the
    census never named -- ``accounts/_reconcile_panel.html`` and
    ``dashboard/_pulse.html``, which render the IDENTICAL ``%b %-d`` -
    ``%b %-d`` and are therefore a second duplicate, not a second register.
    Untouched here: which registers a screen should speak is P47's open half
    and a display decision rather than a calendar one.

    **It takes the two dates rather than a period**, and the reason changed
    shape at plan step ``pay_calendar:C4-a-5`` rather than going away.  It was
    that a pay period is answered by two types whose modules may not import
    each other; C4-a-5 deleted the ORM row's label accessor, so the two
    callers are now :attr:`DerivedPeriod.range_label
    <app.services.pay_calendar.DerivedPeriod.range_label>` and
    ``spending_analysis.window_label``.  Those are still in two packages that
    cannot both be depended on from one place -- ``app.services.pay_calendar``
    is deliberately model-free and imports no other service -- and taking two
    dates is what lets a caller holding neither type ask.

    **The year is carried on BOTH halves when the period straddles one**, and
    on the END alone when it does not -- ledger row **P67**, developer ruling
    2026-08-25.  It printed ``"Dec 26 - Jan 08, 2027"`` until then, which reads
    as a December in 2027 and is a December in 2026; the three copies C2-f3a
    collapsed all did that, and the collapse reproduced it byte for byte on
    purpose so a rendering change was not smuggled in behind a DRY fix.  This
    is that change, made on its own.

    **The rule is :func:`pay_period_label`'s, one function up**, so the two
    registers differ in WIDTH and not in when a year disambiguates: the narrow
    one answers ``"12/26/26 - 01/08/27"`` for this period and ``"02/21 -
    03/06"`` for one inside a single year.  Two registers was always the
    deliberate part; two RULES would be the drift.

    The month name comes from ``strftime`` and so follows ``LC_TIME``; the
    process locale pinned at startup (ruling ``recurrence:R-R92``, closing
    finding **F-15**) is what keeps it English, not anything in this function.

    Args:
        start_date: The payday that opens the period.
        end_date: The last day the period covers.

    Returns:
        The label -- abbreviated month name and zero-padded day on both halves,
        with a four-digit year on each half when the period crosses a year
        boundary and on the end alone when it does not.
    """
    if start_date.year != end_date.year:
        return (
            f"{start_date.strftime('%b %d')}, {start_date.year} - "
            f"{end_date.strftime('%b %d')}, {end_date.year}"
        )
    return (
        f"{start_date.strftime('%b %d')} - "
        f"{end_date.strftime('%b %d')}, {end_date.year}"
    )
