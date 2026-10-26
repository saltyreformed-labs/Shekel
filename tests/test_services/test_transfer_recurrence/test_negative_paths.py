"""Transfer recurrence tests: negative paths and boundary conditions."""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app import ref_cache
from app.enums import StatusEnum
from app.services import transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import create_account_of_type, make_cadence_rule
from tests.oracles.recurrence_baseline import EVERY_PERIOD
from tests._test_helpers import state_template_price


# --- Negative-Path Tests ---------------------------------------------------


class TestNegativePaths:
    """Negative-path and boundary-condition tests for transfer recurrence.

    Verifies behavior with zero/negative amounts, self-transfers, empty
    periods, and immutable status preservation during regeneration.
    """

    def _make_template_with_rule(self, seed_user, cadence,
                                  default_amount=Decimal("100.00"),
                                  from_account_id=None, to_account_id=None,
                                  **rule_kwargs):
        """Helper: create rule + template with configurable amount and accounts."""

        # Create savings account for default to_account if not specified.
        if to_account_id is None:
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Savings NP",
                anchor_balance=Decimal("500.00"),
            )
            to_account_id = savings.id

        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=from_account_id or seed_user["account"].id,
            to_account_id=to_account_id,
            name="Test Transfer NP",
            default_amount=default_amount,
        )
        db.session.add(template)
        db.session.flush()
        state_template_price(template)
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            template, cadence,
            interval_n=rule_kwargs.get("interval_n", 1),
            fires_on_day=rule_kwargs.get("day_of_month"),
            fires_in_month=rule_kwargs.get("month_of_year"),
        )
        db.session.refresh(template)
        return template

    def test_zero_amount_transfer_rejected_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Zero-amount transfer template is rejected by the DB CHECK constraint.

        Input: Template with default_amount=0.00.
        Expected: IntegrityError from ck_transfer_templates_positive_amount.
        The DB enforces that default_amount > 0 at the schema level.
        Why: A zero-amount transfer is financially meaningless. The DB constraint
        catches this before the recurrence engine ever runs.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        with app.app_context():
            with pytest.raises(SAIntegrityError):
                self._make_template_with_rule(
                    seed_user, EVERY_PERIOD, default_amount=Decimal("0.00")
                )
            # Rollback the failed transaction so subsequent tests can use the session.
            db.session.rollback()

    def test_self_transfer_same_account_rejected_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Self-transfers (same from and to account) are rejected by DB constraint.

        Input: Template with from_account_id == to_account_id.
        Expected: IntegrityError from ck_transfer_templates_different_accounts.
        The DB enforces from_account_id != to_account_id at the schema level.
        Why: A self-transfer is logically meaningless and could corrupt balance
        calculations. The DB constraint prevents it before the service runs.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        with app.app_context():
            same_account_id = seed_user["account"].id
            with pytest.raises(SAIntegrityError):
                self._make_template_with_rule(
                    seed_user, EVERY_PERIOD,
                    from_account_id=same_account_id,
                    to_account_id=same_account_id,
                )
            # Rollback the failed transaction so subsequent tests can use the session.
            db.session.rollback()

    def test_generate_with_empty_periods_returns_empty(
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
            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), set(),
                ), seed_user["scenario"].id,
                effective_from=date(2026, 1, 1),
            )

            assert created == []

    def test_immutable_status_preserved_on_regeneration(
        self, app, db, seed_user, seed_periods
    ):
        """Done transfers must be preserved on regeneration.

        Input: Generate for all periods, mark one as done, change template
        amount, regenerate.
        Expected: The done transfer persists with same ID, status, and
        original amount. Other periods get the new amount.
        Why: Settled transfers are financial history that must not be overwritten.
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD
            )

            created = transfer_recurrence.generate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()
            assert len(created) == len(seed_periods)

            # Mark one as done (Paid).
            done_id_val = ref_cache.status_id(StatusEnum.DONE)
            target_xfer = created[3]
            target_id = target_xfer.id
            original_amount = target_xfer.amount
            target_xfer.status_id = done_id_val
            db.session.flush()

            # Change template amount and regenerate.
            template.default_amount = Decimal("200.00")
            state_template_price(template, Decimal("200.00"))
            db.session.flush()

            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
            )
            db.session.flush()

            # The done transfer must still exist unchanged.
            preserved = db.session.get(Transfer, target_id)
            assert preserved is not None, (
                f"Done transfer {target_id} was deleted during regeneration"
            )
            assert preserved.status_id == done_id_val
            assert preserved.id == target_id
            assert preserved.amount == original_amount

    def test_negative_amount_rejected_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Negative transfer amount is rejected by the DB CHECK constraint.

        Input: Template with default_amount=-100.00.
        Expected: IntegrityError from ck_transfer_templates_positive_amount.
        The DB enforces that default_amount > 0 at the schema level.
        Why: A negative transfer amount could reverse the direction of money
        flow, causing incorrect account balances. The DB constraint prevents
        it before the service ever runs.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        with app.app_context():
            with pytest.raises(SAIntegrityError):
                self._make_template_with_rule(
                    seed_user, EVERY_PERIOD,
                    default_amount=Decimal("-100.00"),
                )
            # Rollback the failed transaction so subsequent tests can use the session.
            db.session.rollback()

    def test_max_amount_overflow_rejected_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Commit 32 / MED-07 / PA-25: amounts past Numeric(12,2) max are rejected.

        Input: Template with default_amount=10_000_000_000.00 (11 integer
        digits, exceeds the column's Numeric(12,2) capacity of
        9,999,999,999.99).
        Expected: DataError (NumericValueOutOfRange) -- the column cannot
        represent the value, so PostgreSQL rejects the INSERT before the
        recurrence engine ever sees it.
        Why: closes the PA-25 transfer-recurrence boundary gap recorded in
        07_test_gaps slice-4 concept-6.  The complementary zero-amount,
        self-transfer, and negative-amount boundaries already have pinned
        rejection tests; max-amount overflow did not.
        """
        from sqlalchemy.exc import DataError as SADataError

        with app.app_context():
            with pytest.raises(SADataError):
                self._make_template_with_rule(
                    seed_user, EVERY_PERIOD,
                    default_amount=Decimal("10000000000.00"),
                )
            db.session.rollback()

    def test_at_max_amount_accepted_by_db(
        self, app, db, seed_user, seed_periods
    ):
        """Commit 32 / MED-07 / PA-25: Numeric(12,2) max is accepted.

        Input: Template with default_amount=9_999_999_999.99 (the largest
        value the column can store -- 10 integer digits + 2 decimal
        places).
        Expected: the row inserts and persists the exact Decimal back.
        Pairs with test_max_amount_overflow_rejected_by_db to define the
        boundary precisely (max value accepted, max+1 cent rejected).
        """
        with app.app_context():
            template = self._make_template_with_rule(
                seed_user, EVERY_PERIOD,
                default_amount=Decimal("9999999999.99"),
            )
            db.session.flush()
            db.session.refresh(template)
            assert template.default_amount == Decimal("9999999999.99"), (
                f"Expected 9999999999.99, got {template.default_amount}"
            )
