"""Recurrence engine tests: generate_for_template(), the bound on which periods it places rows in,
and the one ownership a generated row's amount states.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.ref import TransactionType, Status
from app import ref_cache
from app.enums import AmountSourceEnum, TxnTypeEnum
from app.services import pay_period_write, recurrence_engine, status_seam
from app.services.pay_calendar import calendar_for
from app.services.recurrence import placed_periods, rule_occurrences
from app.services.recurrence._months import clamped_day, month_ordinal
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests.oracles.recurrence_baseline import EVERY_PERIOD, EVERY_N_PERIODS, MONTHLY
from tests._test_helpers import (
    derived_span,
    last_covered_day,
    make_cadence_rule,
    resolved_amount,
    rhythm_of,
    settlement_if_settling,
    start_test_pay_list,
    state_template_price,
)
from app.services.amount_ownership import state_own_amount

from ._helpers import _MONTHLY_DAY, build_rule, _calendar, _matched_periods


class TestRecurrenceGeneration:
    """Tests for generate_for_template()."""

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
            name='Test Recurring',
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        # The PRICE, through the app's one write door.  Both create doors state
        # one right after the flush, and since plan step balance:X-au-e a
        # generated row stores no figure and is priced by this series on its
        # own due date -- a template without one generates rows
        # ``_stated_amount`` refuses.
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence, **rule_kwargs,
        )

        # Load the relationships for the recurrence engine.
        db.session.refresh(template)
        return template

    def test_every_period_generates_for_all(self, app, db, seed_user, seed_periods):
        """every_period creates a transaction in every pay period."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == len(seed_periods)
            for txn in created:
                assert resolved_amount(txn) == Decimal("100.00")
                assert txn.name == "Test Recurring"

            # Verify 1:1 mapping between transactions and periods.
            period_ids = {txn.pay_period_id for txn in created}
            expected_ids = {p.id for p in seed_periods}
            assert period_ids == expected_ids, (
                f"Period ID mismatch: "
                f"missing={expected_ids - period_ids}, "
                f"extra={period_ids - expected_ids}"
            )

    def test_every_n_periods_with_offset(self, app, db, seed_user, seed_periods):
        """every_n_periods starting in period 1 generates every other period.

        The phase is the ordinal of the paycheck the opening bound falls in
        (plan step R7b-4), so starting on period index 1's own payday phases
        the rule at ``1 % 2 == 1`` -- indices 1, 3, 5, 7, 9.  It stated
        ``offset_periods=1`` directly until that step made the phase a
        derivation; the fired set is the same, reached from the fact a form
        can state.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_N_PERIODS,
                interval_n=2, starts_on=seed_periods[1].start_date,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # With 10 periods (indices 0-9), offset=1 matches indices 1,3,5,7,9 → 5.
            assert len(created) == 5
            for txn in created:
                period = db.session.get(
                    __import__("app.models.pay_period", fromlist=["PayPeriod"]).PayPeriod,
                    txn.pay_period_id,
                )
                assert (derived_span(period).period_index - 1) % 2 == 0

    def test_a_rule_less_template_generates_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A template that does not repeat auto-generates nothing.

        Rule-less is the ONE way a definition says "does not recur" since
        plan step R2e-3; this named the ``Once`` PATTERN before it, and that
        spelling had its own guard in ``resolve_generation_plan``.  Asserting
        the rule-less path is what still has a guard to protect -- the same
        assertion against a ``Once``-by-name rule would now pass through
        the retired matcher's unmodelled-pattern default instead, proving nothing
        about the branch it was written for.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
            )
            # ONE statement, exactly as
            # ``_recurrence_form_helpers._clear_recurrence_rule`` does since
            # plan step R-F6: dis-associating the rule is what deletes it,
            # because the relationship carries ``delete-orphan`` and the rule
            # holds the owning FK.  It was three statements -- null both sides,
            # then delete the row -- while the FK sat on the template, and an
            # explicit delete after this one now reports
            # ``expected to delete 1 row(s); 0 were matched``, because the
            # dis-association already removed it.
            template.recurrence_rule = None
            db.session.flush()
            assert template.recurrence_rule is None

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert len(created) == 0

    def test_skips_existing_entries(self, app, db, seed_user, seed_periods):
        """Does not create duplicates for periods that already have entries."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
            )

            # First generation.
            first_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first_run) == len(seed_periods)

            # Second generation -- should create nothing new.
            second_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_respects_is_override_flag(self, app, db, seed_user, seed_periods):
        """Overridden entries are not replaced during generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
            )

            # Generate entries.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Override one entry.
            created[0].is_override = True
            state_own_amount(created[0], Decimal("999.99"))
            db.session.flush()

            # Regenerate -- the overridden entry should be preserved.
            from app.exceptions import RecurrenceConflict

            try:
                recurrence_engine.regenerate_for_template(
                    template, GenerationSchedule.for_period_ids(
                        BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                    ), seed_user["scenario"].id,
                )
            except RecurrenceConflict as conflict:
                assert created[0].id in conflict.overridden

            # The overridden amount should still be there.
            db.session.refresh(created[0])
            assert created[0].estimated_amount == Decimal("999.99")

    def test_never_touches_done_transactions(self, app, db, seed_user, seed_periods):
        """Done/received/credit transactions are immutable to the engine."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Mark the first one as done, through the real seam: it writes the
            # whole settlement record in one act (plan step X-au-c3), and a
            # bare status assign leaves a state the record's own CHECKs refuse.
            done_status = db.session.query(Status).filter_by(name="Paid").one()
            status_seam.apply_status_change(
                created[0], done_status.id,
                settlement=settlement_if_settling(created[0], done_status.id),
            )
            db.session.flush()

            # Regenerate -- should not delete the done transaction.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transaction should still exist unchanged: the settle
            # recorded the row's own plan on the ``derived`` basis, and the
            # regenerate touched neither.
            db.session.refresh(created[0])
            assert status_seam.recorded_settlement(created[0]).amount == (
                Decimal("100.00")
            )


