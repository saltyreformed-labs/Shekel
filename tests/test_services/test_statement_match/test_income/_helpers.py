"""The helpers and constants two or more of this package's parts read: the module-level helpers of
``tests/test_services/test_statement_match/test_income.py``, moved verbatim by ``tools/split``.
"""

from app.extensions import db
from app.models.transaction import Transaction
from app.services.statement_match import RuleView, IncomeCreation, record_income_from_line

from tests.test_services.test_statement_match._builders import a_bank_line, a_scope, an_import


def _a_deposit(seed_user, amount="0.15", **kwargs):
    """Stage one unexplained line of money coming IN.

    Args:
        seed_user: The seeded user bundle.
        amount: Signed, POSITIVE into the account.
        **kwargs: Passed through to :func:`a_bank_line`.

    Returns:
        The staged line.
    """
    return a_bank_line(
        seed_user, an_import(seed_user), amount=amount,
        description="DIVIDEND EARNED (Dividend Earned)",
        merchant=kwargs.pop("merchant", "Dividend Earned"), **kwargs,
    )


def _record(seed_user, line, account=None):
    """Run the door for one line, through a scope derived at the point of use.

    **The RULE VIEW is derived at the point of use too**, for the reason the
    scope is (:func:`a_scope`): the door reads it to decide what the deposit is
    filed under (**R-HT(a)**), and a stale one would answer with rules a case
    had just replaced.  ``apply_reviewed`` derives it once per batch and
    threads it; a case calling this door directly stands in for that caller.

    Args:
        seed_user: The seeded user bundle.
        line: The bank line to record.
        account: The account being reviewed, defaulting to the seeded one.

    Returns:
        The :class:`~app.services.statement_match.RecordedIncome`.
    """
    scope = a_scope(seed_user, account)
    return record_income_from_line(
        IncomeCreation(line_id=line.id), scope,
        RuleView.build(scope.owner_id, scope.account_id),
    )


def _rows(seed_user):
    """Return every transaction on the seeded account, newest id last.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The rows.
    """
    return (
        db.session.query(Transaction)
        .filter(Transaction.account_id == seed_user["account"].id)
        .order_by(Transaction.id)
        .all()
    )
