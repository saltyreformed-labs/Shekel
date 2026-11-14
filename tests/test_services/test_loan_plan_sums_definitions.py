"""R16-b-2: the ESTIMATED tier sums EVERY definition, on its own cadence, by occurrence.

Plan step **R16-b-2** (``docs/plans/implementation_plan_recurrence_redesign.md``)
and the rulings of 2026-09-11 it carries.  A loan's forward plan
(:func:`app.services.balance_at._plan.loan_plan`) is what a generate pass over
the owner's whole schedule would write, priced as those rows would be priced:

* every active recurring transfer into the loan is walked on its OWN cadence
  under its AUTHORED closing (rulings **R-R35**, **R-R36**, **R-R37**,
  **R-R65**; findings **D47**, **D48**);
* an occurrence the schedule places that NO ROW IN ANY STATE answers is
  priced from the definition, past or future, and a cancelled or deleted row
  still answers its occurrence (ruling **R-R64**, finding **D46**); an
  occurrence the schedule cannot place is neither generated nor estimated;
* an occurrence is dated as generation would date its row (**R-R69**) and
  priced through the amount model's own arm (**R-R67**), so the plan is
  INVARIANT under generation;
* the charge calendar is the CONTRACT's, seed-aware (**R-R68**; findings
  **D53**, **D54**).

Each case builds the state through the app's own doors -- the loan door's
start sync, the generate pass, the soft-delete door, the transfer create door
-- and reads the plan back, so a fixture cannot describe a state the app
cannot reach.  The companion ``test_loan_plan_assembly.py`` pins the
contract-only arm and the PLANNED tier; ``test_amount_source.py`` pins the
price a row and its estimate share.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    balance_at,
    pay_period_write,
    template_amount_service,
    transfer_recurrence,
    transfer_service,
)
from app.services.balance_at import BalanceContext
from app.services.balance_at._loan_stream import loan_timeline
from app.services.balance_at._plan import (
    _PAYOFF_EXTENSION_MONTHS,
    _window_end,
    loan_plan,
)
from app.services.generation_schedule import GenerationSchedule
from app.services.installment_calendar import installment_dates
from app.services.liability_sign import owed
from app.services.loan_ledger import (
    LoanCashEvent,
    LoanEventStream,
    with_contract_charges,
)
from app.services.recurrence import compute_due_date
from app.services.settle_day import SettleDay
from tests._test_helpers import (
    bind_rule_to_loan,
    create_loan_account,
    create_settled_transfer,
    create_transfer,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_cadence_rule,
    make_every_period_rule,
    on_both_sides,
    rebuild_calendar,
    rhythm_of,
)
from tests.oracles.recurrence_baseline import MONTHLY, MONTHLY_FIRST

# A short amortizing loan whose whole schedule is enumerable: $12,000 at 6%
# over 6 months, originated 2026-01-01, due on the 1st -- the same contract
# ``test_loan_plan_assembly.py`` reads, so its figures cross-check.  The
# level payment is $2,035.15.
_PRINCIPAL = Decimal("12000.00")
_RATE = Decimal("0.06")
_TERM = 6
_ORIGINATION = date(2026, 1, 1)
_LEVEL = Decimal("2035.15")
# Read mid-life: 02-01, 03-01, 04-01 are PAST; 05-01, 06-01, 07-01 FUTURE.
_AS_OF = date(2026, 4, 15)
_TOMORROW = _AS_OF + timedelta(days=1)


def _loan(seed_user):
    """Create the controlled loan and its read pass."""
    account = create_loan_account(
        seed_user, db.session,
        principal=_PRINCIPAL, rate=_RATE, term=_TERM,
        origination_date=_ORIGINATION, payment_day=1,
    )
    ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
    return account, ctx


def _definition(seed_user, loan, base, *, name, cadence=None, bind=True):
    """Author a STATED-price recurring transfer of *base* into *loan*.

    The generic form's shape (no settings row): the price is stated through
    the one write door, effective from the loan's origination so the series
    covers every occurrence.  ``cadence=None`` authors the loan door's
    MONTHLY rule and, with *bind*, syncs its start to the first contractual
    installment as the door does; any other cadence is authored as given.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    template_amount_service.set_amount(
        template, base, effective_on=_ORIGINATION,
    )
    if cadence is None:
        rule = make_cadence_rule(template, MONTHLY, fires_on_day=1)
        if bind:
            bind_rule_to_loan(rule, loan.id)
    else:
        make_cadence_rule(template, cadence, fires_on_day=1)
    db.session.flush()
    return template