# --- DB Integration Tests ----------------------------------------------------


class TestGenerateForTemplate:
    """DB integration tests for generate_for_template()."""

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
            name='Test Recurring',
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

    def test_a_bill_repeating_inside_one_paycheck_generates_each_occurrence(
        self, app, db, seed_user, seed_periods
    ):
        """A monthly bill at a 90-day cadence writes ALL THREE rows.

        **Plan step R4a made the repeat reachable and plan step R17 made it
        storable, and this is the regression test for both.**  The reverse
        matcher walked PAYCHECKS, so a monthly rule emitted one row per
        paycheck and silently dropped the rest -- defect D3.  Forward
        generation emits every occurrence, and while the unique index was keyed
        ``(template, pay_period, scenario)`` the pass could not store them:
        generation REFUSED with ``RecurrenceCadenceUnsupported`` rather than
        under-budget the paycheck (plan ledger row D19, developer ruling
        2026-08-08).

        R17 re-keyed the index onto ``(template, scenario, occurs_on)``,
        because a generated row's identity is the OCCURRENCE it answers and
        never the paycheck its money lands in.  Three occurrences inside one
        90-day paycheck are three distinct keys, so all three now store and the
        refusal was deleted with the exception and its error handler
        (developer ruling 2026-08-28).

        What this asserts is the money: a monthly bill owed three times inside
        one long paycheck is BUDGETED three times.  The old behaviour left the
        owner unable to generate at all; the behaviour before that silently
        under-budgeted by two installments.
        """
        with app.app_context():
            # A 90-day schedule for this owner alone; ``cadence_days`` is
            # user-selectable 1..365, so this is configuration, not a
            # hypothetical.  Built after the seed periods so the batch opens
            # strictly after the latest existing end_date.
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=4,
                rhythm=rhythm_of(90),
            )
            db.session.flush()
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )

            # The premise, asserted rather than assumed: without this the test
            # would pass vacuously if the engine ever stopped repeating a
            # paycheck, and would then prove nothing about the re-key.
            matched = _matched_periods(
                template.recurrence_rule,
                calendar_for(seed_user["user"].id),
                long_periods[0].start_date,
            )
            assert len(matched) > len(
                {period.period_index for period in matched}
            ), (
                "the engine no longer repeats a period, so this test proves "
                "nothing about the re-keyed index"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id),
                    {p.id for p in long_periods},
                ), seed_user["scenario"].id,
            )
            # The flush is the assertion that matters: under the paycheck-keyed
            # index these three rows were an IntegrityError.
            db.session.flush()

            # The expected dates are DERIVED from the paycheck's own span
            # rather than typed as literals, so this asserts the fact the
            # cadence is about (a monthly bill is owed on every 15th the
            # paycheck covers) instead of three figures that would still pass
            # if the fixture's start date moved.
            paycheck = long_periods[0]
            expected_dates = tuple(
                day
                for day in (
                    date(year, month, 15)
                    for year in range(
                        paycheck.start_date.year, last_covered_day(paycheck).year + 1,
                    )
                    for month in range(1, 13)
                )
                if paycheck.start_date <= day <= last_covered_day(paycheck)
            )
            assert len(expected_dates) == 3, (
                "a 90-day paycheck must cover the 15th of three months, or this "
                "fixture no longer exercises the repeat"
            )

            # Every occurrence answered, each by its own row, all three inside
            # the ONE paycheck.
            in_paycheck = [
                row for row in created if row.pay_period_id == paycheck.id
            ]
            assert sorted(
                row.occurs_on for row in in_paycheck
            ) == list(expected_dates)
            assert db.session.query(Transaction).filter_by(
                template_id=template.id, pay_period_id=paycheck.id,
            ).count() == 3

    def test_a_repeat_is_not_written_twice_by_a_second_pass(
        self, app, db, seed_user, seed_periods
    ):
        """The second pass over a repeated paycheck adds NOTHING.

        The companion to the test above, and the one that would catch an
        occurrence-keyed predicate that had quietly gone back to asking about
        the period: with three rows already answering three occurrences inside
        one paycheck, a re-run must find every one of them answered.  A
        predicate keyed on the paycheck would also pass this; a predicate that
        matched rows by POSITION, or one that compared an occurrence against
        the wrong row's date, would not.
        """
        with app.app_context():
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=4,
                rhythm=rhythm_of(90),
            )
            db.session.flush()
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            window = {p.id for p in long_periods}
            first = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), window,
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(first) > 0

            second = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), window,
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert second == []
            assert db.session.query(Transaction).filter_by(
                template_id=template.id,
            ).count() == len(first)

    def test_a_populated_long_cadence_schedule_stays_extendable(
        self, app, db, seed_user, seed_periods
    ):
        """An already-populated repeat paycheck adds nothing on a later pass.

        This asserted an ORDERING while the repeat was refused: the refusal ran
        after the per-period skip, so an already-populated long-cadence
        schedule stayed extendable instead of refusing over paychecks the pass
        was never going to write.  Plan step **R17** deleted the refusal
        (the re-keyed index stores the repeat), so what is left to grade is the
        property that ordering existed to protect -- a second pass over a
        populated repeat writes nothing and raises nothing.

        **What distinguishes it from the case above is the UNDATED claim.**
        The rows are the engine's own (plan step balance:X-cf), and then ONE
        row per paycheck has its ``occurs_on`` cleared -- the pre-R17 shape a
        backfill leaves where no occurrence claims a row, and the shape
        carry-forward still writes -- so each paycheck is HELD by a row that
        answers no occurrence (``OccurrenceClaims.held_undated``) beside two
        that answer their own.  A second pass must write nothing: not the
        two answered occurrences, and not the third, whose only claim on the
        paycheck is the undated row's.  Measured: reading the undated row as
        claiming nothing re-writes that third occurrence and this goes red.
        """
        with app.app_context():
            long_periods = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=4,
                rhythm=rhythm_of(90),
            )
            db.session.flush()
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            window = {p.id for p in long_periods}
            populated = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), window,
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            held = set()
            for row in populated:
                if row.pay_period_id not in held:
                    row.occurs_on = None
                    held.add(row.pay_period_id)
            db.session.flush()
            assert held == window, (
                "every paycheck in the window must hold an undated row, or "
                "this case no longer grades the period claim"
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), window,
                ), seed_user["scenario"].id,
            )

            assert created == []

    def test_effective_from_skips_earlier_periods(
        self, app, db, seed_user, seed_periods
    ):
        """effective_from = 4th period's start → only generates from period 4."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            effective_from = seed_periods[3].start_date
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
                effective_from=effective_from,
            )

            # 10 periods total, skip first 3 → 7 created.
            assert len(created) == 7
            for txn in created:
                period = db.session.get(
                    __import__("app.models.pay_period", fromlist=["PayPeriod"]).PayPeriod,
                    txn.pay_period_id,
                )
                assert period.start_date >= effective_from

    def test_skips_deleted_entries(self, app, db, seed_user, seed_periods):
        """Soft-deleted entries are not duplicated on re-generation."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            # First generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == 10

            # Soft-delete one entry.
            created[2].is_deleted = True
            db.session.flush()

            # Second generation -- should not duplicate the deleted entry.
            second_run = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            assert len(second_run) == 0

    def test_monthly_pattern_generates_correct_count(
        self, app, db, seed_user, seed_periods
    ):
        """Monthly pattern across 10 periods produces one per unique month."""
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=15,
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # 10 biweekly periods starting Jan 2 span ~5 months.
            # Determine expected unique months from periods.
            unique_months = set()
            for p in seed_periods:
                for dt in (p.start_date, last_covered_day(p)):
                    target = date(dt.year, dt.month, 15)
                    if p.start_date <= target <= last_covered_day(p):
                        unique_months.add((dt.year, dt.month))
            assert len(created) == len(unique_months)


