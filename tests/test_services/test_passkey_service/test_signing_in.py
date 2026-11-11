"""Signing in with a passkey: the account it names, the answer checked, and its one use."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models.passkey import Passkey
from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from tests._passkeys import BE, BS, ORIGIN, RP_ID, UP, UV, SoftwareAuthenticator, padding
from tests.test_services.test_passkey_service._helpers import T0, Clock, add, sign_in

_ONE_MICROSECOND = timedelta(microseconds=1)


@pytest.fixture(name="clock")
def _clock(monkeypatch):
    """The service's clock, at T0."""
    return Clock(monkeypatch)


@pytest.fixture(name="user")
def _user(bare_user):
    """The account signing in."""
    return bare_user["user"]


def _refused(user, response, *, state):
    """Complete a sign-in to *user* with *response*; return the refusal it raised."""
    with pytest.raises(PasskeyRefused) as caught:
        passkey_service.complete_sign_in(
            user, json.dumps(response), origin=ORIGIN, state=state,
        )
    return caught.value


class TestTheOptions:
    """A sign-in names no account."""

    def test_no_credential_is_listed_and_the_person_must_be_verified(self, clock):
        """The device offers whichever passkey it holds for this site."""
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        assert options["publicKey"] == {
            "challenge": state["challenge"],
            "timeout": 270_000,
            "rpId": RP_ID,
            "userVerification": "required",
        }
        assert state == {"challenge": state["challenge"], "issued_at": T0.isoformat()}


class TestTheAccount:
    """The read before the lock: the account the passkey names, and no passkey."""

    def test_the_passkey_names_its_account_and_is_not_loaded(self, user, clock):
        """The account comes back, and no passkey row is loaded by the read.

        Counted by ``loaded_as_persistent`` events, not the identity map, which
        holds weak references: a passkey loaded and dropped would leave it.
        The completion that follows loads one, so the count is not deaf.
        """
        _, authenticator = add(user)
        user_id = user.id
        db.session.commit()
        db.session.expunge_all()
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        response = json.dumps(authenticator.get(options))
        loaded = []
        session = db.session()

        def count(_session, instance):
            loaded.append(type(instance))

        event.listen(session, "loaded_as_persistent", count)
        try:
            found = passkey_service.sign_in_account(response)
            assert found.id == user_id
            assert Passkey not in loaded
            passkey_service.complete_sign_in(found, response, origin=ORIGIN, state=state)
            assert Passkey in loaded
        finally:
            event.remove(session, "loaded_as_persistent", count)

    @pytest.mark.parametrize("response", [
        "not json",
        json.dumps({"type": "public-key", "rawId": 5, "response": {}}),
        json.dumps([1, 2]),
    ])
    def test_a_response_that_is_not_a_credential_names_no_one(self, user, clock, response):
        """Refused alike with an unknown id: the caller cannot tell them apart."""
        assert passkey_service.sign_in_account(response) is None

    def test_an_unknown_credential_names_no_one(self, user, clock):
        """A key no account added."""
        add(user)
        options, _ = passkey_service.sign_in_options(origin=ORIGIN)
        response = SoftwareAuthenticator().get(options, user_handle=None)
        assert passkey_service.sign_in_account(json.dumps(response)) is None


class TestASignIn:
    """A correct sign-in, and what it records."""

    def test_an_apple_passkey_signs_in_and_records_its_use(self, user, clock):
        """Counter 0 stays 0; last used moves to the completion's moment."""
        passkey, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        clock.at(T0 + timedelta(seconds=30))
        signed = sign_in(authenticator, state=state, options=options)
        assert signed.id == passkey.id
        db.session.expire_all()
        row = db.session.get(Passkey, passkey.id)
        assert row.last_used_at == T0 + timedelta(seconds=30)
        assert row.sign_count == 0

    def test_an_apple_passkey_signs_in_again_with_its_counter_at_zero(self, user, clock):
        """Both counters 0, every time: not a copied key."""
        _, authenticator = add(user)
        clock.at(T0 + timedelta(minutes=1))
        sign_in(authenticator)
        clock.at(T0 + timedelta(minutes=2))
        assert sign_in(authenticator).sign_count == 0

    def test_a_counting_key_keeps_its_new_count(self, user, clock):
        """Registered at 1, signed at 2: kept at 2."""
        _, authenticator = add(user, SoftwareAuthenticator(counts=True))
        clock.at(T0 + timedelta(minutes=1))
        assert sign_in(authenticator).sign_count == 2


