"""Migration ``6534c1bfeeb1`` -- a bank source's id names one line (plan step plan#2232).

Both directions are driven through the migration's own shipped callables, and
every assertion reads the DATABASE.  The claims graded hardest are the ones
the migration makes about itself: that the upgrade writes ONE key row per
``(account, source, id)`` however many sightings carried it; that the
downgrade writes each id back onto every sighting its OWN source wrote of its
line and onto no other source's; that it drops an id whose source no longer
shows its line (the old rule, ruling **plan#2233**'s state); and that each
direction REFUSES the one state the other side cannot hold, changing nothing,
and prints a query that lists every row of it.  *That the refusal runs before
any DDL is the code's order, which these cases cannot see: the callable
commits only on success and PostgreSQL rolls DDL back, so a refusal after it
would change nothing too.*
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatementSourceEnum
from app.extensions import db
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from tests._test_helpers import (
    load_migration_module,
    run_migration_callable as _run,
)

_MIGRATION = load_migration_module("6534c1bfeeb1_a_bank_id_names_one_line.py")


def _sql(statement, **params):
    """Run one statement that returns rows, and return every row."""
    return db.session.execute(text(statement), params).all()


def _exec(statement, **params):
    """Run one statement that returns no rows."""
    db.session.execute(text(statement), params)


def _another_source():
    """Seed and return the id of a SECOND source, a catalogue row with no parser."""
    return _sql(
        "INSERT INTO ref.statement_sources (name, display_name) "
        "VALUES ('test_other_source', 'Another adapter') RETURNING id",
    )[0][0]


def _an_import(account, source_id, name):
    """One import of *account* from *source_id*, through the head schema."""
    statement = StatementImport(
        account_id=account.id, user_id=account.user_id, source_id=source_id,
        file_name=name, file_digest=name.ljust(64, "0")[:64],
        declared_start=date(2026, 3, 1), declared_end=date(2026, 3, 31),
    )
    db.session.add(statement)
    db.session.flush()
    return statement


def _a_line(account, day, amount):
    """One bank line, unsighted until a caller sights it."""
    line = BankStatementLine(
        account_id=account.id, posted_on=day, amount=Decimal(amount),
        sequence_in_group=0,
    )
    db.session.add(line)
    db.session.flush()
    return line


def _sight(statement, line, description):
    """*statement*'s sighting of *line*."""
    db.session.add(StatementLineSighting(
        account_id=line.account_id, line_id=line.id, import_id=statement.id,
        description=description,
    ))
    db.session.flush()


def _key(line, source_id, external_id):
    """*source_id*'s id for *line*, as the head schema holds it."""
    _exec(
        "INSERT INTO budget.line_source_ids "
        "(account_id, source_id, external_id, line_id) VALUES (:a, :s, :e, :l)",
        a=line.account_id, s=source_id, e=external_id, l=line.id,
    )


def _column_exists():
    """Whether ``statement_line_sightings.external_id`` exists."""
    return bool(_sql(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = "
        "'budget' AND table_name = 'statement_line_sightings' "
        "AND column_name = 'external_id'",
    ))


def _sighting_ids():
    """Every sighting's ``(import_id, line_id, external_id)``, below the revision."""
    return sorted(_sql(
        "SELECT import_id, line_id, external_id "
        "FROM budget.statement_line_sightings",
    ))


@pytest.mark.xdist_group("line_source_id_ddl")
class TestTheRoundTrip:
    """Head -> down -> up, over every shape the downgrade treats differently."""

    def test_down_writes_each_id_to_its_OWN_sources_sightings_and_up_keys_it_once(
        self, db, seed_user,
    ):
        """Three lines, two sources, one id whose source no longer shows its line.

        * ``shared`` -- SECU's two imports and the other source's one all
          sight it; SECU holds ``FIT-1`` on it.  The downgrade writes
          ``FIT-1`` onto BOTH SECU sightings and NOT onto the other source's.
        * ``theirs`` -- only the other source sights it, under ``FIT-1`` too:
          the same string from another source is another id.
        * ``orphan`` -- SECU holds ``FIT-9`` on it, but only the other source
          still sights it (ruling **plan#2233**'s state).  The downgrade has no
          SECU sighting to write ``FIT-9`` onto, so it is dropped: the old
          rule held an id only while its source's sighting carried it.

        Back up, the key holds one row per ``(source, id)`` the sightings
        carried -- ``FIT-1`` once for SECU though two sightings carried it --
        and ``FIT-9`` is gone for good, which is the downgrade's documented
        loss.
        """
        account = seed_user["account"]
        secu = ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        )
        other = _another_source()
        first = _an_import(account, secu, "first.csv")
        again = _an_import(account, secu, "again.csv")
        feed = _an_import(account, other, "feed.dat")
        shared = _a_line(account, date(2026, 3, 2), "-42.17")
        theirs = _a_line(account, date(2026, 3, 3), "-5.00")
        orphan = _a_line(account, date(2026, 3, 4), "-9.00")
        for statement in (first, again, feed):
            _sight(statement, shared, "COFFEE")
        _sight(feed, theirs, "THEIRS")
        _sight(feed, orphan, "ORPHAN")
        _key(shared, secu, "FIT-1")
        _key(theirs, other, "FIT-1")
        _key(orphan, secu, "FIT-9")
        db.session.commit()
        ids = {
            "first": first.id, "again": again.id, "feed": feed.id,
            "shared": shared.id, "theirs": theirs.id, "orphan": orphan.id,
        }

        _run(_MIGRATION.downgrade, db.session)

        assert _sql("SELECT to_regclass('budget.line_source_ids')") == [(None,)]
        assert _sighting_ids() == sorted([
            (ids["first"], ids["shared"], "FIT-1"),
            (ids["again"], ids["shared"], "FIT-1"),
            (ids["feed"], ids["shared"], None),
            (ids["feed"], ids["theirs"], "FIT-1"),
            (ids["feed"], ids["orphan"], None),
        ])
        assert _sql(
            "SELECT count(*) FROM pg_indexes WHERE indexname = "
            "'idx_statement_line_sightings_account_external_id'",
        ) == [(1,)]

        _run(_MIGRATION.upgrade, db.session)

        assert sorted(_sql(
            "SELECT source_id, external_id, line_id FROM budget.line_source_ids",
        )) == sorted([
            (secu, "FIT-1", ids["shared"]),
            (other, "FIT-1", ids["theirs"]),
        ])
        assert not _column_exists()
        assert _sql(
            "SELECT count(*) FROM pg_indexes WHERE indexname = "
            "'idx_statement_line_sightings_account_external_id'",
        ) == [(0,)]
        assert _sql(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
            "AND tgname = 'audit_line_source_ids'",
        ) == [(1,)]


