"""Tests for plan step credit_card:CC-5-5d -- goals on a DEBT, and the tile's rule.

Rulings (``rulings_cc5_5.md`` Blocks 11-12, 19-21): a goal on a debt is a
milestone to get UNDER (R-CC69), its progress runs from what the debt owed when
the goal was set (R-CC70), a debt goal may target ``$0.00`` (R-CC72), it shows
only a projected date and its pace (R-CC73), a goal never moves onto, off or
between debts (R-CC87), every figure is what the debt's /savings TILE shows
(R-CC88), a debt goal carries no per-period contribution (R-CC90), and a card or
other non-loan debt goal RECORDS its start when created while a loan goal
re-reads its own from the books (R-CC91, refining R-CC71).  Ledger row
**CC-371**: the archived list read the DAY where a live non-loan tile reads its
pay period's END.

Every loan here is a 0% loan unless a case says otherwise, so each figure it
asserts is worked by hand: no interest accrues, so a payment is all principal.

New module rather than an append to ``test_savings_dashboard_service.py``:
concurrent lanes add to that file, and two end-of-file appends conflict.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app import ref_cache
from app.enums import GoalModeEnum, TxnTypeEnum
from app.extensions import db
from app.models.savings_goal import SavingsGoal
from app.services import (
    balance_at,
    savings_dashboard_service,
    savings_goal_door,
    savings_goal_service,
)
from app.services.balance_at import BalanceContext
from app.services.savings_dashboard_service._tile import (
    first_tile_day_owing_at_most,
    tile_balance_on,
)
from app.services.savings_dashboard_service._types import ArchivedDebt
from app.services.savings_goal_door import GoalProposal
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    create_transfer,
    one_off_row_of,
)


def _set_on(day):
    """Return a ``created_at`` instant whose display-timezone day is *day*.

    Noon UTC is 07:00-08:00 in New York, so the goal is "set" on *day* in the
    timezone its start is read in (``to_display_date``).
    """
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc)


def _goal(seed_user, account, target, set_day, name="Under it", **fields):
    """Insert a FIXED goal on *account* set on *set_day*, bypassing the door.

    The door is graded separately; these cases grade what a stored goal READS.
    A card goal passes the ``start_owed`` the door would have recorded.
    """
    goal = SavingsGoal(
        user_id=seed_user["user"].id,
        account_id=account.id,
        name=name,
        target_amount=target,
        goal_mode_id=ref_cache.goal_mode_id(GoalModeEnum.FIXED),
        created_at=_set_on(set_day),
        **fields,
    )
    db.session.add(goal)
    db.session.commit()
    return goal


def _visa_with_planned_rows(seed_user, seed_periods):
    """The ruling R-CC88 worked example: a Visa owing $1,000.00 today.

    A ``$200.00`` purchase is planned later in the current pay period
    (``seed_periods[3]``, 2026-02-13 .. 02-26, due 02-23; "today" is 02-15), so
    its tile shows ``$1,200.00`` owed; a ``$400.00`` payment is planned in the
    next period (``seed_periods[4]``, due 03-05), where its tile shows
    ``$800.00`` (1,000 + 200 - 400).

    Returns:
        ``(visa, today)``.
    """
    period, following = seed_periods[3], seed_periods[4]
    visa = create_account_of_type(
        seed_user, db.session, "Credit Card", "Visa",
        anchor_balance=Decimal("-1000.00"), observed_on=period.start_date,
    )
    db.session.flush()
    for row_period, name, amount, txn_type, due in (
        (period, "Planned purchase", Decimal("200.00"), TxnTypeEnum.EXPENSE,
         period.start_date + timedelta(days=10)),
        (following, "Planned payment", Decimal("400.00"), TxnTypeEnum.INCOME,
         following.start_date + timedelta(days=6)),
    ):
        one_off_row_of(
            row_period, name=name, amount=amount,
            user_id=row_period.user_id, account_id=visa.id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(txn_type),
            due_date=due,
        )
    db.session.commit()
    return visa, period.start_date + timedelta(days=2)


def _zero_rate_loan(seed_user, name="Car Loan"):
    """A configured 0% loan: $12,000 over 24 months from 2026-01-01.

    No interest accrues, so a payment is all principal and every balance is
    worked by hand; its contractual installment is $500.00 (12,000 / 24).
    """
    return create_loan_account(
        seed_user, db.session, name=name,
        principal=Decimal("12000.00"), rate=Decimal("0.00000"),
        term=24, origination_date=date(2026, 1, 1),
    )


def _owed(loan, ctx, day):
    """What the loan owes on *day*, from the loan domain's own producer."""
    return balance_at.positions(loan, ctx, [day])[day]


