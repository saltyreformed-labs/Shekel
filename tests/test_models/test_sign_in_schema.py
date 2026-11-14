"""``auth.sign_ins`` refuses what no writer should be trusted to avoid.

Plan#2171, ruling **plan#3925**: each finished sign-in has a row, and a
session is live only while its row is (migration ``764fd7dfb6d4``).

* :class:`TestTheColumnsAreWhatTheRulingSaid` -- four columns, none nullable,
  and the absences (no ended-at, no revoked flag, no expiry) pinned like
  presences, because each would be a second home for a derived fact (rule 14).
* :class:`TestTheKeys` -- FIRING CONTROLS.  The service never writes these
  states, so a test that only asserted a constraint EXISTS would pass against
  one admitting everything.  Each writes the state, flushes, and asserts the
  refusal BY NAME.
* :class:`TestASignOutIsAudited` -- a sign-out is a DELETE, and the audit
  trigger is what records it ever happened.
* :class:`TestTheRoundTrip` -- both directions run on this test's own
  database.
"""

import secrets
from datetime import datetime, timezone

import pytest
import sqlalchemy
import sqlalchemy.exc

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db
from app.models.sign_in import SignIn
from app.utils.bearer_tokens import new_token, token_hash
from tests._test_helpers import (
    constraint_name_from,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module("764fd7dfb6d4_each_sign_in_has_a_record.py")

#: Every constraint the migration names, and the kind PostgreSQL files it as.
_NAMED_CONSTRAINTS = {
    "pk_sign_ins": "p",
    "fk_sign_ins_user_id": "f",
    "uq_sign_ins_token_hash": "u",
    "ck_sign_ins_token_hash_sha256_hex": "c",
}

_INDEX = "ix_sign_ins_user_id"

_TRIGGER = "audit_sign_ins"

_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _a_sign_in(user_id, **overrides):
    """Return an unsaved sign-in of *user_id*, with fields replaceable.

    Its hash is of a fresh token, as the service's would be.
    """
    fields = {
        "user_id": user_id,
        "token_hash": token_hash(new_token()),
        "signed_in_at": _T0,
    }
    fields.update(overrides)
    return SignIn(**fields)


def _refusal_of(sign_in):
    """Add *sign_in*, flush, and return the name of the constraint that refused it."""
    db.session.add(sign_in)
    with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
        db.session.flush()
    db.session.rollback()
    return constraint_name_from(excinfo.value)


def _constraints():
    """Return ``{name: kind}`` of every constraint on the table, from PostgreSQL."""
    return dict(db.session.execute(sqlalchemy.text(
        "SELECT conname, contype FROM pg_constraint "
        "WHERE conrelid = 'auth.sign_ins'::regclass "
        "AND contype IN ('p', 'f', 'u', 'c')"
    )).all())


def _table_exists():
    """Return whether the table ``auth.sign_ins`` exists."""
    return db.session.execute(sqlalchemy.text(
        "SELECT to_regclass('auth.sign_ins') IS NOT NULL"
    )).scalar()


def _trigger_count():
    """Return how many triggers called ``audit_sign_ins`` the database holds."""
    return db.session.execute(sqlalchemy.text(
        "SELECT count(*) FROM pg_trigger WHERE tgname = :name "
        "AND NOT tgisinternal"
    ), {"name": _TRIGGER}).scalar()


class TestTheColumnsAreWhatTheRulingSaid:
    """Four columns; no ended-at, no revoked flag and no expiry, by ruling."""

    def test_it_carries_exactly_the_four_columns_ruled(self, app, db):
        """Named rather than counted, so a rename is as visible as an add.

        No ``ended_at`` or ``revoked`` (a sign-in ends by its row being
        deleted) and no ``expires_at`` (its age is derived from
        ``signed_in_at``): each would be a stored copy of a derived fact with
        nothing to keep it true.
        """
        assert {column.name for column in SignIn.__table__.columns} == {
            "id", "user_id", "token_hash", "signed_in_at",
        }

    def test_no_column_is_nullable_in_the_database(self, app, db):
        """Asked of ``information_schema``, not the model."""
        nullable = db.session.execute(sqlalchemy.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'auth' AND table_name = 'sign_ins' "
            "AND is_nullable = 'YES'"
        )).scalars().all()

        assert nullable == []

    def test_the_user_id_is_indexed(self, app, db):
        """The purge at each sign-in asks for one user's rows."""
        definition = db.session.execute(sqlalchemy.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'auth' AND indexname = :name"
        ), {"name": _INDEX}).scalar()

        assert definition == (
            "CREATE INDEX ix_sign_ins_user_id "
            "ON auth.sign_ins USING btree (user_id)"
        )


