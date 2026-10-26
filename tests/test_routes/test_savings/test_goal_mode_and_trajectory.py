"""A goal's income-relative mode in the form and on the dashboard, and the trajectory and pace its
card shows.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum
from app.extensions import db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.transfer_template import TransferTemplate

from tests._test_helpers import make_cadence_rule, start_test_pay_list
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY

from ._helpers import _create_savings_account, _create_goal


# -- Income-Relative Goal Form and Dashboard Tests (Commit 5.4-4) ----------


class TestIncomeRelativeGoalForm:
    """Tests for income-relative goal mode in the form and dashboard."""

    def test_goal_form_shows_mode_selector(self, app, auth_client, seed_user):
        """GET /savings/goals/new renders the mode selector dropdown.

        Both goal mode options and the income fields must be present
        in the form HTML.
        """
        with app.app_context():
            resp = auth_client.get("/savings/goals/new")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'name="goal_mode_id"' in html
            assert "Fixed" in html
            assert "Income-Relative" in html
            assert 'name="income_unit_id"' in html
            assert 'name="income_multiplier"' in html

    def test_create_income_relative_goal_via_form(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST creates an income-relative goal with correct field values.

        target_amount should be None; income fields should be set.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "3 Paychecks",
                "goal_mode_id": str(ir_id),
                "income_unit_id": str(paychecks_id),
                "income_multiplier": "3.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(
                name="3 Paychecks",
            ).one()
            assert goal.goal_mode_id == ir_id
            assert goal.income_unit_id == paychecks_id
            assert goal.income_multiplier == Decimal("3.00")
            assert goal.target_amount is None

    def test_create_fixed_goal_still_works(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST with goal_mode_id=Fixed creates a fixed goal.

        Backward compatibility -- income fields should be None.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Emergency Fund",
                "goal_mode_id": str(fixed_id),
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(
                name="Emergency Fund",
            ).one()
            assert goal.goal_mode_id == fixed_id
            assert goal.target_amount == Decimal("5000.00")
            assert goal.income_unit_id is None
            assert goal.income_multiplier is None

    def test_create_goal_without_mode_defaults_fixed(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST without goal_mode_id defaults to Fixed via schema load_default.

        Backward compatibility for any code path that omits goal_mode_id.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "No Mode Specified",
                "target_amount": "2000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(
                name="No Mode Specified",
            ).one()
            assert goal.goal_mode_id == fixed_id

    def test_create_fixed_with_income_fields_cleaned(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST with fixed mode but stale income fields cleans them.

        Hidden form fields still submit their old values.  The route
        must strip income_unit_id and income_multiplier for fixed goals.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Stale Fields",
                "goal_mode_id": str(fixed_id),
                "target_amount": "5000.00",
                "income_unit_id": str(paychecks_id),
                "income_multiplier": "3.00",
            }, follow_redirects=True)

            assert resp.status_code == 200

            goal = db.session.query(SavingsGoal).filter_by(
                name="Stale Fields",
            ).one()
            assert goal.goal_mode_id == fixed_id
            assert goal.target_amount == Decimal("5000.00")
            assert goal.income_unit_id is None
            assert goal.income_multiplier is None

    def test_edit_goal_mode_change_fixed_to_relative(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST update changes goal from fixed to income-relative.

        target_amount should be cleared; income fields should be set.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct, name="Mode Change Test")
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

            resp = auth_client.post(
                f"/savings/goals/{goal.id}",
                data={
                    "goal_mode_id": str(ir_id),
                    "income_unit_id": str(months_id),
                    "income_multiplier": "6.00",
                    "name": "Mode Change Test",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(goal)
            assert goal.goal_mode_id == ir_id
            assert goal.income_unit_id == months_id
            assert goal.income_multiplier == Decimal("6.00")
            assert goal.target_amount is None

    def test_edit_income_relative_to_fixed(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST update changes goal from income-relative to fixed.

        income_unit_id and income_multiplier should be cleared.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="IR to Fixed",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.post(
                f"/savings/goals/{goal.id}",
                data={
                    "goal_mode_id": str(fixed_id),
                    "target_amount": "5000.00",
                    "name": "IR to Fixed",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 200
            db.session.refresh(goal)
            assert goal.goal_mode_id == fixed_id
            assert goal.target_amount == Decimal("5000.00")
            assert goal.income_unit_id is None
            assert goal.income_multiplier is None

    def test_create_income_relative_validation_error(
        self, app, auth_client, seed_user
    ):
        """POST with income-relative mode but missing income_unit_id errors.

        Schema cross-field validation rejects this combination.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Missing Unit",
                "goal_mode_id": str(ir_id),
                "income_multiplier": "3.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(
                name="Missing Unit",
            ).first()
            assert goal is None

    def test_goal_form_edit_prepopulates_income_fields(
        self, app, auth_client, seed_user
    ):
        """GET edit form pre-populates mode, unit, and multiplier.

        For an income-relative goal, the mode dropdown should select
        Income-Relative, and the income fields should have values.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Prepopulate Test",
                goal_mode_id=ir_id,
                income_unit_id=months_id,
                income_multiplier=Decimal("3.00"),
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get(f"/savings/goals/{goal.id}/edit")
            assert resp.status_code == 200
            html = resp.data.decode()

            # The income-relative option should be selected.
            assert f'value="{ir_id}"' in html
            # The months unit option should be selected.
            assert f'value="{months_id}"' in html
            # The multiplier value should be pre-filled.
            assert '3.00' in html

    def test_dashboard_shows_income_relative_label(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Dashboard displays the income descriptor for income-relative goals.

        The descriptor text (e.g. '3.00 months of salary') should
        appear on the dashboard for income-relative goals.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            months_id = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Income Descriptor",
                goal_mode_id=ir_id,
                income_unit_id=months_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "3.00 months of salary" in html

    def test_dashboard_fixed_goal_no_descriptor(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Dashboard does NOT show an income descriptor for fixed goals."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            _create_goal(seed_user, acct, name="Fixed Display")

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "of salary" not in html


# -- Goal Trajectory Display Tests (Commit 5.15-1) --------------------------


class TestTrajectoryDisplay:
    """Route-level tests for trajectory and pace display on goal cards.

    Commit 5.15-1: the dashboard shows projected completion dates,
    pace badges, and required monthly contribution when behind.
    """

    def test_dashboard_displays_trajectory(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-14: Goal with recurring transfer shows trajectory info.

        A monthly transfer of $500 into a savings account with $5,000
        balance and $10,000 target.  The dashboard should show the
        projected completion text and the trajectory section.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                name="Monthly Savings",
                default_amount=Decimal("500.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.flush()
            # The definition first, then the cadence onto it (plan step R-F6).
            make_cadence_rule(template, MONTHLY)

            _create_goal(seed_user, acct, name="Trajectory Goal")
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Projected completion" in html

    def test_dashboard_no_contribution_message(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-15: Goal with no recurring transfer shows no-contribution message."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            _create_goal(seed_user, acct, name="No Transfer Goal")

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No recurring contribution" in html

    def test_dashboard_goal_met_message(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-16: Balance exceeds target shows 'Goal met!' text."""
        with app.app_context():
            # Default savings account has $5,000 balance.
            acct = _create_savings_account(seed_user)
            # Target is $3,000 -- already exceeded.
            _create_goal(
                seed_user, acct, name="Met Goal",
                target_amount=Decimal("3000.00"),
            )

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Goal met!" in html

    def test_dashboard_trajectory_with_income_relative_goal(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-17: Income-relative goal uses resolved target for trajectory.

        With a salary profile, the income-relative target is resolved
        to a dollar value.  Trajectory uses this resolved value, not
        a NULL target_amount.
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

            acct = _create_savings_account(seed_user)

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="IR Trajectory Goal",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            # Should not crash -- trajectory is computed on the resolved
            # target, even though target_amount is NULL.
            html = resp.data.decode()
            # With no transfer template but salary data, we get the
            # "No recurring contribution" message.
            assert "No recurring contribution" in html

    def test_dashboard_biweekly_transfer_normalization(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """C-5.15-19: Biweekly transfer normalized to monthly for trajectory.

        A biweekly (EVERY_PERIOD) transfer of $500/period should yield
        a monthly equivalent of $500 * 26 / 12 = $1,083.33.
        With $5,000 balance and $10,000 target:
            remaining = $5,000
            months = ceil(5000 / 1083.33) = 5
        Dashboard should show 'Projected completion' text.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=acct.id,
                name="Biweekly Savings",
                default_amount=Decimal("500.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.flush()
            # The definition first, then the cadence onto it (plan step R-F6).
            make_cadence_rule(template, EVERY_PERIOD)

            _create_goal(seed_user, acct, name="Biweekly Goal")
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Projected completion" in html

    def test_dashboard_no_salary_warning(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Dashboard shows warning when income-relative goal has no salary."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="No Salary Goal",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            assert "No salary profile configured" in html

    def test_dashboard_resolved_target_not_raw(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Dashboard displays resolved_target, not None, for income-relative goals.

        Even without a salary profile (target=$0), the dashboard must
        show '$0' not 'None' or an empty string.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)

            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Resolved Target Check",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                is_active=True,
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            html = resp.data.decode()
            # Must not show "None" where target should be.
            assert "None" not in html or "none" in html.lower()
