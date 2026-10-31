"""The write door records a statement once, and refuses what it cannot trust.

Plan step **bank_import:X-f6a-1**, ruling **R-FP**.  Three properties are what
this leaf is FOR, and all three are graded here:

* **it records what the bank said** -- the lines, with the bank's own posted day;
* **it is idempotent** -- re-importing an overlapping span records the ACT and
  no duplicate lines, which is R-FP's "re-importing a file cannot duplicate"
  read as a property of the door rather than of a key;
* **it moves NO money** -- no transaction, purchase, status or balance is
  touched, which is what makes this leaf separable from the matching that
  follows it.

The refusal tests are FIRING CONTROLS: an account mismatch, a file claimed by
another account, and a restated line are all states no ordinary use produces
(measured: 0 restatements across two real exports twelve days apart), so each
one is planted and its refusal asserted by type.
"""

import hashlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import (
    StatementBalanceEvidenceEnum,
    StatementSourceEnum,
)
from app.exceptions import (
    StatementAccountMismatch,
    StatementBalanceUnexplained,
    StatementLineConflict,
)
from app.models.merchant import Merchant
from app.models.statement_import import (
    AccountExternalIdentity,
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from app.models.journal_entry import Posting
from app.models.transaction import Transaction
from app.services import account_service
from app.models.statement_import import BankStatementLine
from app.services.statement_import import plan_statement, record_statement
from app.services.statement_import._balance import bank_levels
from app.models.ref import AccountType
from app.services.row_valuation import settled_figure

from tests._test_helpers import create_settled_cash_transaction

from . import _csv_builder as build

_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV

_ENTRIES = [
    (date(2026, 3, 2), "-25.00",
     "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
    (date(2026, 3, 3), "1500.00",
     "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL (TOWN OF CLAYTON PAYROLL)"),
    (date(2026, 3, 4), "-40.81",
     "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
]

#: What SECU NAMES the merchant on each of those, in the same order -- the
#: parenthesised trailing token the adapter reads.  **Written out rather than
#: re-parsed here**: a fixture that re-ran the parse would move with it, so a
#: change to the adapter's rule would shift this list and its assertion
#: together and grade nothing.  The shared entries carry a token because every
#: one of the developer's 361 real lines does; a case that wants the ``None``
#: state says so with its own rows.
_MERCHANTS = ["Big Cheese Clayton", "TOWN OF CLAYTON PAYROLL", "Food Lion"]


def _file(entries=None, start="100.00", account_number=None):
    """Return a well-formed payload over *entries*."""
    kwargs = {}
    if account_number is not None:
        kwargs["account_number"] = account_number
    rows = build.chained(start, entries or _ENTRIES, **kwargs)
    return build.build(rows)


def _counts(db, account_id):
    """Return ``{import_id: (sighted, first)}`` -- the ONE derived count.

    What ``statement_imports.line_count`` / ``recorded_count`` stored until
    plan step ``bank_import:X-f6b-1``, read the way the imports table reads
    it.
    """
    return {
        import_id: (sighted, first)
        for import_id, sighted, first in db.session.execute(
            StatementLineSighting.counts_by_import(account_id),
        )
    }


def _record(seed_user, payload, file_name="statement.csv", account=None):
    """Run the door for the seeded user's checking account."""
    return record_statement(
        account_id=(account or seed_user["account"]).id,
        user_id=seed_user["user"].id,
        source=_SOURCE,
        file_name=file_name,
        payload=payload,
    )


def _second_account(db, seed_user):
    """Create a second cash account for the same owner."""
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Second Checking",
            anchor_balance=Decimal("0.00"),
            observed_on=date(2024, 1, 5),
        )
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
    is walked from the postings, not from ``budget.transactions``.
    """

    @pytest.fixture()
    def money_on_the_books(self, db, seed_user, seed_periods):
        """Settle a real transaction so there is something to move."""
        txn = create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("125.00"),
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

    def _ledger(self, db):
        """Return the postings the balance is actually walked from."""
        return [
            (p.id, p.journal_entry_id, p.ledger_account_id, p.amount,
             p.posting_kind_id)
            for p in db.session.query(Posting).order_by(Posting.id).all()
        ]

    def test_no_transaction_row_is_touched(
        self, app, db, seed_user, money_on_the_books,
    ):
        """A statement import writes statement rows and nothing else."""
        before = self._rows(db)
        assert before, "fixture must put a real transaction on the books"

        _record(seed_user, _file())

        assert self._rows(db) == before

    def test_no_POSTING_is_touched(
        self, app, db, seed_user, money_on_the_books,
    ):
        """The stronger claim: the balance's own inputs do not move.

        Asserted on the double-entry postings rather than on a balance figure,
        because a balance that happened not to move could still hide a posting
        that did.
        """
        before = self._ledger(db)
        assert before, "fixture must put postings on the ledger"

        _record(seed_user, _file())

        assert self._ledger(db) == before

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

        Committed and re-read rather than read off the session: a session's
        own state cannot tell a write that was rolled back from one never
        made.  A first import is the plan with the most to record -- the
        identity, every line, the import act and its level -- so nothing it
        leaves unwritten is a write a smaller plan would make.
        """
        plan = plan_statement(
            seed_user["account"].id, seed_user["user"].id, _SOURCE, _file(),
        )
        assert plan.identity_is_new is True
        assert len(plan.fresh_lines) == 3
        assert not db.session.new
        db.session.commit()
        db.session.expire_all()

        assert db.session.query(BankStatementLine).count() == 0
        assert db.session.query(StatementImport).count() == 0
        assert db.session.query(StatementLineSighting).count() == 0

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


class TestTheMerchantWordsBecomeRows:
    """Plan step ``bank_import:X-gd-1``, through the REAL door.

    The parse is graded in ``test_secu_csv`` and the resolver in
    ``test_models/test_merchant_schema.py``; this is the only place that says
    the two meet -- that a word the adapter read reaches
    ``budget.merchants`` as one row per merchant per account, and that a
    re-import of the same file adds none.
    """

    def test_one_row_per_MERCHANT_not_per_line(self, app, db, seed_user):
        """Three lines naming two merchants are two rows.

        A rule is keyed on the merchant, so a second row for one name would
        leave a stated answer reaching half that merchant's lines -- silently,
        because nothing joins the two rows.
        """
        entries = [
            (date(2026, 3, 2), "-25.00",
             "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
            (date(2026, 3, 3), "-40.81",
             "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
            (date(2026, 3, 4), "-12.00",
             "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
        ]

        _record(seed_user, build.build(build.chained("100.00", entries)))

        assert sorted(
            row.name for row in db.session.query(Merchant).all()
        ) == ["Big Cheese Clayton", "Food Lion"]
        named = db.session.query(BankStatementLine).order_by(
            BankStatementLine.posted_on,
        ).all()
        assert named[0].merchant_id == named[1].merchant_id
        assert named[2].merchant_id != named[0].merchant_id

    def test_RE_IMPORTING_the_same_file_adds_no_merchant(
        self, app, db, seed_user,
    ):
        """The idempotence the identity key gives, at the door.

        Re-importing an overlapping span records no line
        (``recorded_count == 0``), and the merchant resolution runs anyway --
        every sighting names its merchant's row (ruling **R-BI16**).  ``ON
        CONFLICT DO NOTHING`` is what makes that
        a no-op instead of an ``IntegrityError`` that would fail an import
        which had nothing to add.
        """
        _record(seed_user, _file())
        before = db.session.query(Merchant).count()

        second = _record(seed_user, _file(), file_name="again.csv")

        assert second.recorded_count == 0
        assert db.session.query(Merchant).count() == before == 3

    def test_a_line_the_source_names_NO_merchant_for_creates_none(
        self, app, db, seed_user,
    ):
        """The NULL that keys nothing, through the door that decides it.

        The parse-level ``None`` is graded in ``test_secu_csv`` and the
        schema-level one in ``test_models/test_merchant_schema.py``; the DOOR
        between them was covered by neither, which an adversarial review found
        on 2026-08-25 (the case it replaces asserted an empty table over a
        merchant that was never going to be created, and no mutation could fail
        it).  Delete ``_merchant_words``' truthiness test or ``_sighting_of``'s
        ``if line.merchant else None`` and this account gains a merchant named
        for nothing.
        """
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 NO TOKEN HERE")]

        _record(seed_user, build.build(build.chained("100.00", entries)))

        assert db.session.query(Merchant).count() == 0
        line = db.session.query(BankStatementLine).one()
        assert line.merchant_id is None
        assert line.merchant_name is None

    def test_a_merchant_is_scoped_to_the_ACCOUNT_that_imported_it(
        self, app, db, seed_user,
    ):
        """Two accounts importing one payee hold two merchants.

        A statement is one bank's record of ONE account and a rule is stated
        per account, so the same word on two accounts is two subjects -- the
        key that lets a Checking answer and a card answer differ.
        """
        second = _second_account(db, seed_user)
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)")]

        _record(seed_user, build.build(build.chained("100.00", entries)))
        _record(
            seed_user,
            build.build(build.chained(
                "100.00", entries, account_number="9999999999",
            )),
            file_name="second.csv", account=second,
        )

        rows = db.session.query(Merchant).filter(
            Merchant.name == "Food Lion",
        ).all()
        assert sorted(row.account_id for row in rows) == sorted(
            [seed_user["account"].id, second.id],
        )


class TestAHeldLineReadsItsNewSightingInTheSameUnitOfWork:
    """The door's expire of ``BankStatementLine.READS_OVER_SIGHTINGS``, graded.

    A held line's eager ``sightings`` collection and its two merchant
    projections were loaded BEFORE the re-import's sighting existed, and a
    row staged by its ids joins neither.  The door expires exactly that set
    on every held line (plan step ``bank_import:X-f6b-1b``, ruling
    **R-BI16**), so a reader holding the instance in the SAME unit of work
    -- the route's, before its commit -- reads the row and not the instance
    as it was.  **This class is the firing control that claim never had**:
    delete the ``expire`` loop at the end of ``_write_records`` and every
    assertion below reads the pre-import ``None``.
    """

    _ENTRIES = [
        (date(2026, 3, 2), "-25.00",
         "POINT OF SALE DEBIT L340 DATE 03-01 COFFEE (Big Cheese Clayton)"),
    ]

    def test_the_merchant_and_the_day_a_re_import_states_are_read_off_the_held_instance(
        self, app, db, seed_user,
    ):
        """One held line, its first sighting naming nothing, the second naming both."""
        _record(seed_user, build.build(build.chained("100.00", self._ENTRIES)))
        held = db.session.query(BankStatementLine).one()
        # Stand in for a first import that named neither fact.
        [first] = held.sightings
        first.merchant_id = None
        first.transaction_on = None
        db.session.flush()
        db.session.expire_all()
        held = db.session.query(BankStatementLine).one()
        assert held.merchant_id is None
        assert held.merchant_name is None
        assert held.transaction_on is None

        second = _record(seed_user, build.build(build.chained(
            "100.00", self._ENTRIES,
        )), file_name="again.csv")

        # The SAME instance, no expire_all: what the door left it able to say.
        assert second.recorded_count == 0
        assert held.merchant_name == "Big Cheese Clayton"
        assert held.merchant_id == db.session.query(Merchant).one().id
        assert held.transaction_on == date(2026, 3, 1)
        assert len(held.sightings) == 2


class TestARefusedImportLeavesTheSessionUNTOUCHED:
    """The claim ``_reconcile`` exists for, and the only refusal that sees it.

    Plan step ``bank_import:X-gd-1`` split DECIDING from WRITING:
    :func:`_reconcile` returns the partition and :func:`_write_records` writes
    it, after the file's last refusal.  Before that split, the door's
    absorbing arm (deleted at ``bank_import:X-f6b-1b``) ran inside the
    reconciliation's own loop, so a refusal raised on group *k* left groups
    1..*k*-1 dirty in the session and the route's rollback is what discarded
    them -- the caveat the module docstring carried since an adversarial
    review found it on 2026-08-20.

    **THE REFUSAL HAS TO FIRE AFTER THE RECONCILIATION, and the two obvious
    ones do not.**  Two adversarial reviews on 2026-08-25 measured a first
    version of this class passing under BOTH mutations it named, and this is
    why: ``StatementLineConflict`` is raised INSIDE ``_reconcile``, so no
    placement of the writes downstream of it can be told apart, and
    ``StatementAccountMismatch`` fires at ``verify_identity``, before the
    reconciliation runs at all.  ``StatementBalanceUnexplained`` is the one
    that lands in between -- ``resolve_anchor`` is the last refusal, and the
    reconciliation has already decided everything by the time it raises.

    **Asserted WITHOUT rolling back**, because what the rollback would hide is
    the whole subject.
    """

    #: Two lines the re-import restates exactly, so both groups PAIR and the
    #: pass decides to absorb before it is refused.  The first carries the
    #: bank's own ``DATE MM-DD`` token, which is what
    #: :attr:`~app.models.statement_import.BankStatementLine.transaction_on`
    #: is read from -- so it can be absorbed without the DESCRIPTION changing,
    #: and the description is what the pairing matches on.
    _ENTRIES = [
        (date(2026, 3, 2), "-25.00",
         "POINT OF SALE DEBIT L340 DATE 03-01 COFFEE (Big Cheese Clayton)"),
        (date(2026, 3, 4), "-40.81",
         "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
    ]

    @staticmethod
    def _contradicting(entries):
        """Return a well-chained file whose HEADER its own chain never reaches.

        The refusal is ``resolve_anchor``'s and it is the last one the door
        performs, which is the whole point -- see the class docstring.
        """
        return build.build(
            build.chained("100.00", entries),
            balance_as_of="03/09/2026", stated_balance="9999.99",
        )

    def test_it_absorbs_NOTHING_from_a_group_it_had_already_walked(
        self, app, db, seed_user,
    ):
        """THE firing control for every write sitting after the last refusal.

        ONE write remains for a held line (plan step ``bank_import:X-f6b-1b``,
        ruling **R-BI16**): its sighting, carrying the merchant key its word
        names.  Move it into ``_reconcile``'s loop and this fails: the row is
        written, the line's merchant reads the refused file's word, and only
        the route's rollback takes it back.
        """
        _record(seed_user, build.build(build.chained("100.00", self._ENTRIES)))
        recorded = db.session.query(BankStatementLine).order_by(
            BankStatementLine.posted_on,
        ).first()
        assert recorded.merchant_name is not None
        # Stand in for a sighting an older adapter wrote, which named no
        # merchant, so the refused file's sighting would be the one naming it.
        [first] = recorded.sightings
        first.merchant_id = None
        db.session.flush()
        sightings_before = db.session.query(StatementLineSighting).count()

        with pytest.raises(StatementBalanceUnexplained):
            _record(
                seed_user, self._contradicting(self._ENTRIES),
                file_name="refused.csv",
            )

        db.session.expire_all()
        assert db.session.query(BankStatementLine).order_by(
            BankStatementLine.posted_on,
        ).first().merchant_id is None
        assert db.session.query(StatementLineSighting).count() == (
            sightings_before
        )

    def test_it_creates_NO_merchant_row(self, app, db, seed_user):
        """A refused file may not leave a merchant behind, and nothing sweeps one.

        ``budget.merchants`` is reclaimed only when an IMPORT is deleted
        (``statement_import.delete_import``), so a row a refused upload created
        would survive with no line and no answer to justify it -- and would put
        a merchant the owner never recorded onto the screen that asks where
        merchants go.  Hoist :func:`~._merchants.resolve_merchants` out of
        ``_write_records`` to anywhere before ``resolve_anchor`` and this
        fails.
        """
        _record(seed_user, build.build(build.chained("100.00", self._ENTRIES)))
        with_a_stranger = self._ENTRIES + [
            (date(2026, 3, 9), "-11.00",
             "POINT OF SALE DEBIT L340 NEW PLACE (Never Seen Ltd)"),
        ]

        with pytest.raises(StatementBalanceUnexplained):
            _record(
                seed_user, self._contradicting(with_a_stranger),
                file_name="refused.csv",
            )

        assert db.session.query(Merchant).filter(
            Merchant.name == "Never Seen Ltd",
        ).count() == 0



class TestTheAccountMappingIsAFactNotAGuess:
    """Ruling R-FP: the first import records it, every import after checks it."""

    def test_the_first_import_records_what_the_source_calls_the_account(
        self, app, db, seed_user,
    ):
        """The mapping exists because the user stated it, not because the app
        inferred it."""
        _record(seed_user, _file())

        identity = db.session.query(AccountExternalIdentity).one()
        assert identity.account_id == seed_user["account"].id
        assert identity.external_account_id == build.ACCOUNT_IDENTITY

    def test_a_second_import_of_the_same_account_records_no_second_mapping(
        self, app, db, seed_user,
    ):
        """One account has ONE identity per source."""
        _record(seed_user, _file())
        _record(seed_user, _file(), file_name="again.csv")

        assert db.session.query(AccountExternalIdentity).count() == 1

    def test_a_file_for_a_DIFFERENT_account_is_refused(
        self, app, db, seed_user,
    ):
        """Importing the card's export into checking, the case R-FP names."""
        _record(seed_user, _file())

        with pytest.raises(StatementAccountMismatch) as caught:
            _record(
                seed_user, _file(account_number="******9999"),
                file_name="wrong.csv",
            )

        assert caught.value.submitted == "Checking ******9999"
        assert caught.value.recorded == build.ACCOUNT_IDENTITY

    def test_the_identity_carries_the_account_NAME_not_just_the_mask(
        self, app, db, seed_user,
    ):
        """The mask alone cannot tell two of one owner's accounts apart.

        SECU masks the number to its last four digits, so two accounts whose
        numbers end alike mask identically -- and comparing only the mask would
        ACCEPT a file imported against the wrong account, which is the failure
        direction this mapping exists to prevent.
        """
        _record(seed_user, _file())

        with pytest.raises(StatementAccountMismatch):
            _record(
                seed_user,
                build.build(build.chained(
                    "100.00", _ENTRIES, account_name="Savings",
                )),
                file_name="savings.csv",
            )

    def test_a_file_another_account_already_claims_is_refused(
        self, app, db, seed_user,
    ):
        """The arm a one-directional check would miss.

        Comparing only against THIS account's recorded identity would let a
        statement already imported under account A be imported again under
        account B as a "first import" -- the same bank statement recorded
        twice, under two accounts.
        """
        _record(seed_user, _file())
        other = _second_account(db, seed_user)
        db.session.flush()

        with pytest.raises(StatementAccountMismatch):
            _record(seed_user, _file(), account=other)

    def test_a_refused_import_stages_nothing(self, app, db, seed_user):
        """"Nothing was imported" is true BEFORE the rollback, not because of it.

        Asserted without rolling back deliberately: the door validates
        everything it can refuse before it stages a single row, so the session
        is already clean when the refusal is raised.  A test that rolled back
        first would pass equally against a door that wrote and then relied on
        the route remembering to undo it.
        """
        _record(seed_user, _file())
        before = db.session.query(BankStatementLine).count()

        with pytest.raises(StatementAccountMismatch):
            _record(
                seed_user, _file(account_number="******9999"),
                file_name="wrong.csv",
            )

        assert db.session.query(BankStatementLine).count() == before
        assert db.session.query(StatementImport).count() == 1


class TestARecordedLineMayNotBeQuietlyRestated:
    """An observation rewritten is what ruling R-FL exists to prevent."""

    def test_a_changed_description_at_a_known_identity_is_refused(
        self, app, db, seed_user,
    ):
        """Same day, same amount, same ordinal -- different text."""
        _record(seed_user, _file())

        changed = list(_ENTRIES)
        changed[0] = (date(2026, 3, 2), "-25.00", "SOMETHING ELSE ENTIRELY")

        with pytest.raises(StatementLineConflict) as caught:
            _record(seed_user, _file(changed), file_name="restated.csv")

        assert caught.value.posted_on == date(2026, 3, 2)
        assert caught.value.submitted == "SOMETHING ELSE ENTIRELY"

    def test_a_changed_running_balance_is_NOT_a_conflict(
        self, app, db, seed_user,
    ):
        """A running balance is not a fact about a line, so it cannot restate one.

        It is a prefix sum over the bank's LISTING ORDER, and SECU lists a
        day's card debits sorted by ascending magnitude rather than by arrival
        -- so a swipe that finalizes onto an already-listed day is INSERTED,
        and every later line on that day legitimately gets a different balance
        while both files verify their own chain.  Comparing it per line refused
        an honest, more-complete re-export of the user's own year-to-date
        statement and left that account unable to import ever again.
        """
        _record(seed_user, _file())

        second = _record(
            seed_user, _file(start="9999.00"), file_name="later.csv",
        )

        assert second.recorded_count == 0

    def test_an_unchanged_re_import_is_NOT_a_conflict(
        self, app, db, seed_user,
    ):
        """The refusal must not fire on the ordinary case.

        A guard that refuses the correct write is worse than none, and the
        correct write here -- re-importing an unchanged year-to-date export --
        is what the user does every single time.
        """
        _record(seed_user, _file())

        second = _record(seed_user, _file(), file_name="again.csv")

        assert second.recorded_count == 0


#: Two same-day same-amount lines: the shape whose ordinal was the only term of
#: the identity key the bank never stated.  Measured across the developer's
#: 2026-07-19, 2026-08-16 and 2026-08-18 exports, 0 of 1,041 real lines shared a
#: ``(day, amount)`` with another -- so every case below is PLANTED, and each is
#: a firing control for a refusal that used to fire on a file the bank had not
#: restated at all.
_TWINS = [
    (date(2026, 3, 2), "-4.75",
     "POINT OF SALE DEBIT L340 STARBUCKS #123 (Starbucks)"),
    (date(2026, 3, 2), "-4.75",
     "POINT OF SALE DEBIT L340 DUNKIN #456 (Dunkin)"),
]

#: A line on a LATER day, so a refusal over the twins costs it too.  This is
#: what makes the cost of a false refusal visible: the whole file is refused,
#: not the disputed line.
_LATER = (date(2026, 3, 5), "-99.00",
          "POINT OF SALE DEBIT L340 ACE HARDWARE (Ace Hardware)")


class TestAGroupIsReconciledAsASet:
    """Plan step X-f6a-4: the ordinal is a surrogate, not a comparison term."""

    def test_the_same_two_lines_in_the_OTHER_order_record_nothing(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the bank re-orders one day's two equal debits.

        Measured against the positional code 2026-08-20, this refused the whole
        file and told the owner the bank had restated a line it had not.  Both
        lines are present in both files; only the ordinal this app assigned
        moved.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user, _file(list(reversed(_TWINS))), file_name="swapped.csv",
        )

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_reorder_does_not_cost_the_file_its_GENUINELY_new_lines(
        self, app, db, seed_user,
    ):
        """MONEY: what a false refusal actually costs is the rest of the export.

        The re-ordered pair is two lines; the file also carries a line the app
        has never seen.  Refusing the file loses that line -- and every other
        line after it -- until someone repairs the account by hand.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user,
            _file(list(reversed(_TWINS)) + [_LATER]),
            file_name="swapped_plus_new.csv",
        )

        assert second.recorded_count == 1
        assert db.session.query(BankStatementLine).filter_by(
            posted_on=date(2026, 3, 5),
        ).one().amount == Decimal("-99.00")

    def test_a_NEW_line_the_bank_lists_FIRST_is_recorded_as_the_new_one(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: a swipe finalizes onto an already-recorded day.

        SECU INSERTS such a line into that day's block rather than appending it
        -- the behaviour that made ``_refuse_restatement`` stop comparing
        running balances -- so the file lists the NEW line FIRST and the
        recorded one after it.  Under the positional rule that compared the new
        line against the recorded one's wording and refused the whole file.
        Exactly one line is new, and the recorded one keeps its own address.
        """
        _record(seed_user, _file([_TWINS[0]]))

        second = _record(
            seed_user,
            _file([_TWINS[1], _TWINS[0]]),
            file_name="inserted.csv",
        )

        assert second.recorded_count == 1
        rows = {
            row.description: row.sequence_in_group
            for row in db.session.query(BankStatementLine).all()
        }
        assert rows == {_TWINS[0][2]: 0, _TWINS[1][2]: 1}

    def test_a_second_IDENTICAL_charge_is_recorded_rather_than_dropped(
        self, app, db, seed_user,
    ):
        """MONEY: the same coffee twice at the same shop is two movements.

        This is what the ordinal exists for, asked of the set-wise
        reconciliation: pairing by wording must be by COUNT, or the second
        charge is read as a duplicate of the first and never recorded.
        """
        same = (date(2026, 3, 2), "-4.75", "POINT OF SALE DEBIT L340 COFFEE")
        _record(seed_user, _file([same]))

        second = _record(seed_user, _file([same, same]), file_name="twice.csv")

        assert second.recorded_count == 1
        assert db.session.query(BankStatementLine).count() == 2
        assert sorted(
            row.sequence_in_group
            for row in db.session.query(BankStatementLine).all()
        ) == [0, 1]

    def test_a_RESTATED_line_in_a_shared_group_is_still_refused(
        self, app, db, seed_user,
    ):
        """The refusal the policy is FOR survives the reconciliation change.

        One of the two lines keeps its wording and pairs; the other's wording
        is gone from the file and an unaccounted-for line stands in its place.
        That is the bank re-wording an observation, and ruling R-FL refuses it.
        """
        _record(seed_user, _file(_TWINS))

        restated = [
            _TWINS[0],
            (date(2026, 3, 2), "-4.75", "SOMETHING ELSE ENTIRELY"),
        ]

        with pytest.raises(StatementLineConflict) as caught:
            _record(seed_user, _file(restated), file_name="restated.csv")

        assert caught.value.recorded == _TWINS[1][2]
        assert caught.value.submitted == "SOMETHING ELSE ENTIRELY"
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_SHORTER_export_over_a_shared_group_refuses_nothing(
        self, app, db, seed_user,
    ):
        """A file covering less than the app holds contradicts nothing.

        The app holds both twins and the file states one.  Under the positional
        rule the surviving line landed at ordinal 0 and was compared against
        the OTHER twin's wording, which refused.  It is now what it is: a
        shorter export, recording nothing and refusing nothing.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user, _file([_TWINS[1]]), file_name="shorter.csv",
        )

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).count() == 2
