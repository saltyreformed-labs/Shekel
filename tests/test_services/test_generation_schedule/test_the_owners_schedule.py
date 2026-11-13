"""The paycheck counts the owner's whole schedule, the owner's window, its gates and bounds."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import RecurrenceWindowError
from app import ref_cache
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.paycheck_line import PaycheckLine
from app.models.ref import CalcMethod, PaycheckLineKind, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.services import cash_ledger, recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import PayCalendar, saved_paydays_in_month_through
from tests._made_up_federal import made_up_credits, made_up_federal
from tests._test_helpers import (
    make_cadence_rule,
    make_line_cadence_rule,
    payroll_basis,
    made_up_fica,
    made_up_state,
    made_up_year,
    start_test_pay_list,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY_FIRST
from app.services.amount_ownership import state_own_amount
from app.tax_law import TaxLaw

from ._helpers import _JANUARY_THIRD_PAYCHECK_INDEX, _make_template, _rows


class TestThePaycheckSeesTheWholeSchedule:
    """D25: the engine counts paydays off the OWNER's calendar, not a window.

    The defect was an ``all_periods`` argument beside the basis, which every
    window, year slice and one-period sample satisfied.  Plan step
    **balance:X-bh-1** deleted that argument: the engine reads
    ``basis.calendar``, and a
    :class:`~app.services.pay_calendar.PayCalendar` is constructible only from
    a COMPLETE payday set.  So the narrow read these cases guard against is no
    longer something a caller of the generation seam can express -- reaching it
    now means building a SECOND calendar holding one payday, which is a
    different owner's schedule rather than a narrowed view of this one.

    **THE REQUIREMENT MOVED AT PLAN STEP balance:X-au-d AND THESE CASES MOVED
    WITH IT.**  They asserted the figure GENERATION stored; generation prices
    nothing now -- a salary row DECLARES its definition and stores no figure at
    all -- so the whole-schedule requirement lives where the derivation does,
    in :meth:`~app.services.income_service.SalaryPricing._breakdown_by_period`, which
    derives its own calendar.  Each case therefore asserts what the AMOUNT
    MODEL answers for the generated row rather than what the row holds, and
    D25's defect is graded exactly as before: a producer counting one January
    payday answers the windowed figure.
    """

    def _salary_template(self, seed_user, tax_law):
        """Create a salary profile whose deduction skips the 3rd paycheck.

        The line's rule -- every paycheck, at most 2 a month, the shape plan
        step salary:R15-b migrates a 24 onto -- is the cadence the engine
        skips on a month's third payday through the recurrence walk (it was
        ``deductions_per_year=24`` under ``_deduction_applies_at`` until that
        step): the exact judgement the truncated period set got wrong on
        production.

        Args:
            seed_user: The ``seed_user`` fixture dict.

        Returns:
            ``(template, profile)``, both flushed.
        """
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=seed_user["categories"]["Car Payment"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            name="Day Job",
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        make_cadence_rule(template, EVERY_PERIOD)

        profile = SalaryProfile(
            period_end_days_before_payday=6,
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            template_id=template.id,
            filing_status_id=db.session.query(FilingStatus)
            .filter_by(name="single").one().id,
            name="Day Job",
            state_code="NC",
        )
        db.session.add(profile)
        start_test_pay_list(profile, Decimal("4000.00"))  # $104,000.00 a year / 26
        db.session.flush()

        deduction = PaycheckLine(
            salary_profile_id=profile.id,
            paycheck_line_kind_id=db.session.query(PaycheckLineKind)
            .filter_by(name="pre_tax_deduction").one().id,
            calc_method_id=db.session.query(CalcMethod)
            .filter_by(name="flat").one().id,
            name="Health Insurance",
            amount=Decimal("500.00"),
        )
        db.session.add(deduction)
        db.session.flush()
        make_line_cadence_rule(db.session, deduction, 24)
        # The made-up federal, FICA and NC rules the three row seeders wrote.
        tax_law(TaxLaw(years=(made_up_year(
            federal=made_up_federal(), federal_credits=made_up_credits(),
            fica=made_up_fica(),
            states={"NC": made_up_state(Decimal("0.0399"))},
        ),)))
        db.session.flush()
        return template, profile

    def test_a_windowed_pass_computes_the_whole_schedule_paycheck(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """Generating index 2 alone gives January's THIRD-paycheck net pay.

        2026-01-30 is January's third payday, so the 24x/yr deduction is
        SKIPPED and the net is higher.  Handed only ``[period 2]`` the
        calculator counted one January payday, called it the first, and took
        the $500 deduction: on production that shape understated one paycheck
        by $502.45.

        The expected figure is not a literal: it is what
        ``calculate_paycheck`` answers for this period against the owner's
        whole schedule, which is the contract being asserted.  The absolute
        claim is made separately, below.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template, profile = self._salary_template(seed_user, tax_law)
            period = seed_periods[_JANUARY_THIRD_PAYCHECK_INDEX]

            schedule = GenerationSchedule.for_period_ids(
                BalanceContext.build(seed_user["user"].id), {period.id},
            )
            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()

            # Pylint: ``import-outside-toplevel`` -- the calculator is
            # imported here only to recompute the contract this test asserts;
            # at module scope it would read as a collaborator of the tests.
            from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel
            # Pylint: ``import-outside-toplevel`` -- see above.
            from tests._tax_configs import (  # pylint: disable=import-outside-toplevel
                tax_configs_for,
            )
            configs = tax_configs_for(profile, 2026)
            # Pylint: ``import-outside-toplevel`` -- see above.
            from app.services.payroll_basis import (  # pylint: disable=import-outside-toplevel
                PayrollBasis,
            )
            # The engine takes a DERIVED calendar (pay-calendar plan step
            # C2-f2d-3 and plan step balance:X-bh-1), and this case's whole
            # point is the difference between the OWNER's calendar and one
            # built over a single payday.
            calendar = schedule.calendar
            derived_period = calendar.period_by_id(period.id)
            # The WHOLE schedule is what the seam itself hands the engine:
            # ``schedule.calendar`` IS ``ctx.calendar()``, built by
            # ``calendar_for`` from every saved payday, and there is no
            # argument beside it for a caller to narrow.  The windowed read
            # below is reachable only by building a SECOND calendar over one
            # payday -- which is what plan step balance:X-bh-1 turned this
            # defect into: not a shorter argument, a different schedule.
            whole_break = paycheck_calculator.calculate_paycheck(
                PayrollBasis(profile, calendar), derived_period, configs,
            )
            # The narrow calendar states nothing about the owner's pay
            # history, which since ruling balance:R-IA's 2026-08-31 amendment
            # is what every unasked owner has -- so it still counts one
            # January payday and still calls a third paycheck a first.  D25's
            # shape survives plan step balance:X-bh-2 unchanged for the
            # default reading, and that is deliberate: the step widens what a
            # calendar can be TOLD, not what it assumes.
            windowed_break = paycheck_calculator.calculate_paycheck(
                payroll_basis(profile, [derived_period]),
                derived_period, configs,
            )
            whole = whole_break.earnings.net_pay
            windowed = windowed_break.earnings.net_pay

            assert len(created) == 1
            # The row STORES nothing (plan step balance:X-au-d), so the figure
            # under test is the one the amount model answers for it -- which is
            # where the whole-schedule requirement now lives.  A row asserted
            # by its column would compare ``None`` here.
            assert created[0].estimated_amount is None
            priced = cash_ledger.amounts_by_key(
                created,
                cash_ledger.derived_amount_basis(seed_user["user"].id, scenario_id),
            )
            assert priced[created[0].id] == whole
            assert whole != windowed, (
                "the window and the whole schedule now agree for this period, "
                "so this test no longer guards anything -- check that "
                "seed_periods still puts three paydays in January"
            )
            # What X-bh-2 added, asserted rather than described: the SAME
            # one-payday calendar, told that the owner's paychecks began
            # earlier, reconstructs January's other two paydays from the
            # cadence and prices this paycheck exactly as the whole schedule
            # does.  So the narrow context is no longer enough to misprice ON
            # ITS OWN -- it must also be paired with an owner who has stated
            # a history, and stating a TRUE history makes it agree.
            assert paycheck_calculator.calculate_paycheck(
                payroll_basis(
                    profile, [derived_period],
                    history_opens_on=date(2025, 1, 1),
                ),
                derived_period, configs,
            ).earnings.net_pay == whole, (
                "with the owner's history stated, the backward rhythm "
                "reconstructs January's other two paydays from the cadence"
            )
            # The direction is the money: the truncated read took a deduction
            # a third paycheck does not pay, so it UNDERSTATED the income.
            #
            # The DEDUCTION is asserted, not the net gap, and an adversarial
            # review is why.  The gap was a $480.05 literal with a stated
            # derivation of "$500 less the 3.99% state rate, because federal
            # does not move" -- and federal did not move here for the opposite
            # of the stated reason: the SHARED made-up federal rules
            # (``made_up_federal``, the old ``seed_tax_bracket_set`` rows) taxed
            # nothing above $47,150, so both annualised bases sat in that top
            # rung and the federal lines matched by accident of the fixture.
            # That rung taxes 22% since a ladder's rates must climb (finding
            # plan#3208), the realistic top bracket the review warned would
            # break a literal gap and send its reader to the wrong cause.  The
            # pre-tax total is the fact this defect is ABOUT, and it is exact
            # on any bracket table.
            assert (
                windowed_break.deductions.total_pre_tax
                - whole_break.deductions.total_pre_tax
            ) == Decimal("500.00"), (
                "the windowed read must have taken the 24x/yr deduction a "
                "third paycheck skips -- that IS the defect"
            )
            assert whole_break.deductions.total_pre_tax == Decimal("0.00")
            assert whole > windowed

    def test_the_deduction_is_skipped_on_the_third_paycheck(
        self, app, db, seed_user, seed_periods, tax_law,
    ):
        """Stated absolutely: the generated row differs from a 1st-payday one.

        The comparison above is relative -- two equal-but-wrong figures would
        satisfy it.  January's FIRST payday pays the $500 deduction and its
        THIRD does not, so the two rows must differ by exactly that, net of the
        tax the deduction shelters.  Read through the amount model rather than
        off the rows, for the reason the class docstring states.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template, _profile = self._salary_template(seed_user, tax_law)

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()

            priced = cash_ledger.amounts_by_key(
                created,
                cash_ledger.derived_amount_basis(seed_user["user"].id, scenario_id),
            )
            by_start = {
                row.pay_period.start_date: priced[row.id] for row in created
            }
            first = by_start[date(2026, 1, 2)]
            third = by_start[date(2026, 1, 30)]
            assert third > first, (
                "a third paycheck skips the 24x/yr deduction, so it must net "
                f"MORE than the month's first: {third} vs {first}"
            )
            # The deduction is PRE-TAX, so skipping it adds the $500 back and
            # then taxes it: the gap is smaller than $500 by exactly the tax
            # on $500, and strictly positive.
            assert Decimal("0.00") < third - first < Decimal("500.00")


class TestAWindowMustBelongToTheOwner:
    """The ONE refusal left, and the two states that stopped being refusable.

    Pay-calendar plan step **C2-f3c** deleted this value's second read of the
    schedule -- it took a tuple of ORM rows BESIDE the calendar and reconciled
    them -- so two of the three checks here lost their subject rather than
    their teeth.  Each is graded below as what it now is: an unsaved period has
    no spelling in a window of ids, and a scrambled stored ordinal cannot
    change any answer the seam gives.
    """

    def test_another_users_period_is_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A window id outside the owner's calendar raises.

        Silent would be worse than loud: the intersection would match nothing
        and the pass would report "generated 0 rows" for a definition that
        fires every paycheck.
        """
        with app.app_context():
            foreign = (
                db.session.query(PayPeriod)
                .filter_by(user_id=seed_second_user["user"].id)
                .first()
            )
            assert foreign is not None, "the second user needs a period to lend"

            with pytest.raises(RecurrenceWindowError, match="are not in user"):
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {foreign.id},
                )

    def test_an_unsaved_period_has_no_spelling_in_a_window(
        self, app, db, seed_user, seed_periods,
    ):
        """An unsaved period cannot even be OFFERED as a window any more.

        **This graded a refusal until pay-calendar plan step C2-f3c.**  The
        window was a list of ORM rows, so a caller could hand over one that had
        never been flushed and whose ``id`` was therefore ``None``; the value
        refused it with "has no id".  A window is a set of
        ``budget.pay_periods.id`` values now, and the id of an unsaved period
        is ``None`` -- which is not one of the owner's saved ids, so the ONE
        surviving check answers it.  The state is refused because it is
        incoherent, not because a second rule was written for it.
        """
        with app.app_context():
            unsaved = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=date(2026, 6, 5),
            )
            assert unsaved.id is None, "the premise: it was never flushed"

            with pytest.raises(RecurrenceWindowError, match="are not in user"):
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {unsaved.id},
                )

    # **``test_a_stored_ordinal_out_of_payday_order_changes_nothing`` was
    # deleted at plan step ``pay_calendar:C4-c``, and it is worth saying what
    # it did rather than leaving a gap.**  It scrambled
    # ``budget.pay_periods.period_index`` underneath an ``EVERY_N_PERIODS``
    # rule at ``interval_n=2`` -- the seam's only ordinal-sensitive expression
    # is ``(period.period_index - offset) % interval_n`` and at 1 it is zero
    # for every ordinal, so 2 was the whole test -- and asserted the generate
    # pass produced byte-identical rows.  It graded a RAISE until plan step
    # C2-f3c, when the second read went and the disagreement stopped being
    # detectable; C4-c dropped the column, so the disagreement stopped being
    # CONSTRUCTIBLE.  The ordinal is a row's position in payday order now, and
    # ``derive_periods`` is the one thing that computes it.

    def test_a_narrowed_WINDOW_still_resolves_against_the_whole_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """Defect **D22**, graded BEHAVIOURALLY because the type cannot grade it.

        The window states what a pass may WRITE; it may never state what a rule
        MEANS.  That is the $118.62 / $502.45 shape: `period_population` hands
        each engine the newly created batch, and reading the rule against that
        batch makes the batch's own first payday look like the owner's.

        **This case asserts the ANSWER, not the shape, and an adversarial
        review of plan step C2-f3c is why.**  What stood here asserted
        ``schedule.calendar is calendar`` and the calendar's own ordinals --
        one a restatement of the dataclass assignment, the other a fact the
        test itself built -- so it could not fire on the defect it was named
        for.  It cannot be graded at the type level either, though plan step
        R7d-c-1 moved WHERE the hole is: the value took its calendar then, so
        ``PayCalendar.from_paydays`` over a window's pairs was one line and one
        argument (``PeriodWindow``'s own docstring records that, as ledger row
        **P24**); it takes the READ PASS now and derives the calendar, so the
        narrowed schedule has to be seated in the pass's memo instead.  Neither
        is a state the type refuses, which is why the grade is BEHAVIOURAL: the
        one thing a narrowed calendar changes is that a ``Monthly First`` rule
        resolved against a one-period window fires in EVERY period, because
        that window's only month holds exactly one payday.

        The control is the pair: the same rule, the same one-period window, one
        schedule built the way `period_population` builds it and one built the
        way D22 built it.  If the narrowed calendar answered the same, this
        test would be measuring nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(seed_user, MONTHLY_FIRST)
            ctx = BalanceContext.build(seed_user["user"].id)
            calendar = ctx.calendar()
            # seed_periods[4] opens 2026-02-27 -- the SECOND payday of
            # February (the first is 02-13), so a whole-schedule Monthly First
            # does NOT name it.
            target = seed_periods[4]

            whole = GenerationSchedule.for_period_ids(ctx, {target.id})
            # Through ``saved_paydays_in_month_through`` since plan step
            # balance:X-bh-2, which deleted ``earliest_start_in_month`` as
            # unreached in ``app/`` (ledger row N-396).  The SAVED twin, not
            # the total one: this asks what the recorded February holds, and
            # the rhythm's backward half would answer about a month the record
            # opens inside -- a different question from the one the premise
            # states.
            assert saved_paydays_in_month_through(
                whole.calendar, date(2026, 2, 28),
            )[0] == seed_periods[3].start_date != target.start_date, (
                "the premise: February's FIRST payday is not the target's"
            )

            created = recurrence_engine.generate_for_template(
                template, whole, scenario_id,
            )
            db.session.flush()
            assert created == [], (
                "resolved against the owner's whole calendar, a Monthly First "
                "rule must not fire in a month's second paycheck"
            )

            # The CONTROL: the same window against a calendar narrowed to it --
            # D22 exactly -- fires, because that calendar's February holds one
            # payday and it is the target's.
            # **Building the control now takes reaching INTO the pass's memo,
            # and that is the property plan step R7d-c-1 added.**  This value
            # took a calendar until that step, so D22 was one argument away;
            # it takes the read pass and DERIVES the calendar, so the only way
            # left to hand generation a narrowed schedule is to seat one in the
            # memo the pass would otherwise fill itself.  The control is kept
            # at full strength -- through the ENGINE, not through the resolver
            # underneath it -- because what it grades is what generation does.
            narrowed_ctx = BalanceContext.build(seed_user["user"].id)
            # pylint: disable-next=protected-access  # the memo IS the subject:
            # see the comment above -- no public door can seat a calendar here,
            # which is exactly what this control has to prove still matters.
            narrowed_ctx._calendars[seed_user["user"].id] = (
                PayCalendar.from_paydays(
                    paydays=[(target.id, target.start_date)],
                    eras=calendar.eras,
                    user_id=seed_user["user"].id,
                    history_opens_on=None,
                )
            )
            narrowed = GenerationSchedule.for_period_ids(
                narrowed_ctx, {target.id},
            )
            assert saved_paydays_in_month_through(
                narrowed.calendar, date(2026, 2, 28),
            ) == (target.start_date,), (
                "the control: the slice thinks the target IS February's first"
            )
            # And it does not merely ANSWER differently, it answers wrongly:
            # the slice's only period runs to a cadence projection, so January
            # AND February both land in it, where the whole-schedule read
            # places those two occurrences in two different paychecks.
            #
            # **Asserted on the ROWS since plan step R17.**  This used to be
            # observed through ``RecurrenceCadenceUnsupported`` -- two
            # occurrences in one paycheck were unstorable, so the narrowed
            # schedule refused and the refusal stood in for the defect.  The
            # re-keyed index stores them, so the defect is now named directly:
            # one paycheck, two occurrences, from a schedule that should have
            # placed them apart.  That is a stronger assertion than the refusal
            # was, because it says WHAT went wrong rather than that something
            # did.
            wrong = recurrence_engine.generate_for_template(
                _make_template(seed_user, MONTHLY_FIRST),
                narrowed, scenario_id,
            )
            db.session.flush()
            assert len(wrong) == 2, (
                "the narrowed calendar must place both occurrences, or this "
                "control no longer reproduces D22"
            )
            assert {row.pay_period_id for row in wrong} == {target.id}, (
                "D22: a calendar narrowed to the window funds two months' "
                "occurrences from one paycheck"
            )

    def test_the_window_is_a_frozen_set(
        self, app, db, seed_user, seed_periods,
    ):
        """``write_period_ids`` is a frozenset, so the value stays frozen."""
        with app.app_context():
            schedule = GenerationSchedule.for_pass(
                BalanceContext.build(seed_user["user"].id),
            )

            assert isinstance(schedule.write_period_ids, frozenset)
            with pytest.raises(AttributeError):
                schedule.write_period_ids.add(999)


class TestGenerationIsStillGated:
    """The gates R4b-1 moved but did not remove."""

    def test_a_cross_user_scenario_generates_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The ownership defence still fires before any resolution."""
        with app.app_context():
            template = _make_template(
                seed_user, EVERY_PERIOD,
            )

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                seed_second_user["scenario"].id,
            )

            assert created == []

    def test_a_populated_period_is_not_written_twice(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-running a pass over the same window creates nothing new."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, EVERY_PERIOD,
            )
            schedule = GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id))

            first = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            second = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()

            assert len(first) == 10
            assert second == []

    def test_a_settled_row_is_left_alone(
        self, app, db, seed_user, seed_periods,
    ):
        """An immutable row blocks regeneration into its period."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, EVERY_PERIOD,
            )
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()
            settled = created[0]
            settled.status_id = ref_cache.status_id(StatusEnum.DONE)
            state_own_amount(settled, Decimal("77.77"))
            db.session.flush()

            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()

            rows = _rows(template, scenario_id)[seed_periods[0].start_date]
            assert len(rows) == 1
            assert rows[0].estimated_amount == Decimal("77.77")


class TestABoundedRuleDoesNotRestartItsCount:
    """``max_occurrences`` counts from the ANCHOR, so the anchor must be real.

    The axis an adversarial review named as the one this step's migration
    could not repair.  ``_occurrence._bounded`` stops after
    ``max_occurrences`` occurrences *from the anchor*, and the anchor is
    measured against whatever schedule the rule was resolved against -- so a
    narrower window moved the anchor forward and RESTARTED the count, emitting
    a fresh full allowance into every extend's new batch.  That is the same
    defect family as D22 and the migration's signature would not have found it.

    Moot on production and measured rather than assumed: 0 of the 46 live rules
    carry a ``max_occurrences`` (no form authors one until plan step R7b), so
    the migration is complete as shipped.  This is what keeps it moot.
    """

    def test_a_bounded_rule_is_exhausted_and_an_extend_adds_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Three occurrences total, not three per window.

        The rule fires every paycheck and stops after 3, so it owns periods
        0-2 and nothing else.  Populating a LATER window must add no row: the
        allowance is spent.  Resolved against that window instead, the anchor
        becomes the window's own opening and the rule fires three more times.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, EVERY_PERIOD,
            )
            template.recurrence_rule.max_occurrences = 3
            db.session.flush()

            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()
            assert sorted(_rows(template, scenario_id)) == [
                seed_periods[0].start_date,
                seed_periods[1].start_date,
                seed_periods[2].start_date,
            ]

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {p.id for p in seed_periods[5:8]},
                ),
                scenario_id,
            )
            db.session.flush()

            assert created == []
            assert len(_rows(template, scenario_id)) == 3
