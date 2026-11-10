"""A source's own id for a line, through the record door (plan step plan#2232).

The id lives once, in ``budget.line_source_ids``, and stays with its LINE for
as long as the line is recorded (ruling **plan#2233**).  These cases grade the
door over that key: the writer records each id once, on the line it names; a
re-sent id meets that line even after the import that first showed it is
undone, while another import still shows the line; the two shapes the key
would refuse are refused first, with a sentence, and write nothing; and the
id is free again once every import showing its line is gone.

**The file is a stand-in, the door is real.**  The one shipped adapter
(SECU's CSV) carries no id, so no CSV bytes can state one; each case that
needs an id replaces the plan's parse with a :class:`ParsedStatement` stating
it (``_plan.parse_statement``, monkeypatched for that one call) and runs
everything after the parse -- the identity check, both id refusals, the
reconciliation, the writer -- unchanged.  Two cases reach the pairing step
directly, the allowance ``test_sightings`` takes, because the shape they
grade (a line only ANOTHER source shows) needs a second source no file can
name.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatementSourceEnum
from app.models.line_source_id import LineSourceId
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from app.services.statement_import import (
    ParsedStatement,
    StatementLine,
    StatementLineIdMoved,
    StatementLineIdsShareALine,
    bank_daily_movements,
    delete_import,
    record_statement,
)
# Pylint: protected-access -- the parse is replaced on the plan module, where
# the door reads it, and the pairing step is a private collaboration of the
# door; a test of the RULE reaches into both, the allowance ``test_sightings``
# takes.
from app.services.statement_import import _plan  # pylint: disable=protected-access
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_import,
)

from . import _csv_builder as build
from ._id_builder import an_id

_SECU = StatementSourceEnum.SECU_CHECKING_CSV
_OCT_8 = date(2026, 10, 8)
_OCT_9 = date(2026, 10, 9)
_COFFEE = "-42.17"


def _line(external_id, *, day=_OCT_8, amount=_COFFEE, description="COFFEE"):
    """One line as an id-carrying source states it."""
    return StatementLine(
        posted_on=day, transaction_on=None, amount=Decimal(amount),
        description=description, external_id=external_id,
    )


def _record(monkeypatch, seed_user, lines, name="feed.dat"):
    """Record *lines* through the door, as one file stating them.

    The parse is the only thing replaced, and only for this call; the file
    answers for the days its lines span, as a CSV export does.
    """
    days = [line.posted_on for line in lines]
    parsed = ParsedStatement(
        external_account_id=build.ACCOUNT_IDENTITY, lines=lines,
        declared_start=min(days), declared_end=max(days),
    )
    with monkeypatch.context() as patch:
        patch.setattr(_plan, "parse_statement", lambda source, payload: parsed)
        return record_statement(
            account_id=seed_user["account"].id,
            user_id=seed_user["user"].id,
            source=_SECU, file_name=name, payload=name.encode(),
        )


def _record_csv(seed_user, entries, name="export.csv"):
    """Record real CSV bytes -- a SECU export, which states no id."""
    return record_statement(
        account_id=seed_user["account"].id, user_id=seed_user["user"].id,
        source=_SECU, file_name=name,
        payload=build.build(build.chained("100.00", entries)),
    )


def _undo(seed_user, outcome):
    """Delete the import *outcome* recorded, through the one undo door."""
    delete_import(
        outcome.import_id, seed_user["user"].id, seed_user["account"].id,
    )


def _ids(db):
    """Every held id, as ``(external_id, posted_on of its line)``, sorted."""
    return sorted(
        db.session.query(LineSourceId.external_id, BankStatementLine.posted_on)
        .join(BankStatementLine, LineSourceId.of_its_line())
        .all()
    )


def _lines(db):
    """Every recorded line, as ``(posted_on, amount)``, sorted."""
    return sorted(
        db.session.query(BankStatementLine.posted_on, BankStatementLine.amount)
        .all()
    )


class TestTheWriterRecordsEachIdOnce:
    """``_record._source_id_of`` -- one row per id, on the line it names."""

    def test_a_fresh_lines_id_is_recorded_on_that_line(
        self, app, db, seed_user, monkeypatch,
    ):
        """The fresh half: the line, its sighting, and its id."""
        _record(monkeypatch, seed_user, [_line("TRN-881")])

        assert _lines(db) == [(_OCT_8, Decimal(_COFFEE))]
        assert _ids(db) == [("TRN-881", _OCT_8)]

    def test_a_re_import_under_the_same_id_writes_no_second_row(
        self, app, db, seed_user, monkeypatch,
    ):
        """The held half paired by its id records a sighting and no id."""
        _record(monkeypatch, seed_user, [_line("TRN-881")], name="one.dat")
        again = _record(monkeypatch, seed_user, [_line("TRN-881")], name="two.dat")

        assert (again.line_count, again.recorded_count) == (1, 0)
        assert db.session.query(StatementLineSighting).count() == 2
        assert _ids(db) == [("TRN-881", _OCT_8)]

    def test_a_new_id_paired_by_WORDING_gives_the_line_a_second_id(
        self, app, db, seed_user, monkeypatch,
    ):
        """The held half paired by wording records the id the file states for it."""
        _record(monkeypatch, seed_user, [_line("TRN-881")], name="one.dat")
        _record(monkeypatch, seed_user, [_line("TRN-999")], name="two.dat")

        assert _lines(db) == [(_OCT_8, Decimal(_COFFEE))]
        assert _ids(db) == [("TRN-881", _OCT_8), ("TRN-999", _OCT_8)]


class TestTheIdStaysWithItsLine:
    """Ruling **plan#2233**: undoing one import does not free an id another import's line keeps."""

    def test_a_re_dated_id_is_REFUSED_after_its_import_is_undone(
        self, app, db, seed_user, monkeypatch,
    ):
        """The ruling's own example, end to end.

        A $42.17 coffee is recorded on Oct 8 as ``TRN-881``; a CSV export
        then shows the same coffee, so the one line has two imports behind
        it.  The first import is undone: the line stays on the CSV's
        sighting, and so does the id.  A file sending ``TRN-881`` dated Oct 9
        is refused, so the bank side of Oct 8-9 stays $42.17 -- under the
        rule before the key, the id went with the undone import's sighting
        and the Oct 9 line was recorded beside the Oct 8 one: $84.34.
        """
        first = _record(monkeypatch, seed_user, [_line("TRN-881")])
        _record_csv(seed_user, [(_OCT_8, _COFFEE, "COFFEE")])
        _undo(seed_user, first)
        assert _ids(db) == [("TRN-881", _OCT_8)]

        with pytest.raises(StatementLineIdMoved) as caught:
            _record(monkeypatch, seed_user, [_line("TRN-881", day=_OCT_9)])

        assert caught.value.recorded == (_OCT_8, Decimal(_COFFEE))
        assert "delete every import that shows it" in str(caught.value)
        assert "even after the import that first showed it is deleted" in str(
            caught.value
        )
        # -42.17 is the one coffee; the refused file added nothing.
        assert bank_daily_movements(seed_user["account"].id) == [
            (_OCT_8, Decimal("-42.17")),
        ]
        assert _lines(db) == [(_OCT_8, Decimal(_COFFEE))]

    def test_the_id_is_FREE_once_every_import_showing_its_line_is_undone(
        self, app, db, seed_user, monkeypatch,
    ):
        """When no other import shows the line, the undo frees the id with it.

        The behaviour the ruling leaves unchanged: the line and its id go
        together, and the next file stating the id on another day records it
        fresh.
        """
        first = _record(monkeypatch, seed_user, [_line("TRN-881")])
        _undo(seed_user, first)
        assert _lines(db) == []
        assert _ids(db) == []

        _record(monkeypatch, seed_user, [_line("TRN-881", day=_OCT_9)])

        assert _lines(db) == [(_OCT_9, Decimal(_COFFEE))]
        assert _ids(db) == [("TRN-881", _OCT_9)]


