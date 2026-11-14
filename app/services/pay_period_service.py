"""
Shekel Budget App -- Pay Period Service

Queries an owner's biweekly pay periods.  Each period is defined by a
start_date (payday) and an end_date (the day before the next payday).

**It no longer WRITES them** (plan step C3-b): ``generate_pay_periods``,
``establish_schedule``, the batch bounds and the forward-only guard moved to
:mod:`app.services.pay_period_write`, which is now the one place in ``app/``
that changes ``budget.pay_periods``.  The reason is C3-a's, one level up --
deciding that a schedule should change and changing it are two concerns, and
the invariant that the stored ``end_date`` / ``period_index`` equalled the
derivation over the owner's paydays needed exactly one home for plan steps C4,
C6 and C7 to inherit.  C4-c has since dropped both columns, so what those
later steps inherit is a table with one fact in it.  What is left here is the
read side, which plan step **C2-f** points at ``pay_calendar.PayCalendar``.

**Three of that step's six readers are GONE at C2-f1** and their questions are
now the calendar's, each answered by ONE derivation over the owner's paydays
instead of by its own SQL:

======================================= ==================================
retired reader                          what answers it now
======================================= ==================================
``get_overlapping_periods``             ``PayCalendar.overlapping``
``get_next_period``                     ``PayCalendar.period_starting_after``
``get_current_and_future_periods``      ``routes._period_options.period_move_options``
======================================= ==================================

The last one is a FORM-OPTIONS rule rather than a calendar question, which is
why it left this module for the route layer rather than for the value: an
already-closed period is not somewhere a user moves an expense TO, and that is
a policy about editing rather than a fact about the schedule.
``companion_service.get_previous_period`` -- a copy of ``get_next_period``
with ``+ 1`` changed to ``- 1`` -- went with them.

**A FOURTH is gone at C2-f2b**, and it went whole rather than by call
site: ``get_periods_in_range`` -- a window selected by ``period_index``,
which is one of the two derived columns plan step **C4-c** dropped -- had all
three of its ``app/`` call sites in the grid route -- ``page.py`` twice and
``partials.py`` once, that module having become the ``app/routes/grid/``
package the same branch -- so moving the grid onto
:meth:`~app.services.pay_calendar.PayCalendar.window` left it with no caller
at all.  The question it answered is the calendar's:

======================================= ==================================
retired reader                          what answers it now
======================================= ==================================
``get_periods_in_range``                ``PayCalendar.window``
======================================= ==================================

**The FIFTH is gone at C2-f3a**:

======================================= ==================================
retired reader                          what answers it now
======================================= ==================================
``get_current_period``                  ``PayCalendar.period_containing``
======================================= ==================================

It answered "which paycheck covers this day" in SQL, and it is worth saying
what was wrong with it rather than only that it moved.  Its ``.first()``
carried NO ``ORDER BY`` (ledger row **P19**), so over two periods covering one
day it returned whichever row PostgreSQL happened to yield first -- a
plan-dependent answer to the application's most-asked period question.  And
none of its three call sites ever passed ``as_of``, so each answered on the
CONTAINER's civil day rather than the owner's; all three now read
``display_today()``.  Neither defect is patched: the derivation is ordered by
construction and takes the day as an argument, so both have no subject.

**Row P49 is NOT closed by that, and an adversarial review of C2-f3a caught
this paragraph claiming it was.**  The row is about the process clock behind
"which paycheck am I in" wherever it is asked, and FIVE sites in
``app/routes/salary/`` still ask it as ``period_containing(date.today())`` --
they took the derivation at C2-f2d-3 and kept the clock.  The row stays open,
re-measured, and names them.

**The SIXTH and LAST is gone at C2-f3c**:

======================================= ==================================
retired reader                          what answers it now
======================================= ==================================
``get_all_periods``                     ``PayCalendar`` itself
======================================= ==================================

It answered "every pay period this owner has" as ORM rows ordered by the
stored ``period_index`` -- one of the two columns plan step **C4-c** dropped -- and
its last caller was the recurrence generation seam's ``GenerationSchedule``,
which read it BESIDE the calendar and then had to reconcile the two.  C2-f3c
deleted the second read; a calendar is the owner's whole schedule already, in
payday order by construction, so there is nothing for a separate reader to
answer.  *The C2-f decomposition ruling of 2026-08-14 said the two readers
"travel together and may not be separated", on a measurement of 11 functions
reading both. By the time C2-f3 was picked up TWO functions did -- the Income
Statement's window defaults and the transfer create form -- and both moved
inside C2-f3a, so the constraint was satisfied rather than broken by splitting
the leaves this way.*

**What is left is one rule**, and it is here rather than in the calendar
package because it is not a calendar question: :func:`earliest_recordable_day`
takes the EARLIER of the owner's first payday and today, so the clock is half
its answer.  :func:`recordable_floor` is that rule over a first payday a caller
already holds -- ``pay_period_gates.gate_removable_head`` asks it of the first
payday a removal would leave (plan step ``pay_calendar:C21``) -- so the floor
is spelled once whether it is read or anticipated.
"""

