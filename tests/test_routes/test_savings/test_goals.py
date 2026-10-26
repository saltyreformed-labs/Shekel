"""Savings goals through their doors: create, update, delete, the unique constraint, the negative
paths, and the Section 5 regression baseline.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.services import account_service
from app.services.auth_service import hash_password

from ._helpers import _create_savings_account, _create_goal


def _create_other_user_with_goal():
    """Create a second user with a savings account and goal.

    Returns:
        dict with keys: user, account, goal.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()


    # The account_service factory requires the user to have at least one pay
    # period to anchor against.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    from datetime import date as _date
    from tests._test_helpers import open_owner_calendar as _open_calendar
    _bootstrap = _open_calendar(other_user.id, _date(2024, 1, 5))[0]
    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=savings_type.id,
            name="Other Savings",
            anchor_balance=Decimal("2000.00"),
        ),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=other_user.id, name="Baseline", is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    goal = SavingsGoal(
        user_id=other_user.id,
        account_id=account.id,
        name="Other Goal",
        target_amount=Decimal("5000.00"),
        target_date=date(2027, 1, 1),
    )
    db.session.add(goal)
    db.session.commit()

    return {"user": other_user, "account": account, "goal": goal}


# ── Goal Create Tests ────────────────────────────────────────────────


class TestGoalCreate:
    """Tests for GET /savings/goals/new and POST /savings/goals."""

    def test_new_goal_form(self, app, auth_client, seed_user):
        """GET /savings/goals/new renders the goal creation form."""
        with app.app_context():
            resp = auth_client.get("/savings/goals/new")
            assert resp.status_code == 200
            assert b'name="target_amount"' in resp.data
            assert b'name="target_date"' in resp.data
            assert b"New Savings Goal" in resp.data

    def test_create_goal_success(self, app, auth_client, seed_user, seed_periods):
        """POST /savings/goals creates a goal and redirects to dashboard."""
        with app.app_context():
            acct = _create_savings_account(seed_user)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "New Car",
                "target_amount": "15000.00",
                "target_date": "2027-12-31",
                "contribution_per_period": "250.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"New Car" in resp.data
            assert b"created" in resp.data

            # Verify in database.
            goal = db.session.query(SavingsGoal).filter_by(name="New Car").one()
            assert goal.target_amount == Decimal("15000.00")
            assert goal.account_id == acct.id

    def test_create_goal_validation_error(self, app, auth_client, seed_user):
        """POST /savings/goals with missing required fields shows error."""
        with app.app_context():
            resp = auth_client.post("/savings/goals", data={
                # Missing name, target_amount, account_id.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_create_goal_invalid_account(self, app, auth_client, seed_user):
        """POST /savings/goals with another user's account is rejected."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.post("/savings/goals", data={
                "account_id": other["account"].id,
                "name": "Sneaky Goal",
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account" in resp.data

            # Verify goal was NOT created.
            goal = db.session.query(SavingsGoal).filter_by(name="Sneaky Goal").first()
            assert goal is None

    def test_create_goal_without_optional_fields(self, app, auth_client, seed_user):
        """POST /savings/goals succeeds without target_date and contribution."""
        with app.app_context():
            acct = _create_savings_account(seed_user)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Rainy Day",
                "target_amount": "1000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"created" in resp.data

            goal = db.session.query(SavingsGoal).filter_by(name="Rainy Day").one()
            assert goal.target_date is None
            assert goal.contribution_per_period is None


# ── Goal Update Tests ────────────────────────────────────────────────


class TestGoalUpdate:
    """Tests for GET /savings/goals/<id>/edit and POST /savings/goals/<id>."""

    def test_edit_goal_form(self, app, auth_client, seed_user):
        """GET /savings/goals/<id>/edit renders the edit form."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)

            resp = auth_client.get(f"/savings/goals/{goal.id}/edit")
            assert resp.status_code == 200
            assert b"Vacation Fund" in resp.data

    def test_update_goal_success(self, app, auth_client, seed_user, seed_periods):
        """POST /savings/goals/<id> updates goal fields."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "name": "Updated Fund",
                "target_amount": "20000.00",
                "target_date": "2028-01-01",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"updated" in resp.data

            db.session.refresh(goal)
            assert goal.name == "Updated Fund"
            assert goal.target_amount == Decimal("20000.00")

    def test_update_goal_clears_target_date(
        self, app, auth_client, seed_user, seed_periods
    ):
        """An emptied target_date input clears the stored date.

        The nullable-field clear rule: ``target_date`` is allow_none
        on the update schema, so the empty submit loads as an explicit
        None (it used to be DROPPED, making the date unclearable from
        the UI) and the route's setattr loop nulls the column -- the
        goal reverts to no-deadline pacing.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)
            assert goal.target_date == date(2027, 6, 1)

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "target_date": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.refresh(goal)
            assert goal.target_date is None

    def test_update_goal_validation_error(self, app, auth_client, seed_user):
        """POST /savings/goals/<id> with invalid data shows error."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "target_amount": "-100.00",  # Negative -- fails Range validator.
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

    def test_edit_goal_idor(self, app, auth_client, seed_user):
        """GET /savings/goals/<id>/edit for another user's goal returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.get(
                f"/savings/goals/{other['goal'].id}/edit",
                follow_redirects=True,
            )
            assert resp.status_code == 404

    def test_update_goal_idor(self, app, auth_client, seed_user):
        """POST /savings/goals/<id> for another user's goal returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.post(
                f"/savings/goals/{other['goal'].id}",
                data={"name": "Hijacked"},
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify original goal unchanged.
            db.session.refresh(other["goal"])
            assert other["goal"].name == "Other Goal"


# ── Goal Delete Tests ────────────────────────────────────────────────


class TestGoalDelete:
    """Tests for POST /savings/goals/<id>/delete."""

    def test_delete_goal_success(self, app, auth_client, seed_user, seed_periods):
        """POST /savings/goals/<id>/delete soft-deactivates the goal."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)

            resp = auth_client.post(
                f"/savings/goals/{goal.id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"deactivated" in resp.data

            db.session.refresh(goal)
            assert goal.is_active is False

    def test_delete_goal_idor(self, app, auth_client, seed_user):
        """POST /savings/goals/<id>/delete for another user's goal returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_with_goal()

            resp = auth_client.post(
                f"/savings/goals/{other['goal'].id}/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404

            # Verify goal still active.
            db.session.refresh(other["goal"])
            assert other["goal"].is_active is True

    def test_delete_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999/delete for missing goal returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals/999999/delete",
                follow_redirects=True,
            )
            assert resp.status_code == 404


# ── Double Submit / Unique Constraint ────────────────────────────────


class TestGoalIdempotency:
    """Tests for unique constraint on savings goals."""

    def test_duplicate_goal_name_same_account(self, app, auth_client, seed_user, seed_periods):
        """POST /savings/goals twice with the same name+account returns a
        flash warning on the second attempt, and creating the same name
        on a different account still succeeds."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            form_data = {
                "account_id": str(acct.id),
                "name": "Emergency Fund",
                "target_amount": "5000.00",
            }

            # -- First submission: succeeds --
            resp1 = auth_client.post("/savings/goals", data=form_data)
            assert resp1.status_code == 302, (
                f"First submit returned {resp1.status_code}, expected 302"
            )

            goal = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Emergency Fund",
            ).one()
            assert goal.target_amount == Decimal("5000.00")
            original_goal_id = goal.id

            # -- Second submission: duplicate, handled gracefully --
            resp2 = auth_client.post("/savings/goals", data=form_data)
            assert resp2.status_code == 302, (
                f"Duplicate submit returned {resp2.status_code}, expected 302"
            )

            location = resp2.headers.get("Location", "")
            assert "/savings" in location, (
                f"Redirect went to {location}, expected /savings"
            )

            # Follow redirect and verify flash warning.
            resp3 = auth_client.get(location)
            assert resp3.status_code == 200
            assert b"already exists" in resp3.data, (
                "Flash warning about duplicate goal not found"
            )

            # -- DB state: exactly 1 goal, unchanged --
            goal_count = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Emergency Fund",
            ).count()
            assert goal_count == 1, (
                f"Expected 1 goal, found {goal_count}"
            )

            # Original goal not modified.
            db.session.expire_all()
            original_goal = db.session.get(SavingsGoal, original_goal_id)
            assert original_goal.target_amount == Decimal("5000.00")

            # Session health check: only 1 goal should exist at this point.
            total_goals = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert total_goals == 1

            # -- Same name on DIFFERENT account must still succeed --
            acct2 = _create_savings_account(seed_user, "Second Savings")
            db.session.commit()
            resp4 = auth_client.post("/savings/goals", data={
                "account_id": str(acct2.id),
                "name": "Emergency Fund",
                "target_amount": "3000.00",
            })
            assert resp4.status_code == 302, (
                f"Same name on different account returned {resp4.status_code}, "
                "expected 302 (should succeed)"
            )

            # Now 2 goals named "Emergency Fund" exist, on different accounts.
            all_ef_goals = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
                name="Emergency Fund",
            ).all()
            assert len(all_ef_goals) == 2, (
                f"Expected 2 goals named 'Emergency Fund', found {len(all_ef_goals)}"
            )
            account_ids = {g.account_id for g in all_ef_goals}
            assert account_ids == {acct.id, acct2.id}