class TestThePlacedPeriodsBound:
    """``ending_on_or_after`` bounds a period's END, never its START.

    **The predicate three production surfaces share** -- the Recurring
    surface's next-date column, the form's occurrence preview, and this
    module's own ported assertions all reach it through
    :func:`~app.services.recurrence.placed_periods`, and the generation seam
    applies the identical comparison to its ``effective_from``.  Nothing
    asserted it: a neutral review mutated the comparison to ``start_date`` in
    all four places and 3,714 tests stayed green, because the only test that
    passed a bound passed a PERIOD BOUNDARY, where the two readings select
    identically.

    The distinction is real money.  A bound falling INSIDE a period means "from
    here forward", and the period the user is standing in still holds rows they
    are owed -- ``regenerate_for_template`` sweeps and rewrites from exactly
    such a date.  Reading it against the period's START would drop the current
    paycheck from the rewrite while the sweep had already deleted its rows.
    """

    def _monthly_placements(self, periods):
        """Placements of a day-15 monthly rule over *periods*."""
        return rule_occurrences(
            build_rule(cadence=MONTHLY, starts_on=date(2026, 1, 15)),
            _calendar(periods),
        )

    def test_a_bound_inside_a_period_keeps_that_period(self, biweekly_periods):
        """The straddling period survives -- the whole point of ``end_date``.

        Under a ``start_date`` reading it would be dropped, which is what makes
        this case, and not a boundary case, the one worth asserting.
        """
        placements = self._monthly_placements(biweekly_periods)
        placed = [p.period for p in placements if p.period is not None]
        straddled = placed[3]
        # Strictly inside: after the period opens, before it closes.
        bound = straddled.start_date + timedelta(days=1)
        assert bound < straddled.end_date, "the fixture gave no interior day"

        kept = placed_periods(placements, ending_on_or_after=bound)

        assert straddled in kept, (
            "a period the bound falls INSIDE must be kept: the bound means "
            "'from here forward', and this paycheck is still ahead of the user"
        )
        # And it is the FIRST kept period -- everything before it is dropped.
        assert kept[0] == straddled
        assert all(period.end_date >= bound for period in kept)

    def test_a_bound_one_day_past_a_period_drops_it(self, biweekly_periods):
        """The complement, so the bound is shown to bound something.

        Without this the assertion above would pass for a function that
        filtered nothing at all.
        """
        placements = self._monthly_placements(biweekly_periods)
        placed = [p.period for p in placements if p.period is not None]
        straddled = placed[3]
        bound = straddled.end_date + timedelta(days=1)

        kept = placed_periods(placements, ending_on_or_after=bound)

        assert straddled not in kept
        assert len(kept) == len(placed) - 4

    def test_no_bound_keeps_every_placed_period(self, biweekly_periods):
        """``None`` applies no bound -- the default every read surface uses."""
        placements = self._monthly_placements(biweekly_periods)

        assert placed_periods(placements) == [
            p.period for p in placements if p.period is not None
        ]

    def test_the_generation_seam_applies_the_same_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """``effective_from`` bounds the period's END in the seam too.

        The seam keeps its own copy of the comparison -- it walks the pairs to
        build ``PlannedOccurrence`` values and to collect the gaps, so it cannot
        take the shared projection -- which is exactly why the copy needs its
        own assertion rather than inheriting the three above.
        """
        with app.app_context():
            template = self._make_template(seed_user)
            schedule = GenerationSchedule.for_pass(BalanceContext.build(template.user_id))
            straddled = seed_periods[3]
            bound = straddled.start_date + timedelta(days=1)
            assert bound < last_covered_day(straddled)

            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, bound,
                block_message="test",
            )

            kept = [row.period.period_id for row in plan.placements]
            assert straddled.id in kept, (
                "the seam dropped the period its bound falls inside"
            )
            assert seed_periods[2].id not in kept

    def _make_template(self, seed_user):
        """A day-15 monthly expense template for this owner.

        The first occurrence is the first 15th the schedule reaches, which is
        what the pre-R7c-b derivation answered for a day-15 rule stating no
        opening bound -- so this template fires in the same periods it always
        did, and the assertions above still measure ``effective_from``.
        """
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        opening = calendar_for(seed_user["user"].id).opening_bound()
        ordinal = month_ordinal(opening)
        first_fifteenth = clamped_day(
            ordinal if opening.day <= _MONTHLY_DAY else ordinal + 1,
            _MONTHLY_DAY,
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            transaction_type_id=expense_type.id,
            name="Bounded Bill",
            default_amount=Decimal("100.00"),
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, MONTHLY, starts_on=first_fifteenth,
        )
        db.session.refresh(template)
        return template


