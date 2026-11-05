"""The sighting relation's rules: pairing within a source, and the line's reads.

Plan step ``bank_import:X-f6b-1``, ruling **R-BI10**.  A line is the bank's
fact; what each import said about it is that import's sighting; the record
door pairs an incoming line to a recorded one WITHIN A SOURCE -- by the
source's own id, then by its wording, then by count against the lines only
other sources showed -- and what is left is new.  These cases grade that rule
step by step, the refusal's reach after it, the door's one-id-one-line
refusal, and the two order claims the line's reads make (LATEST sighting for
the wording, EARLIEST stated day for the transaction day) with a two-element
case where id order and the claimed order disagree.

**The id step and the cross-source step are reached through
:func:`~app.services.statement_import._plan._pair_group` directly**, the
allowance ``test_anchor`` takes for ``resting_on``: the one adapter that
exists carries no ids and there is one source, so no file can put either
shape through the door.  The rows are still real -- built through the ORM,
sightings and imports loaded the way the door loads them -- so the pairing is
graded over the eager collection and the act order it actually reads.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatementSourceEnum
from app.exceptions import (
    StatementLineConflict,
    StatementLineIdMoved,
    StatementParseError,
)
from app.extensions import db as _db
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from app.services.statement_import import (
    StatementLine,
    recent_lines,
    record_statement,
)
# Pylint: protected-access -- the pairing step and the id refusal are private
# collaborations of the record door with no importer outside the package; a
# test of the RULE reaches into them, the allowance ``test_anchor`` takes for
# ``resting_on``.
from app.services.statement_import._plan import (  # pylint: disable=protected-access
    _held_ids,
    _pair_group,
    _refuse_moved_ids,
    _refuse_repeated_ids,
)
from tests._test_helpers import capture_sql_statements
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_sighting,
    an_import,
)

from . import _csv_builder as build

_SECU = StatementSourceEnum.SECU_CHECKING_CSV
_DAY = date(2026, 3, 2)


def _incoming(description, *, external_id=None, day=_DAY, amount="-25.00"):
    """One line as a source states it."""
    return StatementLine(
        posted_on=day, transaction_on=None, amount=Decimal(amount),
        description=description, external_id=external_id,
    )


def _another_source(db):
    """Seed and return the id of a SECOND adapter in ``ref.statement_sources``.

    One source ships today; the cross-source cases need a second, and what
    the pairing reads is the import's ``source_id`` alone, so a catalogue row
    with no parser behind it is exactly enough.
    """
    return db.session.execute(text(
        "INSERT INTO ref.statement_sources (name, display_name) "
        "VALUES ('test_other_source', 'Another adapter') RETURNING id"
    )).scalar()


def _an_import_from(seed_user, source_id, *, created_at=None):
    """One import of the seeded account from *source_id*."""
    statement = StatementImport(
        account_id=seed_user["account"].id,
        user_id=seed_user["user"].id,
        source_id=source_id,
        file_name="other.dat",
        file_digest="d" * 64,
        declared_start=date(2026, 1, 1),
        declared_end=date(2026, 12, 31),
    )
    if created_at is not None:
        statement.created_at = created_at
    _db.session.add(statement)
    _db.session.flush()
    return statement


def _recorded(db, seed_user):
    """Return the seeded account's lines in ordinal order, sightings loaded."""
    db.session.expire_all()
    return (
        db.session.query(BankStatementLine)
        .filter(BankStatementLine.account_id == seed_user["account"].id)
        .order_by(BankStatementLine.sequence_in_group)
        .all()
    )


