"""
Shekel Budget App -- Passkey Service

A passkey is a key a person's phone or security key made for this site, and
it signs Shekel's challenge when they unlock it with Face ID, a fingerprint or
the device's passcode (plan#2538).  This package holds every act on one:
adding it (the registration ceremony), signing in and re-confirming with it
(the authentication ceremony), listing and removing a user's own, and the one
statement of whether an account holds any.

**What a passkey is worth** (ruling plan#3465).  It signs in on its own, with
no password and no code.  User verification is required at every ceremony,
but it is the device's own report, and Shekel cannot check that it was done:
some passkey managers report it without asking (Proton Pass among them, by
passkeys.dev's known-issues page of 2026-04-09), so a passkey is as strong as
the lock of the device or manager that holds it.  An account that holds one
keeps its password and code too, for now (ruling plan#3541); passkey-only
sign-in is step plan#3542, and :func:`passkey_held_by` is the one statement
of "holds a passkey" it will close the password sign-in by.

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
  the authenticator returns must be the one :func:`._ceremony.user_handle`
  derives for that user.  The user must be active.
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
  for, so nothing at registration has checked it.
* **A new passkey signs in once before it is kept** (ruling plan#3525), so
  adding is two ceremonies: :func:`complete_registration` keeps nothing and
  asks the device to sign in with the new passkey as a sign-in does, and
  only that sign-in keeps it (:func:`add_passkey`).  :mod:`._adding` says
  why.

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
before either moves its ``last_used_at``; and the add's keeping locks the
account's own row before it counts, so two adds at once cannot both pass the
cap.

**The clock is the process's**, read by :func:`._ceremony.now` alone, once per
act, and a challenge an act issues is issued at that same reading.

**Where each part lives.**  :mod:`._ceremony` holds what every ceremony
shares: the relying party, the user handle, the clock, a ceremony's state,
and the checks every assertion meets.  :mod:`._adding` holds the add's three
acts; this module, the sign-in and the re-confirm, and listing, removing and
holding a passkey.  :mod:`._responses` reads what the browser sent, and
:mod:`._refusals` names each refusal.

Architecture:
  - No Flask imports.  Takes plain ids, strings and the response's JSON text;
    returns rows, booleans, and the options and state the route hands the
    browser and keeps in its session.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

from collections.abc import Mapping

from fido2.webauthn import UserVerificationRequirement
from sqlalchemy import ColumnElement, exists, select

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.passkey import Passkey
from app.models.user import User
from app.services.passkey_service import _ceremony
from app.services.passkey_service._adding import (
    add_passkey,
    complete_registration,
    registration_options,
)
from app.services.passkey_service._ceremony import (
    CHALLENGE_LIFETIME,
    Ceremony,
    relying_party_id,
)
from app.services.passkey_service._refusals import (
    MAX_PASSKEYS,
    PasskeyRefusal,
    PasskeyRefused,
)
from app.services.passkey_service._responses import read_assertion, read_credential

__all__ = [
    "CHALLENGE_LIFETIME",
    "MAX_PASSKEYS",
    "PasskeyRefusal",
    "PasskeyRefused",
    "add_passkey",
    "complete_re_confirm",
    "complete_registration",
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


def sign_in_options(*, origin: str) -> tuple[dict[str, object], dict[str, str | int]]:
    """Begin a passkey sign-in, naming no account.

    No credential is listed (:func:`._ceremony.sign_in_ceremony`), so the
    one the device offers names its account.

    Args:
        origin: The site's origin, from configuration.

    Returns:
        The options for ``navigator.credentials.get()``, as WebAuthn's JSON,
        and the state the route keeps for :func:`complete_sign_in`.
    """
    options, begun = _ceremony.sign_in_ceremony(origin)
    return options, _ceremony.issue(
        begun, Ceremony.SIGN_IN, user_id=None, issued_at=_ceremony.now(),
    )


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
    options, begun = _ceremony.server(origin).authenticate_begin(
        credentials=_ceremony.descriptors(user_id),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return dict(options), _ceremony.issue(
        begun, Ceremony.RE_CONFIRM, user_id=None, issued_at=_ceremony.now(),
    )


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
        user, response_json, origin=origin, state=state, ceremony=Ceremony.SIGN_IN,
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
        user, response_json, origin=origin, state=state, ceremony=Ceremony.RE_CONFIRM,
    )


def _complete_assertion(
    user: User, response_json: str,
    *, origin: str, state: Mapping[str, str | int] | None, ceremony: Ceremony,
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
        ceremony: ``SIGN_IN`` or ``RE_CONFIRM``.  A sign-in named no
            passkey, so the authenticator must return a user handle; a
            re-confirm named the account's, so a handle is checked only where
            one is returned.

    Returns:
        The passkey, ``last_used_at`` moved to now and its counter to the one
        it reported, flushed.

    Raises:
        PasskeyRefused: ``EXPIRED``, ``MALFORMED``, ``UNKNOWN_CREDENTIAL``,
            ``NOT_VERIFIED``, ``WRONG_USER_HANDLE``, ``ACCOUNT_DISABLED``,
            ``REPLAYED``, ``COUNTER_DID_NOT_RISE``.
    """
    moment = _ceremony.now()
    issued = _ceremony.read_state(state, moment, ceremony)
    assertion = read_assertion(response_json)
    passkey = db.session.execute(
        select(Passkey)
        .where(Passkey.credential_id == assertion.raw_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if passkey is None or passkey.user_id != user.id:
        raise PasskeyRefused(PasskeyRefusal.UNKNOWN_CREDENTIAL)
    _ceremony.verify_signature(
        assertion, _ceremony.attested(passkey.credential_id, passkey.public_key),
        issued=issued, origin=origin,
    )
    _ceremony.refuse_unless_by(
        assertion, user, handle_required=ceremony is Ceremony.SIGN_IN,
    )
    if passkey.last_used_at is not None and issued.issued_at <= passkey.last_used_at:
        raise PasskeyRefused(PasskeyRefusal.REPLAYED)
    counter = assertion.response.authenticator_data.counter
    _ceremony.refuse_a_counter_that_did_not_rise(passkey.sign_count, counter)
    passkey.last_used_at = moment
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
    """Return the ONE statement of "this account holds a passkey", as SQL.

    True while any passkey names the account.  Step plan#3542 closes the
    password sign-in by it, for an account holding one.  A clause
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