def _schedule_through(seed_user, num_periods):
    """Extend the owner's biweekly schedule from 2026-01-02 to *num_periods* paydays."""
    pay_period_write.record_paydays(
        user_id=seed_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=num_periods,
        rhythm=rhythm_of(14),
    )
    db.session.flush()


def _generate(seed_user, template):
    """Run the transfer generate pass for *template* over every saved period."""
    ctx = BalanceContext.build(template.user_id)
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            ctx, {period.period_id for period in ctx.calendar().periods},
        ),
        seed_user["scenario"].id,
    )
    db.session.flush()


def _charges(account, ctx):
    """Return the loan's timeline CHARGES for the pass *ctx* reads.

    Every contractual installment from origination through the timeline's
    last event (plan step recurrence:R16-c-2, ruling **R-R100**): the plan
    carries the loan's contract terms and no charge list of its own, so what
    a month is charged is read off the one timeline the seam replays.
    """
    return loan_timeline(account, ctx).stream.charges


def _estimated(plan):
    """Return the plan's ESTIMATED payments ascending by due date."""
    return sorted(
        (p for p in plan.payments if p.is_estimated), key=lambda p: p.due_date,
    )


@pytest.mark.usefixtures("db", "seed_periods")
class TestEveryDefinitionIsSummed:
    """D47: every recurring transfer into a loan is a payment against it."""

    def test_a_second_definition_adds_its_occurrences_and_pulls_the_payoff_in(
        self, seed_user,
    ):
        """Two definitions into one loan both reach the plan, and the fold sums them.

        The loan's own payment at the level $2,035.15 alone clears on the
        contractual 2026-07-01.  A second $500.00 monthly sweep -- created
        SECOND, so the id tie-break the old tier priced from never picks it --
        adds its own occurrences beside the payment's, and $500.00 a month of
        extra principal against a $6,044.87 seed brings the payoff forward one
        installment, to 2026-06-01.  The old tier priced the whole plan from
        the oldest definition and could not see the sweep at all.
        """
        account, ctx = _loan(seed_user)
        _definition(seed_user, account, _LEVEL, name="Payment")
        assert balance_at.loan_payoff_date(account, ctx) == date(2026, 7, 1)

        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
        _definition(seed_user, account, Decimal("500.00"), name="Sweep")
        plan = loan_plan(account, ctx)

        by_due = {}
        for payment in _estimated(plan):
            by_due.setdefault(payment.due_date, []).append(payment.cash)
        assert by_due[date(2026, 5, 1)] == [_LEVEL, Decimal("500.00")]
        assert by_due[date(2026, 6, 1)] == [_LEVEL, Decimal("500.00")]
        assert balance_at.loan_payoff_date(account, ctx) == date(2026, 6, 1)

    def test_a_definition_on_its_own_cadence_emits_its_own_occurrences(
        self, seed_user,
    ):
        """D48: an every-paycheck definition is 26 occurrences a year, not 12.

        The old tier synthesized one slot per CONTRACTUAL month whatever the
        definition's cadence said, so this definition's estimate read
        byte-identical to a monthly one.  Now each occurrence is a payday: the
        biweekly schedule from 2026-01-02 puts 02-13, 02-27, 03-13 and 03-27
        in February and March -- two per month -- and the walk continues at
        the cadence past the saved horizon.
        """
        account, ctx = _loan(seed_user)
        _schedule_through(seed_user, 8)
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
        template = _definition(
            seed_user, account, Decimal("900.00"), name="Paycheck sweep",
            cadence=None, bind=False,
        )
        # Re-author as every-paycheck through the shared builder: a PERIOD
        # rule's first occurrence is a payday, which the loan door never
        # writes, so it is stated here.
        db.session.delete(template.recurrence_rule)
        db.session.flush()
        make_every_period_rule(db.session, template)
        db.session.flush()

        plan = loan_plan(account, ctx)

        dues = [p.due_date for p in _estimated(plan)]
        in_2026 = [d for d in dues if d.year == 2026 and d >= _AS_OF]
        # Paydays from 04-24 through 12-25 at 14 days: eighteen of them.
        assert len(in_2026) == 18, in_2026
        assert all(
            (later - earlier).days == 14 for earlier, later in zip(in_2026, in_2026[1:])
        )
        assert {p.cash for p in _estimated(plan)} == {Decimal("900.00")}


