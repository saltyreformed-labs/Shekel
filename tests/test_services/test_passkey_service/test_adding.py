"""Adding a passkey: the options the device is asked with, and every refusal of its registration.

The registration's completion keeps nothing (ruling plan#3525): it ends by
asking the device to sign in with the new passkey, and only that sign-in
keeps it, which ``test_keeping`` grades.  Each refusal here is asked at the
completion, so the device is never asked to sign in with a passkey Shekel
would not keep, and nothing is kept.
"""

import json
from datetime import timedelta

import pytest

from app.exceptions import ValidationError
from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from tests._passkeys import (
    AT,
    BE,
    BS,
    ORIGIN,
    RP_ID,
    RS1,
    UP,
    UV,
    SoftwareAuthenticator,
    attestation_object,
    b64url,
    cbor_bytes,
    cbor_int,
    cbor_map,
    cbor_text,
    padding,
    unb64url,
)
from tests.test_services.test_passkey_service._helpers import T0, Clock, add


@pytest.fixture(name="clock")
def _clock(monkeypatch):
    """The service's clock, at T0."""
    return Clock(monkeypatch)


@pytest.fixture(name="user")
def _user(bare_user):
    """The account adding passkeys."""
    return bare_user["user"]


def _completed(user, response, *, state, name="iPhone"):
    """Complete the registration with *response*; return the sign-in's options and state."""
    return passkey_service.complete_registration(
        user, json.dumps(response), name, origin=ORIGIN, state=state,
    )


def _refusal(user, response, *, state, name="iPhone"):
    """Complete the registration with *response*; return the refusal it raised.

    Asserts too that the account holds no passkey it did not hold before.
    """
    held = len(passkey_service.list_passkeys(user.id))
    with pytest.raises(PasskeyRefused) as caught:
        _completed(user, response, state=state, name=name)
    assert len(passkey_service.list_passkeys(user.id)) == held
    return caught.value


class TestTheOptions:
    """What the device is asked to make."""

    def test_the_device_must_verify_the_person_and_keep_a_discoverable_key(self, user, clock):
        """User verification and a discoverable credential are required, for this site's host."""
        options, _ = passkey_service.registration_options(user, origin=ORIGIN)
        public = options["publicKey"]
        assert public["rp"] == {"name": "Shekel", "id": RP_ID}
        assert public["authenticatorSelection"]["userVerification"] == "required"
        assert public["authenticatorSelection"]["residentKey"] == "required"
        # Five minutes less thirty seconds for the two trips, in
        # milliseconds: (5 * 60 - 30) * 1000.
        assert public["timeout"] == 270_000

    def test_the_user_is_named_by_their_id_and_shown_by_their_name(self, user, clock):
        """The handle is the id as eight big-endian bytes; the device shows the email and name."""
        options, _ = passkey_service.registration_options(user, origin=ORIGIN)
        assert options["publicKey"]["user"] == {
            "id": b64url(user.id.to_bytes(8, "big")),
            "name": "bare@shekel.local",
            "displayName": "Bare User",
        }

    def test_every_passkey_the_account_holds_is_excluded(self, user, clock):
        """Two added: the third's options exclude both, by credential id."""
        first, _ = add(clock, user)
        second, _ = add(clock, user)
        options, _ = passkey_service.registration_options(user, origin=ORIGIN)
        assert [
            unb64url(each["id"]) for each in options["publicKey"]["excludeCredentials"]
        ] == [first.credential_id, second.credential_id]

    def test_the_options_and_the_state_are_plain_json(self, user, clock):
        """The route hands one to the browser and keeps the other in its session."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        assert json.loads(json.dumps(options)) == options
        assert state == {
            "ceremony": "registration",
            "challenge": options["publicKey"]["challenge"],
            "issued_at": T0.isoformat(),
            "user_id": user.id,
        }

    def test_an_account_holding_ten_is_refused_before_the_device_is_asked(self, user, clock):
        """The eleventh's options are refused, so no device makes a key Shekel would not keep."""
        for _ in range(10):
            add(clock, user)
        with pytest.raises(PasskeyRefused) as caught:
            passkey_service.registration_options(user, origin=ORIGIN)
        assert caught.value.refusal is PasskeyRefusal.TOO_MANY
        assert str(caught.value) == (
            "An account can hold up to 10 passkeys. Remove one to add another."
        )