def _goal_datum(ctx, goal):
    """The goal's record from the full /savings build."""
    data = savings_dashboard_service.compute_dashboard_data(ctx)
    return next(gd for gd in data["goal_data"] if gd.goal.id == goal.id)


class TestTheTileRuleHasOneHome:
    """Ruling R-CC88 / ledger row CC-371: one rule for the tile, the list, the goal."""

    def test_a_card_tile_reads_the_period_end_and_a_loan_the_day(
        self, app, db, seed_user, seed_periods,
    ):
        """The live tile IS the rule: the card at its period's end, the loan on the day.

        The card's planned ``$200.00`` purchase is inside its tile's figure
        (``-1,200.00``) and outside the day's (``-1,000.00``); the 0% loan's
        tile is the seam's scalar on the day, ``-12,000.00`` with nothing paid.
        """
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            loan = _zero_rate_loan(seed_user)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)
            by_id = {
                ad.account.id: ad
                for ad in savings_dashboard_service.compute_dashboard_data(ctx)[
                    "account_data"
                ]
            }
            assert by_id[visa.id].current_balance == Decimal("-1200.00")
            assert balance_at.balance_at(visa, ctx, today) == Decimal("-1000.00")
            assert tile_balance_on(visa, ctx, today) == Decimal("-1200.00")
            assert by_id[loan.id].current_balance == Decimal("-12000.00")
            assert tile_balance_on(loan, ctx, today) == Decimal("-12000.00")

    def test_an_archived_card_shows_what_its_live_tile_showed(
        self, app, db, seed_user, seed_periods,
    ):
        """CC-371: archived, the Visa reads ``$1,200.00`` owed, its tile's figure.

        CC-5-5c read the day (``$1,000.00``), which is the tile's rule for a
        configured loan only.
        """
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            visa.is_active = False
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            rows = savings_dashboard_service.compute_dashboard_data(ctx)[
                "archived_accounts"
            ]

            row = next(r for r in rows if r.account.id == visa.id)
            assert isinstance(row, ArchivedDebt)
            assert row.owed == Decimal("1200.00")


