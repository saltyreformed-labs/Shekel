"""``auth.passkeys`` refuses what no writer should be trusted to avoid.

Plan#2538, rulings **plan#3465** and **plan#3467**: a passkey signs in on its
own, and an account holding one signs in only by passkey.  Migration
``66184d995b1c`` makes the table and admits the two banner kinds a passkey's
adding and removing raise.

* :class:`TestTheColumns` -- the eight columns, only the last use nullable,
  and the absences pinned like presences, because each would be a stored copy
  of something derivable (rule 14).
* :class:`TestTheKeys` -- FIRING CONTROLS.  The service never writes these
  states, so a test that only asserted a constraint EXISTS would pass against
  one admitting everything.  Each writes the state, flushes, and asserts the
  refusal BY NAME, beside a control that the edge the CHECK admits flushes.
* :class:`TestPasskeysAreAudited` -- adding a passkey adds a way in, and the
  audit trigger is what records it.
* :class:`TestTheBannerKinds` -- every kind the enum names is one the database
  admits, so a banner the code raises can never be refused at commit.
* :class:`TestTheRoundTrip` -- both directions run on this test's own
  database.
"""

from datetime import datetime, timezone

import pytest
import sqlalchemy
import sqlalchemy.exc

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db
from app.models.passkey import (
    CREDENTIAL_ID_MAX_BYTES,
    NAME_MAX_LENGTH,
    SIGN_COUNT_MAX,
    Passkey,
)
from app.utils.security_events import SecurityEventKind
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module("66184d995b1c_a_passkey_signs_in_on_its_own.py")

#: Every constraint the migration names on the table, and its kind.
_NAMED_CONSTRAINTS = {
    "pk_passkeys": "p",
    "fk_passkeys_user_id": "f",
    "uq_passkeys_credential_id": "u",
    "ck_passkeys_credential_id_length": "c",
    "ck_passkeys_public_key_not_empty": "c",
    "ck_passkeys_sign_count_range": "c",
    "ck_passkeys_name_not_blank": "c",
}

_INDEX = "ix_passkeys_user_id"

_TRIGGER = "audit_passkeys"

_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _a_passkey(user_id, credential_id=b"\x01" * 16, **overrides):
    """Return an unsaved passkey of *user_id*, with fields replaceable."""
    fields = {
        "user_id": user_id,
        "credential_id": credential_id,
        "public_key": b"\xa5\x01\x02\x03\x26",
        "name": "iPhone",
        "created_at": _T0,
    }
    fields.update(overrides)
    return Passkey(**fields)


def _refusal_of(passkey):
    """Add *passkey*, flush, and return the name of the constraint that refused it."""
    db.session.add(passkey)
    with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
        db.session.flush()
    db.session.rollback()
    return constraint_name_from(excinfo.value)


def _admitted(passkey):
    """Add *passkey*, flush, and return whether its row now exists."""
    db.session.add(passkey)
    db.session.flush()
    return db.session.get(Passkey, passkey.id) is not None


def _constraints():
    """Return ``{name: kind}`` of every constraint on the table, from PostgreSQL."""
    return dict(db.session.execute(sqlalchemy.text(
        "SELECT conname, contype FROM pg_constraint "
        "WHERE conrelid = 'auth.passkeys'::regclass "
        "AND contype IN ('p', 'f', 'u', 'c')"
    )).all())


def _table_exists():
    """Return whether ``auth.passkeys`` exists."""
    return db.session.execute(sqlalchemy.text(
        "SELECT to_regclass('auth.passkeys') IS NOT NULL"
    )).scalar()


def _trigger_count():
    """Return how many triggers called ``audit_passkeys`` the database holds."""
    return db.session.execute(sqlalchemy.text(
        "SELECT count(*) FROM pg_trigger WHERE tgname = :name "
        "AND NOT tgisinternal"
    ), {"name": _TRIGGER}).scalar()


def _stamp_kind(user_id, kind):
    """Write *kind* as *user_id*'s banner by SQL and flush; the helper is bypassed."""
    db.session.execute(sqlalchemy.text(
        "UPDATE auth.users SET last_security_event_kind = :kind, "
        "last_security_event_at = :at WHERE id = :id"
    ), {"kind": kind, "at": _T0, "id": user_id})
    db.session.flush()


