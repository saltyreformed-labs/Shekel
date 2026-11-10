"""Migration ``6740ce13e174``: a raise states the date it starts.

Plan step salary plan#3048, rulings plan#3090 (a raise states its start date)
and plan#3094 (its month and year are deleted once copied into it).  The
revision adds ``salary.salary_raises.effective_date``, backfills each row with
the 1st of its month, drops ``effective_year`` and ``effective_month`` with
the four constraints over them, and puts the date's three CHECKs and its
duplicate guard in their places.  Its downgrade rebuilds the pair and refuses
while any raise starts on a day other than the 1st.

* :class:`TestTheRevision` -- where it sits and the ``Review:`` line a dropped
  column owes.
* :class:`TestTheRefusals` -- each refusal's decision and message through a
  stub bind, with no database.
* :class:`TestTheRoundTrip` -- both directions RUN on this test's own
  database: the pair is rebuilt from the date and the date from the pair,
  and a raise off the 1st refuses the downgrade on real rows.
* :class:`TestTheTable` -- the migrated table matches the model.
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from app import ref_cache
from app.enums import RaiseTypeEnum
from app.extensions import db
from app.models.salary_raise import SalaryRaise
from tests._test_helpers import (
    load_migration_module,
    make_salary_profile,
    run_migration_callable,
)

_MIGRATION = load_migration_module("6740ce13e174_a_raise_states_the_date_it_starts.py")


class _StubResult:
    """What ``bind.execute(...)`` returns: rows through ``.all()``."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        """Return the stubbed rows."""
        return self._rows


class _StubBind:
    """A bind that answers every statement with *rows* and records it."""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    def execute(self, statement):
        """Record *statement* and answer the stubbed rows."""
        self.statements.append(str(statement))
        return _StubResult(self._rows)


class TestTheRevision:
    """Where the revision sits and what it owes the rules."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "6740ce13e174"
        assert _MIGRATION.down_revision == "897a6663d221"

    def test_it_is_reviewed(self):
        """Two dropped columns carry the ``Review:`` line the rules require."""
        assert "Review: Josh, 2026-11-09" in _MIGRATION.__doc__


class TestTheRefusals:
    """Neither direction loses a stated fact silently."""

    def test_a_dated_table_passes(self):
        """Every row dated after the backfill: nothing refused."""
        bind = _StubBind([])

        _MIGRATION.refuse_undated_raises(bind)

        assert len(bind.statements) == 1
        assert "effective_date IS NULL" in bind.statements[0]

    def test_an_undated_raise_is_refused_by_id(self):
        """Every undated row is named, with the SELECT that finds them."""
        bind = _StubBind([
            SimpleNamespace(id=3, salary_profile_id=1),
            SimpleNamespace(id=8, salary_profile_id=2),
        ])

        with pytest.raises(RuntimeError) as refused:
            _MIGRATION.refuse_undated_raises(bind)

        message = str(refused.value)
        assert "id=3 salary_profile_id=1" in message
        assert "id=8 salary_profile_id=2" in message
        assert "WHERE effective_date IS NULL" in message

    def test_raises_on_the_first_pass(self):
        """Every raise on its month's 1st: the downgrade loses nothing."""
        bind = _StubBind([])

        _MIGRATION.refuse_raises_off_the_first(bind)

        assert len(bind.statements) == 1
        assert "EXTRACT(DAY FROM effective_date) <> 1" in bind.statements[0]

    def test_a_raise_off_the_first_is_refused_by_id(self):
        """Every such raise is named with its date and the ruling."""
        bind = _StubBind([
            SimpleNamespace(id=4, salary_profile_id=1, effective_date=date(2026, 7, 15)),
        ])

        with pytest.raises(RuntimeError) as refused:
            _MIGRATION.refuse_raises_off_the_first(bind)

        message = str(refused.value)
        assert "id=4 salary_profile_id=1 effective_date=2026-07-15" in message
        assert "plan#3090" in message


def _columns() -> set[str]:
    """The raise table's columns, asked of PostgreSQL."""
    return set(db.session.execute(sqlalchemy.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'salary' AND table_name = 'salary_raises'"
    )).scalars())


def _add_raise(profile_id: int, starts: date) -> int:
    """Commit a recurring 3% merit raise starting *starts*; return its id."""
    row = SalaryRaise(
        salary_profile_id=profile_id,
        raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
        effective_date=starts,
        percentage=Decimal("0.0300"),
        is_recurring=True,
    )
    db.session.add(row)
    db.session.commit()
    return row.id


@pytest.mark.usefixtures("db")
class TestTheRoundTrip:
    """Both directions run on this test's own database (the ``db`` fixture's clone)."""

    def test_down_rebuilds_the_pair_and_up_rebuilds_the_date(self, app, seed_user):
        """A July 1 raise round-trips through year 2026, month 7 exactly."""
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session)
            raise_id = _add_raise(profile.id, date(2026, 7, 1))

            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert {"effective_year", "effective_month"} <= _columns()
            assert "effective_date" not in _columns()
            assert db.session.execute(sqlalchemy.text(
                "SELECT effective_year, effective_month FROM salary.salary_raises "
                "WHERE id = :id"
            ), {"id": raise_id}).one() == (2026, 7)

            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert "effective_date" in _columns()
            assert not {"effective_year", "effective_month"} & _columns()
            assert db.session.execute(sqlalchemy.text(
                "SELECT effective_date FROM salary.salary_raises WHERE id = :id"
            ), {"id": raise_id}).scalar() == date(2026, 7, 1)

    def test_a_raise_off_the_first_refuses_the_downgrade(self, app, seed_user):
        """A July 15 raise has no home in a month column; the table is left as it was."""
        with app.app_context():
            profile = make_salary_profile(seed_user, db.session)
            raise_id = _add_raise(profile.id, date(2026, 7, 15))

            with pytest.raises(RuntimeError) as refused:
                run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.rollback()

            assert f"id={raise_id} " in str(refused.value)
            assert "effective_date" in _columns()
            assert "effective_month" not in _columns()


class TestTheTable:
    """The migrated table and the model agree."""

    def test_autogenerate_sees_no_drift_on_the_raise_table(self, app):
        """The migrated ``salary.salary_raises`` matches the model."""
        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_schemas": True,
                    "include_object": lambda obj, name, type_, *_: (
                        type_ != "table" or (obj.schema, name) == ("salary", "salary_raises")
                    ),
                },
            )
            assert compare_metadata(ctx, db.metadata) == []
