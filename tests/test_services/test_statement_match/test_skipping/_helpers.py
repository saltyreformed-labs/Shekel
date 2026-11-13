"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_skipping.py``, moved verbatim by
``tools/split``.
"""

from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.enums import StatementBalanceEvidenceEnum
from app.services import bank_agreement
from app.services.balance_at import BalanceContext
from app.services.statement_match import Tab, reconcile_page, skip_line
from tests._ledger_reads import nets_by_key_and_day

from tests.test_services.test_statement_match._builders import a_scope


#: The evidence class an anchored import states, so the agreement can price a
#: bank balance at all.  The cases that need a REAL comparison use it; the rest
#: pass ``None`` for the agreement, which is the state of an account no import
#: has anchored.
_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN


def _real_agreement(seed_user):
    """Return the account's comparison, built from the DATABASE.

    **Built rather than injected**, which is the whole correction two
    adversarial reviews made on 2026-09-02: a hand-made
    :class:`~app.services.bank_agreement.BankAgreement` passed to both sides of
    a before/after comparison is an equality between two reads of one constant.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The :class:`~app.services.bank_agreement.BankAgreement`.
    """
    return bank_agreement.bank_agreement(
        seed_user["account"], BalanceContext.build(seed_user["user"].id),
    )


def _day_of(agreement, on):
    """Return the one compared day for *on*.

    Args:
        agreement: The :class:`~app.services.bank_agreement.BankAgreement`.
        on: The civil day.

    Returns:
        Its :class:`~app.services.bank_agreement.AgreementDay`.
    """
    return next(row for row in agreement.days if row.day == on)


def _real_hero(seed_user):
    """Return the Reconcile hero over a REAL agreement and a fresh pass.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The :class:`~app.services.statement_match._hero.Hero`.
    """
    return reconcile_page(
        a_scope(seed_user), _real_agreement(seed_user), Tab.TO_EXPLAIN,
    ).hero


def _owner(seed_user):
    """Return the seeded owner's id and account id, as the doors take them.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        ``(owner_id, account_id)``.
    """
    return seed_user["user"].id, seed_user["account"].id


def _skip(seed_user, line):
    """Record a skip of *line* through the door under test.

    Args:
        seed_user: The seeded user bundle.
        line: The bank line to dispose of.

    Returns:
        The :class:`~app.services.statement_match.SkippedLine`.
    """
    owner_id, account_id = _owner(seed_user)
    return skip_line(line.id, owner_id, account_id)


def _money_snapshot(db, seed_user):
    """Return how many rows the two money tables hold, and the ledger, right now.

    **The two the app records a movement in**, a transaction and a purchase against one,
    COUNTED: the claim is that this door writes NONE of them, an absence a count states
    exactly and a field comparison only for the rows it looked at.  Beside them the
    worked-out ledger, net per chart account and day, which any record that books money moves.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle, whose scenario's ledger is read.

    Returns:
        A dict of table name to row count, and ``"ledger"`` to the nets.
    """
    return {
        "transactions": db.session.query(Transaction).count(),
        "transaction_entries": db.session.query(TransactionEntry).count(),
        "ledger": nets_by_key_and_day(db.session, seed_user["scenario"].id),
    }
