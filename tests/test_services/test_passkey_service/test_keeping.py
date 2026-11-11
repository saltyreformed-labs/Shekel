"""A new passkey signs in once before it is kept (ruling plan#3525).

The registration's completion asks the device to sign in with the new passkey
the way a sign-in does, naming no passkey, and only that sign-in keeps it:
by the passkey just made, verified against the key the registration read,
with the account's user handle, the person verified, the backup flags and the
counter read as at every sign-in, inside five minutes.  Every refusal here
leaves the account holding nothing it did not hold before.
"""

import json
from datetime import timedelta

import pytest
from fido2 import cbor

from app.extensions import db
from app.models.passkey import Passkey
from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from tests._passkeys import (
    BS,
    ORIGIN,
    RP_ID,
    UP,
    UV,
    SoftwareAuthenticator,
    b64url,
)
from tests.test_services.test_passkey_service._helpers import (
    T0,
    Clock,
    add,
    add_row,
    sign_in,
    written_directly,
)

_ONE_MICROSECOND = timedelta(microseconds=1)


@pytest.fixture(name="clock")
def _clock(monkeypatch):
    """The service's clock, at T0."""
    return Clock(monkeypatch)


@pytest.fixture(name="user")
def _user(bare_user):
    """The account adding passkeys."""
    return bare_user["user"]


def _registered(user, authenticator, name="iPhone", **create):
    """Run the registration's options and completion by *authenticator*.

    Returns:
        The sign-in's options and the state the route keeps for the keeping.
    """
    options, state = passkey_service.registration_options(user, origin=ORIGIN)
    return passkey_service.complete_registration(
        user, json.dumps(authenticator.create(options, **create)), name,
        origin=ORIGIN, state=state,
    )


def _kept(user, response, *, state):
    """Complete the add's sign-in with *response*; return the passkey kept."""
    return passkey_service.add_passkey(
        user, json.dumps(response), origin=ORIGIN, state=state,
    )


def _refusal(user, response, *, state):
    """Complete the add's sign-in with *response*; return the refusal it raised.

    Asserts too that the account holds no passkey it did not hold before.
    """
    held = len(passkey_service.list_passkeys(user.id))
    with pytest.raises(PasskeyRefused) as caught:
        _kept(user, response, state=state)
    assert len(passkey_service.list_passkeys(user.id)) == held
    return caught.value


class TestTheSignInAsked:
    """What the registration's completion asks the device for, and what it keeps for the route."""

    def test_the_sign_in_names_no_passkey_and_the_person_must_be_verified(self, user, clock):
        """A sign-in's options exactly: no allowCredentials, so the device must find the passkey."""
        options, _ = _registered(user, SoftwareAuthenticator())
        assert options["publicKey"] == {
            "challenge": b64url(add_row(user).challenge),
            "timeout": 270_000,
            "rpId": RP_ID,
            "userVerification": "required",
        }

    def test_its_challenge_is_fresh(self, user, clock):
        """Not the registration's: the sign-in answers a challenge of its own."""
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        sign_in_options, _ = passkey_service.complete_registration(
            user, json.dumps(authenticator.create(options)), "iPhone",
            origin=ORIGIN, state=state,
        )
        assert sign_in_options["publicKey"]["challenge"] != options["publicKey"]["challenge"]

    def test_the_add_holds_the_new_passkey_and_the_state_names_it(self, user, clock):
        """The add's row moves on to the sign-in: its challenge, the completion's moment, and the
        passkey's id, key, counter and name, the key the keeping will verify by.  The state the
        route keeps names the ceremony and the row, as plain JSON, and none of it (ruling plan#3543).
        """
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        clock.at(T0 + timedelta(seconds=10))
        sign_in_options, sign_in_state = passkey_service.complete_registration(
            user, json.dumps(authenticator.create(options)), "  Josh's iPhone  ",
            origin=ORIGIN, state=state,
        )
        row = add_row(user)
        assert sign_in_state == {"ceremony": "add_sign_in", "add": state["add"]}
        assert json.loads(json.dumps(sign_in_state)) == sign_in_state
        assert row.id == state["add"]
        assert b64url(row.challenge) == sign_in_options["publicKey"]["challenge"]
        assert row.issued_at == T0 + timedelta(seconds=10)
        assert row.credential_id == authenticator.credential_id
        assert row.public_key == authenticator.cose_key()
        assert row.sign_count == 0
        assert row.name == "Josh's iPhone"


