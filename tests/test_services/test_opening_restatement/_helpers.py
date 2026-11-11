"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_opening_restatement.py``, moved verbatim by ``tools/split``.
"""

from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.extensions import db as _db
from app.services import match_withdrawal, movement_removal
from app.services.ledger_lines import linked_key
from tests._ledger_reads import source_lines
from tests._test_helpers import create_account_of_type, create_settled_cash_transaction


_ONE_DAY = timedelta(days=1)


#: Every ``budget.account_openings`` row an account carries, oldest RECORDING
#: first, read straight out of SQL.  The append-only claim is about rows that
#: SURVIVE a restatement, so the count and the order are read rather than
#: inferred from what the loader hands back.
_ALL_OPENINGS = (
    "SELECT id, opened_on, opening_equity, source_id FROM "
    "budget.account_openings WHERE account_id = :a ORDER BY created_at, id"
)


#: The constraint's own governing-day function, asked directly.  Going through
#: it rather than re-writing its ``ORDER BY`` here is the point: a hand-written
#: query would agree with the Python loader while the function the CONSTRAINT
#: calls disagreed with both.
_OPENED_ON_IN_SQL = "SELECT budget.account_books_opened_on(:a)"


def _openings(account):
    """Return *account*'s opening rows as tuples, oldest recorded first."""
    return _db.session.execute(
        sa.text(_ALL_OPENINGS), {"a": account.id},
    ).fetchall()


def _cash_account(seed_user, name, *, anchor_balance=Decimal("1000.00")):
    """Return a fresh Checking account with a known opening.

    **It already carries TWO opening rows, and a test must count from there
    rather than from one.**  ``create_account_of_type`` writes the origination
    and then re-opens the books before anything the fixture could date
    (``open_books_before_the_first_assertion``), because an opening equity is
    the closing balance for its own day and every settle the fixture then
    records has to land after it.  Baselines here are therefore RELATIVE:
    :func:`_opening_count` before, compared after.
    """
    return create_account_of_type(
        seed_user, _db.session, "Checking", name,
        anchor_balance=anchor_balance,
    )


def _opening_count(account):
    """Return how many opening rows *account* carries right now."""
    return len(_openings(account))


def _linked_ledger_total(scenario_id, account, source, entry_date=None):
    """Return the net of the account's OWN line for one posting source.

    **Only the linked key, never the counter**, and that distinction is what a
    first version of these cases got wrong: an anchor correction books a
    balanced PAIR, and both legs name the same account, so summing over the
    account returns ``0.00`` for every state and the assertion passes on the
    broken tree and on the fixed one alike.

    Args:
        scenario_id: The scenario.
        account: The account whose own line to total.
        source: The :class:`~app.enums.PostingSourceEnum` member to restrict to.
        entry_date: One civil day, or ``None`` for every day.

    Returns:
        The signed ``Decimal`` net of those lines.
    """
    return sum(
        (
            line.amount
            for line in source_lines(_db.session, linked_key(account), scenario_id, source)
            if entry_date is None or line.entry_date == entry_date
        ),
        Decimal("0.00"),
    )


def _deleted_as_the_door_leaves_it(seed_user, db, period, account, name):
    """A settled $25.00 row, its payment taken off, then hidden: a delete's result.

    The row keeps its own recorded day, which is the movement the boundary
    counts; the removal act runs first because the database refuses a row
    hidden while it holds a payment (ruling **R-CC92**).

    Returns:
        The hidden row, holding nothing.
    """
    txn = create_settled_cash_transaction(
        seed_user, period, Decimal("25.00"),
        account=account, name=name,
    )
    movement_removal.remove_movements(
        list(txn.entries), seed_user["user"].id,
        because=match_withdrawal.LEFT_THE_BOOKS,
        press=None,
    )
    txn.is_deleted = True
    db.session.flush()
    assert not txn.entries and txn.settled_on is not None
    return txn
