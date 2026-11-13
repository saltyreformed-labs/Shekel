"""Re-importing cannot duplicate, and a later export's additions are absorbed."""

from datetime import date
from decimal import Decimal

from app.models.merchant import Merchant
from app.models.statement_import import BankStatementLine, StatementImport, StatementLineSighting

from tests.test_services.test_statement_import import _csv_builder as build

from ._helpers import _ENTRIES, _file, _counts, _record


class TestItIsIdempotent:
    """R-FP's "re-importing a file cannot duplicate", as a property."""

    def test_the_same_file_twice_records_no_second_line(
        self, app, db, seed_user,
    ):
        """The second import adds nothing and says so."""
        _record(seed_user, _file())

        second = _record(seed_user, _file(), file_name="again.csv")

        assert second.recorded_count == 0
        assert second.already_known == 3
        assert db.session.query(BankStatementLine).count() == 3

    def test_the_second_import_is_still_RECORDED_as_an_act(
        self, app, db, seed_user,
    ):
        """Doing nothing is a fact worth keeping: it is the proof you checked."""
        _record(seed_user, _file())
        _record(seed_user, _file(), file_name="again.csv")

        assert db.session.query(StatementImport).count() == 2

    def test_an_overlapping_span_records_only_the_new_lines(
        self, app, db, seed_user,
    ):
        """The real shape: each export covers year-to-date, so every import
        after the first overlaps everything before it."""
        _record(seed_user, _file())

        extended = _ENTRIES + [
            (date(2026, 3, 5), "-12.79", "POINT OF SALE DEBIT L340 BJS"),
        ]
        second = _record(seed_user, _file(extended), file_name="later.csv")

        assert second.recorded_count == 1
        assert second.already_known == 3
        assert db.session.query(BankStatementLine).count() == 4

    def test_a_line_keeps_the_import_that_FIRST_recorded_it(
        self, app, db, seed_user,
    ):
        """Provenance ACCUMULATES: the second import sights every line.

        Under the sighting relation (plan step ``bank_import:X-f6b-1``) a
        re-import records what it saw beside what the first import saw; which
        import was FIRST is a derivation over the two, and it still names the
        first.
        """
        first = _record(seed_user, _file())
        second = _record(seed_user, _file(), file_name="again.csv")

        for row in db.session.query(BankStatementLine).all():
            assert {sighting.import_id for sighting in row.sightings} == {
                first.import_id, second.import_id,
            }
        first_by = db.session.execute(
            StatementLineSighting.first_import_of_each_line(
                seed_user["account"].id,
            ).select(),
        ).all()
        assert len(first_by) == 3
        assert {import_id for _line, import_id in first_by} == {
            first.import_id,
        }
        assert _counts(db, seed_user["account"].id) == {
            first.import_id: (3, 3), second.import_id: (3, 0),
        }

    def test_two_identical_charges_on_one_day_are_both_recorded(
        self, app, db, seed_user,
    ):
        """The ordinal's whole purpose, end to end.

        Without it the second charge is read as a duplicate of the first and
        never recorded -- money the bank took that the app would never see.
        """
        entries = [
            (date(2026, 3, 2), "-4.75", "COFFEE"),
            (date(2026, 3, 2), "-4.75", "COFFEE"),
        ]

        outcome = _record(seed_user, _file(entries))

        assert outcome.recorded_count == 2
        assert db.session.query(BankStatementLine).count() == 2

    def test_those_two_charges_survive_a_re_import_without_duplicating(
        self, app, db, seed_user,
    ):
        """Both halves at once: the ordinal must be STABLE as well as total."""
        entries = [
            (date(2026, 3, 2), "-4.75", "COFFEE"),
            (date(2026, 3, 2), "-4.75", "COFFEE"),
        ]
        _record(seed_user, _file(entries))

        second = _record(seed_user, _file(entries), file_name="again.csv")

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).count() == 2


