"""
Shekel Budget App -- Reading what the browser sent, before fido2 sees it.

The passkey sign-in is public, so this is the boundary every stranger's bytes
cross.  fido2 turns a value it expects to be binary into bytes with
``bytes(value)``, and ``bytes`` of a number is that many zero bytes: measured
on fido2 2.2.1, a credential JSON of ``{"rawId": 50000000}`` allocated 50 MB
before the parse refused it, and a client data ``challenge`` of 99999999999
raised ``MemoryError``.  So :func:`read_credential` admits only a JSON object
whose every binary field is base64url text, :func:`_client_data` only client
data whose members are text, the attestation object's authenticator data must
be a byte string, and fido2 is handed bytes this module decoded.  What fido2
still raises on bytes it cannot read is :data:`UNREADABLE`, and each is a
refusal here, ``MALFORMED``, never a server error.

Client data's ``crossOrigin`` is not asked: Shekel's pages refuse every frame
(``frame-ancestors 'none'``, ``app/__init__.py``), so no browser runs a
ceremony of Shekel's inside another site.
"""

import json
import string
import struct

from fido2 import cbor
from fido2.utils import websafe_decode
from fido2.webauthn import (
    AttestationObject,
    AuthenticationResponse,
    AuthenticatorAssertionResponse,
    AuthenticatorAttestationResponse,
    AuthenticatorData,
    CollectedClientData,
    PublicKeyCredentialType,
    RegistrationResponse,
)

from app.services.passkey_service._refusals import PasskeyRefusal, PasskeyRefused


#: The characters base64url text may hold (RFC 4648, section 5), unpadded, as
#: WebAuthn's JSON writes binary.
_BASE64URL = frozenset(string.ascii_letters + string.digits + "-_")

#: The one credential type WebAuthn defines.
_PUBLIC_KEY = PublicKeyCredentialType.PUBLIC_KEY.value

#: Where the flags byte sits in authenticator data: after the 32-byte hash of
#: the relying party id.
_FLAGS_OFFSET = 32

#: The longest authenticator data an assertion may carry, in bytes, and the
#: longest attestation object a registration may: read before any CBOR is
#: decoded, because fido2's decoder copies the rest of its buffer at every
#: item, so its time grows with the square of the length (measured by plan
#: #3482's review: 0.63 s of CPU for 380 KB).  An assertion with no extension
#: is 37 bytes, and Shekel asks for none; the largest registration Shekel can
#: be sent is about 3.7 KB (a 1,023-byte credential id and an ML-DSA-87 key of
#: 2,592 bytes), and Shekel asks for no attestation statement.
_AUTHENTICATOR_DATA_MAX_BYTES = 1024
_ATTESTATION_OBJECT_MAX_BYTES = 8 * 1024

#: What fido2 2.2's parsers, and a malformed public key's ``verify``, raise on
#: bytes they cannot read, once :func:`read_credential` and
#: :func:`_client_data` have checked the JSON's shape.  Each is here because a
#: test in ``tests/test_services/test_passkey_service`` makes it fire through
#: the service:
#:
#: * ``ValueError`` -- authenticator data too short or too long for its
#:   fields, base64url of a length no encoding has, CBOR's reserved
#:   additional-information values, a public key's point off its curve.
#: * ``TypeError`` -- a CBOR map keyed by a map, a public key's coordinate
#:   that is not a byte string.
#: * ``KeyError`` -- an attestation object or a public key missing a member,
#:   and CBOR's tagged items (major type 6), which fido2 does not decode.
#: * ``IndexError`` -- CBOR that ends before an item's first byte.
#: * ``AttributeError`` -- a credential public key that is not a CBOR map.
#: * ``struct.error`` -- CBOR that ends inside an item's length.
#: * ``RecursionError`` -- CBOR nested deeper than the interpreter recurses.
UNREADABLE = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    struct.error,
    RecursionError,
)


def _decoded(value: object) -> bytes:
    """Return the bytes base64url text *value* holds.

    Args:
        value: A field of the response, as JSON gave it.

    Returns:
        Its bytes.

    Raises:
        PasskeyRefused: ``MALFORMED``, when *value* is not base64url text.
    """
    if not isinstance(value, str) or not _BASE64URL.issuperset(value):
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    try:
        return websafe_decode(value)
    except ValueError as error:
        # binascii.Error: a length no base64 encoding has.
        raise PasskeyRefused(PasskeyRefusal.MALFORMED) from error


def read_credential(response_json: str) -> tuple[bytes, dict[str, object]]:
    """Return a credential's id and its ``response`` member, from its JSON.

    The shape WebAuthn's JSON gives a credential (``PublicKeyCredential``'s
    ``toJSON()``): an object of type ``public-key`` whose ``rawId`` is
    base64url text, whose ``id``, where present, is the same text, and whose
    ``response`` is an object.  Fields the service does not read
    (``authenticatorAttachment``, ``clientExtensionResults``) are never
    handed on.

    Args:
        response_json: The credential's JSON, as the browser sent it.

    Returns:
        The credential id's bytes, and the ``response`` object.

    Raises:
        PasskeyRefused: ``MALFORMED``, for any other shape.
    """
    try:
        credential = json.loads(response_json)
    except (ValueError, RecursionError) as error:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED) from error
    if not isinstance(credential, dict) or credential.get("type") != _PUBLIC_KEY:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    inner = credential.get("response")
    if not isinstance(inner, dict) or credential.get(
        "id", credential.get("rawId"),
    ) != credential.get("rawId"):
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    return _decoded(credential.get("rawId")), inner


