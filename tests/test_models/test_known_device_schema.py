"""``auth.known_devices`` refuses what no writer should be trusted to avoid.

Plan#2173, ruling **plan#2406**, landed the record of a browser a user trusted
to skip the two-factor code.  Plan#2772, ruling **plan#2994**, made it the
record of every browser that finished a sign-in (migration ``b7e4c19a2d60``),
with a trust one fact on it.

* :class:`TestTheColumnsAreWhatTheRulingSaid` -- seven columns, only the trust
  nullable, and the three absences (no expiry, no revoked flag, no lock)
  pinned like presences, because each would be a second home for a derived
  fact (rule 14) or a lock ruling plan#2996 did not make.
* :class:`TestTheKeys` -- FIRING CONTROLS.  The service never writes these
  states, so a test that only asserted a constraint EXISTS would pass against
  one admitting everything.  Each writes the state, flushes, and asserts the
  refusal BY NAME.
* :class:`TestTheTrustIsAudited` -- forgetting a device is a DELETE, and the
  audit trigger is what records it ever happened.
* :class:`TestTheRoundTrip` -- both directions run on this test's own
  database: the downgrade restores the old table and deletes the rows holding
  no trust, and the upgrade puts back every named constraint, the trigger and
  the backfill.
"""

import hashlib
import secrets
from datetime import datetime, timezone

import pytest
import sqlalchemy
import sqlalchemy.exc

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db
from app.models.known_device import KnownDevice
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "b7e4c19a2d60_a_browser_holds_one_record.py",
)

#: Every constraint the migration names, and the kind PostgreSQL files it as.
_NAMED_CONSTRAINTS = {
    "pk_known_devices": "p",
    "fk_known_devices_user_id": "f",
    "uq_known_devices_token_hash": "u",
    "ck_known_devices_token_hash_sha256_hex": "c",
    "ck_known_devices_failed_login_count_non_negative": "c",
}

_INDEX = "ix_known_devices_user_id"

_TRIGGER = "audit_known_devices"

_OLD_TRIGGER = "audit_trusted_devices"

_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


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
        "last_signed_in_at": _T0,
        "trusted_at": _T0,
    }
    fields.update(overrides)
    return KnownDevice(**fields)


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
        "WHERE conrelid = 'auth.known_devices'::regclass "
        "AND contype IN ('p', 'f', 'u', 'c')"
    )).all())


def _table_exists(name):
    """Return whether the table ``auth.<name>`` exists."""
    return db.session.execute(sqlalchemy.text(
        "SELECT to_regclass(:name) IS NOT NULL"
    ), {"name": f"auth.{name}"}).scalar()


def _trigger_count(name=_TRIGGER):
    """Return how many triggers called *name* the database holds."""
    return db.session.execute(sqlalchemy.text(
        "SELECT count(*) FROM pg_trigger WHERE tgname = :name "
        "AND NOT tgisinternal"
    ), {"name": name}).scalar()


class TestTheColumnsAreWhatTheRulingSaid:
    """Seven columns; no expiry, no revoked flag and no lock, by ruling."""

    def test_it_carries_exactly_the_seven_columns_ruled(self, app, db):
        """Named rather than counted, so a rename is as visible as an add.

        No ``expires_at`` column (``KnownDevice.trust_expires_at`` is derived
        from ``trusted_at``) and no ``revoked`` flag (ending a trust is the
        user's ``trusts_ended_at``): each would be a stored copy of a
        derived fact with nothing to keep it true.  No ``locked_until``: ten
        wrong passwords delete the row (ruling plan#2996).
        """
        assert {
            column.name for column in KnownDevice.__table__.columns
        } == {
            "id", "user_id", "token_hash", "user_agent",
            "last_signed_in_at", "trusted_at", "failed_login_count",
        }

    def test_only_the_trust_is_nullable_in_the_database(self, app, db):
        """Asked of ``information_schema``, not the model."""
        nullable = db.session.execute(sqlalchemy.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'auth' AND table_name = 'known_devices' "
            "AND is_nullable = 'YES'"
        )).scalars().all()

        assert nullable == ["trusted_at"]

    def test_the_user_id_is_indexed(self, app, db):
        """The sign-in, the list and the purge each ask for one user's rows."""
        definition = db.session.execute(sqlalchemy.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'auth' AND indexname = :name"
        ), {"name": _INDEX}).scalar()

        assert definition == (
            "CREATE INDEX ix_known_devices_user_id "
            "ON auth.known_devices USING btree (user_id)"
        )


