"""``auth.passkey_adds`` refuses what no writer should be trusted to avoid.

Plan#2538, ruling **plan#3543**: an add of a passkey in flight waits on the
server, one row per account.  Migration ``1ea44adfeec8`` makes the table.

* :class:`TestTheColumns` -- the eight columns, and exactly the four of the
  passkey made nullable, because the registration's phase has none.
* :class:`TestTheKeys` -- FIRING CONTROLS.  The service never writes these
  states, so a test that only asserted a constraint EXISTS would pass against
  one admitting everything.  Each writes the state, flushes, and asserts the
  refusal BY NAME, beside a control that the edge the CHECK admits flushes.
* :class:`TestAddsAreAudited` -- each add begun, registered and kept is on
  the trail.
* :class:`TestTheRoundTrip` -- both directions run on this test's own
  database.
"""

from datetime import datetime, timezone

import pytest
import sqlalchemy
import sqlalchemy.exc

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db
from app.models.passkey import CREDENTIAL_ID_MAX_BYTES, NAME_MAX_LENGTH, SIGN_COUNT_MAX
from app.models.passkey_add import CHALLENGE_MIN_BYTES, PasskeyAdd
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module("1ea44adfeec8_an_add_in_flight_lives_on_the_server.py")

#: Every constraint the migration names on the table, and its kind.
_NAMED_CONSTRAINTS = {
    "pk_passkey_adds": "p",
    "fk_passkey_adds_user_id": "f",
    "uq_passkey_adds_user_id": "u",
    "ck_passkey_adds_challenge_length": "c",
    "ck_passkey_adds_made_together": "c",
    "ck_passkey_adds_credential_id_length": "c",
    "ck_passkey_adds_public_key_not_empty": "c",
    "ck_passkey_adds_sign_count_range": "c",
    "ck_passkey_adds_name_not_blank": "c",
}

_TRIGGER = "audit_passkey_adds"

_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

#: The passkey a registration made, as a row in the sign-in's phase holds it.
_MADE = {
    "credential_id": b"\x01" * 16,
    "public_key": b"\xa5\x01\x02\x03\x26",
    "sign_count": 0,
    "name": "iPhone",
}


def _an_add(user_id, *, made=False, **overrides):
    """Return an unsaved add of *user_id*: the registration's phase, or the sign-in's if *made*."""
    fields = {"user_id": user_id, "challenge": b"\x07" * 32, "issued_at": _T0}
    if made:
        fields.update(_MADE)
    fields.update(overrides)
    return PasskeyAdd(**fields)


def _refusal_of(add):
    """Add *add*, flush, and return the name of the constraint that refused it."""
    db.session.add(add)
    with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
        db.session.flush()
    db.session.rollback()
    return constraint_name_from(excinfo.value)


def _admitted(add):
    """Add *add*, flush, and return whether its row now exists."""
    db.session.add(add)
    db.session.flush()
    return db.session.get(PasskeyAdd, add.id) is not None


def _constraints():
    """Return ``{name: kind}`` of every constraint on the table, from PostgreSQL."""
    return dict(db.session.execute(sqlalchemy.text(
        "SELECT conname, contype FROM pg_constraint "
        "WHERE conrelid = 'auth.passkey_adds'::regclass "
        "AND contype IN ('p', 'f', 'u', 'c')"
    )).all())


def _table_exists(name):
    """Return whether ``auth.<name>`` exists."""
    return db.session.execute(sqlalchemy.text(
        "SELECT to_regclass(:name) IS NOT NULL"
    ), {"name": f"auth.{name}"}).scalar()


def _trigger_count():
    """Return how many triggers called ``audit_passkey_adds`` the database holds."""
    return db.session.execute(sqlalchemy.text(
        "SELECT count(*) FROM pg_trigger WHERE tgname = :name "
        "AND NOT tgisinternal"
    ), {"name": _TRIGGER}).scalar()


