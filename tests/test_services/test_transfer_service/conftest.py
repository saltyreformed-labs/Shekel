"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_transfer_service.py``, moved verbatim by ``tools/split``.  A fixture
here reaches exactly the tests that module held, at the scope it declared.
"""

import pytest

from app.models.category import Category
from app.models.ref import Status


@pytest.fixture()
def transfer_data(app, db, seed_full_user_data):
    """Provide everything the transfer service needs for creation tests.

    Adds the default Transfers: Incoming and Transfers: Outgoing
    categories (which the conftest seed_user fixture does not include).

    Returns:
        dict with keys from seed_full_user_data plus:
        projected_status, incoming_cat, outgoing_cat.
    """
    data = seed_full_user_data
    user = data["user"]

    projected = db.session.query(Status).filter_by(name="Projected").one()

    # Add the default transfer categories the service needs.
    incoming_cat = Category(
        user_id=user.id,
        group_name="Transfers",
        item_name="Incoming",
        sort_order=90,
    )
    outgoing_cat = Category(
        user_id=user.id,
        group_name="Transfers",
        item_name="Outgoing",
        sort_order=91,
    )
    db.session.add_all([incoming_cat, outgoing_cat])
    db.session.commit()

    return {
        **data,
        "projected_status": projected,
        "incoming_cat": incoming_cat,
        "outgoing_cat": outgoing_cat,
    }
