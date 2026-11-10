"""
Shekel Budget App -- Loan Parameters Model (budget schema)

Stores loan configuration for all installment loan types: principal,
rate, term, payment day, and optional ARM fields.  One row per
amortizing account, linked one-to-one via account_id.

E-18 / Commit 15 demoted ``current_principal`` and ``interest_rate``
from authoritative storage to non-authoritative seed columns, and both
are GONE.  DH-#56 completed the OPT-1 drop for ``interest_rate``: the
loan's base / period-0 rate lives in its origination
:class:`RateHistory` row (``create_params`` seeds one for every loan;
the DH-#56 migration backfilled pre-existing loans).  Plan step
``recurrence:R20`` (ruling **R-R72** part 3, finding **REC-519**)
dropped ``current_principal``: the balance the owner states at setup is
a dated ASSERTION and is recorded as one -- a ``tracking_start``
:class:`LoanAnchorEvent` the setup door appends in the same transaction
as this row -- where the column held it as a value nothing read.  The
loan resolver (``app/services/loan_resolver``) derives the displayed
current balance from the latest :class:`LoanAnchorEvent` plus the
confirmed payment stream, and derives the current applicable rate from
the :class:`RateHistory` series.  Display and money surfaces (loan
dashboard card, /savings debt card, /savings account card, year-end
net-worth liability, debt strategy) read the resolver's
``state.current_rate`` and the ``balance_at`` seam's folded balance,
never a stored scalar.  The origination anchor is synthesized from
``original_principal`` and ``origination_date``, the two immutable
figures this row still carries.
"""

from app.extensions import db
from app.models.mixins import AccountScopedUniqueMixin, TimestampMixin


class LoanParams(AccountScopedUniqueMixin, TimestampMixin, db.Model):
    """Loan parameters linked one-to-one with an Account.

    Serves the amortization engine for all installment loan types
    (mortgage, auto loan, student loan, personal loan, HELOC, etc.).
    ARM-specific columns are nullable and cost nothing when unused.

    Neither ``current_principal`` nor ``interest_rate`` is a column any
    longer: DH-#56 dropped the rate (the origination :class:`RateHistory`
    row is the source of truth for it) and plan step ``recurrence:R20``
    dropped the balance (a ``tracking_start`` :class:`LoanAnchorEvent`
    records the balance stated at setup).  See the module docstring for
    the resolver-as-source-of-truth contract.
    """

    __tablename__ = "loan_params"
    __table_args__ = (
        db.CheckConstraint(
            "payment_day >= 1 AND payment_day <= 31",
            name="ck_loan_params_payment_day",
        ),
        db.CheckConstraint(
            "original_principal > 0",
            name="ck_loan_params_orig_principal",
        ),
        # DH-#56 dropped ``interest_rate`` (and its two CHECKs
        # ``ck_loan_params_interest_rate`` / ``..._upper``); the rate
        # domain ``[0, 1]`` is now enforced on ``rate_history.interest_rate``
        # (``ck_rate_history_valid_interest_rate``), the single source
        # of truth for the loan's rate.  Plan step ``recurrence:R20``
        # dropped ``current_principal`` and ``ck_loan_params_curr_principal``
        # with it; a stated balance is a ``LoanAnchorEvent`` row, whose
        # ``ck_loan_anchor_events_balance_nonneg`` bounds it.
        db.CheckConstraint(
            "term_months > 0",
            name="ck_loan_params_term_months",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    original_principal = db.Column(db.Numeric(12, 2), nullable=False)
    # ``current_principal`` sat here until plan step ``recurrence:R20``
    # (ruling **R-R72** part 3): demoted to a nullable seed by migration
    # ``c4f0a5b71e83`` (Commit 15), written by the setup door and read by
    # nothing after E-18, dropped by R20's migration.  The balance the
    # owner states at setup is a dated assertion -- a ``tracking_start``
    # ``LoanAnchorEvent`` -- and display surfaces read
    # ``balance_at.balance_at``, never a column here.
    # DH-#56 dropped the ``interest_rate`` column.  The loan's base /
    # period-0 rate lives in its origination :class:`RateHistory` row
    # (the resolver derives ``state.current_rate`` from the RateHistory
    # series); ``create_params`` seeds the origination row and the
    # DH-#56 migration backfilled pre-existing loans.
    term_months = db.Column(db.Integer, nullable=False)
    origination_date = db.Column(db.Date, nullable=False)
    payment_day = db.Column(db.Integer, nullable=False)
    is_arm = db.Column(db.Boolean, nullable=False, server_default=db.text("false"))
    arm_first_adjustment_months = db.Column(db.Integer, nullable=True)
    arm_adjustment_interval_months = db.Column(db.Integer, nullable=True)
    # HOW the note accrues interest (plan step recurrence:R16-d, finding D52):
    # a fact of the loan, so a value on it, read by every walk through the one
    # producer ``app.utils.money.accrued_interest``.  NOT NULL with no server
    # default because an FK id is not a static literal: the setup door states
    # it, and the R16-d migration backfilled every existing loan to
    # ``monthly_1_12``, the list's one member (ruling plan#3354).
    interest_accrual_convention_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.interest_accrual_conventions.id",
            ondelete="RESTRICT",
            name="fk_loan_params_interest_accrual_convention",
        ),
        nullable=False,
    )

    # Relationships
    account = db.relationship(
        "Account",
        backref=db.backref("loan_params", uselist=False, lazy="joined"),
    )

    def __repr__(self):
        return (
            f"<LoanParams account_id={self.account_id} "
            f"term={self.term_months}>"
        )
