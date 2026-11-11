"""A response the service cannot read is refused as MALFORMED, never a server error.

The sign-in is public, so these are a stranger's bytes.  Two kinds of case:

* :class:`TestTheShape` -- what the service checks before fido2 sees the
  bytes, chief among them a number where binary belongs, which fido2 would
  turn into a buffer that many bytes long.  Each number here is ``2**62``, so
  were the check gone the allocation would fail at once with
  ``MemoryError``, failing the test rather than the machine.
* :class:`TestWhatFido2Raises` -- one case per exception class the service
  catches from fido2's parsers (``_responses.UNREADABLE``), each asserting the
  class it caught, so every class in that tuple is one a test makes fire.
"""

import json
import struct

import pytest

from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from tests._passkeys import (
    AT,
    ED,
    ORIGIN,
    UP,
    UV,
    Clock,
    SoftwareAuthenticator,
    add,
    attestation_object,
    b64url,
    cbor_int,
    cbor_map,
    cbor_text,
    client_data,
)

_HUGE = 2**62

_RP_HASH = bytes(32)


@pytest.fixture(name="clock")
def _clock(monkeypatch):
    """The service's clock, at T0."""
    return Clock(monkeypatch)


@pytest.fixture(name="user")
def _user(bare_user):
    """The account the response is completed against."""
    return bare_user["user"]


def _sign_in_refusal(user, response_json):
    """Complete a sign-in to *user* with *response_json* text; return the refusal."""
    _, state = passkey_service.sign_in_options(origin=ORIGIN)
    with pytest.raises(PasskeyRefused) as caught:
        passkey_service.complete_sign_in(user, response_json, origin=ORIGIN, state=state)
    return caught.value


def _add_refusal(user, response):
    """Complete an add's registration by *user* with *response*; return the refusal."""
    _, state = passkey_service.registration_options(user, origin=ORIGIN)
    with pytest.raises(PasskeyRefused) as caught:
        passkey_service.complete_registration(
            user, json.dumps(response), "iPhone", origin=ORIGIN, state=state,
        )
    return caught.value


def _assertion(auth_data, client_data_json=None):
    """Return a signed assertion's JSON text over *auth_data*."""
    authenticator = SoftwareAuthenticator()
    return json.dumps(authenticator.assertion(
        client_data("webauthn.get", "AAAA", ORIGIN)
        if client_data_json is None else client_data_json,
        auth_data, b"\x00" * 8,
    ))


def _registration(attestation, client_data_json=None):
    """Return a registration response holding *attestation*."""
    authenticator = SoftwareAuthenticator()
    return authenticator.registration(
        client_data("webauthn.create", "AAAA", ORIGIN)
        if client_data_json is None else client_data_json,
        attestation,
    )


def _credential(**overrides):
    """Return a credential's JSON text with fields replaced."""
    credential = {
        "id": "AAAA", "rawId": "AAAA", "type": "public-key",
        "response": {
            "clientDataJSON": b64url(client_data("webauthn.get", "AAAA", ORIGIN)),
            "authenticatorData": b64url(_RP_HASH + bytes([UP | UV]) + bytes(4)),
            "signature": "AAAA",
        },
    }
    credential.update(overrides)
    return json.dumps(credential)


class TestTheShape:
    """What the service checks itself, before fido2 sees anything."""

    @pytest.mark.parametrize("response_json", [
        "not json",
        "[" * 100_000 + "]" * 100_000,
        json.dumps(["a credential", "is an object"]),
        _credential(type="password"),
        _credential(id=_HUGE, rawId=_HUGE),
        _credential(id="BBBB"),
        _credential(id="AA+A", rawId="AA+A"),
        _credential(id="A", rawId="A"),
        _credential(response="not an object"),
    ])
    def test_a_response_that_is_not_a_credential_is_refused(self, user, clock, response_json):
        """Not JSON, nested past recursion, not an object, the wrong type, a number for
        the id, an id that is not the raw id, a character outside base64url, a length
        no base64 has, a response that is not an object."""
        assert _sign_in_refusal(user, response_json).refusal is PasskeyRefusal.MALFORMED

    @pytest.mark.parametrize("members", [
        {"type": "webauthn.get", "challenge": _HUGE, "origin": ORIGIN},
        {"type": "webauthn.get", "origin": ORIGIN},
        {"type": 5, "challenge": "AAAA", "origin": ORIGIN},
        {"type": "webauthn.get", "challenge": "AAAA", "origin": ["https://shekel.test"]},
    ])
    def test_client_data_whose_members_are_not_text_is_refused(self, user, clock, members):
        """A number for the challenge (fido2 would allocate it), a missing member, a
        number for the type, a list for the origin."""
        response = _assertion(
            _RP_HASH + bytes([UP | UV]) + bytes(4), json.dumps(members).encode(),
        )
        assert _sign_in_refusal(user, response).refusal is PasskeyRefusal.MALFORMED

    @pytest.mark.parametrize("raw", [b"[1, 2]", b"{", b"\xff\xfe"])
    def test_client_data_that_is_not_a_json_object_is_refused(self, user, clock, raw):
        """An array, JSON cut short, bytes that are not text."""
        response = _assertion(_RP_HASH + bytes([UP | UV]) + bytes(4), raw)
        assert _sign_in_refusal(user, response).refusal is PasskeyRefusal.MALFORMED

    def test_authenticator_data_too_short_for_its_flags_is_refused(self, user, clock):
        """Thirty-two bytes: the relying party's hash and no flags."""
        response = _assertion(_RP_HASH)
        assert _sign_in_refusal(user, response).refusal is PasskeyRefusal.MALFORMED

    def test_an_assertion_carrying_a_new_credential_is_refused(self, user, clock):
        """An assertion never carries one; fido2 would parse it as a public key."""
        response = _assertion(_RP_HASH + bytes([UP | UV | AT]) + bytes(4))
        refusal = _sign_in_refusal(user, response)
        assert refusal.refusal is PasskeyRefusal.MALFORMED
        assert refusal.__cause__ is None

    def test_authenticator_data_that_is_a_cbor_number_is_refused(self, user, clock):
        """fido2 would build the authenticator data with ``bytes(2**62)``."""
        attestation = attestation_object(b"", auth_data_item=cbor_int(_HUGE))
        refusal = _add_refusal(user, _registration(attestation))
        assert refusal.refusal is PasskeyRefusal.MALFORMED
        assert passkey_service.list_passkeys(user.id) == []

    def test_an_attestation_object_that_is_not_a_cbor_map_is_refused(self, user, clock):
        """A CBOR text string where the map belongs."""
        refusal = _add_refusal(user, _registration(cbor_text("none")))
        assert refusal.refusal is PasskeyRefusal.MALFORMED


