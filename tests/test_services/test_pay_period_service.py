"""
Shekel Budget App -- Pay Period Service Tests

Integration tests for the pay-period READERS.  All of them issue DB queries,
so these run against a real PostgreSQL database using the shared
app/db/bare_user fixtures from conftest.

**The writer's tests moved out at plan step C3-b**, with the writer, to
``test_pay_period_write/``.  What is left here answers "which periods does
this owner have", which plan step C2-f points at ``pay_calendar.PayCalendar``.

**Three readers' tests moved out at C2-f1, WITH the behaviour they grade** --
none was deleted, because none of the behaviour was:

* ``get_next_period`` -> ``test_pay_calendar_value.TestPeriodStartingAfter``,
  beside the ``period_starting_before`` mirror that also replaced
  ``companion_service.get_previous_period``.
* ``get_current_and_future_periods`` ->
  ``tests/test_routes/test_period_options.py``, because the rule it encoded is
  the FORM's ("an already-closed period is not somewhere a row moves TO")
  rather than the calendar's.
* ``get_overlapping_periods`` -> nothing moved, because **it had no direct
  test call site at all**: the retired SQL was exercised only through
  ``calendar_service`` and ``spending_report_service``, never graded on its own.
  ``test_pay_calendar_value``'s ``overlapping`` tests grade the predicate that
  replaced it, and grade it harder -- they cover the crossed range, which the
  query answered with an empty list and the calendar refuses.

**The SIXTH and LAST reader is gone at C2-f3c**: ``get_all_periods`` had ONE
``app/`` caller left -- the recurrence generation seam's
``GenerationSchedule``, which read it BESIDE the owner's calendar -- and that
step deleted the second read.  Its single case here graded that the reader
returned the owner's ten periods in ``period_index`` order; a
:class:`~app.services.pay_calendar.PayCalendar` IS the owner's whole schedule,
in payday order by construction, and ``test_pay_calendar_value`` and
``test_pay_calendar_loader`` already grade that harder than a length check
could -- so the case is DELETED rather than re-pointed at a test helper.
Grading ``tests._test_helpers.all_periods`` would be grading the harness.

**A FOURTH reader's tests moved out at C2-f2b**, and it was the first of the
six to be DELETED rather than re-pointed: ``get_periods_in_range`` had all
three of its ``app/`` call sites in the grid route (``page.py`` twice,
``partials.py`` once), so moving the grid onto ``PayCalendar.window`` left it
with no caller.  Its four cases went to
``test_pay_calendar_value.TestAWindowIsAViewAndKeepsTheRealEnds``, which
already graded three of them (the exact window, the partial one past the end,
and the empty one) on the method that now answers.  The fourth -- a NEGATIVE
``first_index``, which the SQL expressed as ``period_index >= -1`` and which
comes back one period SHORT rather than re-based -- had no equivalent there
and is now its own test in that class, named as inherited coverage.  Reachable
from ``/grid?offset=-99`` on every schedule.
"""

from datetime import date

from app.services.pay_calendar import calendar_for


# ---------------------------------------------------------------------------
# TestGetCurrentPeriod
# ---------------------------------------------------------------------------


class TestTheCurrentPeriodComesFromTheDerivation:
    """"Which paycheck covers this day" over a real schedule in the database.

    **These graded ``pay_period_service.get_current_period`` until plan step
    C2-f3a DELETED it**, and they are re-pointed rather than removed, because
    none of the behaviour was removed -- the same discipline the three C2-f1
    readers got (module docstring).  What answers now is
    :meth:`~app.services.pay_calendar.PayCalendar.period_containing`, reached
    through the one database door, so these remain an INTEGRATION grade of the
    loader plus the derivation plus the search against real rows, where
    ``test_pay_calendar_value`` grades the search alone with no database.

    Two things the retired reader did that these cases now pin the ABSENCE of:
    its ``.first()`` carried no ``ORDER BY`` (ledger row **P19**), and its day
    defaulted to the process clock rather than being asked for (row **P49**).
    Every case here states its own day as a literal, which is the clock
    discipline ``.claude/rules/testing.md`` asks for and which the old
    signature made optional.
    """

    def test_returns_period_containing_date(self, app, db, bare_user, bare_periods):
        """A date within the first period (Jan 2-15) should return it."""
        with app.app_context():
            period = calendar_for(bare_user["user"].id).period_containing(
                date(2026, 1, 5),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.start_date == date(2026, 1, 2)
            assert period.end_date == date(2026, 1, 15)

    def test_no_period_contains_date_returns_none(self, app, db, bare_user, bare_periods):
        """A date before all periods returns None."""
        with app.app_context():
            period = calendar_for(bare_user["user"].id).period_containing(
                date(2020, 1, 1),
            )
            assert period is None

    def test_custom_as_of_date(self, app, db, bare_user, bare_periods):
        """Targeting the 3rd period (index 2) returns the correct period."""
        with app.app_context():
            # Period 2: starts Jan 2 + 28 days = Jan 30, ends Feb 12.
            period = calendar_for(bare_user["user"].id).period_containing(
                date(2026, 2, 1),
            )
            assert period is not None
            assert period.period_index == 2
            assert period.start_date == date(2026, 1, 30)


# ---------------------------------------------------------------------------
# TestNegativeAndBoundaryPaths
# ---------------------------------------------------------------------------


class TestNegativeAndBoundaryPaths:
    """Boundary-condition tests for the pay-period readers.

    The batch-size and large-batch cases moved to
    ``test_pay_period_write/test_what_a_batch_records.py`` with the writer at
    plan step C3-b; what is left here is the date-boundary behaviour of the
    lookups.
    """

    def test_the_current_period_includes_its_own_start_date(
        self, app, db, bare_user, bare_periods,
    ):
        """A day equal to a period's start_date resolves to that period.

        Off-by-one on date boundaries is a classic bug. The first day of a
        period must be included in that period, not the previous one.
        """
        with app.app_context():
            # Period 0 starts on 2026-01-02.
            period = calendar_for(bare_user["user"].id).period_containing(
                date(2026, 1, 2),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.start_date == date(2026, 1, 2)

    def test_the_current_period_includes_its_own_end_date(
        self, app, db, bare_user, bare_periods,
    ):
        """A day equal to a period's end_date resolves to that period.

        The last day of a period must be included in that period. If the
        boundary were exclusive, the user would see no current period on the
        last day of a pay cycle.
        """
        with app.app_context():
            # Period 0 ends on 2026-01-15 (start + 13 days).
            period = calendar_for(bare_user["user"].id).period_containing(
                date(2026, 1, 15),
            )
            assert period is not None
            assert period.period_index == 0
            assert period.end_date == date(2026, 1, 15)

    def test_no_period_covers_a_day_past_the_last_one(
        self, app, db, bare_user, bare_periods,
    ):
        """A day after every generated period resolves to ``None``.

        With 10 periods starting 2026-01-02 at 14-day cadence, the last period
        (index 9) ends on 2026-05-21. A date of 2026-05-22 is outside all ranges.
        Ensures the derivation does not crash or return a wrong period when
        the date is outside all generated ranges.  ``None`` here is the SAVED
        answer, not the total one: ``span_containing`` would project a period
        past the horizon, which is why the three routes that branch on this
        call ``period_containing``.
        """
        with app.app_context():
            # Period 9: start = 2026-05-08, end = 2026-05-21.
            period = calendar_for(bare_user["user"].id).period_containing(
                date(2026, 5, 22),
            )
            assert period is None




