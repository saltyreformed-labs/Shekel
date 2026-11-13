"""The ordinary import: what it records, that it moves no money, and its readable decision."""

import hashlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import StatementBalanceEvidenceEnum
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.transaction import Transaction
from app.services.statement_import import StatementBalanceUnexplained, plan_statement
from app.services.statement_import._balance import bank_levels
from app.services.ledger_lines import linked_key
from app.services.row_valuation import settled_figure

from tests._ledger_reads import nets_by_key_and_day
from tests._test_helpers import create_settled_cash_transaction

from tests.test_services.test_statement_import import _csv_builder as build

from ._helpers import (
    _SOURCE,
    _ENTRIES,
    _MERCHANTS,
    _file,
    _counts,
    _audit_watermark,
    _audited_writes_since,
    _record,
)


class TestItRecordsWhatTheBankSaid:
    """The ordinary import."""

    def test_it_writes_one_line_per_statement_line(self, app, db, seed_user):
        """Three lines in the file, three rows in the database."""
        outcome = _record(seed_user, _file())

        assert outcome.recorded_count == 3
        assert db.session.query(BankStatementLine).count() == 3

    def test_it_records_the_merchant_the_bank_NAMED(
        self, app, db, seed_user,
    ):
        """The column a destination policy is keyed by, written by the adapter.

        Plan step X-f6a-3d.  What makes this worth its own case at THIS tier is
        that the adapter's answer has to reach the row: the parse is graded in
        ``test_secu_csv``, and this is the only place that says the value
        travels from the parse to the column rather than being dropped by the
        staging call.
        """
        _record(seed_user, _file())

        rows = db.session.query(BankStatementLine).order_by(
            BankStatementLine.posted_on,
        ).all()
        assert [row.merchant_name for row in rows] == _MERCHANTS

    def test_it_records_the_banks_posted_day(self, app, db, seed_user):
        """The whole point of the arc: the day the BANK says, not the app."""
        _record(seed_user, _file())

        days = sorted(
            row.posted_on
            for row in db.session.query(BankStatementLine).all()
        )
        assert days == [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]

    def test_it_records_the_signed_amounts(self, app, db, seed_user):
        """Positive INTO the account, the sign ``cash_ledger.movement_cash_leg`` uses."""
        _record(seed_user, _file())

        amounts = sorted(
            Decimal(str(row.amount))
            for row in db.session.query(BankStatementLine).all()
        )
        assert amounts == [
            Decimal("-40.81"), Decimal("-25.00"), Decimal("1500.00"),
        ]

    def test_it_records_the_import_act_with_its_counts(
        self, app, db, seed_user,
    ):
        """Provenance: who, when, which file, and how much was new."""
        _record(seed_user, _file(), file_name="march.csv")

        row = db.session.query(StatementImport).one()
        assert row.file_name == "march.csv"
        # The counts are DERIVED from the sightings (ruling **R-IY**): three
        # sighted, three of them first sighted by this import.
        assert _counts(db, row.account_id) == {row.id: (3, 3)}
        assert row.declared_start == date(2026, 3, 2)
        assert row.declared_end == date(2026, 3, 4)
        assert row.user_id == seed_user["user"].id

    def test_a_chained_file_is_PROVED_by_itself_and_the_ROW_says_so(
        self, app, db, seed_user,
    ):
        """The stored LEVEL, not just the value the door returned.

        **The row itself had no assertion for an ANCHORED import**, so forcing
        the placed day to ``period_end`` or the evidence to a constant left
        the whole suite green -- the column this step exists to add could
        have stored the wrong thing forever.  Found by adversarial review
        2026-08-23.  Since plan step ``balance:X-bj-1`` the placement is a
        row in the level relation naming the import, its amount the claim,
        so that is what is read.

        The fixture chains from `$100.00` through -25.00, +1500.00 and -40.81,
        so it closes at `$1,534.19` on 03-04 and its header states exactly
        that.
        """
        outcome = _record(seed_user, _file())

        row = db.session.query(StatementImport).one()
        assert row.stated_balance == Decimal("1534.19")
        [(level, release)] = bank_levels(seed_user["account"].id)
        assert level.statement_import_id == row.id
        assert level.anchor_balance == Decimal("1534.19")
        assert level.observed_on == date(2026, 3, 4)
        assert ref_cache.statement_balance_evidence_member(
            level.evidence_id
        ) is StatementBalanceEvidenceEnum.FILE_CHAIN
        assert release is None
        # The receipt and the row say ONE thing.
        assert outcome.balance.effective_on == level.observed_on
        assert outcome.balance.evidence is StatementBalanceEvidenceEnum.FILE_CHAIN

    def test_the_stored_day_is_NOT_the_day_the_header_names(
        self, app, db, seed_user,
    ):
        """The 2026-08-16 LAG shape, end to end through the door.

        **Measured on the developer's own export**: its header reads
        ``$4,747.63`` as of 08-16 over a file listing two 08-14 lines worth
        ``-$1,006.72``, and the figure is 08-13's closing.  Until this test the
        shape reached only a pure unit -- no door-level case ever produced an
        ``effective_on`` DIFFERENT from ``period_end``, which is why mutating
        the stored day to ``period_end`` survived.  Found by adversarial review
        2026-08-23.

        Here the chain closes at `$1,534.19` on 03-04 and the header states
        `$1,559.19` -- the 03-03 cumulative -- as of 03-09, so the figure is
        placed at 03-03 while the row's span ends 03-04.
        """
        _record(
            seed_user,
            build.build(build.chained("100.00", _ENTRIES),
                        balance_as_of="03/09/2026",
                        stated_balance="1575.00"),
        )

        row = db.session.query(StatementImport).one()
        assert row.declared_end == date(2026, 3, 4)
        assert row.stated_balance_on == date(2026, 3, 9)
        [(level, _release)] = bank_levels(seed_user["account"].id)
        assert level.statement_import_id == row.id
        assert level.observed_on == date(2026, 3, 3)

    def test_a_header_the_files_lines_CANNOT_REACH_records_no_anchor(
        self, app, db, seed_user,
    ):
        """A date-range export states TODAY's balance, not the range's closing.

        **Measured on the developer's own file**: he exported
        2026-01-02..2026-03-31 on 2026-08-23 and its header reads
        ``Balance as of 08/23/2026,2459.600000`` -- 145 days past its last line
        and `$255.41` from the `$2,715.01` its own 139 lines imply.  The
        movements explaining that difference are simply not in the file, so no
        day inside it can place the figure and refusing it would reject an
        honest export.  The CLAIM is recorded; the anchor is not.

        A PRIOR import supplies the opening, because "cannot be placed" is a
        statement about a known opening the figure fails to reconcile with --
        and it OVERLAPS, as a real consecutive export does, or the walk stops
        at the uncovered day between them and falls back to taking the figure
        at face value.
        """
        _record(seed_user, _file())

        _record(
            seed_user,
            build.build(
                build.chained(
                    "0.00",
                    [_ENTRIES[2],
                     (date(2026, 3, 6), "-10.00",
                      "POINT OF SALE DEBIT L340 FUEL")],
                    with_running=False,
                ),
                balance_as_of="08/16/2026", stated_balance="2501.31",
            ),
            file_name="range.csv",
        )

        row = (
            db.session.query(StatementImport)
            .filter_by(file_name="range.csv").one()
        )
        assert row.stated_balance == Decimal("2501.31")
        assert row.stated_balance_on == date(2026, 8, 16)
        # The claim is recorded and NO level names the import.
        assert [
            level.statement_import_id
            for level, _release in bank_levels(seed_user["account"].id)
        ] == [
            one.id for one in db.session.query(StatementImport)
            .filter(StatementImport.file_name != "range.csv")
        ]

    def test_a_chained_file_CONTRADICTING_itself_is_REFUSED(
        self, app, db, seed_user,
    ):
        """The only refusal: the file's own chain against its own header."""
        with pytest.raises(StatementBalanceUnexplained) as raised:
            _record(
                seed_user,
                build.build(build.chained("100.00", _ENTRIES),
                            balance_as_of="03/09/2026",
                            stated_balance="9999.99"),
            )

        assert raised.value.stated == Decimal("9999.99")
        assert raised.value.implied == Decimal("1534.19")
        assert db.session.query(StatementImport).count() == 0

    def test_recording_a_line_RELEASES_an_anchor_it_undercuts(
        self, app, db, seed_user,
    ):
        """The door's half of the release, through the door.

        A second export inserting a line into a day the first anchor had
        already priced means that anchor was solved without it.  Reproduced as
        a stored day two days early under a *corroborated* badge before the
        release existed.
        """
        _record(seed_user, _file())
        first = db.session.query(StatementImport).one()
        [(level, release)] = bank_levels(seed_user["account"].id)
        assert level.statement_import_id == first.id
        assert level.observed_on == date(2026, 3, 4)
        assert release is None

        # A later export the bank has INSERTED a line into, on a day the first
        # anchor already covers.
        later = _record(
            seed_user,
            build.build(build.chained(
                "100.00",
                _ENTRIES[:2] + [
                    (date(2026, 3, 3), "-5.00", "POINT OF SALE DEBIT L340 X"),
                    _ENTRIES[2],
                ],
            )),
            file_name="inserted.csv",
        )

        # The first level STANDS no more: an appended release names the
        # import that undercut it and the day whose lines changed, and the
        # level row itself is untouched (plan step ``balance:X-bj-1``).
        placements = {
            level.statement_import_id: (level, release)
            for level, release in bank_levels(seed_user["account"].id)
        }
        level, release = placements[first.id]
        assert level.observed_on == date(2026, 3, 4)
        assert release is not None
        assert release.released_by_import_id == later.import_id
        assert release.lines_changed_from == date(2026, 3, 3)
        assert placements[later.import_id][1] is None

    def test_the_outcome_COUNTS_the_placements_it_released(
        self, app, db, seed_user,
    ):
        """Plan step ``bank_import:X-gr``, finding **BI-488**.

        The door called the release and dropped its return, so
        ``ImportOutcome`` carried no count and the receipt could say nothing
        about an import taking a checked balance away from an earlier one.
        The world is the case above's: one placement released.
        """
        _record(seed_user, _file())

        outcome = _record(
            seed_user,
            build.build(build.chained(
                "100.00",
                _ENTRIES[:2] + [
                    (date(2026, 3, 3), "-5.00", "POINT OF SALE DEBIT L340 X"),
                    _ENTRIES[2],
                ],
            )),
            file_name="inserted.csv",
        )

        assert outcome.anchors_released == 1

    def test_an_import_that_undercuts_nothing_counts_ZERO(
        self, app, db, seed_user,
    ):
        """A first import has nothing to undercut; a re-import adds no line.

        Both zeros are graded, because the second is the branch that skips
        the release altogether -- a re-import of a held span records no fresh
        line, and a line already recorded undercuts nothing.
        """
        first = _record(seed_user, _file())
        assert first.anchors_released == 0

        again = _record(seed_user, _file(), file_name="again.csv")

        assert again.recorded_count == 0
        assert again.anchors_released == 0

    def test_a_file_claiming_no_balance_records_NEITHER_column(
        self, app, db, seed_user,
    ):
        """Both-or-neither, which the database also refuses to break.

        ``ck_statement_imports_stated_balance_paired`` makes a half-written
        pair unstorable; this is the door agreeing with it.
        """
        payload = build.build(build.chained("100.00", _ENTRIES))
        without = b"\n".join(
            line for line in payload.split(b"\n")
            if not line.startswith(b"Balance as of")
        )

        _record(seed_user, without)

        row = db.session.query(StatementImport).one()
        assert row.stated_balance is None
        assert row.stated_balance_on is None

    def test_the_DATABASE_refuses_a_half_written_stated_balance(
        self, app, db, seed_user,
    ):
        """One fact in two columns, enforced where a future adapter cannot miss.

        The door writes both or neither today.  A second source adapter is one
        forgotten field from writing a figure with no day -- which asserts
        nothing about an account, and which the reader would then use to select
        an anchor "as of NULL".  Stated structurally so the guarantee does not
        rest on every adapter remembering.
        """
        _record(seed_user, _file())
        row = db.session.query(StatementImport).one()

        row.stated_balance = Decimal("2501.31")
        row.stated_balance_on = None

        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_every_line_belongs_to_its_import_and_its_account(
        self, app, db, seed_user,
    ):
        """The line's account, and ONE sighting per line naming the import."""
        _record(seed_user, _file())

        statement = db.session.query(StatementImport).one()
        for row in db.session.query(BankStatementLine).all():
            assert row.account_id == seed_user["account"].id
            [sighting] = row.sightings
            assert sighting.import_id == statement.id
            assert sighting.account_id == seed_user["account"].id

    def test_a_files_digest_is_the_digest_OF_THE_BYTES(
        self, app, db, seed_user,
    ):
        """So "is this the same file" is answerable where the name is not.

        Asserting the LENGTH, which is what this did first, passes for the
        digest of the empty string or of the file NAME -- neither of which
        answers the question the column exists for.
        """
        payload = _file()
        _record(seed_user, payload)

        assert db.session.query(StatementImport).one().file_digest == (
            hashlib.sha256(payload).hexdigest()
        )

    def test_two_different_files_get_different_digests(
        self, app, db, seed_user,
    ):
        """The other half: the digest has to DISCRIMINATE."""
        _record(seed_user, _file(), file_name="one.csv")
        _record(
            seed_user,
            _file(_ENTRIES + [(date(2026, 3, 5), "-9.99", "EXTRA")]),
            file_name="two.csv",
        )

        digests = {
            row.file_digest
            for row in db.session.query(StatementImport).all()
        }
        assert len(digests) == 2


