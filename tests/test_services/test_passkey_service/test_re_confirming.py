"""Re-confirming with a passkey: only the signed-in account's own, used once."""

import json
from datetime import timedelta

import pytest

from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from tests._passkeys import ORIGIN, unb64url
from tests.test_services.test_passkey_service._helpers import T0, Clock, add


@pytest.fixture(name="clock")
def _clock(monkeypatch):
    """The service's clock, at T0."""
    return Clock(monkeypatch)


@pytest.fixture(name="user")
def _user(bare_user):
    """The signed-in account re-confirming."""
    return bare_user["user"]


def _re_confirm(user, response, *, state):
    """Complete *user*'s re-confirm with *response*."""
    return passkey_service.complete_re_confirm(
        user, json.dumps(response), origin=ORIGIN, state=state,
    )


def test_the_options_list_only_the_accounts_own_passkeys(user, second_user, clock):
    """Two of the account's, one of another's: the two are allowed."""
    first, _ = add(user)
    second, _ = add(user)
    add(second_user["user"])
    options, _ = passkey_service.re_confirm_options(user.id, origin=ORIGIN)
    assert [
        unb64url(each["id"]) for each in options["publicKey"]["allowCredentials"]
    ] == [first.credential_id, second.credential_id]
    assert options["publicKey"]["userVerification"] == "required"


def test_the_accounts_passkey_re_confirms_and_records_its_use(user, clock):
    """Last used moves to the completion's moment."""
    passkey, authenticator = add(user)
    options, state = passkey_service.re_confirm_options(user.id, origin=ORIGIN)
    clock.at(T0 + timedelta(seconds=40))
    confirmed = _re_confirm(user, authenticator.get(options), state=state)
    assert confirmed.id == passkey.id
    assert confirmed.last_used_at == T0 + timedelta(seconds=40)


def test_no_user_handle_is_accepted_when_the_account_was_named(user, clock):
    """WebAuthn: a ceremony that named its account checks a handle only where one is sent."""
    _, authenticator = add(user)
    options, state = passkey_service.re_confirm_options(user.id, origin=ORIGIN)
    clock.at(T0 + timedelta(seconds=1))
    assert _re_confirm(user, authenticator.get(options, user_handle=None), state=state)


def test_another_accounts_handle_is_refused(user, second_user, clock):
    """A handle that is sent must be the account's."""
    _, authenticator = add(user)
    options, state = passkey_service.re_confirm_options(user.id, origin=ORIGIN)
    response = authenticator.get(options, user_handle=second_user["user"].id.to_bytes(8, "big"))
    with pytest.raises(PasskeyRefused) as caught:
        _re_confirm(user, response, state=state)
    assert caught.value.refusal is PasskeyRefusal.WRONG_USER_HANDLE


def test_another_accounts_passkey_is_refused(user, second_user, clock):
    """A passkey of another account re-confirms nothing here, and its use is not recorded."""
    theirs, authenticator = add(second_user["user"])
    options, state = passkey_service.re_confirm_options(user.id, origin=ORIGIN)
    with pytest.raises(PasskeyRefused) as caught:
        _re_confirm(user, authenticator.get(options), state=state)
    assert caught.value.refusal is PasskeyRefusal.UNKNOWN_CREDENTIAL
    assert theirs.last_used_at is None


def test_the_same_answer_sent_twice_is_refused(user, clock):
    """Re-confirmed at T0 + 1 s; the copy of that answer at T0 + 2 s is refused."""
    _, authenticator = add(user)
    options, state = passkey_service.re_confirm_options(user.id, origin=ORIGIN)
    response = authenticator.get(options)
    clock.at(T0 + timedelta(seconds=1))
    _re_confirm(user, response, state=state)
    clock.at(T0 + timedelta(seconds=2))
    with pytest.raises(PasskeyRefused) as caught:
        _re_confirm(user, response, state=state)
    assert caught.value.refusal is PasskeyRefusal.REPLAYED


def test_an_expired_challenge_is_refused(user, clock):
    """Five minutes to the microsecond."""
    _, authenticator = add(user)
    options, state = passkey_service.re_confirm_options(user.id, origin=ORIGIN)
    clock.at(T0 + timedelta(minutes=5))
    with pytest.raises(PasskeyRefused) as caught:
        _re_confirm(user, authenticator.get(options), state=state)
    assert caught.value.refusal is PasskeyRefusal.EXPIRED
