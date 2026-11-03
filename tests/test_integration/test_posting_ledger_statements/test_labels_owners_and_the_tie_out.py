"""The labels, scenario and owner isolation, and a tie-out that can fail."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.account import Account
from app.models.category import Category
from app.models.journal_entry import JournalEntry, Posting
from app.models.scenario import Scenario
from app.services import ledger_report_service
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for
import sqlalchemy as sa

from tests._test_helpers import create_settled_cash_transaction, linked_ledger_account

from ._helpers import (
    _Y,
    _assert_ledger_self_consistent,
    _find_line,
    _labels,
    _reader_bs,
    _assert_reader_matches_independent,
)


# ---------------------------------------------------------------------------
# 8. Display labels branch on kind (live rename, orphan snapshot, equity twin)
# ---------------------------------------------------------------------------


class TestDisplayLabels:
    """A category line reflects a rename; the equity twin keeps its snapshot."""

    def test_rename_reflected_and_equity_twin_uses_snapshot(
        self, app, db, seed_user,
    ):
        """A live category rename reflects; the equity twin label stays snapshot.

        A $100.00 Groceries expense posts to the "Family: Groceries" category
        ledger; renaming the category's item to "Snacks" makes the income line
        read the LIVE ``category.display_name`` ("Family: Snacks").  Renaming the
        Checking ACCOUNT does NOT change its equity twin's balance-sheet label,
        which is the frozen ``"Checking -- Opening"`` snapshot (the twin is not a
        linked row, so the live-account-name rule does not apply to it).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"), account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 15),
            )
            db.session.commit()

            groceries = db.session.get(
                Category, seed_user["categories"]["Groceries"].id,
            )
            groceries.item_name = "Snacks"
            # Rename the linked account too; the equity twin must NOT follow it.
            # Re-fetch into the current session -- the fixture reference is
            # attached to the fixture-setup session and would not persist.
            db.session.get(Account, checking.id).name = "Primary Checking"
            db.session.commit()

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=_Y),
            )
            assert _labels(income.expense.lines) == ["Family: Snacks"]

            sheet = _reader_bs(user_id)
            # The linked Asset line follows the live account name...
            assert _find_line(
                sheet.assets.lines, "Primary Checking",
            ).amount == Decimal("900.00")
            # ...but the equity twin keeps its frozen origination snapshot.
            assert _find_line(
                sheet.equity.lines, "Checking -- Opening",
            ).amount == Decimal("1000.00")
            assert sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()

    def test_orphaned_category_uses_snapshot_label(self, app, db, seed_user):
        """A category ledger account whose category is gone keeps its snapshot label.

        A $100.00 Groceries expense posts to the category ledger; when the
        budget category's key SET-NULLs the account's ``category_id`` (its
        ``kind_id`` stays ``category``), the line falls back to the account's
        own "Family: Groceries" snapshot and the amount is untouched.

        **The orphan state is STAGED, not reached through a category delete.**
        Every plan row names a definition since the family's cutover (plan
        step ``balance:X-bi-7d-2``) and ``transaction_templates.category_id``
        is RESTRICT, so a category with a settled one-off under it cannot be
        deleted at all (the category door archives it; the database refuses
        the delete).  A LEGACY link-less row reached the state through the
        delete until then.  Production can still hold the state -- accounts
        orphaned before the cutover, or a category deleted once every row
        under it was hard-deleted -- so the reader's fallback is graded on
        the state itself: the SET NULL the key performs, written here.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries_id = seed_user["categories"]["Groceries"].id
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 15),
            )
            db.session.commit()
            orphaned = db.session.execute(sa.text(
                "UPDATE budget.ledger_accounts SET category_id = NULL "
                "WHERE category_id = :cid"
            ), {"cid": groceries_id}).rowcount
            db.session.commit()
            assert orphaned == 1

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=_Y),
            )
            assert _labels(income.expense.lines) == ["Family: Groceries"]
            assert income.expense.total == Decimal("100.00")
            # The orphaned category leaves the ledger balanced (SET NULL touches
            # only the display FK, never the postings).
            assert _reader_bs(user_id).tie_out.in_balance is True
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 9. Scenario and owner isolation
# ---------------------------------------------------------------------------


class TestScenarioAndOwnerIsolation:
    """Statements read the baseline only and never bleed across owners."""

    def test_non_baseline_scenario_never_appears(self, app, db, seed_user):
        """A what-if expense is invisible to the baseline statements.

        A $70.00 Groceries expense settled in a NON-baseline what-if scenario
        posts to that scenario only.  The baseline income statement shows no
        Groceries line and the baseline balance sheet's Checking stays on its
        $1000.00 opening -- the readers scope to ``get_baseline_scenario``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            whatif = Scenario(
                user_id=user_id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), account=seed_user["account"], scenario=whatif,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 9, 1),
            )
            db.session.commit()

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert not income.expense.lines
            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("1000.00")
            assert sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()

    def test_owners_do_not_see_each_other(
        self, app, db, seed_user, seed_second_user,
    ):
        """Neither owner's statements show the other's accounts or sources.

        Owner 1 (seeded Checking $1000.00) settles a $60.00 expense; owner 2
        (seeded Checking $2000.00) settles an $80.00 expense.  Each owner's
        balance sheet lists only their own Checking (1000 - 60 = 940.00 and
        2000 - 80 = 1920.00) and neither income statement sees the other's
        expense.
        """
        with app.app_context():
            user1 = seed_user["user"].id
            user2 = seed_second_user["user"].id
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("60.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 10, 1),
            )
            create_settled_cash_transaction(
                seed_second_user, db.session,
                seed_second_user["bootstrap_period"], Decimal("80.00"),
                account=seed_second_user["account"],
                category=seed_second_user["categories"]["Groceries"],
                settled_on=date(_Y, 10, 2),
            )
            db.session.commit()

            sheet1 = _reader_bs(user1)
            sheet2 = _reader_bs(user2)
            assert _labels(sheet1.assets.lines) == ["Checking"]
            assert _find_line(
                sheet1.assets.lines, "Checking",
            ).amount == Decimal("940.00")
            assert _labels(sheet2.assets.lines) == ["Checking"]
            assert _find_line(
                sheet2.assets.lines, "Checking",
            ).amount == Decimal("1920.00")

            income1 = ledger_report_service.compute_income_statement(
                user1, calendar_for(user1), StatementWindow("year", year=_Y),
            )
            income2 = ledger_report_service.compute_income_statement(
                user2, calendar_for(user2), StatementWindow("year", year=_Y),
            )
            assert income1.expense.total == Decimal("60.00")
            assert income2.expense.total == Decimal("80.00")
            _assert_reader_matches_independent(
                user1, seed_user["scenario"].id, sheet1,
            )
            _assert_reader_matches_independent(
                user2, seed_second_user["scenario"].id, sheet2,
            )
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 10. Adversarial: the tie-out is not vacuous
# ---------------------------------------------------------------------------