class TestTheColumns:
    """Eight columns; nothing derivable stored."""

    def test_it_carries_exactly_the_eight_columns(self, app, db):
        """Named rather than counted, so a rename is as visible as an add.

        No ``aaguid`` and no ``transports``: no reader needs either.  No
        ``user_handle``: the service derives the WebAuthn handle from the
        user's id.  No ``updated_at``: nothing reads it.
        """
        assert {column.name for column in Passkey.__table__.columns} == {
            "id", "user_id", "credential_id", "public_key", "sign_count",
            "name", "created_at", "last_used_at",
        }

    def test_only_the_last_use_is_nullable_in_the_database(self, app, db):
        """Asked of ``information_schema``, not the model."""
        nullable = db.session.execute(sqlalchemy.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'auth' AND table_name = 'passkeys' "
            "AND is_nullable = 'YES'"
        )).scalars().all()

        assert nullable == ["last_used_at"]

    def test_the_user_id_is_indexed(self, app, db):
        """The Security page, the closed-road question and re-confirm ask by user."""
        definition = db.session.execute(sqlalchemy.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'auth' AND indexname = :name"
        ), {"name": _INDEX}).scalar()

        assert definition == (
            "CREATE INDEX ix_passkeys_user_id "
            "ON auth.passkeys USING btree (user_id)"
        )

    def test_a_new_passkey_counts_from_zero(self, app, db, seed_user):
        """The database's default counter is 0, the count a new key reports."""
        passkey = _a_passkey(seed_user["user"].id)
        db.session.add(passkey)
        db.session.flush()
        db.session.refresh(passkey)

        assert passkey.sign_count == 0


class TestTheKeys:
    """Each refusal, fired and named, beside the edge it admits."""

    def test_an_empty_credential_id_is_refused(self, app, db, seed_user):
        """A key the browser cannot name could never be found."""
        passkey = _a_passkey(seed_user["user"].id, credential_id=b"")

        assert _refusal_of(passkey) == "ck_passkeys_credential_id_length"

    def test_a_credential_id_past_webauthns_bound_is_refused(
        self, app, db, seed_user,
    ):
        """1024 bytes is one past the 1023 WebAuthn allows."""
        passkey = _a_passkey(
            seed_user["user"].id,
            credential_id=b"\x02" * (CREDENTIAL_ID_MAX_BYTES + 1),
        )

        assert _refusal_of(passkey) == "ck_passkeys_credential_id_length"

    def test_a_credential_id_at_webauthns_bound_is_admitted(
        self, app, db, seed_user,
    ):
        """The control: 1023 bytes flushes."""
        assert _admitted(_a_passkey(
            seed_user["user"].id,
            credential_id=b"\x03" * CREDENTIAL_ID_MAX_BYTES,
        ))

    def test_an_empty_public_key_is_refused(self, app, db, seed_user):
        """No signature can be checked against nothing."""
        passkey = _a_passkey(seed_user["user"].id, public_key=b"")

        assert _refusal_of(passkey) == "ck_passkeys_public_key_not_empty"

    def test_a_negative_count_is_refused(self, app, db, seed_user):
        """The counter is unsigned."""
        passkey = _a_passkey(seed_user["user"].id, sign_count=-1)

        assert _refusal_of(passkey) == "ck_passkeys_sign_count_range"

    def test_a_count_past_32_bits_is_refused(self, app, db, seed_user):
        """The authenticator's field is 32 bits, so 2**32 cannot be read from one."""
        passkey = _a_passkey(seed_user["user"].id, sign_count=SIGN_COUNT_MAX + 1)

        assert _refusal_of(passkey) == "ck_passkeys_sign_count_range"

    def test_the_largest_32_bit_count_is_admitted(self, app, db, seed_user):
        """The control: 2**32 - 1 flushes."""
        assert _admitted(_a_passkey(
            seed_user["user"].id, sign_count=SIGN_COUNT_MAX,
        ))

    def test_a_blank_name_is_refused(self, app, db, seed_user):
        """Spaces alone label nothing on the Security page."""
        passkey = _a_passkey(seed_user["user"].id, name="   ")

        assert _refusal_of(passkey) == "ck_passkeys_name_not_blank"

    def test_a_name_past_its_length_is_refused(self, app, db, seed_user):
        """The column holds :data:`NAME_MAX_LENGTH` characters and no more."""
        db.session.add(_a_passkey(
            seed_user["user"].id, name="x" * (NAME_MAX_LENGTH + 1),
        ))
        with pytest.raises(sqlalchemy.exc.DataError):
            db.session.flush()
        db.session.rollback()

    def test_one_credential_id_names_one_passkey(self, app, db, seed_user):
        """A second row with the same id is refused, whoever's it would be."""
        db.session.add(_a_passkey(seed_user["user"].id))
        db.session.flush()

        assert _refusal_of(_a_passkey(seed_user["user"].id)) == (
            "uq_passkeys_credential_id"
        )

    def test_a_passkey_of_no_user_is_refused(self, app, db):
        """``user_id`` names a user that exists."""
        assert _refusal_of(_a_passkey(987654321)) == "fk_passkeys_user_id"

    def test_a_deleted_users_passkeys_go_with_them(self, app, db, bare_user):
        """``ON DELETE CASCADE``: no passkey outlives its user."""
        user_id = bare_user["user"].id
        db.session.add(_a_passkey(user_id))
        db.session.flush()

        db.session.execute(
            sqlalchemy.text("DELETE FROM auth.users WHERE id = :id"),
            {"id": user_id},
        )

        assert db.session.execute(sqlalchemy.text(
            "SELECT count(*) FROM auth.passkeys WHERE user_id = :id"
        ), {"id": user_id}).scalar() == 0