def _another_source(db):
    """Seed and return the id of a SECOND source, a catalogue row with no parser."""
    return db.session.execute(text(
        "INSERT INTO ref.statement_sources (name, display_name) "
        "VALUES ('test_other_source', 'Another adapter') RETURNING id"
    )).scalar()


def _group(db, seed_user):
    """The seeded account's lines in ordinal order, sightings loaded."""
    db.session.expire_all()
    return (
        db.session.query(BankStatementLine)
        .filter(BankStatementLine.account_id == seed_user["account"].id)
        .order_by(BankStatementLine.sequence_in_group)
        .all()
    )


def _lines_only_another_source_shows(db, seed_user):
    """Two same-day same-amount coffees that ONLY another source has sighted."""
    statement = an_import(seed_user)
    statement.source_id = _another_source(db)
    db.session.flush()
    for ordinal, wording in enumerate(("THEIR FIRST", "THEIR SECOND")):
        a_bank_line(
            seed_user, statement, posted_on=_OCT_8, amount=_COFFEE,
            description=wording, sequence_in_group=ordinal,
        )
    assert db.session.get(StatementImport, statement.id).source_id != (
        ref_cache.statement_source_id(_SECU)
    )


class TestStepOnePairsByTheKey:
    """``_plan._pair_by_id`` reads the key, whether or not this source still shows the line."""

    def test_a_held_id_meets_ITS_line_where_count_would_pick_another(
        self, app, db, seed_user,
    ):
        """No sighting of this source shows either coffee; the key still knows which.

        This source holds ``TRN-882`` on the SECOND coffee (its own imports of
        them undone, another source's sightings keeping both).  Counting
        would pair an incoming line to the FIRST; the id pairs it to the
        second.
        """
        _lines_only_another_source_shows(db, seed_user)
        secu = ref_cache.statement_source_id(_SECU)
        recorded = _group(db, seed_user)
        an_id(recorded[1], secu, "TRN-882")
        incoming = [_line("TRN-882", description="OUR WORD")]

        pairing = _plan._pair_group(  # pylint: disable=protected-access
            incoming, _group(db, seed_user), secu,
            _plan._held_ids(  # pylint: disable=protected-access
                seed_user["account"].id, secu, incoming,
            ),
        )

        assert pairing.held == [(0, 1)]
        assert pairing.fresh == []

    def test_the_count_step_skips_a_line_the_id_step_claimed(
        self, app, db, seed_user,
    ):
        """Step 3 must not hand the id's line to a second incoming line.

        ``TRN-881`` is held on the FIRST coffee; the file states it and one
        line with no id.  The id takes the first coffee, so the count step
        pairs the other line to the SECOND -- never the first again, which
        would be two sightings of one line in one import.
        """
        _lines_only_another_source_shows(db, seed_user)
        secu = ref_cache.statement_source_id(_SECU)
        recorded = _group(db, seed_user)
        an_id(recorded[0], secu, "TRN-881")
        incoming = [
            _line(None, description="NO ID"),
            _line("TRN-881", description="OUR WORD"),
        ]

        pairing = _plan._pair_group(  # pylint: disable=protected-access
            incoming, _group(db, seed_user), secu,
            _plan._held_ids(  # pylint: disable=protected-access
                seed_user["account"].id, secu, incoming,
            ),
        )

        assert sorted(pairing.held) == [(0, 1), (1, 0)]
        assert pairing.fresh == []