class TestTieOutIsNotVacuous:
    """An injected unbalanced leg turns the tie-out red."""

    def test_injected_leg_flips_in_balance_false(self, app, db, seed_user):
        """One raw +50 leg on the opening pushes ledger_net off zero, tie-out red.

        A balanced book has a green tie-out (ledger_net 0.00, in_balance True).
        Inserting one unmatched +50.00 leg onto the seeded Checking opening entry
        (raw SQL, flushed but never committed, so the DEFERRED per-entry trigger
        never fires) makes the whole-ledger net 50.00: the reader's
        ``tie_out.ledger_net`` reads 50.00, its Assets no longer equal
        Liabilities + Equity, and ``in_balance`` flips to False -- so the tie-out
        is a real check, not one the balanced trigger makes vacuously true.
        Rolled back so the leg never lands.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            # Green before the injection.
            assert _reader_bs(user_id).tie_out.in_balance is True

            linked = linked_ledger_account(db.session, checking.id)
            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            # The LATEST opening entry, and named as a choice rather than
            # taken as the only one.  Since plan step X-f3c-2b the seeded
            # account's books are restated, and a restatement REVERSES the
            # opening entry and re-posts it -- three opening-sourced entries
            # where there used to be one, which is production's own shape
            # after the same act.  Any of them carries a leg on this ledger,
            # so the injection below is equally unbalanced whichever is
            # picked; ``.scalar()`` over the set would simply raise.
            entry_id = (
                db.session.query(JournalEntry.id)
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == opening_source,
                )
                .order_by(JournalEntry.id.desc())
                .limit(1)
                .scalar()
            )
            assert entry_id is not None, (
                "no opening entry to inject into -- this class's whole name is "
                "a promise that it is not vacuous, and a None here would "
                "inject nothing and still pass"
            )
            db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": linked.id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.OPENING),
            })
            db.session.flush()

            tampered = _reader_bs(user_id)
            assert tampered.tie_out.ledger_net == Decimal("50.00")
            assert tampered.tie_out.assets != (
                tampered.tie_out.liabilities_plus_equity
            )
            assert tampered.tie_out.in_balance is False

            db.session.rollback()
