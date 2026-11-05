"""Savings dashboard service tests: goal progress, income-relative goals, goal trajectories, and a
render walking a goal transfer once.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from decimal import ROUND_HALF_UP, Decimal

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.services import savings_dashboard_service
from app.services import account_service
from app.services.balance_at import BalanceContext

from tests._test_helpers import start_test_pay_list, state_template_price
from tests.oracles.recurrence_baseline import MONTHLY


class TestGoalProgress:
    """Tests for savings goal progress computation."""

    def test_goal_progress_with_target(
        self, app, db, seed_user, seed_periods
    ):
        """Goal with balance at 50% of target shows 50% progress."""
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Goal Account",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Vacation",
                target_amount=Decimal("10000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert len(result["goal_data"]) == 1
            gd = result["goal_data"][0]
            # 5000 / 10000 * 100 = 50.00 via money.percent_complete (Decimal).
            assert gd.progress_pct == Decimal("50.00")
            assert gd.current_balance == Decimal("5000.00")

    def test_progress_pct_rounds_half_up_fractional_percent(
        self, app, db, seed_user, seed_periods
    ):
        """progress_pct rounds a fractional percent HALF_UP via percent_complete.

        $4,980 / $5,000 = 99.6%.  deep-quality-hunt #20/#78 routed this
        savings card through the canonical ``money.percent_complete``
        (ROUND_HALF_UP, clamped [0, 100], Decimal), retiring the prior
        ``min(100, int(...))`` truncation that disagreed with the
        budget-dashboard savings-goal card for the same goal.  So the value
        is now ``Decimal("99.60")`` (not the old truncated ``99``); the
        template renders it ``"{:.0f}".format(...)`` -> "100%", matching the
        budget dashboard's savings-goal track label (``_tracks.html``;
        the pre-rebuild ``_savings_goals.html`` it replaced).  Revert-proof:
        the old ``int(99.6) == 99`` fails this ``99.60`` assertion.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Truncation Account",
                    anchor_balance=Decimal("4980.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Almost There",
                target_amount=Decimal("5000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            assert gd.current_balance == Decimal("4980.00")
            # 4980 / 5000 * 100 = 99.60, ROUND_HALF_UP via percent_complete
            # (NOT the old int()-truncated 99).
            assert gd.progress_pct == Decimal("99.60")

    def test_progress_pct_clamps_over_funded_to_100(
        self, app, db, seed_user, seed_periods
    ):
        """progress_pct clamps an over-funded goal to 100 (upper bound).

        $6,000 / $5,000 = 120%, clamped to ``Decimal("100.00")`` by
        ``money.percent_complete`` (deep-quality-hunt #20/#78).  The
        companion lower clamp on a negative balance is covered by
        ``test_progress_pct_clamps_negative_balance_to_zero``.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Over-funded Account",
                    anchor_balance=Decimal("6000.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Exceeded",
                target_amount=Decimal("5000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            assert gd.current_balance == Decimal("6000.00")
            # 6000 / 5000 * 100 = 120, clamped to 100.00 by percent_complete.
            assert gd.progress_pct == Decimal("100.00")

    def test_progress_pct_clamps_negative_balance_to_zero(
        self, app, db, seed_user, seed_periods
    ):
        """progress_pct floors a negative-balance goal at 0% (lower bound).

        A goal backed by an overdrawn account (negative projected balance)
        previously produced a NEGATIVE progress_pct -- the prior
        ``min(100, int(...))`` rule had no lower clamp, so an overdrawn
        -$500 against a $5,000 target rendered a -10%-width / "-10%"-label
        bar (deep-quality-hunt #20).  Routing through ``percent_complete``
        floors the ratio at ``Decimal("0")``.  Revert-proof: the old rule
        yields ``min(100, int(-10)) == -10``, failing this 0 assertion.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Overdrawn Account",
                    anchor_balance=Decimal("-500.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Underwater",
                target_amount=Decimal("5000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            assert gd.current_balance == Decimal("-500.00")
            # -500 / 5000 * 100 = -10%, floored to 0.00 by percent_complete.
            assert gd.progress_pct == Decimal("0")

    def test_no_goals_returns_empty_list(
        self, app, db, seed_user, seed_periods
    ):
        """User with no active goals gets an empty goal_data list."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert result["goal_data"] == []


class TestIncomeRelativeGoalDashboard:
    """Integration tests for income-relative goal resolution in the dashboard."""

    def test_dashboard_fixed_goal_includes_resolved_target(
        self, app, db, seed_user, seed_periods
    ):
        """Fixed goal's resolved_target equals its stored target_amount.

        Verifies that fixed goals pass through unmodified by the
        resolution logic.
        """
        with app.app_context():
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Fixed Goal",
                target_amount=Decimal("5000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            assert gd.resolved_target == Decimal("5000.00")
            assert gd.income_descriptor is None

    def test_dashboard_goal_data_includes_new_keys(
        self, app, db, seed_user, seed_periods
    ):
        """The goal record's field set is exactly what its consumers read.

        One of the dict's eleven keys did not become a field: ``goal_mode_id``,
        a straight copy of ``goal.goal_mode_id`` on a record that already
        carries the goal.  (This said "a twelfth key" until plan step X-w6
        recounted it -- eleven keys in, ten fields out.)  An AST
        census over ``app/`` and ``tests/`` found the copy had ZERO readers, so
        plan step X-w4 dropped it when the dict became a
        :class:`~...._goals.GoalProgress` -- finding N-100's
        published-key-with-no-consumer, in the container being typed.  This
        pins the whole set, and its ABSENCE with it, so a future step that
        re-adds a field without a reader fails here.
        """
        with app.app_context():
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Key Check",
                target_amount=Decimal("3000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            # pylint: disable=import-outside-toplevel
            from dataclasses import fields
            # ``start_owed`` since plan step credit_card:CC-5-5d (a debt goal's
            # start, read by ``savings/_debt_goal.html``), with the developer's
            # rule-5 confirmation (2026-09-23).
            assert {f.name for f in fields(gd)} == {
                "goal", "current_balance", "progress_pct", "remaining_periods",
                "required_contribution", "resolved_target",
                "income_descriptor", "has_salary_data", "trajectory",
                "monthly_contribution", "start_owed",
            }
            assert not hasattr(gd, "goal_mode_id"), (
                "the goal's mode is read through ``gd.goal``; a copy of it here "
                "had no reader anywhere in app/ or tests/"
            )

    def test_dashboard_income_relative_goal_resolves_target(
        self, app, db, seed_user, seed_periods
    ):
        """Income-relative goal on the dashboard shows calculated target.

        With a salary profile configured, an income-relative goal
        resolves its target from net biweekly pay * multiplier.
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Test Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("2884.62"))  # $75,000.00 a year / 26

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="3 Paychecks",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            # The exact value depends on the salary profile's net pay.
            # With a salary profile, resolved_target should be > 0.
            assert gd.resolved_target > Decimal("0.00")
            assert gd.has_salary_data is True
            assert isinstance(gd.resolved_target, Decimal)

    def test_dashboard_income_relative_no_salary(
        self, app, db, seed_user, seed_periods
    ):
        """Income-relative goal with no salary profile shows $0.00 target.

        Without a salary profile, net_biweekly_pay is $0 and the
        resolved target is $0.  has_salary_data should be False.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="No Salary Goal",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            assert gd.resolved_target == Decimal("0.00")
            assert gd.has_salary_data is False
            assert gd.progress_pct == 0

    def test_dashboard_income_descriptor_format(
        self, app, db, seed_user, seed_periods
    ):
        """Income descriptor uses the unit name and multiplier.

        For a 3-month income-relative goal, income_descriptor should
        be '3.00 months of salary'.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="3 Months Buffer",
                goal_mode_id=ir_id,
                income_unit_id=months_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            assert gd.income_descriptor == "3.00 months of salary"

    def test_progress_uses_resolved_target(
        self, app, db, seed_user, seed_periods
    ):
        """Progress percentage uses the resolved target, not raw target_amount.

        An income-relative goal with target_amount=None must still
        produce a valid progress percentage (not 0% or a crash).
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="Test Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("2884.62"))  # $75,000.00 a year / 26

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            # The seed user's checking account has $1,000 balance.
            # Create a goal for 1 paycheck of savings.
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="1 Paycheck",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("1.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            # resolved_target = 1 * net_biweekly_pay > 0 (salary exists).
            # progress_pct = 1000 / resolved_target * 100.
            # The exact percentage depends on the salary amount,
            # but it must be > 0 (balance is $1000 and target is > 0).
            assert gd.progress_pct > 0
            assert gd.resolved_target > Decimal("0.00")

    def test_progress_zero_target_no_division_error(
        self, app, db, seed_user, seed_periods
    ):
        """Income-relative goal with $0 resolved target yields 0% progress.

        When there is no salary profile, the resolved target is $0.
        This must not cause a ZeroDivisionError.
        """
        with app.app_context():
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="Zero Target",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            assert gd.progress_pct == 0
            assert gd.required_contribution is None


class TestGoalTrajectoryDashboard:
    """Integration tests for trajectory calculation in the dashboard.

    Verifies that the dashboard service correctly discovers monthly
    contributions from transfer templates and includes trajectory
    data in goal_data dicts.
    """

    def test_goal_row_carries_a_whole_trajectory(
        self, app, db, seed_user, seed_periods
    ):
        """The goal record carries a GoalTrajectory, and it is never absent.

        The non-null arm is the point (plan step X-aa, ruling R-CO).
        ``calculate_trajectory`` has three returns and every one fills all four
        fields, so ``GoalProgress.trajectory`` stopped being ``dict | None`` and
        the goal card's ``{% if gd.trajectory %}`` guard -- a truthiness test on
        an always-four-key value -- went with it.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Trajectory Account",
                    anchor_balance=Decimal("3000.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Trajectory Goal",
                target_amount=Decimal("6000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            # pylint: disable=import-outside-toplevel
            from app.services.savings_goal_service import GoalTrajectory
            assert isinstance(gd.trajectory, GoalTrajectory)
            assert isinstance(gd.monthly_contribution, Decimal)

    def test_trajectory_with_no_transfer_template(
        self, app, db, seed_user, seed_periods
    ):
        """Goal with no recurring transfer shows zero monthly and None trajectory.

        Without a transfer template targeting the account,
        monthly_contribution is $0 and months_to_goal is None.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="No Transfer Account",
                    anchor_balance=Decimal("2000.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="No Transfer Goal",
                target_amount=Decimal("10000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            assert gd.monthly_contribution == Decimal("0.00")
            assert gd.trajectory.months_to_goal is None

    def test_trajectory_with_transfer_template(
        self, app, db, seed_user, seed_periods
    ):
        """Goal with a recurring monthly transfer computes trajectory.

        A $500/month recurring transfer into the savings account with
        $3,000 balance and $6,000 target should produce months_to_goal=6.
        """
        from tests._test_helpers import make_cadence_rule

        with app.app_context():
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="With Transfer Account",
                    anchor_balance=Decimal("3000.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            from app.models.transfer_template import TransferTemplate
            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Monthly Savings",
                is_active=True,
            )
            db.session.add(template)
            db.session.flush()
            # Its price through the one write door: the goal's contribution
            # reads the series since plan step X-bp.
            state_template_price(template, Decimal("500.00"))
            # Authored through the write door (plan step R7c-b): the
            # two-axis columns are NOT NULL, so a rule naming only a pattern
            # cannot be stored.  Onto the definition, which plan step R-F6
            # made the order: the rule carries its owner's FK.
            make_cadence_rule(template, MONTHLY)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Transfer Goal",
                target_amount=Decimal("6000.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            gd = result["goal_data"][0]
            # Monthly transfer of $500 with $3,000 remaining
            assert gd.monthly_contribution == Decimal("500.00")
            # remaining = 6000 - 3000 = 3000, months = ceil(3000/500) = 6
            assert gd.trajectory.months_to_goal == 6


class TestARenderWalksAGoalTransferOnce:
    """Plan ledger row N-513, measured on its own surface (plan step R7d-f-2).

    A transfer from checking into a goal account is in the emergency-fund
    floor's set (``_metrics._committed_expense_floor``, by ``from_account_id``)
    AND in that goal's contribution set (``_goals._load_goal_templates``, by
    ``to_account_id``), so one render reads it through the composed door
    twice.  Measured 2026-09-12 before this step: ``resolve`` once (R16-b-2's
    memo), the occurrence WALK twice.  The walk is the pass's memo now, so the
    second read is memo hits end to end and one render walks the definition
    ONCE -- ``CLAUDE.md`` rule 14's ONE WALK, read literally.
    """

    def test_one_checking_to_goal_transfer_is_walked_once_per_render(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """Both sets read the definition; the pass walks it once.

        Both consumers are shown to FIRE before the count is read: the goal's
        monthly contribution is the transfer's own $200 x 26 / 12, and the
        emergency-fund operand carries the same committed floor (no settled
        expense exists on this fixture, so the floor IS the average).  A
        count of one with either figure absent would be one consumer skipping
        the read rather than the memo serving it.
        """
        # Pylint: ``import-outside-toplevel`` -- the walk-once control patches
        # the name the PASS calls at call time (``_recurrence_memos``, the
        # module of the context's mixin since ruling R-BAL146), a seam-private
        # module this file otherwise has no business importing; kept local
        # so the import states its one purpose beside its one use.
        # pylint: disable=import-outside-toplevel
        from app.services.balance_at import _recurrence_memos
        from tests._test_helpers import make_transfer_template

        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Goal Account",
                    anchor_balance=Decimal("5000.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=savings.id,
                name="Vacation",
                target_amount=Decimal("10000.00"),
                is_active=True,
            )
            db.session.add(goal)
            # $200 every paycheck, checking -> the goal account: the shape
            # both sets select.
            make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            calls = []
            real = _recurrence_memos.occurrence_walk

            def counting(resolved, calendar, **kwargs):
                calls.append(resolved)
                return real(resolved, calendar, **kwargs)

            monkeypatch.setattr(_recurrence_memos, "occurrence_walk", counting)

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )

            # 200 * 26 / 12 at full precision, rounded once at the boundary.
            biweekly_as_monthly = (
                Decimal("200.00") * 26 / 12
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            assert result["goal_data"][0].monthly_contribution == (
                biweekly_as_monthly
            ), "precondition: the goal's set read the transfer"
            assert result["avg_monthly_expenses"] == biweekly_as_monthly, (
                "precondition: the floor's set read the same transfer"
            )
            assert len(calls) == 1, (
                f"one checking-to-goal transfer was walked {len(calls)} times "
                f"in one render; the walk is the pass's memo (row N-513)"
            )
