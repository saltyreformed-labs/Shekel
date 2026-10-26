"""Database constraint regression tests for budget.scenarios.

Locks the storage-tier guarantee that each user has at most one
baseline scenario.  The constraint is materialised by migration
``c5d6e7f8a901_add_positive_amount_check_constraints.py`` as the
partial unique index ``uq_scenarios_one_baseline``
(``ON budget.scenarios (user_id) WHERE is_baseline = true``) and
mirrored by the model declaration in ``app/models/scenario.py``.

Without the index, the budget.balance_calculator would silently pick
one of two baselines for the same user when computing projections,
producing different answers depending on which Scenario row the ORM
returned first -- a load-bearing correctness bug.  The route layer
already enforces idempotency (see ``app/routes/grid/page.py::create_baseline``
and the
``test_grid/test_create.py::TestCreateBaseline.test_create_baseline_idempotent``
test), so the partial unique index is the database-tier backstop for a
defective caller that bypasses the route.

Audit reference: H-2 of
docs/audits/security-2026-04-15/model-migration-drift.md.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.scenario import Scenario


class TestScenarioBaselineUniqueness:
    """uq_scenarios_one_baseline rejects a second baseline per user."""

    def test_second_baseline_for_same_user_rejected(self, app, db, seed_user):
        """Inserting a second baseline Scenario for the same user raises IntegrityError.

        ``seed_user`` creates the user's canonical baseline.  Adding
        a second one violates the partial unique index.
        """
        with app.app_context():
            duplicate = Scenario(
                user_id=seed_user["user"].id,
                name="Baseline Duplicate",
                is_baseline=True,
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as exc_info:
                db.session.flush()
            assert "uq_scenarios_one_baseline" in str(exc_info.value)
            db.session.rollback()

    def test_non_baseline_scenarios_allowed_alongside_baseline(
        self, app, db, seed_user
    ):
        """A user may have many non-baseline scenarios alongside the baseline.

        The partial WHERE clause (``is_baseline = true``) scopes the
        unique index to baseline rows only; what-if scenarios all live
        in the same user_id without conflict.  Verifying this
        end-to-end keeps a future regression that drops the WHERE
        clause from breaking the scenario-cloning workflow.
        """
        with app.app_context():
            for name in ("What-If A", "What-If B", "What-If C"):
                scenario = Scenario(
                    user_id=seed_user["user"].id,
                    name=name,
                    is_baseline=False,
                )
                db.session.add(scenario)
            db.session.flush()
            non_baseline_count = (
                db.session.query(Scenario)
                .filter_by(
                    user_id=seed_user["user"].id, is_baseline=False
                )
                .count()
            )
            assert non_baseline_count == 3
            db.session.rollback()

    def test_separate_users_each_have_own_baseline(
        self, app, db, seed_user, second_user
    ):
        """Two users may each carry one baseline scenario simultaneously.

        The partial unique index is keyed on ``user_id``, so the
        constraint scopes per user.  Without this scoping a multi-
        user database (the eventual hosted form) would silently
        collapse both users' baselines into one.  Both
        ``seed_user`` and ``second_user`` ship a baseline already;
        the assertion just confirms both rows exist and the partial
        index does not collide.
        """
        with app.app_context():
            seed_baseline = (
                db.session.query(Scenario)
                .filter_by(user_id=seed_user["user"].id, is_baseline=True)
                .one()
            )
            other_baseline = (
                db.session.query(Scenario)
                .filter_by(user_id=second_user["user"].id, is_baseline=True)
                .one()
            )
            assert seed_baseline.id != other_baseline.id
            assert seed_baseline.user_id != other_baseline.user_id