class TestWhatFido2Raises:
    """Each class the service catches from fido2, made to fire."""

    @pytest.mark.parametrize("extension, raised", [
        (b"", IndexError),                       # ends before an item's first byte
        (b"\xc0\x00", KeyError),                 # a tag (major type 6)
        (b"\x19\x01", struct.error),             # ends inside a two-byte length
        (b"\x1c", ValueError),                   # reserved additional information
        (b"\xa1\xa0\x00", TypeError),            # a map keyed by a map
        # Nested past recursion yet inside the 1,024-byte cap: each level is
        # one byte and two frames of fido2's decoder against a limit of 1,000.
        (b"\x81" * 980 + b"\x00", RecursionError),
    ])
    def test_extension_data_fido2_cannot_read_is_refused(self, user, clock, extension, raised):
        """The ED flag set and its CBOR malformed in each way fido2 raises on."""
        auth_data = _RP_HASH + bytes([UP | UV | ED]) + bytes(4) + extension
        refusal = _sign_in_refusal(user, _assertion(auth_data))
        assert refusal.refusal is PasskeyRefusal.MALFORMED
        assert refusal.__cause__.__class__ is raised

    def test_authenticator_data_with_bytes_past_its_fields_is_refused(self, user, clock):
        """No flag says anything follows the counter: fido2's ``Wrong length``."""
        refusal = _sign_in_refusal(user, _assertion(_RP_HASH + bytes([UP | UV]) + bytes(5)))
        assert refusal.refusal is PasskeyRefusal.MALFORMED
        assert refusal.__cause__.__class__ is ValueError

    def test_a_public_key_that_is_not_a_cbor_map_is_refused(self, user, clock):
        """A new credential whose key is the CBOR number 5: fido2's ``.get`` on an int."""
        auth_data = (
            _RP_HASH + bytes([UP | UV | AT]) + bytes(4) + bytes(16)
            + b"\x00\x04" + b"\x01\x02\x03\x04" + cbor_int(5)
        )
        response = _registration(attestation_object(auth_data))
        response["rawId"] = response["id"] = b64url(b"\x01\x02\x03\x04")
        refusal = _add_refusal(user, response)
        assert refusal.refusal is PasskeyRefusal.MALFORMED
        assert refusal.__cause__.__class__ is AttributeError

    def test_an_attestation_object_missing_its_format_is_refused(self, user, clock):
        """A CBOR map holding authData alone: fido2's ``data["fmt"]``."""
        attestation = cbor_map([(cbor_text("authData"), b"\x40")])
        refusal = _add_refusal(user, _registration(attestation))
        assert refusal.refusal is PasskeyRefusal.MALFORMED
        assert refusal.__cause__.__class__ is KeyError

    def test_a_client_data_challenge_of_no_base64_length_is_refused(self, user, clock):
        """Text, so it passes the shape check, but one character long: binascii.Error."""
        response = _assertion(
            _RP_HASH + bytes([UP | UV]) + bytes(4),
            client_data("webauthn.get", "A", ORIGIN),
        )
        refusal = _sign_in_refusal(user, response)
        assert refusal.refusal is PasskeyRefusal.MALFORMED
        assert isinstance(refusal.__cause__, ValueError)


def test_a_sign_in_still_reads_a_correct_response_after_the_checks(user, clock):
    """The checks refuse what is malformed and nothing else: a real key signs in."""
    _, authenticator = add(clock, user)
    options, state = passkey_service.sign_in_options(origin=ORIGIN)
    response = json.dumps(authenticator.get(options))
    assert passkey_service.complete_sign_in(
        user, response, origin=ORIGIN, state=state,
    ).user_id == user.id
