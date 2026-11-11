"""The searches every pay-calendar answer is built from, written ONCE.

Plan step **C2-a** built these beside :class:`~._calendar.PayCalendar`; plan
step **C2-c** moved them into their own module when that one passed the
1,000-line ceiling.  Growing past a gate is a signal rather than a nuisance,
and the seam the ceiling was measuring is the one this package exists to draw:
a SEARCH over an ordered period tuple is a primitive, a WINDOW
(:mod:`._window`) is a view, and a CALENDAR (:mod:`._calendar`) is the owner's
whole schedule.  The dependency runs one way -- both of the others import this
and this imports neither -- so no two of them can answer one question
differently.

**Why they are free functions rather than methods.**  Each is shared by the
calendar and by a view over it, and the whole point of plan step C2 is that six
copies of "which pay period contains this date" already disagreed at exactly
the edges that matter (ledger row **P6**).  A primitive defined once, keyed on
one field, is what makes a view and the calendar it came from incapable of
disagreeing.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no database session, no
clock, and no model import.  Every search here is a pure function of the values
a caller supplies -- of the PERIODS, every one of them.  *One searched the
owner's GRID instead, from plan step ``C14-e-2`` to ``C17-c-2b``:
``nominal_payday_after`` answered the grid day the extend door continued a
rhythm from, and went when that door stopped computing a batch and began
materialising the plan (:func:`~._eras.planned_paydays_after`); the loop it
shared, :func:`~._eras.step_after`, stays for the seam.*
:meth:`FiledRow.for_row` is the one member that
takes a caller's mapped row rather than a value, and it READS three attributes
off it -- it imports nothing, so the property that matters (this package loads
and answers with no app stack behind it) is unchanged.
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date
from operator import attrgetter

from ._derive import DerivedPeriod

#: The bisect key for every search here: a period's opening payday.  Module
#: level so no two searches can key on different fields -- which is one of the
#: ways the six implementations row P6 counts came to disagree.
_BY_START_DATE = attrgetter("start_date")


def containing_index(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "int | None":
    """Return the POSITION in *periods* of the period covering *day*, else ``None``.

    **The single containment search.**  :func:`containing_period` is this plus
    an index, and both :class:`PayCalendar` and :class:`PeriodWindow` reach one
    of the two -- so the calendar, a view over it, and a consumer that needs to
    know WHERE in a view the answer sits cannot disagree about which period
    covers a day.  That was the whole point of plan step C2: six copies of this
    predicate already did (ledger row **P6**).

    Periods never overlap (they are derived from a set of distinct sorted
    paydays), so the latest period STARTING on or before *day* is the only
    candidate that can contain it and one bisect answers.

    **The end test is the PERIOD's own** since plan step C4-a-3
    (:meth:`~._derive.DerivedPeriod.covers`, ruling **R-PC31**), where it was
    ``day <= periods[index].end_date`` written here.  The bisect has already
    established the lower bound, so that method re-asks half of what this
    function knows -- and it is asked whole anyway, because a containment rule
    spelled one way in the search and another way at the three sites that place
    a single period is how row **P6**'s six copies came to disagree.

    **The POSITION is what plan step C2-f2c needed**, and it is here rather
    than expressed as arithmetic on :attr:`~._derive.DerivedPeriod.period_index`
    at the caller.  ``investment_dashboard_service._chart`` plots one point per
    period of a projection window and marks the one holding the planned
    retirement date, so what it needs is an offset INTO THAT VIEW; deriving it
    as ``found.period_index - window[0].period_index`` would be a second rule
    about how a window's ordinals relate to the calendar's, true today and
    unenforced, where this is the same bisect the containment answer already
    ran.  The scan it replaced was the last HAND-ROLLED member of row P6's
    census, and the last survivor of ANY kind --
    ``pay_period_service.get_current_period``, which was SQL rather than a scan
    -- was DELETED at plan step **C2-f3a**, which empties row **P6**'s census
    (the ROW is the ``C2`` container's and ticks with it).

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The 0-based position of the containing period, or ``None`` when *day*
        falls in a hole, before the first period, or after the last one's end.
    """
    index = bisect_right(periods, day, key=_BY_START_DATE) - 1
    if index < 0:
        return None
    return index if periods[index].covers(day) else None


def containing_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the period of *periods* whose span covers *day*, else ``None``.

    :func:`containing_index` resolved to the period it names, so the two
    answers come from one bisect and one end-date test rather than from two
    copies of them.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The containing :class:`~._derive.DerivedPeriod`, or ``None`` when *day*
        falls in a hole, before the first period, or after the last one's end.
    """
    index = containing_index(periods, day)
    return None if index is None else periods[index]


def latest_started_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the last period of *periods* opening on or before *day*, else ``None``.

    **The single ordering search**, shared by
    :meth:`PayCalendar.period_starting_on_or_before` and by
    :meth:`PayCalendar.filing_period` -- which needs it over the MATERIALISED
    subset rather than over every payday, and a second bisect written for that
    would be the duplication this step exists to remove.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to place.

    Returns:
        The last period whose ``start_date`` is on or before *day*, or ``None``
        when *day* precedes every one of them.
    """
    index = bisect_right(periods, day, key=_BY_START_DATE) - 1
    if index < 0:
        return None
    return periods[index]


def earliest_started_period(
    periods: "tuple[DerivedPeriod, ...]", day: date,
) -> "DerivedPeriod | None":
    """Return the first period of *periods* opening on or after *day*, else ``None``.

    The exact mirror of :func:`latest_started_period`, and it is HERE rather
    than inline for the reason the module docstring gives: a search over an
    ordered period tuple is a primitive, and this one had been written into
    :meth:`~._calendar.PayCalendar.period_starting_on_or_after` as a bare
    ``bisect_left`` while its backward twin was already shared.  Plan step
    **C2-f1** needed the same search over the MATERIALISED subset -- the
    situation that made :func:`latest_started_period` shared in the first place
    -- and a second bisect written for that would have been the duplication
    this module exists to remove.

    ``bisect_left`` rather than :func:`latest_started_period`'s
    ``bisect_right``: this asks for the first index at or after *day*, so a
    period opening exactly ON *day* must be included rather than stepped past.

    Args:
        periods: Periods in ``start_date`` ascending order.
        day: The calendar day to search forward from, inclusive.

    Returns:
        The first period whose ``start_date`` is on or after *day*, or ``None``
        when every one of them opens earlier -- "the schedule has not reached
        there yet" rather than "never".
    """
    index = bisect_left(periods, day, key=_BY_START_DATE)
    if index >= len(periods):
        return None
    return periods[index]


def paydays_between(
    periods: "tuple[DerivedPeriod, ...]", first_day: date, last_day: date,
) -> "tuple[date, ...]":
    """Return the paydays of *periods* OPENING within ``[first_day, last_day]``.

    **The single span search**, and it is here rather than beside its one
    consumer because an adversarial review of plan step **balance:X-bh-1**
    named the alternative for what it was: :mod:`._rhythm` had written the
    bisect pair itself and reached across for this module's private
    :data:`_BY_START_DATE` to do it, which is the tell that the search belongs
    in the module whose whole argument is that a second bisect written outside
    it is the duplication it exists to remove.

    It counts paydays that OPEN in the span, where
    :func:`~._views.overlapping_window` returns the periods that COVER it --
    two different questions over one ordering, which is why this returns bare
    ``date`` values.  A payday is a day; a period is a span.

    **An empty span is an empty answer**, where
    :func:`~._views.overlapping_window` refuses a crossed range.  The
    dispositions are right for their own callers rather than inconsistent: a
    window whose bounds are the wrong way round is a caller defect, while a
    crossed span arises here from an ordinary question -- "which paydays fell
    this year BEFORE its first day" names ``[Jan 1, Dec 31 of last year]``, and
    the honest answer is none.

    Args:
        periods: Periods in ``start_date`` ascending order.
        first_day: Inclusive lower bound.
        last_day: Inclusive upper bound.

    Returns:
        The paydays, ascending.  Empty for a crossed span, for an empty
        *periods*, and for a span the schedule opens no period in.
    """
    if last_day < first_day:
        return ()
    lower = bisect_left(periods, first_day, key=_BY_START_DATE)
    upper = bisect_right(periods, last_day, key=_BY_START_DATE)
    return tuple(period.start_date for period in periods[lower:upper])


def opening_payday(periods: "tuple[DerivedPeriod, ...]") -> "date | None":
    """Return the first payday of *periods*, or ``None`` when there are none.

    **The single opening-bound rule.**  Shared with the recurrence arc's
    ``PeriodCalendar``, which held a byte-identical copy until plan step C2-a --
    two implementations of "where does this schedule start", which is the defect
    row P6 counts on the containment question and this one has in miniature.

    Args:
        periods: Periods in ``start_date`` ascending order.

    Returns:
        The earliest ``start_date``, or ``None`` for an empty schedule.
    """
    if not periods:
        return None
    return periods[0].start_date


def period_by_id(
    periods: "tuple[DerivedPeriod, ...]", period_id: "int | None",
) -> "DerivedPeriod | None":
    """Return the period of *periods* carrying *period_id*, else ``None``.

    **The single identity lookup.**  Shared with the recurrence arc's
    ``PeriodCalendar`` at plan step C2-b1 for the reason every other primitive
    here is shared: two implementations of one question drift, and this one
    answered a WRITE question -- which stored row a rule's authored start
    period named -- until plan step R7b-4 folded that FK into a date.  **The
    note left then said it had no ``app/`` caller and that its survival was
    open; both were false by 2026-08-16** and the method's own docstring now
    carries the correction and the five callers.

    Linear rather than a map built at construction, and the justification has
    now narrowed twice and WIDENED again.  It was "once per rule" (61 paydays
    against 46 live rules on production); pay-calendar plan step C2-f2d-3
    widened it to once per generated ROW, when the recurrence engine's pricing
    lookup started resolving an id back to a derived period; plan step C2-f3c
    deleted that caller, since the generation seam carries the derived period on
    the placement; and **plan step C4-a-1 put a per-row caller back** --
    ``balance_at._cash_fold._cash_plan``, through
    :meth:`~._calendar.PayCalendar.require_period`, resolves every
    still-projected row's span here.

    **A map WOULD be faster and it is still not built, which is a size
    judgement rather than a refutation.**  C4-a-1's own design review called
    for an index; measured 2026-08-27 at the worst shape either database holds
    -- the dev Checking account's 603 projected rows against 62 paydays -- the
    scan costs **0.194 ms per fold** against **0.019 ms** for a dict built ONCE
    per fold and then indexed 603 times -- so the review was right about the
    direction and the factor is about ten.  (Rebuilding the dict per CALL, which
    is what a naive index inside this function would do, measures 0.692 ms and
    is worse than the scan; saying which thing was built when is the difference
    between a number and a slogan.)  What the measurement decides is the MAGNITUDE: a fold is
    memoized per account per read pass, so the whole saving on the widest
    render this application has is a fifth of a millisecond, against a second
    derived value that must then be kept in step with :attr:`periods`.  The
    number is recorded so the next caller pattern is weighed against a figure
    rather than against this paragraph -- and a per-row caller on a set an
    order of magnitude larger would decide the other way.

    **"Still not built" is a claim about THIS FUNCTION, and plan step C4-a-3
    is what made the distinction worth stating** (adversarial review,
    2026-08-31, which read the paragraph above as refusing the shape).
    ``routes.grid.page._build_entry_maps`` builds a
    ``{period_id: DerivedPeriod}`` index once per render and indexes it once
    per envelope row -- which is not the shape refused here, it is the
    **0.019 ms** one this measurement found TEN TIMES faster than the scan.
    What is refused is the naive index INSIDE this function, rebuilt per call
    at 0.692 ms, which is worse than scanning.  A caller that holds the whole
    period set already, and needs it many times, is exactly the pattern the
    last sentence above says would decide the other way; it decided that way
    at ruling **R-PC34**.
    **:func:`saved_by_id` below is that index, given a name** (plan step
    C4-a-4), so the second such caller asks for it rather than spelling a
    comprehension of its own -- and the choice between the two is made at the
    call site, by which one the caller can honestly use, rather than inside
    either.

    Args:
        periods: The owner's periods, in any order.  Identity is not a search
            over a sorted key, so unlike the two bisects above this carries no
            ordering precondition.
        period_id: A ``budget.pay_periods.id``, or ``None``.

    Returns:
        The matching :class:`~._derive.DerivedPeriod`, or ``None`` when
        *period_id* is ``None`` or names no period here.  ``None`` in is
        ``None`` out rather than an error: a rule may legitimately name no
        start period, and the foreign key is ``ON DELETE SET NULL`` -- though a
        stale in-memory id can outlive the row it named, which is the second
        way this answers ``None``.  A PROJECTED period can never match, because
        every one of them carries ``period_id = None``.
    """
    if period_id is None:
        return None
    for period in periods:
        if period.period_id == period_id:
            return period
    return None


@dataclass(frozen=True, kw_only=True)
class FiledRow:
    """A stored row that names a pay period: which table, which row, which period.

    **The argument :func:`require_period` used to take as two loose integers**,
    and collapsing them is what plan step ``pay_calendar:C4-a-5`` did to it.
    That signature was ``(period_id, transaction_id)``: two ``int``s in a fixed
    order, with nothing in the type system holding them to ONE row.  FOUR of
    its five call sites read both off the same object on the same line and were
    right by inspection; the fifth
    (``reconcile_service._assemble._block_headings``) indexes a three-column
    query tuple by POSITION -- ``row[2], row[0]`` -- which is the spelling that
    can be crossed without anything failing.  A crossed pair does not raise; it
    finds *a* period and returns it.  *This paragraph said "five of its six"
    until an adversarial review recounted it: ``grep`` finds six occurrences of
    the name, and the sixth is ``PayCalendar.require_period`` forwarding its own
    two parameters to this function, which reads nothing off an object and is
    neither kind of call site.*

    **What a crossed pair COSTS, measured rather than assumed, because the
    first draft of this paragraph overstated it** (adversarial design review,
    2026-08-31).  That draft said a wrong period here "moves money to another
    day", citing ``balance_at._cash_fold._cash_plan`` feeding the answer into
    :meth:`~._derive.DerivedPeriod.attribution_day` -- which dates a projected
    row on the daily balance line.  **Those are two different call sites.**
    ``_cash_plan`` reads ``txn.pay_period_id`` and ``txn.id`` off one object on
    one line and was never crossable at all; ``_block_headings``' answer
    becomes ``OutstandingGroup.period``, whose only consumer in the repository
    is the reconcile panel's ``period_span`` macro
    (``accounts/_reconcile_panel.html``) -- no Python reads it.  So the site
    with the money consequence never had the defect shape, and the site with
    the defect shape prints a HEADING.

    **That is still worth a value, and the honest reason is the class rather
    than the instance.**  The heading names which paycheck's envelope a block
    of settle checkboxes belongs to, on a screen whose instruction is "tick
    everything your statement shows" -- a mislabelled block is a wrong input to
    a money decision, not a wrong figure.  And the crossing becomes
    unconstructible for the NEXT caller: ``budget.journal_entries`` carries a
    ``pay_period_id`` too and plan step ``pay_calendar:C7`` opens it, and a
    caller that holds ids rather than a row is exactly where this shape
    reappears -- next time possibly at a site that computes.

    **The fields are KEYWORD-ONLY, and that is the guarantee rather than a
    style.**  ``FiledRow(1, 2)`` is a ``TypeError``, so the only way to build
    one from two bare integers is to name both -- and the only way to build one
    without naming anything is :meth:`for_row`, which reads the pair off ONE
    object and cannot cross it.

    **It carries the TABLE because more than one names a pay period.**
    ``budget.transactions``, ``budget.transfers`` and ``budget.journal_entries``
    all hold a ``pay_period_id``, and the refusal below has to say which row it
    is talking about: the id spaces overlap, so "id=5" without a table sends a
    reader to the wrong row.  The message said ``transaction`` outright until
    C4-a-5 gave the recurrence conflict chooser -- which builds rows for
    transactions AND transfers -- its first non-transaction caller.

    Attributes:
        table: The row's QUALIFIED table name (``"budget.transfers"``), for the
            refusal message.  Sourced from the mapped class's own
            ``__table__.fullname`` rather than written out, so it cannot drift
            from the schema.
        row_id: The row's primary key.
        period_id: The ``budget.pay_periods.id`` that row is filed in.
    """

    table: str
    row_id: int
    period_id: int

    @classmethod
    def for_row(cls, row) -> "FiledRow":
        """Return the :class:`FiledRow` for a mapped row that names a period.

        **The constructor that cannot mispair**, and the one every caller
        holding an entity uses.  It reads the two ids off ONE object, so the
        class of defect the old two-integer signature admitted is not
        expressible through it.

        **It reads attributes and imports nothing**, which is what keeps this
        module's stated boundary (no Flask symbol, no session, no clock) true:
        ``app.services.pay_calendar`` is deliberately model-free -- importing a
        model closes an import cycle through ``pay_schedule_service`` and ends
        the "drive the derivation with no database" property C1's harness rests
        on (see :mod:`._derive`).  Duck-typing the three attributes costs
        nothing structurally: a caller that passes an object without them gets
        an ``AttributeError`` at the call site rather than a wrong answer.

        Args:
            row: A mapped row carrying ``id``, ``pay_period_id`` and a
                ``__table__`` -- today a ``Transaction`` or a ``Transfer``.

        Returns:
            The :class:`FiledRow` naming that row and the period it is filed in.
        """
        return cls(
            table=row.__table__.fullname,
            row_id=row.id,
            period_id=row.pay_period_id,
        )


def require_period(
    periods: "tuple[DerivedPeriod, ...]",
    filed: FiledRow,
    user_id: int,
) -> DerivedPeriod:
    """Return the period *filed* names, REFUSING one *periods* lacks.

    **The identity lookup for a caller holding a row that is already FILED,
    rather than an id someone supplied** (pay-calendar plan step C4-a-1), and
    the difference is which answer is honest.  :func:`period_by_id` answers
    ``None`` because its callers hold an id a user typed, a URL carried or a
    nullable column holds -- "no such period of yours" is a real answer there,
    and each of them renders a 404 or an empty state for it.  A stored
    ``budget.transactions`` row is not that: its ``pay_period_id`` is NOT NULL
    and its foreign key is ``ON DELETE CASCADE``, so the period it names exists
    as long as the row does, and a calendar is one owner's COMPLETE saved
    payday set.  So a ``None`` here is not "not found" -- it is one of the two
    states below, and answering it hands a money surface a decision it has no
    basis to make.  The same is true of ``budget.transfers``, whose
    ``pay_period_id`` is NOT NULL under the same cascade
    (``fk_transfers_pay_period_id``).

    **Nothing in the type system separates the twins, so the rule is written
    down: an id read off a STORED row comes here.**  Some call sites look like
    counter-examples and are not, and they are NAMED rather than counted --
    counting them is what :func:`period_by_id`'s own docstring records going
    stale, repeatedly -- so the next reader does not read them as permission.
    ``statement_match._valuation.transaction_candidate`` asks
    :func:`period_by_id` of a stored ``pay_period_id`` and treats the ``None``
    as "not offerable, and not an error";
    ``statement_match._destinations.destinations_for`` indexes
    :func:`saved_by_id` with one and cannot miss at all.  Both are right, and
    for one reason each states at its own scan: those queries are SCOPED BY THE
    CALENDAR'S OWN period ids, so a row they return names a period the calendar
    was built from.  **Where the precondition is carried by the QUERY, the
    total form is honest; where it rests on two reads agreeing, it is not.**

    **TWO states reach the refusal, and neither is coped with.**

    * **A picture assembled from more than one moment** -- balance finding
      **N-358**, owned by ``balance:X-i5``.  ``balance:X-i3-a`` binds a GET to
      ``REPEATABLE READ, READ ONLY`` and leaves every other method at
      ``READ COMMITTED``, which the writes the owner's lock serialises
      depend on (their read under the lock must see the winner's rows,
      :mod:`app.db_transaction`).  **How exposed a caller is depends on the ORDER of its own
      two reads -- whether it derives its calendar before or after it loads the
      row -- so each states its own** rather than inheriting a sentence from
      here.  What is NOT the rule is "a GET is one snapshot": ``/grid`` and
      ``/dashboard`` open a :func:`~app.db_transaction.write_transaction` block
      for the rolling top-up, so each runs read-only, then writable, then
      read-only again over a NEW snapshot.
    * **A row filed in ANOTHER owner's pay period -- and it is UNSTORABLE
      since plan step ``pay_calendar:C13-a``.**  This clause used to read "0
      such rows on production, re-measured 2026-08-31 over all 1,028 rows",
      which is the shape the paragraph below warns about: a measurement quoted
      as a REASON, decaying invisibly because nobody re-checks a premise.  It
      decayed by being FIXED.  ``budget.transactions`` now carries a
      ``user_id`` held equal to both its account's owner and its paycheck's by
      a composite key each (ruling **R-PC32**), so the row this bullet
      describes cannot be written at all rather than merely being absent.
      **``C13-b`` took the decision this clause was left for, and the handling
      STAYS** (2026-09-03): the refusal is reached by TWO states and only this
      one became unstorable.  The other -- a picture assembled from more than
      one moment, the bullet above -- is live, owned by `balance:X-i5`, and
      reaches this same raise.  So what is dead weight is this BULLET's claim
      on the handling, not the handling; a caller whose calendar no longer
      holds a row's period still gets a refusal rather than a wrong span.
      Taken beside finding **P75**'s nineteen rather than as one of them,
      because it was a placement question and P75 counts only the reads that
      REFUSE.

    **The three quieter answers were weighed and refused** (the review that
    parked C4-a-1, 2026-08-25): placing the row against no span hides a
    contradiction on a money screen, re-deriving and retrying narrows the
    window without closing it, and dropping the row deletes it from whatever is
    being computed.  Each copes with an inconsistent picture rather than
    preventing one, and preventing one is X-i5's work.

    Args:
        periods: The owner's periods, in any order.
        filed: The stored row and the period it names, as ONE value
            (:class:`FiledRow`) -- never a submitted or nullable id, which is
            :func:`period_by_id`'s question.  It was two positional ``int``s
            until plan step ``pay_calendar:C4-a-5``; :class:`FiledRow` states
            why one value.
        user_id: The owner whose calendar *periods* came from, for the message.

    Returns:
        The :class:`~._derive.DerivedPeriod` carrying ``filed.period_id``.

    Raises:
        RuntimeError: *periods* does not hold that period.  Bare rather than a
            :class:`~._derive.PayCalendarError`, matching
            ``balance_at._asset_fold``'s refusal for the same class of state:
            no door may produce it, so no caller should be catching it and none
            does.
    """
    period = period_by_id(periods, filed.period_id)
    if period is None:
        raise RuntimeError(
            f"{filed.table} id={filed.row_id} is filed in pay period "
            f"id={filed.period_id}, which user {user_id}'s pay calendar "
            f"does not hold. Either that period belongs to another owner "
            f"(a row whose account and whose paycheck have different "
            f"owners, which no constraint refuses), or this calendar and "
            f"that row were read at two different moments with a "
            f"concurrent write between them -- balance finding N-358, "
            f"which needs a transaction that is not one snapshot."
        )
    return period


def materialised_periods(
    periods: "tuple[DerivedPeriod, ...]",
) -> "tuple[DerivedPeriod, ...]":
    """Return the periods of *periods* a foreign key can point at.

    **The single "is this period SAVED" rule**, shared by
    :meth:`PayCalendar.filing_period` -- which must answer a period a
    foreign key can point at (:meth:`app.services.ledger_lines.LedgerLine
    .filed_period_id`; a stored ``journal_entries.pay_period_id`` until plan
    task plan#3133) -- and by
    :meth:`PayCalendar.saved`, whose window keys the balance seam's per-period
    maps by ``budget.pay_periods.id``.  Two implementations of the predicate
    would be two answers to "which of these periods exists in the table", and
    an adversarial review of plan step C2-a already caught the first cut of
    ``filing_period`` skipping it: two lines of input returned a period whose
    id was ``None`` straight into a ``NOT NULL`` column.

    A period is unmaterialised two ways, and both are legitimate: a PROJECTION
    past the owner's horizon (:meth:`PayCalendar.axis`), and a candidate payday
    the writer has not saved yet -- which :func:`~._derive.derive_periods`
    accepts by design and which ``pay_period_write`` builds a calendar out of
    on every write.

    Args:
        periods: The periods to filter, in any order.  Their order is
            preserved, so a sorted input yields a sorted output.

    Returns:
        The subset carrying a ``period_id``; empty when none does.
    """
    return tuple(
        period for period in periods if period.period_id is not None
    )


def saved_by_id(
    periods: "tuple[DerivedPeriod, ...]",
) -> "dict[int, DerivedPeriod]":
    """Return the MATERIALISED periods of *periods*, keyed by ``period_id``.

    **:func:`period_by_id` in BULK, and the two are different answers to one
    question on purpose.**  That one SCANS, which is right for a caller holding
    a single id someone supplied; this builds an INDEX, which is right for a
    caller holding a whole ROW SET.  Its docstring carries the measurement --
    the scan against the index against a naive per-call index -- and names this
    caller pattern as the one that decides this way; ruling **R-PC34** took it
    at plan step C4-a-3, and plan step C4-a-4 gave the shape this name.

    **The KEYS are meant to be a query SCOPE, and that is the whole point
    rather than a convenience.**  A caller filters
    ``pay_period_id IN (<this mapping>)`` and then indexes the SAME mapping per
    row the query returned, so a row it cannot place is unconstructible rather
    than guarded against -- which is :func:`require_period`'s own rule above:
    *where the precondition is carried by the QUERY, the total form is honest;
    where it rests on two reads agreeing, it is not.*  **That pointer named the
    METHOD until this step moved the rule off it**, which is the shape a
    cross-reference fails in: the citation stayed green while its target
    emptied.  Two derivations -- an id set for
    the filter and a lookup for the span -- can disagree under a concurrent
    payday write between them; one value cannot.

    **What a caller owes for that guarantee is that the filter and the lookup
    read this ONE mapping**, and nothing here can check it.  A caller that
    filtered on a second derivation and indexed this one would get a
    ``KeyError`` rather than a wrong figure, which is the failure direction to
    be in but is still the caller's to avoid.

    **This function is PURE and its caller is the one that memoizes.**
    :meth:`~._calendar.PayCalendar.saved_by_id` caches what this returns,
    behind a read-only proxy, because a review pass asks it more than once and
    a mutable mapping shared between two producers lets one narrow the other's
    scope.  The rule and the reason are stated there; what belongs here is the
    derivation, which is what every other function in this module is.

    A PROJECTED period carries no id and is not here, through
    :func:`materialised_periods` -- this package's single "is this period
    SAVED" rule, rather than a fourth site spelling ``period_id is not None``
    for itself, which is what
    ``statement_match._candidates`` and both of ``reconcile_service._rows``'s
    scope properties did until plan step C4-a-4.

    Args:
        periods: The owner's periods, in any order.  Identity is not a search
            over a sorted key, so unlike the bisects above this carries no
            ordering precondition.

    Returns:
        ``{period_id: DerivedPeriod}`` over every materialised period, in the
        order they were given.  Empty when none is materialised, which for a
        scope means it admits nothing -- the correct answer for an owner with
        no paydays.
    """
    return {
        period.period_id: period
        for period in materialised_periods(periods)
    }


def final_covered_day(periods: "tuple[DerivedPeriod, ...]") -> "date | None":
    """Return the last day *periods* covers, or ``None`` when there are none.

    The symmetric partner of :func:`opening_payday`, and shared for the same
    reason.  The LAST period's ``end_date`` rather than a maximum over all of
    them, because the periods are ordered and non-overlapping by construction.

    Args:
        periods: Periods in ``start_date`` ascending order.

    Returns:
        The last covered day, or ``None`` for an empty schedule.
    """
    if not periods:
        return None
    return periods[-1].end_date