class TestTheColumns:
    """Eight columns; the passkey made is empty until the registration makes it."""

    def test_it_carries_exactly_the_eight_columns(self, app, db):
        """Named rather than counted, so a rename is as visible as an add.

        No ``ceremony`` column: whether the registration has completed is
        whether the passkey made is filled, and a second statement of it
        could disagree (rule 14).
        """
        assert {column.name for column in PasskeyAdd.__table__.columns} == {
            "id", "user_id", "challenge", "issued_at",
            "credential_id", "public_key", "sign_count", "name",
        }

    def test_only_the_passkey_made_is_nullable_in_the_database(self, app, db):
        """Asked of ``information_schema``, not the model."""
        nullable = db.session.execute(sqlalchemy.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'auth' AND table_name = 'passkey_adds' "
            "AND is_nullable = 'YES' ORDER BY column_name"
        )).scalars().all()

        assert nullable == ["credential_id", "name", "public_key", "sign_count"]

    def test_the_account_is_found_by_a_unique_index(self, app, db):
        """The one-per-account constraint is the index the lookup by account uses."""
        definition = db.session.execute(sqlalchemy.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'auth' AND indexname = 'uq_passkey_adds_user_id'"
        )).scalar()

        assert definition == (
            "CREATE UNIQUE INDEX uq_passkey_adds_user_id "
            "ON auth.passkey_adds USING btree (user_id)"
        )


class TestTheKeys:
    """Each refusal, fired and named, beside the edge it admits."""

    def test_an_add_whose_registration_is_in_flight_is_admitted(self, app, db, seed_user):
        """The control: a challenge and its moment, no passkey made yet."""
        assert _admitted(_an_add(seed_user["user"].id))

    def test_an_add_whose_sign_in_is_in_flight_is_admitted(self, app, db, seed_user):
        """The control: the passkey made, all four filled."""
        assert _admitted(_an_add(seed_user["user"].id, made=True))

    @pytest.mark.parametrize("column", sorted(_MADE))
    def test_a_passkey_made_missing_one_part_is_refused(self, app, db, seed_user, column):
        """Three of the four: the keeping would copy a passkey with a hole in it."""
        add = _an_add(seed_user["user"].id, made=True, **{column: None})

        assert _refusal_of(add) == "ck_passkey_adds_made_together"

    @pytest.mark.parametrize("column", sorted(_MADE))
    def test_one_part_of_a_passkey_alone_is_refused(self, app, db, seed_user, column):
        """One of the four with no passkey made."""
        add = _an_add(seed_user["user"].id, **{column: _MADE[column]})

        assert _refusal_of(add) == "ck_passkey_adds_made_together"

    def test_a_challenge_shorter_than_webauthn_advises_is_refused(self, app, db, seed_user):
        """15 bytes, one short of 16."""
        add = _an_add(seed_user["user"].id, challenge=b"\x07" * (CHALLENGE_MIN_BYTES - 1))

        assert _refusal_of(add) == "ck_passkey_adds_challenge_length"

    def test_a_challenge_of_16_bytes_is_admitted(self, app, db, seed_user):
        """The control: the edge the CHECK admits."""
        assert _admitted(_an_add(
            seed_user["user"].id, challenge=b"\x07" * CHALLENGE_MIN_BYTES,
        ))

    def test_an_empty_credential_id_is_refused(self, app, db, seed_user):
        """``auth.passkeys`` would refuse it at the keeping."""
        add = _an_add(seed_user["user"].id, made=True, credential_id=b"")

        assert _refusal_of(add) == "ck_passkey_adds_credential_id_length"

    def test_a_credential_id_past_webauthns_bound_is_refused(self, app, db, seed_user):
        """1024 bytes is one past the 1023 WebAuthn allows."""
        add = _an_add(
            seed_user["user"].id, made=True,
            credential_id=b"\x02" * (CREDENTIAL_ID_MAX_BYTES + 1),
        )

        assert _refusal_of(add) == "ck_passkey_adds_credential_id_length"

    def test_a_credential_id_at_webauthns_bound_is_admitted(self, app, db, seed_user):
        """The control: 1023 bytes flushes."""
        assert _admitted(_an_add(
            seed_user["user"].id, made=True,
            credential_id=b"\x03" * CREDENTIAL_ID_MAX_BYTES,
        ))

    def test_an_empty_public_key_is_refused(self, app, db, seed_user):
        """No signature can be checked against nothing."""
        add = _an_add(seed_user["user"].id, made=True, public_key=b"")

        assert _refusal_of(add) == "ck_passkey_adds_public_key_not_empty"

    def test_a_negative_count_is_refused(self, app, db, seed_user):
        """The counter is unsigned."""
        add = _an_add(seed_user["user"].id, made=True, sign_count=-1)

        assert _refusal_of(add) == "ck_passkey_adds_sign_count_range"

    def test_a_count_past_32_bits_is_refused(self, app, db, seed_user):
        """The authenticator's field is 32 bits, so 2**32 cannot be read from one."""
        add = _an_add(seed_user["user"].id, made=True, sign_count=SIGN_COUNT_MAX + 1)

        assert _refusal_of(add) == "ck_passkey_adds_sign_count_range"

    def test_the_largest_32_bit_count_is_admitted(self, app, db, seed_user):
        """The control: 2**32 - 1 flushes."""
        assert _admitted(_an_add(
            seed_user["user"].id, made=True, sign_count=SIGN_COUNT_MAX,
        ))

    def test_a_blank_name_is_refused(self, app, db, seed_user):
        """Spaces alone label nothing on the Security page."""
        add = _an_add(seed_user["user"].id, made=True, name="   ")

        assert _refusal_of(add) == "ck_passkey_adds_name_not_blank"

    def test_a_name_past_its_length_is_refused(self, app, db, seed_user):
        """The column holds :data:`NAME_MAX_LENGTH` characters and no more."""
        db.session.add(_an_add(
            seed_user["user"].id, made=True, name="x" * (NAME_MAX_LENGTH + 1),
        ))
        with pytest.raises(sqlalchemy.exc.DataError):
            db.session.flush()
        db.session.rollback()

    def test_one_account_holds_one_add(self, app, db, seed_user):
        """A second row for the account is refused: a newer add replaces the row instead."""
        db.session.add(_an_add(seed_user["user"].id))
        db.session.flush()

        assert _refusal_of(_an_add(seed_user["user"].id)) == "uq_passkey_adds_user_id"

    def test_two_accounts_each_hold_one(self, app, db, seed_user, second_user):
        """The control: the constraint is per account."""
        db.session.add(_an_add(seed_user["user"].id))
        db.session.flush()

        assert _admitted(_an_add(second_user["user"].id))

    def test_an_add_of_no_user_is_refused(self, app, db):
        """``user_id`` names a user that exists."""
        assert _refusal_of(_an_add(987654321)) == "fk_passkey_adds_user_id"

    def test_a_deleted_users_add_goes_with_them(self, app, db, bare_user):
        """``ON DELETE CASCADE``: no add outlives its user."""
        user_id = bare_user["user"].id
        db.session.add(_an_add(user_id, made=True))
        db.session.flush()

        db.session.execute(
            sqlalchemy.text("DELETE FROM auth.users WHERE id = :id"),
            {"id": user_id},
        )

        assert db.session.execute(sqlalchemy.text(
            "SELECT count(*) FROM auth.passkey_adds WHERE user_id = :id"
        ), {"id": user_id}).scalar() == 0


