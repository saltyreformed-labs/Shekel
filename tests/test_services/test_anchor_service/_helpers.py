"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_anchor_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import InterestAccrualConventionEnum, LoanAnchorSourceEnum
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.models.transaction_entry import TransactionEntry
from app.services import account_service
from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    insert_origination_rate,
    make_expense_template,
    settle_day_columns,
)


def _make_checking_account(seed_user, anchor_balance="1000.00", observed_on=None):
    """Create a fresh Checking account carrying one balance assertion.

    It took a ``periods`` argument, to anchor the account at ``periods[0]``,
    until ruling R-EH deleted ``AccountSpec.anchor_period_id`` (plan step
    X-f1c3c).  An assertion is a day and a balance now; the caller's periods
    have nothing to say about it.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        anchor_balance: The opening balance, as a string.
        observed_on: The civil day that opening was true.  ``None`` takes the
            factory's default of today -- which leaves NO room behind the
            opening, so a caller grading a BACK-DATED assertion must pass one
            (plan step X-f1c4c).
    """
    checking_type = db.session.query(AccountType).filter_by(
        name="Checking",
    ).one()
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Helper Checking",
            anchor_balance=Decimal(anchor_balance),
            observed_on=observed_on,
        ),
    )


def _make_savings_account(seed_user, anchor_balance="500.00"):
    """Create a fresh Savings account carrying one balance assertion.

    See :func:`_make_checking_account` for why it no longer takes ``periods``.
    """
    savings_type = db.session.query(AccountType).filter_by(
        name="Savings",
    ).one()
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Helper Savings",
            anchor_balance=Decimal(anchor_balance),
        ),
    )


def _make_projected_expense_with_past_dated_entry(seed_user, period, amount):
    """Create a Projected expense with one unobserved past-dated debit entry.

    Used by the two tests that pin what a true-up does NOT do: its
    ``settled_on`` starts NULL -- the bank has not been seen to take it -- and
    it must still be NULL afterwards.  Returns the
    :class:`TransactionEntry` so the caller can re-read that column.

    The envelope is the engine's own row of a priced, repeating definition
    (:func:`generate_row_of`, plan step balance:X-cf) rather than a copy built
    by hand; the purchase is then recorded against it.
    """
    template = make_expense_template(
        db.session, seed_user, amount="500.00",
        name="Groceries", category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, period)

    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="Past-dated debit",
        purchased_on=date.today() - timedelta(days=1),
        is_credit=False,
        **settle_day_columns(None),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


# ── Loan Anchor Trueup ────────────────────────────────────────────────


def _make_loan_account(seed_user, name="Helper Loan",
                       original_principal="20000.00",
                       interest_rate="0.05000",
                       term_months=60,
                       origination_date=None):
    """Create a fresh Auto Loan account + LoanParams + origination event.

    Builds the minimal loan inventory the loan-anchor true-up tests
    need: an account, a :class:`LoanParams` row, and an origination
    :class:`LoanAnchorEvent`.  Mirrors the production setup in
    :func:`app.routes.loan.params.create_params` so the same anchor-event
    invariants the resolver relies on are satisfied.

    Returns the Account ORM instance.
    """
    if origination_date is None:
        origination_date = date.today() - timedelta(days=365)

    auto_type = db.session.query(AccountType).filter_by(
        name="Auto Loan",
    ).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=auto_type.id,
            name=name,
            anchor_balance=Decimal(original_principal),
        ),
    )
    db.session.flush()

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal(original_principal),
        interest_accrual_convention_id=ref_cache.interest_accrual_convention_id(
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ),
        term_months=term_months,
        origination_date=origination_date,
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()

    insert_origination_rate(params, Decimal(interest_rate))

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=origination_date,
        anchor_balance=Decimal(original_principal),
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
    db.session.commit()
    return account
