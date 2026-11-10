"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_transfer_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.transfer import Transfer
from app.services import transfer_service
from app.utils.dates import display_today
from tests._ledger_reads import scenario_lines, transfer_family_movement_ids
from app.models.amount_ownership import AmountOwnership


def _ledger_nets_for_transfer(transfer_id):
    """Return ``{chart key: net}`` over the lines a transfer's records book.

    Summed per chart account across both sides' records, so a key both sides
    cancel on (Transfers in transit, once both have cleared) drops out rather
    than showing as a $0.00 line.  A loan payment's split comes from the loan's
    walk and no record, so it is not among them.

    Args:
        transfer_id: The transfer whose lines to sum.

    Returns:
        The non-zero nets, keyed by :class:`~app.services.ledger_lines.ChartKey`.
    """
    records = set(transfer_family_movement_ids(transfer_id))
    scenario_id = db.session.get(Transfer, transfer_id).scenario_id
    nets = {}
    for line in scenario_lines(db.session, scenario_id):
        if line.movement_id in records:
            nets[line.key] = nets.get(line.key, Decimal("0")) + line.amount
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
