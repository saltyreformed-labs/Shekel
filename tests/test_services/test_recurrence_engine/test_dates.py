"""Recurrence engine tests: a rule's optional end date, and the due date of a generated row."""

import pytest
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.ref import TransactionType
from app import ref_cache
from app.enums import RecurrenceUnitEnum
from app.services import recurrence_engine
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence import RecurrenceResolutionError, compute_due_date
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    EVERY_N_PERIODS,
    MONTHLY,
    QUARTERLY,
    SEMI_ANNUAL,
    ANNUAL,
)
from tests._test_helpers import (
    make_cadence_rule,
    rebuild_calendar_from_spans,
    state_template_price,
)

from ._helpers import build_rule, _calendar, _matched_periods, _paycheck_covering


class TestEndDate:
    """Tests for the optional end_date on recurrence rules."""

    def test_end_date_limits_every_period(self, biweekly_periods):
        """end_date stops generation after that date (every_period)."""
        # End date after the 5th period's start_date (period index 4).
        end = biweekly_periods[4].start_date
        rule = build_rule(cadence=EVERY_PERIOD, end_date=end)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 5
        for p in matched:
            assert p.start_date <= end

    def test_end_date_none_means_indefinite(self, biweekly_periods):
        """NULL end_date generates for all periods (no change from default)."""
        rule = build_rule(cadence=EVERY_PERIOD, end_date=None)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert len(matched) == 26

    def test_end_date_with_monthly_pattern(self, biweekly_periods):
        """end_date works with monthly pattern -- only months before end."""
        # End in March 2026.
        rule = build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 15),
                        end_date=date(2026, 3, 31))
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        # Should get Jan, Feb, Mar only.
        assert len(matched) == 3
        for p in matched:
            assert p.start_date <= date(2026, 3, 31)

    def test_end_date_before_first_period(self, biweekly_periods):
        """end_date before all periods returns empty list."""
        rule = build_rule(cadence=EVERY_PERIOD,
                        end_date=date(2025, 12, 31))
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        assert matched == []

    def test_end_date_with_effective_from_both_filter(self, biweekly_periods):
        """Both effective_from and end_date narrow the window."""
        # effective_from at period 5, end_date at period 10.
        effective_from = biweekly_periods[5].start_date
        end = biweekly_periods[10].start_date
        rule = build_rule(cadence=EVERY_PERIOD, end_date=end)

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        # Periods 5 through 10 inclusive.
        assert len(matched) == 6
        for p in matched:
            assert p.start_date >= effective_from
            assert p.start_date <= end

    def test_end_date_mid_period_includes_that_period(self, biweekly_periods):
        """A period whose start_date is on the end_date is included."""
        target_period = biweekly_periods[7]
        rule = build_rule(cadence=EVERY_PERIOD,
                        end_date=target_period.start_date)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        # ``rule_occurrences`` answers in the calendar's own ``DerivedPeriod``
        # values since plan step R4b-1, so identity against the fixture's
        # ``FakePeriod`` no longer holds; the schedule ordinal is the stable
        # identity either way.
        assert target_period.period_index in [p.period_index for p in matched]

    def test_end_date_with_every_n_periods(self, biweekly_periods):
        """end_date works correctly with every_n_periods pattern."""
        # Every 3 periods, end at period 12.
        end = biweekly_periods[11].start_date
        rule = build_rule(cadence=EVERY_N_PERIODS, interval_n=3,
                        end_date=end)
        effective_from = biweekly_periods[0].start_date

        matched = _matched_periods(rule, _calendar(biweekly_periods),
                                 effective_from)

        # Periods 0, 3, 6, 9 (index % 3 == 0 and start_date <= end).
        assert len(matched) == 4
        for p in matched:
            assert p.period_index % 3 == 0
            assert p.start_date <= end


class TestEndDateIntegration:
    """Integration tests for end_date with generate_for_template()."""

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Car Payment'].id,
            transaction_type_id=expense_type.id,
            name='Test Recurring End Date',
            default_amount=Decimal("50.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def test_generate_respects_end_date(self, app, db, seed_user, seed_periods):
        """generate_for_template stops at end_date."""
        with app.app_context():
            # Use the 5th period's start_date as end_date.
            end = seed_periods[4].start_date
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD, end_date=end,
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 5
            for txn in created:
                period = txn.pay_period
                assert period.start_date <= end

    def test_regenerate_respects_end_date(self, app, db, seed_user, seed_periods):
        """regenerate_for_template leaves an end-dated rule bounded at 3 rows.

        **RE-RULED at plan step R10-a** (ruling **R-R19**): the assertion was
        ``len(regenerated) == 3``, which read the count of rows the pass
        CREATED as the count of rows the rule names.  Those were the same
        number only while a regeneration rebuilt everything it touched.  The
        step's intent -- an ``end_date`` bounds a regeneration exactly as it
        bounds a generation -- is unchanged and is now asserted against the
        rows themselves, which is what it was always about.
        """
        with app.app_context():
            end = seed_periods[2].start_date
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD, end_date=end,
            )

            # Initial generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(created) == 3

            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            surviving = (
                db.session.query(Transaction)
                .filter(Transaction.template_id == template.id)
                .all()
            )
            assert len(surviving) == 3
            for txn in surviving:
                assert txn.pay_period.start_date <= end


