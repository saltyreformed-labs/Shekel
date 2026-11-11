"""
Shekel Budget App -- What every passkey ceremony shares.

The relying party (:func:`relying_party_id`, :func:`server`), the user handle,
the clock, a ceremony's state as the route keeps it, and the checks every
assertion meets, whichever ceremony asked for it: a sign-in's, a re-confirm's,
and the sign-in by a new passkey that adding one ends with (ruling plan#3525).
Its own module because both halves of the package walk it: the add
(:mod:`._adding`) and the authentications (:mod:`app.services.passkey_service`).

**The clock is the process's**, read by :func:`now` alone, so a test freezes
this module and every act sees the moment it names.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import NamedTuple
from urllib.parse import urlsplit

from fido2 import cbor
from fido2.cose import CoseKey
from fido2.server import Fido2Server
from fido2.webauthn import (
    Aaguid,
    AttestedCredentialData,
    AuthenticationResponse,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialRpEntity,
    PublicKeyCredentialType,
    UserVerificationRequirement,
)
from sqlalchemy import select

from app.extensions import db
from app.models.passkey import Passkey
from app.models.user import User
from app.services.passkey_service._refusals import PasskeyRefusal, PasskeyRefused


#: How long a challenge may be answered, from the moment it is issued.
CHALLENGE_LIFETIME = timedelta(minutes=5)

#: How long the browser is told to wait for the device (the options'
#: ``timeout``): the lifetime less a margin for the options' trip to the
#: browser and the answer's trip back, so a device that answers at the last
#: moment the browser allows still arrives in time.  A late add would leave
#: the device holding a passkey Shekel refused.
_BROWSER_TIMEOUT = CHALLENGE_LIFETIME - timedelta(seconds=30)

#: The name the device shows for the site beside its passkey.
_RELYING_PARTY_NAME = "Shekel"

#: Bytes in the user handle: a user's id, big-endian, fixed width, so one id
#: has one handle.
_USER_HANDLE_BYTES = 8

#: The ports a browser leaves out of an origin, by scheme.
_DEFAULT_PORTS = {"https": 443, "http": 80}

# The keys of a ceremony's state, as the route keeps it in its session.
_STATE_CEREMONY = "ceremony"
_STATE_CHALLENGE = "challenge"
_STATE_ISSUED_AT = "issued_at"
_STATE_USER_ID = "user_id"


class Ceremony(Enum):
    """Which ceremony a state was issued for: the one completion it answers.

    Each state names its own, and each completion refuses a state of another
    (:func:`read_state`), so the state of one ceremony completes no other,
    whatever the route keeps under which key.
    """

    #: The add's first act: the device makes a passkey.
    REGISTRATION = "registration"
    #: The add's second act: the sign-in by the new passkey that keeps it.
    ADD_SIGN_IN = "add_sign_in"
    #: A sign-in, naming no account.
    SIGN_IN = "sign_in"
    #: A re-confirm by the signed-in account, naming its passkeys.
    RE_CONFIRM = "re_confirm"


class Issued(NamedTuple):
    """A ceremony's state as this package issued it.

    Attributes:
        challenge: The challenge, as base64url text.
        issued_at: When it was issued.
        user_id: The account an add was begun for; ``None`` for a sign-in or
            a re-confirm.
    """

    challenge: str
    issued_at: datetime
    user_id: int | None


def now() -> datetime:
    """Return the process's clock, in UTC: this package's one read of it.

    Each act reads it once, so one act has one moment, and a challenge an act
    issues is issued at that moment.

    Returns:
        The present moment, timezone-aware.
    """
    return datetime.now(timezone.utc)


def relying_party_id(origin: str) -> str:
    """Return the relying party id of *origin*: its host.

    *origin* must be an origin as a browser writes one in a ceremony's client
    data, because the completion compares the two exactly: a scheme, ``://``,
    a lower-case host and a port only where it is not the scheme's own, with
    no path, not even a trailing slash.  The scheme is ``https``, or ``http``
    for ``localhost`` alone, the only insecure origin a browser lets use a
    passkey.

    Args:
        origin: The site's origin, as configuration names it
            (``PASSKEY_ORIGIN``).

    Returns:
        Its host, for example ``shekel.example.com``.

    Raises:
        ValueError: *origin* is not such an origin.  The message names the
            setting and the value.
    """
    refusal = ValueError(
        "PASSKEY_ORIGIN must be an origin as a browser writes it, such as "
        "https://shekel.example.com: lower case, ASCII (a browser writes an "
        "international name as punycode), https (http only for localhost), "
        f"no path and no trailing slash; got {origin!r}."
    )
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError as error:
        # A bracketed host that is not an address, or a port not a number.
        raise refusal from error
    host = parts.hostname
    secure = parts.scheme == "https" or (
        parts.scheme == "http" and host == "localhost"
    )
    written = f"{parts.scheme}://{host}" + ("" if port is None else f":{port}")
    if (
        host is None or not secure or not origin.isascii() or origin != written
        or port == _DEFAULT_PORTS[parts.scheme]
    ):
        raise refusal
    return host


def server(origin: str) -> Fido2Server:
    """Return the ceremony server for *origin*, which accepts that origin alone.

    Args:
        origin: The site's origin (:func:`relying_party_id` says which).

    Returns:
        A :class:`~fido2.server.Fido2Server` for *origin*'s host, telling the
        browser to give up after :data:`_BROWSER_TIMEOUT`.
    """

    def is_this_site(candidate: str) -> bool:
        """Return whether the client data's *candidate* origin is *origin*."""
        return candidate == origin

    ceremonies = Fido2Server(
        PublicKeyCredentialRpEntity(
            name=_RELYING_PARTY_NAME, id=relying_party_id(origin),
        ),
        verify_origin=is_this_site,
    )
    ceremonies.timeout = _BROWSER_TIMEOUT // timedelta(milliseconds=1)
    return ceremonies


