"""``auth.trusted_devices`` refuses what no writer should be trusted to avoid.

Plan#2173, ruling **plan#2406**.  Migration ``69634349bdce`` lands the record
of a browser a user trusted to skip the two-factor code.

* :class:`TestTheColumnsAreWhatTheRulingSaid` -- five columns, none nullable,
  and the two absences (no expiry, no revoked flag) pinned like presences,
  because each would be a second home for a derived fact (rule 14).
* :class:`TestTheKeys` -- FIRING CONTROLS.  The service never writes these
  states, so a test that only asserted a constraint EXISTS would pass against
  one admitting everything.  Each writes the state, flushes, and asserts the
  refusal BY NAME.
* :class:`TestTheTrustIsAudited` -- forgetting a device is a DELETE, and the
  audit trigger is what records it ever happened.
* :class:`TestTheRoundTrip` -- both directions run on this test's own
  database: the downgrade drops the table and its trigger, and the upgrade
  puts back every named constraint and the trigger.
"""

import hashlib
import secrets
from datetime import datetime, timezone

import pytest
import sqlalchemy
import sqlalchemy.exc

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db
from app.models.trusted_device import TrustedDevice
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "69634349bdce_a_trusted_device_skips_only_the_code.py",
)

#: Every constraint the migration names, and the kind PostgreSQL files it as.
_NAMED_CONSTRAINTS = {
    "pk_trusted_devices": "p",
    "fk_trusted_devices_user_id": "f",
    "uq_trusted_devices_token_hash": "u",
    "ck_trusted_devices_token_hash_sha256_hex": "c",
}

_INDEX = "ix_trusted_devices_user_id"

_TRIGGER = "audit_trusted_devices"


def _a_device(user_id, **overrides):
    """Return an unsaved device of *user_id*, with fields replaceable.

    Its hash is of a fresh random token, as the service's would be.
    """
    fields = {
        "user_id": user_id,
        "token_hash": hashlib.sha256(
            secrets.token_urlsafe(32).encode(),
        ).hexdigest(),
        "user_agent": "Firefox",
        "trusted_at": datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return TrustedDevice(**fields)


def _refusal_of(device):
    """Add *device*, flush, and return the name of the constraint that refused it."""
    db.session.add(device)
    with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
        db.session.flush()
    db.session.rollback()
    return constraint_name_from(excinfo.value)


def _constraints():
    """Return ``{name: kind}`` of every constraint on the table, from PostgreSQL."""
    return dict(db.session.execute(sqlalchemy.text(
        "SELECT conname, contype FROM pg_constraint "
        "WHERE conrelid = 'auth.trusted_devices'::regclass "
        "AND contype IN ('p', 'f', 'u', 'c')"
    )).all())


def _table_exists():
    """Return whether ``auth.trusted_devices`` exists."""
    return db.session.execute(sqlalchemy.text(
        "SELECT to_regclass('auth.trusted_devices') IS NOT NULL"
    )).scalar()


def _trigger_count():
    """Return how many triggers named :data:`_TRIGGER` the database holds."""
    return db.session.execute(sqlalchemy.text(
        "SELECT count(*) FROM pg_trigger WHERE tgname = :name "
        "AND NOT tgisinternal"
    ), {"name": _TRIGGER}).scalar()


class TestTheColumnsAreWhatTheRulingSaid:
    """Five columns; no expiry and no revoked flag, by ruling."""

    def test_it_carries_exactly_the_five_columns_ruled(self, app, db):
        """Named rather than counted, so a rename is as visible as an add.

        No ``expires_at`` column (``TrustedDevice.expires_at`` is derived from
        ``trusted_at``) and no ``revoked`` flag (revocation is the user's
        ``session_invalidated_at``): each would be a stored copy of a derived
        fact with nothing to keep it true.
        """
        assert {
            column.name for column in TrustedDevice.__table__.columns
        } == {"id", "user_id", "token_hash", "user_agent", "trusted_at"}

    def test_every_column_is_not_null_in_the_database(self, app, db):
        """Asked of ``information_schema``, not the model."""
        nullable = db.session.execute(sqlalchemy.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'auth' AND table_name = 'trusted_devices' "
            "AND is_nullable = 'YES'"
        )).scalars().all()

        assert nullable == []

    def test_the_user_id_is_indexed(self, app, db):
        """The sign-in, the list and the purge each ask for one user's rows."""
        definition = db.session.execute(sqlalchemy.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'auth' AND indexname = :name"
        ), {"name": _INDEX}).scalar()

        assert definition == (
            "CREATE INDEX ix_trusted_devices_user_id "
            "ON auth.trusted_devices USING btree (user_id)"
        )