class TestTheName:
    """The name typed for a passkey, refused before anything else is asked."""

    @pytest.mark.parametrize("name, message", [
        ("", "Give the passkey a name."),
        (" \t ", "Give the passkey a name."),
        ("x" * 61, "A passkey's name can be at most 60 characters."),
        ("iPhone\x00", "A passkey's name cannot hold a NUL character."),
    ])
    def test_a_name_the_table_would_refuse_is_refused(self, user, clock, name, message):
        """Blank, 61 characters, a NUL: each refused with its sentence, no row written."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        response = SoftwareAuthenticator().create(options)
        with pytest.raises(ValidationError, match=message):
            _completed(user, response, state=state, name=name)
        assert passkey_service.list_passkeys(user.id) == []

    def test_sixty_characters_are_kept(self, user, clock):
        """The edge the length check admits."""
        passkey, _ = add(clock, user, name="x" * 60)
        assert passkey.name == "x" * 60


class TestTheChallenge:
    """A challenge answers once, for five minutes, for the account it was made for."""

    def test_no_state_is_refused_as_expired(self, user, clock):
        """A second submit, or a session that ended, finds no state."""
        options, _ = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, SoftwareAuthenticator().create(options), state=None)
        assert refusal.refusal is PasskeyRefusal.EXPIRED
        assert str(refusal) == "That took too long. Please try again."

    def test_five_minutes_less_a_microsecond_is_in_time(self, user, clock):
        """The last instant the challenge lives: the sign-in is asked for."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        clock.at(T0 + timedelta(minutes=5) - timedelta(microseconds=1))
        sign_in_options, _ = _completed(
            user, SoftwareAuthenticator().create(options), state=state,
        )
        assert sign_in_options["publicKey"]["userVerification"] == "required"

    def test_five_minutes_to_the_microsecond_is_expired(self, user, clock):
        """The challenge is dead at exactly its lifetime."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        clock.at(T0 + timedelta(minutes=5))
        refusal = _refusal(user, SoftwareAuthenticator().create(options), state=state)
        assert refusal.refusal is PasskeyRefusal.EXPIRED

    def test_an_add_begun_by_another_account_is_refused(self, user, second_user, clock):
        """Begun by one account in this browser and completed by another after a sign-in.

        Kept, the key would carry the first account's handle under the second
        account, and never sign in.
        """
        other = second_user["user"]
        options, state = passkey_service.registration_options(other, origin=ORIGIN)
        refusal = _refusal(user, SoftwareAuthenticator().create(options), state=state)
        assert refusal.refusal is PasskeyRefusal.WRONG_ACCOUNT

    def test_a_challenge_another_add_issued_is_not_verified(self, user, clock):
        """The state of one add and the answer to another: fido2's challenge check."""
        options, _ = passkey_service.registration_options(user, origin=ORIGIN)
        _, other_state = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, SoftwareAuthenticator().create(options), state=other_state)
        assert refusal.refusal is PasskeyRefusal.NOT_VERIFIED
        assert str(refusal.__cause__) == "Wrong challenge in response."