class TestTheKeys:
    """Each refusal, fired and named."""

    def test_a_raw_token_is_refused(self, app, db, seed_user):
        """A writer storing the token itself (43 URL-safe characters) is refused."""
        device = _a_device(
            seed_user["user"].id, token_hash=secrets.token_urlsafe(32),
        )

        assert _refusal_of(device) == "ck_known_devices_token_hash_sha256_hex"

    def test_an_uppercase_hash_is_refused(self, app, db, seed_user):
        """One spelling of a hash: an uppercase digest could never be found."""
        device = _a_device(seed_user["user"].id)
        device.token_hash = device.token_hash.upper()

        assert _refusal_of(device) == "ck_known_devices_token_hash_sha256_hex"

    def test_a_negative_count_is_refused(self, app, db, seed_user):
        """A browser's wrong passwords are never fewer than none."""
        device = _a_device(seed_user["user"].id, failed_login_count=-1)

        assert _refusal_of(device) == (
            "ck_known_devices_failed_login_count_non_negative"
        )

    def test_a_lowercase_sha256_hex_is_admitted(self, app, db, seed_user):
        """The control: the one shape the CHECK admits flushes."""
        db.session.add(_a_device(seed_user["user"].id))
        db.session.flush()

        assert db.session.query(KnownDevice).count() == 1

    def test_one_hash_names_one_device(self, app, db, seed_user):
        """A second row with the same hash is refused."""
        first = _a_device(seed_user["user"].id)
        db.session.add(first)
        db.session.flush()

        second = _a_device(seed_user["user"].id, token_hash=first.token_hash)

        assert _refusal_of(second) == "uq_known_devices_token_hash"

    def test_a_device_of_no_user_is_refused(self, app, db):
        """``user_id`` names a user that exists."""
        assert _refusal_of(_a_device(987654321)) == "fk_known_devices_user_id"

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
            "SELECT count(*) FROM auth.known_devices WHERE user_id = :id"
        ), {"id": user_id}).scalar() == 0


class TestTheTrustIsAudited:
    """Forgetting a device is a DELETE, so the trail is the record."""

    def test_the_table_is_in_the_audited_list(self, app, db):
        """``app.audit_infrastructure.AUDITED_TABLES`` names it."""
        assert ("auth", "known_devices") in AUDITED_TABLES

    def test_the_migration_and_the_audit_list_agree(self, app, db):
        """Two lists that must agree are two lists that can drift."""
        audited = {name for schema, name in AUDITED_TABLES if schema == "auth"}

        assert _MIGRATION._NEW_TABLE in audited

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
            "WHERE table_name = 'known_devices' "
            "AND operation = 'DELETE' AND row_id = :row_id"
        ), {"row_id": device_id}).scalar()
        assert old["user_id"] == seed_user["user"].id
        assert old["token_hash"] == token_hash


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone)."""

    def test_the_downgrade_restores_the_old_table_and_its_trigger(self, app):
        """Down: the new name is gone, and the old name holds its old five columns."""
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert not _table_exists("known_devices")
            assert _table_exists("trusted_devices")
            assert _trigger_count() == 0
            assert _trigger_count(_OLD_TRIGGER) == 1
            assert set(db.session.execute(sqlalchemy.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'auth' AND table_name = 'trusted_devices'"
            )).scalars()) == {
                "id", "user_id", "token_hash", "user_agent", "trusted_at",
            }

    def test_the_downgrade_keeps_a_trust_and_deletes_the_rest(
        self, app, seed_user,
    ):
        """A row holding no trust cannot live in the old table; a row holding one does."""
        user_id = seed_user["user"].id
        with app.app_context():
            trusted = _a_device(user_id)
            db.session.add_all([trusted, _a_device(user_id, trusted_at=None)])
            db.session.flush()
            trusted_id = trusted.id

            run_migration_callable(_MIGRATION.downgrade, db.session)

            assert db.session.execute(sqlalchemy.text(
                "SELECT id FROM auth.trusted_devices WHERE user_id = :id"
            ), {"id": user_id}).scalars().all() == [trusted_id]

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
            assert _trigger_count(_OLD_TRIGGER) == 0
            assert db.session.execute(sqlalchemy.text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE schemaname = 'auth' AND indexname = :name"
            ), {"name": _INDEX}).scalar() == 1

    def test_the_upgrade_keeps_a_trust_and_backfills_the_rest(
        self, app, seed_user,
    ):
        """A trusted row returns known from its trust, its count zero, and cookie-ready.

        Its token's hash is untouched, so the browser holding that token today
        still names this row after the upgrade.
        """
        user_id = seed_user["user"].id
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, db.session)
            token_hash = hashlib.sha256(
                secrets.token_urlsafe(32).encode(),
            ).hexdigest()
            db.session.execute(sqlalchemy.text(
                "INSERT INTO auth.trusted_devices "
                "(user_id, token_hash, user_agent, trusted_at) "
                "VALUES (:user_id, :token_hash, 'Firefox', :trusted_at)"
            ), {"user_id": user_id, "token_hash": token_hash, "trusted_at": _T0})

            run_migration_callable(_MIGRATION.upgrade, db.session)

            assert db.session.execute(sqlalchemy.text(
                "SELECT token_hash, trusted_at, last_signed_in_at, "
                "failed_login_count FROM auth.known_devices "
                "WHERE user_id = :id"
            ), {"id": user_id}).one() == (token_hash, _T0, _T0, 0)