class TestKeptOnlyAfterBoth:
    """Nothing is kept by the registration; the sign-in keeps it, and is its first use."""

    def test_the_registration_keeps_nothing_and_the_sign_in_keeps_it(self, user, clock):
        """After the first completion the account holds none; after the second, this one."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        assert passkey_service.list_passkeys(user.id) == []
        assert passkey_service.has_passkey(user.id) is False
        passkey = _kept(user, authenticator.get(options), state=state)
        assert [each.id for each in passkey_service.list_passkeys(user.id)] == [passkey.id]
        assert passkey_service.has_passkey(user.id) is True

    def test_the_row_holds_the_key_the_registration_read_and_the_sign_ins_moment(self, user, clock):
        """Registered at T0 + 10 s, signed in at T0 + 20 s: added and last used at T0 + 20 s."""
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        clock.at(T0 + timedelta(seconds=10))
        sign_in_options, sign_in_state = passkey_service.complete_registration(
            user, json.dumps(authenticator.create(options)), "  Josh's iPhone  ",
            origin=ORIGIN, state=state,
        )
        clock.at(T0 + timedelta(seconds=20))
        passkey = _kept(user, authenticator.get(sign_in_options), state=sign_in_state)
        db.session.expire_all()
        row = db.session.get(Passkey, passkey.id)
        assert row.user_id == user.id
        assert row.credential_id == authenticator.credential_id
        assert cbor.decode(row.public_key) == cbor.decode(authenticator.cose_key())
        assert row.sign_count == 0
        assert row.name == "Josh's iPhone"
        assert row.created_at == T0 + timedelta(seconds=20)
        assert row.last_used_at == T0 + timedelta(seconds=20)

    def test_a_counting_key_keeps_the_counter_its_sign_in_reported(self, user, clock):
        """Registered at 1, its sign-in at 2: kept at 2."""
        authenticator = SoftwareAuthenticator(counts=True)
        options, state = _registered(user, authenticator)
        assert add_row(user).sign_count == 1
        assert _kept(user, authenticator.get(options), state=state).sign_count == 2

    def test_the_tenth_is_kept(self, user, clock):
        """Ten is the cap, not one past it."""
        for _ in range(10):
            add(clock, user)
        assert len(passkey_service.list_passkeys(user.id)) == 10

    def test_a_sign_in_begun_before_the_add_is_refused_as_already_answered(self, user, clock):
        """The add's sign-in is the passkey's first use, and the replay bound starts there.

        A sign-in's options issued a second before the add, answered by the
        new passkey after it, are refused, as any challenge issued before a
        passkey's last use is.
        """
        options, state = passkey_service.sign_in_options(origin=ORIGIN)
        _, authenticator = add(clock, user, at=T0 + timedelta(seconds=1))
        clock.at(T0 + timedelta(seconds=2))
        with pytest.raises(PasskeyRefused) as caught:
            sign_in(authenticator, state=state, options=options)
        assert caught.value.refusal is PasskeyRefusal.REPLAYED

    def test_a_sign_in_begun_after_the_add_signs_in(self, user, clock):
        """Issued a microsecond after the add's sign-in: the edge the bound admits."""
        _, authenticator = add(clock, user, at=T0)
        clock.at(T0 + _ONE_MICROSECOND)
        assert sign_in(authenticator).last_used_at == T0 + _ONE_MICROSECOND