def _client_data(raw: bytes) -> CollectedClientData:
    """Return fido2's reading of the client data *raw* holds.

    The client data must be a JSON object whose ``type``, ``challenge`` and
    ``origin`` are text, which fido2 does not ask: it decodes the challenge
    with ``bytes(value)``, so a number there would allocate that many bytes.

    Args:
        raw: The decoded ``clientDataJSON``.

    Returns:
        fido2's :class:`~fido2.webauthn.CollectedClientData`.

    Raises:
        PasskeyRefused: ``MALFORMED``.
    """
    try:
        members = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED) from error
    if not isinstance(members, dict) or not all(
        isinstance(members.get(member), str)
        for member in ("type", "challenge", "origin")
    ):
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    try:
        return CollectedClientData(raw)
    except UNREADABLE as error:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED) from error


def read_registration(response_json: str) -> RegistrationResponse:
    """Return the registration *response_json* holds, refusing one fido2 cannot complete.

    Besides the shape (:func:`read_credential`, :func:`_client_data`), the
    attestation object must be a CBOR map whose authenticator data is a byte
    string, because fido2 builds that data with ``bytes(value)``, and a CBOR
    integer there would allocate that many bytes; and it must be no longer
    than :data:`_ATTESTATION_OBJECT_MAX_BYTES`.  The authenticator data must
    carry the new credential, as a registration's always does, name it by the
    id the browser sent, and not say it is backed up and cannot be.

    Args:
        response_json: The browser's ``navigator.credentials.create()``
            result, as JSON.

    Returns:
        fido2's reading of it.

    Raises:
        PasskeyRefused: ``MALFORMED``.
    """
    raw_id, inner = read_credential(response_json)
    client_data = _client_data(_decoded(inner.get("clientDataJSON")))
    attestation = _decoded(inner.get("attestationObject"))
    if len(attestation) > _ATTESTATION_OBJECT_MAX_BYTES:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    try:
        decoded = cbor.decode(attestation)
        readable = isinstance(decoded, dict) and isinstance(decoded.get("authData"), bytes)
        registration = None if not readable else RegistrationResponse(
            raw_id=raw_id,
            response=AuthenticatorAttestationResponse(
                client_data=client_data,
                attestation_object=AttestationObject(attestation),
            ),
        )
    except UNREADABLE as error:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED) from error
    if registration is None:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    auth_data = registration.response.attestation_object.auth_data
    credential_data = auth_data.credential_data
    if credential_data is None or credential_data.credential_id != raw_id:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    _refuse_a_backup_never_eligible(auth_data)
    return registration


def read_assertion(response_json: str) -> AuthenticationResponse:
    """Return the assertion *response_json* holds, refusing one fido2 cannot complete.

    Besides the shape (:func:`read_credential`, :func:`_client_data`), the
    authenticator data must hold a flags byte and be no longer than
    :data:`_AUTHENTICATOR_DATA_MAX_BYTES`, it must not say it carries a new
    credential (an assertion never does, and fido2 would parse one as a
    public key), and it must not say it is backed up and cannot be.

    Args:
        response_json: The browser's ``navigator.credentials.get()`` result,
            as JSON.

    Returns:
        fido2's reading of it.  Its ``user_handle`` is ``None`` when the
        browser sent none.

    Raises:
        PasskeyRefused: ``MALFORMED``.
    """
    raw_id, inner = read_credential(response_json)
    client_data = _client_data(_decoded(inner.get("clientDataJSON")))
    authenticator_data = _decoded(inner.get("authenticatorData"))
    signature = _decoded(inner.get("signature"))
    user_handle = inner.get("userHandle")
    if user_handle is not None:
        user_handle = _decoded(user_handle)
    if (
        not _FLAGS_OFFSET < len(authenticator_data) <= _AUTHENTICATOR_DATA_MAX_BYTES
        or authenticator_data[_FLAGS_OFFSET] & AuthenticatorData.FLAG.AT
    ):
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    try:
        assertion = AuthenticationResponse(
            raw_id=raw_id,
            response=AuthenticatorAssertionResponse(
                client_data=client_data,
                authenticator_data=AuthenticatorData(authenticator_data),
                signature=signature,
                user_handle=user_handle,
            ),
        )
    except UNREADABLE as error:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED) from error
    _refuse_a_backup_never_eligible(assertion.response.authenticator_data)
    return assertion


def _refuse_a_backup_never_eligible(auth_data: AuthenticatorData) -> None:
    """Refuse authenticator data saying it is backed up but cannot be.

    Web Authentication Level 3's step that fido2 leaves to its caller, at a
    registration and at every assertion: a credential not eligible for backup
    (BE) cannot be backed up (BS).

    Args:
        auth_data: The authenticator data, as fido2 read it.

    Raises:
        PasskeyRefused: ``MALFORMED``.
    """
    if auth_data.is_backed_up() and not auth_data.is_backup_eligible():
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