class TestItMovesNoMoney:
    """The property that makes this leaf separable from the matching.

    **These were VACUOUS until an adversarial review measured them**: the
    ``seed_user`` fixture creates no ``Transaction`` at all, so the before /
    after snapshot compared ``[] == []`` and the clearing-link assertion
    counted zero rows in an empty table.  The guard the plan's separability
    argument rests on graded nothing.  They now settle a real transaction
    first, and snapshot the LEDGER as well as the rows -- because the balance
    is walked from the movements and assertions the ledger is worked out from,
    not from ``budget.transactions`` alone.
    """

    @pytest.fixture()
    def money_on_the_books(self, db, seed_user, seed_periods):
        """Settle a real transaction so there is something to move."""
        txn = create_settled_cash_transaction(
            seed_user, seed_periods[0], Decimal("125.00"),
            settled_on=date(2026, 3, 3), name="Groceries",
        )
        db.session.flush()
        return txn

    def _rows(self, db):
        """Return every field an import could plausibly disturb.

        The settlement record replaced ``actual_amount`` at plan step
        ``balance:X-au-c3`` and moved onto the covering movement at
        ``balance:X-bi-4b-2``, so the snapshot follows it: a row's figure is
        the sum of its entries (``settled_figure``), and it belongs in a
        comparison whose whole job is to prove that recording a statement
        moves nothing.
        """
        return [
            (t.id, t.status_id, t.settled_on, settled_figure(t),
             t.estimated_amount, t.pay_period_id, t.reconciled_by_id)
            for t in db.session.query(Transaction)
            .order_by(Transaction.id).all()
        ]

    def _ledger(self, db, seed_user):
        """Return the worked-out ledger's net per chart account and day."""
        return nets_by_key_and_day(db.session, seed_user["scenario"].id)

    def test_no_transaction_row_is_touched(
        self, app, db, seed_user, money_on_the_books,
    ):
        """A statement import writes statement rows and nothing else."""
        before = self._rows(db)
        assert before, "fixture must put a real transaction on the books"

        _record(seed_user, _file())

        assert self._rows(db) == before

    def test_no_LEDGER_line_moves(
        self, app, db, seed_user, money_on_the_books,
    ):
        """The stronger claim: the balance's own inputs do not move.

        Asserted on the worked-out ledger's net per chart account and day
        rather than on a balance figure, because a balance that happened not
        to move could still hide a line that did.
        """
        before = self._ledger(db, seed_user)
        # Not empty: the $125.00 Groceries row settled 2026-03-03 books 0 - 125.00 =
        # -125.00 on Checking's own line (money out of an asset, debit-positive).
        assert before[(linked_key(seed_user["account"]), date(2026, 3, 3))] == Decimal("-125.00")

        _record(seed_user, _file())

        assert self._ledger(db, seed_user) == before

    def test_no_clearing_link_is_written(
        self, app, db, seed_user, money_on_the_books,
    ):
        """Deciding which statement showed a line is the NEXT leaf's job."""
        before = db.session.query(Transaction).filter(
            Transaction.reconciled_by_id.isnot(None)
        ).count()

        _record(seed_user, _file())

        assert db.session.query(Transaction).filter(
            Transaction.reconciled_by_id.isnot(None)
        ).count() == before

    def test_no_settle_DAY_is_corrected(
        self, app, db, seed_user, money_on_the_books,
    ):
        """The single fact the NEXT leaf exists to change, pinned here.

        The bank file states 2026-03-02..2026-03-04 and the settled row carries
        2026-03-03, so a matcher that started working early would have every
        opportunity to move it.
        """
        _record(seed_user, _file())

        assert money_on_the_books.settled_on == date(2026, 3, 3)