class TestAddsAreAudited:
    """Each add begun, registered and kept is on the trail."""

    def test_the_table_is_in_the_audited_list(self, app, db):
        """``app.audit_infrastructure.AUDITED_TABLES`` names it."""
        assert ("auth", "passkey_adds") in AUDITED_TABLES

    def test_the_migration_and_the_audit_list_agree(self, app, db):
        """Two lists that must agree are two lists that can drift."""
        audited = {name for schema, name in AUDITED_TABLES if schema == "auth"}

        assert _MIGRATION._NEW_TABLE in audited

    def test_the_table_carries_its_trigger(self, app, db):
        """Under the name the deploy's trigger census counts by."""
        assert _trigger_count() == 1

    def test_beginning_an_add_writes_its_row_to_the_audit_log(self, app, db, seed_user):
        """The INSERT row carries the add's account and its moment."""
        add = _an_add(seed_user["user"].id)
        db.session.add(add)
        db.session.flush()

        new = db.session.execute(sqlalchemy.text(
            "SELECT new_data FROM system.audit_log "
            "WHERE table_name = 'passkey_adds' "
            "AND operation = 'INSERT' AND row_id = :row_id"
        ), {"row_id": add.id}).scalar()
        assert new["user_id"] == seed_user["user"].id
        assert new["credential_id"] is None


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone)."""

    def test_the_downgrade_drops_the_table_and_its_trigger_and_nothing_else(self, app):
        """Down: no adds table, no trigger left behind, and the passkeys table stays."""
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert not _table_exists("passkey_adds")
            assert _trigger_count() == 0
            assert _table_exists("passkeys")

    def test_the_upgrade_restores_every_named_constraint_and_the_trigger(self, app):
        """Down, then up: the table returns as the head has it."""
        with app.app_context():
            before = _constraints()
            run_migration_callable(_MIGRATION.downgrade, db.session)
            run_migration_callable(_MIGRATION.upgrade, db.session)

            assert _constraints() == before == _NAMED_CONSTRAINTS
            assert _trigger_count() == 1
