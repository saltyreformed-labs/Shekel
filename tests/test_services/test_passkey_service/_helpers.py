"""Acts the passkey service tests share: a clock set per act, and a passkey added."""

import json
from datetime import datetime, timezone

from app.services import passkey_service
from tests._passkeys import ORIGIN, SoftwareAuthenticator
from tests._test_helpers import freeze_today

#: The moment each case starts at.  Fixed, so no case depends on the day the
#: suite runs; the service never reads the calendar.
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


class Clock:
    """The service's clock, set to the moment each act names."""

    def __init__(self, monkeypatch):
        """Hold *monkeypatch* and start at :data:`T0`."""
        self._monkeypatch = monkeypatch
        self.at(T0)

    def at(self, moment: datetime) -> None:
        """Freeze the service package's clock at *moment* (UTC)."""
        freeze_today(
            self._monkeypatch, moment.date(),
            modules=(passkey_service.__name__,),
            at_time=moment.time(),
        )


def add(user, authenticator=None, name="iPhone", **create):
    """Add a passkey to *user* through both halves of the ceremony; return it and its key.

    Args:
        user: The account adding it.
        authenticator: The key to add, or ``None`` for a new one.
        name: The name typed for it.
        **create: Passed to :meth:`SoftwareAuthenticator.create`.

    Returns:
        The new passkey row and the authenticator holding its key.
    """
    authenticator = SoftwareAuthenticator() if authenticator is None else authenticator
    options, state = passkey_service.registration_options(user, origin=ORIGIN)
    passkey = passkey_service.add_passkey(
        user, json.dumps(authenticator.create(options, **create)), name,
        origin=ORIGIN, state=state,
    )
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