@pytest.mark.usefixtures("db", "seed_periods")
class TestAnOccurrenceNoRowAnswers:
    """R-R64: what nothing answers is priced; what the owner un-planned is not."""

    def test_a_past_occurrence_with_no_row_is_priced_and_visible_tomorrow(
        self, seed_user,
    ):
        """An in-schedule occurrence before ``as_of`` that no row answers is estimated.

        The schedule opens 2026-01-02, so the 02-01, 03-01 and 04-01
        occurrences are placeable; nothing generated them, so they are "not
        generated yet" and priced exactly as the rows generation would write
        -- dated on their own day, VISIBLE the day after ``as_of`` (ruling
        D1's clamp, the same one an overdue projected row gets).
        """
        account, ctx = _loan(seed_user)
        _definition(seed_user, account, _LEVEL, name="Payment")

        plan = loan_plan(account, ctx)

        past = [p for p in _estimated(plan) if p.due_date < _AS_OF]
        assert [p.due_date for p in past] == [
            date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1),
        ]
        assert all(p.effective_date == _TOMORROW for p in past)
        assert all(p.cash == _LEVEL for p in past)

    def test_a_soft_deleted_row_answers_its_occurrence_and_pays_nothing(
        self, seed_user,
    ):
        """A row the owner removed is neither resurrected by the estimate nor paid.

        Generate the rows, soft-delete March's through the transfer door, and
        the 03-01 occurrence leaves the plan entirely -- not PLANNED (the row
        is gone from the loaders) and not ESTIMATED (the tombstone still
        answers the occurrence) -- while 02-01 and 04-01 stay PLANNED.
        """
        account, ctx = _loan(seed_user)
        _schedule_through(seed_user, 8)
        template = _definition(seed_user, account, _LEVEL, name="Payment")
        _generate(seed_user, template)
        march = (
            db.session.query(Transfer)
            .filter(
                Transfer.transfer_template_id == template.id,
                Transfer.occurs_on == date(2026, 3, 1),
            )
            .one()
        )
        transfer_service.delete_transfer(
            march.id, seed_user["user"].id, soft=True,
        )
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

        plan = loan_plan(account, ctx)

        dues = sorted(p.due_date for p in plan.payments if p.due_date < _AS_OF)
        assert dues == [date(2026, 2, 1), date(2026, 4, 1)]
        assert not any(
            p.is_estimated for p in plan.payments if p.due_date < _AS_OF
        )
        # ...and March is still CHARGED (rulings R-R37 and R-R71, finding
        # D53): the contract charges every month after the loan's last
        # balance assertion whether or not a payment lands in it, so the
        # skipped month's $50.12 stands until April's payment clears it.
        # Hand-checked from the $12,000 seed: Feb pays 1,975.15 principal
        # (10,024.85), March charges 50.12 and nothing pays, April's payment
        # clears 50.12 + 50.12 and pays 1,934.91 (8,089.94), then 40.45 /
        # 1,994.70 (6,095.24), 30.48 / 2,004.67 (4,090.57), 20.45 / 2,014.70
        # (2,075.87), 10.38 / 2,024.77 (51.10), and the 09-01 installment
        # clears the rest: the deletion moved the payoff OUT by one
        # installment AND the skipped month's interest, 2026-09-01.  Read on
        # any other day the answer is the same, which is what R-R71 bought:
        # bounded at the read day instead, this loan answered 2026-08-01
        # once as_of had passed the skipped month.
        assert [c.on_date for c in _charges(account, ctx)][:3] == [
            date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1),
        ]
        assert balance_at.loan_payoff_date(account, ctx) == date(2026, 9, 1)

    def test_an_occurrence_at_or_before_origination_is_not_estimated(
        self, seed_user,
    ):
        """Ruling R-C's boundary, asked of the estimate as the write door asks it of a row.

        A SECOND definition into the loan keeps the start its owner authored
        (its control is unlocked, plan ledger row D50); authored before the
        loan, it names occurrences the write door would REFUSE
        (``_reject_payment_before_origination``), so a generate pass would
        not write them and the fold, which would erase such a payment against
        a zero balance, must not price them.  The plan drops them as it drops
        every payment at or before the loan's LATEST assertion, of which the
        origination is the first (ruling R-R72, the one post-anchor
        predicate :func:`~app.services.rate_period_engine.due_after_anchor`
        the door's refusal is spelled through too), so the occurrence ON the
        origination date is refused as well.

        **The schedule is opened BEFORE the loan so that the boundary is
        what this test measures.**  Its first version left ``seed_periods``'
        2026-01-02 opening in place, under which 2025-11-01, 2025-12-01 and
        the on-origination 2026-01-01 occurrence have no paycheck to live in
        and are dropped by R-R64's schedule bound before any origination
        predicate is asked -- an adversarial review deleted the predicate and
        the test stayed green.  Opened at 2025-12-05, the 2026-01-01
        occurrence IS placeable (the 12-19 paycheck covers it) and only the
        assertion bound refuses it; 2025-11-01 and 2025-12-01 still precede
        the schedule, which is the other boundary and not this one.
        """
        # Opened before the loan exists: a loan's books open the day before
        # its origination, and no calendar may open at or before an account's
        # books (ruling balance:R-HG).
        rebuild_calendar(seed_user["user"].id, date(2025, 12, 5), 16, 14)
        account, ctx = _loan(seed_user)
        _definition(seed_user, account, _LEVEL, name="Payment")
        early = _definition(
            seed_user, account, Decimal("100.00"), name="Early sweep",
            bind=False,
        )
        early.recurrence_rule.starts_on = date(2025, 11, 1)
        db.session.flush()
        template_amount_service.set_amount(
            early, Decimal("100.00"), effective_on=date(2025, 11, 1),
        )
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
        # The precondition that makes the case discriminating: the schedule
        # can place the on-origination occurrence, so nothing but R-C drops it.
        assert ctx.calendar().span_containing(_ORIGINATION) is not None

        plan = loan_plan(account, ctx)

        sweep = sorted(
            p.due_date for p in _estimated(plan) if p.cash == Decimal("100.00")
        )
        assert sweep, "precondition: the sweep's later occurrences are estimated"
        assert sweep[0] == date(2026, 2, 1), sweep[:3]
        assert all(due > _ORIGINATION for due in sweep)

    def test_an_occurrence_before_the_schedule_opens_is_not_estimated(
        self, seed_user,
    ):
        """R-R64's boundary: a definition starting before the owner's first payday.

        Authored ahead of the loan and never bound to it, the rule names
        2025-06-01 onward; ``seed_periods``' schedule opens 2026-01-02, so the
        2025 occurrences have no paycheck to live in under
        ``CONTAINING_DATE`` and are neither generated nor estimated.  Read
        literally the ruling would have priced them -- the Mortgage's rule
        names 87 such occurrences.
        """
        account, ctx = _loan(seed_user)
        template = _definition(
            seed_user, account, _LEVEL, name="Payment", bind=False,
        )
        rule = template.recurrence_rule
        rule.starts_on = date(2025, 6, 1)
        db.session.flush()
        template_amount_service.set_amount(
            template, _LEVEL, effective_on=date(2025, 6, 1),
        )
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

        plan = loan_plan(account, ctx)

        assert min(p.due_date for p in plan.payments) == date(2026, 2, 1)


