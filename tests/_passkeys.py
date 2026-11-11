"""A software authenticator for the passkey tests (plan#2538).

It does what a phone does in a passkey ceremony, from ``cryptography`` alone:
a key of any algorithm the options offer (:data:`OFFERED`; an EC P-256 key,
COSE algorithm -7, ES256, by default, as an Apple passkey's is) makes a
credential for the options the service sent, and signs real
``authenticatorData`` and ``clientDataJSON`` for a sign-in.  Each response is the JSON a browser's ``toJSON()`` writes,
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
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, mldsa, rsa
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding

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

#: The other COSE algorithm ids the options offer (IANA's COSE Algorithms
#: registry).
EDDSA = -8
ES384 = -35
ES512 = -36
ES256K = -47
PS256 = -37
RS256 = -257
MLDSA44 = -48
MLDSA65 = -49
MLDSA87 = -50

#: Every algorithm the registration options offer, in their order (fido2's
#: default list), spelled out so a change to it fails a test.
OFFERED = (ES256, EDDSA, ES384, ES512, PS256, RS256, ES256K, MLDSA44, MLDSA65, MLDSA87)

#: COSE key types (IANA's COSE Key Types registry).
_OKP = 1
_EC2 = 2
_RSA = 3
_AKP = 7

#: Each EC algorithm's curve, its COSE curve id, its coordinates' length in
#: bytes and its hash (RFC 9053, 2.1 and 7.1; RFC 8812 for secp256k1).
_EC = {
    ES256: (ec.SECP256R1, 1, 32, hashes.SHA256),
    ES384: (ec.SECP384R1, 2, 48, hashes.SHA384),
    ES512: (ec.SECP521R1, 3, 66, hashes.SHA512),
    ES256K: (ec.SECP256K1, 8, 32, hashes.SHA256),
}

#: Each ML-DSA algorithm's key class (FIPS 204).
_ML_DSA = {
    MLDSA44: mldsa.MLDSA44PrivateKey,
    MLDSA65: mldsa.MLDSA65PrivateKey,
    MLDSA87: mldsa.MLDSA87PrivateKey,
}

#: Ed25519's COSE curve id (RFC 9053, 7.2).
_ED25519 = 6

#: The bits of the RSA keys made, the least RS256 and PS256 keys are made with.
_RSA_BITS = 2048
_RSA_EXPONENT = 65537


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
        algorithm: The COSE algorithm its key signs by.
        credential_id: The id it names the passkey by.
        user_handle: The handle the registration options named, kept and
            returned at each sign-in as a discoverable passkey does.
        counter: Its signature counter as last reported.
        counts: Whether the counter rises at each signature.  Apple's
            passkeys never count, so the default is False.
    """

    def __init__(
        self, *, counts: bool = False, credential_id: bytes | None = None,
        algorithm: int = ES256,
    ):
        """Make a new key and credential id.

        Args:
            counts: Whether the counter rises at each signature.
            credential_id: The id to use, or ``None`` for 16 random bytes.
            algorithm: The COSE algorithm of the key, one of :data:`OFFERED`.
        """
        self.algorithm = algorithm
        if algorithm in _EC:
            self._key = ec.generate_private_key(_EC[algorithm][0]())
        elif algorithm == EDDSA:
            self._key = ed25519.Ed25519PrivateKey.generate()
        elif algorithm in (PS256, RS256):
            self._key = rsa.generate_private_key(_RSA_EXPONENT, _RSA_BITS)
        else:
            self._key = _ML_DSA[algorithm].generate()
        self.credential_id = os.urandom(16) if credential_id is None else credential_id
        self.user_handle: bytes | None = None
        self.counter = 0
        self.counts = counts

    def public_numbers(self) -> ec.EllipticCurvePublicNumbers:
        """Return an EC key's curve point, for a test that builds a key of its own."""
        return self._key.public_key().public_numbers()

    def _key_members(self) -> tuple[int, list[tuple[bytes, bytes]]]:
        """Return the key's COSE key type and its public members, encoded (RFC 9053, 7)."""
        public = self._key.public_key()
        if self.algorithm in _EC:
            _, curve, size, _ = _EC[self.algorithm]
            numbers = public.public_numbers()
            return _EC2, [
                (cbor_int(-1), cbor_int(curve)),
                (cbor_int(-2), cbor_bytes(numbers.x.to_bytes(size, "big"))),
                (cbor_int(-3), cbor_bytes(numbers.y.to_bytes(size, "big"))),
            ]
        if self.algorithm == EDDSA:
            return _OKP, [
                (cbor_int(-1), cbor_int(_ED25519)),
                (cbor_int(-2), cbor_bytes(public.public_bytes_raw())),
            ]
        if self.algorithm in (PS256, RS256):
            numbers = public.public_numbers()
            return _RSA, [
                (cbor_int(-1), cbor_bytes(
                    numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big"),
                )),
                (cbor_int(-2), cbor_bytes(
                    numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big"),
                )),
            ]
        return _AKP, [(cbor_int(-1), cbor_bytes(public.public_bytes_raw()))]

    def cose_key(
        self, alg: int | None = None, *, kty: int | None = None,
        extra: tuple[tuple[bytes, bytes], ...] = (),
    ) -> bytes:
        """Return the public key as a COSE key (RFC 9052, section 7), its members in canonical order.

        Args:
            alg: The algorithm id to label it with, or ``None`` for its own.
            kty: The key type to label it with, or ``None`` for its own.
            extra: Encoded members to append after its own, for a test of a
                key an authenticator padded.
        """
        key_type, members = self._key_members()
        return cbor_map([
            (cbor_int(1), cbor_int(key_type if kty is None else kty)),
            (cbor_int(3), cbor_int(self.algorithm if alg is None else alg)),
            *members,
            *extra,
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
        """Return this key's signature over *message*, as its algorithm signs (RFC 9053, 2)."""
        if self.algorithm in _EC:
            return self._key.sign(message, ec.ECDSA(_EC[self.algorithm][3]()))
        if self.algorithm == PS256:
            return self._key.sign(
                message,
                rsa_padding.PSS(
                    mgf=rsa_padding.MGF1(hashes.SHA256()),
                    salt_length=rsa_padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        if self.algorithm == RS256:
            return self._key.sign(message, rsa_padding.PKCS1v15(), hashes.SHA256())
        return self._key.sign(message)

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
