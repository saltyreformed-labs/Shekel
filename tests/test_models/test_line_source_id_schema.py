"""The bank-line id key's schema: ``budget.line_source_ids`` (plan step plan#2232).

What the DATABASE refuses and keeps, independently of the record door: a
source names ONE line per id (``uq_line_source_ids_account_source_external``),
the id's account is its line's (``fk_line_source_ids_line_account``), the id
goes when its line goes and at no other time (ruling **plan#2233**), a source
that has named a line cannot leave the catalogue, the table is audit-logged,
and the model matches what the migration built.  Every case arranges through
the ORM and reads the database back.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy.exc
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from app import ref_cache
from app.enums import StatementSourceEnum
from app.models.line_source_id import LineSourceId
from app.models.statement_import import BankStatementLine, StatementImport
from tests.test_services.test_statement_import._id_builder import an_id
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_sighting,
    an_import,
)

_DAY = date(2026, 3, 2)


def _secu():
    """The one shipped source's ``ref.statement_sources`` id."""
    return ref_cache.statement_source_id(StatementSourceEnum.SECU_CHECKING_CSV)


def _another_source(db):
    """Seed and return the id of a SECOND source, a catalogue row with no parser."""
    return db.session.execute(text(
        "INSERT INTO ref.statement_sources (name, display_name) "
        "VALUES ('test_other_source', 'Another adapter') RETURNING id"
    )).scalar()


def _ids(db):
    """Every id row, as ``(source_id, external_id, line_id)``, sorted."""
    return sorted(
        (row.source_id, row.external_id, row.line_id)
        for row in db.session.query(LineSourceId).all()
    )


class TestASourceNamesOneLinePerId:
    """``uq_line_source_ids_account_source_external`` -- the rule itself."""

    def test_a_second_line_under_one_sources_id_is_refused(
        self, app, db, seed_user,
    ):
        """The double count the key exists to make unrepresentable."""
        statement = an_import(seed_user)
        first = a_bank_line(seed_user, statement, posted_on=_DAY, amount="-42.17")
        second = a_bank_line(
            seed_user, statement, posted_on=date(2026, 3, 3), amount="-42.17",
        )
        an_id(first, _secu(), "TRN-881")

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            an_id(second, _secu(), "TRN-881")

        assert "uq_line_source_ids_account_source_external" in str(caught.value)

    def test_ANOTHER_sources_id_of_the_same_string_is_its_own(
        self, app, db, seed_user,
    ):
        """Ids are per source: the same string from two adapters is two ids."""
        statement = an_import(seed_user)
        first = a_bank_line(seed_user, statement, posted_on=_DAY, amount="-1.00")
        second = a_bank_line(seed_user, statement, posted_on=_DAY, amount="-2.00")
        other = _another_source(db)

        an_id(first, _secu(), "TRN-881")
        an_id(second, other, "TRN-881")

        assert _ids(db) == sorted([
            (_secu(), "TRN-881", first.id), (other, "TRN-881", second.id),
        ])

    def test_one_line_MAY_hold_two_ids_from_one_source(
        self, app, db, seed_user,
    ):
        """The converse is not the rule: a wording pairing can give a line a second id.

        The model's docstring claims this, and the line-sharing refusal
        (``_plan._refuse_ids_sharing_a_line``) exists because of it.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, posted_on=_DAY, amount="-42.17")

        an_id(line, _secu(), "TRN-881")
        an_id(line, _secu(), "TRN-999")

        assert _ids(db) == [
            (_secu(), "TRN-881", line.id), (_secu(), "TRN-999", line.id),
        ]


class TestTheIdFollowsItsLine:
    """``fk_line_source_ids_line_account`` -- ruling **plan#2233**'s lifetime."""

    def test_the_id_OUTLIVES_its_imports_while_another_import_shows_the_line(
        self, app, db, seed_user,
    ):
        """Deleting the import that recorded the line leaves the line, and the id.

        The import that first showed the line goes; a second import still
        sights it, so the line stands (``budget.remove_line_left_unsighted``)
        and the id stays held on it.  Under the rule before this key the id
        lived on the first import's sighting and went with it.
        """
        first = an_import(seed_user)
        later = an_import(seed_user)
        line = a_bank_line(seed_user, first, posted_on=_DAY, amount="-42.17")
        a_sighting(seed_user, later, line, description="COFFEE")
        an_id(line, _secu(), "TRN-881")
        line_id = line.id

        db.session.delete(db.session.get(StatementImport, first.id))
        db.session.flush()
        db.session.expire_all()

        assert db.session.get(BankStatementLine, line_id) is not None
        assert _ids(db) == [(_secu(), "TRN-881", line_id)]

    def test_the_id_GOES_with_its_line(self, app, db, seed_user):
        """Deleting the last import that shows a line takes the line and its id."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, posted_on=_DAY, amount="-42.17")
        an_id(line, _secu(), "TRN-881")
        line_id = line.id

        db.session.delete(db.session.get(StatementImport, statement.id))
        db.session.flush()
        db.session.expire_all()

        assert db.session.get(BankStatementLine, line_id) is None
        assert _ids(db) == []

    def test_an_id_on_ANOTHER_accounts_line_is_refused(
        self, app, db, seed_user,
    ):
        """The id's account IS its line's: the composite key, not a copy."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, posted_on=_DAY, amount="-42.17")
        db.session.add(LineSourceId(
            account_id=line.account_id + 1, source_id=_secu(),
            external_id="TRN-881", line_id=line.id,
        ))

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            db.session.flush()

        assert "fk_line_source_ids_line_account" in str(caught.value)

    def test_a_source_that_named_a_line_cannot_leave_the_catalogue(
        self, app, db, seed_user,
    ):
        """``fk_line_source_ids_source`` is RESTRICT, the rule for a ``ref`` key."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, posted_on=_DAY, amount="-42.17")
        other = _another_source(db)
        an_id(line, other, "TRN-881")

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            db.session.execute(
                text("DELETE FROM ref.statement_sources WHERE id = :s"),
                {"s": other},
            )

        assert "fk_line_source_ids_source" in str(caught.value)


class TestTheTable:
    """The audit trail, and the model against what the migration built."""

    def test_an_id_is_audit_logged(self, app, db, seed_user):
        """``budget.line_source_ids`` is in ``AUDITED_TABLES`` and its trigger fires."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, posted_on=_DAY, amount="-42.17")
        row = an_id(line, _secu(), "TRN-881")

        logged = db.session.execute(text(
            "SELECT operation FROM system.audit_log "
            "WHERE table_schema = 'budget' AND table_name = 'line_source_ids' "
            "AND row_id = :r"
        ), {"r": row.id}).scalars().all()

        assert logged == ["INSERT"]

    def test_autogenerate_sees_no_drift_on_the_id_key_or_the_sightings(
        self, app, db,
    ):
        """The migrated tables match the models: the key, and the sighting without its id."""
        watched = {
            ("budget", "line_source_ids"),
            ("budget", "statement_line_sightings"),
        }
        ctx = MigrationContext.configure(
            connection=db.session.connection(),
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_schemas": True,
                "include_object": lambda obj, name, type_, *_: (
                    type_ != "table" or (obj.schema, name) in watched
                ),
            },
        )

        assert compare_metadata(ctx, db.metadata) == []
