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

**Between the acts the add waits on the server** (ruling plan#3543), in its
account's one row of ``auth.passkey_adds``
(:class:`~app.models.passkey_add.PasskeyAdd`): the challenge of the act in
flight and when it was issued, then the passkey the registration made.  The
row is the add's one home (CLAUDE.md rule 14), so the state the route keeps
names only the ceremony and the row's id, and none of what the row holds: no
key outgrows a cookie, and the sign-in's signature is checked against the
key the registration read, never one from the browser's answer, which names
its passkey by id alone.  A newer add replaces the account's row under a new
id (:func:`_begin_add`), so an older add's state names nothing and completes
nothing.

**Used once.**  Each completion reads the row ``FOR UPDATE``, so a second
completion of one act waits for the first and then finds the act done: the
registration's moves the row on to the sign-in, and the keeping deletes the
row in the transaction of the ``INSERT`` that keeps the passkey.  So an add's
answer and state replayed later keep nothing, even once that passkey is
removed.  A row whose challenge is :data:`._ceremony.CHALLENGE_LIFETIME` old
is refused and deleted when a completion reads it, and every account's dead
row is deleted by the next add any account begins (:func:`_delete_dead_adds`,
ruling plan#3578), so a dead add lasts only until anyone's next add.

**The key is trimmed** (ruling plan#3543) before it is held or kept
(:func:`_kept_key`): re-encoded with its key type, its algorithm and that key
type's public members alone, so ``auth.passkeys.public_key`` is what a
sign-in verifies by and no more, whatever else the authenticator put in it.

**Asked early, bound late.**  The cap is asked at all three acts, and a
credential id any account holds at the registration's completion, so the
device is not asked to make a passkey, or to sign in with one, that Shekel
already knows it would refuse.  The answers that bind are the keeping's,
under the account's row lock and by its one ``INSERT ... ON CONFLICT DO
NOTHING``, which two accounts' adds at once both meet: a credential another
account keeps meanwhile is refused only there, after the device has signed.
The options count under the account's row lock, which a keeping in flight
holds, so they count what it kept; after them no add of the account can keep
a passkey while this one is in flight (a newer add replaces it), so the cap
counted again at the two completions binds against any other writer of the
account's passkeys, of which there is none today.

**Each state names its ceremony** (:class:`._ceremony.Ceremony`) and its
add, so the registration's state completes only that add's registration and
the keeping's only its keeping, whatever the route keeps under which session
key.
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
from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert

from app.exceptions import ValidationError
from app.extensions import db
from app.models.passkey import CREDENTIAL_ID_MAX_BYTES, NAME_MAX_LENGTH, Passkey
from app.models.passkey_add import PasskeyAdd
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
    read_credential,
    read_registration,
)

# The key of an add's state, beside its ceremony's name: the id of its row in
# ``auth.passkey_adds``, the one thing the route keeps of it.
_STATE_ADD = "add"

# A COSE key's two members every key type has (RFC 9052, section 7.1).
_KEY_TYPE = 1
_ALGORITHM = 3

# The COSE key types (IANA's COSE Key Types registry).
_OKP = 1
_EC2 = 2
_RSA = 3
_AKP = 7

#: The key type of each algorithm the registration options offer (fido2's
#: default list, ``CoseKey.supported_algorithms``), by IANA's COSE Algorithms
#: registry.  ``test_adding`` adds a key of every algorithm the options offer,
#: so one the options offer and this map lacks fails it.
_KEY_TYPE_OF = {
    -8: _OKP,     # EdDSA (Ed25519)
    -7: _EC2,     # ES256
    -35: _EC2,    # ES384
    -36: _EC2,    # ES512
    -47: _EC2,    # ES256K
    -37: _RSA,    # PS256
    -257: _RSA,   # RS256
    -48: _AKP,    # ML-DSA-44
    -49: _AKP,    # ML-DSA-65
    -50: _AKP,    # ML-DSA-87
}

#: The labels of each key type's public members: what a sign-in verifies a
#: signature by (RFC 9053, 7.1 and 7.2; RFC 8230, 4; and the ML-DSA draft's
#: AKP type, draft-ietf-cose-dilithium).  A private member, a key id and
#: anything else an authenticator adds is none of these, and is dropped.
_PUBLIC_MEMBERS = {
    _OKP: (-1, -2),       # crv, x
    _EC2: (-1, -2, -3),   # crv, x, y
    _RSA: (-1, -2),       # n, e
    _AKP: (-1,),          # pub
}


class _Made(NamedTuple):
    """The passkey a registration made, checked, as the add's row will hold it.

    Attributes:
        credential_id: Its id.
        public_key: Its key, trimmed and encoded (:func:`_kept_key`).
        sign_count: The counter its registration reported.
    """

    credential_id: bytes
    public_key: bytes
    sign_count: int


def _begin_add(user_id: int, challenge: bytes, moment: datetime) -> int:
    """Write *user_id*'s add in flight, its registration's *challenge* issued at *moment*.

    One statement, so two adds begun at once by one account cannot both
    insert: ``ON CONFLICT`` on ``uq_passkey_adds_user_id`` replaces the
    account's row instead, waiting for a completion that holds it.  The
    replacement takes the new row's ``id`` (``EXCLUDED.id``, the next value
    the sequence gave the insert), so the older add's state names no row,
    and empties the passkey any registration of it made.

    Args:
        user_id: The account adding a passkey.
        challenge: The registration's challenge.
        moment: When it was issued.

    Returns:
        The add's id, which its state names.
    """
    proposed = insert(PasskeyAdd).values(
        user_id=user_id, challenge=challenge, issued_at=moment,
    )
    return db.session.execute(
        proposed.on_conflict_do_update(
            constraint="uq_passkey_adds_user_id",
            set_={
                "id": proposed.excluded.id,
                "challenge": proposed.excluded.challenge,
                "issued_at": proposed.excluded.issued_at,
                "credential_id": None,
                "public_key": None,
                "sign_count": None,
                "name": None,
            },
        )
        .returning(PasskeyAdd.id)
    ).scalar_one()


def _delete_dead_adds(moment: datetime) -> None:
    """Delete every account's add whose challenge is dead at *moment* (ruling plan#3578).

    In the same write as the add begun, as the strangers' guess records
    clean up after themselves
    (:func:`app.services.guess_record_service._delete_quiet_records`), so a
    dead add lasts only until anyone's next add.  Dead by the one statement
    of the lifetime every completion refuses by (:func:`._ceremony.expired`),
    counted from the challenge of the act in flight: an add whose
    registration has completed is dead five minutes after that, when its
    keeping would refuse it too.  So no add alive at *moment* is deleted, and
    the add just begun, issued at *moment*, is never among them.  A
    completion reads its clock before it reads its row, so one that read it
    just before the add died and reaches the row after this clean-up finds
    it gone and is refused as expired, as it would be a moment later.  The
    audit trail keeps each row deleted.

    Rows another transaction holds are skipped rather than waited for, and
    left for a later add: the holder is a completion of the add (perhaps one
    refused, as a completion locks the row before it asks whose it is), a
    newer add of its account, or a deletion, and two adds' clean-ups never
    wait on each other.

    Args:
        moment: The moment of the add begun.
    """
    dead = (
        select(PasskeyAdd.id)
        .where(_ceremony.expired(PasskeyAdd.issued_at, moment))
        .with_for_update(skip_locked=True)
    )
    db.session.execute(
        delete(PasskeyAdd)
        .where(PasskeyAdd.id.in_(dead))
        .execution_options(synchronize_session="fetch")
    )


def _add_in_flight(
    user: User, state: Mapping[str, str | int] | None,
    ceremony: Ceremony, moment: datetime,
) -> PasskeyAdd:
    """Return the add *state* names, locked, once it is *user*'s with *ceremony* in flight.

    The opening both of the add's completions share.  The row is read ``FOR
    UPDATE`` and afresh (``populate_existing``), so a second completion of
    one act, or a newer add replacing the row, waits until the first's
    transaction ends, and then reads what it left.  The refusals keep
    plan#3533's order, the add's age before its account; an expired row is
    deleted only when it is the account's own, so a completion never deletes
    another account's add (an add begun deletes every dead one,
    :func:`_delete_dead_adds`).

    Args:
        user: The signed-in account completing it.
        state: The state the route kept, or ``None``.
        ceremony: ``REGISTRATION`` or ``ADD_SIGN_IN``.
        moment: The completion's moment.

    Returns:
        The add's row.

    Raises:
        PasskeyRefused: ``EXPIRED`` for no state of *ceremony*'s
            (:func:`._ceremony.refuse_unless_named`), an add that is gone
            (replaced by a newer one, kept, or deleted as expired), a
            registration already completed, or a challenge
            :data:`._ceremony.CHALLENGE_LIFETIME` old, whose row is deleted
            (flushed, so it is gone once the caller commits the refusal);
            ``WRONG_ACCOUNT`` for an add begun by another account in this
            browser.
    """
    add_id = _ceremony.refuse_unless_named(state, ceremony)[_STATE_ADD]
    add = db.session.execute(
        select(PasskeyAdd)
        .where(PasskeyAdd.id == add_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if add is None:
        raise PasskeyRefused(PasskeyRefusal.EXPIRED)
    # The registration's completion fills the passkey made, so a filled one
    # is an add whose registration is done.
    if (add.credential_id is None) != (ceremony is Ceremony.REGISTRATION):
        raise PasskeyRefused(PasskeyRefusal.EXPIRED)
    if _ceremony.expired(add.issued_at, moment):
        if add.user_id == user.id:
            db.session.delete(add)
            db.session.flush()
        raise PasskeyRefused(PasskeyRefusal.EXPIRED)
    if add.user_id != user.id:
        raise PasskeyRefused(PasskeyRefusal.WRONG_ACCOUNT)
    return add


def lock_the_account(user_id: int) -> None:
    """Lock *user_id*'s own row ``FOR NO KEY UPDATE``, the lock the cap is counted under.

    Taken by the add's options and its keeping before they count, so each
    counts what a keeping holding it kept.  Two such locks conflict, while
    the ``KEY SHARE`` lock a foreign key's check takes does not: a row
    written meanwhile that names the account is not held up.  It is the
    first row lock either act takes, because a deletion of the account would
    lock its row before the rows its cascade deletes, and the two orders
    would deadlock.  A reset's removal of every passkey and the add in
    flight takes it first too
    (:func:`app.services.passkey_service.remove_every_passkey`), for the
    same reason: it deletes the add's row, which the keeping locks after
    this one.  And so does a Remove of one passkey
    (:func:`app.services.passkey_service.remove_passkey`), whose banner
    writes the account's row after its delete: a reset holding this lock
    deletes the account's passkeys, so a Remove that held its passkey's row
    first would deadlock with it.

    Args:
        user_id: The account whose row to lock.
    """
    db.session.execute(
        select(User.id).where(User.id == user_id).with_for_update(key_share=True)
    )


def _issued(add: PasskeyAdd) -> Issued:
    """Return the ceremony *add* has in flight, as fido2's completions check it.

    Args:
        add: The add's row.

    Returns:
        Its challenge, as the base64url text fido2 issued it as, and when.
    """
    return Issued(challenge=websafe_encode(add.challenge), issued_at=add.issued_at)


def _refuse_a_full_account(user_id: int) -> None:
    """Refuse an add to an account that holds :data:`MAX_PASSKEYS` already.

    Asked at each of the add's three acts.  When its options are made, under
    the account's row lock (:func:`lock_the_account`), which an older add's
    keeping in flight holds, so the device is never asked to make a passkey
    the add would refuse (it would keep it, and Shekel would not); when its
    registration completes, so the device is not asked to sign in with one;
    and when the passkey is kept, under the account's row lock
    (:func:`add_passkey`), the answer that binds.  No add of the account can
    keep a passkey between the first and the last (a newer add replaces this
    one), so the last two bind against any other writer of its passkeys.

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
    ``ON CONFLICT DO NOTHING``), which two accounts' adds at once both meet.

    Args:
        credential_id: The new passkey's id.

    Raises:
        PasskeyRefused: ``ALREADY_ADDED``.
    """
    if _credential_kept(credential_id):
        raise PasskeyRefused(PasskeyRefusal.ALREADY_ADDED)


def _credential_kept(credential_id: bytes) -> bool:
    """Return whether any account keeps a passkey whose id is *credential_id*.

    Args:
        credential_id: A passkey's id.

    Returns:
        True while a row of ``auth.passkeys`` holds it.
    """
    return db.session.execute(
        select(exists().where(Passkey.credential_id == credential_id))
    ).scalar_one()


def may_be_kept(response_json: str) -> bool:
    """Return whether the passkey a refused add's answer names may be one an account keeps.

    Asked before the page is told that the device's new passkey is kept
    nowhere, which lets the device delete it, so the answer errs to "may":
    true when any account keeps a passkey of that id (one the device
    answered with although the options excluded it, or one a replayed
    keeping finds already kept), and true when the answer names no id this
    service can read, since then nothing shows it is not kept.  Only a
    readable answer naming an id no account keeps is false.

    Args:
        response_json: The browser's answer to one of the add's steps, a
            registration's or a sign-in's, as JSON.

    Returns:
        False only when no account keeps the passkey the answer names.
    """
    try:
        credential_id, _ = read_credential(response_json)
    except PasskeyRefused:
        return True
    return _credential_kept(credential_id)


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


def _kept_key(public_key: CoseKey, offered: set[int]) -> bytes:
    """Return the new passkey's key as held and kept, refusing one no sign-in could verify by.

    Its algorithm must be one the options offered, an integer (a CBOR array
    there is no algorithm, and no set can hold one), and its key type that
    algorithm's.  The key is then trimmed (ruling plan#3543): re-encoded with
    its key type, its algorithm and that type's public members alone
    (:data:`_PUBLIC_MEMBERS`), so what is held and kept is what a sign-in
    verifies by and no more.  The trimmed key must load: asked to verify an
    empty signature over an empty message, a key that loads refuses the
    signature (``InvalidSignature``), and a malformed one raises while it is
    built, a member missing among them.  Loading is all this proves; that
    the key verifies is proven by the add's sign-in (ruling plan#3525), which
    is checked against this trimmed key, and which this check keeps a key
    that cannot load from reaching: fido2 would raise on one there as no
    refusal at all.

    Args:
        public_key: The key the authenticator made, as fido2 read it.
        offered: The algorithm ids the registration options offered.

    Returns:
        The trimmed key, CBOR-encoded as the tables keep it.

    Raises:
        PasskeyRefused: ``UNOFFERED_ALGORITHM`` or ``UNUSABLE_KEY``.
    """
    algorithm = public_key.get(_ALGORITHM)
    if not isinstance(algorithm, int) or algorithm not in offered:
        raise PasskeyRefused(PasskeyRefusal.UNOFFERED_ALGORITHM)
    key_type = _KEY_TYPE_OF[algorithm]
    if public_key.get(_KEY_TYPE) != key_type:
        raise PasskeyRefused(PasskeyRefusal.UNUSABLE_KEY)
    try:
        trimmed = CoseKey.parse(
            {_KEY_TYPE: key_type, _ALGORITHM: algorithm}
            | {label: public_key[label] for label in _PUBLIC_MEMBERS[key_type]}
        )
        trimmed.verify(b"", b"")
    except InvalidSignature:
        return cbor.encode(trimmed)
    except UNREADABLE as error:
        raise PasskeyRefused(PasskeyRefusal.UNUSABLE_KEY) from error
    raise PasskeyRefused(PasskeyRefusal.UNUSABLE_KEY)


def _registered(add: PasskeyAdd, response_json: str, *, origin: str) -> _Made:
    """Return the passkey the registration *response_json* made, answering *add*.

    The registration's checks, in order: fido2's, the key
    (:func:`_kept_key`), the credential id's length, and an id no account
    holds yet.

    Args:
        add: The add whose registration it answers, its row locked.
        response_json: The browser's ``navigator.credentials.create()``
            result, as JSON.
        origin: The site's origin, from configuration.

    Returns:
        The passkey made, its key trimmed.

    Raises:
        PasskeyRefused: ``MALFORMED``; ``NOT_VERIFIED``;
            ``UNOFFERED_ALGORITHM``; ``UNUSABLE_KEY``; ``ALREADY_ADDED``.
    """
    registration = read_registration(response_json)
    ceremonies = _ceremony.server(origin)
    try:
        auth_data = ceremonies.register_complete(
            _ceremony.fido_state(_issued(add)), registration,
        )
    except ValueError as error:
        raise PasskeyRefused(PasskeyRefusal.NOT_VERIFIED) from error
    credential_data = auth_data.credential_data
    public_key = _kept_key(
        credential_data.public_key,
        {parameters.alg for parameters in ceremonies.allowed_algorithms},
    )
    if not 0 < len(credential_data.credential_id) <= CREDENTIAL_ID_MAX_BYTES:
        raise PasskeyRefused(PasskeyRefusal.MALFORMED)
    _refuse_a_held_credential(credential_data.credential_id)
    return _Made(
        credential_id=credential_data.credential_id,
        public_key=public_key,
        sign_count=auth_data.counter,
    )


def registration_options(
    user: User, *, origin: str,
) -> tuple[dict[str, object], dict[str, str | int]]:
    """Begin adding a passkey to *user*'s account.

    User verification and a discoverable credential are both required, so
    the passkey can sign in with no email typed and no password; every
    passkey the account holds is excluded, so a device that holds one says so.
    The cap is counted under the account's row lock
    (:func:`lock_the_account`), and the add is written to the account's row
    (:func:`_begin_add`), replacing any older add in flight, and every
    account's dead add is deleted (:func:`_delete_dead_adds`), so a write:
    the route's request is a command.

    Args:
        user: The signed-in account adding a passkey.
        origin: The site's origin, from configuration.

    Returns:
        The options for ``navigator.credentials.create()``, as WebAuthn's JSON
        (``{"publicKey": {...}}``, binary as base64url text), and the state
        the route keeps for :func:`complete_registration`: the ceremony's
        name and the add's id.

    Raises:
        PasskeyRefused: ``TOO_MANY``.
    """
    lock_the_account(user.id)
    _refuse_a_full_account(user.id)
    moment = _ceremony.now()
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
    add_id = _begin_add(user.id, websafe_decode(begun["challenge"]), moment)
    _delete_dead_adds(moment)
    return dict(options), _ceremony.make_state(
        Ceremony.REGISTRATION, **{_STATE_ADD: add_id},
    )


def complete_registration(
    user: User, response_json: str, name: str,
    *, origin: str, state: Mapping[str, str | int] | None,
) -> tuple[dict[str, object], dict[str, str | int]]:
    """Check the passkey *user*'s device made, named *name*, and ask it to sign in.

    The add's first completion, which keeps nothing (ruling plan#3525): it
    returns the options of a sign-in by the new passkey, a sign-in's own,
    naming no passkey (:func:`._ceremony.sign_in_ceremony`), so the device
    must find it on its own.  The add's row moves on to that sign-in: its
    challenge and moment, and the passkey made, its key trimmed
    (:func:`_kept_key`), which :func:`add_passkey` keeps it from.  Every
    refusal the add can make before that sign-in is made here, so the device
    is not asked to sign in with a passkey Shekel already knows it would
    refuse (one another account keeps meanwhile is refused at the keeping).

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
        and the state the route keeps for :func:`add_passkey`: the
        ceremony's name and the add's id.

    Raises:
        ValidationError: The name (:func:`_checked_name`).
        PasskeyRefused: ``EXPIRED``; ``WRONG_ACCOUNT`` for an add begun by
            another account in this browser; ``TOO_MANY``; ``MALFORMED``;
            ``NOT_VERIFIED``; ``UNOFFERED_ALGORITHM``; ``UNUSABLE_KEY``;
            ``ALREADY_ADDED``.
    """
    kept_name = _checked_name(name)
    moment = _ceremony.now()
    add = _add_in_flight(user, state, Ceremony.REGISTRATION, moment)
    _refuse_a_full_account(user.id)
    made = _registered(add, response_json, origin=origin)
    options, begun = _ceremony.sign_in_ceremony(origin)
    add.challenge = websafe_decode(begun["challenge"])
    add.issued_at = moment
    add.credential_id = made.credential_id
    add.public_key = made.public_key
    add.sign_count = made.sign_count
    add.name = kept_name
    db.session.flush()
    return options, _ceremony.make_state(Ceremony.ADD_SIGN_IN, **{_STATE_ADD: add.id})


def add_passkey(
    user: User, response_json: str,
    *, origin: str, state: Mapping[str, str | int] | None,
) -> Passkey:
    """Keep the passkey *user*'s device made, once it has signed in with it.

    The add's second completion (ruling plan#3525).  The answer must be by
    the new passkey and is checked as every sign-in's is: by the key the
    registration read, which the add's row holds
    (:func:`._ceremony.verify_signature`), by the account, which must be
    active and whose user handle a ceremony naming no passkey requires
    (:func:`._ceremony.refuse_unless_by`), and with the backup flags and the
    counter read as at every assertion.  Its last use is set to this moment:
    the passkey has signed in once.

    The account's own row is locked first (:func:`lock_the_account`),
    before the add's row and the count, so two keepings of one account, or a
    keeping and a newer add's options, wait for each other there, an owner's
    lock held or not (the package docstring), and each reads what the other
    left.  The add's row is then read ``FOR UPDATE`` (:func:`_add_in_flight`).
    The new
    row is written by one statement that also asks whether any account holds
    its credential id (``ON CONFLICT DO NOTHING`` on
    ``uq_passkeys_credential_id``), so two accounts adding one credential at
    once cannot both pass a check made before it: the constraint is asked,
    never tripped.  The add's row is deleted in the same transaction, so the
    add completes once (ruling plan#3543).

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
    moment = _ceremony.now()
    lock_the_account(user.id)
    add = _add_in_flight(user, state, Ceremony.ADD_SIGN_IN, moment)
    _refuse_a_full_account(user.id)
    assertion = read_assertion(response_json)
    if assertion.raw_id != add.credential_id:
        raise PasskeyRefused(PasskeyRefusal.NOT_THE_NEW_PASSKEY)
    _ceremony.verify_signature(
        assertion, _ceremony.attested(add.credential_id, add.public_key),
        issued=_issued(add), origin=origin,
    )
    _ceremony.refuse_unless_by(assertion, user, handle_required=True)
    counter = assertion.response.authenticator_data.counter
    _ceremony.refuse_a_counter_that_did_not_rise(add.sign_count, counter)
    passkey_id = db.session.execute(
        insert(Passkey)
        .values(
            user_id=user.id,
            credential_id=add.credential_id,
            public_key=add.public_key,
            sign_count=counter,
            name=add.name,
            created_at=moment,
            last_used_at=moment,
        )
        .on_conflict_do_nothing(constraint="uq_passkeys_credential_id")
        .returning(Passkey.id)
    ).scalar_one_or_none()
    if passkey_id is None:
        raise PasskeyRefused(PasskeyRefusal.ALREADY_ADDED)
    db.session.delete(add)
    db.session.flush()
    return db.session.get(Passkey, passkey_id)
