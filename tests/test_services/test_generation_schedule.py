"""R4b-1: a generate pass reads the OWNER's schedule, not the caller's window.

Both recurrence engines took ONE ``periods`` list and used it for two unrelated
jobs -- the schedule each rule was RESOLVED against, and the set of periods the
pass could WRITE into.  ``period_population`` hands them only the newly created
periods, so every schedule extend re-read every rule as though the owner's pay
history began at the new batch.

Three measured consequences, all live on production (2026-08-08, against a
streamed clone of ``shekel-prod-db``), one class each below:

* **D22** -- a ``Monthly First`` rule re-fired in a month it had already
  covered: 3 spurious ``Phone Allowance`` rows, $118.62, one per extend landing
  a new period in a covered month.
* **D25** -- ``calculate_paycheck`` received the same truncated list as its
  ``all_periods``, so third-paycheck detection read 1-3 periods instead of 61.
  One salary row was STORED $502.45 below its true net pay.  The read-time
  recompute (the read-time repair X-au-d deleted) kept that figure off every
  surface, so what it endangered was the stored cache the grid's inline editor
  pre-fills from -- see the migration's docstring for the measurement.
* **D2** -- a rule's chosen start period was not in the window, so the opening
  bound it states was dropped.

**What was shown RED against the pre-R4b-1 code, and by what means.**  The
authority is the production path, not a ported test file: a ``git worktree`` at
``HEAD`` driving ``pay_period_admin.extend_pay_periods`` six times over a
streamed clone of ``shekel-prod-db``, against the same six on this code.

```text
                              HEAD          this code
duplicate Monthly First months   3 NEW           0
salary rows disagreeing with a
  whole-schedule recompute       1 ($502.45)     0
```

Two of the classes below were additionally ported back onto the old
``periods``-list signature and run at ``HEAD``, where they fail on their own
assertions: ``TestTheStartPeriodBoundSurvivesTheWindow`` generates two rows
into paychecks the rule's start period excludes, and
``TestThePredictionIsTheGenerationCall``'s predictor names all ten periods
where the engine fires in five.

**A textual port is lossy, and saying so is the point.**  An earlier draft of
this docstring claimed all four defects had been shown red that way.  Two of
them had not: the port's own rewrite had left a ``NameError``, and a test that
fails to import is not a test that fails.  An adversarial review caught it by
redoing the port.  The classes with no ``HEAD`` counterpart at all --
``TestTheWindowStillNarrowsWhatIsWritten``, ``TestAWindowMustBelongToTheOwner``,
``TestOneScheduleReadServesTheWholeRequest``,
``TestRegenerateSweepsOnlyWhatItRewrites`` and
``TestABoundedRuleDoesNotRestartItsCount`` -- are forward guards over shapes
this step created or an adversarial review named, not reproductions of a
measured defect, and they are labelled as such rather than counted as
evidence.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date, timedelta
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
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.routes._period_population import populate_new_periods
from app.services import (
    cash_ledger,
    pay_period_write,
    recurrence_engine,
)
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import (
    PayCalendar,
    saved_paydays_in_month_through,
)
from tests._test_helpers import (
    rhythm_of,
    counting_calls,
    counting_read_passes,
    create_account_of_type,
    derived_span,
    last_covered_day,
    make_cadence_rule,
    make_line_cadence_rule,
    make_transfer_template,
    payroll_basis,
    populate_in_a_fresh_pass,
    made_up_federal,
    made_up_fica,
    made_up_state,
    made_up_year,
    start_test_pay_list,
    state_template_price,
)
from tests.oracles.recurrence_baseline import (
    EVERY_PERIOD,
    MONTHLY_FIRST,
)
from app.services.amount_ownership import state_own_amount
from app.tax_law import TaxLaw

#: The one database door every pay-calendar derivation goes through, as
#: ``tests/test_arch/test_one_read_pass_per_render.py`` names it.  A service
#: that derives one below its caller's door shows up here as a non-zero count.
_CALENDAR_DOOR = ("app.services.pay_calendar._loader", "calendar_for")

# ``seed_periods`` runs 10 biweekly periods from 2026-01-02, so the paydays are
# 01-02, 01-16, 01-30, 02-13, 02-27, 03-13, 03-27, 04-10, 04-24, 05-08.
# Two facts below depend on that shape and are asserted rather than assumed:
#   * JANUARY holds THREE of them (indices 0, 1, 2) -- the shape that produced
#     the $502.45 error;
#   * MAY holds one so far (index 9), so appending period 10 (2026-05-22) lands
#     a NEW period in a month a ``Monthly First`` rule has already covered --
#     the shape that produced the duplicate rows.
_JANUARY_THIRD_PAYCHECK_INDEX = 2
_LAST_SEEDED_INDEX = 9


def _make_template(seed_user, cadence, **rule_kwargs):
    """Create a recurring expense template and its rule.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author.
        **rule_kwargs: Extra columns for the rule (``day_of_month``,
            ``start_date``, ...).

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`.
    """
    rule_kwargs.pop("offset_periods", None)
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Car Payment"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name="Phone Allowance",
        default_amount=Decimal("39.54"),
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template)
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_cadence_rule(
        template, cadence,
        interval_n=rule_kwargs.pop("interval_n", 1),
        fires_on_day=rule_kwargs.pop("day_of_month", None),
        fires_in_month=rule_kwargs.pop("month_of_year", None),
        **rule_kwargs,
    )
    return template


def _rows(template, scenario_id):
    """Return the template's rows keyed by their period's start date.

    Args:
        template: The template whose rows to read.
        scenario_id: The scenario to read within.

    Returns:
        ``{start_date: [Transaction, ...]}``, one entry per period holding a
        row.
    """
    result: dict[date, list[Transaction]] = {}
    rows = (
        db.session.query(Transaction)
        .filter_by(template_id=template.id, scenario_id=scenario_id)
        .all()
    )
    for row in rows:
        result.setdefault(row.pay_period.start_date, []).append(row)
    return result


def _placements(template, scenario_id):
    """Return what a pass DECIDED, free of row identity.

    ``_rows`` keys by period and hands back the ``Transaction`` objects, which
    two passes over two templates can never compare equal.  What a generate
    pass decides is which paycheck each row lands in and what the definition
    says there, so that is what this reduces to.

    Args:
        template: The template whose rows to read.
        scenario_id: The scenario to read within.

    Returns:
        ``{start_date: [(due_date, estimated_amount), ...]}``, the due dates
        sorted so a repeated paycheck compares stably.
    """
    return {
        start: sorted((row.due_date, row.estimated_amount) for row in rows)
        for start, rows in _rows(template, scenario_id).items()
    }


def _append_period(seed_user, seed_periods):
    """Append one 14-day pay period after the seeded schedule.

    Reproduces exactly what ``pay_period_admin.extend_pay_periods`` returns: a
    flushed batch of new, EMPTY periods, and nothing else changed.  The
    repopulation is the caller's second call since ruling **R-R38**, which is
    what :func:`_populate` runs.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        seed_periods: The seeded schedule.

    Returns:
        The new ``[PayPeriod]`` batch, flushed.
    """
    created = pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=last_covered_day(seed_periods[-1]) + timedelta(days=1),
        num_periods=1,
        rhythm=rhythm_of(14),
    )
    db.session.flush()
    return created


class TestTheScheduleShapeTheseTestsRestOn:
    """The fixture facts every class below depends on, asserted not assumed."""

    def test_january_holds_three_paychecks(self, app, seed_user, seed_periods):
        """Index 2 (2026-01-30) is January's THIRD payday.

        The D25 class needs a third paycheck to exist; a fixture change that
        removed one would otherwise make those tests pass vacuously.
        """
        with app.app_context():
            january = [
                period for period in seed_periods
                if period.start_date.year == 2026
                and period.start_date.month == 1
            ]
            assert [derived_span(period).period_index for period in january] == [0, 1, 2]
            assert (
                seed_periods[_JANUARY_THIRD_PAYCHECK_INDEX].start_date
                == date(2026, 1, 30)
            )

    def test_appending_one_period_lands_in_an_already_covered_month(
        self, app, db, seed_user, seed_periods,
    ):
        """The appended period opens in MAY, which index 9 already covers.

        The D22 class needs the new period to fall in a month the rule has
        already fired in; on a schedule where it did not, no duplicate could
        arise and the test would prove nothing.
        """
        with app.app_context():
            assert seed_periods[_LAST_SEEDED_INDEX].start_date == date(2026, 5, 8)
            appended = _append_period(seed_user, seed_periods)
            assert appended[0].start_date == date(2026, 5, 22)
            assert appended[0].start_date.month == 5


class TestAnExtendDoesNotDuplicateAMonthlyFirstRow:
    """D22: the window's own first payday is not the owner's."""

    def test_appending_a_period_to_a_covered_month_adds_no_second_row(
        self, app, db, seed_user, seed_periods,
    ):
        """A month already holding the rule's row gains nothing on an extend.

        ``Monthly First`` fires on the 1st of each month and places on the
        first PAYCHECK at or after it, so May 2026 is owed exactly one row --
        the 2026-05-08 one.  Resolved against the extend's own window the
        anchor became 2026-05-01 all over again, because 2026-05-22 is the
        window's earliest May payday, and the placement then produced a second
        row.  Measured on production: 3 such rows, $39.54 each.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, MONTHLY_FIRST,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()
            before = _rows(template, scenario_id)
            assert date(2026, 5, 8) in before, (
                "May's first paycheck must already hold the row, or the "
                "duplicate this test guards against cannot arise"
            )

            appended = _append_period(seed_user, seed_periods)
            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in appended})
            db.session.flush()

            after = _rows(template, scenario_id)
            may_rows = [
                start for start in after
                if start.year == 2026 and start.month == 5
            ]
            assert may_rows == [date(2026, 5, 8)], (
                f"May 2026 is owed one row and holds {sorted(may_rows)}"
            )
            assert len(after) == len(before)

    def test_a_month_the_extend_first_reaches_is_still_generated(
        self, app, db, seed_user, seed_periods,
    ):
        """The narrowing removes duplicates, not the rows that are owed.

        The paired half of the test above: an extend that opens a NEW calendar
        month must still generate that month's row, or the fix would have
        traded a duplicate for a silent omission.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, MONTHLY_FIRST,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()

            # Two appends: 2026-05-22 (May, covered) then 2026-06-05, which
            # opens June.
            first = _append_period(seed_user, seed_periods)
            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in first})
            second = _append_period(seed_user, seed_periods + first)
            assert second[0].start_date == date(2026, 6, 5)
            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in second})
            db.session.flush()

            after = _rows(template, scenario_id)
            assert date(2026, 6, 5) in after, (
                "June's first paycheck is owed a row and did not get one"
            )
            assert len(after[date(2026, 6, 5)]) == 1


class TestTheWindowStillNarrowsWhatIsWritten:
    """The schedule widened; the write window must not have."""

    def test_a_windowed_pass_writes_only_into_its_window(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-paycheck rule, generated into ONE period, writes one row.

        Resolving against the owner's whole schedule names all ten periods.
        Were the narrowing dropped, this pass would write ten rows -- and a
        real extend would re-walk every historical period on every run.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, EVERY_PERIOD,
            )

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {seed_periods[4].id},
                ),
                scenario_id,
            )
            db.session.flush()

            assert len(created) == 1
            assert created[0].pay_period_id == seed_periods[4].id
            assert list(_rows(template, scenario_id)) == [
                seed_periods[4].start_date,
            ]


class TestTheStartPeriodBoundSurvivesTheWindow:
    """D2: the chosen "First paycheck" is part of the rule, not of the pass."""

    def test_a_windowed_pass_with_its_own_boundary_still_honours_the_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """The batch-boundary shape: an explicit ``effective_from`` + a window.

        **This is the only shape that reproduces D2**, and getting that wrong
        cost this file a false claim.  A window alone does not: the pre-R4b-1
        code defaulted ``effective_from`` to the rule's start period whenever
        the caller passed ``None``, which reproduced the bound by accident.  It
        only vanished for a caller that supplied its OWN boundary -- and
        ``period_population`` supplies exactly that, the new batch's opening
        payday, on every schedule extend.

        With the caller's boundary set, the old code took the explicit value,
        resolved the rule against the window, failed to find start period 5 in
        it, dropped the bound entirely, and generated into a paycheck the user
        had excluded.  Resolved against the owner's schedule the start period
        is found and the bound holds.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                EVERY_PERIOD,
                starts_on=seed_periods[5].start_date,
            )
            window = seed_periods[2:4]

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {p.id for p in window},
                ),
                scenario_id,
                effective_from=window[0].start_date,
            )
            db.session.flush()

            assert created == []
            assert _rows(template, scenario_id) == {}

    def test_the_extend_path_itself_honours_the_bound(
        self, app, db, seed_user, seed_periods,
    ):
        """The same shape through the real repopulation entry point.

        ``populate_periods_from_active_templates`` is what an extend runs, so
        this drives D2's actual production path rather than a reconstruction
        of it.  A rule whose first paycheck is index 5 must gain nothing when
        periods 2-3 are (re)populated.

        **It said "and it defaults its boundary to the new batch's opening
        payday" until pay-calendar plan step C2-f3c**, which deleted that
        boundary as a provable no-op: every period of a write window ends on
        or after that window's own opening payday, so bounding placements by
        it could never drop one.  What holds this case up is therefore the
        RULE's own opening bound resolved against the owner's whole calendar,
        which is what D2 was about; the caller-supplied-boundary shape is
        still covered by the sibling case above, which passes one explicitly.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                EVERY_PERIOD,
                starts_on=seed_periods[5].start_date,
            )

            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in seed_periods[2:4]})
            db.session.flush()

            assert _rows(template, scenario_id) == {}
            assert scenario_id == seed_user["scenario"].id

    def test_the_same_rule_still_generates_from_its_stated_start_on(
        self, app, db, seed_user, seed_periods,
    ):
        """The bound is honoured, not merely restrictive.

        Without this the test above would pass against a rule that generates
        nothing at all.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user,
                EVERY_PERIOD,
                starts_on=seed_periods[5].start_date,
            )

            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {seed_periods[5].id},
                ),
                scenario_id,
            )
            db.session.flush()

            assert [row.pay_period_id for row in created] == [
                seed_periods[5].id,
            ]


class TestThePredictionIsTheGenerationCall:
    """``can_generate_in_period`` answered from a one-period schedule."""

    def test_the_predictor_agrees_with_generation_in_every_period(
        self, app, db, seed_user, seed_periods,
    ):
        """For a ``Monthly First`` rule the two answers must coincide.

        The predictor drives the carry-forward GENERATE branch.  Reading a
        one-period schedule it saw one month holding one payday and answered
        "the engine fires here" for EVERY period: measured on production, 32 of
        61 for the live ``Phone Allowance`` rule, where the true answer is each
        month's first paycheck only.

        ONE schedule serves all ten questions, which is also the shape the
        carry-forward context now threads: building one per call would repeat
        the schedule query and the forward walk once per envelope row.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, MONTHLY_FIRST,
            )
            schedule = GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id))

            predicted = [
                period.start_date for period in seed_periods
                if recurrence_engine.can_generate_in_period(
                    template, period.id, scenario_id, schedule=schedule,
                )
            ]

            recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            generated = sorted(_rows(template, scenario_id))

            assert predicted == generated
            # Stated absolutely too: two equal-but-wrong answers would satisfy
            # the comparison above.  Each 2026 month in the fixture window is
            # owed exactly one row, on its FIRST payday.
            assert generated == [
                date(2026, 1, 2), date(2026, 2, 13), date(2026, 3, 13),
                date(2026, 4, 10), date(2026, 5, 8),
            ]


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
            default_amount=Decimal("0.00"),
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, EVERY_PERIOD,
        )

        profile = SalaryProfile(
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
            federal=made_up_federal(), fica=made_up_fica(),
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
            from app.services.tax_config_service import (  # pylint: disable=import-outside-toplevel
                load_tax_configs_for_year,
            )
            configs = load_tax_configs_for_year(profile, 2026)
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
            # does not move" -- and federal does not move here for the opposite
            # of the stated reason: the SHARED made-up federal rules
            # (``made_up_federal``, the old ``seed_tax_bracket_set`` rows) tax
            # nothing above $47,150, so both annualised bases sit in that top
            # rung and the federal lines match by accident of the fixture.  The
            # first person to give that helper a realistic top bracket would
            # have broken a financial assertion and been sent to the wrong
            # cause.  The pre-tax total is the fact this defect is ABOUT, and
            # it is exact on any bracket table.
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


class TestTheTransferEngineTakesTheSameSchedule:
    """Half the changed surface, and the migration ships a DELETE for it."""

    def _transfer_template(self, seed_user, cadence):
        """Create a recurring transfer template into a fresh savings account.

        Args:
            seed_user: The ``seed_user`` fixture dict.
            cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author.

        Returns:
            The flushed :class:`~app.models.transfer_template.TransferTemplate`.
        """
        # Pylint: ``import-outside-toplevel`` -- the account helper and the
        # transfer model are needed by this class alone; at module scope they
        # would load for every test in the file.
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- see above.
        from tests._test_helpers import create_savings_account  # pylint: disable=import-outside-toplevel

        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Savings Sweep",
            default_amount=Decimal("50.00"),
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(template, cadence)
        return template

    def test_an_extend_does_not_duplicate_a_monthly_first_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer engine inherits the fix through the shared preamble.

        ``transfer_recurrence`` reaches the schedule through
        ``recurrence_engine.resolve_generation_plan``, so D22 is structurally
        shared -- but "shared by construction" is an argument, and the two
        engines are deliberate parallels that have drifted before.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the transfer engine and
            # its model are this class's collaborators only.
            from app.models.transfer import Transfer  # pylint: disable=import-outside-toplevel
            # Pylint: ``import-outside-toplevel`` -- see above.
            from app.services import transfer_recurrence  # pylint: disable=import-outside-toplevel

            scenario_id = seed_user["scenario"].id
            template = self._transfer_template(
                seed_user, MONTHLY_FIRST,
            )
            transfer_recurrence.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()

            def may_starts():
                """Return the May 2026 pay-period starts holding a transfer."""
                rows = (
                    db.session.query(Transfer)
                    .filter_by(
                        transfer_template_id=template.id,
                        scenario_id=scenario_id,
                    ).all()
                )
                return sorted(
                    row.pay_period.start_date for row in rows
                    if row.pay_period.start_date.month == 5
                )

            assert may_starts() == [date(2026, 5, 8)]

            appended = _append_period(seed_user, seed_periods)
            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in appended})
            db.session.flush()

            assert may_starts() == [date(2026, 5, 8)]


class TestRegenerateSweepsOnlyWhatItRewrites:
    """The delete bound and the write bound are ONE bound.

    Found by adversarial review of this step: ``regenerate_for_template``'s
    ``effective_from=None`` default used to take the SCHEDULE's opening payday
    while ``generate_for_template`` wrote only inside the window, so a narrow
    window deleted every row from the owner's first payday forward and
    recreated only a few.  No route reaches it -- both callers pass a
    whole-schedule window -- and nothing in the signature said it was illegal.
    """

    def test_a_narrow_window_does_not_sweep_the_whole_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """Regenerating into periods 5-9 leaves periods 0-4 alone."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, EVERY_PERIOD,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()
            assert len(_rows(template, scenario_id)) == 10

            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {p.id for p in seed_periods[5:]},
                ),
                scenario_id,
            )
            db.session.flush()

            # All ten survive: 0-4 were never in the sweep's window, and 5-9
            # were deleted and regenerated.
            assert sorted(_rows(template, scenario_id)) == [
                period.start_date for period in seed_periods
            ]


class TestTheSweepDomainIsTheWINDOWAndNotABoundOnIt:
    """What let ``regeneration_bound`` go, graded where it is visible.

    The helper existed because the sweep was SQL against a column: "no lower
    bound" had to become a concrete date, and the date had to be the WRITE
    WINDOW's opening or the sweep reached rows the pass would never rewrite.
    Plan step C2-f3c deleted it on the strength of a stronger property -- the
    domain IS the window, so it cannot exceed the window whatever the bound is.

    **The sibling class's narrow-window case cannot see that property**
    (adversarial review of C2-f3c).  It uses a contiguous TAIL, where "the ids
    in the window" and "every period from the window's opening onward" select
    the identical rows -- so it passes byte-identically on both trees and
    measures nothing about the change.  Only a window with a HOLE in it
    separates the two readings, and nothing else in the suite builds one.

    Not reachable from ``app/`` today: both ``regenerate_for_template`` callers
    pass a whole-schedule window.  That was equally true of the asymmetry
    ``regeneration_bound`` was written to close, which is the argument for
    grading it rather than trusting it.
    """

    def test_a_window_with_a_HOLE_sweeps_neither_side_of_the_hole(
        self, app, db, seed_user, seed_periods,
    ):
        """Periods 2 and 6 are the window; 3, 4 and 5 keep their rows."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(seed_user, EVERY_PERIOD)
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(
                    BalanceContext.build(seed_user["user"].id),
                ),
                scenario_id,
            )
            db.session.flush()
            assert len(_rows(template, scenario_id)) == 10

            # The rule is CLEARED, so every row the pass can see is an orphan
            # and the RETIRE branch is what decides.  That makes the domain
            # visible: exactly the rows it reaches are the rows that go.
            db.session.delete(template.recurrence_rule)
            db.session.flush()
            db.session.refresh(template)

            holed = {seed_periods[2].id, seed_periods[6].id}
            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), holed,
                ),
                scenario_id,
            )
            db.session.flush()

            survivors = {
                row.pay_period_id
                for rows in _rows(template, scenario_id).values()
                for row in rows
            }
            assert survivors == {
                period.id for period in seed_periods
            } - holed, (
                "a sweep bounded by the window's OPENING rather than shaped by "
                "the window itself takes periods 3, 4 and 5 with it -- they "
                "sit inside the hole and no pass would recreate them"
            )


class TestTheGeneratePassTakesTheReadPass:
    """What plan step R7d-c-1 changed, and the ordering rule it bought.

    :class:`~app.services.generation_schedule.GenerationSchedule` took a
    :class:`~app.services.pay_calendar.PayCalendar` until that step and takes a
    :class:`~app.services.balance_at.BalanceContext` now, deriving the calendar
    from it.  The reason is plan step R7d-c-2: a loan payment's closing bound
    stops being the ``budget.recurrence_rules.end_date`` column and becomes a
    fold over the loan, which needs a read pass, and the 2026-08-16 ruling
    forbids a producer below the route building one.

    Three properties are graded here, and only the first is a tidiness claim:

    * the value derives NOTHING of its own -- the pass's memo is the request's
      one derivation;
    * a pass whose calendar was resolved BEFORE the write that created the
      window's periods is REFUSED, loudly, rather than silently generating
      into a schedule that does not hold them.  That is the whole safety
      argument for letting a lazily-derived value in here at all;
    * a repopulation opens exactly ONE pass for the whole batch, whatever the
      template count, so the batch holds one calendar derivation, one
      baseline-scenario resolution and one answer per loan.  **It is NOT an
      order-independence claim**: a first draft said a transfer this loop
      generates into a loan would change a later definition's bound, and
      ``period_population``'s docstring carries the measurement that refutes
      it -- future rows do not move a derived payoff, because the ESTIMATED
      tier prices every uncovered future installment from the DEFINITION.
    """

    def test_the_schedule_derives_nothing_of_its_own(
        self, app, db, seed_user, seed_periods,
    ):
        """The pass's memo is the derivation; the value adds none.

        The COLD case is the one that matters: it pins that constructing this
        value costs exactly ONE derivation rather than none (which would mean
        the calendar came from somewhere else) or two.
        """
        with app.app_context():
            cold = BalanceContext.build(seed_user["user"].id)
            with counting_calls(_CALENDAR_DOOR) as counts:
                schedule = GenerationSchedule.for_pass(cold)
            assert counts["calendar_for"] == 1, (
                f"constructing over a cold pass derived the owner's calendar "
                f"{counts['calendar_for']} times; the pass memoizes one"
            )

            with counting_calls(_CALENDAR_DOOR) as again:
                second = GenerationSchedule.for_pass(cold)
                assert second.calendar is schedule.calendar, (
                    "two schedules over one pass must answer the SAME calendar "
                    "object, or the memo is not what they are reading"
                )
            assert again["calendar_for"] == 0, (
                f"a second schedule over the same pass derived "
                f"{again['calendar_for']} more calendars; the memo is what "
                f"holds a request to one"
            )

    def test_a_pass_resolved_before_the_write_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A stale pass cannot generate into periods it cannot see.

        The failure mode this refusal exists for: a caller builds the read
        pass, reads its calendar for some earlier decision, THEN records new
        paydays, then asks this value to write into them.  The pass's memo
        still holds the pre-write schedule, so the new ids are not in it -- and
        without the refusal the window would simply intersect to nothing and
        the extend would report "generated 0 rows" for every definition the
        owner has.
        """
        with app.app_context():
            stale = BalanceContext.build(seed_user["user"].id)
            stale.calendar()  # resolved BEFORE the write below

            created = _append_period(seed_user, seed_periods)
            new_ids = {period.id for period in created}

            with pytest.raises(RecurrenceWindowError, match="are not in user"):
                GenerationSchedule.for_period_ids(stale, new_ids)

            # The CONTROL: the same window, a pass whose memo is still empty
            # when it is asked -- which is the order ``period_population``
            # runs in.  Without this the raise above would be satisfied by any
            # window this owner cannot write into, including a broken one.
            fresh = BalanceContext.build(seed_user["user"].id)
            schedule = GenerationSchedule.for_period_ids(fresh, new_ids)
            assert schedule.write_period_ids == frozenset(new_ids), (
                "a pass resolved AFTER the write must accept the same window "
                "the stale one was refused for"
            )

    def test_a_stale_pass_through_for_pass_is_NOT_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The refusal above does NOT cover :meth:`for_pass`, and here is what does.

        **An adversarial review of plan step R7d-c-1 measured this after the
        docstring claimed the opposite.**  :meth:`for_period_ids` catches a
        stale pass because its window comes from somewhere else; ``for_pass``
        takes its window FROM the calendar, so the window is a subset by
        construction and ``__post_init__`` has nothing to compare.  A stale
        pass there does not raise -- it UNDER-GENERATES in silence, leaving the
        periods it cannot see empty.

        Pinned rather than fixed, because the type cannot tell a stale memo
        from a fresh one and no ``for_pass`` caller in ``app/`` creates a pay
        period.  What this case protects is the next reader of that docstring:
        if a later step teaches ``for_pass`` to refuse, this test says so by
        failing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(seed_user, EVERY_PERIOD)
            db.session.flush()

            stale = BalanceContext.build(seed_user["user"].id)
            covered_before = {
                period.period_id
                for period in stale.calendar().saved()
            }

            created_periods = _append_period(seed_user, seed_periods)
            new_ids = {period.id for period in created_periods}

            # NO raise -- the contrast with the case above is the whole point.
            schedule = GenerationSchedule.for_pass(stale)
            assert schedule.write_period_ids.isdisjoint(new_ids), (
                "a stale pass's window cannot name a period recorded after it "
                "resolved; if it can, the memo is not what for_pass reads"
            )

            created = recurrence_engine.generate_for_template(
                template, schedule, scenario_id,
            )
            db.session.flush()
            placed = {row.pay_period_id for row in created}
            assert placed, "the pass generated nothing, so nothing is graded"
            assert placed <= covered_before, (
                "a stale pass wrote into a period it could not see"
            )
            assert placed.isdisjoint(new_ids), (
                f"the stale pass covered {placed & new_ids}, so this case is "
                f"no longer measuring the silent gap it was written for"
            )

    def test_one_read_pass_serves_the_whole_repopulation(
        self, app, db, seed_user, seed_periods,
    ):
        """Seven definitions across BOTH engines, one pass for the batch.

        ``populate_periods_from_active_templates`` runs both engines over every
        active definition the owner has.  A pass rebuilt per template would
        derive the owner's whole pay calendar once per definition, resolve the
        baseline scenario once per definition, and -- from plan step R7d-c-2 --
        re-fold every destination loan once per definition.  The count is ONE
        for the batch.

        **The counted call is the ROUTE's half since ruling R-R38**, and what
        the count grades moved with it.  The producer cannot open a second pass
        any more -- it takes one and has no constructor -- so "one per batch
        rather than one per template" is now a property of the SIGNATURE.  What
        this case still measures is that ``populate_new_periods`` opens exactly
        one for the batch rather than looping the doors' periods and opening
        one each, which no signature prevents.  Counting the producer alone
        would read ZERO and grade nothing.
        """
        with app.app_context():
            for index in range(6):
                template = _make_template(seed_user, EVERY_PERIOD)
                template.name = f"Recurring {index}"
                db.session.flush()
            # The TRANSFER loop is a second pass over a second table, and an
            # adversarial review of plan step R7d-c-1 found this case graded
            # only the transaction one: with no transfer template the engine
            # iterates nothing, so a per-template rebuild THERE would read one
            # pass and pass.
            destination = create_account_of_type(
                seed_user, db.session, "Savings", name="Holiday Fund",
            )
            make_transfer_template(db.session, seed_user, destination)
            db.session.flush()

            created = _append_period(seed_user, seed_periods)
            new_ids = {period.id for period in created}

            with counting_read_passes() as passes:
                written = populate_new_periods(seed_user["user"].id, created)
            assert written >= 7, (
                f"the count was taken over a repopulation that wrote {written} "
                f"rows; SEVEN definitions were seeded across the two engines "
                f"and both loops have to have run for this count to grade them"
            )
            assert passes["n"] == 1, (
                f"the repopulation opened {passes['n']} read passes for one "
                f"batch of {written} rows; one pass per batch is what makes "
                f"the answer independent of template order"
            )


class TestOneScheduleReadServesTheWholeRequest:
    """The N+1 an adversarial review measured, and the gate against its return.

    ``can_generate_in_period`` runs once per envelope row being carried
    forward.  Building a ``GenerationSchedule`` inside it cost one schedule
    read AND one full forward occurrence walk per row -- measured at 12 queries
    for the preview and 24 for the execute over 12 envelope rows, against a
    baseline of 0.  The schedule is resolved once in
    ``_build_carry_forward_context`` and threaded.

    **The instrument changed at pay-calendar plan step C2-f3c and the claim got
    stronger.**  It counted ``pay_period_service.get_all_periods``, which the
    value used to read for itself; that reader is deleted and the value TOOK
    the caller's :class:`~app.services.pay_calendar.PayCalendar`.  So the
    number this asserts is ZERO derivations inside the service -- the request's
    one derivation happens at its door, which is what ledger row **P68** asked
    for.  ``tests/test_arch/test_one_read_pass_per_render.py`` grades the
    render's total; this grades the service's share of it.

    **Plan step R7d-c-1 made it the read PASS, and the zero now has a
    precondition worth stating**: the derivation is the pass's memo rather than
    a value the route computed, so it happens wherever it is first asked for.
    The route asks at its door (``_resolve_carry_forward_context`` reads
    ``balance_ctx.calendar()`` to look both periods up), so zero below the door
    is still the shape the application runs -- and the second measurement below
    grades the thing that actually prevents the N+1: handed a pass whose memo
    is EMPTY, the service derives ONE calendar for twelve envelope rows, not
    twelve.  The old assertion could not see that at all; a service deriving
    one per row from its own ``calendar_for`` would have failed it, but so
    would a service deriving exactly one, and only the first is the defect.
    """

    def test_a_twelve_row_carry_forward_derives_no_calendar(
        self, app, db, seed_user, seed_periods,
    ):
        """Twelve envelope rows, zero calendar derivations inside the service."""
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the carry-forward service
            # is this class's collaborator only.
            from app.services import carry_forward_service  # pylint: disable=import-outside-toplevel

            scenario_id = seed_user["scenario"].id
            for index in range(12):
                template = _make_template(
                    seed_user, EVERY_PERIOD,
                )
                template.name = f"Envelope {index}"
                template.is_envelope = True
                db.session.flush()
                recurrence_engine.generate_for_template(
                    template,
                    GenerationSchedule.for_pass(
                        BalanceContext.build(seed_user["user"].id),
                    ),
                    scenario_id,
                )
            db.session.commit()

            # **The N+1 gate proper** (plan step R7d-c-1), and it runs FIRST
            # because the execute below carries these rows and a second
            # preview over the same pair then plans nothing.  A pass whose memo
            # is EMPTY derives ONE calendar for the whole preview, not one per
            # envelope row -- which is the claim the two zeros further down
            # cannot make, because a memo that was already full satisfies them
            # whatever the service would have caused on its own.
            cold = BalanceContext.build(seed_user["user"].id)
            with counting_calls(_CALENDAR_DOOR) as cold_counts:
                cold_preview = carry_forward_service.preview_carry_forward(
                    seed_periods[0].id, seed_periods[1].id, scenario_id,
                    balance_ctx=cold,
                )
            assert len(cold_preview.plans) == 12, (
                "the cold count was taken over a preview that planned nothing, "
                "so the envelope branch this grades never ran"
            )
            assert cold_counts["calendar_for"] == 1, (
                f"twelve envelope rows caused "
                f"{cold_counts['calendar_for']} calendar derivations on a pass "
                f"that had none; the memo is what holds it to one, and one per "
                f"row is the N+1 this class exists to prevent"
            )

            # The ROUTE's shape: one pass, and its calendar derived at the
            # door -- ``_resolve_carry_forward_context`` reads it to look both
            # periods up before the service is called at all.
            balance_ctx = BalanceContext.build(seed_user["user"].id)
            balance_ctx.calendar()

            with counting_calls(_CALENDAR_DOOR) as counts:
                preview = carry_forward_service.preview_carry_forward(
                    seed_periods[0].id, seed_periods[1].id, scenario_id,
                    balance_ctx=balance_ctx,
                )
            assert len(preview.plans) == 12, (
                "the count was taken over a preview that planned nothing, so "
                "the envelope branch this grades never ran"
            )
            assert counts["calendar_for"] == 0, (
                f"the preview derived the owner's calendar "
                f"{counts['calendar_for']} times below its door; it takes the "
                f"one the route's pass already holds"
            )

            with counting_calls(_CALENDAR_DOOR) as counts:
                carried = carry_forward_service.carry_forward_unpaid(
                    seed_periods[0].id, seed_periods[1].id, scenario_id,
                    balance_ctx=balance_ctx,
                )
            assert carried == 12, (
                "the count was taken over an execute that carried nothing"
            )
            assert counts["calendar_for"] == 0, (
                f"the execute path derived the owner's calendar "
                f"{counts['calendar_for']} times below its door; it takes the "
                f"one the route's pass already holds"
            )

            # **The instrument's own control**, and an adversarial review of
            # plan step C2-f3c is why it is here: ``== 0`` is satisfied
            # identically by "the service derives nothing" and by "the counter
            # is blind", and only one of those is the claim.
            #
            # It calls the door the way ``app/`` code calls it -- through the
            # module attribute -- because that is the only shape
            # ``counting_calls`` can see.  The instrument patches every
            # ``app.*`` module holding the real function as an attribute of
            # that NAME, so a producer that did ``from ... import calendar_for
            # as _cal`` would derive uncounted and both zeros above would read
            # green through the N+1 this class exists to prevent.  That hole is
            # the instrument's, not this step's; what this control pins is that
            # the counter is live for the ordinary spelling.
            from app.services import pay_calendar  # pylint: disable=import-outside-toplevel

            with counting_calls(_CALENDAR_DOOR) as control:
                pay_calendar.calendar_for(seed_user["user"].id)
            assert control["calendar_for"] == 1, (
                f"the counter saw {control['calendar_for']} of one direct call "
                f"to the door it is keyed on, so both zeros above mean nothing"
            )


class TestNoOrmPayPeriodEntersTheSeam:
    """The generation seam names ``budget.pay_periods`` in exactly one way: ids.

    **What makes plan step C4 safe for this seam**, and the property
    pay-calendar plan step C2-f3c actually delivered.  Every module below used
    to hold an ORM ``PayPeriod`` -- the schedule value carried a tuple of them,
    the placements carried one each, the row select joined the table on
    ``end_date``, and the amount producer resolved an id back to a derived
    period.  Reading a period's BOUNDS off the row means reading
    ``end_date`` and ``period_index``, the two columns C4-c dropped, and finding
    those readers by hand is what ledger row **P70** records going wrong: an
    AST census found SIX in query position where the step's own specification
    named two.

    Graded on the IMPORT rather than on the attribute, because
    ``txn.pay_period.end_date`` reaches the same columns through a relationship
    with no ``PayPeriod`` name in sight, while a module that imports the model
    at all is a module that can grow one.  The seam takes ids in and derived
    periods through, so it has no reason to name the mapped class.
    """

    #: Every module of the generation seam, as repository-relative paths.  The
    #: two engines, the value they share, the shared queries, and the
    #: orchestrator above them.
    _SEAM = (
        "app/services/generation_schedule.py",
        "app/services/period_population.py",
        "app/services/transfer_recurrence.py",
        "app/services/_recurrence_common.py",
        "app/services/recurrence_engine/__init__.py",
        "app/services/recurrence_engine/_amounts.py",
        "app/services/recurrence_engine/_conflicts.py",
        "app/services/recurrence_engine/_generate.py",
        "app/services/recurrence_engine/_maintain.py",
        "app/services/recurrence_engine/_plan.py",
    )

    def _pay_period_imports(self, relative_path):
        """Return every import of the pay-period MODEL in *relative_path*.

        Keyed on what was imported rather than on the local binding, so
        ``from app.models.pay_period import PayPeriod as Row`` cannot hide.

        Args:
            relative_path: Path under the repository root to parse.

        Returns:
            The set of ``(module, name)`` pairs naming the model.
        """
        # Pylint: ``import-outside-toplevel`` -- this census is the only user.
        import ast  # pylint: disable=import-outside-toplevel
        import pathlib  # pylint: disable=import-outside-toplevel

        root = pathlib.Path(__file__).resolve().parents[2]
        tree = ast.parse((root / relative_path).read_text())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    # BOTH spellings: ``from app.models.pay_period import
                    # PayPeriod`` and ``from app.models import pay_period``.
                    # An adversarial review of plan step C2-f3c found the
                    # second walked straight past this census.
                    if module.endswith("models.pay_period"):
                        found.add((module, alias.name))
                    elif f"{module}.{alias.name}".endswith("models.pay_period"):
                        found.add((module, alias.name))
            elif isinstance(node, ast.Import):
                found.update(
                    (alias.name, alias.name) for alias in node.names
                    if alias.name.endswith("models.pay_period")
                )
        return found

    def test_no_seam_module_imports_the_pay_period_model(self):
        """None of the ten modules names ``app.models.pay_period``."""
        offenders = {
            relative: self._pay_period_imports(relative)
            for relative in self._SEAM
            if self._pay_period_imports(relative)
        }
        assert offenders == {}, offenders

    def test_the_census_sees_an_import_when_there_is_one(self):
        """The CONTROL: a module that DOES import the model is detected.

        Without it the assertion above passes for a census that parses nothing
        -- a wrong path, a swallowed error, an ``ast`` walk that misses
        ``ImportFrom``.  ``pay_period_write`` is the one module in ``app/``
        whose whole job is writing that table, so it will always import it.
        """
        assert ("app.models.pay_period", "PayPeriod") in (
            self._pay_period_imports("app/services/pay_period_write.py")
        )

    def test_the_census_sees_every_spelling_of_the_import(self, tmp_path):
        """The CONTROL for the census's own BRANCHES, one per import form.

        The live tree exercises exactly one of the three: ``from
        app.models.pay_period import PayPeriod``.  An adversarial review of
        plan step C2-f3c found the ``from app.models import pay_period``
        spelling walking past the census unseen, and the ``ast.Import`` arm
        with no firing control at all -- nothing in ``app/`` writes ``import
        app.models.pay_period``, so it could have been deleted silently.
        """
        # Pylint: ``import-outside-toplevel`` -- this control is the only user.
        import ast  # pylint: disable=import-outside-toplevel

        spellings = {
            "from app.models.pay_period import PayPeriod\n":
                ("app.models.pay_period", "PayPeriod"),
            "from app.models import pay_period\n":
                ("app.models", "pay_period"),
            "import app.models.pay_period\n":
                ("app.models.pay_period", "app.models.pay_period"),
        }
        for source, expected in spellings.items():
            tree = ast.parse(source)
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        if module.endswith("models.pay_period"):
                            found.add((module, alias.name))
                        elif f"{module}.{alias.name}".endswith(
                            "models.pay_period",
                        ):
                            found.add((module, alias.name))
                elif isinstance(node, ast.Import):
                    found.update(
                        (alias.name, alias.name) for alias in node.names
                        if alias.name.endswith("models.pay_period")
                    )
            assert expected in found, (source, found)
