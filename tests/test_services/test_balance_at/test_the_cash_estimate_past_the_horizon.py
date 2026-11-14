"""The cash fold's estimated tier past the last saved paycheck (plan step C9, task plan#3941).

Past the owner's last saved paycheck no row exists, so until this tier every
account's balance froze on that day.  Each recurring definition's occurrences
on PROJECTED paychecks are priced as their rows would be and land where their
rows would (rulings plan#3930 and plan#3932), as far as each read asks (ruling
plan#4036).

**The schedule every case reads**, the ``seed_periods`` fixture: ten saved
biweekly paychecks from 2026-01-02, so the saved horizon is 2026-05-21.  The
projected paydays continue at the same cadence: 05-22, 06-05, 06-19, 07-03,
07-17 and 07-31 through :data:`_LAST_DAY`.  No row of any definition here is
written inside the saved schedule unless a case says so, so every balance up
to the horizon is the account's opening and any movement past it is the
estimate's.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.pay_schedule import PaySchedule
from app.services import pay_period_rolling
from app.services.balance_at import (
    BalanceContext,
    cash_balance_at,
    cash_daily_balance_series,
    cash_daily_facts_series,
    loan_closing_date,
)
from app.services.balance_at import _cash_estimate
from tests._test_helpers import (
    bare_expense_template,
    create_loan_account,
    create_savings_account,
    generate_transfer_of,
    make_cadence_rule,
    make_income_template,
    make_loan_payment_template,
    make_transfer_template,
    populate_in_a_fresh_pass,
    state_template_price,
)
from tests.oracles.recurrence_baseline import MONTHLY

# The read pass's as-of, inside the saved schedule.
_AS_OF = date(2026, 3, 1)

# The saved horizon: the last saved paycheck (payday 05-08) ends here.
_HORIZON = date(2026, 5, 21)

# The last day the cases read.
_LAST_DAY = date(2026, 7, 31)

# A read pass dated past two projected paydays (05-22 and 06-05): the state of
# an owner whose saved schedule has stopped growing (rolling generation off, or
# a schedule nobody has topped up).
_LATE_AS_OF = date(2026, 6, 10)


def _a_month_of_bills(seed_user):
    """Author the three definitions the checking cases read, and commit.

    A $2,000.00 paycheck every payday, $1,500.00 rent on the 1st of each
    month, and a $200.00 transfer to savings every payday.

    Args:
        seed_user: The seed owner.

    Returns:
        The savings account the transfer pays into.
    """
    savings = create_savings_account(
        seed_user, db.session, "Savings", Decimal("0.00"),
    )
    make_income_template(db.session, seed_user, amount="2000.00")
    rent = bare_expense_template(db.session, seed_user, name="Rent")
    state_template_price(rent, Decimal("1500.00"))
    make_cadence_rule(rent, MONTHLY, fires_on_day=1)
    make_transfer_template(db.session, seed_user, savings, amount="200.00")
    db.session.commit()
    return savings


def _daily(account, as_of, last_day):
    """Return *account*'s daily cash balance from the day after *as_of* through *last_day*."""
    ctx = BalanceContext.build(account.user_id, as_of)
    return cash_daily_balance_series(
        account, ctx, as_of + timedelta(days=1), last_day,
    )


def _moves(series):
    """Return ``{day: change from the day before}`` for every day the series moves on."""
    days = list(series)
    return {
        today: series[today] - series[yesterday]
        for yesterday, today in zip(days, days[1:])
        if series[today] != series[yesterday]
    }


