"""How each row says it repeats, from one read of its rule per build: its next date as the
recurrence engine places it, its worded cadence and closing bound in the active sections and
among transfers, and the archived drawer's rows, worded in the caller's order with no occurrence
placed.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY, ANNUAL
from app.services import recurring_view
from app.services.recurrence import rule_occurrences
from app.services.recurrence import _reading
from app.services.recurrence import compute_due_date

from ._helpers import (
    _calendar,
    _ctx,
    _create_expense,
    _create_income,
    _create_savings,
    _create_transfer,
)


# ── Engine-backed next dates ─────────────────────────────────────────


class TestNextDates:
    """Next occurrence is the recurrence engine's own due date."""

    def test_next_date_monthly_is_engine_due_date(
        self, seed_user, seed_periods_today,
    ):
        """A monthly-on-the-15th expense's next_date equals the engine's
        due date for the next matching period on or after today, and lands
        on the 15th.
        """
        today = date.today()
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("100.00"), day_of_month=15)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        next_date = view.expenses.rows[0].next_date
        # Independent engine recomputation of the contract.
        # Reached through the template that owns it (plan step R-F6): the
        # rule is authored onto the definition, so the definition is what a
        # caller holds a handle to.
        rule = tmpl.recurrence_rule
        matched = [
            placement
            for placement in rule_occurrences(
                rule, _calendar(seed_periods_today),
            )
            if placement.period is not None
            and placement.period.end_date >= today
        ]
        expected = next(
            compute_due_date(rule, p.occurrence, p.period)
            for p in matched
            if compute_due_date(rule, p.occurrence, p.period) >= today
        )
        assert next_date == expected
        assert next_date >= today
        assert next_date.day == 15

    def test_next_date_every_period_is_future_period_start(
        self, seed_user, seed_periods_today,
    ):
        """An every-paycheck expense's next_date is a pay-period start on or
        after today (the current period's start is already past).
        """
        today = date.today()
        tmpl = _create_expense(seed_user, EVERY_PERIOD, Decimal("50.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )
        next_date = view.expenses.rows[0].next_date
        assert next_date is not None
        assert next_date >= today
        assert next_date in {p.start_date for p in seed_periods_today}


# ── The cadence phrase, and the ONE read it comes from (plan step R7a) ─


class TestTheRecurrenceDescription:
    """Every row carries how it repeats, produced here rather than in Jinja."""

    def test_an_active_row_carries_its_worded_cadence(
        self, seed_user, seed_periods_today,
    ):
        """The producer answers the phrase the cell renders.

        Until plan step R7a the Recurrence column was eight Jinja branches
        reading ``pattern_id`` / ``day_of_month`` / ``month_of_year`` off the
        rule -- columns plan step R7c drops.  The phrase is a function of what
        the recurrence MEANS against the owner's schedule, which a template
        holds neither of.
        """
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("100.00"), day_of_month=22)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )

        assert view.expenses.rows[0].recurrence.cadence == "Monthly (day 22)"

    def test_a_rule_less_row_carries_none(self, seed_user, seed_periods_today):
        """"Does not repeat" is ``recurrence_rule_id IS NULL`` since R2e-3.

        ``None`` rather than a phrase, so the cell's "One-time" wording is a
        display decision and the producer states absence honestly.
        """
        tmpl = _create_expense(seed_user, None, Decimal("100.00"))

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )

        assert view.expenses.rows[0].recurrence is None

    def test_the_closing_bound_reaches_the_row_as_its_own_line(
        self, seed_user, seed_periods_today,
    ):
        """The closing bound is carried as the cell's own second line.

        A worded phrase since plan step R7b-3 rather than a bare date, so the
        surface renders one string and the wording of a stop is decided in the
        producer beside the wording of a cadence.

        Both dates are LITERALS and so is the expectation.  Deriving the
        expected string with ``f"{end:%b}"`` would assert the locale-safe
        producer against the ``strftime`` it replaced, and could not fail for
        the reason it was written; a date built from ``today()`` would make
        the case's subject move with the calendar.  A bound in 2029 is past
        every fixture's horizon, which is what this asserts about -- the cell
        carries the stop whether or not the schedule reaches it.
        """
        end = date(2029, 9, 15)
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("100.00"), day_of_month=22, end_date=end)

        view = recurring_view.build_view(
            [], [tmpl], [], _ctx(seed_user),
        )

        assert view.expenses.rows[0].recurrence.stops == "until Sep 15, 2029"

    def test_a_transfer_row_carries_one_too(
        self, seed_user, seed_periods_today,
    ):
        """The transfers section takes the same producer, not a second one."""
        savings = _create_savings(seed_user)
        tmpl = _create_transfer(seed_user, EVERY_PERIOD, Decimal("50.00"), savings)

        view = recurring_view.build_view(
            [], [], [tmpl], _ctx(seed_user),
        )

        assert view.transfers.rows[0].recurrence.cadence == "Every paycheck"

    def test_each_rule_is_RESOLVED_exactly_once_per_build(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """The phrase and the next date are two questions about ONE read.

        ``read_rule`` returns the resolved meaning and the placements
        together, so a row costs one ``resolve``.  Composing the two steps at
        the call site instead would resolve twice per row -- a second
        resolution point in one request.

        Patched at the DEFINITION site the composition calls
        (``_reading.resolve``), not at this module's imported name: patching a
        re-export proves only that the harness reads what it reads.
        """
        expense = _create_expense(seed_user, MONTHLY, Decimal("100.00"), day_of_month=22)
        income = _create_income(seed_user, EVERY_PERIOD, Decimal("2000.00"))

        calls = []
        real_resolve = _reading.resolve

        def counting_resolve(spec, calendar):
            # The CADENCE identifies the call since plan step R7b, which is
            # what a spec carries now; the two definitions above are a monthly
            # bill and an every-paycheck income, so the two entries also say
            # the right rules were read.
            calls.append((spec.interval_n, spec.unit))
            return real_resolve(spec, calendar)

        monkeypatch.setattr(_reading, "resolve", counting_resolve)

        view = recurring_view.build_view(
            [income], [expense], [], _ctx(seed_user),
        )

        # The control must be shown to FIRE: if the patch missed, ``calls`` is
        # empty and "exactly two" would pass for the wrong reason.
        assert view.expenses.rows[0].recurrence is not None
        assert view.income.rows[0].recurrence is not None
        assert len(calls) == 2, (
            f"two rule-bearing definitions resolved {len(calls)} times; each "
            f"must be read once per build"
        )


class TestTheArchivedDrawer:
    """Archived definitions get a producer, not raw ORM handed to Jinja."""

    def test_an_archived_row_carries_its_worded_cadence(
        self, seed_user, seed_periods_today,
    ):
        """The drawer shows how a definition repeated before it was archived."""
        tmpl = _create_expense(seed_user, ANNUAL, Decimal("400.00"), day_of_month=1, month_of_year=11)
        tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [tmpl], _ctx(seed_user),
        )

        assert len(rows) == 1
        assert rows[0].template is tmpl
        assert rows[0].recurrence.cadence == "Yearly (Nov 1)"

    def test_a_rule_less_archived_row_carries_none(
        self, seed_user, seed_periods_today,
    ):
        """A non-repeating archived definition reads "One-time" in the cell."""
        tmpl = _create_expense(seed_user, None, Decimal("400.00"))
        tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [tmpl], _ctx(seed_user),
        )

        assert rows[0].recurrence is None

    def test_it_preserves_the_callers_order(
        self, seed_user, seed_periods_today,
    ):
        """The drawer's own sort is the route's; the producer does not re-sort.

        Unlike the active sections, which apply the locked cost-descending
        default -- archived rows have no cost to sort by.
        """
        first = _create_expense(seed_user, None, Decimal("1.00"), name="Zed")
        second = _create_expense(seed_user, None, Decimal("900.00"), name="Abe")
        for tmpl in (first, second):
            tmpl.is_active = False
        db.session.flush()

        rows = recurring_view.build_archived_rows(
            [first, second], _ctx(seed_user),
        )

        assert [row.template.name for row in rows] == ["Zed", "Abe"]

    def test_it_walks_no_occurrences(
        self, seed_user, seed_periods_today, monkeypatch,
    ):
        """An archived definition generates nothing, so nothing is placed.

        It needs the cadence and not where rows land, so it takes the read
        door's FIRST step alone.  Computing placements and discarding them is
        the defect ledger row D26 names, one surface over.
        """
        tmpl = _create_expense(seed_user, MONTHLY, Decimal("50.00"), day_of_month=9)
        tmpl.is_active = False
        db.session.flush()

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError(
                "the archived drawer walked occurrences it does not render"
            )

        monkeypatch.setattr(_reading, "occurrence_placements", fail_if_called)

        rows = recurring_view.build_archived_rows(
            [tmpl], _ctx(seed_user),
        )

        assert rows[0].recurrence.cadence == "Monthly (day 9)"
