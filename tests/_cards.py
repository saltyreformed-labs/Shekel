"""A credit card for a test to put a row on (plan task plan#2929).

A Credit press puts the row on the owner's card, so every test of the press
needs one.  Many files build their own (``_card`` in a dozen of them); this is
the one the Credit press's tests share.
"""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from tests._test_helpers import create_account_of_type


def add_card(seed_user, name: str = "Visa") -> Account:
    """Return a new active credit card of the ``seed_user`` owner's, owing nothing.

    Its books open before any day a fixture could date a row
    (``create_account_of_type``), so a press on a past row is not refused
    as dated inside its opening.

    Args:
        seed_user: The ``seed_user`` fixture dict (the owner).
        name: The card's name.

    Returns:
        The card :class:`~app.models.account.Account`, flushed.
    """
    return create_account_of_type(
        seed_user, db.session, "Credit Card", name, anchor_balance=Decimal("0.00"),
    )
