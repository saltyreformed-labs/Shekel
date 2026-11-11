"""A software authenticator for the passkey tests (plan#2538).

It does what a phone does in a passkey ceremony, from ``cryptography`` alone:
an EC P-256 key (COSE algorithm -7, ES256) makes a credential for the options
the service sent, and signs real ``authenticatorData`` and ``clientDataJSON``
for a sign-in.  Each response is the JSON a browser's ``toJSON()`` writes,
binary as unpadded base64url text, which is what the service reads.

**Its encodings are its own, not fido2's.**  The COSE key, the attestation
object and the authenticator data are written here byte by byte from the
specifications (RFC 8949 for CBOR, RFC 9052 for COSE, Web Authentication
Level 3 for the rest), so a test does not grade fido2 with fido2's own
encoder: a mistake that encoder and its parser shared would pass.

Every field a refusal test needs to get wrong can be: the origin, the relying
party id, the flags, the counter, the user handle, the ceremony's type, the
key, the credential id.  The defaults are a correct Apple passkey: user
present and verified, and a counter that stays at 0.
"""

import base64
import hashlib
import json
import os
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

#: The origin the suite's configuration names (``TestConfig.PASSKEY_ORIGIN``),
#: spelled out here so a moved setting fails a test rather than moving both.
ORIGIN = "https://shekel.test"

#: That origin's host: the relying party id the authenticator hashes.
RP_ID = "shekel.test"

#: Authenticator data flags (Web Authentication, section 6.1).
UP = 0x01
UV = 0x04
BE = 0x08
BS = 0x10
AT = 0x40
ED = 0x80

#: The COSE algorithm id of ES256, and of RS1 (SHA-1 RSA), which the options
#: never offer.
ES256 = -7
RS1 = -65535


def b64url(data: bytes) -> str:
    """Return *data* as unpadded base64url text, as WebAuthn's JSON writes binary."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    """Return the bytes unpadded base64url *text* holds."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _head(major: int, value: int) -> bytes:
    """Return a CBOR item's head: its major type and its argument (RFC 8949, 3)."""
    if value < 24:
        return bytes([major << 5 | value])
    if value < 0x100:
        return bytes([major << 5 | 24, value])
    if value < 0x10000:
        return bytes([major << 5 | 25]) + struct.pack(">H", value)
    if value < 0x100000000:
        return bytes([major << 5 | 26]) + struct.pack(">I", value)
    return bytes([major << 5 | 27]) + struct.pack(">Q", value)


def cbor_int(value: int) -> bytes:
    """Return *value* as a CBOR integer (major type 0 or 1)."""
    return _head(0, value) if value >= 0 else _head(1, -1 - value)


def cbor_bytes(value: bytes) -> bytes:
    """Return *value* as a CBOR byte string (major type 2)."""
    return _head(2, len(value)) + value


def cbor_text(value: str) -> bytes:
    """Return *value* as a CBOR text string (major type 3)."""
    encoded = value.encode("utf-8")
    return _head(3, len(encoded)) + encoded


def cbor_map(items: list[tuple[bytes, bytes]]) -> bytes:
    """Return a CBOR map (major type 5) of already-encoded keys and values."""
    return _head(5, len(items)) + b"".join(key + value for key, value in items)


def attestation_object(auth_data: bytes, auth_data_item: bytes | None = None) -> bytes:
    """Return a ``none`` attestation object holding *auth_data*.

    Args:
        auth_data: The authenticator data.
        auth_data_item: The encoded CBOR item to put under ``authData``
            instead of *auth_data* as a byte string, for a test that needs it
            to be something else.
    """
    return cbor_map([
        (cbor_text("fmt"), cbor_text("none")),
        (cbor_text("attStmt"), cbor_map([])),
        (cbor_text("authData"),
         cbor_bytes(auth_data) if auth_data_item is None else auth_data_item),
    ])


def padding(length: int) -> bytes:
    """Return extension outputs exactly *length* bytes long: ``{"x": h'00...'}``.

    For a test that needs authenticator data or an attestation object of an
    exact size.  At least 7 bytes: the map, the key and a byte string's head.
    """
    for fill in range(length, -1, -1):
        encoded = cbor_map([(cbor_text("x"), cbor_bytes(bytes(fill)))])
        if len(encoded) == length:
            return encoded
    raise ValueError(f"no padding is {length} bytes long")


def client_data(kind: str, challenge: str, origin: str) -> bytes:
    """Return ``clientDataJSON`` as a browser writes it."""
    return json.dumps({
        "type": kind, "challenge": challenge, "origin": origin,
        "crossOrigin": False,
    }, separators=(",", ":")).encode("utf-8")


