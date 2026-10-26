"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_integration/test_a_transfer_shadow_is_derived.py``, moved verbatim by
``tools/split``.
"""

from decimal import Decimal

from app.extensions import db as _db
from app.models.transfer import Transfer
from app.services import account_service
from tests._test_helpers import create_transfer


#: P&I 1,199.10 + escrow 300.00 on the seeded $200k / 6% / 360mo mortgage.
_CONTRACT = Decimal("1499.10")


#: What the owner types instead -- $174.10 SHORT of the installment.
_TYPED = Decimal("1325.00")


def _plain_transfer(seed_user, seed_periods, amount="250.00"):
    """A checking-to-savings transfer -- no loan, no definition behind it.

    It was ``_plain_pair`` and returned the transfer AND its two shadow legs
    until plan step balance:X-bi-6-4d-2, where a transfer became one row
    (ruling **R-BAL167** class 4).
    """
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name="Sinking Fund",
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    _db.session.flush()
    xfer = create_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal(amount),
    )
    _db.session.flush()
    return xfer


def _reread(xfer_id):
    """Return the transfer *xfer_id* as the database now holds it."""
    _db.session.expire_all()
    return _db.session.get(Transfer, xfer_id)
