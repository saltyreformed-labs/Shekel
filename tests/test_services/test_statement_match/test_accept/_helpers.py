"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_accept.py``, moved verbatim by ``tools/split``.
"""

from app.services import statement_match

from app.services.ledger_lines import linked_key
from tests._ledger_reads import family_movement_ids, movement_nets_by_date

from tests.test_services.test_statement_match._builders import a_scope, a_submission


def _posted_cash_by_day(db, txn, account):
    """Return what *txn* books on *account*'s own line, netted per civil day.

    Args:
        db: The session fixture.
        txn: The transaction whose movements to read.
        account: The account whose own line to read.

    Returns:
        ``{entry_date: net}`` over the days this row moves cash, a day the
        money left holding nothing.
    """
    return movement_nets_by_date(
        db.session, family_movement_ids(txn), linked_key(account), txn.scenario_id,
    )


def _submit(
    seed_user, lines=(), transactions=(), entries=(), residual=None,
    transfers=(),
):
    """Accept a match naming exactly these subjects.

    Args:
        seed_user: The seeded user bundle.
        lines: Bank line rows.
        transactions: Transaction rows.
        entries: Purchase rows.
        residual: The difference the screen showed and the owner agreed to
            record, or ``None`` (plan step ``bank_import:X-f6d-4``).
        transfers: Transfer rows, each ticked as its leg on this account
            (leaf ``balance:X-bi-6-4c-1``).

    Returns:
        The :class:`~app.services.statement_match.AcceptedMatch`.
    """
    # DERIVED HERE, so every call sees the rows this test has staged.  A
    # scope built once per test would be a snapshot older than the fixture.
    # **The SAME scope builds the submission and applies it**, which is the
    # two-moment flow the screen has: the reviewed state a tick carries is read
    # off the pass that rendered it (finding **N-336**).
    scope = a_scope(seed_user)
    return statement_match.accept_match(
        a_submission(
            scope, lines=lines, transactions=transactions, entries=entries,
            residual=residual, transfers=transfers,
        ),
        scope,
    )