class TestTheCeremony:
    """fido2's checks, each refusing an answer wrong in exactly that way."""

    @pytest.mark.parametrize("change, cause", [
        ({"origin": "https://evil.test"}, "Invalid origin in CollectedClientData."),
        ({"origin": "https://www.shekel.test"}, "Invalid origin in CollectedClientData."),
        ({"rp_id": "evil.test"}, "Wrong RP ID hash in response."),
        ({"flags": UP}, "User verification required, but user verified flag not set."),
        ({"flags": UV}, "User Present flag not set."),
        ({"kind": "webauthn.create"}, "Incorrect type in CollectedClientData."),
    ])
    def test_a_wrong_answer_is_not_verified(self, user, clock, change, cause):
        """Each wrong origin, relying party, flag or type: refused, its use unrecorded."""
        passkey, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, authenticator.get(options, **change), state=state)
        assert refusal.refusal is PasskeyRefusal.NOT_VERIFIED
        assert str(refusal.__cause__) == cause
        assert passkey.last_used_at is None

    def test_another_keys_signature_is_not_verified(self, user, clock):
        """The credential id of the added key, signed by another key."""
        _, authenticator = add(user)
        impostor = SoftwareAuthenticator(credential_id=authenticator.credential_id)
        impostor.user_handle = authenticator.user_handle
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, impostor.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.NOT_VERIFIED
        assert str(refusal.__cause__) == "Invalid signature."

    def test_an_unknown_credential_is_refused(self, user, clock):
        """A key no account added, completed against an account."""
        add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        stranger = SoftwareAuthenticator()
        stranger.user_handle = user.id.to_bytes(8, "big")
        refusal = _refused(user, stranger.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.UNKNOWN_CREDENTIAL

    def test_another_accounts_passkey_is_refused_for_this_account(self, user, second_user, clock):
        """The account locked and the passkey's account must be one."""
        _, theirs = add(second_user["user"])
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, theirs.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.UNKNOWN_CREDENTIAL


class TestTheFlagsAndSize:
    """What the authenticator data says of itself, and how much of it there is."""

    def test_backed_up_but_never_eligible_is_refused(self, user, clock):
        """BS without BE, in an assertion."""
        _, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, authenticator.get(options, flags=UP | UV | BS), state=state)
        assert refusal.refusal is PasskeyRefusal.MALFORMED

    def test_an_icloud_keychain_passkey_signs_in(self, user, clock):
        """BE and BS both set, as Apple's synced passkeys report."""
        _, authenticator = add(user)
        clock.at(T0 + timedelta(seconds=1))
        assert sign_in(authenticator, flags=UP | UV | BE | BS) is not None

    def test_authenticator_data_past_1_kib_is_refused(self, user, clock):
        """37 bytes and 988 of extension outputs: 1,025, refused before any decode."""
        _, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        response = authenticator.get(options, extensions=padding(1025 - 37))
        assert _refused(user, response, state=state).refusal is PasskeyRefusal.MALFORMED

    def test_authenticator_data_of_1_kib_signs_in(self, user, clock):
        """The edge the cap admits: 37 bytes and 987 of extension outputs."""
        _, authenticator = add(user)
        clock.at(T0 + timedelta(seconds=1))
        assert sign_in(authenticator, extensions=padding(1024 - 37)) is not None


