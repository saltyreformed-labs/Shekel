"""Recurrence engine tests: a day no pay period covers, absorbed by the paycheck before it."""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.services._recurrence_common import TemplateRowSelector, rows_this_pass_may_maintain
from app.models.transaction_template import TransactionTemplate
from app.models.ref import TransactionType
from app.services import pay_period_write, recurrence_engine
from app.services.pay_calendar import calendar_for
from app.services.recurrence import rule_occurrences
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests.oracles.recurrence_baseline import MONTHLY, MONTHLY_FIRST
from tests._test_helpers import (
    record_paydays_across_a_hole,
    all_periods,
    last_covered_day,
    make_cadence_rule,
    rhythm_of,
    state_template_price,
)


class TestALegacyScheduleHole:
    """A day no pay period covers: ABSORBED by the paycheck before it.

    **This class asserted the opposite until plan step C2-b2, and the reversal
    IS the step** (plan ledger rows **D7** / **P27**).  Pay periods were not
    contiguous by construction, so a day could belong to no paycheck; the
    engine answered ``PlacementOutcome.SCHEDULE_GAP``, logged
    ``recurrence_occurrence_unplaced`` at WARNING naming the orphaned dates,
    and generated no row for them.  C2-b2 pointed the engine at the DERIVED
    calendar, in which a period runs to the day before the next payday.  A hole
    is then not a state a READER can see: the preceding paycheck covers those
    days.  The outcome enum, ``GenerationPlan.gaps`` and
    ``report_schedule_gaps`` went with the state they described.

    **Two writers had to close before the reader could stop looking, and both
    have**: ``balance:X-ad-a`` deleted the registration bootstrap payday, and
    plan step **C3-b** replaced the batch guard with a writer that materialises
    the derivation.  The first test below is the CONTROL for that closure.

    **And since plan step ``pay_calendar:C4-c`` the stored rows cannot hold a
    hole either.**  ``end_date`` is dropped, so a period's last covered day is
    the day before the next payday and nothing else; the only shape left is a
    payday JUMP, which is what the fixture below builds and which the writer
    accepts.  ``scripts/integrity_check.py`` **BA-07** reported the stored
    state while it was expressible and went with the column.

    **The absorption is not free, and the third test says so.**  An absorbed
    hole leaves an OVER-LONG paycheck, and a monthly bill can fall inside one
    more than once.  The paycheck-keyed index could not hold that pair, so the
    pass was REFUSED; since plan step **R17** re-keyed onto the occurrence both
    installments are written, and the third test grades the rows rather than
    the refusal.

    **"Not yet" was never a gap**, and the class still ends with the control
    that says so: under ``PERIOD_STARTING_ON_OR_AFTER`` an occurrence dated
    after the LAST PAYDAY has no paycheck to defer onto even on a perfectly
    contiguous schedule, and that is 43% of biweekly schedule openings.  Since
    C2-b2 it is the ONLY way a placement answers ``None``.
    """

    #: Days after the seed schedule's last covered day that the second batch
    #: opens.  Large enough that a whole calendar month (June 2026) falls in
    #: the jump, so a monthly rule has exactly one occurrence the preceding
    #: paycheck has to absorb and the assertions below are about that one date.
    _GAP_DAYS = 43

    #: The rule's scheduling day, and therefore the day every occurrence and
    #: every generated ``due_date`` falls on.
    _DAY_OF_MONTH = 15

    def _schedule_with_a_payday_jump(self, seed_user, seed_periods):
        """Append a second batch 43 days after the schedule's horizon.

        The whole fixture is the real writer's, which is the point: the shape
        these tests are about is one the app produces.

        *It re-opened a stored hole on the last line until plan step
        ``pay_calendar:C4-c``.*  While ``end_date`` was a column, the writer's
        own recompute closed the hole this appended, so the fixture had to put
        it back to reach the state a pre-C3-b row could hold.  The column is
        gone: the days between the horizon and the new payday belong to the
        preceding paycheck, and there is no second value that could say
        otherwise.

        Returns:
            ``(later_periods, jump_start, jump_end)`` -- the appended batch and
            the inclusive span of days the preceding paycheck absorbs.
        """
        last_covered = self._horizon(seed_user)
        later_start = last_covered + timedelta(days=self._GAP_DAYS)
        later = record_paydays_across_a_hole(
            user_id=seed_user["user"].id,
            first_payday=later_start,
            num_periods=6,
            rhythm=rhythm_of(14),
        )
        return (
            later,
            last_covered + timedelta(days=1),
            later_start - timedelta(days=1),
        )

    @staticmethod
    def _horizon(seed_user):
        """Return the owner's last covered day, DERIVED.

        ``seed_periods[-1].end_date`` until plan step ``pay_calendar:C4-c``
        dropped the column; the horizon is the last payday plus the cadence,
        and the calendar is what answers it.
        """
        return calendar_for(seed_user["user"].id).horizon()

    def _days_between(self, first, last, day=None):
        """Every *day* of the month in ``first..last``, inclusive, ascending.

        Args:
            first: Inclusive lower bound.
            last: Inclusive upper bound.
            day: Day of the month, defaulting to :data:`_DAY_OF_MONTH`.

        Returns:
            The matching dates, ascending.
        """
        day = self._DAY_OF_MONTH if day is None else day
        return [
            date(year, month, day)
            for year in range(first.year, last.year + 1)
            for month in range(1, 13)
            if first <= date(year, month, day) <= last
        ]

    def test_the_writer_no_longer_leaves_a_gapped_batch(
        self, app, db, seed_user, seed_periods,
    ):
        """The CONTROL: appending a late batch now absorbs the days it skips.

        This test used to assert the opposite -- that the real writer leaves a
        hole -- and said in its own docstring that it would "go red and say so"
        if the writer were ever tightened to refuse gaps (finding **F-10**).
        Plan step **C3-b** tightened it, this went red, and the assertion is
        inverted rather than deleted so the closure has a control of its own.

        The days are checked over the WHOLE span rather than its first day: a
        writer that closed the hole's opening and left its middle uncovered
        would satisfy a single-day check.
        """
        with app.app_context():
            last_covered = self._horizon(seed_user)
            later_start = last_covered + timedelta(days=self._GAP_DAYS)
            later = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=later_start,
                num_periods=6,
                rhythm=rhythm_of(14),
            )
            assert later[0].start_date == later_start

            # The days the OLD writer would have left behind.
            calendar = calendar_for(seed_user["user"].id)
            day = last_covered + timedelta(days=1)
            while day < later_start:
                assert calendar.period_containing(day) is not None, (
                    f"{day} is covered by no pay period"
                )
                day += timedelta(days=1)
            # And it is the PRECEDING paycheck that absorbed them, running to
            # the day before the new payday rather than stopping at its old
            # horizon.
            preceding = calendar.period_containing(seed_periods[-1].start_date)
            assert preceding.end_date == later_start - timedelta(days=1)

    def test_the_calendar_absorbs_the_hole_into_the_preceding_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """The occurrence in the hole is SEATED, not skipped.

        Read off the generation seam's own plan, which is where the fact lives:
        every occurrence the rule names has a period, and the one dated inside
        the hole carries the paycheck that opened before it.  This same fixture
        produced a placement with NO period before plan step C2-b2.

        A day-5 rule rather than the class's day-15 one, deliberately.  The
        absorbing paycheck opens 2026-05-08 and derives an end of 2026-07-02,
        so the 5th falls inside it exactly ONCE (2026-06-05: May's is in the
        previous paycheck and July's in the next).  This test is therefore
        about the SEATING, and the next one -- where two occurrences of the 15th land in
        that one paycheck -- is about the repeat.
        """
        absorbed_day = 5
        with app.app_context():
            _later, gap_start, gap_end = self._schedule_with_a_payday_jump(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=absorbed_day,
            )
            schedule = GenerationSchedule.for_pass(BalanceContext.build(template.user_id))
            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )

            absorbing = next(
                period
                for period in all_periods(
                    seed_user["user"].id,
                )
                if period.start_date == seed_periods[-1].start_date
            )
            # The premise, asserted rather than assumed: the fixture really
            # does name an occurrence inside the hole.  Without it the
            # assertions below could pass over an empty list.
            in_hole = [
                row for row in plan.placements
                if gap_start <= row.occurrence <= gap_end
            ]
            assert len(in_hole) == 1, (
                f"the fixture must name exactly one occurrence in the hole "
                f"{gap_start}..{gap_end}, got "
                f"{[row.occurrence for row in plan.placements]}"
            )
            assert in_hole[0].period.period_id == absorbing.id
            # What makes this an ABSORPTION rather than an ordinary
            # containment: the paycheck spans far more than one cadence,
            # because it ran on to the day before the next payday.
            assert (
                in_hole[0].period.end_date - in_hole[0].period.start_date
            ).days + 1 > 14

            # **The COUNT, and it is load-bearing** -- the assertion an
            # adversarial review of plan step C2-b2 caught this test dropping
            # when it replaced the gap-era one.  Without it a pass that seated
            # the absorbed occurrence and dropped the other eight would look
            # identical to a correct one, which is the exact mutant the class
            # was burned by before.  Derived from the calendar the engine
            # itself walks, so a fixture change moves both together.
            named = self._days_between(
                schedule.calendar.opening_bound(),
                schedule.calendar.horizon(),
                day=absorbed_day,
            )
            assert [row.occurrence for row in plan.placements] == named
            # And nothing is left homeless: every occurrence has a paycheck,
            # each inside the DERIVED span of the one it was seated in.
            assert all(row.period is not None for row in plan.placements)
            for row in plan.placements:
                derived = row.period
                assert derived.start_date <= row.occurrence <= derived.end_date

    def test_the_regenerate_sweep_and_the_regeneration_share_ONE_period_end(
        self, app, db, seed_user, seed_periods,
    ):
        """The bound both halves of a regenerate read must be the same end.

        ``regenerate_for_template`` decides which existing rows it may act on
        and which periods the rule names, and both decisions are bounded by
        ``effective_from``.  For that to be ONE pass rather than two, both must
        read the same answer to "when does this paycheck end".

        **They read the DERIVED end since pay-calendar plan step C2-f3c, and
        this test was INVERTED there.**  The row half used to be SQL over
        ``pay_periods.end_date`` -- the STORED column -- while plan step C2-b2
        had already given the rule half a calendar whose ends are derived; the
        fix at the time was to make the RULE half read the stored column too,
        by resolving the ORM row before applying the bound, and this test
        asserted the absorbing paycheck was in NEITHER half.  C2-f3c removed
        the stored column from the seam entirely: the row select is now a
        period-ID set filtered by the derived end
        (``_recurrence_common.rows_this_pass_may_maintain``), so the absorbing
        paycheck is in BOTH halves.

        **And that is the correct answer, not merely a consistent one.** The
        absorbing paycheck's span runs to 2026-07-02, so it is the paycheck the
        owner's money on 2026-06-01 actually lives in.  A regeneration
        effective from that date must maintain it.  The old behaviour skipped
        it because a stored column said the paycheck had ended six weeks
        earlier -- a column plan step ``pay_calendar:C4-c`` DROPPED, and which
        no writer had produced since plan step C3-b.

        The bound falls deep INSIDE the absorbing paycheck rather than near a
        boundary, which is what makes the two memberships below a real
        question: the paycheck opens 2026-05-08 and runs to 2026-07-02, and
        the bound is 2026-06-01.
        """
        with app.app_context():
            self._schedule_with_a_payday_jump(seed_user, seed_periods)
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=5,
            )
            scenario_id = seed_user["scenario"].id
            schedule = GenerationSchedule.for_pass(
                BalanceContext.build(template.user_id),
            )
            absorbing = next(
                period
                for period in all_periods(seed_user["user"].id)
                if period.start_date == seed_periods[-1].start_date
            )
            bound = date(2026, 6, 1)

            # The premise: the bound really does fall inside the absorbing
            # paycheck, and past where an un-absorbed one would have ended.
            absorbing_span = schedule.calendar.period_by_id(absorbing.id)
            assert absorbing_span.start_date < bound <= absorbing_span.end_date
            assert bound > absorbing_span.start_date + timedelta(days=13)

            # Give the paycheck a row, so the sweep has something to collect.
            recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()

            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, scenario_id, bound, block_message="test",
            )
            swept = rows_this_pass_may_maintain(
                TemplateRowSelector(
                    Transaction, Transaction.template_id, template,
                    scenario_id,
                ),
                schedule, bound,
            )

            # BOTH halves reach it, because both read the derived end.
            assert absorbing.id in {
                row.period.period_id for row in plan.placements
            }, "the rule half skipped the paycheck its own calendar says is live"
            assert absorbing.id in {row.pay_period_id for row in swept}, (
                "the row half skipped the paycheck the rule half named -- the "
                "two are reading different definitions of this period's end"
            )

            # **And the two halves apply the SAME comparison, not merely the
            # same column** (adversarial review of plan step C2-f3c).  The
            # memberships above are satisfied by a sweep using ``>`` where the
            # rule uses ``>=``; the difference is invisible unless a bound
            # falls exactly ON a derived end, which no other case in the suite
            # arranges -- every ``effective_from`` elsewhere is a period START
            # or a round date, and a 14-day schedule never makes those equal.
            # A row the rule NAMES but the fetch does not return is a
            # duplicate create, or an ``IntegrityError`` on the generation
            # index.
            on_the_end = schedule.calendar.period_by_id(absorbing.id).end_date
            named_there = {
                row.period.period_id
                for row in recurrence_engine.resolve_generation_plan(
                    template, schedule, scenario_id, on_the_end,
                    block_message="test",
                ).placements
            }
            swept_there = {
                row.pay_period_id for row in rows_this_pass_may_maintain(
                    TemplateRowSelector(
                        Transaction, Transaction.template_id, template,
                        scenario_id,
                    ),
                    schedule, on_the_end,
                )
            }
            assert absorbing.id in named_there, (
                "the premise: a bound ON the derived end must still NAME the "
                "period, or this case grades an empty set"
            )
            assert named_there <= swept_there, (
                f"the rule half names {sorted(named_there - swept_there)} that "
                f"the row half does not offer, on a bound that falls exactly "
                f"on a derived period end -- the two are not one comparison"
            )

    def test_an_absorbed_occurrence_is_DATED_by_its_cadence_not_its_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """Plan ledger row **D18**, closed by plan step R5-a (ruling **R-R94**).

        **This asserted the DEFECT until R5-a, deliberately, so the step that
        fixed it had a control** -- and its docstring said this test would go
        red when that step landed, which is what it did.  ``compute_due_date``
        dated a generated row by scanning the two ENDPOINT months of the
        paycheck it landed in and discarded the occurrence the walk found; when
        a paycheck absorbs a hole it spans months neither endpoint names, so
        the 2026-06-05 occurrence seated in the 2026-05-08 paycheck was
        generated with ``due_date = 2026-05-05`` -- a month early, colliding
        with the date the PREVIOUS paycheck's row already carried.

        R5-a dates a row from the occurrence it answers, so the row carries the
        date the cadence named and no two rows share one.
        """
        absorbed_day = 5
        with app.app_context():
            _later, gap_start, gap_end = self._schedule_with_a_payday_jump(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=absorbed_day,
            )
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(template.user_id)),
                seed_user["scenario"].id,
            )

            absorbing = next(
                period
                for period in all_periods(
                    seed_user["user"].id,
                )
                if period.start_date == seed_periods[-1].start_date
            )
            seated = [
                txn for txn in created if txn.pay_period_id == absorbing.id
            ]
            assert len(seated) == 1, (
                "the fixture must seat exactly one absorbed occurrence in the "
                "paycheck that swallowed the hole"
            )
            # The occurrence the cadence named, for the record.
            assert gap_start <= date(2026, 6, absorbed_day) <= gap_end
            # ...and the date the row carries, which is now that occurrence.
            assert seated[0].due_date == date(2026, 6, absorbed_day)
            assert seated[0].due_date == seated[0].occurs_on
            # The collision is gone: one row per date.
            assert [txn.due_date for txn in created].count(
                date(2026, 5, absorbed_day),
            ) == 1

    def test_an_absorbed_hole_can_make_one_paycheck_owe_a_bill_twice(
        self, app, db, seed_user, seed_periods,
    ):
        """The cost of absorbing, now WRITTEN rather than refused.

        The hole spans a whole calendar month, so the paycheck that absorbs it
        covers the 15th twice.  While the unique index was keyed
        ``(template, pay_period, scenario)`` both rows could not be stored, so
        ``refuse_unstorable_repeats`` refused the whole pass and wrote nothing.
        Plan step **R17** re-keyed it onto ``(template, scenario, occurs_on)``
        -- two occurrences are two keys -- so the absorbing paycheck now
        carries both installments and the money is budgeted rather than
        withheld (developer ruling 2026-08-28).

        **This is ONE of the ways plan step C2-b2 moves money, not the only
        one**, and an adversarial review of that step refuted the claim that it
        was.  Wherever a stored column disagrees with the payday derivation the
        engine believes the derivation, and there are THREE such columns --
        the hole this test builds (plan ledger row **P27**), the stored cadence
        against the last stored end (**P28**), and a stored ordinal that is not
        ``0..n-1`` (**P26**).  ``recurrence/_occurrence.py``'s module docstring
        states all three and what each one costs.

        None is reachable through a live door: ``pay_period_write`` writes the
        derivation over the whole payday list on every write and REPAIRS such a
        row, so each means data written before plan step C3-b or edited outside
        that module.  Production carries none of the three (61 contiguous
        periods, 0 index mismatches, 0 end mismatches, measured 2026-08-10).
        """
        with app.app_context():
            _later, gap_start, gap_end = self._schedule_with_a_payday_jump(
                seed_user, seed_periods,
            )
            template = self._make_template_with_rule(
                seed_user, MONTHLY, fires_on_day=self._DAY_OF_MONTH,
            )
            schedule = GenerationSchedule.for_pass(BalanceContext.build(template.user_id))

            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )
            db.session.flush()

            # The date the hole used to swallow is now ANSWERED by a row of its
            # own, rather than costing the owner the whole pass.
            in_hole = self._days_between(gap_start, gap_end)
            assert len(in_hole) == 1, (
                f"the fixture must put exactly one {self._DAY_OF_MONTH}th in "
                f"the hole {gap_start}..{gap_end}, got {in_hole}"
            )
            assert in_hole[0] in {row.occurs_on for row in created}

            # And the absorbing paycheck holds BOTH -- two rows, two
            # occurrences, one period, which the old index could not store.
            absorbing = [
                row for row in created if row.occurs_on == in_hole[0]
            ][0].pay_period_id
            both = [
                row for row in created if row.pay_period_id == absorbing
            ]
            assert len(both) == 2
            assert len({row.occurs_on for row in both}) == 2

    def test_a_contiguous_schedule_past_its_last_payday_places_nothing_there(
        self, app, db, seed_user, seed_periods,
    ):
        """The control that matters: "not yet" is ordinary, and now unique.

        A ``Monthly First`` rule places on the first paycheck STARTING on or
        after the 1st of each month, so an occurrence after the last payday has
        nothing to defer onto -- and the schedule below is CONTIGUOUS, built by
        the real writer, with its final period straddling a month boundary so
        that case is reached.  Plan step R4b-2's first draft reported it as a
        corrupt schedule; two neutral reviews measured that at 43% of biweekly
        schedule openings.  Since plan step C2-b2 it is the ONLY way a
        placement answers ``None``, and this is the case that goes red if a
        second one is ever reintroduced.

        A ``Monthly`` (CONTAINING_DATE) rule cannot exercise it -- the first
        draft's control used one, which is why the defect survived a green
        suite.
        """
        with app.app_context():
            # One more period, CONTIGUOUS with the seed batch, chosen so the
            # last period spans 2026-05-22..2026-06-04 -- across a month
            # boundary, so the 1st of June falls after the last payday while
            # still inside the schedule's covered span.
            tail = pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
                num_periods=1,
                rhythm=rhythm_of(14),
            )
            db.session.flush()
            last = tail[-1]
            assert last.start_date.month != last_covered_day(last).month, (
                "the control needs a final period straddling a month boundary"
            )

            template = self._make_template_with_rule(seed_user, MONTHLY_FIRST)
            schedule = GenerationSchedule.for_pass(BalanceContext.build(template.user_id))
            plan = recurrence_engine.resolve_generation_plan(
                template, schedule, seed_user["scenario"].id, None,
                block_message="test",
            )
            placements = rule_occurrences(
                template.recurrence_rule, schedule.calendar,
            )
            # The premise: the rule really does name an occurrence with no
            # paycheck to defer onto.  Without this the assertions below could
            # simply mean nothing was unplaceable.
            unplaceable = [
                placement for placement in placements
                if placement.period is None
            ]
            assert len(unplaceable) == 1, (
                f"the control must exercise an unplaceable occurrence, got "
                f"{[p.occurrence for p in unplaceable]}"
            )
            # Past the last PAYDAY and still inside the covered span -- exactly
            # the shape the deleted SCHEDULE_GAP branch used to misread.
            assert unplaceable[0].occurrence > last.start_date
            assert schedule.calendar.period_containing(
                unplaceable[0].occurrence,
            ) is not None

            created = recurrence_engine.generate_for_template(
                template, schedule, seed_user["scenario"].id,
            )

            assert created, "the control generated nothing to be a control over"
            # The unplaceable occurrence is simply absent from the plan; it is
            # not reported, and it is not written anywhere.
            assert [row.occurrence for row in plan.placements] == [
                placement.occurrence for placement in placements
                if placement.period is not None
            ]

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
            name='Gap Bill',
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
