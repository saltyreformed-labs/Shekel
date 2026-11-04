"""
Shekel Budget App -- Transfer Template Model (budget schema)

A template defines a recurring transfer between accounts (e.g. "Monthly
savings contribution") along with its recurrence rule and its price: a
dated series (``amount_versions``), or the loan's own payment for a loan
payment that follows its loan.  The transfer recurrence engine uses
templates to auto-generate Transfer rows into future pay periods.
"""

from app.extensions import db
from app.models._derived_flag import DerivedFlag
from app.models.mixins import (
    IsActiveMixin,
    OptimisticLockMixin,
    SortOrderMixin,
    TimestampMixin,
    UserScopedMixin,
)


class TransferTemplate(
    UserScopedMixin, IsActiveMixin, SortOrderMixin, OptimisticLockMixin,
    TimestampMixin, db.Model,
):
    """Blueprint for a recurring transfer between two accounts.

    Optimistic locking: see :class:`Transaction` for the
    ``version_id_col`` contract.  Concurrent transfer-template edits
    race for the bump; the loser raises ``StaleDataError`` and the
    route surfaces a flash + redirect.  See commit C-18 of the
    2026-04-15 security remediation plan.
    """

    __tablename__ = "transfer_templates"
    __table_args__ = (
        db.Index("idx_transfer_templates_user", "user_id"),
        db.CheckConstraint(
            "from_account_id != to_account_id",
            name="ck_transfer_templates_different_accounts",
        ),
        db.CheckConstraint(
            "version_id > 0",
            name="ck_transfer_templates_version_id_positive",
        ),
        db.UniqueConstraint("user_id", "name", name="uq_transfer_templates_user_name"),
        {"schema": "budget"},
    )

    # Pylint: ``duplicate-code`` -- Incidental id-PK + from/to-account FK
    # preamble, shared by structure (not by domain) with the transfer table.
    # A transfer is a generated instance and a transfer_template is its
    # blueprint; they are deliberately separate tables (the transfer carries
    # the shadow-transaction invariants), so extracting a base would couple
    # them wrongly (coding-standards rule 13).  One-sided disable.
    # pylint: disable=duplicate-code
    id = db.Column(db.Integer, primary_key=True)
    from_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_account_id = db.Column(
        db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = db.Column(db.String(200), nullable=False)
    # No price column: a definition's price is its dated series
    # (``amount_versions``), or amount rule 4 for a loan payment that follows
    # its loan.  ``default_amount`` stored a copy until plan step X-bp
    # dropped it (rulings R-IY, R-BAL16).
    # is_active + sort_order: from IsActiveMixin / SortOrderMixin.
    category_id = db.Column(
        db.Integer, db.ForeignKey("budget.categories.id", ondelete="SET NULL"),
    )
    # The live-derive flag + the standing overpayment live in the 1:1
    # ``loan_payment_settings`` row (decision B), reached via ``settings`` below;
    # a template with no settings row is not a loan payment.  The old
    # ``derive_from_loan`` column was dropped by migration ``c1a4f7b9e2d3``.
    # version_id + its version_id_col mapper config: from OptimisticLockMixin.

    # Relationships
    from_account = db.relationship(
        "Account", foreign_keys=[from_account_id], lazy="joined"
    )
    to_account = db.relationship(
        "Account", foreign_keys=[to_account_id], lazy="joined"
    )
    # 0-or-1 cadence, and the FK is on the RULE (plan step R-F6).  See the
    # transaction template's twin for the whole of why; the two are the same
    # relationship onto the same satellite, and this arm is the one a loan
    # payment and an investment contribution ride on.
    recurrence_rule = db.relationship(
        "RecurrenceRule", uselist=False, lazy="joined",
        cascade="all, delete-orphan",
        back_populates="transfer_template",
    )
    category = db.relationship("Category", lazy="joined")
    transfers = db.relationship(
        "Transfer", back_populates="template", lazy="select"
    )
    # 1:1 loan-payment settings (decision B): present only for recurring loan
    # payments, holding ``derive_from_loan`` + ``extra_principal``.  ``None`` for
    # every other template (investment contribution, generic transfer); readers
    # default the settings when the row is absent.  ``delete-orphan`` disposes
    # the row with the template (the DB FK is ON DELETE CASCADE too).
    settings = db.relationship(
        "LoanPaymentSettings", uselist=False, back_populates="template",
        cascade="all, delete-orphan",
    )
    # The effective-dated history of the definition's price (plan step
    # X-au-a), its only price since X-bp.  A DERIVE-mode loan payment has NONE
    # and must not: amount rule 4 computes its P&I plus escrow from the loan,
    # so a version here would record a derived quantity as a stated price.  A
    # MANUAL loan payment DOES have one -- there the operator owns the base
    # cash.
    # :mod:`app.services.template_amount_service` owns both the predicate and
    # the write door.
    amount_versions = db.relationship(
        "TemplateAmountVersion",
        back_populates="transfer_template",
        cascade="all, delete-orphan",
        lazy="select",
    )

    @DerivedFlag
    def recurs(self):
        """True when this definition has a recurrence rule.

        **The ONE accessor for "does this transfer definition repeat"**
        (plan step ``balance:X-ci-1``, ruling **R-BAL20** applied to the
        transfer twin; the body is :attr:`TransactionTemplate.recurs`'s,
        stated on the definition because that is where the fact lives -- the
        0-or-1 cadence ``recurrence:R-F6`` put on the RULE's side -- and
        delegated to by :attr:`Transfer.recurs` for every row it holds).  A
        definition with no rule is a ONE-TIME transfer's: it moves money
        exactly once, its single Transfer is PLACED by
        ``routes/transfers/_instances._materialize_one_time_transfer`` rather
        than generated, no pass ever regenerates it, and
        ``transfer_recurrence.propagate_to_unruled_template`` is how its edits
        reach it (a cleared cadence's survivors are the same shape).  Every
        site that read ``recurrence_rule is None`` to ask this reads here
        since X-ci-1; the discardable count, the override flip and the
        due-date gate ask the row, which asks here.

        A :class:`DerivedFlag` for the reason its twin is: the question is
        one a query would want to ask, a plain property at class level
        compares ``False`` to everything, and spelling it in SQL would be a
        second body of one rule (ruling **R-IZ**).  The readers load and ask.
        """
        return self.recurrence_rule is not None

    def __repr__(self):
        return f"<TransferTemplate {self.id} '{self.name}'>"
