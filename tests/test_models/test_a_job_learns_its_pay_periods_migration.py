"""Migration ``0fe9c332b9b0``: a job learns its pay periods.

Plan step salary plan#3048 (task plan#3099), ruling plan#3096.  The revision
adds ``salary.salary_profiles.period_end_days_before_payday``, fills every
existing job with the developer's 6 (his 07-16 stub: Pay Date 07-16, Period
End 07-10), makes it NOT NULL and bounds it to 0..365.  Its downgrade drops
the bound and the column.

* :class:`TestTheRevision` -- where it sits.
* :class:`TestTheRefusal` -- the three-step rule's NULL check, through a stub
  bind with no database.
* :class:`TestTheRoundTrip` -- both directions RUN on this test's own
  database: the column goes and comes back holding 6, whatever the job had.
* :class:`TestTheRange` -- the CHECK refuses one past each end.
* :class:`TestTheTable` -- the migrated table matches the model.
"""

from types import SimpleNamespace

import pytest
import sqlalchemy
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from tests._test_helpers import (
    load_migration_module,
    make_salary_profile,
    run_migration_callable,
)

_MIGRATION = load_migration_module("0fe9c332b9b0_a_job_learns_its_pay_periods.py")
_COLUMN = "period_end_days_before_payday"


class _StubBind:
    """A bind that answers every statement with *rows* and records it."""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    def execute(self, statement):
        """Record *statement* and answer the stubbed rows."""
        self.statements.append(str(statement))
        return SimpleNamespace(all=lambda: self._rows)


class TestTheRevision:
    """Where the revision sits."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration onto the raise-date head."""
        assert _MIGRATION.revision == "0fe9c332b9b0"
        assert _MIGRATION.down_revision == "6740ce13e174"


class TestTheRefusal:
    """A job left without a gap is named before the column turns NOT NULL."""

    def test_a_filled_table_passes(self):
        """No NULL after the backfill: one SELECT, nothing refused."""
        bind = _StubBind([])

        _MIGRATION.refuse_unfilled_profiles(bind)

        assert len(bind.statements) == 1
        assert f"{_COLUMN} IS NULL" in bind.statements[0]

    def test_an_unfilled_job_is_refused_by_id(self):
        """Every unfilled job is named, with the SELECT that finds them."""
        bind = _StubBind([
            SimpleNamespace(id=3, user_id=1), SimpleNamespace(id=8, user_id=2),
        ])

        with pytest.raises(RuntimeError) as refused:
            _MIGRATION.refuse_unfilled_profiles(bind)

        message = str(refused.value)
        assert "id=3 user_id=1, id=8 user_id=2" in message
        assert f"WHERE {_COLUMN} IS NULL" in message


def _gap_column():
    """``(is_nullable, column_default)`` of the column, or ``None`` when absent."""
    row = db.session.execute(sqlalchemy.text(
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'salary' AND table_name = 'salary_profiles' "
        "AND column_name = :column"
    ), {"column": _COLUMN}).one_or_none()
    return None if row is None else (row.is_nullable, row.column_default)


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone)."""

    def test_down_drops_it_and_up_fills_every_job_with_six(self, app, seed_user):
        """A job stating 3 comes back stating 6: the upgrade fills, it does not keep.

        The downgrade has nowhere to keep the 3, so the upgrade's backfill is
        what every job then holds -- the developer's gap, the one job
        production has.
        """
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session)
            profile.period_end_days_before_payday = 3
            db.session.commit()
            profile_id = profile.id

            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert _gap_column() is None

            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert _gap_column() == ("NO", None)
            assert db.session.execute(sqlalchemy.text(
                f"SELECT {_COLUMN} FROM salary.salary_profiles WHERE id = :id"
            ), {"id": profile_id}).scalar() == 6


@pytest.mark.usefixtures("db")
class TestTheRange:
    """``ck_salary_profiles_period_end_days_range``: 0..365, a backstop to the form."""

    @pytest.mark.parametrize("days", [-1, 366])
    def test_one_past_either_end_is_refused(self, app, seed_user, days):
        """-1 and 366 are refused by name; the form refuses both first, in words."""
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session)
            db.session.commit()
            profile.period_end_days_before_payday = days
            with pytest.raises(IntegrityError) as refused:
                db.session.commit()
            db.session.rollback()
            assert "ck_salary_profiles_period_end_days_range" in str(refused.value)

    @pytest.mark.parametrize("days", [0, 365])
    def test_each_end_is_taken(self, app, seed_user, days):
        """0 and 365 are stored."""
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session)
            profile.period_end_days_before_payday = days
            db.session.commit()
            db.session.expire_all()
            assert profile.period_end_days_before_payday == days


class TestTheTable:
    """The migrated table and the model agree."""

    def test_autogenerate_sees_no_drift_on_the_profile_table(self, app):
        """The migrated ``salary.salary_profiles`` matches the model."""
        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_schemas": True,
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table" or (obj.schema, name) == ("salary", "salary_profiles")
                    ),
                },
            )
            assert compare_metadata(ctx, db.metadata) == []
