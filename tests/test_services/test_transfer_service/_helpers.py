"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_transfer_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transfer import Transfer
from app.services import transfer_service
from app.utils.dates import display_today
from tests._test_helpers import transfer_family_journal_filter
from app.models.amount_ownership import AmountOwnership


def _ledger_nets_for_transfer(transfer_id):
    """Return ``{ledger_account_id: net}`` over a transfer's posted legs.

    Reads the posted side the way the reconcile does -- summed per ledger
    account across every journal entry the transfer wrote -- so a leg that has
    been reversed to zero drops out rather than showing as two rows.

    Args:
        transfer_id: The transfer whose postings to sum.

    Returns:
        The non-zero nets, keyed by ledger account id.
    """
    rows = (
        db.session.query(Posting.ledger_account_id, Posting.amount)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(transfer_family_journal_filter(transfer_id))
        .all()
    )
    nets = {}
    for ledger_account_id, amount in rows:
        nets[ledger_account_id] = nets.get(
            ledger_account_id, Decimal("0"),
        ) + amount
    return {key: net for key, net in nets.items() if net != 0}


def _the_day_before_today():
    """Return yesterday in the owner's timezone, a day an account's books can open on.

    Money a settle dates TODAY lands on an account whose books open no later
    than this.
    """
    return display_today() - timedelta(days=1)


def _create_basic_transfer(td):
    """Helper: create a transfer using the standard test data."""
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=td["user"].id,
            from_account_id=td["account"].id,
            to_account_id=td["savings_account"].id,
            pay_period_id=td["periods"][0].id,
            scenario_id=td["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("250.00")),
            status_id=td["projected_status"].id,
            category_id=td["categories"]["Rent"].id,
        ),
    )


def _reread(xfer_id):
    """Commit, expire every loaded object, and return the transfer's row re-read.

    What a door WROTE is what the database holds after the commit, not what
    the session's in-memory object says, so an edit case reads the row this
    returns (ruling R-BAL290 part 1).
    """
    db.session.commit()
    db.session.expire_all()
    return db.session.get(Transfer, xfer_id)
