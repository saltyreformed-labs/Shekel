"""Recurrence engine tests: cross-user isolation, and negative paths and boundary conditions."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.ref import TransactionType, Status
from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._test_helpers import make_cadence_rule, resolved_amount, state_template_price


class TestCrossUserIsolation:
    """IDOR tests for the recurrence engine."""

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
            name='Test Recurring',
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


    def test_cross_user_isolation(
        self, app, db, seed_user, seed_periods, second_user
    ):
        """Template owned by user A must not generate into B's scenario.

        generate_for_template validates that the template's user_id
        matches the scenario's user_id. If they differ, zero
        transactions are created (defense-in-depth against IDOR).
        """
        with app.app_context():
            # Template belongs to seed_user (user A).
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            # SECURITY: Attempt to generate into user B's
            # scenario using user A's template. This should
            # be rejected but currently is not.
            created = recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ),
                second_user["scenario"].id,
            )

            # Correct behavior: no transactions should be
            # created across user boundaries.
            assert len(created) == 0, (
                f"IDOR: Template (user_id="
                f"{seed_user['user'].id}) generated "
                f"{len(created)} transactions into scenario "
                f"(user_id={second_user['user'].id}). "
                f"generate_for_template needs an ownership "
                f"check."
            )


# --- Negative-Path Tests ---------------------------------------------------


class TestNegativePaths:
    """Negative-path and boundary-condition tests for the recurrence engine.

    Verifies behavior with zero-amount templates, None recurrence rules,
    empty period lists, and immutable status preservation during regeneration.
    """

    def _make_template_with_rule(
        self, seed_user, cadence,
        default_amount=Decimal("100.00"), **rule_kwargs,
    ):
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
            name='Test Recurring NP',
            default_amount=default_amount,
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

    def test_template_with_zero_estimated_amount(
        self, app, db, seed_user, seed_periods
    ):
        """Zero-amount template generates transactions with amount=0.00.

        Input: Template with default_amount=0.00, every_period pattern.
        Expected: One transaction per period, each with estimated_amount=0.00.
        The engine does not skip zero-amount templates.
        Why: A template accidentally set to $0 must behave predictably, not crash
        or generate phantom balances.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD, default_amount=Decimal("0.00")
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            # Engine generates for all periods regardless of amount.
            assert len(created) == len(seed_periods)
            for txn in created:
                assert resolved_amount(txn) == Decimal("0.00")

    def test_template_with_none_recurrence_rule(
        self, app, db, seed_user, seed_periods
    ):
        """Template with no recurrence rule returns empty list.

        Input: Template with recurrence_rule_id=None.
        Expected: generate_for_template returns [].
        Why: Templates without rules are manually placed; the engine must not
        crash or generate spurious transactions.
        """
        with app.app_context():
            expense_type = (
                db.session.query(TransactionType)
                .filter_by(name="Expense")
                .one()
            )
            template = TransactionTemplate(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                category_id=seed_user["categories"]["Car Payment"].id,
                transaction_type_id=expense_type.id,
                name="No Rule Template",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            state_template_price(template)
            db.session.refresh(template)

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )

            assert created == []

    def test_received_status_in_existing_transaction_is_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Received transactions must NOT be deleted or recreated on regeneration.

        Input: Generate for all periods, mark one as received, regenerate.
        Expected: The received transaction persists with same ID and status.
        Other periods are regenerated normally.
        Why: The recurrence engine must never overwrite settled financial history.
        A received paycheck deleted by regeneration corrupts balance history.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            # Initial generation.
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark the 3rd transaction as received.
            received_status = (
                db.session.query(Status).filter_by(name="Received").one()
            )
            target_txn = created[2]
            target_id = target_txn.id
            target_txn.status_id = received_status.id
            db.session.flush()

            # Regenerate -- received transaction must survive.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Verify the received transaction still exists unchanged.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Received transaction {target_id} was deleted during regeneration"
            )
            assert preserved.status.name == "Received"
            assert preserved.id == target_id

    def test_generate_with_empty_periods_list(
        self, app, db, seed_user, seed_periods
    ):
        """Empty periods list returns empty without error.

        Input: Template with valid rule, periods=[].
        Expected: Returns []. No crash.
        Why: Edge case when the user has no pay periods generated yet.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )
            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), set(),
                ), seed_user["scenario"].id,
                effective_from=date(2026, 1, 1),
            )

            assert created == []

    def test_cancelled_status_in_existing_is_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Cancelled transactions must be preserved on regeneration.

        Input: Generate for all periods, cancel one, regenerate.
        Expected: The cancelled transaction persists with same ID and status.
        Why: Cancelled items represent a deliberate user action that must
        not be overwritten by the recurrence engine.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Cancel one transaction.
            cancelled_status = (
                db.session.query(Status).filter_by(name="Cancelled").one()
            )
            target_txn = created[4]
            target_id = target_txn.id
            target_txn.status_id = cancelled_status.id
            db.session.flush()

            # Regenerate.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Cancelled transaction must still exist.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Cancelled transaction {target_id} was deleted "
                f"during regeneration"
            )
            assert preserved.status.name == "Cancelled"
            assert preserved.id == target_id

    def test_credit_status_in_existing_is_immutable(
        self, app, db, seed_user, seed_periods
    ):
        """Credit transactions must be preserved on regeneration.

        Input: Generate for all periods, mark one as credit, regenerate.
        Expected: The credit transaction persists with same ID and status.
        Why: Credit items represent payments on a credit card and must not
        be overwritten by the recurrence engine.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = recurrence_engine.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark one as credit.
            credit_status = (
                db.session.query(Status).filter_by(name="Credit").one()
            )
            target_txn = created[6]
            target_id = target_txn.id
            target_txn.status_id = credit_status.id
            db.session.flush()

            # Regenerate.
            recurrence_engine.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # Credit transaction must still exist.
            preserved = db.session.get(Transaction, target_id)
            assert preserved is not None, (
                f"Credit transaction {target_id} was deleted "
                f"during regeneration"
            )
            assert preserved.status.name == "Credit"
            assert preserved.id == target_id