class TestTheUserHandle:
    """No account was named, so the authenticator must say whose passkey signed."""

    def test_no_user_handle_is_refused(self, user, clock):
        """A sign-in that names no account requires one."""
        _, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, authenticator.get(options, user_handle=None), state=state)
        assert refusal.refusal is PasskeyRefusal.WRONG_USER_HANDLE

    def test_another_accounts_handle_is_refused(self, user, second_user, clock):
        """The handle of an account other than the passkey's."""
        _, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        response = authenticator.get(
            options, user_handle=second_user["user"].id.to_bytes(8, "big"),
        )
        refusal = _refused(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.WRONG_USER_HANDLE


class TestTheAccountMustBeActive:
    """A disabled account's passkey proves who it is and still does not sign in."""

    def test_a_deactivated_account_is_refused(self, user, clock):
        """Refused after the signature is proven, its use unrecorded."""
        passkey, authenticator = add(user)
        user.is_active = False
        db.session.flush()
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, authenticator.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.ACCOUNT_DISABLED
        assert passkey.last_used_at is None


class TestOneAnswerOnce:
    """A challenge issued at or before the passkey's last use is refused."""

    def test_the_same_answer_sent_twice_is_refused(self, user, clock):
        """Signed in at T0 + 1 s by a challenge issued at T0; the copy at T0 + 2 s is refused."""
        _, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        response = authenticator.get(options)
        clock.at(T0 + timedelta(seconds=1))
        user_found = passkey_service.sign_in_account(json.dumps(response))
        passkey_service.complete_sign_in(user_found, json.dumps(response), origin=ORIGIN, state=state)
        clock.at(T0 + timedelta(seconds=2))
        refusal = _refused(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.REPLAYED

    def test_a_challenge_issued_the_instant_of_the_last_use_is_refused(self, user, clock):
        """Issued at the last use's very moment: "at or before" includes it."""
        _, authenticator = add(user)
        clock.at(T0 + timedelta(minutes=1))
        sign_in(authenticator)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, authenticator.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.REPLAYED

    def test_a_challenge_issued_a_microsecond_after_the_last_use_signs_in(self, user, clock):
        """The edge the replay bound admits."""
        _, authenticator = add(user)
        clock.at(T0 + timedelta(minutes=1))
        sign_in(authenticator)
        clock.at(T0 + timedelta(minutes=1) + _ONE_MICROSECOND)
        assert sign_in(authenticator).last_used_at == T0 + timedelta(minutes=1) + _ONE_MICROSECOND

    def test_a_challenge_issued_before_another_sign_in_is_refused(self, user, clock):
        """Two tabs: the one opened first is refused once the other signed in."""
        _, authenticator = add(user)
        first_options, first_state = passkey_service.sign_in_options(origin=ORIGIN)
        clock.at(T0 + timedelta(seconds=10))
        sign_in(authenticator)
        clock.at(T0 + timedelta(seconds=20))
        refusal = _refused(user, authenticator.get(first_options), state=first_state)
        assert refusal.refusal is PasskeyRefusal.REPLAYED


class TestTheCounter:
    """A counting key's counter must rise; Apple's never counts and is not refused."""

    @pytest.mark.parametrize("reported", [1, 0])
    def test_a_counter_that_does_not_rise_is_refused(self, user, clock, reported):
        """Registered at 1: 1 again (equal) or 0 (fallen) is refused, the count kept at 1."""
        passkey, authenticator = add(user, SoftwareAuthenticator(counts=True))
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, authenticator.get(options, counter=reported), state=state)
        assert refusal.refusal is PasskeyRefusal.COUNTER_DID_NOT_RISE
        assert passkey.sign_count == 1
        assert passkey.last_used_at is None


class TestTheChallenge:
    """Five minutes, and a state of the sign-in's own."""

    def test_no_state_is_refused_as_expired(self, user, clock):
        """A second submit finds no state."""
        _, authenticator = add(user)
        options, _ = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refused(user, authenticator.get(options), state=None)
        assert refusal.refusal is PasskeyRefusal.EXPIRED

    def test_five_minutes_to_the_microsecond_is_expired(self, user, clock):
        """Dead at exactly its lifetime, and its use unrecorded."""
        passkey, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        clock.at(T0 + timedelta(minutes=5))
        refusal = _refused(user, authenticator.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.EXPIRED
        assert passkey.last_used_at is None

    def test_five_minutes_less_a_microsecond_signs_in(self, user, clock):
        """The last instant it lives."""
        _, authenticator = add(user)
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        clock.at(T0 + timedelta(minutes=5) - _ONE_MICROSECOND)
        assert sign_in(authenticator, state=state, options=options) is not None