class TestItsDecisionIsReadable:
    """The decision half (``plan_statement``) is the import's, and writes nothing.

    Plan step ``balance:X-bk-2`` (plan#1192): the reconcile's act sheet must
    know which lines an import WOULD add before any act, on a copy it reads
    only, so the door's decision is a public read the door itself performs.
    """

    def test_it_names_exactly_the_lines_the_import_then_adds(
        self, app, db, seed_user,
    ):
        """An overlapping export: the plan's fresh lines are the one new line.

        The first file's three lines are held; the later export adds one
        line on 03-05, so the plan names that line alone -- and the import
        performed afterwards records exactly it.
        """
        _record(seed_user, _file())
        extended = _ENTRIES + [
            (date(2026, 3, 5), "-12.79", "POINT OF SALE DEBIT L340 BJS"),
        ]

        plan = plan_statement(
            seed_user["account"].id, seed_user["user"].id, _SOURCE,
            _file(extended),
        )

        assert [
            (line.posted_on, line.amount, line.description)
            for line in plan.fresh_lines
        ] == [(date(2026, 3, 5), Decimal("-12.79"),
               "POINT OF SALE DEBIT L340 BJS")]
        outcome = _record(seed_user, _file(extended), file_name="later.csv")
        assert outcome.recorded_count == 1
        newest = db.session.query(BankStatementLine).order_by(
            BankStatementLine.id.desc(),
        ).first()
        assert (newest.posted_on, newest.amount) == (
            date(2026, 3, 5), Decimal("-12.79"),
        )

    def test_it_stages_nothing_and_writes_nothing(self, app, db, seed_user):
        """A plan of a first import leaves the database as it found it.

        Graded by ``system.audit_log``, whose trigger logs every INSERT,
        UPDATE and DELETE on every table the import writes (each is in
        ``AUDITED_TABLES``): a census of writes, where a count per table
        sees only the tables someone thought to list.  Committed first, so
        anything the plan staged and did not flush (an object added, changed
        or deleted) is written, and logged, before the census.  A first
        import is the plan with the most to record, so nothing it leaves
        unwritten is a write a smaller plan would make; the import itself,
        performed afterwards, is the control that the census sees each of
        those writes -- the identity, the import act, its level, every line,
        its sighting and the merchant each names.
        """
        watermark = _audit_watermark(db)
        plan = plan_statement(
            seed_user["account"].id, seed_user["user"].id, _SOURCE, _file(),
        )
        assert plan.identity_is_new is True
        assert len(plan.fresh_lines) == 3
        assert not db.session.new
        db.session.commit()

        assert not _audited_writes_since(db, watermark)

        _record(seed_user, _file())
        db.session.commit()

        assert _audited_writes_since(db, watermark) == {
            ("budget.account_external_identities", "INSERT"): 1,
            ("budget.statement_imports", "INSERT"): 1,
            ("budget.account_anchor_history", "INSERT"): 1,
            ("budget.bank_statement_lines", "INSERT"): 3,
            ("budget.statement_line_sightings", "INSERT"): 3,
            ("budget.merchants", "INSERT"): 3,
        }

    def test_it_refuses_what_the_import_refuses(self, app, db, seed_user):
        """The refusal is the decision's, so the plan raises it too.

        The same self-contradicting file the door refuses above: its chain
        implies 1534.19 where its header states 9999.99.
        """
        with pytest.raises(StatementBalanceUnexplained) as raised:
            plan_statement(
                seed_user["account"].id, seed_user["user"].id, _SOURCE,
                build.build(build.chained("100.00", _ENTRIES),
                            balance_as_of="03/09/2026",
                            stated_balance="9999.99"),
            )

        assert raised.value.stated == Decimal("9999.99")
        assert raised.value.implied == Decimal("1534.19")