class SoftwareAuthenticator:
    """One passkey, held in software: its key, its credential id, its counter.

    Attributes:
        credential_id: The id it names the passkey by.
        user_handle: The handle the registration options named, kept and
            returned at each sign-in as a discoverable passkey does.
        counter: Its signature counter as last reported.
        counts: Whether the counter rises at each signature.  Apple's
            passkeys never count, so the default is False.
    """

    def __init__(self, *, counts: bool = False, credential_id: bytes | None = None):
        """Make a new key and credential id.

        Args:
            counts: Whether the counter rises at each signature.
            credential_id: The id to use, or ``None`` for 16 random bytes.
        """
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16) if credential_id is None else credential_id
        self.user_handle: bytes | None = None
        self.counter = 0
        self.counts = counts

    def public_numbers(self) -> ec.EllipticCurvePublicNumbers:
        """Return the public key's curve point, for a test that builds a key of its own."""
        return self._key.public_key().public_numbers()

    def cose_key(self, alg: int = ES256) -> bytes:
        """Return the public key as a COSE EC2 key (RFC 9052, section 7.1).

        Args:
            alg: The algorithm id to label it with.
        """
        numbers = self.public_numbers()
        return cbor_map([
            (cbor_int(1), cbor_int(2)),      # kty: EC2
            (cbor_int(3), cbor_int(alg)),    # alg
            (cbor_int(-1), cbor_int(1)),     # crv: P-256
            (cbor_int(-2), cbor_bytes(numbers.x.to_bytes(32, "big"))),
            (cbor_int(-3), cbor_bytes(numbers.y.to_bytes(32, "big"))),
        ])

    def _next_counter(self) -> int:
        """Advance the counter if it counts, and return it."""
        if self.counts:
            self.counter += 1
        return self.counter

    def create(
        self, options: dict[str, object], *, origin: str = ORIGIN, rp_id: str = RP_ID,
        flags: int = UP | UV | AT, kind: str = "webauthn.create",
        cose_key: bytes | None = None, raw_id: bytes | None = None,
        extensions: bytes = b"",
    ) -> dict[str, object]:
        """Answer *options* (``registration_options``'s) with a new credential.

        Args:
            options: The options, as the service returned them.
            origin: The origin the browser writes into the client data.
            rp_id: The relying party id whose hash the authenticator writes.
            flags: The authenticator data flags.
            kind: The client data's ``type``.
            cose_key: The public key's encoding, or ``None`` for this key's.
            raw_id: The id the browser reports, or ``None`` for the
                credential's own.
            extensions: Encoded CBOR to append as extension outputs; when
                given, the ED flag is set.

        Returns:
            The registration response's JSON, as a dict.
        """
        public = options["publicKey"]
        self.user_handle = unb64url(public["user"]["id"])
        auth_data = (
            hashlib.sha256(rp_id.encode("ascii")).digest()
            + struct.pack(">BI", flags | (ED if extensions else 0), self._next_counter())
            + bytes(16)
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + (self.cose_key() if cose_key is None else cose_key)
            + extensions
        )
        return self.registration(
            client_data(kind, public["challenge"], origin),
            attestation_object(auth_data),
            raw_id=raw_id,
        )

    def registration(
        self, client_data_json: bytes, attestation: bytes, *, raw_id: bytes | None = None,
    ) -> dict[str, object]:
        """Return a registration response's JSON from its two binary members."""
        identifier = b64url(self.credential_id if raw_id is None else raw_id)
        return {
            "id": identifier,
            "rawId": identifier,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data_json),
                "attestationObject": b64url(attestation),
            },
            "clientExtensionResults": {},
        }

    def get(
        self, options: dict[str, object], *, origin: str = ORIGIN, rp_id: str = RP_ID,
        flags: int = UP | UV, kind: str = "webauthn.get",
        user_handle: bytes | None | str = "kept", counter: int | None = None,
        extensions: bytes = b"",
    ) -> dict[str, object]:
        """Answer *options* (a sign-in's or a re-confirm's) with a signature.

        Args:
            options: The options, as the service returned them.
            origin: The origin the browser writes into the client data.
            rp_id: The relying party id whose hash the authenticator writes.
            flags: The authenticator data flags.
            kind: The client data's ``type``.
            user_handle: The handle to return: ``"kept"`` for the one the
                registration named, ``None`` to return none, or other bytes.
            counter: The counter to report, or ``None`` for the next one.
            extensions: Encoded CBOR to append as extension outputs; when
                given, the ED flag is set.

        Returns:
            The authentication response's JSON, as a dict.
        """
        client_data_json = client_data(kind, options["publicKey"]["challenge"], origin)
        auth_data = hashlib.sha256(rp_id.encode("ascii")).digest() + struct.pack(
            ">BI", flags | (ED if extensions else 0),
            self._next_counter() if counter is None else counter,
        ) + extensions
        return self.assertion(
            client_data_json, auth_data,
            self.user_handle if user_handle == "kept" else user_handle,
        )

    def sign(self, message: bytes) -> bytes:
        """Return this key's ECDSA signature over *message* (DER, SHA-256)."""
        return self._key.sign(message, ec.ECDSA(hashes.SHA256()))

    def assertion(
        self, client_data_json: bytes, auth_data: bytes, user_handle: bytes | None,
    ) -> dict[str, object]:
        """Return an authentication response's JSON, signing *auth_data* and the client data."""
        response = {
            "clientDataJSON": b64url(client_data_json),
            "authenticatorData": b64url(auth_data),
            "signature": b64url(
                self.sign(auth_data + hashlib.sha256(client_data_json).digest())
            ),
        }
        if user_handle is not None:
            response["userHandle"] = b64url(user_handle)
        identifier = b64url(self.credential_id)
        return {
            "id": identifier,
            "rawId": identifier,
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
        }