class TestThePairingWithinASource:
    """:func:`_pair_group`'s four steps, and the refusal's reach after them."""

    def test_an_id_pairs_FIRST_even_where_the_wording_differs(
        self, app, db, seed_user,
    ):
        """Step 1: this source's own id breaks the tie before any wording compare."""
        secu = ref_cache.statement_source_id(_SECU)
        statement = an_import(seed_user)
        held = a_bank_line(
            seed_user, statement, posted_on=_DAY, amount="-25.00",
            description="COFFEE", sequence_in_group=0,
        )
        [sighting] = held.sightings
        sighting.external_id = "FIT-1"
        db.session.flush()

        pairing = _pair_group(
            [_incoming("REWORDED BY THE BANK", external_id="FIT-1")],
            _recorded(db, seed_user), secu,
        )

        assert pairing.held == [(0, 0)]
        assert pairing.fresh == []
        assert pairing.restated is None

    def test_wording_pairs_within_this_source_by_its_LATEST_sighting(
        self, app, db, seed_user,
    ):
        """Step 2: the wording compared is what THIS source said most recently.

        Two same-source sightings of one line with two wordings (an id
        pairing allowed the second); a third file stating the LATER wording
        pairs, and one stating the earlier does not.
        """
        secu = ref_cache.statement_source_id(_SECU)
        earlier = an_import(
            seed_user,
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        later = an_import(
            seed_user,
            created_at=datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc),
        )
        line = a_bank_line(
            seed_user, earlier, posted_on=_DAY, amount="-25.00",
            description="OLD WORDING", sequence_in_group=0,
        )
        a_sighting(seed_user, later, line, description="NEW WORDING")
        recorded = _recorded(db, seed_user)

        assert _pair_group([_incoming("NEW WORDING")], recorded, secu).held == [
            (0, 0),
        ]
        stale = _pair_group([_incoming("OLD WORDING")], recorded, secu)
        assert stale.fresh == [0]
        assert stale.restated == "NEW WORDING"

    def test_a_line_only_ANOTHER_source_showed_pairs_by_COUNT(
        self, app, db, seed_user,
    ):
        """Step 3: a different wording across sources is never a restatement.

        The other source recorded two same-day same-amount lines; this
        source's file states two with its own words.  They pair in ordinal
        order, nothing is fresh, and nothing is refused -- which is finding
        **N-303** closed at the root.
        """
        secu = ref_cache.statement_source_id(_SECU)
        other = _an_import_from(seed_user, _another_source(db))
        first = a_bank_line(
            seed_user, other, posted_on=_DAY, amount="-25.00",
            description="THEIR FIRST", sequence_in_group=0,
        )
        second = a_bank_line(
            seed_user, other, posted_on=_DAY, amount="-25.00",
            description="THEIR SECOND", sequence_in_group=1,
        )

        pairing = _pair_group(
            [_incoming("OUR ONE"), _incoming("OUR TWO")],
            _recorded(db, seed_user), secu,
        )

        assert pairing.held == [(0, 0), (1, 1)]
        assert pairing.fresh == []
        assert pairing.restated is None
        assert first.sequence_in_group < second.sequence_in_group

    def test_what_nothing_accounts_for_is_FRESH(self, app, db, seed_user):
        """Step 4, with the count step exhausted first."""
        secu = ref_cache.statement_source_id(_SECU)
        other = _an_import_from(seed_user, _another_source(db))
        a_bank_line(
            seed_user, other, posted_on=_DAY, amount="-25.00",
            description="THEIRS", sequence_in_group=0,
        )

        pairing = _pair_group(
            [_incoming("OURS"), _incoming("OURS AGAIN")],
            _recorded(db, seed_user), secu,
        )

        assert pairing.held == [(0, 0)]
        assert pairing.fresh == [1]
        assert pairing.restated is None

    def test_the_refusal_keeps_its_reach_within_a_source(
        self, app, db, seed_user,
    ):
        """A same-source member the file no longer states, beside a fresh one."""
        secu = ref_cache.statement_source_id(_SECU)
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, posted_on=_DAY, amount="-25.00",
            description="COFFEE", sequence_in_group=0,
        )

        pairing = _pair_group(
            [_incoming("SOMETHING ELSE")], _recorded(db, seed_user), secu,
        )

        assert pairing.fresh == [0]
        assert pairing.restated == "COFFEE"

    def test_a_member_ANOTHER_source_holds_is_never_a_restatement(
        self, app, db, seed_user,
    ):
        """The refusal does not reach across sources.

        The other source holds two lines; this file states three.  Two pair
        by count, the third is fresh, and the other source's members are
        not this file's to restate -- so nothing is refused.
        """
        secu = ref_cache.statement_source_id(_SECU)
        other = _an_import_from(seed_user, _another_source(db))
        for ordinal, wording in enumerate(("THEIRS 1", "THEIRS 2")):
            a_bank_line(
                seed_user, other, posted_on=_DAY, amount="-25.00",
                description=wording, sequence_in_group=ordinal,
            )

        pairing = _pair_group(
            [_incoming("A"), _incoming("B"), _incoming("C")],
            _recorded(db, seed_user), secu,
        )

        assert pairing.held == [(0, 0), (1, 1)]
        assert pairing.fresh == [2]
        assert pairing.restated is None