class TestACardGoalKeepsTheStartItRecorded:
    """Rulings R-CC70, R-CC88, R-CC91: a card goal's start is what it recorded."""

    def test_the_card_worked_example_reads_zero_then_57_14(
        self, app, db, seed_user, seed_periods,
    ):
        """R-CC88's own numbers: start ``$1,200.00``, 0.00%, then ``$800.00``.

        Next period: (1,200 - 800) / (1,200 - 500) = 400 / 700 = 57.14%.
        """
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            goal = _goal(
                seed_user, visa, Decimal("500.00"), today,
                start_owed=Decimal("1200.00"),
            )

            set_day = _goal_datum(
                BalanceContext.build(seed_user["user"].id, as_of=today), goal,
            )
            assert set_day.is_debt
            assert set_day.start_owed == Decimal("1200.00")
            assert set_day.shown_balance == Decimal("1200.00")
            assert set_day.current_balance == Decimal("-1200.00")
            assert set_day.progress_pct == Decimal("0.00")
            assert set_day.monthly_contribution is None

            next_period = _goal_datum(
                BalanceContext.build(
                    seed_user["user"].id, as_of=seed_periods[4].start_date,
                ),
                goal,
            )
            assert next_period.start_owed == Decimal("1200.00")
            assert next_period.shown_balance == Decimal("800.00")
            assert next_period.progress_pct == Decimal("57.14")

    def test_a_payment_later_in_the_set_period_counts(
        self, app, db, seed_user, seed_periods,
    ):
        """The review's H1, ruled R-CC91: a $300 payment on day 5 reads 42.86%.

        Paid 02-20, inside the set day's period (02-13 .. 02-26): the tile at
        02-20 owes 1,000 + 200 (planned 02-23) - 300 = 900, so progress is
        (1,200 - 900) / (1,200 - 500) = 300 / 700 = 42.86%.  A start re-read
        from the period's end would have read 900 and 0.00%.
        """
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            goal = _goal(
                seed_user, visa, Decimal("500.00"), today,
                start_owed=Decimal("1200.00"),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[3], Decimal("300.00"),
                account=visa, is_income=True, name="Card payment",
                settled_on=date(2026, 2, 20),
            )
            db.session.commit()

            gd = _goal_datum(
                BalanceContext.build(
                    seed_user["user"].id, as_of=date(2026, 2, 20),
                ),
                goal,
            )
            assert gd.start_owed == Decimal("1200.00")
            assert gd.shown_balance == Decimal("900.00")
            assert gd.progress_pct == Decimal("42.86")

    def test_a_start_already_under_the_target_still_reads_met(
        self, app, db, seed_user, seed_periods,
    ):
        """Met is met even when the start leaves no span to divide.

        The Visa owes ``$1,200.00`` both at its recorded start and now, under a
        ``$1,300.00`` target stored directly: ``percent_complete`` answers ``0``
        for a non-positive span, so only the explicit met branch reads it
        complete.
        """
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            goal = _goal(
                seed_user, visa, Decimal("1300.00"), today,
                start_owed=Decimal("1200.00"),
            )

            gd = _goal_datum(
                BalanceContext.build(seed_user["user"].id, as_of=today), goal,
            )
            assert gd.progress_pct == Decimal("100.00")
            assert gd.trajectory.months_to_goal == 0

    def test_a_start_under_the_target_while_owing_more_reads_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Owing ``$1,500.00`` against a ``$1,200.00`` start and a ``$1,250.00`` target: 0%.

        Stored directly (a CREATE would refuse the target, the Visa owing
        $1,200 when set; an edit to it now would be admitted): the span
        1,200 - 1,250 is not positive, and the debt has not reached the
        target, so the read is 0 rather than a crash or a negative bar.
        Period 5 (from 03-13) folds the purchase and payment the seam carries
        forward plus a $700 purchase: 1,000 + 200 - 400 + 700 = 1,500.
        """
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            later_period = seed_periods[5]
            one_off_row_of(
                later_period, name="Big purchase", amount=Decimal("700.00"),
                user_id=later_period.user_id, account_id=visa.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                due_date=later_period.start_date + timedelta(days=3),
            )
            db.session.commit()
            goal = _goal(
                seed_user, visa, Decimal("1250.00"), today,
                start_owed=Decimal("1200.00"),
            )

            gd = _goal_datum(
                BalanceContext.build(
                    seed_user["user"].id, as_of=later_period.start_date,
                ),
                goal,
            )
            assert gd.shown_balance == Decimal("1500.00")
            assert gd.progress_pct == Decimal("0")


class TestALoanGoalReReadsItsStart:
    """Rulings R-CC70, R-CC71: a loan goal's start is re-read on its set day."""

    def test_a_loan_goal_runs_from_what_it_owed_the_day_it_was_set(
        self, app, db, seed_user, seed_periods,
    ):
        """12,000 owed on 02-15; two $1,000 payments; 10,000 on 03-20: 66.67%.

        (12,000 - 10,000) / (12,000 - 9,000) = 2,000 / 3,000 = 66.67%.  The
        missed contractual installments stay owed (the seam carries an unpaid
        projection forward to the reading day), so only the payments MADE
        count.  Today is the suite's frozen 2026-03-20.  Nothing is recorded:
        the start is re-read.
        """
        with app.app_context():
            loan = _zero_rate_loan(seed_user)
            set_day, later = date(2026, 2, 15), date(2026, 3, 20)
            goal = _goal(seed_user, loan, Decimal("9000.00"), set_day)
            for period, paid_on in (
                (seed_periods[4], date(2026, 3, 1)),
                (seed_periods[5], date(2026, 3, 15)),
            ):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], loan, period,
                    amount=Decimal("1000.00"), settled_on=paid_on,
                )
            db.session.commit()

            at_set = _goal_datum(
                BalanceContext.build(seed_user["user"].id, as_of=set_day), goal,
            )
            assert goal.start_owed is None
            assert at_set.start_owed == Decimal("12000.00")
            assert at_set.progress_pct == Decimal("0.00")

            at_later = _goal_datum(
                BalanceContext.build(seed_user["user"].id, as_of=later), goal,
            )
            assert at_later.start_owed == Decimal("12000.00")
            assert at_later.shown_balance == Decimal("10000.00")
            assert at_later.progress_pct == Decimal("66.67")
            assert at_later.required_contribution is None
            assert at_later.trajectory.required_monthly is None

    def test_a_debt_at_or_under_its_target_reads_met(
        self, app, db, seed_user, seed_periods,
    ):
        """Owing 10,000 against a 10,000 target is 100% and months-to-goal 0."""
        with app.app_context():
            loan = _zero_rate_loan(seed_user)
            goal = _goal(seed_user, loan, Decimal("10000.00"), date(2026, 2, 15))
            for period, paid_on in (
                (seed_periods[4], date(2026, 3, 1)),
                (seed_periods[5], date(2026, 3, 15)),
            ):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], loan, period,
                    amount=Decimal("1000.00"), settled_on=paid_on,
                )
            db.session.commit()
            later = date(2026, 3, 20)

            met = _goal_datum(
                BalanceContext.build(seed_user["user"].id, as_of=later), goal,
            )
            assert met.progress_pct == Decimal("100.00")
            assert met.trajectory.months_to_goal == 0
            assert met.trajectory.projected_completion_date == later

    def test_a_goal_on_an_archived_debt_reads_what_it_owes(
        self, app, db, seed_user, seed_periods,
    ):
        """An archived debt has no projection; its goal reads 12,000 owed, 0.00%.

        Not ``$0.00`` owed and "met".  Nothing is planned or paid, so its plan
        is its contract line (ruling plan#3982): the 02-01, 03-01 and 04-01
        installments, missed by the 04-15 read, count on 04-16 at $500 each
        (ruling plan#3983), so it owes 10,500 then, under the goal's 11,000 on
        the goal's projected date.  Until plan step recurrence:R16-f the plan
        billed no missed installment, and the date was 06-01 ($500 on 05-01
        and 06-01).
        """
        with app.app_context():
            loan = _zero_rate_loan(seed_user)
            goal = _goal(seed_user, loan, Decimal("11000.00"), date(2026, 3, 15))
            loan.is_active = False
            db.session.commit()
            today = date(2026, 4, 15)

            gd = _goal_datum(
                BalanceContext.build(seed_user["user"].id, as_of=today), goal,
            )
            assert gd.shown_balance == Decimal("12000.00")
            assert gd.start_owed == Decimal("12000.00")
            assert gd.progress_pct == Decimal("0.00")
            assert gd.trajectory.projected_completion_date == date(2026, 4, 16)