def user_handle(user_id: int) -> bytes:
    """Return the WebAuthn user handle of *user_id*: the one derivation of it.

    Derived, never stored (CLAUDE.md rule 14).  It names no one outside this
    site, and ids are never reused, so a handle names one account for ever.

    Args:
        user_id: The ``auth.users.id``.

    Returns:
        The id as :data:`_USER_HANDLE_BYTES` big-endian bytes.
    """
    return user_id.to_bytes(_USER_HANDLE_BYTES, "big")


def issue(
    begun: Mapping[str, object], ceremony: Ceremony,
    *, user_id: int | None, issued_at: datetime,
) -> dict[str, str | int]:
    """Return the state the route keeps for the ceremony fido2 just began.

    Args:
        begun: What fido2's ``register_begin`` or ``authenticate_begin``
            returned beside the options.
        ceremony: Which ceremony it is.
        user_id: The account an add is begun for, or ``None``.
        issued_at: The moment of the act issuing it (:func:`now`).

    Returns:
        The ceremony, the challenge and the moment it was issued, and
        *user_id* where one is given: plain text and an integer, which a
        session keeps as they are.
    """
    state: dict[str, str | int] = {
        _STATE_CEREMONY: ceremony.value,
        _STATE_CHALLENGE: begun["challenge"],
        _STATE_ISSUED_AT: issued_at.isoformat(),
    }
    if user_id is not None:
        state[_STATE_USER_ID] = user_id
    return state


def read_state(
    state: Mapping[str, str | int] | None, moment: datetime, ceremony: Ceremony,
) -> Issued:
    """Return the *ceremony* *state* names, refusing none, another's or an expired one.

    A state of another ceremony is refused as no state at all: this
    ceremony is not in flight, whatever else is.

    Args:
        state: What :func:`issue` returned, as the route kept it, or ``None``
            when it kept none (a second submit, a session that ended).
        moment: The completion's moment.
        ceremony: The ceremony being completed.

    Returns:
        The issued ceremony.

    Raises:
        PasskeyRefused: ``EXPIRED``, when there is no state of *ceremony*'s
            or the challenge is :data:`CHALLENGE_LIFETIME` old or older.
    """
    if state is None or state.get(_STATE_CEREMONY) != ceremony.value:
        raise PasskeyRefused(PasskeyRefusal.EXPIRED)
    issued = Issued(
        challenge=state[_STATE_CHALLENGE],
        issued_at=datetime.fromisoformat(state[_STATE_ISSUED_AT]),
        user_id=state.get(_STATE_USER_ID),
    )
    if moment >= issued.issued_at + CHALLENGE_LIFETIME:
        raise PasskeyRefused(PasskeyRefusal.EXPIRED)
    return issued


def fido_state(issued: Issued) -> dict[str, object]:
    """Return the state fido2's completion checks against, user verification required.

    Built here rather than kept, so the requirement is this package's at
    every completion and never a value read back from a session.

    Args:
        issued: The ceremony being completed.

    Returns:
        fido2's state: the challenge, and user verification required.
    """
    return {
        "challenge": issued.challenge,
        "user_verification": UserVerificationRequirement.REQUIRED,
    }