class TestItAbsorbsWhatALaterExportAdds:
    """Information GAINED is not a restatement, and it must not be discarded."""

    def test_a_transaction_day_arriving_later_is_recorded(
        self, app, db, seed_user,
    ):
        """The arm that MOVES A DATE, not just a display figure.

        A line recorded by an adapter that could not read the bank's stated
        transaction day carries NULL, and a match writes that day onto a
        matched purchase's ``purchased_on`` (ruling **R-FW**).  Without this
        arm a re-import of the very same file left the column NULL forever --
        the running-balance defect above, on a column that feeds a date write.
        Found by adversarial financial review 2026-08-18, which measured the
        re-import leaving it untouched.
        """
        stated = date(2026, 3, 1)
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 DATE 03-01 COFFEE")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        recorded = db.session.query(BankStatementLine).one()
        assert recorded.transaction_on == stated
        # Stand in for a SIGHTING an older adapter wrote, which knew no such
        # day (plan step ``bank_import:X-f6b-1``: the day is the sighting's).
        [older] = recorded.sightings
        older.transaction_on = None
        db.session.flush()
        db.session.expire_all()
        assert db.session.query(BankStatementLine).one().transaction_on is None

        second = _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")

        assert second.recorded_count == 0
        db.session.expire_all()
        assert db.session.query(BankStatementLine).one().transaction_on == stated

    def test_a_DISAGREEING_transaction_day_is_left_alone(
        self, app, db, seed_user,
    ):
        """Two sightings state two days, and the line reads the EARLIEST.

        A stated day is an observation, not a draft: the later sighting
        overwrites nothing (it is its own row), and the day a match writes
        onto a purchase is the earliest any source states -- money cannot be
        spent after the earliest day a source says it was.  THE FIRING
        CONTROL for ``BankStatementLine.transaction_on``'s ``min``: read the
        LATEST sighting's day instead and this fails.
        """
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 DATE 03-01 COFFEE")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        recorded = db.session.query(BankStatementLine).one()
        [older] = recorded.sightings
        older.transaction_on = date(2026, 2, 27)
        db.session.flush()

        _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")

        db.session.expire_all()
        line = db.session.query(BankStatementLine).one()
        assert sorted(
            sighting.transaction_on for sighting in line.sightings
        ) == [date(2026, 2, 27), date(2026, 3, 1)]
        assert line.transaction_on == date(2026, 2, 27)

    def test_a_LATER_sighting_stating_an_EARLIER_day_moves_the_line(
        self, app, db, seed_user,
    ):
        """The other direction of the same rule, so "earliest" is not "first".

        A first sighting states 03-01; the re-import's sighting states
        02-27.  Reading the FIRST sighting's day would leave 03-01; reading
        the earliest across both gives 02-27.
        """
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 DATE 03-01 COFFEE")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        second = _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")
        db.session.expire_all()
        line = db.session.query(BankStatementLine).one()
        [later] = [
            sighting for sighting in line.sightings
            if sighting.import_id == second.import_id
        ]
        later.transaction_on = date(2026, 2, 27)
        db.session.flush()
        db.session.expire_all()

        assert db.session.query(
            BankStatementLine,
        ).one().transaction_on == date(2026, 2, 27)

    def test_a_running_balance_arriving_later_is_recorded(
        self, app, db, seed_user,
    ):
        """The path the developer actually took, and it lost the data.

        The running-balance column is an export OPTION.  The 10-column file is
        what SECU gives you by default; the page then tells you to re-export
        with the column, and before this the second import recognised every
        line as already known and threw the balances away -- leaving NULL
        forever on the very fact the CSV was chosen over the OFX to obtain.
        """
        without = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))
        _record(seed_user, without, file_name="plain.csv")
        assert all(
            row.running_balance is None
            for row in db.session.query(BankStatementLine).all()
        )

        second = _record(seed_user, _file(), file_name="with-balance.csv")

        assert second.recorded_count == 0
        assert sorted(
            Decimal(str(row.running_balance))
            for row in db.session.query(BankStatementLine).all()
        ) == [Decimal("75.00"), Decimal("1534.19"), Decimal("1575.00")]

    def test_a_category_arriving_later_is_recorded(
        self, app, db, seed_user,
    ):
        """The same rule on the other optional fact."""
        bare = [build.row(date(2026, 3, 2), "-25.00", "COFFEE",
                          running="75.00")]
        _record(seed_user, build.build(bare))

        labelled = [build.row(date(2026, 3, 2), "-25.00", "COFFEE",
                              category="Food/Coffee", running="75.00")]
        _record(seed_user, build.build(labelled), file_name="second.csv")

        assert db.session.query(BankStatementLine).one().source_category == (
            "Food/Coffee"
        )


    def test_a_merchant_arriving_later_is_recorded(
        self, app, db, seed_user,
    ):
        """The arm a destination POLICY keys on (plan step X-f6a-3d).

        A line whose merchant is NULL joins no policy, so a row recorded by an
        adapter that could not name one would go on being offered a bare
        chooser forever -- even after an export that DOES name one had been
        imported over it.  Same rule, same direction, as the transaction-day
        arm above; the consequence here is a decision the owner has to make
        again rather than a date left wrong.  **Since plan step
        ``bank_import:X-f6b-1b`` (ruling R-BI16) the second import writes
        the key onto ITS OWN sighting and nothing onto the line or the first
        sighting**: the line's merchant is the earliest sighting that names
        one, which is now the second's.
        """
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        recorded = db.session.query(BankStatementLine).one()
        assert recorded.merchant_name == "Big Cheese Clayton"
        # Stand in for a sighting an older adapter wrote, which named no
        # merchant.  The MERCHANT ROW stays: it outlives the sightings that
        # named it, which is what makes the second import's sighting a
        # re-point rather than a create (plan step ``bank_import:X-gd-1``).
        [first] = recorded.sightings
        first.merchant_id = None
        db.session.flush()
        db.session.expire_all()
        assert db.session.query(BankStatementLine).one().merchant_id is None

        second = _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")

        assert second.recorded_count == 0
        db.session.expire_all()
        line = db.session.query(BankStatementLine).one()
        assert line.merchant_name == "Big Cheese Clayton"
        # The first sighting still names none: the fact is the second's.
        assert sorted(
            (sighting.import_id, sighting.merchant_id)
            for sighting in line.sightings
        ) == [(first.import_id, None), (second.import_id, line.merchant_id)]

    def test_a_DISAGREEING_merchant_is_left_alone(
        self, app, db, seed_user,
    ):
        """The line's merchant is the EARLIEST naming sighting's, whatever comes later.

        A re-export naming a different word records that word's key on its
        own sighting and moves nothing: overwriting would silently re-point
        every destination policy the owner had stated against the old name,
        which is a decision moving without anyone deciding it (ruling
        **R-BI16**).  THE FIRING CONTROL for the producer's ORDER: flip
        ``_stated_by_the_naming_sighting`` to newest-first and this fails.
        """
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        recorded = db.session.query(BankStatementLine).one()
        renamed = Merchant(account_id=recorded.account_id, name="Cheese Shop")
        db.session.add(renamed)
        db.session.flush()
        # Stand in for a first sighting that named a different word.
        [first] = recorded.sightings
        first.merchant_id = renamed.id
        db.session.flush()

        _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")

        # **Read back from the DATABASE, not from the instance this test just
        # re-pointed.**  The line's merchant projections are loaded with the
        # row, so the in-session object would still answer what it was loaded
        # with, whatever the door wrote.  What the next request sees is what
        # is persisted.
        db.session.expire_all()
        line = db.session.query(BankStatementLine).one()
        assert line.merchant_name == "Cheese Shop"
        assert line.merchant_id == renamed.id
        # The re-import's own sighting carries ITS word's key, untouched by
        # the line's answer.
        later = max(line.sightings, key=lambda sighting: sighting.import_id)
        assert later.merchant_id != renamed.id
        assert db.session.get(Merchant, later.merchant_id).name == (
            "Big Cheese Clayton"
        )