@pytest.mark.usefixtures("db", "seed_periods")
class TestTheEstimateIsWhatGenerationWouldWrite:
    """R-R67 / R-R69: generating the rows changes nothing the fold reads."""

    def test_generating_the_rows_replaces_estimates_with_identical_records(
        self, seed_user,
    ):
        """The plan's (due date, cash) pairs are byte-identical before and after generation.

        The invariant the whole step exists to establish, over occurrences
        rather than month slots: every row the generate pass writes carries
        the date and the cash its estimate carried, so the loan's payoff
        cannot move when generation runs.  Past occurrences included -- the
        rows generation writes for them are overdue projected rows, clamped
        to tomorrow exactly as their estimates were.
        """
        account, ctx = _loan(seed_user)
        _schedule_through(seed_user, 14)
        template = _definition(seed_user, account, _LEVEL, name="Payment")
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
        before = loan_plan(account, ctx)
        before_charges = _charges(account, ctx)
        assert all(p.is_estimated for p in before.payments)

        _generate(seed_user, template)
        db.session.commit()
        written = db.session.query(Transfer).filter(
            Transfer.transfer_template_id == template.id,
        ).count()
        assert written >= 6, "precondition: the pass wrote the schedule's rows"
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
        after = loan_plan(account, ctx)
        after_charges = _charges(account, ctx)

        def keyed(plan):
            return [
                (p.due_date, p.effective_date, p.cash) for p in plan.payments
            ]

        assert keyed(after) == keyed(before)
        assert sum(1 for p in after.payments if not p.is_estimated) == written
        assert [(c.on_date, c.escrow) for c in after_charges] == [
            (c.on_date, c.escrow) for c in before_charges
        ]

    def test_a_monthly_first_occurrence_is_dated_on_its_funding_payday(
        self, seed_user,
    ):
        """A ``Monthly First`` definition's estimate carries the row's date, not the occurrence's.

        Generation dates such a row from its paycheck (``compute_due_date``
        answers ``period.start_date`` for a cadence with no scheduling day),
        so the estimate does too -- past the saved horizon on a PROJECTED
        payday.  Asserted against the same function over the same placement,
        and against the occurrence itself, which it must NOT equal.
        """
        account, ctx = _loan(seed_user)
        _schedule_through(seed_user, 8)
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
        template = _definition(
            seed_user, account, Decimal("300.00"), name="Monthly first",
            cadence=MONTHLY_FIRST,
        )
        db.session.flush()

        plan = loan_plan(account, ctx)

        calendar = ctx.calendar()
        estimates = _estimated(plan)
        assert estimates, "precondition: the definition names occurrences"
        for payment in estimates:
            period = calendar.span_containing(payment.due_date)
            assert payment.due_date == period.start_date, payment
            # The occurrence this payday funds: the 1st of its month (a
            # ``Monthly First`` rule fires on the 1st and is funded by the
            # first paycheck starting on or after it).
            assert payment.due_date == compute_due_date(
                template.recurrence_rule, period.start_date.replace(day=1),
                period,
            )
        assert any(p.due_date.day != 1 for p in estimates), (
            "a payday-dated estimate must differ from the occurrence on the 1st"
        )