class TestTheKeys:
    """Each refusal, fired and named."""

    def test_a_raw_token_is_refused(self, app, db, seed_user):
        """A writer storing the token itself (43 URL-safe characters) is refused."""
        device = _a_device(
            seed_user["user"].id, token_hash=secrets.token_urlsafe(32),
        )

        assert _refusal_of(device) == "ck_trusted_devices_token_hash_sha256_hex"

    def test_an_uppercase_hash_is_refused(self, app, db, seed_user):
        """One spelling of a hash: an uppercase digest could never be found."""
        device = _a_device(seed_user["user"].id)
        device.token_hash = device.token_hash.upper()

        assert _refusal_of(device) == "ck_trusted_devices_token_hash_sha256_hex"

    def test_a_lowercase_sha256_hex_is_admitted(self, app, db, seed_user):
        """The control: the one shape the CHECK admits flushes."""
        db.session.add(_a_device(seed_user["user"].id))
        db.session.flush()

        assert db.session.query(TrustedDevice).count() == 1

    def test_one_hash_names_one_device(self, app, db, seed_user):
        """A second row with the same hash is refused."""
        first = _a_device(seed_user["user"].id)
        db.session.add(first)
        db.session.flush()

        second = _a_device(seed_user["user"].id, token_hash=first.token_hash)

        assert _refusal_of(second) == "uq_trusted_devices_token_hash"

    def test_a_device_of_no_user_is_refused(self, app, db):
        """``user_id`` names a user that exists."""
        assert _refusal_of(_a_device(987654321)) == "fk_trusted_devices_user_id"

    def test_a_deleted_users_devices_go_with_them(self, app, db, bare_user):
        """``ON DELETE CASCADE``: the database removes the rows, so none outlives its user."""
        user_id = bare_user["user"].id
        db.session.add(_a_device(user_id))
        db.session.flush()

        db.session.execute(
            sqlalchemy.text("DELETE FROM auth.users WHERE id = :id"),
            {"id": user_id},
        )

        assert db.session.execute(sqlalchemy.text(
            "SELECT count(*) FROM auth.trusted_devices WHERE user_id = :id"
        ), {"id": user_id}).scalar() == 0


class TestTheTrustIsAudited:
    """Forgetting a device is a DELETE, so the trail is the record."""

    def test_the_table_is_in_the_audited_list(self, app, db):
        """``app.audit_infrastructure.AUDITED_TABLES`` names it."""
        assert ("auth", "trusted_devices") in AUDITED_TABLES

    def test_the_migration_and_the_audit_list_agree(self, app, db):
        """Two lists that must agree are two lists that can drift."""
        audited = {name for schema, name in AUDITED_TABLES if schema == "auth"}

        assert _MIGRATION._AUDITED_NEW_TABLE in audited

    def test_the_table_carries_its_trigger(self, app, db):
        """Under the name the deploy's trigger census counts by."""
        assert _trigger_count() == 1

    def test_forgetting_a_device_writes_the_whole_row_to_the_audit_log(
        self, app, db, seed_user,
    ):
        """The DELETE row carries the device's user and hash."""
        device = _a_device(seed_user["user"].id)
        db.session.add(device)
        db.session.flush()
        device_id, token_hash = device.id, device.token_hash

        db.session.delete(device)
        db.session.flush()

        old = db.session.execute(sqlalchemy.text(
            "SELECT old_data FROM system.audit_log "
            "WHERE table_name = 'trusted_devices' "
            "AND operation = 'DELETE' AND row_id = :row_id"
        ), {"row_id": device_id}).scalar()
        assert old["user_id"] == seed_user["user"].id
        assert old["token_hash"] == token_hash


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone)."""

    def test_the_downgrade_drops_the_table_and_its_trigger(self, app):
        """Down: no table, no trigger; the devices are forgotten, as stated."""
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert not _table_exists()
            assert _trigger_count() == 0

    def test_the_upgrade_restores_every_named_constraint_and_the_trigger(
        self, app,
    ):
        """Down, then up: the table returns as the head build has it."""
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
