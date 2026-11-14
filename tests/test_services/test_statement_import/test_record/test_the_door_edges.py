"""Merchant words become rows, a held line's sighting, and a refused import's session."""

from datetime import date

import pytest

from app.models.merchant import Merchant
from app.models.statement_import import BankStatementLine, StatementLineSighting
from app.services.statement_import import StatementBalanceUnexplained

from tests.test_services.test_statement_import import _csv_builder as build

from ._helpers import _file, _record, _second_account


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
        merchants go.  Hoist
        :func:`~app.services.statement_import._merchants.resolve_merchants` out
        of ``_write_records`` to anywhere before ``resolve_anchor`` and this
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
