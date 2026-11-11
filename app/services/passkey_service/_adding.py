"""
Shekel Budget App -- Adding a passkey: three acts, and the passkey kept only after the last.

**A new passkey signs in once before it is kept** (ruling plan#3525).  No
attestation is asked for, so nothing at registration proves the key the
device made can verify a signature: it can load and still never verify (an
RSA modulus too small to sign).  Kept, it would be a passkey that cannot sign
in, or that its device cannot find on its own; and once an account with a
passkey signs in only by passkey (step plan#3542, which ruling plan#3541 set
for later), it would lock the account out.  So adding asks the device twice:

1. :func:`registration_options` -- the device is asked to make a passkey.
2. :func:`complete_registration` -- every check a registration can be
   refused by, and nothing kept.  It returns the options of a sign-in by the
   new passkey, a sign-in's own, naming no passkey, so the device must find it
   on its own, as every later sign-in will need it to.
3. :func:`add_passkey` -- that sign-in, verified against the key the
   registration read, keeps the passkey, its last use that moment.

The key travels between the last two in the state the route keeps, never in
what the browser answers: a sign-in's answer names its passkey by id alone,
and its signature is checked against the key the registration read.

**Asked early, bound late.**  The cap is asked at all three acts, and a
credential id any account holds at the registration's completion, so the
device is not asked to make a passkey, or to sign in with one, that Shekel
already knows it would refuse.  The answers that bind are the keeping's,
under the account's row lock and by its one ``INSERT ... ON CONFLICT DO
NOTHING``, which two adds at once both meet: a passkey another add keeps
meanwhile is refused only there, after the device has signed.

**Each state names its ceremony** (:class:`._ceremony.Ceremony`), so the
registration's state completes only the registration and the keeping's only
the keeping, whatever the route keeps under which session key.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import NamedTuple

from cryptography.exceptions import InvalidSignature
from fido2 import cbor
from fido2.cose import CoseKey
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import (
    PublicKeyCredentialUserEntity,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert

from app.exceptions import ValidationError
from app.extensions import db
from app.models.passkey import CREDENTIAL_ID_MAX_BYTES, NAME_MAX_LENGTH, Passkey
from app.models.user import User
from app.services.passkey_service import _ceremony
from app.services.passkey_service._ceremony import Ceremony, Issued
from app.services.passkey_service._refusals import (
    MAX_PASSKEYS,
    PasskeyRefusal,
    PasskeyRefused,
)
from app.services.passkey_service._responses import (
    UNREADABLE,
    read_assertion,
    read_registration,
)

# The keys of the new passkey an add's sign-in state carries, beside the
# ceremony's own (:func:`._ceremony.issue`): binary as base64url text, so the
# state stays JSON.
_STATE_CREDENTIAL_ID = "credential_id"
_STATE_PUBLIC_KEY = "public_key"
_STATE_SIGN_COUNT = "sign_count"
_STATE_NAME = "name"


class _Made(NamedTuple):
    """The passkey a registration made, as an add's sign-in state carries it.

    Attributes:
        credential_id: Its id.
        public_key: Its COSE key, encoded as the table keeps it.
        sign_count: The counter its registration reported.
        name: The name typed for it, as it will be kept.
    """

    credential_id: bytes
    public_key: bytes
    sign_count: int
    name: str


def _add_in_flight(
    user: User, state: Mapping[str, str | int] | None, ceremony: Ceremony,
) -> tuple[datetime, Issued]:
    """Return the moment of an add's completion and the *ceremony* it completes.

    The opening both of the add's completions share: one read of the clock,
    the state of this ceremony's and not yet expired, and an add begun by the
    account completing it.

    Args:
        user: The signed-in account completing it.
        state: The state the route kept, or ``None``.
        ceremony: ``REGISTRATION`` or ``ADD_SIGN_IN``.

    Returns:
        The completion's moment and the issued ceremony.

    Raises:
        PasskeyRefused: ``EXPIRED``; ``WRONG_ACCOUNT`` for an add begun by
            another account in this browser.
    """
    moment = _ceremony.now()
    issued = _ceremony.read_state(state, moment, ceremony)
    if issued.user_id != user.id:
        raise PasskeyRefused(PasskeyRefusal.WRONG_ACCOUNT)
    return moment, issued


def _read_made(state: Mapping[str, str | int]) -> _Made:
    """Return the new passkey an add's sign-in *state* carries.

    Args:
        state: What :func:`complete_registration` returned, as the route kept
            it, already read as the keeping's by :func:`_add_in_flight`.

    Returns:
        The passkey as the registration read it.
    """
    return _Made(
        credential_id=websafe_decode(state[_STATE_CREDENTIAL_ID]),
        public_key=websafe_decode(state[_STATE_PUBLIC_KEY]),
        sign_count=state[_STATE_SIGN_COUNT],
        name=state[_STATE_NAME],
    )


def _refuse_a_full_account(user_id: int) -> None:
    """Refuse an add to an account that holds :data:`MAX_PASSKEYS` already.

    Asked at each of the add's three acts.  When its options are made, so the
    device is never asked to make a passkey the add would refuse (it would
    keep it, and Shekel would not); when its registration completes, so the
    device is not asked to sign in with one; and when the passkey is kept,
    under the account's row lock (:func:`add_passkey`), which is the answer
    two adds at once both meet.

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


