"""Two acts at once: one answer completed twice, the cap counted past a held account, an add replaced.

The owner's lock serialises an account's requests, but a companion whose
owner is gone has none to take, so the decisions that must not race take
row locks of their own (the service's module docstring).  Each case holds the
first act's row on a connection of its own, mid-transaction, runs the second
act in a thread with its own session, and commits the first once the second
waits on it (:func:`tests._lock_waits.act_past_a_held_row`).  A second act
that took no lock would not wait at its read, and would decide on the row as
it stood before the first committed.
"""

import json
from datetime import timedelta

from sqlalchemy import text

from app.extensions import db
from app.models.user import User
from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from tests._lock_waits import act_past_a_held_row
from tests._passkeys import ORIGIN, SoftwareAuthenticator
from tests.test_services.test_passkey_service._helpers import ADDED_AT, T0, Clock, add


def test_one_answer_completed_twice_at_once_signs_in_once(app, bare_user, monkeypatch):
    """The first completion holds the passkey and has moved its last use; the second waits, then is refused."""
    clock = Clock(monkeypatch)
    user = bare_user["user"]
    passkey, authenticator = add(clock, user)
    options, state = passkey_service.sign_in_options(origin=ORIGIN)
    response = json.dumps(authenticator.get(options))
    user_id, passkey_id = user.id, passkey.id
    db.session.commit()
    clock.at(T0 + timedelta(seconds=2))

    def second_completion():
        """The second completion, in a session of its own."""
        try:
            passkey_service.complete_sign_in(
                db.session.get(User, user_id), response, origin=ORIGIN, state=state,
            )
        except PasskeyRefused as refused:
            return refused.refusal
        return None

    holder = db.engine.connect()
    try:
        holder.execute(
            text("SELECT id FROM auth.passkeys WHERE id = :id FOR UPDATE"),
            {"id": passkey_id},
        )
        holder.execute(
            text("UPDATE auth.passkeys SET last_used_at = :at WHERE id = :id"),
            {"at": T0 + timedelta(seconds=1), "id": passkey_id},
        )
        refusal = act_past_a_held_row(app, holder, second_completion)
    finally:
        holder.close()
    assert refusal is PasskeyRefusal.REPLAYED


def test_a_keeping_counts_what_the_accounts_lock_holder_wrote(app, bare_user, monkeypatch):
    """A writer holds the account's row and has written the tenth; the keeping waits, then is refused.

    No add of the account can be that writer while this one is in flight (a
    newer add replaces it, ruling plan#3543), so the holder writes the tenth
    by SQL: the keeping's count is ordered after whatever holds the account.
    """
    clock = Clock(monkeypatch)
    user = bare_user["user"]
    for _ in range(9):
        add(clock, user)
    authenticator = SoftwareAuthenticator()
    options, state = passkey_service.registration_options(user, origin=ORIGIN)
    sign_in_options, sign_in_state = passkey_service.complete_registration(
        user, json.dumps(authenticator.create(options)), "iPad",
        origin=ORIGIN, state=state,
    )
    response = json.dumps(authenticator.get(sign_in_options))
    user_id = user.id
    db.session.commit()

    def second_add():
        """The second add's keeping, in a session of its own."""
        try:
            passkey_service.add_passkey(
                db.session.get(User, user_id), response,
                origin=ORIGIN, state=sign_in_state,
            )
        except PasskeyRefused as refused:
            return refused.refusal
        return None

    holder = db.engine.connect()
    try:
        holder.execute(
            text("SELECT id FROM auth.users WHERE id = :id FOR UPDATE"), {"id": user_id},
        )
        holder.execute(
            text(
                "INSERT INTO auth.passkeys"
                " (user_id, credential_id, public_key, sign_count, name,"
                " created_at, last_used_at)"
                " VALUES (:user_id, :credential_id, :public_key, 0, 'iPhone', :at, :at)"
            ),
            {
                "user_id": user_id, "credential_id": b"\x0a" * 16,
                "public_key": SoftwareAuthenticator().cose_key(), "at": T0,
            },
        )
        refusal = act_past_a_held_row(app, holder, second_add)
    finally:
        holder.close()
    assert refusal is PasskeyRefusal.TOO_MANY
    db.session.expire_all()
    assert len(passkey_service.list_passkeys(user_id)) == 10


def test_a_registration_completed_while_a_newer_add_replaces_it_completes_nothing(
    app, bare_user, monkeypatch,
):
    """A newer add holds the account's add row, replacing it; the registration waits, then is refused.

    The holder writes what :func:`_begin_add`'s conflict arm writes: a new
    id, a new challenge, the passkey made emptied.  The registration's read
    of its add is ``FOR UPDATE``, so it waits for the replacement and then
    finds its add gone.  A read without the lock would see the row as it
    stood, check the answer against it, and fail at its own write of a row
    whose id had moved.
    """
    Clock(monkeypatch)
    user = bare_user["user"]
    authenticator = SoftwareAuthenticator()
    options, state = passkey_service.registration_options(user, origin=ORIGIN)
    response = json.dumps(authenticator.create(options))
    user_id = user.id
    db.session.commit()

    def registration():
        """The older add's registration, in a session of its own."""
        try:
            passkey_service.complete_registration(
                db.session.get(User, user_id), response, "iPhone",
                origin=ORIGIN, state=state,
            )
        except PasskeyRefused as refused:
            return refused.refusal
        return None

    holder = db.engine.connect()
    try:
        holder.execute(
            text(
                "UPDATE auth.passkey_adds SET"
                " id = nextval(pg_get_serial_sequence('auth.passkey_adds', 'id')),"
                " challenge = :challenge, issued_at = :at, credential_id = NULL,"
                " public_key = NULL, sign_count = NULL, name = NULL"
                " WHERE user_id = :user_id"
            ),
            {"challenge": b"\x05" * 32, "at": T0, "user_id": user_id},
        )
        refusal = act_past_a_held_row(app, holder, registration)
    finally:
        holder.close()
    assert refusal is PasskeyRefusal.EXPIRED