class TestASourceNamesOneLinePerId:
    """:func:`_held_ids` and :func:`_refuse_moved_ids` -- the door's rule."""

    def test_a_held_id_on_ANOTHER_day_is_refused(self, app, db, seed_user):
        """The shape ``uq_bank_statement_lines_external_id`` refused as a 500."""
        secu = ref_cache.statement_source_id(_SECU)
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, posted_on=_DAY, amount="-25.00",
            description="COFFEE", sequence_in_group=0,
        )
        [sighting] = line.sightings
        sighting.external_id = "FIT-1"
        db.session.flush()
        moved = [_incoming("COFFEE", external_id="FIT-1", day=date(2026, 3, 9))]

        held = _held_ids(seed_user["account"].id, secu, moved)
        assert held == {"FIT-1": (_DAY, Decimal("-25.00"))}
        with pytest.raises(StatementLineIdMoved) as caught:
            _refuse_moved_ids(moved, held)

        assert caught.value.recorded == (_DAY, Decimal("-25.00"))
        assert caught.value.stated == (date(2026, 3, 9), Decimal("-25.00"))

    def test_the_same_id_on_the_same_line_is_accepted(
        self, app, db, seed_user,
    ):
        """A re-import re-sighting a line under its id is the ordinary case."""
        secu = ref_cache.statement_source_id(_SECU)
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, posted_on=_DAY, amount="-25.00",
            description="COFFEE", sequence_in_group=0,
        )
        [sighting] = line.sightings
        sighting.external_id = "FIT-1"
        db.session.flush()
        again = [_incoming("COFFEE", external_id="FIT-1")]

        _refuse_moved_ids(again, _held_ids(seed_user["account"].id, secu, again))

    def test_ANOTHER_sources_id_is_not_this_sources(self, app, db, seed_user):
        """Ids are per source; the same string from two adapters is two ids."""
        secu = ref_cache.statement_source_id(_SECU)
        other = _an_import_from(seed_user, _another_source(db))
        line = a_bank_line(
            seed_user, other, posted_on=_DAY, amount="-25.00",
            description="THEIRS", sequence_in_group=0,
        )
        [sighting] = line.sightings
        sighting.external_id = "FIT-1"
        db.session.flush()
        ours = [_incoming("OURS", external_id="FIT-1", day=date(2026, 3, 9))]

        assert _held_ids(seed_user["account"].id, secu, ours) == {}

    def test_a_file_stating_one_id_TWICE_is_refused(self, app, db, seed_user):
        """The shape the retired unique index caught, at the door now.

        Without this the second line would be minted fresh beside the first
        under the same id, and the next import's lookup would hold the id on
        whichever row came back last.  Found by adversarial review
        2026-09-18.
        """
        twice = [
            _incoming("COFFEE", external_id="FIT-1"),
            _incoming("TEA", external_id="FIT-1", day=date(2026, 3, 9)),
        ]

        with pytest.raises(StatementParseError, match="'FIT-1' on two lines"):
            _refuse_repeated_ids(twice)
        assert seed_user["account"].id

    def test_a_file_with_no_ids_asks_nothing(self, app, db, seed_user):
        """SECU's CSV carries none; the lookup issues no statement."""
        secu = ref_cache.statement_source_id(_SECU)

        assert _held_ids(
            seed_user["account"].id, secu, [_incoming("COFFEE")],
        ) == {}


class TestTheDoorEndToEnd:
    """Through ``record_statement`` with real CSV bytes, one source."""

    def test_a_same_source_restatement_is_still_refused(
        self, app, db, seed_user,
    ):
        """Ruling **R-FL** keeps its reach through the relation."""
        entries = [(_DAY, "-25.00", "COFFEE")]
        record_statement(
            account_id=seed_user["account"].id, user_id=seed_user["user"].id,
            source=_SECU, file_name="first.csv",
            payload=build.build(build.chained("100.00", entries)),
        )

        with pytest.raises(StatementLineConflict):
            record_statement(
                account_id=seed_user["account"].id,
                user_id=seed_user["user"].id,
                source=_SECU, file_name="restated.csv",
                payload=build.build(build.chained(
                    "100.00", [(_DAY, "-25.00", "TEA")],
                )),
            )

        assert db.session.query(StatementLineSighting).count() == 1

    def test_a_re_import_records_a_sighting_per_line_and_no_line(
        self, app, db, seed_user,
    ):
        """What "re-importing cannot duplicate" looks like under the relation."""
        entries = [(_DAY, "-25.00", "COFFEE"), (date(2026, 3, 3), "50.00", "PAY")]
        payload = build.build(build.chained("100.00", entries))
        first = record_statement(
            account_id=seed_user["account"].id, user_id=seed_user["user"].id,
            source=_SECU, file_name="first.csv", payload=payload,
        )
        again = record_statement(
            account_id=seed_user["account"].id, user_id=seed_user["user"].id,
            source=_SECU, file_name="again.csv", payload=payload,
        )

        assert (first.line_count, first.recorded_count) == (2, 2)
        assert (again.line_count, again.recorded_count) == (2, 0)
        assert db.session.query(BankStatementLine).count() == 2
        assert db.session.query(StatementLineSighting).count() == 4