class TestTwoIdsOneLine:
    """``StatementLineIdsShareALine`` -- the other shape the key would refuse."""

    def test_a_file_stating_both_ids_of_one_line_as_two_lines_is_refused(
        self, app, db, seed_user, monkeypatch,
    ):
        """Refused with a sentence, and nothing is written.

        The line holds ``TRN-881`` and, by a wording pairing, ``TRN-999``.  A
        file stating both as two coffees says they are two transactions; the
        id step can pair only one to the line, and the other recorded beside
        it would put a held id on a second line.  Before the key this
        recorded the second coffee silently, with ``TRN-999`` on two lines.
        """
        _record(monkeypatch, seed_user, [_line("TRN-881")], name="one.dat")
        _record(monkeypatch, seed_user, [_line("TRN-999")], name="two.dat")
        sightings = db.session.query(StatementLineSighting).count()

        with pytest.raises(StatementLineIdsShareALine) as caught:
            _record(
                monkeypatch, seed_user,
                [_line("TRN-881"), _line("TRN-999")], name="three.dat",
            )

        assert caught.value.external_ids == ("TRN-881", "TRN-999")
        assert caught.value.recorded == (_OCT_8, Decimal(_COFFEE))
        assert "delete every import that shows it" in str(caught.value)
        assert db.session.query(StatementLineSighting).count() == sightings
        assert _lines(db) == [(_OCT_8, Decimal(_COFFEE))]
        assert _ids(db) == [("TRN-881", _OCT_8), ("TRN-999", _OCT_8)]