def test_an_add_begun_while_a_keeping_holds_the_account_counts_what_it_kept(
    app, bare_user, monkeypatch,
):
    """A keeping holds the account's row and has written the tenth; a newer add's options wait, then refuse.

    So the device is never asked to make an eleventh: the options count
    under the account's lock, after the keeping that held it.
    """
    clock = Clock(monkeypatch)
    user = bare_user["user"]
    for _ in range(9):
        add(clock, user)
    user_id = user.id
    db.session.commit()

    def options():
        """The newer add's options, in a session of its own."""
        try:
            passkey_service.registration_options(
                db.session.get(User, user_id), origin=ORIGIN,
            )
        except PasskeyRefused as refused:
            return refused.refusal
        return None

    holder = db.engine.connect()
    try:
        holder.execute(
            text("SELECT id FROM auth.users WHERE id = :id FOR NO KEY UPDATE"),
            {"id": user_id},
        )
        holder.execute(
            text(
                "INSERT INTO auth.passkeys"
                " (user_id, credential_id, public_key, sign_count, name,"
                " created_at, last_used_at)"
                " VALUES (:user_id, :credential_id, :public_key, 0, 'iPhone', :at, :at)"
            ),
            {
                "user_id": user_id, "credential_id": b"\x0d" * 16,
                "public_key": SoftwareAuthenticator().cose_key(), "at": T0,
            },
        )
        refusal = act_past_a_held_row(app, holder, options)
    finally:
        holder.close()
    assert refusal is PasskeyRefusal.TOO_MANY


def test_a_completion_reads_the_passkey_afresh_over_a_stale_copy(bare_user, monkeypatch):
    """The session holds the passkey as it stood before another completion committed.

    The completion's read refreshes it (``populate_existing``), so the answer
    the other completion used is refused rather than judged by the stale copy.
    """
    clock = Clock(monkeypatch)
    user = bare_user["user"]
    passkey, authenticator = add(clock, user)
    options, state = passkey_service.sign_in_options(origin=ORIGIN)
    response = json.dumps(authenticator.get(options))
    passkey_id = passkey.id
    db.session.commit()
    stale = db.session.get(passkey.__class__, passkey_id)
    assert stale.last_used_at == ADDED_AT
    with db.engine.begin() as other:
        other.execute(
            text("UPDATE auth.passkeys SET last_used_at = :at WHERE id = :id"),
            {"at": T0 + timedelta(seconds=1), "id": passkey_id},
        )
    clock.at(T0 + timedelta(seconds=2))
    try:
        passkey_service.complete_sign_in(user, response, origin=ORIGIN, state=state)
    except PasskeyRefused as refused:
        assert refused.refusal is PasskeyRefusal.REPLAYED
    else:
        raise AssertionError("the stale copy's last use let the answer sign in twice")


def test_a_keeping_holds_up_no_row_that_names_the_account(bare_user, monkeypatch):
    """While a keeping's transaction is open, a row naming the account is still written.

    The keeping locks the account's row ``FOR NO KEY UPDATE``, which two
    keepings conflict on and a foreign key's ``KEY SHARE`` check does not.
    Were it ``FOR UPDATE``, the other connection's insert would wait on it
    and meet its two-second lock timeout.
    """
    Clock(monkeypatch)
    user = bare_user["user"]
    authenticator = SoftwareAuthenticator()
    options, state = passkey_service.registration_options(user, origin=ORIGIN)
    sign_in_options, sign_in_state = passkey_service.complete_registration(
        user, json.dumps(authenticator.create(options)), "iPhone",
        origin=ORIGIN, state=state,
    )
    user_id = user.id
    db.session.commit()
    passkey_service.add_passkey(
        db.session.get(User, user_id), json.dumps(authenticator.get(sign_in_options)),
        origin=ORIGIN, state=sign_in_state,
    )
    other = db.engine.connect()
    try:
        other.execute(text("SET lock_timeout = '2s'"))
        other.execute(
            text(
                "INSERT INTO auth.passkeys"
                " (user_id, credential_id, public_key, sign_count, name,"
                " created_at, last_used_at)"
                " VALUES (:user_id, :credential_id, :public_key, 0, 'iPad', :at, :at)"
            ),
            {
                "user_id": user_id, "credential_id": b"\x0c" * 16,
                "public_key": SoftwareAuthenticator().cose_key(), "at": T0,
            },
        )
        other.rollback()
    finally:
        other.close()
        db.session.rollback()
