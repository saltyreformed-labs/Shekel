"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_integration/test_transfer_settle_freeze.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from app.extensions import db
from app.models.transfer import Transfer
from app.services import account_service, status_seam, transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import create_transfer, open_books_before_the_first_assertion
from tests.test_integration.test_loan_transfer_live_amount._helpers import (
    _build_derived_loan_transfer,
    _side_leg,
)
from app.services.transfer_legs import parent_of


#: P&I 1,199.10 + escrow 300.00, the figure the freeze captures.
_LIVE_PITI = Decimal("1499.10")


#: The deliberately stale price the template's dormant series states.
_STALE = Decimal("1.00")


def _derived_loan_transfer(seed_user, seed_periods):
    """Return one projected loan-payment transfer.

    Built on the derive-from-loan fixture the live-override integration test
    already owns, so the two grade the same loan with the same arithmetic: a
    $200k / 6% / 360mo mortgage with $3,600/yr escrow behind a recurring
    transfer whose stated price is stale.

    It returned the transfer AND its expense shadow until plan step
    ``balance:X-bi-6-4d-2``, where a transfer became one row and the shadow
    stopped being born (ruling **R-BAL167** class 4).  Four callers read the
    shadow and every other discarded it: three read only its ``account_id``,
    the transfer's from-account, where the expense twin sat; one read the
    period it was filed in and its due date, both copied off the transfer.
    Each reads the transfer's own column now.
    """
    loan, _escrow, scenario_id, template, _rule, _periods = (
        _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
    )
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id), {p.id for p in seed_periods},
        ),
        scenario_id,
    )
    db.session.commit()

    xfer = (
        db.session.query(Transfer)
        .filter_by(to_account_id=loan.id)
        .order_by(Transfer.id)
        .first()
    )
    assert xfer is not None, "expected a generated loan-payment transfer"
    return xfer


def _plain_transfer(
    seed_user, seed_periods, amount="250.00", destination="Savings",
):
    """Return a projected checking-to-savings transfer -- NO loan behind it.

    The control shape: its template carries no ``loan_payment_settings`` row,
    so the freeze resolves to ``None`` and the row books its own estimate.  It
    is what every live transfer on production is today.

    *destination* names the receiving account because ``uq_accounts_user_name``
    is unique per owner, so a case wanting two independent transfers has to ask
    for two destinations.
    """
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name=destination,
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    db.session.flush()
    # Its BOOKS open before anything this fixture dates (plan step
    # X-f3c-2b, ruling **R-HG**): ``create_account`` opens them on the day it
    # asserts -- the owner's today -- and this suite settles on or before it.
    open_books_before_the_first_assertion(db.session, savings)
    return create_transfer(
        seed_user, db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal(amount),
    )


def _sides(xfer_id):
    """Return *xfer_id*'s two side accounts, the from (expense) side first.

    What this file asks a settle about since plan step
    ``balance:X-bi-6-4d-2`` (ruling **R-BAL167** class 4): a side's payment
    record hangs off the TRANSFER and nothing keeps a twin's status, day or
    figure, so each side is read as the transfer and its record, never as its
    twin row (the deleted ``_shadows``, which went with its last caller at plan
    step ``balance:X-bi-6-4d-2``, where no twin is born).
    """
    xfer = db.session.get(Transfer, xfer_id)
    return (xfer.from_account_id, xfer.to_account_id)


def _side_settlement(xfer_id, account_id):
    """Return the settlement *xfer_id*'s side on *account_id* records, or ``None``.

    ``status_seam.recorded_settlement`` of :func:`_side_leg`'s parent (ruling
    **plan#1983**): the record's figure and who wrote it.  It read the
    side-shaped twin ``recorded_leg_settlement`` until that ruling, and the
    ``recorded_settlement(shadow)`` before plan step ``balance:X-bi-6-4d-2``
    (ruling **R-BAL167** class 4).
    """
    return status_seam.recorded_settlement(
        parent_of(_side_leg(xfer_id, account_id)),
    )
