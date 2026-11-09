"""The fixture every part of this package shares: a second account for a transfer to land on."""

from __future__ import annotations

import pytest

from app.extensions import db as _db
from tests._test_helpers import create_account_of_type


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so a transfer has a destination.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned account stays bound to the live session, the same pattern
    ``seed_user`` uses.  Its opening is the ``$100.00`` every case's Savings
    net starts from.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Posting Savings",
    )
    _db.session.commit()
    return acct