class TestCheckingPastTheHorizon:
    """Checking moves by each projected paycheck, bill and transfer."""

    def test_each_projected_occurrence_moves_checking_on_its_rows_day(
        self, seed_user, seed_periods,
    ):
        """Past 2026-05-21 checking moves on exactly the days its rows would land.

        Hand count of the projected paychecks through 2026-07-31, each a
        $2,000.00 paycheck less the $200.00 transfer, landing on its payday:
        05-22, 06-05, 06-19, 07-03, 07-17, 07-31, each +$1,800.00.  Rent is due
        on the 1st and lands on its due date inside the paycheck holding it:
        06-01 (in 05-22 .. 06-04) and 07-01 (in 06-19 .. 07-02), each
        -$1,500.00.  The 05-01 rent sits in a SAVED paycheck and is not
        estimated (ruling plan#3932).  Net through 07-31:
        6 x $1,800.00 - 2 x $1,500.00 = $7,800.00.  The old fold read flat
        from the horizon on.
        """
        _a_month_of_bills(seed_user)
        checking = seed_user["account"]

        series = _daily(checking, _AS_OF, _LAST_DAY)

        assert _moves(series) == {
            date(2026, 5, 22): Decimal("1800.00"),
            date(2026, 6, 1): Decimal("-1500.00"),
            date(2026, 6, 5): Decimal("1800.00"),
            date(2026, 6, 19): Decimal("1800.00"),
            date(2026, 7, 1): Decimal("-1500.00"),
            date(2026, 7, 3): Decimal("1800.00"),
            date(2026, 7, 17): Decimal("1800.00"),
            date(2026, 7, 31): Decimal("1800.00"),
        }
        assert series[_LAST_DAY] - series[_HORIZON] == Decimal("7800.00")

    def test_a_scalar_read_runs_as_far_as_it_asks(self, seed_user, seed_periods):
        """One date past the horizon counts every occurrence through that day.

        The same three definitions read as one scalar at 2026-06-30, with no
        longer read on the pass first: 05-22, 06-05 and 06-19 at +$1,800.00
        and rent on 06-01 at -$1,500.00, so $3,900.00 above the horizon's
        balance (ruling plan#4036: no page states how far it reads).
        """
        _a_month_of_bills(seed_user)
        checking = seed_user["account"]
        horizon_ctx = BalanceContext.build(checking.user_id, _AS_OF)
        read_ctx = BalanceContext.build(checking.user_id, _AS_OF)

        at_horizon = cash_balance_at(checking, horizon_ctx, _HORIZON)
        at_june_end = cash_balance_at(checking, read_ctx, date(2026, 6, 30))

        assert at_june_end - at_horizon == Decimal("3900.00")


class TestAnEstimateCannotHaveAlreadyHappened:
    """Ruling R-G on an estimate: it lands no earlier than the day after the pass's as-of.

    Every other case here reads a pass dated inside the saved schedule, where
    that floor never binds.  It binds once the pass is dated past a projected
    payday.
    """

    def test_what_is_owed_by_the_as_of_lands_the_day_after(
        self, seed_user, seed_periods,
    ):
        """On a pass dated 2026-06-10, everything owed by then lands on 06-11.

        Owed by 06-10, each attributed to a day already gone: the 05-22 and
        06-05 paychecks less their transfers, +$1,800.00 each, and the 06-01
        rent, -$1,500.00.  They land together on 06-11:
        1,800.00 - 1,500.00 + 1,800.00 = +$2,100.00.  The later ones land on
        their own days, as on any pass: 06-19, 07-03, 07-17 and 07-31 at
        +$1,800.00, and 07-01 at -$1,500.00.  Net through 07-31:
        2,100.00 + 4 x 1,800.00 - 1,500.00 = $7,800.00, the figure the pass
        dated 03-01 reads, since what is owed does not depend on the day it
        is read.

        The day facts carry it as ``estimated``: zero on every day through the
        as-of, so a reader comparing past days against the bank's record
        ignores it as it ignores ``planned``, and each day's four components
        sum to that day's move.
        """
        _a_month_of_bills(seed_user)
        checking = seed_user["account"]
        ctx = BalanceContext.build(checking.user_id, _LATE_AS_OF)

        facts = cash_daily_facts_series(
            checking, ctx, _HORIZON, _LAST_DAY,
        ).facts

        balances = {day: fact.balance for day, fact in facts.items()}
        assert _moves(balances) == {
            date(2026, 6, 11): Decimal("2100.00"),
            date(2026, 6, 19): Decimal("1800.00"),
            date(2026, 7, 1): Decimal("-1500.00"),
            date(2026, 7, 3): Decimal("1800.00"),
            date(2026, 7, 17): Decimal("1800.00"),
            date(2026, 7, 31): Decimal("1800.00"),
        }
        assert balances[_LAST_DAY] - balances[_HORIZON] == Decimal("7800.00")
        through_the_as_of = [day for day in facts if day <= _LATE_AS_OF]
        assert {day: facts[day].estimated for day in through_the_as_of} == (
            dict.fromkeys(through_the_as_of, Decimal("0.00"))
        )
        assert facts[date(2026, 6, 11)].estimated == Decimal("2100.00")
        days = list(facts)
        for yesterday, today in zip(days, days[1:]):
            fact = facts[today]
            assert fact.balance - facts[yesterday].balance == (
                fact.recorded + fact.asserted + fact.planned + fact.estimated
            ), today

    def test_a_read_ending_on_the_as_of_walks_nothing(
        self, seed_user, seed_periods,
    ):
        """Checking at 2026-06-10, on the pass dated that day, reads its opening.

        The read ends past the last saved paycheck but not past the as-of,
        so nothing estimated can land by it, and the tier answers without
        walking any definition.  The opening is checking's balance at the
        horizon on a pass dated 03-01, flat through the saved schedule
        (:class:`TestNothingAtOrBeforeTheHorizonMoves`).
        """
        _a_month_of_bills(seed_user)
        checking = seed_user["account"]
        opening = cash_balance_at(
            checking, BalanceContext.build(checking.user_id, _AS_OF), _HORIZON,
        )
        ctx = BalanceContext.build(checking.user_id, _LATE_AS_OF)

        assert cash_balance_at(checking, ctx, _LATE_AS_OF) == opening
        assert not ctx.estimates