@pytest.mark.usefixtures("db", "seed_periods")
class TestTheChargeCalendarIsTheContracts:
    """R-R68 / R-R71: charges follow the contract's installments, seed-aware."""

    def test_a_skipped_month_is_charged_whichever_side_of_today_it_is_on(
        self, seed_user, monkeypatch,
    ):
        """R-R71: the payoff is a function of the records, not of the read day.

        The adversarial review's case: Feb-Apr settled, May's row soft-deleted.
        Charging every month at or after ``as_of`` -- the calendar as first
        built -- charged May while it lay ahead of an April read and nowhere
        once a May read had passed it, so the same records answered
        2026-09-01 in April and 2026-08-01 in May.  Bounded at the loan's
        last balance assertion instead, both reads answer 2026-09-01, and the
        skipped month's $30.22 stands in both.
        """
        # The settle door refuses a settle day that has not happened, so the
        # clock is pinned past the last settlement.
        freeze_today(monkeypatch, date(2026, 4, 15))
        account, _ctx = _loan(seed_user)
        _schedule_through(seed_user, 14)
        template = _definition(seed_user, account, _LEVEL, name="Payment")
        _generate(seed_user, template)
        for occurrence in (date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)):
            row = (
                db.session.query(Transfer)
                .filter(
                    Transfer.transfer_template_id == template.id,
                    Transfer.occurs_on == occurrence,
                )
                .one()
            )
            transfer_service.update_transfer(
                row.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                side_days=on_both_sides(
                    row.from_account_id, row.to_account_id,
                    SettleDay(day=occurrence, basis=SettledDayBasisEnum.ENTERED),
                ),
            )
        may = (
            db.session.query(Transfer)
            .filter(
                Transfer.transfer_template_id == template.id,
                Transfer.occurs_on == date(2026, 5, 1),
            )
            .one()
        )
        transfer_service.delete_transfer(may.id, seed_user["user"].id, soft=True)
        db.session.commit()

        payoffs = {}
        for read_day in (date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15)):
            # A REAL later read: the clock moves with the read day, so the
            # confirmed present is what that day's reader sees rather than
            # an ``as_of`` past today's fallback.
            freeze_today(monkeypatch, read_day)
            ctx = BalanceContext.build(seed_user["user"].id, read_day)
            assert date(2026, 5, 1) in {
                c.on_date for c in _charges(account, ctx)
            }, read_day
            payoffs[read_day] = balance_at.loan_payoff_date(account, ctx)
        assert set(payoffs.values()) == {date(2026, 9, 1)}, payoffs

    def test_an_ad_hoc_extra_does_not_displace_the_installment_of_its_month(
        self, seed_user,
    ):
        """Occurrence identity, not month slot: an extra ADDS, it does not substitute.

        A $2,000.00 ad-hoc projected transfer on 05-20 beside the definition's
        05-01 occurrence: the old month-slot de-dup dropped the 05-01
        installment as "covered" by the extra, so the plan paid $2,000.00 in
        May where the owner will pay $4,035.15; by occurrence the installment
        is still owed and both reach the plan.  May is charged ONCE, on the
        contract's day, whichever payment lands first.
        """
        account, ctx = _loan(seed_user)
        _schedule_through(seed_user, 14)
        _definition(seed_user, account, _LEVEL, name="Payment")
        may = next(
            p for p in BalanceContext.build(seed_user["user"].id).calendar().periods
            if p.covers(date(2026, 5, 20))
        )
        create_transfer(
            seed_user, db.session, seed_user["account"], account,
            db.session.get(PayPeriod, may.period_id),
            amount=Decimal("2000.00"), due_date=date(2026, 5, 20),
        )
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

        plan = loan_plan(account, ctx)

        in_may = sorted(
            (p.due_date, p.cash) for p in plan.payments
            if (p.due_date.year, p.due_date.month) == (2026, 5)
        )
        assert in_may == [
            (date(2026, 5, 1), _LEVEL), (date(2026, 5, 20), Decimal("2000.00")),
        ]
        assert [
            c.on_date for c in _charges(account, ctx)
            if (c.on_date.year, c.on_date.month) == (2026, 5)
        ] == [date(2026, 5, 1)]

    def test_the_charge_sequence_reaches_every_payment_past_the_extension(
        self, seed_user,
    ):
        """A plan payment sixty-plus months past the contract still faces its interval's charge.

        The adversarial review's M1: the calendar's dates were the contract's
        rows plus the sixty-month extension, so a loan that had matured more
        than five years ago while still owing (``_secured_debt`` names the
        shape) folded a live projected row against NO charge, where the old
        payments-derived calendar followed it.  Since plan step
        recurrence:R16-c-2 a stream is charged through its OWN last event,
        whatever lies there (:func:`~app.services.loan_ledger.with_contract_charges`,
        ruling **R-R100**), one installment a month, and the sixty-month
        extension is a recurring payment's window alone (``_plan._window_end``,
        sixty installments past the contract's last: 2031-07-01).  The
        window's installments are read off the loan's own rule here; they
        were the from-origination schedule's rows and the sixty dates listed
        past its last until plan step recurrence:R16-f deleted that schedule.
        """
        account, ctx = _loan(seed_user)
        calendar = loan_plan(account, ctx).calendar

        assert _window_end(calendar) == date(2031, 7, 1)
        bounded = installment_dates(
            calendar.origination_date, calendar.payment_day,
            _window_end(calendar),
        )
        assert len(bounded) == _TERM + _PAYOFF_EXTENSION_MONTHS
        assert bounded[_TERM] == date(2026, 8, 1)

        # A payment on the 10th faces the installment its interval opens on,
        # dated on the contract's day (the 1st): the charges end in the
        # payment's month, whether it falls before or after the contract's day.
        for far in (date(2035, 3, 10), date(2035, 3, 1)):
            extended = [
                charge.on_date for charge in with_contract_charges(
                    LoanEventStream(
                        charges=(),
                        payments=(),
                        projections=[LoanCashEvent(
                            on_date=far, cash=_LEVEL, source=None,
                            visible_on=far,
                        )],
                    ),
                    calendar,
                ).charges
            ]
            assert extended[:len(bounded)] == bounded
            assert extended[-1] == date(2035, 3, 1), far
        assert all(
            (later.year - earlier.year) * 12 + later.month - earlier.month == 1
            for earlier, later in zip(extended, extended[1:])
        )
        assert not with_contract_charges(
            LoanEventStream(charges=(), payments=()), calendar,
        ).charges

    def test_a_slot_the_seed_charged_is_not_charged_again(
        self, seed_user, monkeypatch,
    ):
        """D54: a projected extra in a settled month faces no fresh charge.

        April's installment settled on 04-01 (charged there); a $300.00
        projected extra on 04-10 is in the plan.  The old calendar charged
        April AGAIN at the extra's date.  Now April is charged ONCE, on its
        installment, in the one timeline (plan step recurrence:R16-c-2,
        ruling R-R100), so the extra pays pure principal -- and the forward
        periods from May are charged whether or not a payment lands in them.
        Until that step the check read the plan's own charge list, which
        excluded the settled walk's April slot; the developer approved the
        re-expressed check (rule 5).
        """
        # The settle door refuses a settle day that has not happened, so the
        # clock is the read day.
        freeze_today(monkeypatch, _AS_OF)
        account, ctx = _loan(seed_user)
        _schedule_through(seed_user, 14)
        _definition(seed_user, account, _LEVEL, name="Payment")
        calendar = BalanceContext.build(seed_user["user"].id).calendar()
        april = db.session.get(
            PayPeriod, calendar.span_containing(date(2026, 4, 1)).period_id,
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account, april,
            amount=_LEVEL, settled_on=date(2026, 4, 1), due_date=date(2026, 4, 1),
        )
        create_transfer(
            seed_user, db.session, seed_user["account"], account,
            db.session.get(
                PayPeriod, calendar.span_containing(date(2026, 4, 10)).period_id,
            ),
            amount=Decimal("300.00"), due_date=date(2026, 4, 10),
        )
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

        plan = loan_plan(account, ctx)

        assert any(
            p.due_date == date(2026, 4, 10) and p.cash == Decimal("300.00")
            for p in plan.payments
        ), "precondition: the extra is in the plan"
        timeline = loan_timeline(account, ctx)
        charged = [c.on_date for c in timeline.stream.charges]
        assert [
            on_date for on_date in charged
            if (on_date.year, on_date.month) == (2026, 4)
        ] == [date(2026, 4, 1)]
        extra = next(
            outcome for outcome in timeline.projected_splits
            if outcome.due_date == date(2026, 4, 10)
        )
        assert (extra.interest, extra.escrow, extra.principal) == (
            Decimal("0.00"), Decimal("0.00"), Decimal("300.00"),
        )
        # The contract's installments from the read on: 05-01, 06-01, 07-01.
        forward = [date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1)]
        ahead = [on_date for on_date in charged if on_date >= _AS_OF]
        assert ahead[:len(forward)] == forward
        # ...and past the contract the extension is charged too, monthly.
        assert ahead[len(forward)] == date(2026, 8, 1)


