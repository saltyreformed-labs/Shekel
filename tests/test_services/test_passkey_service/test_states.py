"""A ceremony's state completes that ceremony alone.

Each state names its ceremony, and each completion refuses another's as no
state at all (``EXPIRED``), whatever the route keeps under which session key.
Each case hands a completion a state of another ceremony with an answer that
is otherwise good for it, so the state is the one thing wrong.  Before the
states named their ceremony, a registration's state reached the keeping as a
``KeyError`` (a server error), and an add's sign-in state completed a sign-in
and a re-confirm.
"""

import json

import pytest

from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from tests._passkeys import ADDED_AT, ORIGIN, Clock, SoftwareAuthenticator, add


@pytest.fixture(name="clock")
def _clock(monkeypatch):
    """The service's clock, at T0."""
    return Clock(monkeypatch)


@pytest.fixture(name="user")
def _user(bare_user):
    """The account whose ceremonies these are."""
    return bare_user["user"]


def _expired(completion):
    """Run *completion*; assert it was refused as no state of its ceremony's."""
    with pytest.raises(PasskeyRefused) as caught:
        completion()
    assert caught.value.refusal is PasskeyRefusal.EXPIRED


def _add_sign_in(user, authenticator):
    """Run an add's registration by *authenticator*; return the sign-in's options and state."""
    options, state = passkey_service.registration_options(user, origin=ORIGIN)
    return passkey_service.complete_registration(
        user, json.dumps(authenticator.create(options)), "iPhone",
        origin=ORIGIN, state=state,
    )


def test_the_keeping_refuses_a_registrations_state(user, clock):
    """Two tabs: the session key holds the second add's registration when the first's sign-in posts."""
    authenticator = SoftwareAuthenticator()
    sign_in_options, _ = _add_sign_in(user, authenticator)
    _, registration_state = passkey_service.registration_options(user, origin=ORIGIN)
    _expired(lambda: passkey_service.add_passkey(
        user, json.dumps(authenticator.get(sign_in_options)),
        origin=ORIGIN, state=registration_state,
    ))
    assert passkey_service.list_passkeys(user.id) == []


def test_the_keeping_refuses_a_sign_ins_state(user, clock):
    """A sign-in's state names no account and no new passkey."""
    authenticator = SoftwareAuthenticator()
    _add_sign_in(user, authenticator)
    options, state = passkey_service.sign_in_options(origin=ORIGIN)
    _expired(lambda: passkey_service.add_passkey(
        user, json.dumps(authenticator.get(options)), origin=ORIGIN, state=state,
    ))
    assert passkey_service.list_passkeys(user.id) == []


def test_the_registration_refuses_the_keepings_state(user, clock):
    """The add's sign-in state completes no registration."""
    authenticator = SoftwareAuthenticator()
    _, sign_in_state = _add_sign_in(user, authenticator)
    options, _ = passkey_service.registration_options(user, origin=ORIGIN)
    _expired(lambda: passkey_service.complete_registration(
        user, json.dumps(SoftwareAuthenticator().create(options)), "iPad",
        origin=ORIGIN, state=sign_in_state,
    ))


@pytest.mark.parametrize("completion", ["complete_sign_in", "complete_re_confirm"])
def test_a_sign_in_and_a_re_confirm_refuse_the_keepings_state(user, clock, completion):
    """An older passkey answers the add's sign-in, and that answer signs nothing in."""
    passkey, older = add(clock, user)
    sign_in_options, sign_in_state = _add_sign_in(user, SoftwareAuthenticator())
    response = json.dumps(older.get(sign_in_options))
    _expired(lambda: getattr(passkey_service, completion)(
        user, response, origin=ORIGIN, state=sign_in_state,
    ))
    assert passkey.last_used_at == ADDED_AT


def test_a_sign_in_refuses_a_re_confirms_state(user, clock):
    """A re-confirm's answer, with its state, signs no one in."""
    passkey, authenticator = add(clock, user)
    options, state = passkey_service.re_confirm_options(user.id, origin=ORIGIN)
    _expired(lambda: passkey_service.complete_sign_in(
        user, json.dumps(authenticator.get(options)), origin=ORIGIN, state=state,
    ))
    assert passkey.last_used_at == ADDED_AT


def test_a_re_confirm_refuses_a_sign_ins_state(user, clock):
    """A sign-in's answer, with its state, re-confirms nothing."""
    passkey, authenticator = add(clock, user)
    options, state = passkey_service.sign_in_options(origin=ORIGIN)
    _expired(lambda: passkey_service.complete_re_confirm(
        user, json.dumps(authenticator.get(options)), origin=ORIGIN, state=state,
    ))
    assert passkey.last_used_at == ADDED_AT
