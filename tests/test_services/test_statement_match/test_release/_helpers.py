"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_release.py``, moved verbatim by ``tools/split``.
"""

from datetime import timedelta

from app.extensions import db
from app.services import balance_at, statement_match
from app.services.balance_at import BalanceContext
from app.services.statement_match import NewEnvelope, PurchaseCreation
from app.services.statement_match import _create

from tests._ledger_reads import account_net

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_answers,
    an_import,
)


def _record(seed_user, line, minted=None, **destination):
    """Record *line* as a purchase, into whichever destination is named.

    Args:
        seed_user: The seeded user bundle.
        line: The recorded bank line.
        minted: What this REQUEST has already created; ``None`` gives each call
            its own empty registry, which is the HAND path -- one line, one
            request.  A case about a SWEEP passes one registry to several
            calls, because that is what one press is.
        **destination: The ``PurchaseCreation`` destination fields.

    Returns:
        The :class:`~app.services.statement_match.CreatedPurchase`.
    """
    return statement_match.create_purchase_from_line(
        PurchaseCreation(line_id=line.id, **destination),
        a_scope(seed_user),
        minted if minted is not None else _create.MintedEnvelopes.none_yet(),
        an_answers(seed_user),
        applied_by_rule=False,
    )


def _a_new_envelope(seed_user, name="Walmart"):
    """Return the NEW-ENVELOPE answer these tests submit."""
    return NewEnvelope(
        name=name, category_id=seed_user["categories"]["Groceries"].id,
    )


def _release(seed_user, match_id):
    """Release one match through the door under test."""
    return statement_match.release_match(
        match_id, seed_user["user"].id, seed_user["account"].id,
    )


def _balance_on(seed_user, day):
    """Return the checking account's balance as of *day*."""
    ctx = BalanceContext(
        user_id=seed_user["user"].id,
        scenario=seed_user["scenario"], as_of=day,
    )
    return balance_at.balance_at(seed_user["account"], ctx, day)


def _posted_total(seed_user):
    """Return what the posted ledger says this account holds."""
    return account_net(db.session, seed_user["account"].id, seed_user["scenario"].id)


def _a_swipe(seed_user, *, amount="-57.96", offset=5, sequence=0):
    """Return one unexplained card-swipe line, five days into the period."""
    return a_bank_line(
        seed_user, an_import(seed_user), amount=amount,
        posted_on=seed_user["bootstrap_period"].start_date
        + timedelta(days=offset),
        description="POINT OF SALE DEBIT L340 WAL-MART",
        sequence_in_group=sequence,
    )


def _a_submission(seed_user, line, txn):
    """Return a one-to-one submission naming *line* and *txn*."""
    return a_submission(
        a_scope(seed_user), lines=[line], transactions=[txn],
    )