from datetime import date

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.utils.dates import display_today


def earliest_recordable_day(user_id: int) -> date:
    """Return the earliest civil day this user's app can honestly date money at.

    ``min(the user's earliest pay period start, today)``.  Taking the EARLIER of
    the two is what keeps the bound from refusing a legitimate entry: a user
    whose periods are all still in the future must be able to record what
    happened today, while nobody may back-date into a past the app has no
    schedule for.

    **It has TWO SERVICE consumers, and it lives here so they cannot drift.**

    * ``anchor_service.resolve_observation_day`` -- an anchor's ``observed_on``,
      for BOTH writers of ``AccountAnchorHistory`` (the account factory's
      origination assertion and the true-up door's).  An unbounded day opens the
      modelled-return window (``balance_at._asset_return.AccrualWindow``
      materialises EVERY calendar day from it) and fabricates contribution
      history back to it (finding **N-133**).
    * ``status_seam.reject_settle_day_before_the_schedule`` -- a settle day
      (plan step X-f1c, ruling **R-EL**).  An unbounded day is absorbed into the
      opening assertion by ``cash_ledger._walk``, which then resets the running
      total to the asserted balance -- so the row's money silently leaves the
      projection while the row still reads Paid.

    **Four ROUTE consumers read it too, and they are a different use**: the two
    settle-day inputs (``routes/transactions/forms``, ``routes/transfers/forms``)
    and the two anchor date inputs (``routes/accounts/crud.new_account``,
    ``routes/accounts/anchor._anchor_day_bounds``) set an input's ``min`` from
    it so the browser refuses what the service would refuse.  That is a
    convenience and never the guard -- an input bound is captured at RENDER time
    and this floor moves whenever pay periods are generated or truncated.

    It was ``account_service.earliest_observable_day`` until X-f1c needed the
    same bound one module lower.  This module is the right home: the rule is a
    PAY-PERIOD SCHEDULE question with no account in it, and living here keeps it
    reachable from ``status_seam``, which must stay below the services that call
    it.  **The first bullet named ``account_service._reject_undatable_observation``
    and this paragraph named ``account_service.earliest_observable_day`` until
    plan step X-f1c4c deleted both** (ruling R-ER moved the rule to the module
    that owns what an assertion is); three independent reviews of that step
    found this docstring still naming them.

    Args:
        user_id: The owner whose schedule sets the floor.

    Returns:
        The earliest recordable civil day.  Today when the user has no pay
        periods at all -- every caller's own operation then fails on the missing
        schedule, which is a clearer error than a date bound.
    """
    earliest = (
        db.session.query(db.func.min(PayPeriod.start_date))
        .filter(PayPeriod.user_id == user_id)
        .scalar()
    )
    return recordable_floor(earliest, display_today())


def recordable_floor(first_payday: "date | None", today: date) -> date:
    """Return the earliest recordable day for a schedule opening on *first_payday*.

    The rule :func:`earliest_recordable_day` reads off the owner's stored
    schedule, stated over a value so a caller that holds the first payday --
    or is about to change it -- asks the same rule rather than a second
    spelling of it.  ``pay_period_gates.gate_removable_head`` is that caller
    (plan step ``pay_calendar:C21``, ruling **R-PC109**): it asks where the
    floor stands now and where it would stand once the first paydays go, and
    refuses a removal that would leave recorded money below it.

    Args:
        first_payday: The schedule's first payday, or ``None`` for an owner
            with no pay periods.
        today: The owner's civil day (``display_today``).

    Returns:
        The earlier of the two; *today* when there is no first payday.
    """
    if first_payday is None:
        return today
    return min(first_payday, today)
