"""``auth.users.trusts_ended_at`` is the account's sign-out time, renamed for its one job.

Plan#2171, task plan#3931, rulings **plan#3927** and **plan#3952** (migration
``dc6a49d5f720``).  Every sign-out everywhere ends sessions by deleting their
sign-in records, so the column keeps one job, ending trusted devices, and its
name says so.  A rename and nothing else: the type, the nullability and every
value carry over in both directions.

* :class:`TestTheColumn` -- the head build holds the new name only, as the
  model declares it.
* :class:`TestTheRoundTrip` -- both directions run on this test's own
  database, each carrying a stored instant and a NULL across.
"""

from datetime import datetime, timezone

import pytest
import sqlalchemy

from app.extensions import db
from app.models.user import User
from tests._test_helpers import load_migration_module, run_migration_callable

_MIGRATION = load_migration_module("dc6a49d5f720_the_sign_out_time_ends_only_trusts.py")

_OLD = "session_invalidated_at"
_NEW = "trusts_ended_at"

#: A trust-ending instant one user's row holds across the round trip.
_T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _the_column(name):
    """Return ``(data_type, is_nullable)`` of ``auth.users.<name>``, or ``None``.

    Asked of ``information_schema``, so it reads the database and not the model.
    """
    return db.session.execute(sqlalchemy.text(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'auth' AND table_name = 'users' "
        "AND column_name = :name"
    ), {"name": name}).one_or_none()


def _values(column, user_ids):
    """Return ``{user_id: value}`` of ``auth.users.<column>`` for *user_ids*, by SQL.

    Through a table clause rather than the model, because between the
    downgrade and the upgrade the model names a column the database does not
    hold.
    """
    users = sqlalchemy.table(
        "users", sqlalchemy.column("id"), sqlalchemy.column(column), schema="auth",
    )
    return dict(db.session.execute(
        sqlalchemy.select(users.c.id, users.c[column]).where(users.c.id.in_(user_ids))
    ).all())


class TestTheColumn:
    """The head build holds the new name only."""

    def test_the_model_and_the_database_name_it_trusts_ended_at(self):
        """A nullable timestamptz under the new name; the old name is gone."""
        assert _NEW in User.__table__.columns
        assert _OLD not in User.__table__.columns
        assert _the_column(_NEW) == ("timestamp with time zone", "YES")
        assert _the_column(_OLD) is None


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone).

    In the test's own app context: renaming a column of ``auth.users`` locks
    it, and the runner commits only the session it is handed
    (``run_migration_callable``), so a read of ``auth.users`` left open in an
    outer scope would hold the rename.
    """

    def test_the_downgrade_restores_the_old_name_with_every_value(
        self, seed_user, seed_second_user,
    ):
        """Down: the old name holds the instant and the NULL; the new name is gone."""
        ended, never = seed_user["user"].id, seed_second_user["user"].id
        seed_user["user"].trusts_ended_at = _T0
        db.session.commit()

        run_migration_callable(_MIGRATION.downgrade, db.session)

        assert _the_column(_NEW) is None
        assert _the_column(_OLD) == ("timestamp with time zone", "YES")
        assert _values(_OLD, (ended, never)) == {ended: _T0, never: None}

    def test_the_upgrade_carries_every_value_back(
        self, seed_user, seed_second_user,
    ):
        """Down, then up: the new name holds what it held, the type and nullability kept."""
        ended, never = seed_user["user"].id, seed_second_user["user"].id
        seed_user["user"].trusts_ended_at = _T0
        db.session.commit()

        run_migration_callable(_MIGRATION.downgrade, db.session)
        run_migration_callable(_MIGRATION.upgrade, db.session)

        assert _the_column(_OLD) is None
        assert _the_column(_NEW) == ("timestamp with time zone", "YES")
        assert _values(_NEW, (ended, never)) == {ended: _T0, never: None}
        db.session.expire_all()
        assert db.session.get(User, ended).trusts_ended_at == _T0