class TestNothingAtOrBeforeTheHorizonMoves:
    """Inside the saved schedule the saved rows are the plan."""

    def test_the_balance_is_flat_through_the_horizon_and_no_walk_runs_for_it(
        self, seed_user, seed_periods,
    ):
        """No definition has a row inside the saved schedule, and nothing is estimated there.

        Every paycheck from 2026-01-02 to 05-08 owes the paycheck, the rent
        and the transfer, and no row answers any of them; a walk over saved
        paychecks would land each at ``as_of + 1`` (ruling R-G).  The balance
        stays the opening from 03-02 through 05-21.  A read ending at the
        horizon walks no definition at all.
        """
        _a_month_of_bills(seed_user)
        checking = seed_user["account"]
        long_series = _daily(checking, _AS_OF, _LAST_DAY)
        ctx = BalanceContext.build(checking.user_id, _AS_OF)

        short_series = cash_daily_balance_series(
            checking, ctx, _AS_OF + timedelta(days=1), _HORIZON,
        )

        opening = long_series[_AS_OF + timedelta(days=1)]
        assert {
            day: balance for day, balance in long_series.items()
            if day <= _HORIZON
        } == dict.fromkeys(short_series, opening)
        assert set(short_series.values()) == {opening}
        assert not ctx.estimates


class TestBothLegsOfATransferAgree:
    """A transfer's two accounts read one walk of its definition."""

    def test_savings_gains_what_checking_sends_on_the_same_days(
        self, seed_user, seed_periods, monkeypatch,
    ):
        """The $200.00 transfer lands +$200.00 on savings each day it leaves checking.

        Only the transfer is authored here.  Projected paydays through
        2026-07-31: six, each -$200.00 on checking and +$200.00 on savings.
        One read pass reads both accounts and walks the definition once.
        """
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        template = make_transfer_template(
            db.session, seed_user, savings, amount="200.00",
        )
        db.session.commit()
        checking = seed_user["account"]
        ctx = BalanceContext.build(checking.user_id, _AS_OF)
        walks = []
        real_walk = _cash_estimate.unwritten_occurrences

        def counting_walk(definition, *args, **kwargs):
            """Record which definition is walked, then walk it."""
            walks.append(definition.id)
            return real_walk(definition, *args, **kwargs)

        monkeypatch.setattr(
            _cash_estimate, "unwritten_occurrences", counting_walk,
        )
        sent = cash_daily_balance_series(
            checking, ctx, _AS_OF + timedelta(days=1), _LAST_DAY,
        )
        received = cash_daily_balance_series(
            savings, ctx, _AS_OF + timedelta(days=1), _LAST_DAY,
        )

        paydays = [
            date(2026, 5, 22), date(2026, 6, 5), date(2026, 6, 19),
            date(2026, 7, 3), date(2026, 7, 17), date(2026, 7, 31),
        ]
        assert _moves(sent) == dict.fromkeys(paydays, Decimal("-200.00"))
        assert _moves(received) == dict.fromkeys(paydays, Decimal("200.00"))
        assert walks == [template.id]