@pytest.mark.usefixtures("db", "seed_periods")
class TestTheLatestAssertionIsTheBoundary:
    """R-R72: the plan folds nothing the loan's latest assertion subsumes."""

    def test_a_payment_the_latest_assertion_subsumes_is_not_folded(
        self, seed_user, monkeypatch,
    ):
        """A projected row due at or before a true-up is inside that true-up.

        Rows generated for 02-01, 03-01 and 04-01, all still projected, and
        the owner asserts $12,000.00 on 04-10 -- nothing paid.  The settled
        walk, once those rows settle, walks each and then RESETS at the
        anchor, so their cash never reaches the post-assertion balance; the
        plan must not fold them either, or settling the rows moves the
        balance by their whole cash.  An adversarial review of this step
        measured exactly that: $6,044.87 on 04-16 projected, $12,000.00 the
        day the rows settled, and the payoff 2026-07-01 -> 2026-10-01.

        Now the plan drops every payment due at or before 04-10
        (``due_after_anchor``) and the first charge the assertion does not
        clear is 05-01: every contractual installment is charged since plan
        step recurrence:R16-c-2 (ruling R-R100), and the 04-10 assertion
        clears the three standing before it (R-R72 part (2)).  So the six
        level payments from 05-01 clear the $12,000.00 on 2026-10-01 -- and
        settling the three subsumed rows on 04-15 changes nothing: the balance
        on 04-16 and the payoff read the same before and after.  Until that
        step the check read the plan's own charge list, which began at 05-01;
        the developer approved the re-expressed check (rule 5).
        """
        freeze_today(monkeypatch, date(2026, 4, 15))
        account, _ctx = _loan(seed_user)
        _schedule_through(seed_user, 14)
        template = _definition(seed_user, account, _LEVEL, name="Payment")
        _generate(seed_user, template)
        insert_trueup_event(
            loan_params_for(db.session, account.id), _PRINCIPAL,
            date(2026, 4, 10),
        )
        db.session.commit()

        def read():
            ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)
            plan = loan_plan(account, ctx)
            # The seam's balance is HELD, negative when owed (ruling
            # R-CC47), so the read is what the loan owes, through owed().
            return (
                min(p.due_date for p in plan.payments),
                min(
                    c.on_date for c in _charges(account, ctx)
                    if c.on_date > date(2026, 4, 10)
                ),
                owed(balance_at.balance_at(account, ctx, _TOMORROW)),
                balance_at.loan_payoff_date(account, ctx),
            )

        projected = read()
        assert projected == (
            date(2026, 5, 1), date(2026, 5, 1), _PRINCIPAL, date(2026, 10, 1),
        )

        for occurrence in (date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)):
            row = (
                db.session.query(Transfer)
                .filter(
                    Transfer.transfer_template_id == template.id,
                    Transfer.occurs_on == occurrence,
                )
                .one()
            )
            transfer_service.update_transfer(
                row.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                side_days=on_both_sides(
                    row.from_account_id, row.to_account_id,
                    SettleDay(
                        day=date(2026, 4, 15), basis=SettledDayBasisEnum.ENTERED,
                    ),
                ),
            )
        db.session.commit()

        assert read() == projected

    def test_a_matured_loan_with_a_live_definition_is_walked_past_the_read(
        self, seed_user,
    ):
        """The definition walk's window reaches past today, not just past the contract.

        A 10-year loan from 2005 matured in 2015 and its post-contractual
        extension ran out in 2020, but it still owes $5,000.00 (asserted on
        the read day) and a $500.00 monthly definition pays it.  An
        adversarial review of this step found the walk's window ending at the
        extension, so the plan held no occurrence at all and the payoff read
        ``None`` where the definition clears the loan.  The window is now the
        later of the extension's end and the same span past the read.

        At 5%: 05-01 charges $20.83 and pays $479.17 of principal, and the
        eleventh payment, $117.49 + $0.49, clears it on 2027-03-01.  Every
        contractual installment from 2005 is charged since plan step
        recurrence:R16-c-2 (ruling R-R100) and the read-day assertion clears
        them (R-R72 part (2)); the first charge after it is 05-01 (the check
        read the plan's own charge list, which began there, until that step;
        the developer approved the re-expressed check, rule 5).
        """
        account = create_loan_account(
            seed_user, db.session, name="Matured Balloon",
            principal=Decimal("240000.00"), term=120,
            origination_date=date(2005, 1, 1), payment_day=1,
        )
        insert_trueup_event(
            loan_params_for(db.session, account.id), Decimal("5000.00"), _AS_OF,
        )
        sweep = _definition(
            seed_user, account, Decimal("500.00"), name="Sweep", bind=False,
        )
        sweep.recurrence_rule.starts_on = date(2026, 5, 1)
        db.session.commit()
        ctx = BalanceContext.build(seed_user["user"].id, _AS_OF)

        plan = loan_plan(account, ctx)

        estimates = _estimated(plan)
        assert [p.due_date for p in estimates][:2] == [
            date(2026, 5, 1), date(2026, 6, 1),
        ]
        assert estimates[-1].due_date >= date(2027, 3, 1)
        assert min(
            c.on_date for c in _charges(account, ctx) if c.on_date > _AS_OF
        ) == date(2026, 5, 1)
        assert balance_at.loan_payoff_date(account, ctx) == date(2027, 3, 1)
