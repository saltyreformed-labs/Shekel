"""The FK-* checks: a key naming a row that is missing or of the wrong kind (an account's owner and
type, a transaction's period, a template's category, with no category accepted, and a transfer's
own keys, FK-14 to FK-16).
"""

from decimal import Decimal

from app.models.ref import Status, TransactionType
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import bare_expense_template, create_account_of_type, create_transfer
from scripts.integrity_check import check_referential_integrity


# ── Referential Integrity ────────────────────────────────────────


class TestReferentialIntegrity:
    """Tests for FK-* referential integrity checks."""

    def test_clean_database_passes_all(self, app, db, seed_user, seed_periods):
        """All FK checks pass on a properly seeded database."""
        results = check_referential_integrity(db.session)
        assert all(r.passed for r in results), (
            f"Failed checks: {[r.check_id for r in results if not r.passed]}"
        )
        # 12 since plan step X-f1c3c: FK-03 ("accounts pointing to a
        # nonexistent anchor period") went with the column it queried; 15
        # since balance:X-bi-6-4c-4 added FK-05..07's transfer twins FK-14..16
        # (ruling R-BAL160).
        assert len(results) == 15

    def test_fk01_detects_orphaned_account(self, app, db, seed_user):
        """FK-01 detects an account whose user_id references a nonexistent user."""
        # Insert an account with a bogus user_id via raw SQL to bypass FK.
        # It needed anchor columns pointed at a real period until plan step
        # X-f1c3c (ruling R-EH) deleted them; an account row is now just its
        # owner, type and name, and the orphan under test is the user_id.
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        db.session.execute(db.text("""
            INSERT INTO budget.accounts (user_id, account_type_id, name)
            VALUES (99999, 1, 'Orphaned Account')
        """))
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk01 = next(r for r in results if r.check_id == "FK-01")
        assert not fk01.passed
        assert fk01.detail_count == 1  # 1 orphaned account inserted

        # Restore FK enforcement.
        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk05_detects_transaction_with_missing_period(
        self, app, db, seed_user, seed_periods
    ):
        """FK-05 detects a transaction referencing a nonexistent pay period."""
        definition = bare_expense_template(db.session, seed_user)
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        status = db.session.query(Status).filter_by(name="Projected").one()
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        db.session.execute(db.text("""
            INSERT INTO budget.transactions
                (template_id, pay_period_id, user_id, scenario_id, account_id,
                 status_id, name, transaction_type_id, estimated_amount,
                 due_date, occurs_on)
            VALUES (:tid, 99999, :uid, :sid, :aid, :stid, 'Ghost Txn', :ttid,
                    50.00, :due, :due)
        """), {
            # ``session_replication_role = 'replica'`` suppresses referential
            # TRIGGERS, which is what lets ``pay_period_id`` dangle -- it does
            # not suppress NOT NULL, so the owner plan step
            # ``pay_calendar:C13-a`` added is stated like every other column
            # here.  It is the seeded owner's: the row is forged in its PARENT
            # POINTER, which is what FK-05 is about, and nowhere else.
            # Nor does it suppress a CHECK: the row names a rule-less
            # definition of the owner's and a day (plan step
            # ``balance:X-bi-7d-1``), because the family's cutover makes a
            # link-less row unstorable and a linked row must be dated.  The
            # period whose start the day would be does not exist, so the
            # seeded first paycheck's stands in; FK-05 reads neither column.
            "tid": definition.id,
            "due": seed_periods[0].start_date,
            "uid": seed_user["user"].id,
            "sid": seed_user["scenario"].id,
            "aid": seed_user["account"].id,
            "stid": status.id,
            "ttid": txn_type.id,
        })
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk05 = next(r for r in results if r.check_id == "FK-05")
        assert not fk05.passed
        assert fk05.detail_count == 1  # 1 transaction with missing period

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk10_detects_template_with_missing_category(self, app, db, seed_user):
        """FK-10 detects a transaction template with an invalid category_id."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        db.session.execute(db.text("""
            INSERT INTO budget.transaction_templates
                (user_id, account_id, category_id, transaction_type_id,
                 name, default_amount)
            VALUES (:uid, :aid, 99999, :ttid, 'Bad Template', 25.00)
        """), {
            "uid": seed_user["user"].id,
            "aid": seed_user["account"].id,
            "ttid": txn_type.id,
        })
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk10 = next(r for r in results if r.check_id == "FK-10")
        assert not fk10.passed
        assert fk10.detail_count == 1  # 1 template with missing category

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))

    def test_fk10_accepts_a_template_with_NO_category(self, app, db, seed_user):
        """A category-less definition is legal, so FK-10 passes it.

        Finding **BAL-574** (fixed at plan step ``balance:X-bi-6-4c-4``,
        ruling **R-BAL162**): ``transaction_templates.category_id`` went
        nullable at ``balance:X-bi-7b`` (ruling **R-BAL24**) for the
        definitions statement matching mints for money the app cannot name,
        and FK-10 kept reporting each one as a dangling key -- a CRITICAL on
        every sweep of a production holding eight.  The dangling case above
        is the firing control: the check narrows, it does not go quiet.
        """
        txn_type = db.session.query(TransactionType).filter_by(name="Expense").one()
        db.session.add(TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=None,
            transaction_type_id=txn_type.id,
            name="Dividend Earned",
            default_amount=Decimal("4.12"),
        ))
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk10 = next(r for r in results if r.check_id == "FK-10")
        assert fk10.passed, fk10.details

    def test_fk14_to_16_grade_a_transfers_OWN_keys(
        self, app, db, seed_user, seed_periods,
    ):
        """FK-14..16 read ``budget.transfers``; FK-05..07 read its shadows.

        Plan step ``balance:X-bi-6-4c-4`` (ruling **R-BAL160**): a transfer's
        pay period, scenario and category were graded only through the
        copies its two shadow rows carry, which ``X-bi-6-4d`` stops writing
        for new transfers.  The transfer's three keys are pointed at nothing
        with the referential triggers off, its shadows left pointing at real
        rows: each twin names the transfer once, and FK-05..07 -- which grade
        the rows of ``budget.transactions`` -- stay clean, so neither family
        is answering for the other.  The transfer carries a REAL category
        first, so a clean FK-16 is graded on a key it resolves rather than on
        a NULL it skips.
        """
        # pylint: disable=import-outside-toplevel  -- the module convention.
        from app.services import transfer_service

        savings = create_account_of_type(
            seed_user, db.session, "Savings", "FK Twin Savings",
        )
        db.session.commit()
        transfer = create_transfer(
            seed_user, db.session, seed_user["account"], savings,
            seed_periods[0],
        )
        transfer_service.update_transfer(
            transfer.id, seed_user["user"].id,
            category_id=seed_user["categories"]["Rent"].id,
        )
        db.session.commit()
        clean = {
            r.check_id: r.passed
            for r in check_referential_integrity(db.session)
        }
        assert clean["FK-14"] and clean["FK-15"] and clean["FK-16"]

        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        try:
            db.session.execute(db.text(
                "UPDATE budget.transfers SET pay_period_id = 99991, "
                "scenario_id = 99992, category_id = 99993 WHERE id = :id"
            ), {"id": transfer.id})
            db.session.flush()

            by_id = {
                r.check_id: r for r in check_referential_integrity(db.session)
            }
            assert [row["id"] for row in by_id["FK-14"].details] == [transfer.id]
            assert by_id["FK-14"].details[0]["pay_period_id"] == 99991
            assert [row["id"] for row in by_id["FK-15"].details] == [transfer.id]
            assert by_id["FK-15"].details[0]["scenario_id"] == 99992
            assert [row["id"] for row in by_id["FK-16"].details] == [transfer.id]
            assert by_id["FK-16"].details[0]["category_id"] == 99993
            assert all(by_id[cid].severity == "critical"
                       for cid in ("FK-14", "FK-15", "FK-16"))
            assert by_id["FK-05"].passed
            assert by_id["FK-06"].passed
            assert by_id["FK-07"].passed
        finally:
            db.session.execute(db.text(
                "SET session_replication_role = 'origin'"
            ))

    def test_fk02_detects_account_with_invalid_type(self, app, db, seed_user):
        """FK-02: Accounts with invalid account_type_id."""
        db.session.execute(db.text(
            "SET session_replication_role = 'replica'"
        ))
        db.session.execute(db.text("""
            INSERT INTO budget.accounts (user_id, account_type_id, name)
            VALUES (:uid, 99999, 'Bad Type Account')
        """), {"uid": seed_user["user"].id})
        db.session.flush()

        results = check_referential_integrity(db.session)
        fk02 = next(r for r in results if r.check_id == "FK-02")
        assert not fk02.passed
        assert fk02.detail_count == 1
        # Verify the detail identifies the offending row.
        assert fk02.details[0]["name"] == "Bad Type Account"
        assert fk02.details[0]["account_type_id"] == 99999

        db.session.execute(db.text(
            "SET session_replication_role = 'origin'"
        ))