class TestTheKeys:
    """Each refusal, fired and named."""

    def test_a_raw_token_is_refused(self, app, db, seed_user):
        """A writer storing the token itself (43 URL-safe characters) is refused."""
        sign_in = _a_sign_in(
            seed_user["user"].id, token_hash=secrets.token_urlsafe(32),
        )

        assert _refusal_of(sign_in) == "ck_sign_ins_token_hash_sha256_hex"

    def test_an_uppercase_hash_is_refused(self, app, db, seed_user):
        """One spelling of a hash: an uppercase digest could never be found."""
        sign_in = _a_sign_in(seed_user["user"].id)
        sign_in.token_hash = sign_in.token_hash.upper()

        assert _refusal_of(sign_in) == "ck_sign_ins_token_hash_sha256_hex"

    def test_a_lowercase_sha256_hex_is_admitted(self, app, db, seed_user):
        """The control: the one shape the CHECK admits flushes."""
        db.session.add(_a_sign_in(seed_user["user"].id))
        db.session.flush()

        assert db.session.query(SignIn).filter_by(
            user_id=seed_user["user"].id, signed_in_at=_T0,
        ).count() == 1

    def test_one_hash_names_one_sign_in(self, app, db, seed_user):
        """A second row with the same hash is refused."""
        first = _a_sign_in(seed_user["user"].id)
        db.session.add(first)
        db.session.flush()

        second = _a_sign_in(seed_user["user"].id, token_hash=first.token_hash)

        assert _refusal_of(second) == "uq_sign_ins_token_hash"

    def test_a_sign_in_of_no_user_is_refused(self, app, db):
        """``user_id`` names a user that exists."""
        assert _refusal_of(_a_sign_in(987654321)) == "fk_sign_ins_user_id"

    def test_a_deleted_users_sign_ins_go_with_them(self, app, db, bare_user):
        """``ON DELETE CASCADE``: the database removes the rows, so none outlives its user."""
        user_id = bare_user["user"].id
        db.session.add(_a_sign_in(user_id))
        db.session.flush()

        db.session.execute(
            sqlalchemy.text("DELETE FROM auth.users WHERE id = :id"),
            {"id": user_id},
        )

        assert db.session.execute(sqlalchemy.text(
            "SELECT count(*) FROM auth.sign_ins WHERE user_id = :id"
        ), {"id": user_id}).scalar() == 0


class TestASignOutIsAudited:
    """A sign-out is a DELETE, so the trail is the record."""

    def test_the_table_is_in_the_audited_list(self, app, db):
        """``app.audit_infrastructure.AUDITED_TABLES`` names it."""
        assert ("auth", "sign_ins") in AUDITED_TABLES

    def test_the_migration_and_the_audit_list_agree(self, app, db):
        """Two lists that must agree are two lists that can drift."""
        audited = {name for schema, name in AUDITED_TABLES if schema == "auth"}

        assert _MIGRATION._NEW_TABLE in audited

    def test_the_table_carries_its_trigger(self, app, db):
        """Under the name the deploy's trigger census counts by."""
        assert _trigger_count() == 1

    def test_a_sign_out_writes_the_whole_row_to_the_audit_log(
        self, app, db, seed_user,
    ):
        """The DELETE row carries the sign-in's user and hash."""
        sign_in = _a_sign_in(seed_user["user"].id)
        db.session.add(sign_in)
        db.session.flush()
        sign_in_id, hashed = sign_in.id, sign_in.token_hash

        db.session.delete(sign_in)
        db.session.flush()

        old = db.session.execute(sqlalchemy.text(
            "SELECT old_data FROM system.audit_log "
            "WHERE table_name = 'sign_ins' "
            "AND operation = 'DELETE' AND row_id = :row_id"
        ), {"row_id": sign_in_id}).scalar()
        assert old["user_id"] == seed_user["user"].id
        assert old["token_hash"] == hashed


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone).

    In the test's own app context, not a nested one: dropping the table drops
    its foreign key, which locks ``auth.users``, and the runner commits only
    the session it is handed (``run_migration_callable``), so a fixture's read
    of ``auth.users`` left open in an outer scope would hold the DROP.
    """

    def test_the_downgrade_drops_the_table_and_its_trigger(self, app, seed_user):
        """Down: the table, a row in it and its trigger are all gone."""
        db.session.add(_a_sign_in(seed_user["user"].id))
        db.session.flush()

        run_migration_callable(_MIGRATION.downgrade, db.session)

        assert not _table_exists()
        assert _trigger_count() == 0

    def test_the_upgrade_restores_every_named_constraint_and_the_trigger(
        self, app,
    ):
        """Down, then up: the table returns as the head build has it."""
        before = _constraints()
        run_migration_callable(_MIGRATION.downgrade, db.session)
        run_migration_callable(_MIGRATION.upgrade, db.session)

        assert _constraints() == before == _NAMED_CONSTRAINTS
        assert _trigger_count() == 1
        assert db.session.execute(sqlalchemy.text(
            "SELECT count(*) FROM pg_indexes "
            "WHERE schemaname = 'auth' AND indexname = :name"
        ), {"name": _INDEX}).scalar() == 1
