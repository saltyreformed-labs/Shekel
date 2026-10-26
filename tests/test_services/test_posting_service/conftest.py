"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_services/test_posting_service.py``, moved verbatim by ``tools/split``.  A fixture
here reaches exactly the tests that module held, at the scope it declared.
"""

from __future__ import annotations

import pytest

from app.extensions import db as _db
from tests._test_helpers import create_account_of_type


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so a transfer has a destination.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned account stays bound to the live session, the same pattern
    ``seed_user`` uses.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Posting Savings",
    )
    _db.session.commit()
    return acct