class TestThePayerStopsAtTheLoansPayoff:
    """A loan payment's paying side follows the loan's own plan."""

    def test_checking_pays_the_loan_until_it_closes_and_not_after(
        self, seed_user, seed_periods,
    ):
        """A $3,000.00 loan at 0% paid $1,000.00 on the 1st closes on 2026-07-01.

        The loan originates 2026-04-01 for three months; the payment is a
        manual $1,000.00 on the 1st from 05-01.  The 05-01 payment's row is
        written (it sits in the saved paycheck 04-24 .. 05-07), so the
        projected ones are 06-01 and 07-01, which retire $3,000.00 - $1,000.00
        - $1,000.00 - $1,000.00 = $0.00 on 07-01.  The loan's plan holds
        occurrences after that (its walk runs past the payoff and routes them
        to excess); generation writes none of them, and checking pays none of
        them.  So checking moves -$1,000.00 on 06-01 and on 07-01 and never
        again through 2026-12-31.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Van Loan",
            principal=Decimal("3000.00"), rate=Decimal("0.00000"), term=3,
            origination_date=date(2026, 4, 1), payment_day=1,
        )
        template = make_loan_payment_template(
            db.session, seed_user, loan, amount="1000.00",
            derive_from_loan=False,
        )
        generate_transfer_of(template, seed_periods[8])
        db.session.commit()
        as_of = date(2026, 4, 15)
        checking = seed_user["account"]
        ctx = BalanceContext.build(checking.user_id, as_of)

        series = cash_daily_balance_series(
            checking, ctx, as_of + timedelta(days=1), date(2026, 12, 31),
        )

        assert loan_closing_date(loan, ctx) == date(2026, 7, 1)
        assert _moves(series) == {
            date(2026, 5, 1): Decimal("-1000.00"),
            date(2026, 6, 1): Decimal("-1000.00"),
            date(2026, 7, 1): Decimal("-1000.00"),
        }

    def test_a_payment_unwritten_inside_the_saved_schedule_is_the_loans_alone(
        self, seed_user, seed_periods,
    ):
        """With no row for the 05-01 payment, the loan counts it and checking does not pay it.

        The loan and payment above, but the 05-01 payment's row is never
        written.  It sits in the saved paycheck 04-24 .. 05-07, where the
        saved rows are checking's plan (ruling plan#3932), so the estimate
        debits only the occurrences on PROJECTED paychecks: -$1,000.00 on
        06-01 and on 07-01, and nothing on 05-01.  The loan's own plan
        prices the unwritten occurrence (ruling R-R64), so its payoff is
        still 07-01: $3,000.00 - 3 x $1,000.00 = $0.00.  Debiting checking
        for the loan's whole plan would move it -$1,000.00 on 05-01 as well.
        """
        loan = create_loan_account(
            seed_user, db.session, name="Van Loan",
            principal=Decimal("3000.00"), rate=Decimal("0.00000"), term=3,
            origination_date=date(2026, 4, 1), payment_day=1,
        )
        make_loan_payment_template(
            db.session, seed_user, loan, amount="1000.00",
            derive_from_loan=False,
        )
        db.session.commit()
        as_of = date(2026, 4, 15)
        checking = seed_user["account"]
        ctx = BalanceContext.build(checking.user_id, as_of)

        series = cash_daily_balance_series(
            checking, ctx, as_of + timedelta(days=1), date(2026, 12, 31),
        )

        assert loan_closing_date(loan, ctx) == date(2026, 7, 1)
        assert _moves(series) == {
            date(2026, 6, 1): Decimal("-1000.00"),
            date(2026, 7, 1): Decimal("-1000.00"),
        }


class TestARollingTopUpMovesNoBalance:
    """Saving a projected paycheck and populating it changes no balance on any date."""

    def test_top_up_then_populate_leaves_every_daily_balance_unchanged(
        self, seed_user, seed_periods,
    ):
        """Three projected paychecks become saved rows, and checking and savings read the same.

        Before: 05-22, 06-05 and 06-19 are projected, and their paycheck,
        rent (06-01 and 07-01) and transfer are estimated.  The top-up saves
        those three paychecks (the window target is three past the six
        current-and-future paychecks at 2026-03-01) and population writes
        their rows: 3 paychecks + 3 transfers + 2 rents = 8.  After, those
        rows are the PLANNED tier and the estimate starts at 07-03.  Every
        daily balance from 03-02 to 07-31 is unchanged on both accounts.
        """
        savings = _a_month_of_bills(seed_user)
        checking = seed_user["account"]
        before = (
            _daily(checking, _AS_OF, _LAST_DAY),
            _daily(savings, _AS_OF, _LAST_DAY),
        )
        schedule = db.session.query(PaySchedule).filter_by(
            user_id=checking.user_id,
        ).one()
        schedule.rolling_enabled = True
        schedule.rolling_target_periods = 9
        db.session.commit()

        appended = pay_period_rolling.top_up_rolling_window(
            checking.user_id, as_of=_AS_OF,
        )
        created = populate_in_a_fresh_pass(
            checking.user_id, [period.id for period in appended],
        )
        db.session.commit()

        assert [period.start_date for period in appended] == [
            date(2026, 5, 22), date(2026, 6, 5), date(2026, 6, 19),
        ]
        assert created == 8
        after = (
            _daily(checking, _AS_OF, _LAST_DAY),
            _daily(savings, _AS_OF, _LAST_DAY),
        )
        assert after == before
