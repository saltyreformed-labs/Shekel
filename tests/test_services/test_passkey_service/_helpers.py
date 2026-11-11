"""Acts the passkey service tests share: a clock set per act, and a passkey added."""

import json
import os
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.passkey import Passkey
from app.models.passkey_add import PasskeyAdd
from app.services import passkey_service
from tests._passkeys import ORIGIN, SoftwareAuthenticator
from tests._test_helpers import freeze_today

#: The moment each case starts at.  Fixed, so no case depends on the day the
#: suite runs; the service never reads the calendar.
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

#: When :func:`add` adds a passkey unless told otherwise: a minute before
#: :data:`T0`.  A kept passkey has signed in once, at its add (ruling
#: plan#3525), and a challenge issued at or before a passkey's last use is
#: refused, so a ceremony a case begins at T0 must be issued after the add.
ADDED_AT = T0 - timedelta(minutes=1)

#: The module whose clock the service reads: the package's one read of it.
#: Named as text, so a clock that moved modules fails every case at once.
_CLOCK_MODULE = "app.services.passkey_service._ceremony"


class Clock:
    """The service's clock, set to the moment each act names.

    Attributes:
        moment: The moment it is set to.
    """

    def __init__(self, monkeypatch):
        """Hold *monkeypatch* and start at :data:`T0`."""
        self._monkeypatch = monkeypatch
        self.moment = T0
        self.at(T0)

    def at(self, moment: datetime) -> None:
        """Freeze the service's clock at *moment* (UTC)."""
        self.moment = moment
        freeze_today(
            self._monkeypatch, moment.date(),
            modules=(_CLOCK_MODULE,),
            at_time=moment.time(),
        )


def add(clock, user, authenticator=None, name="iPhone", *, at=ADDED_AT, **create):
    """Add a passkey to *user* at *at* through all three acts; return it and its key.

    The registration's options, its completion, and the sign-in by the new
    passkey that keeps it, all at *at*.  *clock* is put back where it stood,
    so the case goes on at its own moment.

    Args:
        clock: The case's :class:`Clock`.
        user: The account adding it.
        authenticator: The key to add, or ``None`` for a new one.
        name: The name typed for it.
        at: The moment of the add, which is the passkey's first use.
        **create: Passed to :meth:`SoftwareAuthenticator.create`.

    Returns:
        The new passkey row and the authenticator holding its key.
    """
    authenticator = SoftwareAuthenticator() if authenticator is None else authenticator
    resume = clock.moment
    clock.at(at)
    options, state = passkey_service.registration_options(user, origin=ORIGIN)
    sign_in_options, sign_in_state = passkey_service.complete_registration(
        user, json.dumps(authenticator.create(options, **create)), name,
        origin=ORIGIN, state=state,
    )
    passkey = passkey_service.add_passkey(
        user, json.dumps(authenticator.get(sign_in_options)),
        origin=ORIGIN, state=sign_in_state,
    )
    clock.at(resume)
    return passkey, authenticator


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
    user = passkey_service.sign_in_account(response)
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