# --- Due Date Generation Tests -----------------------------------------------


class TestDueDateGeneration:
    """Tests for due_date computation during transaction generation.

    Verifies that generate_for_template correctly computes due_date on
    each created Transaction by delegating to compute_due_date.  Tests
    cover every recurrence pattern, day-of-month clamping for short
    months, and edge cases around leap years and month boundaries.  The
    due-day tests (the next-month convention and its clamping) left with
    ``due_day_of_month`` at plan step R5-a (ruling R-R96).
    """

    def _make_template_with_rule(self, seed_user, cadence, **rule_kwargs):
        """Create a template whose rule is AUTHORED, not hand-built.

        The rule half is ``_test_helpers.make_cadence_rule``: nine copies of
        this helper differed only in the template's name, amount and category,
        and every one of them constructed a ``RecurrenceRule`` field by field.
        Plan step R7c-b made that construction impossible -- ``unit_id``,
        ``placement_id``, ``shift_id`` and ``starts_on`` are ``NOT NULL`` -- so
        the rule is authored through the same door a form goes through.
        """
        expense_type = (
            db.session.query(TransactionType)
            .filter_by(name="Expense")
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]['Rent'].id,
            transaction_type_id=expense_type.id,
            name='Test Template',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def _make_custom_periods(self, seed_user, *spans):
        """Rebuild the owner's calendar so its periods OPEN on these spans.

        **These built ``PayPeriod(...)`` rows by hand until plan step
        ``pay_calendar:C4-b-1``, and every case in this class passed BECAUSE
        of that.**  A hand-built row sets ``end_date`` itself, and the owner
        had no ``budget.pay_schedule`` row, so
        ``pay_schedule_service.resolve_schedule`` inferred the cadence from
        that same typed end -- the derivation then handed back the span the
        author had written, and the case was measuring its own input.  Give
        the owner the stored cadence a real one has and a typed 28-day
        February derives 14 days and generates nothing.

        ``rebuild_calendar_from_spans`` builds them through the reset door and
        the writer instead.  What that changes for a caller: an INTERIOR
        span's end runs to the day before the next span opens, so a gapped
        request gets wider interior periods than it asked for (a gap is not
        expressible in a derived calendar).  Every case here asserts on the
        occurrence's own DAY, which lands in the same period either way, and
        the LAST span -- the one end the derivation projects from the cadence
        -- is exact.

        **What this does NOT remove, and an adversarial review of the step is
        why it is written down**: the owner's cadence is still the last span's
        length, so the date a case types still decides it.  What is gone is the
        CIRCULARITY -- the cadence is now a stored fact the writer persisted,
        not a value inferred back out of the ``end_date`` the same case typed,
        and a 28-day payer is an owner production can have.  The coupling
        survives one level up and is a caller's choice rather than a loop.

        Args:
            seed_user: The seed_user fixture dict.
            *spans: ``(start, end)`` pairs, ascending, at least one.

        Returns:
            The owner's periods, payday ascending -- one per span.
        """
        return rebuild_calendar_from_spans(seed_user["user"].id, list(spans))

    def _make_custom_period(self, seed_user, start, end):
        """Rebuild the owner's calendar as the ONE period *start*..*end*.

        :meth:`_make_custom_periods` for the single-span cases, where the
        stated end is exact: it is the last span, so the owner's cadence is
        its length and the derivation projects precisely it.

        Args:
            seed_user: The seed_user fixture dict.
            start: The period's start_date, which is the owner's only payday.
            end: The period's end_date.

        Returns:
            The created PayPeriod.
        """
        return self._make_custom_periods(seed_user, (start, end))[0]

    # -- Basic pattern tests ---------------------------------------------------

    def test_due_date_monthly_pattern(self, app, db, seed_user, seed_periods):
        """Monthly pattern with fires_on_day=15 sets due_date to the 15th.

        seed_periods P0 = Jan 2 - Jan 15, which contains Jan 15.
        Expected: txn.due_date == 2026-01-15.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # Find the transaction assigned to the period containing Jan 15.
            jan_txns = [
                txn for txn in created
                if txn.pay_period_id == _paycheck_covering(
                    seed_user, date(2026, 1, 15),
                )
            ]
            assert len(jan_txns) == 1
            assert jan_txns[0].due_date == date(2026, 1, 15)

    def test_due_date_every_period_pattern(
        self, app, db, seed_user, seed_periods
    ):
        """Every Period with no day_of_month sets due_date to period.start_date.

        P0 starts Jan 2, so due_date == 2026-01-02.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            # First transaction should have due_date == first period's start.
            first_txn = [
                t for t in created if t.pay_period_id == seed_periods[0].id
            ][0]
            assert first_txn.due_date == date(2026, 1, 2)

            # Verify all transactions use their period's start_date.
            period_start_map = {p.id: p.start_date for p in seed_periods}
            for txn in created:
                assert txn.due_date == period_start_map[txn.pay_period_id]

    # -- Month-end clamping tests ----------------------------------------------

    def test_due_date_feb_clamping(self, app, db, seed_user):
        """fires_on_day=30 clamped to Feb 28 in a non-leap year (2026).

        Custom period covers all of February to ensure Feb 28 falls inside.
        min(30, 28) = 28, so due_date == 2026-02-28.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 2, 1), date(2026, 2, 28),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=30,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 2, 28)

    def test_due_date_feb_leap_year(self, app, db, seed_user):
        """fires_on_day=29 in Feb 2028 (leap year) gives Feb 29.

        2028 is a leap year, so min(29, 29) = 29.
        due_date == 2028-02-29.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2028, 2, 1), date(2028, 2, 29),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=29,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2028, 2, 29)

    def test_due_date_day31_in_30day_month(self, app, db, seed_user):
        """fires_on_day=31 clamped to 30 in April (30-day month).

        April has 30 days: min(31, 30) = 30.
        due_date == 2026-04-30.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 4, 1), date(2026, 4, 30),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=31,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 4, 30)

    # -- No recurrence rule ----------------------------------------------------

    def test_due_date_no_recurrence_rule(
        self, app, db, seed_user, seed_periods
    ):
        """Template with no recurrence rule returns empty list.

        A template with recurrence_rule_id=None is manually placed and
        generate_for_template returns [] without computing any due_date.
        """
        with app.app_context():
            from app.models.ref import TransactionType
            from app.models.transaction_template import TransactionTemplate

            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense")
                .one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                name="No Rule Template",
                default_amount=Decimal("100.00"),
                category_id=seed_user["categories"]["Rent"].id,
                transaction_type_id=expense_type.id,
                account_id=seed_user["account"].id,
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            db.session.refresh(template)

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert created == []

    # -- Quarterly and annual patterns -----------------------------------------

    def test_due_date_quarterly_pattern(self, app, db, seed_user):
        """Quarterly pattern with fires_in_month=1 produces due dates in Jan, Apr, Jul, Oct.

        Creates four custom periods -- one covering the 15th of each
        quarterly month -- and asserts each transaction's due_date.
        """
        with app.app_context():
            # Four periods, each spanning the full target month.
            quarters = [
                (date(2026, 1, 1), date(2026, 1, 31)),   # Jan
                (date(2026, 4, 1), date(2026, 4, 30)),   # Apr
                (date(2026, 7, 1), date(2026, 7, 31)),   # Jul
                (date(2026, 10, 1), date(2026, 10, 31)), # Oct
            ]
            periods = self._make_custom_periods(seed_user, *quarters)

            template = self._make_template_with_rule(
                seed_user, QUARTERLY,
                fires_in_month=1, fires_on_day=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 4
            expected_dates = [
                date(2026, 1, 15),
                date(2026, 4, 15),
                date(2026, 7, 15),
                date(2026, 10, 15),
            ]
            actual_dates = sorted(txn.due_date for txn in created)
            assert actual_dates == expected_dates

    def test_due_date_annual_pattern(self, app, db, seed_user):
        """Annual pattern with fires_in_month=10, fires_on_day=1 gives Oct 1.

        Custom period covers October 2026.
        due_date == 2026-10-01.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 10, 1), date(2026, 10, 31),
            )
            template = self._make_template_with_rule(
                seed_user, ANNUAL,
                fires_in_month=10, fires_on_day=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 10, 1)

    def test_due_date_semi_annual_pattern(self, app, db, seed_user):
        """Semi-Annual with fires_in_month=1 produces due dates in Jan and Jul.

        Two custom periods cover Jan and Jul. fires_on_day=15.
        """
        with app.app_context():
            periods = self._make_custom_periods(
                seed_user,
                (date(2026, 1, 1), date(2026, 1, 31)),
                (date(2026, 7, 1), date(2026, 7, 31)),
            )
            template = self._make_template_with_rule(
                seed_user, SEMI_ANNUAL,
                fires_in_month=1, fires_on_day=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 2
            actual_dates = sorted(txn.due_date for txn in created)
            assert actual_dates == [date(2026, 1, 15), date(2026, 7, 15)]

    # -- Period spanning two months --------------------------------------------

    def test_due_date_period_spanning_two_months(self, app, db, seed_user):
        """fires_on_day=1, period Jan 17 - Feb 1: due_date is Feb 1, not Jan 1.

        The period spans two months. Jan 1 is before the period start;
        Feb 1 is the occurrence the rule places in [Jan 17, Feb 1], and the
        row is due on it (plan step R5-a dates a row from its occurrence --
        it scanned the period's two endpoint months until then).
        due_date == 2026-02-01.
        """
        with app.app_context():
            period = self._make_custom_period(
                seed_user, date(2026, 1, 17), date(2026, 2, 1),
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=1,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {period.id},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 1
            assert created[0].due_date == date(2026, 2, 1)

    # -- Pure function test for compute_due_date ------------------------------

    def test_compute_due_date_is_pure_function(self, app, db):
        """compute_due_date does not touch the database -- it's a pure function.

        Constructs a rule and a REAL :class:`DerivedPeriod` to verify that
        compute_due_date can produce correct results without any DB
        interaction.

        **The period is the real value and not ``FakePeriod``** since
        pay-calendar plan step C4-a-3, which measured the double out of
        contract: this function's documented parameter is a ``DerivedPeriod``,
        it now asks that value's own ``covers``, and the duck-typed stand-in
        answered ``AttributeError``.  A ``DerivedPeriod`` is a frozen
        dataclass over five plain fields, so building one costs the purity
        claim nothing -- the fake was never buying anything the real type
        does not give.
        """
        with app.app_context():
            from app import ref_cache
            # Test with a day-of-month cadence (monthly-style).
            rule_monthly = build_rule(
                cadence=MONTHLY, starts_on=date(2026, 1, 20),
            )
            period = DerivedPeriod(
                period_id=1,
                period_index=5,
                start_date=date(2026, 3, 13),
                end_date=date(2026, 3, 26),
                end_is_projected=False,
            )
            result = compute_due_date(
                rule_monthly, date(2026, 3, 20), period,
            )
            assert result == date(2026, 3, 20)

            # Test with a cadence that names no day (every-period style).
            rule_every = build_rule(cadence=EVERY_PERIOD)
            result = compute_due_date(
                rule_every, date(2026, 3, 13), period,
            )
            assert result == date(2026, 3, 13)

    def test_this_function_is_why_the_WEEK_unit_is_unofferable(self, app, db):
        """The tie between the offer set's rule and this function's two sources.

        **``_offer.has_row_date_coordinate`` exists for THIS function**, and
        until this case nothing but a docstring said so -- which an adversarial
        review of plan step R8-a named as the coupling's weak point: if a later
        step gave ``compute_due_date`` a third date source, that predicate
        would go on withholding the ``WEEK`` unit with every gate green.

        The two sources are all there are, and the assertions below are them:
        the OCCURRENCE, for a cadence dated from a day of the month (plan step
        R5-a), and -- for one naming no day -- the funding paycheck's own
        ``start_date``.  For the ``PERIOD`` unit the second IS the occurrence,
        so a row dated from it carries the date the cadence named.  For the
        ``WEEK`` unit it is NOT: a weekly occurrence is a calendar date
        strictly inside its paycheck, and the unit names no day of the month,
        so dating from the paycheck discards the authored weekday for the life
        of the rule.

        That is why the unit is withheld rather than refused at the door.
        Plan step **R8-b** is what deletes both the predicate and this case,
        by dating a weekly row from its occurrence too.
        """
        with app.app_context():
            period = DerivedPeriod(
                period_id=1,
                period_index=5,
                start_date=date(2026, 3, 13),
                end_date=date(2026, 3, 26),
                end_is_projected=False,
            )
            starts_on = date(2026, 1, 20)

            # A hand-edited or restored row naming the WEEK unit: the one way
            # to reach this state, since the write door and the picker both
            # refuse the cadence.
            weekly = build_rule(
                cadence=MONTHLY, starts_on=starts_on,
            )
            weekly.unit_id = ref_cache.recurrence_unit_id(
                RecurrenceUnitEnum.WEEK,
            )

            with pytest.raises(
                RecurrenceResolutionError, match="generated row",
            ):
                compute_due_date(weekly, date(2026, 3, 17), period)

            # And what the refusal is standing in front of: the paycheck's own
            # start, which is not any date a weekly cadence from ``starts_on``
            # fires on.  Both facts asserted, because the refusal is only worth
            # having while the fallback would be WRONG.
            weekly_occurrences = {
                starts_on + timedelta(days=7 * step) for step in range(60)
            }
            assert period.start_date not in weekly_occurrences
            assert weekly_occurrences & {
                date(2026, 3, 17), date(2026, 3, 24),
            } == {date(2026, 3, 17), date(2026, 3, 24)}