def descriptors(user_id: int) -> list[PublicKeyCredentialDescriptor]:
    """Return a descriptor of each of *user_id*'s passkeys, for a ceremony's options.

    Args:
        user_id: The account whose passkeys to name.

    Returns:
        One descriptor per passkey, by credential id.
    """
    return [
        PublicKeyCredentialDescriptor(
            type=PublicKeyCredentialType.PUBLIC_KEY, id=credential_id,
        )
        for credential_id in db.session.execute(
            select(Passkey.credential_id)
            .where(Passkey.user_id == user_id)
            .order_by(Passkey.id)
        ).scalars()
    ]


def sign_in_ceremony(origin: str) -> tuple[dict[str, object], Mapping[str, object]]:
    """Begin an authentication that names no passkey, the person verified.

    A sign-in's, and the add's sign-in by its new passkey: no credential is
    listed, so the device offers whichever passkeys it holds for this site,
    and must find the one chosen on its own.

    Args:
        origin: The site's origin, from configuration.

    Returns:
        The options for ``navigator.credentials.get()``, as WebAuthn's JSON,
        and what fido2 returned beside them, for :func:`issue`.
    """
    options, begun = server(origin).authenticate_begin(
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return dict(options), begun


def attested(credential_id: bytes, public_key: bytes) -> AttestedCredentialData:
    """Return the credential fido2 checks a signature by, from a passkey's id and key.

    Args:
        credential_id: The passkey's id.
        public_key: Its COSE key, encoded as the table keeps it.

    Returns:
        The credential, with no authenticator model named.
    """
    return AttestedCredentialData.create(
        Aaguid.NONE, credential_id, CoseKey.parse(cbor.decode(public_key)),
    )


def verify_signature(
    assertion: AuthenticationResponse, credential: AttestedCredentialData,
    *, issued: Issued, origin: str,
) -> None:
    """Refuse *assertion* unless *credential* signed it, answering *issued*, here.

    fido2's checks, which every assertion meets, a sign-in's, a re-confirm's
    and the add's own sign-in: the ceremony's type, this site's origin, the
    challenge, the relying party's hash, the person present and verified, and
    the signature.

    Args:
        assertion: The answer, as :func:`._responses.read_assertion` read it.
        credential: The passkey it must be by (:func:`attested`).
        issued: The ceremony it answers.
        origin: The site's origin, from configuration.

    Raises:
        PasskeyRefused: ``NOT_VERIFIED``.
    """
    try:
        server(origin).authenticate_complete(fido_state(issued), [credential], assertion)
    except ValueError as error:
        raise PasskeyRefused(PasskeyRefusal.NOT_VERIFIED) from error


def refuse_unless_by(
    assertion: AuthenticationResponse, user: User, *, handle_required: bool,
) -> None:
    """Refuse a verified *assertion* unless it is by *user*, an account that may sign in.

    Its user handle must be *user*'s, and *user* must be active.  Asked only
    of an answer whose signature is verified (:func:`verify_signature`), so
    neither refusal tells a stranger anything about an account.

    Args:
        assertion: The answer, its signature verified.
        user: The account the passkey is for.
        handle_required: Whether the authenticator must return a handle at
            all: where the ceremony named no passkey, it is the
            authenticator's statement of whose passkey signed.  Where it named
            the account's, WebAuthn checks a handle only where one is returned.

    Raises:
        PasskeyRefused: ``WRONG_USER_HANDLE``, ``ACCOUNT_DISABLED``.
    """
    handle = assertion.response.user_handle
    if handle != user_handle(user.id) and (handle is not None or handle_required):
        raise PasskeyRefused(PasskeyRefusal.WRONG_USER_HANDLE)
    if not user.is_active:
        raise PasskeyRefused(PasskeyRefusal.ACCOUNT_DISABLED)


def refuse_a_counter_that_did_not_rise(held: int, reported: int) -> None:
    """Refuse a counting key's signature whose counter did not rise past *held*.

    A key whose counter is 0 does not count (Apple's never do), and any
    counter it reports is accepted.  One that counts must rise, or the key
    may have been copied.

    Args:
        held: The counter the passkey last reported.
        reported: The counter this signature reports.

    Raises:
        PasskeyRefused: ``COUNTER_DID_NOT_RISE``.
    """
    if held != 0 and reported <= held:
        raise PasskeyRefused(PasskeyRefusal.COUNTER_DID_NOT_RISE)