@pytest.mark.xdist_group("line_source_id_ddl")
class TestTheRefusals:
    """Each direction refuses the state the other side cannot hold, and lists it."""

    def test_the_upgrade_REFUSES_one_sources_id_on_two_lines(
        self, db, seed_user,
    ):
        """Below the revision nothing held the rule; the key would fail half-way.

        Stepped down over an empty key, then two SECU sightings of two lines
        are given one id by hand -- the state the door's check refused and a
        writer outside the door could still make.  The upgrade names it and
        changes nothing: the key table is still absent, the column still
        present.  The query its message prints lists the id with both its
        lines and the one import stating it, and the remedy reaches every
        import of that source, not one.
        """
        account = seed_user["account"]
        secu = ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        )
        statement = _an_import(account, secu, "one.csv")
        one = _a_line(account, date(2026, 3, 2), "-1.00")
        two = _a_line(account, date(2026, 3, 3), "-2.00")
        _sight(statement, one, "X")
        _sight(statement, two, "Y")
        db.session.commit()
        _run(_MIGRATION.downgrade, db.session)
        _exec(
            "UPDATE budget.statement_line_sightings SET external_id = 'FIT-1'",
        )
        db.session.commit()

        with pytest.raises(RuntimeError, match="1 bank line id") as caught:
            _run(_MIGRATION.upgrade, db.session)
        db.session.rollback()

        assert _sql("SELECT to_regclass('budget.line_source_ids')") == [(None,)]
        assert _column_exists()
        assert _MIGRATION.IDS_ON_TWO_LINES_SQL.strip() in str(caught.value)
        assert "every import of that source" in str(caught.value)
        assert _sql(_MIGRATION.IDS_ON_TWO_LINES_SQL) == [
            (account.id, secu, "FIT-1", [one.id, two.id], [statement.id]),
        ]

    def test_the_downgrade_REFUSES_a_line_holding_two_ids_its_source_still_shows(
        self, db, seed_user,
    ):
        """Below the revision an id is one import's; which import stated which is lost.

        One SECU line holds ``FIT-1`` and ``FIT-2`` and a SECU sighting still
        shows it.  The downgrade names the count and changes nothing: the
        key table stands with both rows, and no column was added.  The query
        its message prints lists the line with both its ids.
        """
        account = seed_user["account"]
        secu = ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        )
        statement = _an_import(account, secu, "one.csv")
        line = _a_line(account, date(2026, 3, 2), "-42.17")
        _sight(statement, line, "COFFEE")
        _key(line, secu, "FIT-1")
        _key(line, secu, "FIT-2")
        db.session.commit()

        with pytest.raises(RuntimeError, match="1 bank line") as caught:
            _run(_MIGRATION.downgrade, db.session)
        db.session.rollback()

        assert _sql(
            "SELECT count(*) FROM budget.line_source_ids",
        ) == [(2,)]
        assert not _column_exists()
        assert _MIGRATION.LINES_WITH_TWO_IDS_SQL.strip() in str(caught.value)
        assert _sql(_MIGRATION.LINES_WITH_TWO_IDS_SQL) == [
            (account.id, secu, line.id, ["FIT-1", "FIT-2"]),
        ]

    def test_two_ids_on_a_line_its_source_NO_LONGER_shows_are_dropped_not_refused(
        self, db, seed_user,
    ):
        """The refusal's reach ends where the old rule's did.

        The same two ids, but only the other source sights the line: no SECU
        sighting remains to write either onto, so both go, as the old rule
        would have forgotten them, and the downgrade proceeds.
        """
        account = seed_user["account"]
        secu = ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        )
        feed = _an_import(account, _another_source(), "feed.dat")
        line = _a_line(account, date(2026, 3, 2), "-42.17")
        _sight(feed, line, "COFFEE")
        _key(line, secu, "FIT-1")
        _key(line, secu, "FIT-2")
        db.session.commit()
        line_id, feed_id = line.id, feed.id

        _run(_MIGRATION.downgrade, db.session)

        assert _sighting_ids() == [(feed_id, line_id, None)]
        _run(_MIGRATION.upgrade, db.session)
        assert _sql("SELECT count(*) FROM budget.line_source_ids") == [(0,)]