def _refuse_a_held_credential(credential_id: bytes) -> None:
    """Refuse a new passkey whose credential id an account holds already.

    Asked when the registration completes, so a device that ignored the
    exclusion is not asked to sign in with a passkey Shekel would not keep.
    The answer that binds is the keeping's own statement (:func:`add_passkey`,
    ``ON CONFLICT DO NOTHING``), which two adds at once both meet.

    Args:
        credential_id: The new passkey's id.

    Raises:
        PasskeyRefused: ``ALREADY_ADDED``.
    """
    if db.session.execute(
        select(exists().where(Passkey.credential_id == credential_id))
    ).scalar_one():
        raise PasskeyRefused(PasskeyRefusal.ALREADY_ADDED)


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
    raises while it is built.  Loading is all this proves; that the key
    verifies is proven by the add's sign-in (ruling plan#3525), which this
    check keeps a key that cannot load from reaching: fido2 would raise on
    one there as no refusal at all.

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
        the route keeps for :func:`complete_registration`.

    Raises:
        PasskeyRefused: ``TOO_MANY``.
    """
    _refuse_a_full_account(user.id)
    options, begun = _ceremony.server(origin).register_begin(
        PublicKeyCredentialUserEntity(
            id=_ceremony.user_handle(user.id),
            name=user.email,
            display_name=user.display_name or user.email,
        ),
        credentials=_ceremony.descriptors(user.id),
        resident_key_requirement=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return dict(options), _ceremony.issue(
        begun, Ceremony.REGISTRATION, user_id=user.id, issued_at=_ceremony.now(),
    )


def complete_registration(
    user: User, response_json: str, name: str,
    *, origin: str, state: Mapping[str, str | int] | None,
) -> tuple[dict[str, object], dict[str, str | int]]:
    """Check the passkey *user*'s device made, named *name*, and ask it to sign in.

    The add's first completion, which keeps nothing (ruling plan#3525): it
    returns the options of a sign-in by the new passkey, a sign-in's own,
    naming no passkey (:func:`._ceremony.sign_in_ceremony`), so the device
    must find it on its own, and the state :func:`add_passkey` keeps it from.
    Every refusal the add can make before that sign-in is made here, so the
    device is not asked to sign in with a passkey Shekel already knows it
    would refuse (one another add keeps meanwhile is refused at the keeping).

    Args:
        user: The signed-in account.
        response_json: The browser's ``navigator.credentials.create()``
            result, as JSON.
        name: The name typed for the passkey.
        origin: The site's origin, from configuration.
        state: What :func:`registration_options` returned, as the route kept
            it, or ``None``.

    Returns:
        The options for ``navigator.credentials.get()``, as WebAuthn's JSON,
        and the state the route keeps for :func:`add_passkey`: a fresh
        challenge, its issue time and the account, beside the new passkey's
        id, key, counter and name, as plain JSON.

    Raises:
        ValidationError: The name (:func:`_checked_name`).
        PasskeyRefused: ``EXPIRED``; ``WRONG_ACCOUNT`` for an add begun by
            another account in this browser; ``TOO_MANY``; ``MALFORMED``;
            ``NOT_VERIFIED``; ``UNOFFERED_ALGORITHM``; ``UNUSABLE_KEY``;
            ``ALREADY_ADDED``.
    """
    kept_name = _checked_name(name)
    moment, issued = _add_in_flight(user, state, Ceremony.REGISTRATION)
    _refuse_a_full_account(user.id)
    registration = read_registration(response_json)
    ceremonies = _ceremony.server(origin)
    try:
        auth_data = ceremonies.register_complete(_ceremony.fido_state(issued), registration)
    except ValueError as error:
        raise PasskeyRefused(PasskeyRefusal.NOT_VERIFIED) from error
    credential_data = auth_data.credential_data
    _refuse_an_unusable_key(
        credential_data.public_key,
        {parameters.alg for parameters in ceremonies.allowed_algorithms},
    )
    if not 0 < len(credential_data.credential_id) <= CREDENTIAL_ID_MAX_BYTES:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    _refuse_a_held_credential(credential_data.credential_id)
    options, begun = _ceremony.sign_in_ceremony(origin)
    return options, _ceremony.issue(
        begun, Ceremony.ADD_SIGN_IN, user_id=user.id, issued_at=moment,
    ) | {
        _STATE_CREDENTIAL_ID: websafe_encode(credential_data.credential_id),
        _STATE_PUBLIC_KEY: websafe_encode(cbor.encode(credential_data.public_key)),
        _STATE_SIGN_COUNT: auth_data.counter,
        _STATE_NAME: kept_name,
    }


def add_passkey(
    user: User, response_json: str,
    *, origin: str, state: Mapping[str, str | int] | None,
) -> Passkey:
    """Keep the passkey *user*'s device made, once it has signed in with it.

    The add's second completion (ruling plan#3525).  The answer must be by
    the new passkey and is checked as every sign-in's is: by the key the
    registration read, which the state carries
    (:func:`._ceremony.verify_signature`), by the account, which must be
    active and whose user handle a ceremony naming no passkey requires
    (:func:`._ceremony.refuse_unless_by`), and with the backup flags and the
    counter read as at every assertion.  Its last use is set to this moment:
    the passkey has signed in once.

    The account's own row is locked before the count, so two adds at once
    wait for each other there, an owner's lock held or not (the package
    docstring), and the count each reads is the one the other left.  The
    lock is ``FOR NO KEY UPDATE``, which two adds' locks conflict on, while
    the ``KEY SHARE`` lock a foreign key's check takes does not: a row
    written meanwhile that names the account is not held up.  The new
    row is written by one statement that also asks whether any account holds
    its credential id (``ON CONFLICT DO NOTHING`` on
    ``uq_passkeys_credential_id``), so two accounts adding one credential at
    once cannot both pass a check made before it: the constraint is asked,
    never tripped.

    Args:
        user: The signed-in account.
        response_json: The browser's ``navigator.credentials.get()`` result,
            as JSON.
        origin: The site's origin, from configuration.
        state: What :func:`complete_registration` returned, as the route kept
            it, or ``None``.

    Returns:
        The new :class:`~app.models.passkey.Passkey`, flushed.

    Raises:
        PasskeyRefused: ``EXPIRED``; ``WRONG_ACCOUNT``; ``TOO_MANY``;
            ``MALFORMED``; ``NOT_THE_NEW_PASSKEY`` for an answer by another of
            the device's passkeys; ``NOT_VERIFIED``; ``WRONG_USER_HANDLE``;
            ``ACCOUNT_DISABLED``; ``COUNTER_DID_NOT_RISE``; ``ALREADY_ADDED``.
    """
    moment, issued = _add_in_flight(user, state, Ceremony.ADD_SIGN_IN)
    made = _read_made(state)
    db.session.execute(
        select(User.id).where(User.id == user.id).with_for_update(key_share=True)
    )
    _refuse_a_full_account(user.id)
    assertion = read_assertion(response_json)
    if assertion.raw_id != made.credential_id:
        raise PasskeyRefused(PasskeyRefusal.NOT_THE_NEW_PASSKEY)
    _ceremony.verify_signature(
        assertion, _ceremony.attested(made.credential_id, made.public_key),
        issued=issued, origin=origin,
    )
    _ceremony.refuse_unless_by(assertion, user, handle_required=True)
    counter = assertion.response.authenticator_data.counter
    _ceremony.refuse_a_counter_that_did_not_rise(made.sign_count, counter)
    passkey_id = db.session.execute(
        insert(Passkey)
        .values(
            user_id=user.id,
            credential_id=made.credential_id,
            public_key=made.public_key,
            sign_count=counter,
            name=made.name,
            created_at=moment,
            last_used_at=moment,
        )
        .on_conflict_do_nothing(constraint="uq_passkeys_credential_id")
        .returning(Passkey.id)
    ).scalar_one_or_none()
    if passkey_id is None:
        raise PasskeyRefused(PasskeyRefusal.ALREADY_ADDED)
    return db.session.get(Passkey, passkey_id)
