"""The service tests' own acts, beside ``tests/_passkeys.py``'s.

A whole sign-in, a passkey written directly, and an add's row read back.
"""

import json
import os

from app.extensions import db
from app.models.passkey import Passkey
from app.models.passkey_add import PasskeyAdd
from app.services import passkey_service
from tests._passkeys import ORIGIN, T0, SoftwareAuthenticator


def sign_in(authenticator, *, state=None, options=None, **get):
    """Run a whole passkey sign-in by *authenticator*; return the passkey it signed in by.

    Args:
        authenticator: The key signing in.
        state: The ceremony's state, or ``None`` to begin one here.
        options: Its options, given with *state*.
        **get: Passed to :meth:`SoftwareAuthenticator.get`.
    """
    if state is None:
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
    response = json.dumps(authenticator.get(options, **get))
    user = passkey_service.sign_in_account(response, state=state)
    return passkey_service.complete_sign_in(user, response, origin=ORIGIN, state=state)


def written_directly(user, *, at=T0):
    """Write a passkey of *user*'s by the ORM, as no act of the service does; return it.

    For a case about the count a completion reads: no add of an account can
    keep a passkey while another of its adds is in flight (a newer add
    replaces the older, ruling plan#3543), so a passkey that arrives
    meanwhile is written here, as any other writer would leave it.
    """
    passkey = Passkey(
        user_id=user.id, credential_id=os.urandom(16),
        public_key=SoftwareAuthenticator().cose_key(), name="iPad",
        created_at=at, last_used_at=at,
    )
    db.session.add(passkey)
    db.session.flush()
    return passkey


def add_row(user):
    """Return *user*'s add in flight, read afresh, or ``None``."""
    return db.session.execute(
        db.select(PasskeyAdd)
        .where(PasskeyAdd.user_id == user.id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