class TestTheNewPasskeyMustSign:
    """The answer is by the passkey just made, verified against the key the registration read."""

    def test_another_of_the_devices_passkeys_is_refused(self, user, clock):
        """The device holds an older passkey of this account and the person chose it."""
        _, older = add(clock, user)
        options, state = _registered(user, SoftwareAuthenticator())
        refusal = _refusal(user, older.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.NOT_THE_NEW_PASSKEY
        assert str(refusal) == (
            "That was a different passkey. Please try again and choose the one you just made."
        )

    def test_a_key_the_device_cannot_sign_for_is_never_kept(self, user, clock):
        """The registration carried a key that loads, but not the key the device signs with.

        The case ruling plan#3525 exists for: no check at registration can
        tell, and kept, it would close the password sign-in for a passkey
        that never signs in.
        """
        device = SoftwareAuthenticator()
        stranger = SoftwareAuthenticator()
        options, state = _registered(user, device, cose_key=stranger.cose_key())
        refusal = _refusal(user, device.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.NOT_VERIFIED
        assert str(refusal.__cause__) == "Invalid signature."

    def test_another_key_signing_under_the_new_credential_id_is_refused(self, user, clock):
        """The key comes from the add's row, so an impostor naming the new id cannot sign it in."""
        device = SoftwareAuthenticator()
        options, state = _registered(user, device)
        impostor = SoftwareAuthenticator(credential_id=device.credential_id)
        impostor.user_handle = device.user_handle
        refusal = _refusal(user, impostor.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.NOT_VERIFIED
        assert str(refusal.__cause__) == "Invalid signature."


    def test_the_new_passkey_answering_another_challenge_is_not_verified(self, user, clock):
        """The add in flight, and its new passkey signing a sign-in's challenge: fido2's check.

        So the keeping verifies against the add's own challenge, from its
        row, never one the answer carries.
        """
        authenticator = SoftwareAuthenticator()
        _, state = _registered(user, authenticator)
        other_options, _ = passkey_service.sign_in_options(origin=ORIGIN)
        refusal = _refusal(user, authenticator.get(other_options), state=state)
        assert refusal.refusal is PasskeyRefusal.NOT_VERIFIED
        assert str(refusal.__cause__) == "Wrong challenge in response."


class TestTheSignInIsChecked:
    """Every check a sign-in meets, each refusing an answer wrong in exactly that way."""

    @pytest.mark.parametrize("change, cause", [
        ({"origin": "https://evil.test"}, "Invalid origin in CollectedClientData."),
        ({"origin": "https://www.shekel.test"}, "Invalid origin in CollectedClientData."),
        ({"rp_id": "evil.test"}, "Wrong RP ID hash in response."),
        ({"flags": UP}, "User verification required, but user verified flag not set."),
        ({"flags": UV}, "User Present flag not set."),
        ({"kind": "webauthn.create"}, "Incorrect type in CollectedClientData."),
    ])
    def test_a_wrong_answer_is_not_verified(self, user, clock, change, cause):
        """Each wrong origin, relying party, flag or type: refused, nothing kept."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        refusal = _refusal(user, authenticator.get(options, **change), state=state)
        assert refusal.refusal is PasskeyRefusal.NOT_VERIFIED
        assert str(refusal.__cause__) == cause

    def test_no_user_handle_is_refused(self, user, clock):
        """The sign-in named no passkey, so the device must say whose it is."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        refusal = _refusal(user, authenticator.get(options, user_handle=None), state=state)
        assert refusal.refusal is PasskeyRefusal.WRONG_USER_HANDLE

    def test_another_accounts_handle_is_refused(self, user, second_user, clock):
        """A handle that is not the account's: the passkey would never sign this account in."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        response = authenticator.get(
            options, user_handle=second_user["user"].id.to_bytes(8, "big"),
        )
        refusal = _refusal(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.WRONG_USER_HANDLE

    def test_an_account_disabled_between_the_two_acts_is_refused(self, user, clock):
        """Refused after the signature is proven, as a sign-in by a disabled account is."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        user.is_active = False
        db.session.flush()
        refusal = _refusal(user, authenticator.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.ACCOUNT_DISABLED

    def test_backed_up_but_never_eligible_is_refused(self, user, clock):
        """BS without BE, in the add's sign-in as in any assertion."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        refusal = _refusal(user, authenticator.get(options, flags=UP | UV | BS), state=state)
        assert refusal.refusal is PasskeyRefusal.MALFORMED

    @pytest.mark.parametrize("reported", [1, 0])
    def test_a_counter_that_did_not_rise_from_the_registration_is_refused(
        self, user, clock, reported,
    ):
        """Registered at 1: its sign-in at 1 again (equal) or 0 (fallen) is refused."""
        authenticator = SoftwareAuthenticator(counts=True)
        options, state = _registered(user, authenticator)
        refusal = _refusal(user, authenticator.get(options, counter=reported), state=state)
        assert refusal.refusal is PasskeyRefusal.COUNTER_DID_NOT_RISE

    def test_an_answer_that_is_not_a_credential_is_refused(self, user, clock):
        """Read by the reader every assertion is: MALFORMED, never a server error."""
        _, state = _registered(user, SoftwareAuthenticator())
        with pytest.raises(PasskeyRefused) as caught:
            passkey_service.add_passkey(user, "not json", origin=ORIGIN, state=state)
        assert caught.value.refusal is PasskeyRefusal.MALFORMED
        assert passkey_service.list_passkeys(user.id) == []


class TestTheChallenge:
    """Five minutes from the registration's completion, for the account that began the add."""

    def test_no_state_is_refused_as_expired(self, user, clock):
        """A second submit, or a session that ended, finds no state."""
        authenticator = SoftwareAuthenticator()
        options, _ = _registered(user, authenticator)
        refusal = _refusal(user, authenticator.get(options), state=None)
        assert refusal.refusal is PasskeyRefusal.EXPIRED

    def test_five_minutes_after_the_registration_completed_is_expired(self, user, clock):
        """Completed at T0 + 1 min; signed at T0 + 6 min, exactly its lifetime: refused."""
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        clock.at(T0 + timedelta(minutes=1))
        sign_in_options, sign_in_state = passkey_service.complete_registration(
            user, json.dumps(authenticator.create(options)), "iPhone",
            origin=ORIGIN, state=state,
        )
        clock.at(T0 + timedelta(minutes=6))
        refusal = _refusal(user, authenticator.get(sign_in_options), state=sign_in_state)
        assert refusal.refusal is PasskeyRefusal.EXPIRED

    def test_an_expired_sign_in_is_refused_and_its_add_deleted(self, user, clock):
        """Refused at its lifetime, and the row is gone (flushed with the refusal)."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        clock.at(T0 + timedelta(minutes=5))
        refusal = _refusal(user, authenticator.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.EXPIRED
        assert add_row(user) is None

    def test_five_minutes_less_a_microsecond_is_kept(self, user, clock):
        """The last instant it lives, counted from the registration's completion, not its options."""
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        clock.at(T0 + timedelta(minutes=1))
        sign_in_options, sign_in_state = passkey_service.complete_registration(
            user, json.dumps(authenticator.create(options)), "iPhone",
            origin=ORIGIN, state=state,
        )
        clock.at(T0 + timedelta(minutes=6) - _ONE_MICROSECOND)
        passkey = _kept(user, authenticator.get(sign_in_options), state=sign_in_state)
        assert passkey.last_used_at == T0 + timedelta(minutes=6) - _ONE_MICROSECOND

    def test_an_add_begun_by_another_account_is_refused(self, user, second_user, clock):
        """The sign-in state of another account's add, completed by this one."""
        other = second_user["user"]
        authenticator = SoftwareAuthenticator()
        options, state = _registered(other, authenticator)
        refusal = _refusal(user, authenticator.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.WRONG_ACCOUNT
        assert passkey_service.list_passkeys(other.id) == []


class TestOneAddsStateCompletesNoOther:
    """Two adds begun: the newer replaces the older (ruling plan#3543), whose state keeps nothing."""

    def test_the_other_adds_passkey_is_refused(self, user, clock):
        """State of the first add, answer by the second's passkey to the second's sign-in.

        The first add is gone, so its state names no add in flight: refused as
        expired, and the second's passkey is not kept by the first's state.
        """
        first, second = SoftwareAuthenticator(), SoftwareAuthenticator()
        _, first_state = _registered(user, first)
        second_options, _ = _registered(user, second)
        refusal = _refusal(user, second.get(second_options), state=first_state)
        assert refusal.refusal is PasskeyRefusal.EXPIRED

    def test_its_own_passkey_answering_the_other_adds_challenge_is_refused(
        self, user, clock,
    ):
        """State of the first add, answer by the first's passkey to the second's sign-in."""
        first, second = SoftwareAuthenticator(), SoftwareAuthenticator()
        _, first_state = _registered(user, first)
        second_options, _ = _registered(user, second)
        refusal = _refusal(user, first.get(second_options), state=first_state)
        assert refusal.refusal is PasskeyRefusal.EXPIRED

    def test_the_newer_adds_own_state_keeps_its_passkey(self, user, clock):
        """The control: the add that replaced the older one completes."""
        first, second = SoftwareAuthenticator(), SoftwareAuthenticator()
        _registered(user, first)
        second_options, second_state = _registered(user, second)
        passkey = _kept(user, second.get(second_options), state=second_state)
        assert passkey.credential_id == second.credential_id


class TestKeptOnce:
    """The keeping's own statement is the answer that binds: one credential, one row, ten at most."""

    def test_the_same_sign_in_completed_twice_keeps_one(self, user, clock):
        """A second submit of the answer with its state: the add was used (ruling plan#3543)."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        response = authenticator.get(options)
        _kept(user, response, state=state)
        refusal = _refusal(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.EXPIRED
        assert len(passkey_service.list_passkeys(user.id)) == 1

    def test_the_keeping_deletes_the_add(self, user, clock):
        """Kept: the account holds no add in flight."""
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        _kept(user, authenticator.get(options), state=state)
        assert add_row(user) is None

    def test_an_adds_answer_replayed_after_its_passkey_is_removed_keeps_nothing(
        self, user, clock,
    ):
        """Kept, removed, then the add's answer and state sent again within five minutes.

        The replay plan#3533's review measured (its ``PROBE4``): with the add
        in the state alone, this kept the removed passkey again.
        """
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        response = authenticator.get(options)
        passkey = _kept(user, response, state=state)
        passkey_service.remove_passkey(user.id, passkey.id)
        clock.at(T0 + timedelta(seconds=30))
        refusal = _refusal(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.EXPIRED
        assert passkey_service.list_passkeys(user.id) == []

    def test_a_credential_another_account_kept_meanwhile_is_refused(self, user, second_user, clock):
        """Two devices naming one credential id, each registered before either was kept.

        Each signs with its own key under its own account's handle, so the
        one thing refusing the second keeping is the statement that writes
        the row, asked of every account.
        """
        shared = b"\x0b" * 16
        mine, theirs = SoftwareAuthenticator(credential_id=shared), SoftwareAuthenticator(
            credential_id=shared,
        )
        my_options, my_state = _registered(user, mine)
        their_options, their_state = _registered(second_user["user"], theirs)
        _kept(second_user["user"], theirs.get(their_options), state=their_state)
        refusal = _refusal(user, mine.get(my_options), state=my_state)
        assert refusal.refusal is PasskeyRefusal.ALREADY_ADDED
        assert passkey_service.list_passkeys(user.id) == []

    def test_an_eleventh_signed_after_a_tenth_was_kept_is_refused(self, user, clock):
        """Registered at nine; a tenth written meanwhile; the eleventh's sign-in refuses.

        No add can keep a tenth while this one is in flight (a newer add
        replaces it, ruling plan#3543), so the tenth is written directly
        (:func:`written_directly`): the count at the keeping binds whatever
        wrote it.
        """
        for _ in range(9):
            add(clock, user)
        authenticator = SoftwareAuthenticator()
        options, state = _registered(user, authenticator)
        written_directly(user)
        refusal = _refusal(user, authenticator.get(options), state=state)
        assert refusal.refusal is PasskeyRefusal.TOO_MANY
        assert len(passkey_service.list_passkeys(user.id)) == 10