# ── Negative Paths ────────────────────────────────────────────────


class TestSavingsNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR, deactivated accounts, validation."""

    def test_edit_nonexistent_goal(self, app, auth_client, seed_user):
        """GET /savings/goals/999999/edit for a nonexistent goal returns 404 (security)."""
        with app.app_context():
            resp = auth_client.get(
                "/savings/goals/999999/edit", follow_redirects=True,
            )

            assert resp.status_code == 404

    def test_update_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999 for a nonexistent goal returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post("/savings/goals/999999", data={
                "name": "Ghost Goal",
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 404

    def test_delete_nonexistent_goal(self, app, auth_client, seed_user):
        """POST /savings/goals/999999/delete for a nonexistent goal returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals/999999/delete", follow_redirects=True,
            )

            assert resp.status_code == 404

    def test_create_goal_on_deactivated_account(self, app, auth_client, seed_user):
        """POST /savings/goals with account_id of a deactivated account is rejected."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            acct.is_active = False
            db.session.commit()

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Deactivated Test",
                "target_amount": "5000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account." in resp.data

            # Verify no goal was created.
            goal = db.session.query(SavingsGoal).filter_by(
                name="Deactivated Test",
            ).first()
            assert goal is None

    def test_update_goal_account_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /savings/goals/<id> with another user's account_id is rejected."""
        with app.app_context():
            acct = _create_savings_account(seed_user)
            goal = _create_goal(seed_user, acct)
            original_account_id = goal.account_id

            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "account_id": str(second_user["account"].id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid account." in resp.data

            # Verify goal's account_id was NOT changed.
            db.session.expire_all()
            refreshed = db.session.get(SavingsGoal, goal.id)
            assert refreshed.account_id == original_account_id, (
                "Goal's account_id must not change to another user's account"
            )

    def test_delete_other_users_goal_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /savings/goals/<id>/delete for another user's goal is blocked."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            other_acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=second_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Other Savings",
                    anchor_balance=Decimal("2000.00"),
                ),
            )
            db.session.add(other_acct)
            db.session.flush()

            goal = SavingsGoal(
                user_id=second_user["user"].id,
                account_id=other_acct.id,
                name="Other Goal",
                target_amount=Decimal("5000.00"),
            )
            db.session.add(goal)
            db.session.commit()
            goal_id = goal.id

            resp = auth_client.post(
                f"/savings/goals/{goal_id}/delete",
                follow_redirects=True,
            )

            assert resp.status_code == 404

            # Verify goal still exists and is active.
            db.session.expire_all()
            refreshed = db.session.get(SavingsGoal, goal_id)
            assert refreshed is not None
            assert refreshed.is_active is True

    def test_create_goal_missing_required_fields(self, app, auth_client, seed_user):
        """POST /savings/goals with empty form data fails validation and creates no record."""
        with app.app_context():
            resp = auth_client.post(
                "/savings/goals", data={}, follow_redirects=True,
            )

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no goal was created.
            count = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0

    def test_create_goal_negative_target_amount(self, app, auth_client, seed_user):
        """POST /savings/goals with negative target_amount fails schema validation."""
        with app.app_context():
            acct = _create_savings_account(seed_user)

            resp = auth_client.post("/savings/goals", data={
                "account_id": acct.id,
                "name": "Bad Goal",
                "target_amount": "-1000.00",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Please correct the highlighted errors" in resp.data

            # Verify no goal was created.
            count = db.session.query(SavingsGoal).filter_by(
                user_id=seed_user["user"].id,
            ).count()
            assert count == 0


# ── Section 5 Regression Baseline ──────────────────────────────────────


class TestSavingsGoalRegression:
    """Regression baseline for Section 5 savings goal changes.

    Locks down the full savings goal lifecycle (create, read, update,
    deactivate) and edge cases before Section 5 modifies savings
    projections and goal computation.
    """

    def test_full_lifecycle_create_read_update_deactivate(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Complete goal lifecycle: create -> read on dashboard -> update
        -> deactivate -> verify absent from active views.

        This is the primary regression test for the goal CRUD pipeline.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "Lifecycle Savings")

            # Create.
            resp = auth_client.post("/savings/goals", data={
                "name": "Lifecycle Goal",
                "target_amount": "8000.00",
                "target_date": "2027-12-01",
                "contribution_per_period": "100.00",
                "account_id": str(acct.id),
            })
            assert resp.status_code == 302

            goal = db.session.query(SavingsGoal).filter_by(
                name="Lifecycle Goal"
            ).one()
            assert goal.target_amount == Decimal("8000.00")
            assert goal.is_active is True

            # Read on dashboard.
            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Lifecycle Goal" in resp.data

            # Update.
            resp = auth_client.post(f"/savings/goals/{goal.id}", data={
                "name": "Updated Goal",
                "target_amount": "12000.00",
                "target_date": "2028-06-01",
                "contribution_per_period": "150.00",
                "account_id": str(acct.id),
            })
            assert resp.status_code == 302
            db.session.refresh(goal)
            assert goal.name == "Updated Goal"
            assert goal.target_amount == Decimal("12000.00")

            # Deactivate.
            resp = auth_client.post(f"/savings/goals/{goal.id}/delete")
            assert resp.status_code == 302
            db.session.refresh(goal)
            assert goal.is_active is False

            # Verify absent from active goal list (goal name may still
            # appear in flash/toast messages, so check the DB instead).
            active_goals = (
                db.session.query(SavingsGoal)
                .filter_by(user_id=seed_user["user"].id, is_active=True)
                .all()
            )
            active_names = [g.name for g in active_goals]
            assert "Updated Goal" not in active_names

    def test_goal_with_past_target_date(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Goal with target_date in the past must not crash the dashboard.

        Users may have goals whose deadlines have passed.  The dashboard
        should handle this gracefully.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "Past Date Savings")

            # Create goal with past target_date directly in DB.
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Overdue Goal",
                target_amount=Decimal("5000.00"),
                target_date=date(2020, 1, 1),
            )
            db.session.add(goal)
            db.session.commit()

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Overdue Goal" in resp.data

    def test_goal_with_zero_target_amount_rejected(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Goal with zero target_amount must fail validation.

        The savings_goals table has a CHECK constraint: target_amount > 0.
        The schema validation should catch this before hitting the DB.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "Zero Target Savings")
            auth_client.post("/savings/goals", data={
                "name": "Zero Goal",
                "target_amount": "0.00",
                "account_id": str(acct.id),
            })
            # Should fail validation -- not create the goal.
            count = db.session.query(SavingsGoal).filter_by(
                name="Zero Goal"
            ).count()
            assert count == 0

    def test_goal_without_contribution_per_period(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Goal without contribution_per_period must still render on dashboard.

        contribution_per_period is optional (nullable).  The dashboard
        must handle None gracefully in its progress calculations.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "No Contrib Savings")

            resp = auth_client.post("/savings/goals", data={
                "name": "No Contribution Goal",
                "target_amount": "3000.00",
                "target_date": "2028-01-01",
                "account_id": str(acct.id),
            })
            assert resp.status_code == 302

            goal = db.session.query(SavingsGoal).filter_by(
                name="No Contribution Goal"
            ).one()
            assert goal.contribution_per_period is None

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"No Contribution Goal" in resp.data

    def test_goal_without_target_date(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Goal without target_date must still render on dashboard.

        target_date is nullable.  The dashboard's remaining_periods
        calculation must handle None target_date without error.
        """
        with app.app_context():
            acct = _create_savings_account(seed_user, "No Date Savings")

            resp = auth_client.post("/savings/goals", data={
                "name": "Dateless Goal",
                "target_amount": "7000.00",
                "account_id": str(acct.id),
            })
            assert resp.status_code == 302

            goal = db.session.query(SavingsGoal).filter_by(
                name="Dateless Goal"
            ).one()
            assert goal.target_date is None

            resp = auth_client.get("/savings")
            assert resp.status_code == 200
            assert b"Dateless Goal" in resp.data

    def test_goal_idor_view_blocked(
        self, app, auth_client, seed_user, seed_periods,
        seed_second_user, second_auth_client, seed_second_periods,
    ):
        """User A cannot view or edit User B's savings goal.

        Verifies the ownership check on the goal edit endpoint returns
        an identical response for 'not found' and 'not yours'.
        """
        with app.app_context():
            # Create goal for user B.
            other_acct = _create_savings_account(
                seed_second_user, "Other User Savings",
            )
            other_goal = SavingsGoal(
                user_id=seed_second_user["user"].id,
                account_id=other_acct.id,
                name="Private Goal",
                target_amount=Decimal("20000.00"),
            )
            db.session.add(other_goal)
            db.session.commit()

            # User A tries to access User B's goal edit form.
            resp = auth_client.get(f"/savings/goals/{other_goal.id}/edit")
            assert resp.status_code == 404

            # User A tries to update User B's goal.
            resp = auth_client.post(f"/savings/goals/{other_goal.id}", data={
                "name": "Hijacked",
                "target_amount": "1.00",
                "account_id": str(other_acct.id),
            })
            assert resp.status_code == 404

            # Goal must be unchanged.
            db.session.refresh(other_goal)
            assert other_goal.name == "Private Goal"
            assert other_goal.target_amount == Decimal("20000.00")

    def test_goal_negative_target_amount_rejected(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Negative target_amount must be rejected by schema validation."""
        with app.app_context():
            acct = _create_savings_account(seed_user, "Neg Target Savings")
            auth_client.post("/savings/goals", data={
                "name": "Negative Goal",
                "target_amount": "-5000.00",
                "account_id": str(acct.id),
            })
            count = db.session.query(SavingsGoal).filter_by(
                name="Negative Goal"
            ).count()
            assert count == 0
