"""
Shekel Budget App -- Passkey Service

A passkey is a key a person's phone or security key made for this site, and
it signs Shekel's challenge when they unlock it with Face ID, a fingerprint or
the device's passcode (plan#2538).  This package holds every act on one:
adding it (the registration ceremony), signing in and re-confirming with it
(the authentication ceremony), listing and removing a user's own, and the one
statement of whether an account holds any.

**What a passkey is worth** (rulings plan#3465, plan#3467).  It signs in on its
own, with no password and no code, because user verification is required at
every ceremony: the device's face, finger or passcode is the second factor.
An account that holds one signs in only by passkey, and :func:`passkey_held_by`
is the one statement of that.

**The ceremonies' checks are fido2's** (ruling plan#3475): the signature, the
challenge, the relying party's hash, user presence and user verification,
through :class:`fido2.server.Fido2Server`.  This package adds what that server
leaves to its caller, each a step of the Web Authentication specification:

* **The origin, exactly.**  fido2's default accepts any subdomain of the
  relying party; this server accepts the one origin configuration names and
  nothing else.  The relying party id is that origin's host
  (:func:`relying_party_id`), from configuration and never from the request's
  ``Host`` header, so a request that names another host still gets this site's
  ceremony.
* **The credential.**  A sign-in finds it by its id and its user by it; a
  re-confirm finds it only among the signed-in user's own.  The user handle
  the authenticator returns must be the one :func:`_user_handle` derives for
  that user.  The user must be active.
* **One answer, once.**  Each challenge's state carries the moment it was
  issued, and a completion refuses a challenge issued at or before the
  passkey's ``last_used_at``, then moves ``last_used_at`` to now: so a signed
  answer cannot sign in twice, which the signature counter cannot promise
  because Apple's passkeys always report 0.  A counter that does count must
  rise, or the key may have been copied.  A challenge lives
  :data:`CHALLENGE_LIFETIME`.
* **Adding.**  Up to :data:`MAX_PASSKEYS` per account (ruling plan#3472),
  every one already added excluded so the device says so rather than making a
  second, a credential id no account holds yet, and a public key of an
  algorithm the options offered that loads as a key: no attestation is asked
  for, so nothing at registration has checked it.  A key can load and still
  never verify (an RSA modulus too small to sign); whether a new passkey must
  sign once before it closes the password sign-in is question plan#3525.

**What the browser sent is read first**, by :mod:`._responses`, because fido2
would allocate a buffer as large as any number a stranger puts where it
expects bytes; that module says how.

**Locks.**  The owner's lock of the account is the first lock each act's
transaction holds, and this package never takes it
(``app.services.user_write_lock`` names its one home): a signed-in request's
command transaction holds it from its start (``app.db_transaction``), and a
sign-in reads the account by :func:`sign_in_account`, takes it
(``app.db_transaction.bind_sign_in_owner``), and only then completes.  But a
companion whose owner is gone has no owner to lock, so the two decisions that
must not race take row locks of their own, after it: a completion reads the
passkey ``FOR UPDATE``, so two completions of one answer cannot both read it
before either moves its ``last_used_at``; and an add locks the account's own
row before it counts, so two adds at once cannot both pass the cap.

**The clock is the process's**, ``datetime.now(timezone.utc)``, read once per
act in this module alone, and the state's issue time is read from it too.

Architecture:
  - No Flask imports.  Takes plain ids, strings and the response's JSON text;
    returns rows, booleans, and the options and state the route hands the
    browser and keeps in its session.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from fido2 import cbor
from fido2.cose import CoseKey
from fido2.server import Fido2Server
from fido2.webauthn import (
    Aaguid,
    AttestedCredentialData,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialRpEntity,
    PublicKeyCredentialType,
    PublicKeyCredentialUserEntity,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.dialects.postgresql import insert

from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.passkey import CREDENTIAL_ID_MAX_BYTES, NAME_MAX_LENGTH, Passkey
from app.models.user import User
from app.services.passkey_service._refusals import (
    MAX_PASSKEYS,
    PasskeyRefusal,
    PasskeyRefused,
)
from app.services.passkey_service._responses import (
    UNREADABLE,
    read_assertion,
    read_credential,
    read_registration,
)

__all__ = [
    "CHALLENGE_LIFETIME",
    "MAX_PASSKEYS",
    "PasskeyRefusal",
    "PasskeyRefused",
    "add_passkey",
    "complete_re_confirm",
    "complete_sign_in",
    "has_passkey",
    "list_passkeys",
    "passkey_held_by",
    "re_confirm_options",
    "registration_options",
    "relying_party_id",
    "remove_passkey",
    "sign_in_account",
    "sign_in_options",
]


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
_STATE_CHALLENGE = "challenge"
_STATE_ISSUED_AT = "issued_at"
_STATE_USER_ID = "user_id"


class _Issued(NamedTuple):
    """A ceremony's state as this module issued it.

    Attributes:
        challenge: The challenge, as base64url text.
        issued_at: When it was issued.
        user_id: The account an add was begun for; ``None`` for a sign-in or
            a re-confirm.
    """

    challenge: str
    issued_at: datetime
    user_id: int | None


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


def _server(origin: str) -> Fido2Server:
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

    server = Fido2Server(
        PublicKeyCredentialRpEntity(
            name=_RELYING_PARTY_NAME, id=relying_party_id(origin),
        ),
        verify_origin=is_this_site,
    )
    server.timeout = _BROWSER_TIMEOUT // timedelta(milliseconds=1)
    return server


def _user_handle(user_id: int) -> bytes:
    """Return the WebAuthn user handle of *user_id*: the one derivation of it.

    Derived, never stored (CLAUDE.md rule 14).  It names no one outside this
    site, and ids are never reused, so a handle names one account for ever.

    Args:
        user_id: The ``auth.users.id``.

    Returns:
        The id as :data:`_USER_HANDLE_BYTES` big-endian bytes.
    """
    return user_id.to_bytes(_USER_HANDLE_BYTES, "big")


def _issue(fido_state: Mapping[str, object], user_id: int | None) -> dict[str, str | int]:
    """Return the state the route keeps for the ceremony fido2 just began.

    Args:
        fido_state: What fido2's ``register_begin`` or ``authenticate_begin``
            returned beside the options.
        user_id: The account an add is begun for, or ``None``.

    Returns:
        The challenge and the moment it was issued, and *user_id* where one
        is given: plain text and an integer, which a session keeps as they are.
    """
    state: dict[str, str | int] = {
        _STATE_CHALLENGE: fido_state["challenge"],
        _STATE_ISSUED_AT: datetime.now(timezone.utc).isoformat(),
    }
    if user_id is not None:
        state[_STATE_USER_ID] = user_id
    return state


def _read_state(state: Mapping[str, str | int] | None, now: datetime) -> _Issued:
    """Return the ceremony *state* names, refusing none or an expired one.

    Args:
        state: What :func:`_issue` returned, as the route kept it, or ``None``
            when it kept none (a second submit, a session that ended).
        now: The completion's moment.

    Returns:
        The issued ceremony.

    Raises:
        PasskeyRefused: ``EXPIRED``, when there is no state or the challenge
            is :data:`CHALLENGE_LIFETIME` old or older.
    """
    if state is None:
        raise PasskeyRefused(PasskeyRefusal.EXPIRED)
    issued = _Issued(
        challenge=state[_STATE_CHALLENGE],
        issued_at=datetime.fromisoformat(state[_STATE_ISSUED_AT]),
        user_id=state.get(_STATE_USER_ID),
    )
    if now >= issued.issued_at + CHALLENGE_LIFETIME:
        raise PasskeyRefused(PasskeyRefusal.EXPIRED)
    return issued


def _fido_state(issued: _Issued) -> dict[str, object]:
    """Return the state fido2's completion checks against, user verification required.

    Built here rather than kept, so the requirement is this module's at every
    completion and never a value read back from a session.

    Args:
        issued: The ceremony being completed.

    Returns:
        fido2's state: the challenge, and user verification required.
    """
    return {
        "challenge": issued.challenge,
        "user_verification": UserVerificationRequirement.REQUIRED,
    }


def _descriptors(user_id: int) -> list[PublicKeyCredentialDescriptor]:
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


def _refuse_a_full_account(user_id: int) -> None:
    """Refuse an add to an account that holds :data:`MAX_PASSKEYS` already.

    Asked when the add's options are made, so the device is never asked to
    make a passkey the completion would refuse (it would keep it, and Shekel
    would not), and again at the completion, under the owner's lock, which is
    the answer two adds at once both meet.

    Args:
        user_id: The account adding a passkey.

    Raises:
        PasskeyRefused: ``TOO_MANY``.
    """
    held = db.session.execute(
        select(func.count()).select_from(Passkey).where(Passkey.user_id == user_id)
    ).scalar_one()
    if held >= MAX_PASSKEYS:
        raise PasskeyRefused(PasskeyRefusal.TOO_MANY)


def _checked_name(name: str) -> str:
    """Return *name* as a passkey keeps it, refusing one the table would refuse.

    Args:
        name: The name typed for the passkey.

    Returns:
        *name* without the white space around it.

    Raises:
        ValidationError: The name is blank, longer than
            :data:`~app.models.passkey.NAME_MAX_LENGTH` characters, or holds
            a NUL, which PostgreSQL text cannot.
    """
    kept = name.strip()
    if not kept:
        raise ValidationError("Give the passkey a name.")
    if len(kept) > NAME_MAX_LENGTH:
        raise ValidationError(
            f"A passkey's name can be at most {NAME_MAX_LENGTH} characters."
        )
    if "\x00" in kept:
        raise ValidationError("A passkey's name cannot hold a NUL character.")
    return kept


def _refuse_an_unusable_key(public_key: CoseKey, offered: set[int]) -> None:
    """Refuse a new passkey's public key that no sign-in could verify by.

    Its algorithm must be one the options offered, an integer (a CBOR array
    there is no algorithm, and no set can hold one), and the key must load:
    asked to verify an empty signature over an empty message, a key that
    loads refuses the signature (``InvalidSignature``), and a malformed one
    raises while it is built, which would otherwise be every sign-in's error.
    Loading is all this proves (question plan#3525).

    Args:
        public_key: The key the authenticator made, as fido2 read it.
        offered: The algorithm ids the registration options offered.

    Raises:
        PasskeyRefused: ``UNOFFERED_ALGORITHM`` or ``UNUSABLE_KEY``.
    """
    algorithm = public_key.get(3)
    if not isinstance(algorithm, int) or algorithm not in offered:
        raise PasskeyRefused(PasskeyRefusal.UNOFFERED_ALGORITHM)
    try:
        public_key.verify(b"", b"")
    except InvalidSignature:
        return
    except UNREADABLE as error:
        raise PasskeyRefused(PasskeyRefusal.UNUSABLE_KEY) from error
    raise PasskeyRefused(PasskeyRefusal.UNUSABLE_KEY)


def registration_options(
    user: User, *, origin: str,
) -> tuple[dict[str, object], dict[str, str | int]]:
    """Begin adding a passkey to *user*'s account.

    User verification and a discoverable credential are both required, so
    the passkey can sign in with no email typed and no password; every
    passkey the account holds is excluded, so a device that holds one says so.

    Args:
        user: The signed-in account adding a passkey.
        origin: The site's origin, from configuration.

    Returns:
        The options for ``navigator.credentials.create()``, as WebAuthn's JSON
        (``{"publicKey": {...}}``, binary as base64url text), and the state
        the route keeps for :func:`add_passkey`.

    Raises:
        PasskeyRefused: ``TOO_MANY``.
    """
    _refuse_a_full_account(user.id)
    options, fido_state = _server(origin).register_begin(
        PublicKeyCredentialUserEntity(
            id=_user_handle(user.id),
            name=user.email,
            display_name=user.display_name or user.email,
        ),
        credentials=_descriptors(user.id),
        resident_key_requirement=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return dict(options), _issue(fido_state, user.id)


def add_passkey(
    user: User, response_json: str, name: str,
    *, origin: str, state: Mapping[str, str | int] | None,
) -> Passkey:
    """Complete adding a passkey to *user*'s account, named *name*.

    The account's own row is locked before the count, so two adds at once
    wait for each other there, an owner's lock held or not (the module
    docstring), and the count each reads is the one the other left.  The new
    row is written by one
    statement that also asks whether any account holds its credential id
    (``ON CONFLICT DO NOTHING`` on ``uq_passkeys_credential_id``), so two
    accounts adding one credential at once cannot both pass a check made
    before it: the constraint is asked, never tripped.

    Args:
        user: The signed-in account.
        response_json: The browser's ``navigator.credentials.create()``
            result, as JSON.
        name: The name typed for the passkey.
        origin: The site's origin, from configuration.
        state: What :func:`registration_options` returned, as the route kept
            it, or ``None``.

    Returns:
        The new :class:`~app.models.passkey.Passkey`, flushed.

    Raises:
        ValidationError: The name (:func:`_checked_name`).
        PasskeyRefused: ``EXPIRED``; ``WRONG_ACCOUNT`` for an add begun by
            another account in this browser; ``TOO_MANY``; ``MALFORMED``;
            ``NOT_VERIFIED``; ``UNOFFERED_ALGORITHM``; ``UNUSABLE_KEY``;
            ``ALREADY_ADDED``.
    """
    kept_name = _checked_name(name)
    now = datetime.now(timezone.utc)
    issued = _read_state(state, now)
    if issued.user_id != user.id:
        raise PasskeyRefused(PasskeyRefusal.WRONG_ACCOUNT)
    db.session.execute(select(User.id).where(User.id == user.id).with_for_update())
    _refuse_a_full_account(user.id)
    registration = read_registration(response_json)
    server = _server(origin)
    try:
        auth_data = server.register_complete(_fido_state(issued), registration)
    except ValueError as error:
        raise PasskeyRefused(PasskeyRefusal.NOT_VERIFIED) from error
    credential_data = auth_data.credential_data
    _refuse_an_unusable_key(
        credential_data.public_key,
        {parameters.alg for parameters in server.allowed_algorithms},
    )
    if not 0 < len(credential_data.credential_id) <= CREDENTIAL_ID_MAX_BYTES:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    passkey_id = db.session.execute(
        insert(Passkey)
        .values(
            user_id=user.id,
            credential_id=credential_data.credential_id,
            public_key=cbor.encode(credential_data.public_key),
            sign_count=auth_data.counter,
            name=kept_name,
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_passkeys_credential_id")
        .returning(Passkey.id)
    ).scalar_one_or_none()
    if passkey_id is None:
        raise PasskeyRefused(PasskeyRefusal.ALREADY_ADDED)
    return db.session.get(Passkey, passkey_id)


def sign_in_options(*, origin: str) -> tuple[dict[str, object], dict[str, str | int]]:
    """Begin a passkey sign-in, naming no account.

    No credential is listed, so the device offers whichever passkeys it holds
    for this site, and the one chosen names its account.

    Args:
        origin: The site's origin, from configuration.

    Returns:
        The options for ``navigator.credentials.get()``, as WebAuthn's JSON,
        and the state the route keeps for :func:`complete_sign_in`.
    """
    options, fido_state = _server(origin).authenticate_begin(
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return dict(options), _issue(fido_state, None)


def re_confirm_options(
    user_id: int, *, origin: str,
) -> tuple[dict[str, object], dict[str, str | int]]:
    """Begin a passkey re-confirm by *user_id*, listing only their passkeys.

    Args:
        user_id: The signed-in account re-confirming.
        origin: The site's origin, from configuration.

    Returns:
        The options for ``navigator.credentials.get()``, as WebAuthn's JSON,
        and the state the route keeps for :func:`complete_re_confirm`.
    """
    options, fido_state = _server(origin).authenticate_begin(
        credentials=_descriptors(user_id),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return dict(options), _issue(fido_state, None)


def sign_in_account(response_json: str) -> User | None:
    """Return the account whose passkey *response_json* names, before any lock.

    A sign-in's first read: the route takes the account's owner lock between
    this and :func:`complete_sign_in`, which reads the passkey itself under
    it.  So only the account is read here, by the statement that finds the
    passkey, and no passkey is loaded: ``app.db_transaction`` names what a
    sign-in reads before the lock, and the account's row is the one it
    expires once the lock is held.

    Args:
        response_json: The browser's ``navigator.credentials.get()`` result,
            as JSON.

    Returns:
        The :class:`~app.models.user.User`, or ``None`` alike for a response
        that is not a credential and for one no passkey has the id of, so
        neither the caller nor the browser can tell them apart.
    """
    try:
        credential_id, _ = read_credential(response_json)
    except PasskeyRefused:
        return None
    return db.session.execute(
        select(User)
        .join(Passkey, Passkey.user_id == User.id)
        .where(Passkey.credential_id == credential_id)
    ).scalar_one_or_none()


def complete_sign_in(
    user: User, response_json: str,
    *, origin: str, state: Mapping[str, str | int] | None,
) -> Passkey:
    """Complete a passkey sign-in to *user*, under the account's owner lock.

    The account is the one :func:`sign_in_account` found, and the caller has
    taken its owner lock since.  The authenticator must return the user
    handle, because no account was named before the ceremony: it is the
    authenticator's statement of whose passkey signed.

    Args:
        user: The account :func:`sign_in_account` found.
        response_json: The browser's ``navigator.credentials.get()`` result,
            as JSON.
        origin: The site's origin, from configuration.
        state: What :func:`sign_in_options` returned, as the route kept it,
            or ``None``.

    Returns:
        The passkey that signed in, its ``last_used_at`` and counter moved.

    Raises:
        PasskeyRefused: Any refusal :func:`_complete_assertion` names.
    """
    return _complete_assertion(
        user, response_json, origin=origin, state=state, handle_required=True,
    )


def complete_re_confirm(
    user: User, response_json: str,
    *, origin: str, state: Mapping[str, str | int] | None,
) -> Passkey:
    """Complete *user*'s passkey re-confirm.

    The account was named before the ceremony (the signed-in user), so the
    passkey must be one of theirs, and a user handle is checked where the
    authenticator returns one, as WebAuthn says of a ceremony that named its
    account.

    Args:
        user: The signed-in account re-confirming.
        response_json: The browser's ``navigator.credentials.get()`` result,
            as JSON.
        origin: The site's origin, from configuration.
        state: What :func:`re_confirm_options` returned, as the route kept
            it, or ``None``.

    Returns:
        The passkey that re-confirmed, its ``last_used_at`` and counter moved.

    Raises:
        PasskeyRefused: Any refusal :func:`_complete_assertion` names.
    """
    return _complete_assertion(
        user, response_json, origin=origin, state=state, handle_required=False,
    )


def _complete_assertion(
    user: User, response_json: str,
    *, origin: str, state: Mapping[str, str | int] | None, handle_required: bool,
) -> Passkey:
    """Check an assertion by one of *user*'s passkeys, and record its use.

    The one completion of both authentication ceremonies.  The passkey is read
    here ``FOR UPDATE`` and afresh (``populate_existing``, whatever an earlier
    statement loaded), so its ``last_used_at`` and counter are what the last
    completion committed, and another completion of it waits until this one's
    transaction ends.  Each refusal is asked in
    an order that keeps it meaningful: what needs no key, then the key's
    signature, then what only a verified answer can be refused for.

    Args:
        user: The account the passkey must belong to.
        response_json: The browser's ``navigator.credentials.get()`` result,
            as JSON.
        origin: The site's origin, from configuration.
        state: The ceremony's state, as the route kept it, or ``None``.
        handle_required: Whether the authenticator must return a user handle.

    Returns:
        The passkey, ``last_used_at`` moved to now and its counter to the one
        it reported, flushed.

    Raises:
        PasskeyRefused: ``EXPIRED``, ``MALFORMED``, ``UNKNOWN_CREDENTIAL``,
            ``NOT_VERIFIED``, ``WRONG_USER_HANDLE``, ``ACCOUNT_DISABLED``,
            ``REPLAYED``, ``COUNTER_DID_NOT_RISE``.
    """
    now = datetime.now(timezone.utc)
    issued = _read_state(state, now)
    assertion = read_assertion(response_json)
    passkey = db.session.execute(
        select(Passkey)
        .where(Passkey.credential_id == assertion.raw_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if passkey is None or passkey.user_id != user.id:
        raise PasskeyRefused(PasskeyRefusal.UNKNOWN_CREDENTIAL)
    stored = AttestedCredentialData.create(
        Aaguid.NONE, passkey.credential_id,
        CoseKey.parse(cbor.decode(passkey.public_key)),
    )
    try:
        _server(origin).authenticate_complete(
            _fido_state(issued), [stored], assertion,
        )
    except ValueError as error:
        raise PasskeyRefused(PasskeyRefusal.NOT_VERIFIED) from error
    handle = assertion.response.user_handle
    if handle != _user_handle(user.id) and (handle is not None or handle_required):
        raise PasskeyRefused(PasskeyRefusal.WRONG_USER_HANDLE)
    if not user.is_active:
        raise PasskeyRefused(PasskeyRefusal.ACCOUNT_DISABLED)
    if passkey.last_used_at is not None and issued.issued_at <= passkey.last_used_at:
        raise PasskeyRefused(PasskeyRefusal.REPLAYED)
    counter = assertion.response.authenticator_data.counter
    if passkey.sign_count != 0 and counter <= passkey.sign_count:
        raise PasskeyRefused(PasskeyRefusal.COUNTER_DID_NOT_RISE)
    passkey.last_used_at = now
    passkey.sign_count = counter
    db.session.flush()
    return passkey


def list_passkeys(user_id: int) -> list[Passkey]:
    """Return *user_id*'s passkeys, most recently added first.

    Args:
        user_id: The ``auth.users.id`` whose passkeys to list.

    Returns:
        The :class:`~app.models.passkey.Passkey` rows, by ``created_at``
        descending, then ``id`` descending so two added in one instant still
        list in one order.
    """
    return list(db.session.execute(
        select(Passkey)
        .where(Passkey.user_id == user_id)
        .order_by(Passkey.created_at.desc(), Passkey.id.desc())
    ).scalars())


def remove_passkey(user_id: int, passkey_id: int) -> None:
    """Remove one of *user_id*'s passkeys.

    Removing an account's last passkey opens its password sign-in again
    (ruling plan#3467), because :func:`passkey_held_by` then finds none.

    Args:
        user_id: The ``auth.users.id`` whose passkey it is.
        passkey_id: The ``auth.passkeys.id`` to remove.

    Raises:
        NotFoundError: No passkey *passkey_id* belongs to *user_id*.  One
            refusal for "no such passkey" and "another user's passkey", so
            the answer is no oracle for which ids exist.
    """
    passkey = db.session.execute(
        select(Passkey).where(
            Passkey.id == passkey_id, Passkey.user_id == user_id,
        )
    ).scalar_one_or_none()
    if passkey is None:
        raise NotFoundError(
            f"Passkey {passkey_id} not found for user {user_id}."
        )
    db.session.delete(passkey)
    db.session.flush()


def passkey_held_by(user_id: int | ColumnElement[int]) -> ColumnElement[bool]:
    """Return the ONE statement of "this account signs in only by passkey", as SQL.

    True while any passkey names the account (ruling plan#3467).  A clause
    rather than a query, so a statement that finds an account can ask it in
    the same statement (``passkey_held_by(User.id)``), and :func:`has_passkey`
    asks it of one id.

    Args:
        user_id: An ``auth.users.id``, or a column holding one.

    Returns:
        A boolean ``EXISTS`` clause over ``auth.passkeys``.
    """
    return exists().where(Passkey.user_id == user_id)


def has_passkey(user_id: int) -> bool:
    """Return whether *user_id* holds a passkey, by :func:`passkey_held_by`.

    Args:
        user_id: The ``auth.users.id`` to ask about.

    Returns:
        True while the account holds one.
    """
    return db.session.execute(select(passkey_held_by(user_id))).scalar_one()