class TestTheCeremony:
    """fido2's checks, each refusing an answer wrong in exactly that way."""

    @pytest.mark.parametrize("change, cause", [
        ({"origin": "https://evil.test"}, "Invalid origin in CollectedClientData."),
        # fido2's own default would accept a subdomain of the relying party.
        ({"origin": "https://www.shekel.test"}, "Invalid origin in CollectedClientData."),
        ({"origin": "https://shekel.test/"}, "Invalid origin in CollectedClientData."),
        ({"rp_id": "evil.test"}, "Wrong RP ID hash in response."),
        ({"flags": UP | AT}, "User verification required, but User Verified flag not set."),
        ({"flags": UV | AT}, "User Present flag not set."),
        ({"kind": "webauthn.get"}, "Incorrect type in CollectedClientData."),
    ])
    def test_a_wrong_answer_is_not_verified(self, user, clock, change, cause):
        """Each wrong origin, relying party, flag or type: refused, nothing kept."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, SoftwareAuthenticator().create(options, **change), state=state)
        assert refusal.refusal is PasskeyRefusal.NOT_VERIFIED
        assert str(refusal.__cause__) == cause
        assert str(refusal) == "That passkey could not be checked. Please try again."


class TestTheKey:
    """The key the device made must at least load as one a sign-in could verify by."""

    def test_an_algorithm_the_options_never_offered_is_refused(self, user, clock):
        """An EC key labelled RS1 (SHA-1 RSA), which fido2 knows but Shekel never offers."""
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        response = authenticator.create(options, cose_key=authenticator.cose_key(alg=RS1))
        refusal = _refusal(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.UNOFFERED_ALGORITHM

    @pytest.mark.parametrize("algorithm", [
        b"\x81\x26",                                     # the array [-7]
        cbor_map([(cbor_int(1), cbor_int(2))]),           # the map {1: 2}
        cbor_text("ES256"),
    ])
    def test_an_algorithm_that_is_not_a_number_is_refused(self, user, clock, algorithm):
        """A COSE alg that is an array, a map or text: refused, never a set's TypeError."""
        authenticator = SoftwareAuthenticator()
        numbers = authenticator.public_numbers()
        key = cbor_map([
            (cbor_int(1), cbor_int(2)), (cbor_int(3), algorithm),
            (cbor_int(-1), cbor_int(1)),
            (cbor_int(-2), cbor_bytes(numbers.x.to_bytes(32, "big"))),
            (cbor_int(-3), cbor_bytes(numbers.y.to_bytes(32, "big"))),
        ])
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, authenticator.create(options, cose_key=key), state=state)
        assert refusal.refusal is PasskeyRefusal.UNOFFERED_ALGORITHM

    def test_a_point_off_the_curve_is_refused(self, user, clock):
        """An ES256 key whose y is not the curve's at x: it would fail every sign-in."""
        authenticator = SoftwareAuthenticator()
        numbers = authenticator.public_numbers()
        off_curve = cbor_map([
            (cbor_int(1), cbor_int(2)), (cbor_int(3), cbor_int(-7)),
            (cbor_int(-1), cbor_int(1)),
            (cbor_int(-2), cbor_bytes(numbers.x.to_bytes(32, "big"))),
            (cbor_int(-3), cbor_bytes((numbers.y ^ 1).to_bytes(32, "big"))),
        ])
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, authenticator.create(options, cose_key=off_curve), state=state)
        assert refusal.refusal is PasskeyRefusal.UNUSABLE_KEY
        assert isinstance(refusal.__cause__, ValueError)

    def test_a_coordinate_that_is_not_bytes_is_refused(self, user, clock):
        """An ES256 key whose x is a number: its verify raises TypeError while building the key."""
        authenticator = SoftwareAuthenticator()
        numbers = authenticator.public_numbers()
        numeric_x = cbor_map([
            (cbor_int(1), cbor_int(2)), (cbor_int(3), cbor_int(-7)),
            (cbor_int(-1), cbor_int(1)), (cbor_int(-2), cbor_int(5)),
            (cbor_int(-3), cbor_bytes(numbers.y.to_bytes(32, "big"))),
        ])
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, authenticator.create(options, cose_key=numeric_x), state=state)
        assert refusal.refusal is PasskeyRefusal.UNUSABLE_KEY
        assert isinstance(refusal.__cause__, TypeError)

    def test_a_key_missing_its_coordinate_is_refused(self, user, clock):
        """An ES256 key with no y: its verify raises KeyError."""
        authenticator = SoftwareAuthenticator()
        numbers = authenticator.public_numbers()
        no_y = cbor_map([
            (cbor_int(1), cbor_int(2)), (cbor_int(3), cbor_int(-7)),
            (cbor_int(-1), cbor_int(1)),
            (cbor_int(-2), cbor_bytes(numbers.x.to_bytes(32, "big"))),
        ])
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, authenticator.create(options, cose_key=no_y), state=state)
        assert refusal.refusal is PasskeyRefusal.UNUSABLE_KEY
        assert isinstance(refusal.__cause__, KeyError)