class TestWhatALineReadsOffItsSightings:
    """The two order claims, each with a case where id order disagrees."""

    def test_the_wording_is_the_LATEST_imports_by_act_order_not_by_id(
        self, app, db, seed_user,
    ):
        """The later-run import's sighting is written FIRST (lower id)."""
        later = an_import(
            seed_user,
            created_at=datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc),
        )
        earlier = an_import(
            seed_user,
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        line = a_bank_line(
            seed_user, later, posted_on=_DAY, amount="-25.00",
            description="WHAT THE BANK CALLS IT NOW", sequence_in_group=0,
            source_category="Later/Category",
        )
        old = a_sighting(
            seed_user, earlier, line, description="WHAT IT CALLED IT THEN",
            source_category="Earlier/Category", running_balance="1.00",
        )
        [new] = [s for s in line.sightings if s.id != old.id]
        assert new.id < old.id

        db.session.expire_all()
        read = db.session.get(BankStatementLine, line.id)
        assert read.current.id == new.id
        assert read.description == "WHAT THE BANK CALLS IT NOW"
        assert read.source_category == "Later/Category"
        assert read.running_balance is None

    def test_the_transaction_day_is_the_EARLIEST_any_sighting_states(
        self, app, db, seed_user,
    ):
        """Not the latest sighting's, and not the first written."""
        first = an_import(seed_user)
        second = an_import(seed_user)
        line = a_bank_line(
            seed_user, first, posted_on=_DAY, amount="-25.00",
            description="X", sequence_in_group=0,
            transaction_on=date(2026, 3, 1),
        )
        a_sighting(seed_user, second, line, description="X", transaction_on=None)
        third = an_import(seed_user)
        a_sighting(
            seed_user, third, line, description="X",
            transaction_on=date(2026, 2, 27),
        )

        db.session.expire_all()
        assert db.session.get(BankStatementLine, line.id).transaction_on == (
            date(2026, 2, 27)
        )

    def test_reading_every_lines_facts_is_ONE_statement(
        self, app, db, seed_user,
    ):
        """The eagerness, graded: sightings and their imports ride the line's query.

        The property ``test_reading_every_lines_merchant_is_ONE_statement``
        grades for the merchant, extended to the relation the wording now
        lives on: a lazy load here is one statement per line on every list
        surface (finding **N-309**'s class).
        """
        statement = an_import(seed_user)
        again = an_import(seed_user)
        for ordinal in range(3):
            line = a_bank_line(
                seed_user, statement, posted_on=_DAY, amount="-25.00",
                description=f"LINE {ordinal}", sequence_in_group=ordinal,
            )
            a_sighting(seed_user, again, line, description=f"LINE {ordinal} AGAIN")
        db.session.flush()
        db.session.expire_all()

        def _read():
            return sorted(
                (line.description, line.transaction_on)
                for line in db.session.query(BankStatementLine).all()
            )

        facts, statements = capture_sql_statements(_read)

        assert facts == [
            ("LINE 0 AGAIN", None), ("LINE 1 AGAIN", None),
            ("LINE 2 AGAIN", None),
        ]
        assert len(statements) == 1

    def test_a_bounded_list_counts_LINES_not_joined_rows(
        self, app, db, seed_user,
    ):
        """``LIMIT`` over a joined collection bounds the parents.

        Three lines, each sighted twice; a limit of 2 must return two LINES,
        not the first two joined rows -- which would be one line twice.
        """
        statement = an_import(seed_user)
        again = an_import(seed_user)
        for ordinal in range(3):
            line = a_bank_line(
                seed_user, statement, posted_on=_DAY, amount="-25.00",
                description=f"LINE {ordinal}", sequence_in_group=ordinal,
            )
            a_sighting(seed_user, again, line, description=f"LINE {ordinal} AGAIN")
        db.session.flush()
        db.session.expire_all()

        listed = recent_lines(seed_user["account"].id, limit=2)

        assert len(listed) == 2
        assert len({line.id for line in listed}) == 2
        assert all(len(line.sightings) == 2 for line in listed)
