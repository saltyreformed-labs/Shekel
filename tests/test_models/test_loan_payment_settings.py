"""Tests for the loan_payment_settings model + its reader accessor (decision B).

The 1:1 ``budget.loan_payment_settings`` table holds a recurring loan payment's
``derive_from_loan`` (moved off ``transfer_templates``) and ``extra_principal``
(the overpayment feature).  These tests pin the storage-tier guards (the
non-negative-extra CHECK, the one-row-per-template UNIQUE, the CASCADE delete)
and the ``loan_payment_config`` accessor's row-absent defaults, which every
live-derive / overpayment reader relies on to stay dormant for non-loan
transfers.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer_template import TransferTemplate
from app.services.recurring_transfer_query import loan_payment_config
from tests._test_helpers import create_account_of_type


def _make_template(seed_user, *, name="Payment"):
    """Create + flush a minimal recurring TransferTemplate for the test user.

    Checking (``seed_user``'s account) -> a fresh Savings account, so the
    ``from != to`` CHECK is satisfied; no settings row is attached.
    """
    savings = create_account_of_type(
        seed_user, db.session, "Savings", f"Dest {name}",
    )
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    return template


class TestLoanPaymentConfigAccessor:
    """The ``loan_payment_config`` accessor: row-absent defaults + row values."""

    def test_no_settings_row_defaults_to_non_derive_zero_extra(
        self, app, seed_user,
    ):
        """A template with no settings row resolves to ``(False, 0.00)``.

        This is the load-bearing default that keeps the live-derive /
        overpayment machinery dormant for every generic transfer and
        investment contribution (which never get a settings row).
        """
        with app.app_context():
            template = _make_template(seed_user)
            derive, extra = loan_payment_config(template)
            assert derive is False
            assert extra == Decimal("0.00")

    def test_settings_row_values_are_returned(self, app, seed_user):
        """With a settings row the accessor returns its derive flag + extra.

        Derive True, extra 125.50 -> the accessor returns exactly those,
        as a ``Decimal`` for the money field.
        """
        with app.app_context():
            template = _make_template(seed_user)
            template.settings = LoanPaymentSettings(
                derive_from_loan=True, extra_principal=Decimal("125.50"),
            )
            db.session.flush()
            derive, extra = loan_payment_config(template)
            assert derive is True
            assert extra == Decimal("125.50")


class TestLoanPaymentSettingsConstraints:
    """Storage-tier guards on ``loan_payment_settings``."""

    def test_negative_extra_principal_rejected(self, app, seed_user):
        """The ``extra_principal >= 0`` CHECK rejects a negative overpayment."""
        with app.app_context():
            template = _make_template(seed_user)
            db.session.add(LoanPaymentSettings(
                transfer_template_id=template.id,
                extra_principal=Decimal("-0.01"),
            ))
            with pytest.raises(IntegrityError):
                db.session.flush()

    def test_second_settings_row_for_one_template_rejected(
        self, app, seed_user,
    ):
        """The UNIQUE(transfer_template_id) makes the 1:1 relationship real."""
        with app.app_context():
            template = _make_template(seed_user)
            db.session.add(LoanPaymentSettings(
                transfer_template_id=template.id,
            ))
            db.session.flush()
            db.session.add(LoanPaymentSettings(
                transfer_template_id=template.id,
            ))
            with pytest.raises(IntegrityError):
                db.session.flush()

    def test_deleting_template_cascades_to_settings(self, app, seed_user):
        """Deleting a template disposes its settings row (FK ON DELETE CASCADE).

        The settings die with their template, so a deleted recurring payment
        never leaves an orphaned settings row.
        """
        with app.app_context():
            template = _make_template(seed_user)
            template.settings = LoanPaymentSettings(derive_from_loan=True)
            db.session.flush()
            settings_id = template.settings.id

            db.session.delete(template)
            db.session.flush()

            assert (
                db.session.get(LoanPaymentSettings, settings_id) is None
            )