class TestTheCredentialId:
    """One credential id names one passkey, of one account."""

    def test_the_same_passkey_added_twice_is_refused(self, user, clock):
        """A device that ignored the exclusion: refused before it is asked to sign in."""
        authenticator = SoftwareAuthenticator()
        add(clock, user, authenticator)
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, authenticator.create(options), state=state)
        assert refusal.refusal is PasskeyRefusal.ALREADY_ADDED
        assert str(refusal) == "That passkey has already been added."

    def test_a_credential_another_account_holds_is_refused(self, user, second_user, clock):
        """The check asks every account, not this one's."""
        authenticator = SoftwareAuthenticator()
        add(clock, second_user["user"], authenticator)
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        refusal = _refusal(user, authenticator.create(options), state=state)
        assert refusal.refusal is PasskeyRefusal.ALREADY_ADDED
        assert passkey_service.list_passkeys(user.id) == []

    def test_a_credential_id_past_1023_bytes_is_refused(self, user, clock):
        """WebAuthn's longest id is 1023 bytes; 1024 is refused before the table refuses it."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        response = SoftwareAuthenticator(credential_id=b"\x07" * 1024).create(options)
        refusal = _refusal(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.MALFORMED

    def test_a_credential_id_of_1023_bytes_is_kept(self, user, clock):
        """The edge the length check admits."""
        passkey, _ = add(clock, user, SoftwareAuthenticator(credential_id=b"\x07" * 1023))
        assert len(passkey.credential_id) == 1023

    def test_an_empty_credential_id_is_refused(self, user, clock):
        """Authenticator data can name a credential of no bytes; the table cannot keep one."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        response = SoftwareAuthenticator(credential_id=b"").create(options)
        refusal = _refusal(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.MALFORMED

    def test_an_id_other_than_the_one_the_key_names_is_refused(self, user, clock):
        """The browser's rawId and the authenticator data's credential id must be one id."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        response = SoftwareAuthenticator().create(options, raw_id=b"\x09" * 16)
        refusal = _refusal(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.MALFORMED

    def test_authenticator_data_with_no_new_credential_is_refused(self, user, clock):
        """A registration's authenticator data without the AT flag carries no key."""
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        response = authenticator.registration(
            json.dumps({
                "type": "webauthn.create",
                "challenge": options["publicKey"]["challenge"],
                "origin": ORIGIN,
            }).encode(),
            attestation_object(bytes(32) + bytes([UP | UV]) + bytes(4)),
        )
        refusal = _refusal(user, response, state=state)
        assert refusal.refusal is PasskeyRefusal.MALFORMED


class TestTheFlagsAndSize:
    """What the authenticator data says of itself, and how much of it there is."""

    def test_backed_up_but_never_eligible_is_refused(self, user, clock):
        """BS without BE: Web Authentication Level 3's step fido2 leaves to its caller."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        response = SoftwareAuthenticator().create(options, flags=UP | UV | AT | BS)
        assert _refusal(user, response, state=state).refusal is PasskeyRefusal.MALFORMED

    def test_backed_up_and_eligible_is_kept(self, user, clock):
        """An iCloud Keychain passkey's flags: BE and BS both set."""
        passkey, _ = add(clock, user, flags=UP | UV | AT | BE | BS)
        assert passkey.id is not None

    def test_an_attestation_object_past_8_kib_is_refused(self, user, clock):
        """Refused before its CBOR is decoded, whose time grows with the square of its length."""
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        response = SoftwareAuthenticator().create(options, extensions=padding(8192))
        assert _refusal(user, response, state=state).refusal is PasskeyRefusal.MALFORMED

    def test_an_attestation_object_of_8_kib_is_admitted(self, user, clock):
        """The edge the cap admits: padded by extension outputs to exactly 8,192 bytes.

        A ``none`` attestation object around this key and a 16-byte id, with
        no extensions, measures its own size here, so the padding makes the
        whole exactly the cap.  One byte fewer than the difference: the
        ``authData`` byte string's CBOR head grows from two bytes to three once
        it passes 255 bytes.
        """
        authenticator = SoftwareAuthenticator()
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        bare = authenticator.create(options)
        bare_length = len(unb64url(bare["response"]["attestationObject"]))
        authenticator.counter = 0
        response = authenticator.create(options, extensions=padding(8192 - bare_length - 1))
        assert len(unb64url(response["response"]["attestationObject"])) == 8192
        sign_in_options, _ = _completed(user, response, state=state)
        assert sign_in_options["publicKey"]["userVerification"] == "required"


class TestTheEleventh:
    """Ruling plan#3472: up to ten, asked again when the registration completes."""

    def test_an_eleventh_completed_after_its_options_is_refused(self, user, clock):
        """Options made at nine; a tenth added meanwhile; the eleventh's registration refuses."""
        for _ in range(9):
            add(clock, user)
        options, state = passkey_service.registration_options(user, origin=ORIGIN)
        add(clock, user)
        refusal = _refusal(user, SoftwareAuthenticator().create(options), state=state)
        assert refusal.refusal is PasskeyRefusal.TOO_MANY
        assert len(passkey_service.list_passkeys(user.id)) == 10