class TestPasskeysAreAudited:
    """Adding a passkey adds a way in, so the trail records it."""

    def test_the_table_is_in_the_audited_list(self, app, db):
        """``app.audit_infrastructure.AUDITED_TABLES`` names it."""
        assert ("auth", "passkeys") in AUDITED_TABLES

    def test_the_migration_and_the_audit_list_agree(self, app, db):
        """Two lists that must agree are two lists that can drift."""
        audited = {name for schema, name in AUDITED_TABLES if schema == "auth"}

        assert _MIGRATION._NEW_TABLE in audited

    def test_the_table_carries_its_trigger(self, app, db):
        """Under the name the deploy's trigger census counts by."""
        assert _trigger_count() == 1

    def test_adding_a_passkey_writes_its_row_to_the_audit_log(
        self, app, db, seed_user,
    ):
        """The INSERT row carries the passkey's user and name."""
        passkey = _a_passkey(seed_user["user"].id)
        db.session.add(passkey)
        db.session.flush()

        new = db.session.execute(sqlalchemy.text(
            "SELECT new_data FROM system.audit_log "
            "WHERE table_name = 'passkeys' "
            "AND operation = 'INSERT' AND row_id = :row_id"
        ), {"row_id": passkey.id}).scalar()
        assert new["user_id"] == seed_user["user"].id
        assert new["name"] == "iPhone"


class TestTheBannerKinds:
    """The enum's every kind is one the database admits."""

    @pytest.mark.parametrize("kind", list(SecurityEventKind))
    def test_every_kind_the_code_raises_is_admitted(
        self, app, db, seed_user, kind,
    ):
        """A kind the CHECK refused would turn a banner into a 500 at commit."""
        _stamp_kind(seed_user["user"].id, kind.value)

        assert db.session.execute(sqlalchemy.text(
            "SELECT last_security_event_kind FROM auth.users WHERE id = :id"
        ), {"id": seed_user["user"].id}).scalar() == kind.value

    def test_a_kind_the_enum_does_not_name_is_still_refused(
        self, app, db, seed_user,
    ):
        """The widened CHECK is still a whitelist."""
        with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
            _stamp_kind(seed_user["user"].id, "passkey_renamed")
        db.session.rollback()

        assert constraint_name_from(excinfo.value) == (
            "ck_users_security_event_kind"
        )


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone)."""

    def test_the_downgrade_drops_the_table_and_its_trigger(self, app):
        """Down: no passkey table, no trigger left behind."""
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert not _table_exists()
            assert _trigger_count() == 0

    def test_the_downgrade_refuses_the_passkey_kinds_again(
        self, app, seed_user,
    ):
        """After the downgrade the CHECK admits only the four kinds it had."""
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, db.session)

            with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
                _stamp_kind(seed_user["user"].id, "passkey_added")
            assert constraint_name_from(excinfo.value) == (
                "ck_users_security_event_kind"
            )

    def test_the_downgrade_clears_a_passkey_banner_and_keeps_another(
        self, app, seed_user, second_user,
    ):
        """A passkey banner cannot live under four kinds; a password one can."""
        passkey_user = seed_user["user"].id
        password_user = second_user["user"].id
        # Reading the ids opened the outer session's transaction on
        # ``auth.users``, whose lock would block the downgrade's ALTER of that
        # table in the nested context (``run_migration_callable``'s contract).
        db.session.commit()
        with app.app_context():
            _stamp_kind(passkey_user, "passkey_removed")
            _stamp_kind(password_user, "password_changed")

            run_migration_callable(_MIGRATION.downgrade, db.session)

            rows = dict(db.session.execute(sqlalchemy.text(
                "SELECT id, last_security_event_kind FROM auth.users "
                "WHERE id IN (:a, :b)"
            ), {"a": passkey_user, "b": password_user}).all())
            assert rows == {
                passkey_user: None, password_user: "password_changed",
            }
            assert db.session.execute(sqlalchemy.text(
                "SELECT last_security_event_at FROM auth.users WHERE id = :id"
            ), {"id": passkey_user}).scalar() is None

    def test_the_upgrade_restores_every_named_constraint_and_the_trigger(
        self, app, seed_user,
    ):
        """Down, then up: the table and the six kinds return as the head has them."""
        with app.app_context():
            before = _constraints()
            run_migration_callable(_MIGRATION.downgrade, db.session)
            run_migration_callable(_MIGRATION.upgrade, db.session)

            assert _constraints() == before == _NAMED_CONSTRAINTS
            assert _trigger_count() == 1
            assert db.session.execute(sqlalchemy.text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE schemaname = 'auth' AND indexname = :name"
            ), {"name": _INDEX}).scalar() == 1
            _stamp_kind(seed_user["user"].id, "passkey_added")