class TestTheProjectedDateIsTheTilesFirstDayUnder:
    """Ruling R-CC73 read by R-CC88's rule: when the tile first shows it under."""

    def test_a_zero_target_on_a_loan_is_its_payoff_installment(
        self, app, db, seed_user, seed_periods,
    ):
        """ONE walk: a ``$0.00`` goal's date is the payoff installment's day.

        A 5% loan read 2026-03-15 with its payoff far ahead: the installment is
        not overdue, so its visible day is its due day and the goal names the
        loan page's derived payoff date exactly.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            today = date(2026, 3, 15)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)
            goal = _goal(seed_user, loan, Decimal("0.00"), today)

            gd = _goal_datum(ctx, goal)

            payoff = balance_at.loan_payoff_date(loan, ctx)
            assert payoff is not None and payoff > today
            assert gd.trajectory.projected_completion_date == payoff

    def test_an_overdue_installment_crosses_on_the_day_the_tile_shows_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Review M2: the date is the VISIBLE day, never a past DUE day.

        A $1,000 payment is PLANNED for 03-01 and still unpaid on 03-20: the
        fold counts it from the day after the read (ruling D1), 03-21, so its
        tile first shows under 11,000 then -- the date the goal names -- while
        its due date is past.  The loan has no recurring payment, so its plan
        is its contract line beside the record (ruling plan#3982): the 02-01
        installment, missed and covered by no record, is billed the day after
        the read too, at its $500 (ruling plan#3983), and the planned $1,000
        covers 03-01's.  The seam's positions grade it: 12,000 on 03-20,
        10,500 on 03-21 (it read 11,000 until plan step recurrence:R16-f,
        when the plan billed no missed installment).
        """
        with app.app_context():
            loan = _zero_rate_loan(seed_user)
            create_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[4], amount=Decimal("1000.00"),
                due_date=date(2026, 3, 1),
            )
            db.session.commit()
            today = date(2026, 3, 20)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            crossing = first_tile_day_owing_at_most(
                loan, ctx, Decimal("11000.00"), is_loan=True, balances=None,
            )

            assert crossing == date(2026, 3, 21)
            owed = balance_at.positions(loan, ctx, [today, crossing])
            assert owed[today] == Decimal("12000.00")
            assert owed[crossing] == Decimal("10500.00")

    def test_a_card_crosses_on_the_first_day_of_the_period_that_gets_it_under(
        self, app, db, seed_user, seed_periods,
    ):
        """The Visa owes ``$800.00`` at the next period's end, so under ``$900.00``
        its tile first shows it on that period's first day; the seam's own
        period-end reads grade it."""
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)
            current, following = seed_periods[3], seed_periods[4]
            period_ends = {
                p.start_date: p.end_date
                for p in ctx.reported_periods()
                if p.start_date in (current.start_date, following.start_date)
            }

            crossing = first_tile_day_owing_at_most(
                visa, ctx, Decimal("900.00"), is_loan=False, balances=None,
            )

            assert crossing == following.start_date
            assert balance_at.balance_at(
                visa, ctx, period_ends[following.start_date],
            ) == Decimal("-800.00")
            assert balance_at.balance_at(
                visa, ctx, period_ends[current.start_date],
            ) == Decimal("-1200.00")

    def test_a_debt_nothing_planned_gets_under_has_no_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Under ``$100.00`` no planned row gets the Visa there: no date, 'behind'."""
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            goal = _goal(
                seed_user, visa, Decimal("100.00"), today,
                start_owed=Decimal("1200.00"), target_date=date(2026, 12, 31),
            )

            gd = _goal_datum(
                BalanceContext.build(seed_user["user"].id, as_of=today), goal,
            )
            assert gd.trajectory.projected_completion_date is None
            assert gd.trajectory.months_to_goal is None
            assert gd.trajectory.pace == "behind"


class TestTheGoalDoor:
    """The ONE refusal set both goal routes call, and the start it hands back."""

    def _proposal(self, account, target, **overrides):
        """A FIXED proposal on *account*."""
        fields = {
            "account_id": account.id,
            "goal_mode_id": ref_cache.goal_mode_id(GoalModeEnum.FIXED),
            "target_amount": target,
            "contribution_per_period": None,
        }
        fields.update(overrides)
        return GoalProposal(**fields)

    def test_a_card_target_is_judged_against_its_tile_and_its_start_recorded(
        self, app, db, seed_user, seed_periods,
    ):
        """Tile ``$1,200.00``: ``$1,200.00`` refused; ``$1,199.99`` admitted with start 1,200.

        The DAY's figure (``$1,000.00``) is neither the bound nor the start
        (rulings R-CC88, R-CC91).
        """
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            refused = savings_goal_door.judge_goal_save(
                ctx, self._proposal(visa, Decimal("1200.00")),
            )
            assert refused.refusal is not None
            assert "$1,200.00" in refused.refusal
            assert refused.start_owed is None
            admitted = savings_goal_door.judge_goal_save(
                ctx, self._proposal(visa, Decimal("1199.99")),
            )
            assert admitted.refusal is None
            assert admitted.start_owed == Decimal("1200.00")

    def test_a_loan_goal_records_no_start(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan goal re-reads its start (R-CC71), so the door records none."""
        with app.app_context():
            loan = _zero_rate_loan(seed_user)
            ctx = BalanceContext.build(
                seed_user["user"].id, as_of=date(2026, 3, 20),
            )

            verdict = savings_goal_door.judge_goal_save(
                ctx, self._proposal(loan, Decimal("11999.99")),
            )
            assert verdict.refusal is None
            assert verdict.start_owed is None

    def test_a_loan_type_without_terms_takes_no_goal(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling R-CC93: an Auto Loan with no terms is refused a goal; nothing recorded.

        Its terms would later make it a loan read by the day under a start
        recorded as a card's (the delta review's HIGH-1: 40.00% with no
        payment).
        """
        with app.app_context():
            auto = create_account_of_type(
                seed_user, db.session, "Auto Loan", "Auto",
                anchor_balance=Decimal("-20000.00"),
                observed_on=seed_periods[3].start_date,
            )
            db.session.commit()
            ctx = BalanceContext.build(
                seed_user["user"].id, as_of=seed_periods[3].start_date,
            )

            verdict = savings_goal_door.judge_goal_save(
                ctx, self._proposal(auto, Decimal("15000.00")),
            )
            assert verdict.refusal == (
                "Enter Auto's loan terms before setting a goal on it."
            )
            assert verdict.start_owed is None

    def test_zero_is_a_debt_target_and_not_a_savings_one(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling R-CC72: ``$0.00`` pays a debt off; a savings goal is refused it."""
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            assert savings_goal_door.judge_goal_save(
                ctx, self._proposal(visa, Decimal("0.00")),
            ).refusal is None
            assert savings_goal_door.judge_goal_save(
                ctx, self._proposal(seed_user["account"], Decimal("0.00")),
            ).refusal == "A savings goal's target must be above $0.00."
            assert savings_goal_door.judge_goal_save(
                ctx, self._proposal(seed_user["account"], None),
            ).refusal == "Target amount is required for fixed-amount goals."

    def test_a_debt_goal_is_fixed_and_carries_no_per_period_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """Income-relative is refused (R-CC69's premise); so is a contribution (R-CC90)."""
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            income_mode = savings_goal_door.judge_goal_save(
                ctx, self._proposal(
                    visa, None,
                    goal_mode_id=ref_cache.goal_mode_id(
                        GoalModeEnum.INCOME_RELATIVE,
                    ),
                ),
            ).refusal
            assert income_mode is not None and "fixed" in income_mode
            contribution = savings_goal_door.judge_goal_save(
                ctx, self._proposal(
                    visa, Decimal("500.00"),
                    contribution_per_period=Decimal("100.00"),
                ),
            ).refusal
            assert contribution is not None and "per-period" in contribution

    def test_a_goal_moves_only_between_savings_accounts(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling R-CC87, all four directions."""
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            amex = create_account_of_type(
                seed_user, db.session, "Credit Card", "Amex",
                anchor_balance=Decimal("-900.00"),
                observed_on=seed_periods[3].start_date,
            )
            second_savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
                anchor_balance=Decimal("100.00"),
                observed_on=seed_periods[3].start_date,
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)
            savings_goal = _goal(
                seed_user, seed_user["account"], Decimal("5000.00"), today,
                name="Save",
            )
            debt_goal = _goal(
                seed_user, visa, Decimal("500.00"), today,
                start_owed=Decimal("1200.00"),
            )

            for goal, onto in (
                (savings_goal, visa),
                (debt_goal, seed_user["account"]),
                (debt_goal, amex),
            ):
                assert savings_goal_door.judge_goal_save(
                    ctx, self._proposal(onto, goal.target_amount), goal,
                ).refusal == savings_goal_door.MOVE_REFUSED
            moved = savings_goal_door.judge_goal_save(
                ctx, self._proposal(second_savings, Decimal("5000.00")),
                savings_goal,
            )
            assert moved.refusal is None and moved.start_owed is None

    def test_an_unchanged_target_is_not_re_judged_against_today(
        self, app, db, seed_user, seed_periods,
    ):
        """A goal the debt has already passed can be renamed; a NEW target cannot sit above it."""
        with app.app_context():
            visa, today = _visa_with_planned_rows(seed_user, seed_periods)
            goal = _goal(
                seed_user, visa, Decimal("1500.00"), today,
                start_owed=Decimal("1600.00"),
            )
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            unchanged = savings_goal_door.judge_goal_save(
                ctx, self._proposal(visa, Decimal("1500.00")), goal,
            )
            assert unchanged.refusal is None and unchanged.start_owed is None
            assert savings_goal_door.judge_goal_save(
                ctx, self._proposal(visa, Decimal("1400.00")), goal,
            ).refusal is not None

    def test_an_edit_cannot_move_a_goal_onto_an_archived_account(
        self, app, db, seed_user, seed_periods,
    ):
        """The edit route checked ownership only; the create refused an archived account."""
        with app.app_context():
            archived = create_account_of_type(
                seed_user, db.session, "Savings", "Old Savings",
                anchor_balance=Decimal("100.00"),
                observed_on=seed_periods[3].start_date,
            )
            archived.is_active = False
            db.session.commit()
            today = seed_periods[3].start_date
            goal = _goal(
                seed_user, seed_user["account"], Decimal("5000.00"), today,
            )
            ctx = BalanceContext.build(seed_user["user"].id, as_of=today)

            assert savings_goal_door.judge_goal_save(
                ctx, self._proposal(archived, Decimal("5000.00")), goal,
            ).refusal == savings_goal_door.INVALID_ACCOUNT


class TestTheDebtTrajectory:
    """:func:`savings_goal_service.calculate_debt_trajectory`, the pure reading."""

    def test_zero_months_means_met_and_nothing_else(self):
        """A crossing later THIS month is one month away, never the met ``0``.

        :func:`app.utils.dates.months_between` counts month boundaries, so it
        answers ``0`` for a crossing inside the current month, and the cards
        read ``0`` as "Goal met!".
        """
        as_of = date(2026, 3, 20)
        this_month = savings_goal_service.calculate_debt_trajectory(
            owed_now=Decimal("1000.00"), target_amount=Decimal("500.00"),
            crossing_date=date(2026, 3, 27), target_date=None, as_of=as_of,
        )
        assert this_month.months_to_goal == 1
        assert this_month.projected_completion_date == date(2026, 3, 27)
        assert this_month.required_monthly is None

        met = savings_goal_service.calculate_debt_trajectory(
            owed_now=Decimal("500.00"), target_amount=Decimal("500.00"),
            crossing_date=None, target_date=date(2026, 6, 1), as_of=as_of,
        )
        assert met.months_to_goal == 0
        assert met.pace == "ahead"
        assert met.required_monthly is None

    def test_a_later_crossing_counts_months_and_reads_its_pace(self):
        """July against a June target date is behind; July, on track; August, ahead."""
        as_of = date(2026, 3, 20)
        for target_date, pace in (
            (date(2026, 6, 30), "behind"),
            (date(2026, 7, 31), "on_track"),
            (date(2026, 8, 31), "ahead"),
        ):
            trajectory = savings_goal_service.calculate_debt_trajectory(
                owed_now=Decimal("1000.00"), target_amount=Decimal("500.00"),
                crossing_date=date(2026, 7, 10), target_date=target_date,
                as_of=as_of,
            )
            assert trajectory.months_to_goal == 4
            assert trajectory.pace == pace