class TestWhatAGeneratedRowsAmountOWNERSHIPIs:
    """Plan step **balance:X-au-e**: generation states ONE ownership, always.

    **This class was ``TestPaycheckAmountFallback`` and its subject is
    deleted.**  It graded ``_get_transaction_amount``'s exception narrowing --
    that a ``ZeroDivisionError`` or an ``InvalidOperation`` out of the paycheck
    calculator fell back to ``template.default_amount`` while an
    ``AttributeError`` propagated (C-01).  Generation runs no pricing at all
    now, so there is no call to raise and no fallback to narrow.

    **Its second subject is deleted too, and this is that rewrite.**  X-au-d
    replaced the fallback with a FORK -- ``_generated_amount_ownership`` asking
    ``template_amount_service.owns_its_amount``, ``own`` over the scalar for a
    definition that states its price and ``derived`` for one whose price is
    computed -- and X-au-e deletes the fork rather than one of its arms.  There
    is no producer left to unit-test, so both cases below grade the public act
    instead: what ``generate_for_template`` actually WRITES.

    **Both cases are kept even though they now assert the same shape**, and
    that is the point rather than a redundancy: a producer that declared only
    the salary rows -- which is exactly what shipped at X-au-d -- passes the
    first and fails the second.  Deleting the second because it stopped
    distinguishing anything would delete the only case that says the cutover
    happened.
    """

    def _generated_row(self, db, seed_user, seed_periods, template):
        """Generate from *template* and return the first row it wrote."""
        make_cadence_rule(template, EVERY_PERIOD)
        db.session.refresh(template)
        created = recurrence_engine.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            ), seed_user["scenario"].id,
        )
        db.session.flush()
        assert created, "the fixture generated no rows to assert about"
        return created[0]

    def test_a_salary_linked_definition_gives_its_rows_a_DECLARATION(
        self, app, db, seed_user, seed_periods,
    ):
        """A definition an ACTIVE profile names prices its rows by computing.

        So the row names the definition and holds no figure: the state that
        makes a stale paycheck unrepresentable rather than merely unlikely.
        Shipped at X-au-d and unchanged by X-au-e.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the salary models are not
            # this module's subject and importing them at the top would put the
            # paycheck stack on every recurrence test's load path.
            from app.models.ref import FilingStatus  # pylint: disable=import-outside-toplevel
            # Pylint: ``import-outside-toplevel`` -- see above.
            from app.models.salary_profile import SalaryProfile  # pylint: disable=import-outside-toplevel

            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=next(iter(seed_user["categories"].values())).id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
                name="Paycheck",
                default_amount=Decimal("1500.00"),
            )
            db.session.add(template)
            db.session.flush()
            salary = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                template_id=template.id,
                name="X-au-d Salary",
                state_code="NC",
                is_active=True,
            )
            db.session.add(salary)
            start_test_pay_list(salary, Decimal("4000.00"))  # $104,000.00 a year / 26
            db.session.flush()

            row = self._generated_row(db, seed_user, seed_periods, template)

            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )

    def test_a_definition_that_STATES_its_price_ALSO_gives_a_DECLARATION(
        self, app, db, seed_user, seed_periods,
    ):
        """The partner case, and the one plan step X-au-e inverted.

        It asserted ``ownership.figure == Decimal("500.00")`` and
        ``source_id is None`` -- generation handing an ordinary definition's
        rows that definition's scalar to OWN.  That is the copy the cutover
        deleted: the row declares the template and the template's own
        effective-dated series prices it on the row's due date, which is what
        the last assertion here reads.
        """
        with app.app_context():
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=next(iter(seed_user["categories"].values())).id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                name="Rent",
                default_amount=Decimal("500.00"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)

            row = self._generated_row(db, seed_user, seed_periods, template)

            assert row.estimated_amount is None
            assert row.amount_source_id == ref_cache.amount_source_id(
                AmountSourceEnum.TEMPLATE,
            )
            # The figure is not lost, it has one home: the definition's series.
            assert resolved_amount(row) == Decimal("500.00")
