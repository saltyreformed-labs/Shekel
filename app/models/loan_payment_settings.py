"""
Shekel Budget App -- Loan payment settings model (budget schema)

The loan-payment attributes of a recurring transfer, normalized off the generic
:class:`~app.models.transfer_template.TransferTemplate` into their own 1:1 table
(decision B, ``docs/design/escrow_line_identity_refactor.md`` Sec. 6.3).

A recurring transfer that is a LOAN payment carries two settings the generic
transfer does not: whether its cash amount is derived LIVE from the destination
loan (``derive_from_loan``, moved here off ``transfer_templates`` to retire that
column's loan-only smell) and a standing extra-principal amount added on top of
the base payment every month (``extra_principal``, the overpayment feature).  A
template with no ``loan_payment_settings`` row is not a loan payment: every
reader defaults ``derive_from_loan`` to ``False`` and ``extra_principal`` to
``0.00`` for the row-absent case, so investment contributions and generic
transfers are unaffected.

``extra_principal`` is applied as a LIVE loan-level parameter -- added to the
cash at display, frozen at settlement, and threaded into the payoff projection
-- rather than baked into the stored transfer amount, so changing it updates
every surface from one source with no shadow regeneration.  See the design doc
Sec. 6 for the full overpayment model.
"""

from decimal import Decimal

from app.extensions import db
from app.models.mixins import TimestampMixin


class LoanPaymentSettings(TimestampMixin, db.Model):
    """The loan-payment settings of one recurring transfer (1:1 with its template).

    Keyed by ``transfer_template_id`` (UNIQUE, ON DELETE CASCADE), so the settings
    die with their template and a template has at most one settings row.  Holds
    ``derive_from_loan`` (the live-derive flag moved off
    :class:`~app.models.transfer_template.TransferTemplate`) and ``extra_principal``
    (the standing monthly overpayment, ``>= 0``, default ``0.00``).  The
    ``TransferTemplate.settings`` relationship (``uselist=False``) exposes it; a
    ``None`` settings row means "not a loan payment" and every reader supplies the
    defaults, so the table exists only for the loan-payment templates.
    """

    __tablename__ = "loan_payment_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "transfer_template_id",
            name="uq_loan_payment_settings_transfer_template_id",
        ),
        db.CheckConstraint(
            "extra_principal >= 0",
            name="ck_loan_payment_settings_nonneg_extra_principal",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    transfer_template_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.transfer_templates.id", ondelete="CASCADE",
            name="fk_loan_payment_settings_transfer_template_id",
        ),
        nullable=False,
    )
    # Moved off ``transfer_templates`` (decision B): TRUE means this recurring
    # transfer's cash is derived LIVE from the destination loan (P&I + as-of
    # escrow) on every render, so it tracks the loan after an escrow / rate
    # change instead of staying frozen at a stated price.
    derive_from_loan = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    # A standing extra principal payment added to the base cash EVERY month, in
    # both derive and manual mode (spec Sec. 6.1).  Default 0.00 (no
    # overpayment).  Applied live everywhere it matters (display, settle freeze,
    # payoff projection), never baked into the stored transfer amount, so it has
    # a single source of truth.
    extra_principal = db.Column(
        db.Numeric(12, 2), nullable=False, default=Decimal("0.00"),
        server_default=db.text("0"),
    )

    # Relationships
    template = db.relationship(
        "TransferTemplate", back_populates="settings",
    )

    def __repr__(self):
        return (
            f"<LoanPaymentSettings template_id={self.transfer_template_id} "
            f"derive={self.derive_from_loan} extra={self.extra_principal}>"
        )
