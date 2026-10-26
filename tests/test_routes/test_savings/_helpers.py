"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_savings.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.savings_goal import SavingsGoal

from tests._test_helpers import create_account_of_type, create_loan_account


# ── Helpers ──────────────────────────────────────────────────────────


def _create_savings_account(
    seed_user, name="Savings",
    anchor_balance=Decimal("5000.00"),
):
    """Create a savings account for the test user.

    Args:
        seed_user: The seed user fixture dict.
        name: Account display name (default "Savings").
        anchor_balance: Origination anchor balance (default
            $5,000.00).  Pass an explicit value when the test
            assertion depends on a non-default anchor; this routes
            through ``account_service.create_account`` so the dated
            ``AccountAnchorHistory`` SoT (E-19, Commit 4) and the
            cache columns agree from t0.  Required by Commit 6:
            ``cash_ledger.resolve_anchor`` reads history, so
            mutating only the cache columns after creation no longer
            propagates to ``/savings``.
        anchor_period_id: Pay period to anchor the new account
            against.  ``None`` falls back to
            ``account_service.resolve_anchor_period_id`` (the
            user's earliest pay period at create time).

    Returns:
        Account: the new savings account, COMMITTED.  Committed rather than
        flushed (plan step balance:X-i3): the callers that go on to issue a
        request need the account to exist as far as that request is concerned,
        and a request holds a transaction of its own in which an uncommitted
        row does not.
    """
    # Through the shared factory rather than the ``AccountType`` lookup +
    # ``AccountSpec`` block spelled by hand, which is the same four lines it
    # is.  It also opens the account's BOOKS before anything a fixture dates
    # (plan step X-f3c-2b, ruling **R-HG**) -- what the expenses below need,
    # since they settle in the seeded periods rather than on the day
    # ``create_account`` would otherwise open the books.
    acct = create_account_of_type(
        seed_user, db.session, "Savings", name,
        anchor_balance=anchor_balance,
    )
    db.session.commit()
    return acct


def _create_goal(seed_user, account, name="Vacation Fund",
                 target_amount=Decimal("10000.00"), target_date=None):
    """Create a savings goal for the test user.

    Returns:
        SavingsGoal: the new goal.
    """
    goal = SavingsGoal(
        user_id=seed_user["user"].id,
        account_id=account.id,
        name=name,
        target_amount=target_amount,
        target_date=target_date or date(2027, 6, 1),
    )
    db.session.add(goal)
    db.session.commit()
    return goal


# ── Paid-Off Badge Tests (Commit 5.9-2) ──────────────────────────────


def _create_small_loan(seed_user, name="Test Loan",
                       principal=Decimal("1000.00"),
                       rate=Decimal("0.05000"), term=24):
    """Create a small loan with LoanParams for paid-off badge testing.

    Routes through the ONE shared loan builder
    (:func:`tests._test_helpers.create_loan_account`), whose defaults are this
    loan: an Auto Loan originated 2026-01-01 with payment_day 1, so remaining
    months is comfortably positive (~21 from April 2026).  The factory opens
    the loan's genesis posting ledger in the same transaction as its
    ``LoanParams``, exactly as ``loan.create_params`` does in production; the
    hand-rolled block this replaced never did, so these cards rendered a loan
    in a state production cannot produce.
    """
    return create_loan_account(
        seed_user, db.session, name=name, principal=principal,
        rate=rate, term=term,
    )
