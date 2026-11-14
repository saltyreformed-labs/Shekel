"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_property.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.ref import AccountType
from app.services import account_service


def _property_type_id(db):
    """Return the seeded Property account-type id."""
    return db.session.query(AccountType).filter_by(name="Property").one().id


def _make_property(
    db, seed_user, periods, name="House", rate=None,
    anchor_balance=Decimal("400000.00"),
):
    """Create a Property via the service (no params unless ``rate`` given).

    The default $400,000 anchor posts a Step-5 opening correction at create
    time, which makes the account archive-only under hard-delete Guard 5;
    the hard-delete test passes ``Decimal("0.00")`` (a zero opening books
    nothing, keeping the row deletable).
    """
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=_property_type_id(db),
            name=name,
            anchor_balance=anchor_balance,
        ),
    )
    db.session.add(acct)
    db.session.flush()
    if rate is not None:
        db.session.add(AssetAppreciationParams(
            account_id=acct.id, annual_appreciation_rate=rate,
        ))
    db.session.commit()
    return acct
